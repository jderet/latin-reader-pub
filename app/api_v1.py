"""API v1 — le contrat des clients natifs.

Le site rend ses pages sur le serveur ; cette API expose les mêmes
opérations en JSON, avec un schéma de sortie déclaré (visible dans
/docs) et une authentification par jeton porteur. Trois principes :

- rien ici n'est nécessaire au site : les pages continuent de vivre
  sur leurs routes historiques ;
- toute la logique reste dans les services (knowledge, cards, …) —
  l'API est une couche de présentation, pas un deuxième métier ;
- les réponses sont des structures, jamais du HTML : chaque client les
  met en forme pour sa plateforme.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import secrets

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_session
from .deps import current_user
from .models import ApiToken, Card, Lemma, LemmaStatus, TextDoc, TextToken, User
from .nlp.normalize import lemma_key
from .services import auth as auth_svc
from .services import cards as cards_svc
from .services.importer import AMBIGUITY_MARGIN
from .services import dictionary as dict_svc
from .services import knowledge
from .services import settings as settings_svc

router = APIRouter(prefix="/api/v1", tags=["api-v1"])


# --------------------------------------------------------------------------
# Authentification
# --------------------------------------------------------------------------
def require_reader(
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user),
) -> User:
    """Un lecteur authentifié — par jeton ou par cookie.

    Contrairement aux pages, on ne redirige jamais : un client d'API
    attend un code, pas une page de connexion. Le compte administrateur
    est refusé : il n'a ni statuts ni fiches (cf. app/deps.py).
    """
    if user is None:
        raise HTTPException(401, "authentification requise")
    if user.is_admin:
        raise HTTPException(403, "compte gestionnaire : aucune donnée de lecture")
    return user


class TokenRequest(BaseModel):
    username: str
    password: str
    label: str | None = Field(None, description="Nom de l'appareil, libre")


class TokenOut(BaseModel):
    token: str = Field(description="À conserver : il n'est montré qu'une fois")
    user_id: int
    username: str


@router.post("/auth/token", response_model=TokenOut)
def create_token(payload: TokenRequest, session: Session = Depends(get_session)):
    user = auth_svc.authenticate(session, payload.username, payload.password)
    if user is None:
        raise HTTPException(401, "identifiants invalides")
    raw = secrets.token_urlsafe(32)
    session.add(
        ApiToken(
            user_id=user.id,
            token_hash=hashlib.sha256(raw.encode()).hexdigest(),
            label=payload.label,
        )
    )
    return TokenOut(token=raw, user_id=user.id, username=user.username)


class MeOut(BaseModel):
    user_id: int
    username: str
    display_name: str | None


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(require_reader)):
    return MeOut(user_id=user.id, username=user.username,
                 display_name=user.display_name)


# --------------------------------------------------------------------------
# Bibliothèque
# --------------------------------------------------------------------------
class TextOut(BaseModel):
    id: int
    title: str
    author: str | None
    word_count: int
    page_count: int
    status: str
    engine: str | None


@router.get("/texts", response_model=list[TextOut])
def list_texts(session: Session = Depends(get_session),
               user: User = Depends(require_reader)):
    docs = session.scalars(select(TextDoc).order_by(TextDoc.id)).all()
    return [
        TextOut(id=d.id, title=d.title, author=d.author,
                word_count=d.word_count or 0, page_count=d.page_count or 1,
                status=d.status, engine=d.engine)
        for d in docs
    ]


class TokenItem(BaseModel):
    id: int
    surface: str
    trailing: str
    is_word: bool
    lemma_id: int | None
    ambiguous: bool
    sentence: int


class PageOut(BaseModel):
    text_id: int
    page: int
    page_count: int
    tokens: list[TokenItem]
    statuses: dict[int, int] = Field(
        description="lemma_id -> statut 0-4 ; absent = jamais rencontré"
    )
    ignored: list[int] = Field(description="lemma_id ignorés par le lecteur")


@router.get("/texts/{text_id}/pages/{page}", response_model=PageOut)
def text_page(text_id: int, page: int,
              session: Session = Depends(get_session),
              user: User = Depends(require_reader)):
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise HTTPException(404, "texte inconnu")
    tokens = session.scalars(
        select(TextToken)
        .where(TextToken.text_id == text_id, TextToken.page_idx == page)
        .order_by(TextToken.idx)
    ).all()
    statuses = knowledge.statuses_for_tokens(session, user.id, tokens)
    return PageOut(
        text_id=text_id,
        page=page,
        page_count=doc.page_count or 1,
        tokens=[
            TokenItem(
                id=t.id, surface=t.surface, trailing=t.trailing or "",
                is_word=t.is_word, lemma_id=t.chosen_lemma_id,
                ambiguous=bool(
                    t.is_word and not t.is_resolved
                    and t.ambiguity_margin < AMBIGUITY_MARGIN
                ),
                sentence=t.sentence_idx or 0,
            )
            for t in tokens
        ],
        statuses={
            lid: st.status for lid, st in statuses.items() if not st.is_ignored
        },
        ignored=[lid for lid, st in statuses.items() if st.is_ignored],
    )


# --------------------------------------------------------------------------
# Panneau de mot — la structure, pas le HTML
# --------------------------------------------------------------------------
class CandidateOut(BaseModel):
    lemma_id: int
    display: str
    upos: str | None
    score: float
    feats: dict[str, str]
    chosen: bool


class DictEntryOut(BaseModel):
    source: str
    text: str


class WordPanelOut(BaseModel):
    token_id: int
    surface: str
    lemma_id: int | None
    display: str | None = Field(None, description="vedette du dictionnaire")
    upos: str | None
    status: int | None = Field(None, description="0-4, None = jamais rencontré")
    is_ignored: bool
    is_locked: bool
    gloss: str | None = Field(None, description="glose personnelle du lecteur")
    shared_gloss: str | None = Field(None, description="glose de l'administrateur")
    note: str | None
    suggested_gloss: str
    candidates: list[CandidateOut]
    entries: list[DictEntryOut]
    sentence: str


@router.get("/tokens/{token_id}", response_model=WordPanelOut)
def word_panel(token_id: int, session: Session = Depends(get_session),
               user: User = Depends(require_reader)):
    tok = session.get(TextToken, token_id)
    if tok is None:
        raise HTTPException(404, "mot inconnu")

    lemma = session.get(Lemma, tok.chosen_lemma_id) if tok.chosen_lemma_id else None
    status = (
        session.get(LemmaStatus, (user.id, tok.chosen_lemma_id))
        if tok.chosen_lemma_id else None
    )

    # Même règle que le panneau HTML : une ambiguïté tranchée par
    # l'administrateur ne montre plus ses candidats écartés.
    candidates: list[CandidateOut] = []
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
                CandidateOut(
                    lemma_id=obj.id, display=obj.display, upos=obj.upos,
                    score=cand.get("score", 0), feats=cand.get("feats") or {},
                    chosen=obj.id == tok.chosen_lemma_id,
                )
            )

    prefs = settings_svc.load(session, user.id)
    entries = dict_svc.lookup_all(lemma.lemma, tok.surface) if lemma else []
    suggested = ""
    if not (status and status.gloss):
        if lemma and lemma.shared_gloss:
            suggested = lemma.shared_gloss
        elif prefs["autofill_gloss"] and lemma:
            from .services import gaffiot as gaffiot_svc

            suggested = gaffiot_svc.get_gaffiot().gloss(lemma.lemma, lemma.upos)

    return WordPanelOut(
        token_id=tok.id,
        surface=tok.surface,
        lemma_id=lemma.id if lemma else None,
        display=lemma.display if lemma else None,
        upos=lemma.upos if lemma else None,
        status=status.status if status else None,
        is_ignored=bool(status and status.is_ignored),
        is_locked=bool(status and status.is_locked),
        gloss=status.gloss if status else None,
        shared_gloss=lemma.shared_gloss if lemma else None,
        note=status.note if status else None,
        suggested_gloss=suggested,
        candidates=candidates,
        entries=[
            DictEntryOut(source=e.get("source", ""), text=e.get("body", ""))
            for e in entries
        ],
        sentence=cards_svc.sentence_of(session, tok),
    )


# --------------------------------------------------------------------------
# Statuts — écriture et synchronisation différentielle
# --------------------------------------------------------------------------
class StatusIn(BaseModel):
    status: int | None = Field(None, ge=0, le=4)
    is_ignored: bool | None = None
    gloss: str | None = None
    note: str | None = None


class StatusOut(BaseModel):
    lemma_id: int
    status: int
    is_ignored: bool
    is_locked: bool
    gloss: str | None
    updated_at: dt.datetime


def _status_out(row: LemmaStatus) -> StatusOut:
    return StatusOut(
        lemma_id=row.lemma_id, status=row.status, is_ignored=row.is_ignored,
        is_locked=row.is_locked, gloss=row.gloss, updated_at=row.updated_at,
    )


@router.post("/lemmas/{lemma_id}/status", response_model=StatusOut)
def set_status(lemma_id: int, payload: StatusIn,
               session: Session = Depends(get_session),
               user: User = Depends(require_reader)):
    if session.get(Lemma, lemma_id) is None:
        raise HTTPException(404, "lemme inconnu")
    row = knowledge.set_status(
        session, user.id, lemma_id,
        status=payload.status, is_ignored=payload.is_ignored,
        gloss=payload.gloss, note=payload.note,
    )
    return _status_out(row)


@router.get("/statuses", response_model=list[StatusOut])
def list_statuses(
    since: dt.datetime | None = None,
    session: Session = Depends(get_session),
    user: User = Depends(require_reader),
):
    """Tous les statuts du lecteur, ou seulement ceux modifiés depuis
    `since` — c'est la moitié serveur d'une synchronisation :
    le client retient la date de sa dernière visite et ne redemande
    que le delta."""
    q = select(LemmaStatus).where(LemmaStatus.user_id == user.id)
    if since is not None:
        if since.tzinfo is None:
            since = since.replace(tzinfo=dt.timezone.utc)
        q = q.where(LemmaStatus.updated_at > since)
    rows = session.scalars(q.order_by(LemmaStatus.updated_at)).all()
    return [_status_out(r) for r in rows]


# --------------------------------------------------------------------------
# Révision
# --------------------------------------------------------------------------
class CardOut(BaseModel):
    id: int
    kind: str
    front: str
    back: str
    lemma_id: int | None
    is_new: bool
    due_at: dt.datetime | None


@router.get("/reviews/due", response_model=list[CardOut])
def due_cards(session: Session = Depends(get_session),
              user: User = Depends(require_reader)):
    queue = cards_svc.due_queue(session, user.id)
    return [
        CardOut(id=c.id, kind=c.kind, front=c.front, back=c.back,
                lemma_id=c.lemma_id, is_new=c.is_new, due_at=c.due_at)
        for c in queue
    ]


class ReviewIn(BaseModel):
    card_id: int
    button: str = Field(description="again | hard | good | easy")
    elapsed_ms: int | None = None


class ReviewOut(BaseModel):
    interval_days: int
    is_lapse: bool
    lemma_status: int | None = None


@router.post("/reviews", response_model=ReviewOut)
def post_review(payload: ReviewIn,
                session: Session = Depends(get_session),
                user: User = Depends(require_reader)):
    card = session.get(Card, payload.card_id)
    if card is None or card.user_id != user.id:
        raise HTTPException(404, "fiche inconnue")
    try:
        out = cards_svc.review(session, card, payload.button,
                               elapsed_ms=payload.elapsed_ms)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return ReviewOut(
        interval_days=out.get("interval_days", 0),
        is_lapse=bool(out.get("is_lapse")),
        lemma_status=out.get("lemma_status"),
    )


@router.get("/health")
def health():
    return {"status": "ok", "api": "v1"}
