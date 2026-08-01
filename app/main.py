from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session, init_db
from .models import Card, Lemma, LemmaStatus, TextDoc, TextToken
from .nlp.registry import available_engines
from .services import cards as cards_svc
from .services import dictionary as dict_svc
from .services import images as image_svc
from .services import disambiguation as disamb_svc
from .services import exporter, knowledge
from .services import settings as settings_svc
from .services.importer import (
    AMBIGUITY_MARGIN,
    create_text,
    is_auto_ignored,
    process_in_background,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

BASE_DIR = Path(__file__).resolve().parent
app = FastAPI(title="Lecteur latin", docs_url="/api/docs")
class NoCacheStatic(StaticFiles):
    """Fichiers statiques jamais mis en cache par le navigateur.

    Sans cela, une correction de style ou de script n'atteint pas
    l'utilisateur : le navigateur ressert sa copie (« 304 Not Modified »)
    et le probleme semble persister alors qu'il est corrige.
    """

    def file_response(self, *args, **kwargs):  # noqa: ANN002, ANN003
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount("/static", NoCacheStatic(directory=BASE_DIR / "static"), name="static")

# Empreinte des fichiers statiques : elle change des qu'un fichier est
# modifie, ce qui invalide automatiquement le cache du navigateur.
def _asset_version() -> str:
    latest = 0.0
    for path in (BASE_DIR / "static").glob("*"):
        if path.is_file():
            latest = max(latest, path.stat().st_mtime)
    return str(int(latest))


templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals["status_labels"] = knowledge.STATUS_LABELS
templates.env.globals["kind_labels"] = cards_svc.KIND_LABELS
templates.env.globals["show_image_for"] = knowledge.show_image_for
templates.env.globals["asset_version"] = _asset_version()


@app.on_event("startup")
def _startup() -> None:
    init_db()


# --------------------------------------------------------------------------
# Bibliotheque
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def library(request: Request, session: Session = Depends(get_session)):
    texts = session.scalars(select(TextDoc).order_by(TextDoc.created_at.desc())).all()
    rows = [{"doc": t, "coverage": knowledge.text_coverage(session, t.id) if t.status == "ready" else None} for t in texts]
    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "request": request,
            "rows": rows,
            "queue": cards_svc.queue_stats(session),
            "progress": knowledge.global_progress(session),
            "engines": available_engines(),
        },
    )


@app.post("/texts")
def add_text(
    title: str = Form(""),
    author: str = Form(""),
    content: str = Form(...),
    language_stage: str = Form("classical"),
    session: Session = Depends(get_session),
):
    if not content.strip():
        raise HTTPException(400, "texte vide")
    doc = create_text(
        session,
        title=title or content.strip()[:60],
        author=author or None,
        content=content,
        language_stage=language_stage,
    )
    session.commit()
    process_in_background(doc.id)
    return RedirectResponse(f"/texts/{doc.id}", status_code=303)


@app.post("/texts/{text_id}/relemmatize")
def relemmatize(text_id: int, engine: str = Form(""), session: Session = Depends(get_session)):
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    process_in_background(text_id, engine or None)
    return RedirectResponse(f"/texts/{text_id}", status_code=303)


@app.post("/texts/{text_id}/delete")
def delete_text(text_id: int, session: Session = Depends(get_session)):
    doc = session.get(TextDoc, text_id)
    if doc:
        session.delete(doc)
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------
@app.get("/texts/{text_id}", response_class=HTMLResponse)
def read_text(
    request: Request, text_id: int, page: int = 0, session: Session = Depends(get_session)
):
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)

    tokens = session.scalars(
        select(TextToken)
        .where(TextToken.text_id == text_id, TextToken.page_idx == page)
        .order_by(TextToken.idx)
    ).all()
    statuses = knowledge.statuses_for_tokens(session, tokens)
    lemmas = {
        l.id: l
        for l in session.scalars(
            select(Lemma).where(
                Lemma.id.in_({t.chosen_lemma_id for t in tokens if t.chosen_lemma_id})
            )
        ).all()
    }

    prefs = settings_svc.load(session)
    view = []
    unseen_lemmas: set[int] = set()
    image_targets: list[dict] = []
    seen_image_lemmas: set[int] = set()
    for tok in tokens:
        lemma = lemmas.get(tok.chosen_lemma_id or -1)
        st = statuses.get(tok.chosen_lemma_id or -1)
        auto_ignored = bool(lemma and is_auto_ignored(lemma.upos, tok.surface))
        unseen = st is None and not auto_ignored
        if unseen and tok.chosen_lemma_id:
            unseen_lemmas.add(tok.chosen_lemma_id)

        # L'image n'accompagne que les mots encore mal maitrises, et une
        # seule fois par lemme et par page : la repeter a chaque occurrence
        # saturerait la marge.
        if (
            prefs["show_images"]
            and st
            and st.image_path
            and knowledge.show_image_for(st)
            and tok.chosen_lemma_id not in seen_image_lemmas
        ):
            seen_image_lemmas.add(tok.chosen_lemma_id)
            image_targets.append(
                {
                    "token_id": tok.id,
                    "lemma_id": tok.chosen_lemma_id,
                    "url": f"/media/{st.image_path}",
                    "alt": st.image_alt or (lemma.display if lemma else ""),
                    "lemma": lemma.display if lemma else "",
                    "gloss": st.gloss or "",
                }
            )

        view.append(
            {
                "t": tok,
                "lemma": lemma,
                "status": st.status if st and not st.is_ignored else None,
                "ignored": bool(st and st.is_ignored) or (st is None and auto_ignored),
                "unseen": unseen,
                "ambiguous": tok.is_word
                and not tok.is_resolved
                and tok.ambiguity_margin < AMBIGUITY_MARGIN,
            }
        )

    return templates.TemplateResponse(
        request,
        "reader.html",
        {
            "request": request,
            "doc": doc,
            "tokens": view,
            "page": page,
            "coverage": knowledge.text_coverage(session, text_id) if doc.status == "ready" else None,
            "pending": disamb_svc.pending_count(session, text_id, AMBIGUITY_MARGIN),
            "unseen_count": len(unseen_lemmas),
            "image_targets": image_targets,
            "prefs": prefs,
        },
    )


@app.get("/api/texts/{text_id}/status")
def text_status(text_id: int, session: Session = Depends(get_session)):
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    return {
        "status": doc.status,
        "engine": doc.engine,
        "engine_version": doc.engine_version,
        "word_count": doc.word_count,
        "page_count": doc.page_count,
        "error": doc.error_message,
    }


# --------------------------------------------------------------------------
# Panneau lateral
# --------------------------------------------------------------------------
@app.get("/panel/token/{token_id}", response_class=HTMLResponse)
def token_panel(request: Request, token_id: int, session: Session = Depends(get_session)):
    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404)

    lemma = session.get(Lemma, tok.chosen_lemma_id) if tok.chosen_lemma_id else None
    status = session.get(LemmaStatus, tok.chosen_lemma_id) if tok.chosen_lemma_id else None

    candidates = []
    for cand in tok.candidates or []:
        bare = str(cand.get("lemma", "")).lower().rstrip("0123456789#")
        obj = session.scalars(
            select(Lemma).where(Lemma.lemma == bare, Lemma.upos == cand.get("upos"))
        ).first()
        if obj is None:
            continue
        candidates.append(
            {
                "lemma": obj,
                "score": cand.get("score", 0),
                "feats": cand.get("feats") or {},
                "chosen": obj.id == tok.chosen_lemma_id,
            }
        )

    forms = []
    if lemma:
        siblings = session.scalars(
            select(TextToken).where(TextToken.chosen_lemma_id == lemma.id)
        ).all()
        seen: dict[str, int] = {}
        for s in siblings:
            seen[s.surface.lower()] = seen.get(s.surface.lower(), 0) + 1
        forms = sorted(seen.items(), key=lambda kv: -kv[1])[:24]

    existing = (
        session.scalars(select(Card).where(Card.lemma_id == lemma.id)).all()
        if lemma
        else []
    )

    prefs = settings_svc.load(session)
    entries = dict_svc.get_dictionary().lookup(lemma.lemma) if lemma else []
    # Traduction proposee par defaut : premiere entree du dictionnaire.
    # Elle n'est jamais enregistree tant que l'utilisateur ne valide pas.
    suggested = ""
    if prefs["autofill_gloss"] and entries and not (status and status.gloss):
        suggested = entries[0].body

    return templates.TemplateResponse(
        request,
        "_panel.html",
        {
            "request": request,
            "token": tok,
            "lemma": lemma,
            "status": status,
            "candidates": candidates,
            "forms": forms,
            "cards": {c.kind for c in existing},
            "entries": entries,
            "suggested_gloss": suggested,
            "show_dictionary": prefs["panel_dictionary"],
            "sentence": cards_svc.sentence_of(session, tok),
            "llm": dict_svc.llm_available(),
            "occurrences": len(forms),
        },
    )


@app.post("/api/tokens/{token_id}/resolve")
def resolve_token(
    token_id: int,
    lemma_id: int = Form(...),
    scope: str = Form("global"),
    session: Session = Depends(get_session),
):
    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404)
    try:
        result = disamb_svc.resolve(session, token=tok, lemma_id=lemma_id, scope=scope)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "lemma_id": result.lemma_id,
        "scope": result.scope,
        "updated_tokens": result.updated_token_ids,
        "updated_texts": result.updated_texts,
    }


@app.post("/api/lemmas/{lemma_id}/status")
def update_status(
    lemma_id: int,
    status: int | None = Form(None),
    is_ignored: bool | None = Form(None),
    gloss: str | None = Form(None),
    note: str | None = Form(None),
    unlock: bool = Form(False),
    session: Session = Depends(get_session),
):
    if session.get(Lemma, lemma_id) is None:
        raise HTTPException(404)
    try:
        row = knowledge.set_status(
            session,
            lemma_id,
            status=status,
            is_ignored=is_ignored,
            gloss=gloss,
            note=note,
            lock=False if unlock else None,
            manual=status is not None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    token_ids = [
        t.id
        for t in session.scalars(
            select(TextToken).where(TextToken.chosen_lemma_id == lemma_id)
        ).all()
    ]
    return {
        "lemma_id": lemma_id,
        "status": row.status,
        "is_ignored": row.is_ignored,
        "is_locked": row.is_locked,
        "tokens": token_ids,
    }


@app.post("/api/lemmas/{lemma_id}/suggest")
def suggest(lemma_id: int, context: str = Form(""), session: Session = Depends(get_session)):
    lemma = session.get(Lemma, lemma_id)
    if lemma is None:
        raise HTTPException(404)
    gloss = dict_svc.suggest_gloss(lemma.lemma, context)
    if gloss is None:
        raise HTTPException(503, "suggestion indisponible : cle d'API absente ou appel echoue")
    return {"gloss": gloss}


# --------------------------------------------------------------------------
# Fiches
# --------------------------------------------------------------------------
@app.post("/api/cards")
def add_cards(
    lemma_id: int = Form(...),
    kinds: str = Form("la_fr"),
    token_id: int | None = Form(None),
    gloss: str = Form(""),
    session: Session = Depends(get_session),
):
    token = session.get(TextToken, token_id) if token_id else None
    if gloss.strip():
        knowledge.set_status(session, lemma_id, gloss=gloss, manual=False)
    try:
        created = cards_svc.create_cards(
            session,
            lemma_id=lemma_id,
            kinds=[k for k in kinds.split(",") if k],
            token=token,
            gloss=gloss or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"created": [c.id for c in created], "count": len(created)}


@app.get("/cards", response_class=HTMLResponse)
def card_list(request: Request, session: Session = Depends(get_session)):
    rows = session.scalars(select(Card).order_by(Card.due_at)).all()
    lemmas = {l.id: l for l in session.scalars(select(Lemma)).all()}
    return templates.TemplateResponse(
        request,
        "cards.html",
        {
            "request": request,
            "cards": rows,
            "lemmas": lemmas,
            "stats": cards_svc.queue_stats(session),
        },
    )


@app.post("/api/cards/{card_id}/suspend")
def suspend_card(card_id: int, session: Session = Depends(get_session)):
    card = session.get(Card, card_id)
    if card is None:
        raise HTTPException(404)
    card.is_suspended = not card.is_suspended
    return {"card_id": card_id, "is_suspended": card.is_suspended}


@app.post("/api/cards/{card_id}/delete")
def delete_card(card_id: int, session: Session = Depends(get_session)):
    card = session.get(Card, card_id)
    if card:
        session.delete(card)
    return {"deleted": card_id}


# --------------------------------------------------------------------------
# Revision
# --------------------------------------------------------------------------
@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request, session: Session = Depends(get_session)):
    queue = cards_svc.due_queue(session)
    lemma_ids = {c.lemma_id for c in queue}
    lemmas = {
        l.id: l for l in session.scalars(select(Lemma).where(Lemma.id.in_(lemma_ids))).all()
    }
    statuses = {
        r.lemma_id: r
        for r in session.scalars(
            select(LemmaStatus).where(LemmaStatus.lemma_id.in_(lemma_ids))
        ).all()
    }

    # L'image accompagne la fiche : au verso en version (latin vers
    # francais, elle illustre la reponse), au recto en theme et en texte
    # a trous (elle aide a retrouver le mot latin).
    IMAGE_SIDE = {"la_fr": "back", "fr_la": "front", "cloze": "front"}

    payload = []
    for c in queue:
        st = statuses.get(c.lemma_id)
        image = f"/media/{st.image_path}" if st and st.image_path else None
        payload.append(
            {
                "id": c.id,
                "kind": c.kind,
                "kind_label": cards_svc.KIND_LABELS[c.kind],
                "front": c.front,
                "back": c.back,
                "lemma": lemmas[c.lemma_id].display if c.lemma_id in lemmas else "",
                "feats": (c.extra or {}).get("feats", {}),
                "is_new": c.is_new,
                "image": image,
                "image_side": IMAGE_SIDE.get(c.kind, "back") if image else None,
            }
        )
    return templates.TemplateResponse(
        request,
        "review.html",
        {"request": request, "queue": payload, "stats": cards_svc.queue_stats(session)},
    )


@app.post("/api/reviews")
def post_review(
    card_id: int = Form(...),
    button: str = Form(...),
    elapsed_ms: int | None = Form(None),
    session: Session = Depends(get_session),
):
    card = session.get(Card, card_id)
    if card is None:
        raise HTTPException(404)
    try:
        return cards_svc.review(session, card, button, elapsed_ms=elapsed_ms)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


# --------------------------------------------------------------------------
# Statistiques et exports
# --------------------------------------------------------------------------
@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "request": request,
            "progress": knowledge.global_progress(session),
            "weakest": knowledge.weakest_features(session),
            "queue": cards_svc.queue_stats(session),
            "engines": available_engines(),
        },
    )


@app.get("/export/texts/{text_id}.conllu", response_class=PlainTextResponse)
def export_conllu(text_id: int, session: Session = Depends(get_session)):
    try:
        return exporter.to_conllu(session, text_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/export/anki.csv", response_class=PlainTextResponse)
def export_anki(session: Session = Depends(get_session)):
    return exporter.to_anki_csv(session)


@app.get("/export/lemmas.csv", response_class=PlainTextResponse)
def export_lemmas(session: Session = Depends(get_session)):
    return exporter.to_lemma_csv(session)


@app.get("/api/health")
def health():
    return {"status": "ok", "engines": available_engines()}


# --------------------------------------------------------------------------
# Images
# --------------------------------------------------------------------------
@app.get("/media/{name}")
def media(name: str):
    path = image_svc.resolve(name)
    if path is None:
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000"})


def _image_still_used(session: Session, name: str, exclude_lemma: int) -> bool:
    stmt = select(LemmaStatus).where(
        LemmaStatus.image_path == name, LemmaStatus.lemma_id != exclude_lemma
    )
    return session.scalars(stmt).first() is not None


@app.post("/api/lemmas/{lemma_id}/image")
async def set_image(
    lemma_id: int,
    file: UploadFile | None = File(None),
    data_url: str = Form(""),
    alt: str = Form(""),
    session: Session = Depends(get_session),
):
    """Téléversement d'un fichier, ou image collée depuis le presse-papier."""
    if session.get(Lemma, lemma_id) is None:
        raise HTTPException(404)

    try:
        if file is not None and file.filename:
            name = image_svc.save_bytes(await file.read())
        elif data_url.strip():
            name = image_svc.save_data_url(data_url)
        else:
            raise HTTPException(400, "aucune image fournie")
    except image_svc.ImageError as exc:
        raise HTTPException(400, str(exc)) from exc

    row = knowledge.set_status(session, lemma_id, manual=False)
    previous = row.image_path
    row.image_path = name
    row.image_alt = alt.strip() or None
    session.flush()
    if previous and previous != name:
        image_svc.delete_if_orphan(previous, _image_still_used(session, previous, lemma_id))

    return {"lemma_id": lemma_id, "image": name, "url": f"/media/{name}"}


@app.post("/api/lemmas/{lemma_id}/image/delete")
def delete_image(lemma_id: int, session: Session = Depends(get_session)):
    row = session.get(LemmaStatus, lemma_id)
    if row is None or not row.image_path:
        return {"lemma_id": lemma_id, "image": None}
    previous = row.image_path
    row.image_path = None
    row.image_alt = None
    session.flush()
    image_svc.delete_if_orphan(previous, _image_still_used(session, previous, lemma_id))
    return {"lemma_id": lemma_id, "image": None}


# --------------------------------------------------------------------------
# Onglet « Mots »
# --------------------------------------------------------------------------
@app.get("/words", response_class=HTMLResponse)
def words_page(
    request: Request,
    q: str = "",
    status: str = "",
    image: str = "",
    session: Session = Depends(get_session),
):
    status_filter = int(status) if status.isdigit() else None
    rows = knowledge.vocabulary(
        session,
        query=q,
        status_filter=status_filter,
        only_ignored=(status == "ignored"),
        with_image={"yes": True, "no": False}.get(image),
    )
    return templates.TemplateResponse(
        request,
        "words.html",
        {
            "rows": rows,
            "q": q,
            "status": status,
            "image": image,
            "counts": knowledge.status_counts(session),
            "prefs": settings_svc.load(session),
        },
    )


@app.post("/api/lemmas/{lemma_id}/details")
def update_details(
    lemma_id: int,
    gloss: str = Form(""),
    note: str = Form(""),
    session: Session = Depends(get_session),
):
    """Modification de la traduction et de la note depuis l'onglet Mots."""
    if session.get(Lemma, lemma_id) is None:
        raise HTTPException(404)
    row = knowledge.set_status(session, lemma_id, gloss=gloss, note=note, manual=False)
    # Une glose vide doit pouvoir effacer la precedente.
    if not gloss.strip():
        row.gloss = None
    if not note.strip():
        row.note = None
    return {"lemma_id": lemma_id, "gloss": row.gloss, "note": row.note}


# --------------------------------------------------------------------------
# Validation en masse des mots jamais vus
# --------------------------------------------------------------------------
@app.post("/api/texts/{text_id}/validate-unseen")
def validate_unseen(
    text_id: int,
    page: int = Form(0),
    status: int = Form(0),
    session: Session = Depends(get_session),
):
    """Marque comme connus tous les mots encore absents de la base.

    Portée : la page affichée, conformément au choix retenu.
    """
    if session.get(TextDoc, text_id) is None:
        raise HTTPException(404)
    tokens = session.scalars(
        select(TextToken).where(
            TextToken.text_id == text_id,
            TextToken.page_idx == page,
            TextToken.is_word.is_(True),
        )
    ).all()
    return knowledge.validate_unseen(session, tokens, status=status)


# --------------------------------------------------------------------------
# Édition des fiches
# --------------------------------------------------------------------------
@app.post("/api/cards/{card_id}/edit")
def edit_card(
    card_id: int,
    front: str = Form(...),
    back: str = Form(...),
    reset_schedule: bool = Form(False),
    session: Session = Depends(get_session),
):
    card = session.get(Card, card_id)
    if card is None:
        raise HTTPException(404)
    try:
        cards_svc.update_card(
            session, card, front=front, back=back, reset_schedule=reset_schedule
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "card_id": card.id,
        "front": card.front,
        "back": card.back,
        "due_at": card.due_at.isoformat(),
        "interval_days": card.interval_days,
    }


# --------------------------------------------------------------------------
# Reglages d'affichage
# --------------------------------------------------------------------------
@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "prefs": settings_svc.load(session),
            "options": settings_svc.OPTIONS,
            "groups": settings_svc.GROUPS,
            "diagnostic": {
                "asset_version": _asset_version(),
                "files": sorted(
                    (f.name, int(f.stat().st_mtime))
                    for f in (BASE_DIR / "static").glob("*")
                    if f.is_file()
                ),
                "engines": available_engines(),
            },
        },
    )


@app.post("/settings")
async def save_settings(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    settings_svc.save(session, {k: str(v) for k, v in form.items()})
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/reset")
def reset_settings(session: Session = Depends(get_session)):
    settings_svc.reset(session)
    return RedirectResponse("/settings", status_code=303)


# --------------------------------------------------------------------------
# Suppression d'un mot de la liste
# --------------------------------------------------------------------------
@app.post("/api/lemmas/{lemma_id}/forget")
def forget_lemma(lemma_id: int, session: Session = Depends(get_session)):
    """Retire le mot de la liste : statut, glose, note, image et fiches.

    Le lemme lui-meme reste dans le lexique partage (les tokens des textes
    y renvoient), mais il redevient « jamais rencontre » et reapparait en
    bleu a la lecture.
    """
    row = session.get(LemmaStatus, lemma_id)
    image = row.image_path if row else None

    cards = session.scalars(select(Card).where(Card.lemma_id == lemma_id)).all()
    for card in cards:
        session.delete(card)
    if row is not None:
        session.delete(row)
    session.flush()

    if image:
        image_svc.delete_if_orphan(image, _image_still_used(session, image, lemma_id))

    token_ids = [
        t.id
        for t in session.scalars(
            select(TextToken).where(TextToken.chosen_lemma_id == lemma_id)
        ).all()
    ]
    return {"lemma_id": lemma_id, "deleted_cards": len(cards), "tokens": token_ids}
