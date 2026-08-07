from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

try:
    from starlette.middleware.sessions import SessionMiddleware
except ModuleNotFoundError as exc:  # pragma: no cover - dépendance absente
    # Message lisible plutôt qu'une trace : la cause est une dépendance
    # manquante, pas un défaut de l'application.
    raise SystemExit(
        f"Dépendance manquante ({exc.name}).\n"
        "Installez-la puis relancez :\n"
        f"    .venv/bin/pip install {exc.name}\n"
    ) from exc

from .db import get_session, init_db, session_scope
from .deps import (
    MODE_ADMIN,
    MODE_KEY,
    MODE_USER,
    RedirectToLogin,
    current_user,
    redirect,
    require_admin,
    require_user,
)
from .models import (
    Book,
    Card,
    Lemma,
    LemmaStatus,
    PageRead,
    TextDoc,
    TextToken,
    User,
)
from .nlp.registry import available_engines
from .services import auth as auth_svc
from .services import gaffiot as gaffiot_svc
from .services import lemma_merge
from .services import cards as cards_svc
from .services import dictionary as dict_svc
from .services import images as image_svc
from .services import disambiguation as disamb_svc
from .services import exporter, knowledge
from .services import lemma_reference as lemref
from .services import settings as settings_svc
from .nlp.normalize import lemma_key
from .services.importer import (
    get_or_create_form,
    get_or_create_lemma,
    AMBIGUITY_MARGIN,
    create_text,
    is_auto_ignored,
    process_in_background,
)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


def _session_secret() -> str:
    """Cle de signature des sessions, persistee a cote de la base."""
    import secrets

    from .db import DATA_DIR

    path = DATA_DIR / "session.key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    key = secrets.token_urlsafe(48)
    path.write_text(key, encoding="utf-8")
    path.chmod(0o600)
    return key
@asynccontextmanager
async def lifespan(_: FastAPI):
    """Prepare la base et garantit l'existence d'un administrateur."""
    init_db()
    with session_scope() as session:
        auth_svc.ensure_default_admin(session)
        # Deux normalisations coexistaient : « provincia » et « prouincia »
        # pouvaient designer le meme mot. On fusionne au demarrage, en
        # reportant statuts et fiches.
        rapport = lemma_merge.merge_all(session)
        from .services import lexicon as lexicon_svc

        premier = lexicon_svc.ensure_seeded(session)
        if premier:
            log.warning("lexique constitué : %d lemmes", premier["added"])

        vedettes = lemma_merge.backfill_headwords(session)
        if vedettes:
            log.info("%d vedette(s) renseignée(s) depuis le Gaffiot", vedettes)
        if rapport["merged"]:
            log.warning(
                "%d lemme(s) en double fusionné(s) — %d statut(s) et %d fiche(s) reportés",
                rapport["merged"], rapport["statuses_moved"], rapport["cards_moved"],
            )
    yield


app = FastAPI(title="Lecteur latin", docs_url="/api/docs", lifespan=lifespan)

from .api_v1 import router as api_v1_router  # noqa: E402 - dépend de app

app.include_router(api_v1_router)

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




@app.middleware("http")
async def _attach_user(request: Request, call_next):
    """Rend le compte connecte accessible aux gabarits.

    Les dependances FastAPI ne sont pas visibles depuis Jinja : on pose
    donc l'utilisateur sur `request.state`, lu par la barre de navigation.
    """
    request.state.user = None
    request.state.mode = MODE_ADMIN
    try:
        user_id = request.session.get("user_id")
        request.state.mode = request.session.get(MODE_KEY, MODE_ADMIN)
    except Exception:  # noqa: BLE001 - session absente (fichiers statiques)
        user_id = None
    if user_id:
        with session_scope() as session:
            account = session.get(User, user_id)
            if account is not None:
                session.expunge(account)
                request.state.user = account
    return await call_next(request)


# Session signée dans un cookie.
#
# ATTENTION A L'ORDRE : Starlette place le dernier middleware ajouté à
# l'extérieur de la pile, donc l'exécute en premier. La session doit être
# ajoutée APRÈS `_attach_user`, qui la lit — sinon celui-ci s'exécute
# avant qu'elle n'existe, ne trouve aucun compte, et la barre de
# navigation se retrouve vide.
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    session_cookie="lecteur_latin",
    same_site="lax",
    https_only=False,
    max_age=60 * 60 * 24 * 30,
)


@app.exception_handler(RedirectToLogin)
def _redirect_to_login(request: Request, exc: RedirectToLogin):
    return RedirectResponse(exc.destination, status_code=303)


@app.exception_handler(StarletteHTTPException)
def _http_error(request: Request, exc: StarletteHTTPException):
    """Page d'erreur lisible pour la navigation, JSON pour l'API.

    Sans ce gabarit, une adresse erronée affichait le JSON brut de
    FastAPI ({"detail": "Not Found"}), sans barre de navigation ni
    chemin de retour.
    """
    veut_du_json = request.url.path.startswith(("/api/", "/panel/")) or (
        "text/html" not in request.headers.get("accept", "")
    )
    if veut_du_json:
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                            headers=exc.headers)

    est_admin = request.state.user and request.state.user.is_admin
    en_gestion = est_admin and request.state.mode != "user"
    if exc.status_code == 404:
        titre, message = "Page introuvable", (
            "Cette adresse ne correspond à rien — le texte a peut-être été "
            "supprimé, ou le lien est erroné."
        )
    else:
        titre = f"Erreur {exc.status_code}"
        message = str(exc.detail or "Quelque chose s'est mal passé.")
    return templates.TemplateResponse(
        request,
        "error.html",
        {
            "titre": titre,
            "message": message,
            "retour_url": "/admin" if en_gestion else "/bibliotheque",
            "retour_libelle": "Retour au tableau de bord"
            if en_gestion else "Retour à la bibliothèque",
        },
        status_code=exc.status_code,
    )



# --------------------------------------------------------------------------
# Connexion, inscription, compte
# --------------------------------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, user: User | None = Depends(current_user)):
    # Un invite n'est pas « connecte » : il doit pouvoir rejoindre son
    # compte nomme, quitte a abandonner ce qu'il a marque en passant.
    if user is not None and not user.is_guest:
        return redirect("/admin" if user.is_admin else "/bibliotheque")
    return templates.TemplateResponse(request, "login.html", {"mode": "login"})


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    account = auth_svc.authenticate(session, username, password)
    if account is None:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"mode": "login", "error": "Identifiant ou mot de passe incorrect.",
             "username": username},
            status_code=401,
        )
    request.session["user_id"] = account.id
    return redirect("/admin" if account.is_admin else "/bibliotheque")


@app.get("/register", response_class=HTMLResponse)
def register_page(request: Request, user: User | None = Depends(current_user)):
    if user is not None and not user.is_guest:
        return redirect("/admin" if user.is_admin else "/bibliotheque")
    return templates.TemplateResponse(
        request, "login.html", {"mode": "register", "guest": user is not None}
    )


@app.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    password2: str = Form(""),
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user),
):
    """Inscription libre : tout nouveau compte est un compte de lecture.

    Si le visiteur lisait deja sous un compte de passage, on convertit ce
    compte plutot que d'en ouvrir un second : ce qu'il a marque en lisant
    le suit dans son compte nomme.
    """
    invite = user if (user is not None and user.is_guest) else None
    if password2 and password != password2:
        return templates.TemplateResponse(
            request, "login.html",
            {"mode": "register", "error": "Les deux mots de passe diffèrent.",
             "username": username, "guest": invite is not None},
            status_code=400,
        )
    try:
        if invite is not None:
            account = auth_svc.promote_guest(session, invite, username, password)
        else:
            account = auth_svc.create_user(session, username, password)
    except auth_svc.AuthError as exc:
        return templates.TemplateResponse(
            request, "login.html",
            {"mode": "register", "error": str(exc), "username": username,
             "guest": invite is not None},
            status_code=400,
        )
    session.flush()
    request.session["user_id"] = account.id
    return redirect("/bibliotheque")


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return redirect("/login")


@app.get("/account", response_class=HTMLResponse)
def account_page(request: Request, user: User = Depends(current_user)):
    if user is None:
        raise RedirectToLogin()
    # Un compte de passage n'a pas de mot de passe a changer : la seule
    # action qui ait un sens pour lui est de se donner un nom.
    if user.is_guest:
        return redirect("/register")
    return templates.TemplateResponse(request, "account.html", {"account": user})


@app.post("/account/password")
def change_password(
    request: Request,
    current: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if user is None:
        raise RedirectToLogin()
    if not auth_svc.verify_password(current, user.password_hash):
        return templates.TemplateResponse(
            request, "account.html",
            {"account": user, "error": "Mot de passe actuel incorrect."},
            status_code=400,
        )
    try:
        auth_svc.set_password(session, user, password)
    except auth_svc.AuthError as exc:
        return templates.TemplateResponse(
            request, "account.html",
            {"account": user, "error": str(exc)}, status_code=400,
        )
    return templates.TemplateResponse(
        request, "account.html", {"account": user, "message": "Mot de passe modifié."}
    )


# --------------------------------------------------------------------------
# Bibliotheque
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    """Vitrine : ce qu'est l'application, pour qui arrive sans rien savoir.

    Elle ne depend pas de `require_user`, et c'est deliberé : atterrir sur
    la racine n'ouvre donc aucun compte de passage. Les robots
    d'indexation, qui ne gardent pas de cookie et frappent surtout la
    racine, cessent du meme coup d'en semer un par visite.
    """
    return templates.TemplateResponse(request, "home.html", {})


@app.get("/bibliotheque", response_class=HTMLResponse)
def library(
    request: Request,
    q: str = "",
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Accueil du lecteur : le catalogue, classé par auteur.

    La recherche porte sur les titres de livres et de textes. Chaque
    livre affiche sa progression, c'est-à-dire la part de son vocabulaire
    déjà connue du lecteur.
    """
    recherche = q.strip().lower()

    livres = session.scalars(
        select(Book).order_by(Book.sort_order, Book.author, Book.title)
    ).all()
    textes = session.scalars(
        select(TextDoc).order_by(TextDoc.chapter_idx, TextDoc.created_at.desc())
    ).all()

    couvertures = {
        t.id: (knowledge.text_coverage(session, user.id, t.id) if t.status == "ready" else None)
        for t in textes
    }

    def concerne(livre: Book) -> bool:
        if not recherche:
            return True
        champs = [livre.title, livre.subtitle or "", livre.author or ""]
        champs += [t.title for t in livre.texts]
        return any(recherche in c.lower() for c in champs)

    # Un livre progresse comme la moyenne de ses chapitres, ponderee par
    # leur longueur : un chapitre de mille mots pese plus qu'un de cent.
    par_auteur: dict[str, list] = {}
    for livre in livres:
        if not concerne(livre):
            continue
        chapitres = [
            {"doc": t, "coverage": couvertures.get(t.id)} for t in livre.texts
        ]
        mots = sum(
            (c["coverage"] or {}).get("total_words", 0) for c in chapitres
        )
        connus = sum(
            (c["coverage"] or {}).get("total_words", 0)
            * (c["coverage"] or {}).get("known_ratio", 0)
            for c in chapitres
        )
        par_auteur.setdefault(livre.display_author, []).append(
            {
                "book": livre,
                "chapters": chapitres,
                "words": mots,
                "ratio": (connus / mots) if mots else 0.0,
                "spectrum": _merge_spectrum(chapitres),
            }
        )

    isoles = [
        {"doc": t, "coverage": couvertures.get(t.id)}
        for t in textes
        if t.book_id is None
        and (not recherche or recherche in t.title.lower())
    ]

    # Reprendre la lecture : le dernier texte ouvert et pas termine.
    reprise = next(
        (
            {"doc": t, "coverage": couvertures.get(t.id)}
            for t in sorted(textes, key=lambda x: x.created_at, reverse=True)
            if t.status == "ready"
        ),
        None,
    )

    return templates.TemplateResponse(
        request,
        "library.html",
        {
            "request": request,
            "by_author": dict(sorted(par_auteur.items())),
            "loose": isoles,
            "resume": reprise,
            "q": q,
            "queue": cards_svc.queue_stats(session, user.id),
            "progress": knowledge.global_progress(session, user.id),
        },
    )


def _merge_spectrum(chapitres: list) -> list:
    """Additionne les repartitions de plusieurs chapitres."""
    from collections import Counter

    totaux: Counter = Counter()
    libelles: dict[str, str] = {}
    total = 0
    for chapitre in chapitres:
        for tranche in (chapitre["coverage"] or {}).get("spectrum", []):
            totaux[tranche["key"]] += tranche["count"]
            libelles[tranche["key"]] = tranche["label"]
            total += tranche["count"]
    if not total:
        return []
    ordre = ["s0", "s1", "s2", "s3", "s4", "unseen"]
    return [
        {
            "key": cle,
            "label": libelles[cle],
            "count": totaux[cle],
            "share": round(100 * totaux[cle] / total, 1),
        }
        for cle in ordre
        if totaux.get(cle)
    ]


@app.post("/texts")
def add_text(
    title: str = Form(""),
    author: str = Form(""),
    content: str = Form(...),
    language_stage: str = Form("classical"),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
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
def relemmatize(
    text_id: int,
    engine: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    process_in_background(text_id, engine or None)
    return RedirectResponse(f"/texts/{text_id}", status_code=303)


@app.post("/texts/{text_id}/delete")
def delete_text(
    text_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    doc = session.get(TextDoc, text_id)
    if doc:
        session.delete(doc)
    return RedirectResponse("/", status_code=303)


# --------------------------------------------------------------------------
# Lecture
# --------------------------------------------------------------------------
def _page_spectrum(tokens: list, statuses: dict) -> dict:
    """Repartition du vocabulaire de la page, du mieux su au jamais vu.

    Meme lecture que la jauge de la bibliotheque : les couleurs sont
    celles des mots eux-memes, si bien qu'on interprete la barre sans
    legende.
    """
    from collections import Counter

    compte: Counter = Counter()
    total = 0
    for tok in tokens:
        if not tok.is_word or not tok.chosen_lemma_id:
            continue
        statut = statuses.get(tok.chosen_lemma_id)
        if statut is not None and statut.is_ignored:
            continue
        total += 1
        compte["unseen" if statut is None else f"s{statut.status}"] += 1

    if not total:
        return {"total": 0, "known_ratio": 0.0, "spectrum": []}

    ordre = ["s0", "s1", "s2", "s3", "s4", "unseen"]
    libelles = {
        "s0": "maîtrisé", "s1": "presque su", "s2": "en cours",
        "s3": "fragile", "s4": "inconnu", "unseen": "jamais rencontré",
    }
    connus = compte["s0"] + compte["s1"]
    return {
        "total": total,
        "known_ratio": connus / total,
        "spectrum": [
            {
                "key": cle,
                "label": libelles[cle],
                "count": compte[cle],
                "share": round(100 * compte[cle] / total, 1),
            }
            for cle in ordre
            if compte.get(cle)
        ],
    }


@app.get("/texts/{text_id}", response_class=HTMLResponse)
def read_text(
    request: Request,
    text_id: int,
    page: int = 0,
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)

    tokens = session.scalars(
        select(TextToken)
        .where(TextToken.text_id == text_id, TextToken.page_idx == page)
        .order_by(TextToken.idx)
    ).all()
    statuses = knowledge.statuses_for_tokens(session, user.id, tokens)
    lemmas = {
        l.id: l
        for l in session.scalars(
            select(Lemma).where(
                Lemma.id.in_({t.chosen_lemma_id for t in tokens if t.chosen_lemma_id})
            )
        ).all()
    }

    prefs = settings_svc.load(session, user.id)
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
            and lemma
            and lemma.image_path
            and knowledge.show_image_for(st)
            and tok.chosen_lemma_id not in seen_image_lemmas
        ):
            seen_image_lemmas.add(tok.chosen_lemma_id)
            image_targets.append(
                {
                    "token_id": tok.id,
                    "lemma_id": tok.chosen_lemma_id,
                    "url": f"/media/{lemma.image_path}",
                    "alt": lemma.image_alt or lemma.display,
                    "lemma": lemma.display if lemma else "",
                    "gloss": (st.gloss if st and st.gloss else lemma.shared_gloss) or "",
                }
            )

        # Glose affichee sous le mot au survol : la traduction saisie par
        # l'utilisateur prime sur celle du lexique.
        tip = ""
        if lemma:
            # Traduction personnelle, puis celle de l'administrateur,
            # puis le lexique embarqué.
            tip = (
                (st.gloss if st and st.gloss else "")
                or (lemma.shared_gloss or "")
                or dict_svc.short_gloss(lemma.lemma)
            )

        view.append(
            {
                "t": tok,
                "lemma": lemma,
                "tip": tip,
                "status": st.status if st and not st.is_ignored else None,
                "ignored": bool(st and st.is_ignored) or (st is None and auto_ignored),
                "unseen": unseen,
                "enclitic": tok.is_enclitic,
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
            "coverage": knowledge.text_coverage(session, user.id, text_id) if doc.status == "ready" else None,
            "pending": disamb_svc.pending_count(session, text_id, AMBIGUITY_MARGIN),
            "unseen_count": len(unseen_lemmas),
            # Jauge de la page affichee, figee au chargement.
            "page_coverage": _page_spectrum(tokens, statuses),
            "page_read": session.get(PageRead, (user.id, text_id, page)) is not None,
            "pages_read": session.scalar(
                select(func.count())
                .select_from(PageRead)
                .where(PageRead.user_id == user.id, PageRead.text_id == text_id)
            ) or 0,
            # Un texte sans mot illustre n'a pas besoin de gouttieres :
            # la place revient a la marge, et le seuil de bascule baisse
            # d'autant.
            "has_images": bool(image_targets),
            "image_targets": image_targets,
            "prefs": prefs,
            "video_id": doc.video_id,
            "audio_path": doc.audio_path,
            "cues": doc.cues or [],
        },
    )


@app.get("/api/texts/{text_id}/status")
def text_status(text_id: int, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
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
def token_panel(request: Request, token_id: int, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404)

    lemma = session.get(Lemma, tok.chosen_lemma_id) if tok.chosen_lemma_id else None
    status = session.get(LemmaStatus, (user.id, tok.chosen_lemma_id)) if tok.chosen_lemma_id else None

    # Une ambiguïté tranchée par l'administrateur ne concerne plus le
    # lecteur : il ne voit que le lemme retenu. Lui présenter les
    # candidats écartés serait rouvrir une question déjà réglée.
    candidates = []
    if not tok.is_resolved:
        for cand in tok.candidates or []:
            bare = lemma_key(str(cand.get("lemma", "")))
            obj = session.scalars(
                select(Lemma).where(
                    Lemma.lemma == bare, Lemma.upos == cand.get("upos")
                )
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

    prefs = settings_svc.load(session, user.id)
    entries = (
        dict_svc.lookup_all(lemma.lemma, tok.surface) if lemma else []
    )
    # Traduction proposee par defaut. Elle n'est jamais enregistree tant
    # que l'utilisateur ne valide pas.
    #
    # Ordre : sa propre traduction si elle existe, sinon celle que
    # l'administrateur a redigee pour tous, sinon le Gaffiot.
    suggested = ""
    if not (status and status.gloss):
        if lemma and lemma.shared_gloss:
            suggested = lemma.shared_gloss
        elif prefs["autofill_gloss"] and lemma:
            suggested = gaffiot_svc.get_gaffiot().gloss(lemma.lemma, lemma.upos)

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
            "show_help": prefs["show_help"],
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
    admin: User = Depends(require_admin),
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
    user: User = Depends(require_user),
):
    if session.get(Lemma, lemma_id) is None:
        raise HTTPException(404)
    try:
        row = knowledge.set_status(
            session,
            user.id,
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
def suggest(lemma_id: int, context: str = Form(""), session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
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
    user: User = Depends(require_user),
):
    token = session.get(TextToken, token_id) if token_id else None
    if gloss.strip():
        knowledge.set_status(session, user.id, lemma_id, gloss=gloss, manual=False)
    try:
        created = cards_svc.create_cards(
            session,
            user.id,
            lemma_id=lemma_id,
            kinds=[k for k in kinds.split(",") if k],
            token=token,
            gloss=gloss or None,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"created": [c.id for c in created], "count": len(created)}


@app.get("/cards")
def card_list_redirect(
    user: User = Depends(require_user),
):
    """Les fiches ont rejoint la page de révision."""
    return redirect("/review#liste")


@app.get("/cards/old", response_class=HTMLResponse)
def card_list(request: Request, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    rows = session.scalars(select(Card).order_by(Card.due_at)).all()
    lemmas = {l.id: l for l in session.scalars(select(Lemma)).all()}
    return templates.TemplateResponse(
        request,
        "cards.html",
        {
            "request": request,
            "cards": rows,
            "lemmas": lemmas,
            "stats": cards_svc.queue_stats(session, user.id),
        },
    )


@app.post("/api/cards/{card_id}/suspend")
def suspend_card(card_id: int, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    card = session.get(Card, card_id)
    if card is None:
        raise HTTPException(404)
    card.is_suspended = not card.is_suspended
    return {"card_id": card_id, "is_suspended": card.is_suspended}


@app.post("/api/cards/{card_id}/delete")
def delete_card(card_id: int, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    card = session.get(Card, card_id)
    if card:
        session.delete(card)
    return {"deleted": card_id}


# --------------------------------------------------------------------------
# Revision
# --------------------------------------------------------------------------
@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    queue = cards_svc.due_queue(session, user.id)
    lemma_ids = {c.lemma_id for c in queue}
    lemmas = {
        l.id: l for l in session.scalars(select(Lemma).where(Lemma.id.in_(lemma_ids))).all()
    }
    lemmas_by_id = {
        l.id: l for l in session.scalars(select(Lemma).where(Lemma.id.in_(lemma_ids))).all()
    }

    # L'image accompagne la fiche : au verso en version (latin vers
    # francais, elle illustre la reponse), au recto en theme et en texte
    # a trous (elle aide a retrouver le mot latin).
    IMAGE_SIDE = {"la_fr": "back", "fr_la": "front", "cloze": "front"}

    payload = []
    for c in queue:
        illustrated = lemmas_by_id.get(c.lemma_id)
        image = (
            f"/media/{illustrated.image_path}"
            if illustrated and illustrated.image_path
            else None
        )
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
        {
            "request": request,
            "queue": payload,
            "stats": cards_svc.queue_stats(session, user.id),
            "cards": session.scalars(
                select(Card).where(Card.user_id == user.id).order_by(Card.due_at)
            ).all(),
            "lemmas": {
                l.id: l
                for l in session.scalars(
                    select(Lemma).where(
                        Lemma.id.in_(
                            select(Card.lemma_id).where(Card.user_id == user.id)
                        )
                    )
                ).all()
            },
        },
    )


@app.post("/api/reviews")
def post_review(
    card_id: int = Form(...),
    button: str = Form(...),
    elapsed_ms: int | None = Form(None),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
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
def stats_page(request: Request, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "request": request,
            "progress": knowledge.global_progress(session, user.id),
            "weakest": knowledge.weakest_features(session, user.id),
            "queue": cards_svc.queue_stats(session, user.id),
            "engines": available_engines(),
        },
    )


@app.get("/export/texts/{text_id}.conllu", response_class=PlainTextResponse)
def export_conllu(text_id: int, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    try:
        return exporter.to_conllu(session, text_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/export/anki.csv", response_class=PlainTextResponse)
def export_anki(session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    return exporter.to_anki_csv(session)


@app.get("/export/lemmas.csv", response_class=PlainTextResponse)
def export_lemmas(session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    return exporter.to_lemma_csv(session)


@app.get("/sw.js", include_in_schema=False)
def service_worker():
    """Sert le service worker depuis la racine.

    Un service worker ne peut contrôler que les pages sous son chemin :
    servi depuis /static/, il ne verrait rien. D'où cette route dédiée.
    """
    return FileResponse(
        Path(__file__).resolve().parent / "static" / "sw.js",
        media_type="application/javascript",
    )


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
    stmt = select(Lemma).where(Lemma.image_path == name, Lemma.id != exclude_lemma)
    return session.scalars(stmt).first() is not None


@app.post("/api/lemmas/{lemma_id}/image")
async def set_image(
    lemma_id: int,
    file: UploadFile | None = File(None),
    data_url: str = Form(""),
    alt: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
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

    row = session.get(Lemma, lemma_id)
    previous = row.image_path
    row.image_path = name
    row.image_alt = alt.strip() or None
    session.flush()
    if previous and previous != name:
        image_svc.delete_if_orphan(previous, _image_still_used(session, previous, lemma_id))

    return {"lemma_id": lemma_id, "image": name, "url": f"/media/{name}"}


@app.post("/api/lemmas/{lemma_id}/image/delete")
def delete_image(
    lemma_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    row = session.get(Lemma, lemma_id)
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
    user: User = Depends(require_user),
):
    status_filter = int(status) if status.isdigit() else None
    rows = knowledge.vocabulary(
        session,
        user.id,
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
            "counts": knowledge.status_counts(session, user.id),
            "prefs": settings_svc.load(session, user.id),
        },
    )


@app.post("/api/lemmas/{lemma_id}/details")
def update_details(
    lemma_id: int,
    gloss: str = Form(""),
    note: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Modification de la traduction et de la note depuis l'onglet Mots."""
    if session.get(Lemma, lemma_id) is None:
        raise HTTPException(404)
    row = knowledge.set_status(session, user.id, lemma_id, gloss=gloss, note=note, manual=False)
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
    user: User = Depends(require_user),
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
    return knowledge.validate_unseen(session, user.id, tokens, status=status)


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
    user: User = Depends(require_user),
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
def settings_page(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if user is None:
        raise RedirectToLogin()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "prefs": settings_svc.load(session, user.id),
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
                "lemma_reference": lemref.status(),
                "gaffiot": gaffiot_svc.status(),
                "form_table": lemref.form_table_status(),
                "short_lexicon": dict_svc.short_lexicon_status(),
                "stardicts": dict_svc.stardict_status(),
                "stardict_dir": str(dict_svc.STARDICT_DIR),
            },
            "purge": knowledge.summarize_without_content(session, user.id),
        },
    )


@app.post("/settings")
async def save_settings(
    request: Request,
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if user is None:
        raise RedirectToLogin()
    form = await request.form()
    settings_svc.save(session, user.id, {k: str(v) for k, v in form.items()})
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/reset")
def reset_settings(
    session: Session = Depends(get_session),
    user: User = Depends(current_user),
):
    if user is None:
        raise RedirectToLogin()
    settings_svc.reset(session, user.id)
    return RedirectResponse("/settings", status_code=303)


# --------------------------------------------------------------------------
# Suppression d'un mot de la liste
# --------------------------------------------------------------------------
@app.post("/api/lemmas/{lemma_id}/forget")
def forget_lemma(lemma_id: int, session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Retire le mot de la liste : statut, glose, note, image et fiches.

    Le lemme lui-meme reste dans le lexique partage (les tokens des textes
    y renvoient), mais il redevient « jamais rencontre » et reapparait en
    bleu a la lecture.
    """
    row = session.get(LemmaStatus, (user.id, lemma_id))

    cards = session.scalars(
        select(Card).where(Card.user_id == user.id, Card.lemma_id == lemma_id)
    ).all()
    for card in cards:
        session.delete(card)
    if row is not None:
        session.delete(row)
    session.flush()

    token_ids = [
        t.id
        for t in session.scalars(
            select(TextToken).where(TextToken.chosen_lemma_id == lemma_id)
        ).all()
    ]
    return {"lemma_id": lemma_id, "deleted_cards": len(cards), "tokens": token_ids}


@app.post("/settings/purge-empty")
def purge_empty_words(
    keep_ignored: bool = Form(False),
    keep_noted: bool = Form(False),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Retire de la liste du lecteur les mots sans traduction ni image.

    Les images ne sont pas touchees : depuis le passage au multi-compte,
    elles appartiennent au lemme et sont partagees. Un lecteur qui fait
    le menage chez lui ne doit pas les effacer pour les autres.
    """
    knowledge.purge_without_content(
        session, user.id, keep_ignored=keep_ignored, keep_noted=keep_noted
    )
    return redirect("/settings")


# --------------------------------------------------------------------------
# Espace d'administration
# --------------------------------------------------------------------------
def _admin_context(session: Session, admin: User) -> dict:
    """Donnees du tableau de bord.

    Extrait de la vue : la route d'import video doit pouvoir reafficher
    la meme page en cas d'echec, sans dupliquer ces requetes.
    """
    texts = session.scalars(select(TextDoc).order_by(TextDoc.created_at.desc())).all()
    users = [u for u in auth_svc.list_users(session) if not u.is_admin]

    # Progression de chaque lecteur, sans entrer dans le detail : c'est un
    # tableau de bord, pas une surveillance.
    progress = []
    for account in users:
        counts = knowledge.status_counts(session, account.id)
        connus = sum(n for s, n in counts["by_status"].items() if s <= 1)
        suivis = sum(counts["by_status"].values())
        progress.append(
            {
                "user": account,
                "tracked": suivis,
                "known": connus,
                "cards": cards_svc.queue_stats(session, account.id),
            }
        )

    illustrated = session.scalar(
        select(func.count()).select_from(Lemma).where(Lemma.image_path.is_not(None))
    )
    glossed = session.scalar(
        select(func.count()).select_from(Lemma).where(Lemma.shared_gloss.is_not(None))
    )
    pending = session.scalar(
        select(func.count()).select_from(TextToken).where(
            TextToken.is_word.is_(True),
            TextToken.is_resolved.is_(False),
            TextToken.ambiguity_margin < AMBIGUITY_MARGIN,
        )
    )

    return {
        "texts": texts,
        "progress": progress,
        "engines": available_engines(),
        "illustrated": illustrated or 0,
        "glossed": glossed or 0,
        "pending_arbitrations": pending or 0,
        "default_password": auth_svc.uses_default_password(session),
    }


@app.get("/admin", response_class=HTMLResponse)
def admin_home(
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    return templates.TemplateResponse(
        request, "admin.html", _admin_context(session, admin)
    )


PAGE_SIZE = 200


@app.get("/admin/lemmas", response_class=HTMLResponse)
def admin_lemmas(
    request: Request,
    q: str = "",
    filtre: str = "",
    dans_textes: str = "",
    page: int = 0,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Le lexique entier, glose et image par lemme.

    La table des lemmes est le Gaffiot : plus de 70 000 entrees. La liste
    est donc paginee, et une case permet de ne garder que les lemmes
    effectivement rencontres dans les textes — les seuls qui meritent
    qu'on s'en occupe.
    """
    rencontres = select(TextToken.chosen_lemma_id).where(
        TextToken.chosen_lemma_id.is_not(None)
    )

    stmt = select(Lemma)
    if dans_textes:
        stmt = stmt.where(Lemma.id.in_(rencontres))
    if q.strip():
        motif = f"%{q.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Lemma.lemma).like(motif)
            | func.lower(func.coalesce(Lemma.headword, "")).like(motif)
            | func.lower(func.coalesce(Lemma.shared_gloss, "")).like(motif)
        )
    if filtre == "image":
        stmt = stmt.where(Lemma.image_path.is_not(None))
    elif filtre == "sans_image":
        stmt = stmt.where(Lemma.image_path.is_(None))
    elif filtre == "sans_glose":
        stmt = stmt.where(Lemma.shared_gloss.is_(None))

    total = session.scalar(
        select(func.count()).select_from(stmt.subquery())
    ) or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))

    lignes = session.scalars(
        stmt.order_by(Lemma.lemma, Lemma.homonym_idx)
        .offset(page * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).all()

    vus = {
        row[0] for row in session.execute(rencontres.distinct()).all()
    }

    return templates.TemplateResponse(
        request,
        "admin_lemmas.html",
        {
            "rows": lignes,
            "seen": vus,
            "total": total,
            "page": page,
            "pages": pages,
            "q": q,
            "filtre": filtre,
            "dans_textes": dans_textes,
            "gaffiot_gloss": gaffiot_svc.get_gaffiot().gloss,
            "lexicon_size": session.scalar(select(func.count()).select_from(Lemma)) or 0,
        },
    )


@app.post("/admin/lemmas/add")
def admin_add_lemma(
    key: str = Form(...),
    upos: str = Form("X"),
    headword: str = Form(""),
    gloss: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Ajoute un lemme absent du Gaffiot.

    Il est consigné dans un fichier distinct de la source, pour qu'on ne
    mélange pas les deux, et survit ainsi à une reconstruction du lexique.
    """
    from .services import lexicon as lexicon_svc

    # Le Gaffiot fait foi : s'il connait deja le mot, on ne cree pas de
    # doublon d'appoint, on renvoie vers l'entree existante.
    if gaffiot_svc.get_gaffiot().knows(key):
        return redirect("/admin/lemmas?q=" + key.strip())

    try:
        lexicon_svc.save_addition(key, upos, headword, gloss)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    lemme = get_or_create_lemma(session, key, upos)
    session.flush()
    if headword.strip():
        lemme.headword = headword.strip()
    if gloss.strip():
        lemme.shared_gloss = gloss.strip()
    return redirect("/admin/lemmas?q=" + key.strip())


@app.post("/admin/lemmas/{lemma_id}/remove")
def admin_remove_lemma(
    lemma_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Retire un lemme d'appoint. Le Gaffiot, lui, n'est jamais amputé."""
    from .services import lexicon as lexicon_svc

    lemme = session.get(Lemma, lemma_id)
    if lemme is None:
        raise HTTPException(404)
    if gaffiot_svc.get_gaffiot().knows(lemme.lemma):
        raise HTTPException(
            400, "ce lemme vient du Gaffiot : il ne peut pas être retiré"
        )
    lexicon_svc.remove_addition(lemme.lemma, lemme.upos)
    session.query(TextToken).filter_by(chosen_lemma_id=lemme.id).update(
        {TextToken.chosen_lemma_id: None}, synchronize_session=False
    )
    session.delete(lemme)
    return redirect("/admin/lemmas")


@app.post("/admin/lexicon/rebuild")
def admin_rebuild_lexicon(
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Reconstruit le lexique depuis le Gaffiot.

    Opération destructive : les statuts et les fiches de tous les
    lecteurs pointent vers des identifiants de lemmes et disparaissent
    avec eux. L'interface le fait confirmer.
    """
    from .services import lexicon as lexicon_svc

    rapport = lexicon_svc.seed_lemmas(session, reset=True)
    log.warning(
        "lexique reconstruit : %d lemmes, %d statut(s) et %d fiche(s) effacés",
        rapport["added"],
        rapport["cleared"].get("statuses", 0),
        rapport["cleared"].get("cards", 0),
    )
    return redirect("/admin/lemmas")


@app.post("/api/admin/lemmas/{lemma_id}/gloss")
def admin_set_gloss(
    lemma_id: int,
    gloss: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    lemma = session.get(Lemma, lemma_id)
    if lemma is None:
        raise HTTPException(404)
    lemma.shared_gloss = gloss.strip() or None
    return {"lemma_id": lemma_id, "gloss": lemma.shared_gloss}


@app.get("/admin/arbitrate", response_class=HTMLResponse)
def admin_arbitrate(
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """File des mots ambigus à trancher, tous textes confondus."""
    tokens = session.scalars(
        select(TextToken)
        .where(
            TextToken.is_word.is_(True),
            TextToken.is_resolved.is_(False),
            TextToken.ambiguity_margin < AMBIGUITY_MARGIN,
        )
        .order_by(TextToken.text_id, TextToken.idx)
        .limit(200)
    ).all()

    entries = []
    seen: set[int] = set()
    for tok in tokens:
        # Une forme ne se présente qu'une fois : l'arbitrage se propage.
        if tok.form_id in seen:
            continue
        seen.add(tok.form_id)
        entries.append(
            {
                "token": tok,
                "text": session.get(TextDoc, tok.text_id),
                "sentence": cards_svc.sentence_of(session, tok),
                "candidates": tok.candidates or [],
                "chosen": session.get(Lemma, tok.chosen_lemma_id)
                if tok.chosen_lemma_id
                else None,
            }
        )

    return templates.TemplateResponse(
        request, "admin_arbitrate.html", {"entries": entries}
    )


@app.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "users": auth_svc.list_users(session),
            "me": admin,
            "guests": auth_svc.count_guests(session),
        },
    )


@app.post("/admin/users/create")
def admin_create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    is_admin: bool = Form(False),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    try:
        auth_svc.create_user(session, username, password, is_admin=is_admin)
    except auth_svc.AuthError as exc:
        return templates.TemplateResponse(
            request,
            "admin_users.html",
            {"users": auth_svc.list_users(session), "me": admin, "error": str(exc)},
            status_code=400,
        )
    return redirect("/admin/users")


@app.post("/admin/users/{user_id}/password")
def admin_reset_password(
    user_id: int,
    password: str = Form(...),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    account = session.get(User, user_id)
    if account is None:
        raise HTTPException(404)
    try:
        auth_svc.set_password(session, account, password)
    except auth_svc.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    return redirect("/admin/users")


@app.post("/admin/users/{user_id}/delete")
def admin_delete_user(
    user_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    account = session.get(User, user_id)
    if account is None:
        raise HTTPException(404)
    if account.id == admin.id:
        raise HTTPException(400, "vous ne pouvez pas supprimer votre propre compte")
    if account.is_admin and auth_svc.count_users(session, admins=True) <= 1:
        raise HTTPException(400, "il doit rester au moins un administrateur")
    # Les statuts, fiches, réglages et compétences suivent en cascade.
    session.delete(account)
    return redirect("/admin/users")


@app.post("/admin/settings/apply-to-all")
def admin_apply_settings(
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Applique les réglages d'affichage de l'administrateur à tous.

    Chacun reste libre de les modifier ensuite : ce n'est pas un
    verrouillage, seulement un point de départ commun.
    """
    from .models import Setting

    reference = settings_svc.load(session, admin.id)
    lecteurs = [u for u in auth_svc.list_users(session) if not u.is_admin]
    for account in lecteurs:
        for option in settings_svc.OPTIONS:
            valeur = reference[option.key]
            stored = "1" if valeur is True else ("0" if valeur is False else str(valeur))
            row = session.get(Setting, (account.id, option.key))
            if row is None:
                session.add(Setting(user_id=account.id, key=option.key, value=stored))
            else:
                row.value = stored
    return redirect("/admin")


@app.post("/api/tokens/{token_id}/resolve-by-name")
def resolve_by_name(
    token_id: int,
    lemma: str = Form(...),
    upos: str = Form("X"),
    scope: str = Form("global"),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Arbitrage depuis la file : le lemme est désigné par son nom.

    La file d'arbitrage affiche les candidats bruts du moteur, qui ne sont
    pas tous encore présents dans la table des lemmes.
    """
    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404)
    target = get_or_create_lemma(session, lemma, upos)
    session.flush()
    try:
        result = disamb_svc.resolve(
            session, token=tok, lemma_id=target.id, scope=scope
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "lemma_id": result.lemma_id,
        "updated_tokens": len(result.updated_token_ids),
        "updated_texts": result.updated_texts,
    }


# --------------------------------------------------------------------------
# Bascule entre mode gestion et mode lecture
# --------------------------------------------------------------------------
@app.post("/mode/{mode}")
def switch_mode(
    request: Request,
    mode: str,
    next_url: str = Form("/"),
    user: User = Depends(require_admin),
):
    """Passe l'administrateur d'un mode à l'autre.

    En mode lecture, il annote pour lui-même : ses statuts et ses fiches
    lui appartiennent, comme à n'importe quel lecteur.
    """
    if mode not in (MODE_ADMIN, MODE_USER):
        raise HTTPException(400, "mode inconnu")
    request.session[MODE_KEY] = mode
    # Une destination doit rester interne : jamais de renvoi hors du site.
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"
    if mode == MODE_ADMIN and next_url in ("/", "/words", "/cards", "/review", "/stats"):
        next_url = "/admin"
    return redirect(next_url)


# --------------------------------------------------------------------------
# Arbitrage dans le texte
# --------------------------------------------------------------------------
@app.get("/admin/texts/{text_id}", response_class=HTMLResponse)
def admin_read_text(
    request: Request,
    text_id: int,
    page: int = 0,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Le texte tel que l'administrateur le travaille.

    Les mots à plusieurs lemmes possibles se parcourent aux flèches ; le
    lemme retenu se choisit de même, et s'enregistre aussitôt. L'arbitrage
    porte sur l'occurrence, non sur la forme : « quod » peut être relatif
    ici et conjonction trois lignes plus loin.
    """
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)

    tokens = session.scalars(
        select(TextToken)
        .where(TextToken.text_id == text_id, TextToken.page_idx == page)
        .order_by(TextToken.idx)
    ).all()
    lemmas = {
        l.id: l
        for l in session.scalars(
            select(Lemma).where(
                Lemma.id.in_({t.chosen_lemma_id for t in tokens if t.chosen_lemma_id})
            )
        ).all()
    }

    view, ambigus = [], 0
    for tok in tokens:
        lemma = lemmas.get(tok.chosen_lemma_id or -1)
        # Tous les mots offrant plusieurs lemmes distincts, arbitrés ou non :
        # on doit pouvoir revenir sur un choix.
        distincts = {
            str(c.get("lemma", "")).lower().rstrip("0123456789#")
            for c in (tok.candidates or [])
        }
        multiple = tok.is_word and len(distincts) > 1
        if multiple:
            ambigus += 1
        view.append(
            {
                "t": tok,
                "lemma": lemma,
                "multiple": multiple,
                "flagged": multiple
                and not tok.is_resolved
                and tok.ambiguity_margin < AMBIGUITY_MARGIN,
                "ignored": bool(lemma and lemma.is_ignored),
            }
        )

    return templates.TemplateResponse(
        request,
        "admin_reader.html",
        {
            "doc": doc,
            "tokens": view,
            "page": page,
            "ambiguous_count": ambigus,
            "resolved_count": sum(
                1 for v in view if v["multiple"] and v["t"].is_resolved
            ),
        },
    )


@app.get("/admin/panel/token/{token_id}", response_class=HTMLResponse)
def admin_token_panel(
    request: Request,
    token_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404)
    lemma = session.get(Lemma, tok.chosen_lemma_id) if tok.chosen_lemma_id else None

    # Candidats tels que le moteur les a produits, dédoublonnés par lemme.
    vus, candidats = set(), []
    for cand in tok.candidates or []:
        nom = str(cand.get("lemma", "")).lower().rstrip("0123456789#")
        if not nom or nom in vus:
            continue
        vus.add(nom)
        candidats.append(
            {
                "lemma": nom,
                "upos": cand.get("upos", "X"),
                "feats": cand.get("feats") or {},
                "score": cand.get("score", 0),
                "chosen": bool(lemma and lemma.lemma == nom),
                # La glose d'arbitrage vient de la base de reference, non
                # d'un dictionnaire d'appoint : c'est elle qui definit ce
                # que sont les lemmes entre lesquels on tranche.
                "gloss": gaffiot_svc.get_gaffiot().gloss(nom, cand.get("upos")),
            }
        )

    parent = session.get(TextToken, tok.parent_token_id) if tok.parent_token_id else None
    enfant = session.scalars(
        select(TextToken).where(TextToken.parent_token_id == tok.id)
    ).first()

    return templates.TemplateResponse(
        request,
        "_admin_panel.html",
        {
            "token": tok,
            "lemma": lemma,
            "candidates": candidats,
            "sentence": cards_svc.sentence_of(session, tok),
            "entries": dict_svc.lookup_all(lemma.lemma, tok.surface) if lemma else [],
            "parent": parent,
            "enclitic_child": enfant,
            "splittable": _splittable(tok),
        },
    )


def _splittable(tok: TextToken) -> str | None:
    """L'enclitique que l'on pourrait détacher de ce mot, s'il y en a une."""
    from .nlp.normalize import split_enclitic

    if not tok.is_word or tok.parent_token_id:
        return None
    decoupe = split_enclitic(tok.surface)
    return decoupe[1] if decoupe else None


@app.post("/api/admin/tokens/{token_id}/lemma")
def admin_set_token_lemma(
    token_id: int,
    lemma: str = Form(...),
    upos: str = Form("X"),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Fixe le lemme de CETTE occurrence, sans toucher aux autres."""
    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404)

    target = get_or_create_lemma(session, lemma, upos)
    session.flush()
    tok.chosen_lemma_id = target.id
    tok.is_resolved = True
    tok.is_guessed = False
    for cand in tok.candidates or []:
        nom = str(cand.get("lemma", "")).lower().rstrip("0123456789#")
        if nom == target.lemma and cand.get("feats"):
            tok.feats = cand["feats"]
            break
    return {
        "token_id": tok.id,
        "lemma_id": target.id,
        "lemma": target.lemma,
        "resolved": True,
    }


@app.post("/api/admin/lemmas/{lemma_id}/ignore")
def admin_toggle_ignore(
    lemma_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Marque un lemme comme nom propre ou mot à écarter, pour tous."""
    lemma = session.get(Lemma, lemma_id)
    if lemma is None:
        raise HTTPException(404)
    lemma.is_ignored = not lemma.is_ignored
    return {"lemma_id": lemma_id, "is_ignored": lemma.is_ignored}



def _shift_indices(session: Session, text_id: int, from_idx: int, delta: int) -> None:
    """Decale les index des tokens suivants, sans violer l'unicite.

    Un simple `idx += 1` echoue : la contrainte (text_id, idx) est
    verifiee ligne par ligne, et l'ordre d'ecriture n'est pas garanti. On
    passe donc par des index negatifs, qui ne peuvent entrer en collision
    avec les positifs, avant de revenir aux valeurs definitives.
    """
    from sqlalchemy import update

    session.execute(
        update(TextToken)
        .where(TextToken.text_id == text_id, TextToken.idx >= from_idx)
        .values(idx=-(TextToken.idx + delta) - 1)
    )
    session.flush()
    session.execute(
        update(TextToken)
        .where(TextToken.text_id == text_id, TextToken.idx < 0)
        .values(idx=-TextToken.idx - 1)
    )
    session.flush()
    session.expire_all()


@app.post("/api/admin/tokens/{token_id}/split")
def admin_split_token(
    token_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Détache l'enclitique d'un mot que le moteur n'a pas segmenté.

    Le nouveau token s'insère juste après ; les suivants sont décalés d'un
    rang. Les positions de caractères sont recalculées pour que le texte
    rendu reste identique à la source.
    """
    from .nlp.normalize import form_key, split_enclitic
    from .nlp.enclitics import ENCLITIC_LEMMAS

    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404)
    decoupe = split_enclitic(tok.surface)
    if decoupe is None:
        raise HTTPException(400, "aucune enclitique détachable dans ce mot")

    radical, enclitique = decoupe
    cle = form_key(enclitique)
    nom, upos = ENCLITIC_LEMMAS.get(cle, (cle, "PART"))

    # On décale les tokens suivants pour faire une place.
    text_id, position = tok.text_id, tok.idx
    session.flush()
    _shift_indices(session, text_id, position + 1, 1)
    tok = session.get(TextToken, token_id)

    coupe = tok.char_start + len(radical)
    trailing_origine = tok.trailing
    tok.surface = radical
    tok.char_end = coupe
    tok.trailing = ""
    tok.form_id = get_or_create_form(session, form_key(radical)).id
    session.flush()

    enfant = TextToken(
        text_id=tok.text_id,
        idx=tok.idx + 1,
        sentence_idx=tok.sentence_idx,
        page_idx=tok.page_idx,
        surface=enclitique,
        char_start=coupe,
        char_end=coupe + len(enclitique),
        trailing=trailing_origine,
        is_word=True,
        is_enclitic=True,
        parent_token_id=tok.id,
        form_id=get_or_create_form(session, cle).id,
        chosen_lemma_id=get_or_create_lemma(session, nom, upos).id,
        candidates=[{"lemma": nom, "upos": upos, "feats": {}, "score": 0.95}],
        ambiguity_margin=1.0,
        is_resolved=True,
    )
    session.add(enfant)
    session.flush()
    return {"parent": tok.id, "enclitic": enfant.id, "surface": tok.surface}


@app.post("/api/admin/tokens/{token_id}/merge")
def admin_merge_token(
    token_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Recolle une enclitique détachée à tort sur le mot qui la précède."""
    from .nlp.normalize import form_key

    enfant = session.get(TextToken, token_id)
    if enfant is None:
        raise HTTPException(404)
    parent = (
        session.get(TextToken, enfant.parent_token_id)
        if enfant.parent_token_id
        else None
    )
    if parent is None:
        raise HTTPException(400, "ce mot n'est pas une enclitique détachée")

    parent.surface += enfant.surface
    parent.char_end = enfant.char_end
    parent.trailing = enfant.trailing
    parent.form_id = get_or_create_form(session, form_key(parent.surface)).id
    parent.is_resolved = False
    text_id, position = parent.text_id, parent.idx
    session.delete(enfant)
    session.flush()
    _shift_indices(session, text_id, position + 2, -1)
    parent = session.get(TextToken, parent.id)
    return {"token_id": parent.id, "surface": parent.surface}


# --------------------------------------------------------------------------
# Import d'une vidéo sous-titrée
# --------------------------------------------------------------------------
@app.post("/texts/youtube")
def add_video(
    request: Request,
    url: str = Form(...),
    title: str = Form(""),
    language_stage: str = Form("classical"),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Importe les sous-titres latins d'une vidéo.

    La vidéo n'est pas téléchargée : elle reste sur YouTube, incrustée
    dans la page. Seuls les sous-titres deviennent un texte de la
    bibliothèque, lemmatisé comme les autres.
    """
    from .services import youtube as yt

    try:
        video = yt.import_video(url)
    except yt.TranscriptError as exc:
        return templates.TemplateResponse(
            request,
            "admin.html",
            {
                **_admin_context(session, admin),
                "video_error": str(exc),
            },
            status_code=400,
        )

    doc = create_text(
        session,
        title=title.strip() or f"Vidéo {video.video_id}",
        content=video.content,
        source_note=f"https://www.youtube.com/watch?v={video.video_id}",
        language_stage=language_stage,
        created_by=admin.id,
    )
    doc.source_kind = "youtube"
    doc.video_id = video.video_id
    doc.cues = [c.as_dict() for c in video.cues]
    session.commit()
    process_in_background(doc.id)
    return redirect("/admin")


# --------------------------------------------------------------------------
# Enregistrement associé à un texte
# --------------------------------------------------------------------------
@app.get("/audio/{name}")
def serve_audio(name: str):
    from .services import audio as audio_svc

    chemin = audio_svc.resolve(name)
    if chemin is None:
        raise HTTPException(404)
    # `Accept-Ranges` permet au navigateur de sauter dans le fichier sans
    # le télécharger entièrement : indispensable pour l'alignement.
    return FileResponse(
        chemin,
        media_type=audio_svc.media_type(name),
        headers={"Accept-Ranges": "bytes", "Cache-Control": "public, max-age=31536000"},
    )


def _segments_of(doc: TextDoc) -> list:
    from .services.segments import Segment

    return [Segment.from_dict(d) for d in (doc.cues or [])]


def _store_segments(doc: TextDoc, segments: list) -> None:
    from .services import segments as seg_svc

    doc.cues = [s.as_dict() for s in seg_svc.normalize(segments)]


@app.post("/api/admin/texts/{text_id}/audio")
async def upload_audio(
    text_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Attache un enregistrement au texte et prépare le découpage.

    Le texte reste lisible tant que l'alignement n'est pas fait : le
    lecteur affiche alors un simple lecteur audio, sans surbrillance.
    """
    from .services import audio as audio_svc
    from .services import segments as seg_svc

    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    try:
        nom = audio_svc.save_bytes(await file.read())
    except audio_svc.AudioError as exc:
        raise HTTPException(400, str(exc)) from exc

    precedent = doc.audio_path
    doc.audio_path = nom
    # On ne redécoupe pas un texte déjà segmenté : l'alignement en cours
    # serait perdu.
    if not doc.cues:
        _store_segments(doc, seg_svc.split_text(doc.raw_content))
    session.flush()

    if precedent and precedent != nom:
        encore = session.scalars(
            select(TextDoc).where(TextDoc.audio_path == precedent, TextDoc.id != text_id)
        ).first()
        audio_svc.delete_if_orphan(precedent, encore is not None)

    return {"text_id": text_id, "audio": nom, "segments": len(doc.cues)}


@app.post("/api/admin/texts/{text_id}/audio/delete")
def delete_audio(
    text_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    from .services import audio as audio_svc

    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    precedent = doc.audio_path
    doc.audio_path = None
    session.flush()
    if precedent:
        encore = session.scalars(
            select(TextDoc).where(TextDoc.audio_path == precedent)
        ).first()
        audio_svc.delete_if_orphan(precedent, encore is not None)
    return {"text_id": text_id, "audio": None}


@app.get("/admin/texts/{text_id}/align", response_class=HTMLResponse)
def align_page(
    request: Request,
    text_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Éditeur d'alignement : frappe au rythme puis retouche."""
    from .services import segments as seg_svc

    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)

    segments = _segments_of(doc)
    if not segments and doc.raw_content:
        segments = seg_svc.split_text(doc.raw_content)
        _store_segments(doc, segments)
        segments = _segments_of(doc)

    vue = [
        {
            "index": i,
            "text": doc.raw_content[s.char_start : s.char_end].strip(),
            "char_start": s.char_start,
            "char_end": s.char_end,
            "start": s.start,
            "end": s.end,
        }
        for i, s in enumerate(segments)
    ]
    return templates.TemplateResponse(
        request,
        "admin_align.html",
        {
            "doc": doc,
            "segments": vue,
            "coverage": round(seg_svc.coverage(segments) * 100),
        },
    )


@app.post("/api/admin/texts/{text_id}/segments/taps")
def apply_taps(
    text_id: int,
    taps: str = Form(...),
    duration: float = Form(0.0),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Applique une séance de frappe au rythme.

    Chaque frappe marque le début d'un segment ; la fin est le début du
    suivant. Une séance interrompue laisse le reste non aligné plutôt
    que de tout perdre.
    """
    from .services import segments as seg_svc

    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    try:
        instants = [float(t) for t in taps.split(",") if t.strip()]
    except ValueError as exc:
        raise HTTPException(400, "relevés de temps illisibles") from exc

    segments = seg_svc.apply_taps(
        _segments_of(doc), instants, duration or None
    )
    _store_segments(doc, segments)
    return {
        "aligned": sum(1 for s in segments if s.aligned),
        "total": len(segments),
    }


@app.post("/api/admin/texts/{text_id}/segments/{index}")
def edit_segment(
    text_id: int,
    index: int,
    start: float | None = Form(None),
    end: float | None = Form(None),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Retouche manuelle des bornes d'un segment."""
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    segments = _segments_of(doc)
    if not 0 <= index < len(segments):
        raise HTTPException(404, "segment inconnu")
    if start is not None:
        segments[index].start = max(0.0, start)
    if end is not None:
        segments[index].end = max(0.0, end)
    _store_segments(doc, segments)
    return {"index": index, "start": segments[index].start, "end": segments[index].end}


@app.post("/api/admin/texts/{text_id}/segments/{index}/merge")
def merge_segment(
    text_id: int,
    index: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    from .services import segments as seg_svc

    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    try:
        segments = seg_svc.merge(_segments_of(doc), index)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _store_segments(doc, segments)
    return {"total": len(segments)}


@app.post("/api/admin/texts/{text_id}/segments/{index}/split")
def split_segment(
    text_id: int,
    index: int,
    position: int = Form(...),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    from .services import segments as seg_svc

    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    try:
        segments = seg_svc.split_at(
            _segments_of(doc), index, position, doc.raw_content
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    _store_segments(doc, segments)
    return {"total": len(segments)}


@app.post("/api/admin/texts/{text_id}/segments/reset")
def reset_segments(
    text_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Redécoupe le texte, en perdant l'alignement."""
    from .services import segments as seg_svc

    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    _store_segments(doc, seg_svc.split_text(doc.raw_content))
    return {"total": len(doc.cues)}


@app.post("/api/lemmas/{lemma_id}/note")
def update_note(
    lemma_id: int,
    note: str = Form(""),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Note personnelle, depuis le panneau de lecture."""
    if session.get(Lemma, lemma_id) is None:
        raise HTTPException(404)
    row = knowledge.set_status(session, user.id, lemma_id, note=note, manual=False)
    if not note.strip():
        row.note = None
    return {"lemma_id": lemma_id, "note": row.note}


@app.post("/api/admin/tokens/{token_id}/word")
def admin_toggle_word(
    token_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Fait entrer un token dans le vocabulaire, ou l'en écarte.

    Le découpeur écarte ce qui n'est pas fait de lettres : chiffres,
    ponctuation, symboles. C'est le bon choix par défaut, mais il rate les
    cas mixtes — abréviations pointées, mots à trait d'union, chiffres
    romains transcrits en arabe. L'administrateur tranche.
    """
    from .nlp.normalize import form_key

    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404)

    tok.is_word = not tok.is_word
    if tok.is_word:
        cle = form_key(tok.surface)
        tok.form_id = get_or_create_form(session, cle).id
        if not tok.chosen_lemma_id:
            # On propose le meilleur lemme atteste pour cette forme.
            from .services.lemma_reference import attested_readings, propose

            lectures = attested_readings(cle) or propose(cle)
            lemme, upos = (lectures[0][0], lectures[0][1]) if lectures else (cle, "X")
            cible = get_or_create_lemma(session, lemme, upos)
            session.flush()
            tok.chosen_lemma_id = cible.id
            tok.candidates = [
                {"lemma": l, "upos": u, "feats": {}, "score": s}
                for l, u, s in (lectures[:4] or [(cle, "X", 0.15)])
            ]
            tok.is_guessed = not lectures
    else:
        tok.chosen_lemma_id = None
        tok.form_id = None
        tok.candidates = []
    session.flush()
    return {
        "token_id": tok.id,
        "is_word": tok.is_word,
        "lemma": (
            session.get(Lemma, tok.chosen_lemma_id).lemma
            if tok.chosen_lemma_id
            else None
        ),
    }


@app.post("/admin/users/demo")
def admin_regenerate_demo(
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Recrée le compte de démonstration « moyen »."""
    auth_svc.regenerate_demo_user(session)
    return redirect("/admin/users")


# --------------------------------------------------------------------------
# Livres
# --------------------------------------------------------------------------
@app.get("/admin/books", response_class=HTMLResponse)
def admin_books(
    request: Request,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    livres = session.scalars(
        select(Book).order_by(Book.sort_order, Book.author, Book.title)
    ).all()
    orphelins = session.scalars(
        select(TextDoc).where(TextDoc.book_id.is_(None)).order_by(TextDoc.title)
    ).all()
    return templates.TemplateResponse(
        request, "admin_books.html", {"books": livres, "orphans": orphelins}
    )


@app.post("/admin/books")
def create_book(
    title: str = Form(...),
    subtitle: str = Form(""),
    author: str = Form(""),
    era: str = Form(""),
    translator: str = Form(""),
    description: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    livre = Book(
        title=title.strip(),
        subtitle=subtitle.strip() or None,
        author=author.strip() or None,
        era=era.strip() or None,
        translator=translator.strip() or None,
        description=description.strip() or None,
    )
    session.add(livre)
    session.flush()
    return redirect("/admin/books")


@app.post("/admin/books/{book_id}")
def update_book(
    book_id: int,
    title: str = Form(...),
    subtitle: str = Form(""),
    author: str = Form(""),
    era: str = Form(""),
    translator: str = Form(""),
    description: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    livre = session.get(Book, book_id)
    if livre is None:
        raise HTTPException(404)
    livre.title = title.strip()
    livre.subtitle = subtitle.strip() or None
    livre.author = author.strip() or None
    livre.era = era.strip() or None
    livre.translator = translator.strip() or None
    livre.description = description.strip() or None
    return redirect("/admin/books")


@app.post("/admin/books/{book_id}/delete")
def delete_book(
    book_id: int,
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Supprime le recueil. Les textes subsistent, simplement détachés."""
    livre = session.get(Book, book_id)
    if livre is None:
        raise HTTPException(404)
    for texte in livre.texts:
        texte.book_id = None
        texte.chapter_idx = 0
    session.delete(livre)
    return redirect("/admin/books")


@app.post("/api/admin/books/{book_id}/cover")
async def upload_cover(
    book_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    livre = session.get(Book, book_id)
    if livre is None:
        raise HTTPException(404)
    try:
        nom = image_svc.save_bytes(await file.read())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    livre.cover_path = nom
    return {"book_id": book_id, "cover": nom, "url": f"/media/{nom}"}


@app.post("/api/admin/texts/{text_id}/book")
def attach_to_book(
    text_id: int,
    book_id: str = Form(""),
    chapter_idx: int = Form(0),
    chapter_label: str = Form(""),
    session: Session = Depends(get_session),
    admin: User = Depends(require_admin),
):
    """Rattache un texte à un livre, comme chapitre numéroté."""
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)
    doc.book_id = int(book_id) if book_id.strip() else None
    doc.chapter_idx = chapter_idx
    doc.chapter_label = chapter_label.strip() or None
    return {
        "text_id": text_id,
        "book_id": doc.book_id,
        "chapter_idx": doc.chapter_idx,
    }


@app.post("/api/texts/{text_id}/pages/{page}/read")
def mark_page_read(
    text_id: int,
    page: int,
    mark_unseen: bool = Form(False),
    session: Session = Depends(get_session),
    user: User = Depends(require_user),
):
    """Signale une page comme lue, et marque éventuellement son vocabulaire.

    Les deux gestes sont distincts : on peut avoir tout compris sans
    vouloir enregistrer quoi que ce soit.
    """
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404)

    valides = 0
    if mark_unseen:
        tokens = session.scalars(
            select(TextToken).where(
                TextToken.text_id == text_id, TextToken.page_idx == page
            )
        ).all()
        valides = knowledge.validate_unseen(session, user.id, tokens, status=0)[
            "validated"
        ]

    if session.get(PageRead, (user.id, text_id, page)) is None:
        session.add(PageRead(user_id=user.id, text_id=text_id, page_idx=page))
    session.flush()

    lues = session.scalar(
        select(func.count())
        .select_from(PageRead)
        .where(PageRead.user_id == user.id, PageRead.text_id == text_id)
    ) or 0
    return {"validated": valides, "pages_read": lues, "pages": doc.page_count}
