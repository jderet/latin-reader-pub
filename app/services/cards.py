"""Creation des fiches, file de revision, notation."""

from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Card,
    CardContext,
    Lemma,
    LemmaStatus,
    ReviewLog,
    TextToken,
    utcnow,
)
from . import knowledge
from .srs import BUTTON_QUALITY, Sm2State, next_status, sm2

KINDS = ("la_fr", "fr_la", "cloze")
KIND_LABELS = {
    "la_fr": "latin vers francais",
    "fr_la": "francais vers latin",
    "cloze": "texte a trous",
}
MAX_CONTEXTS = 5
CLOZE_MASK = "……"

DAILY_NEW_LIMIT = 20
DAILY_REVIEW_LIMIT = 200


def sentence_of(session: Session, token: TextToken) -> str:
    rows = session.scalars(
        select(TextToken)
        .where(
            TextToken.text_id == token.text_id,
            TextToken.sentence_idx == token.sentence_idx,
        )
        .order_by(TextToken.idx)
    ).all()
    return "".join(t.surface + (t.trailing or "") for t in rows).strip()


def cloze_of(session: Session, token: TextToken) -> str:
    rows = session.scalars(
        select(TextToken)
        .where(
            TextToken.text_id == token.text_id,
            TextToken.sentence_idx == token.sentence_idx,
        )
        .order_by(TextToken.idx)
    ).all()
    parts = []
    for t in rows:
        parts.append((CLOZE_MASK if t.id == token.id else t.surface) + (t.trailing or ""))
    return "".join(parts).strip()


def create_cards(
    session: Session,
    user_id: int,
    *,
    lemma_id: int,
    kinds: list[str],
    token: TextToken | None = None,
    gloss: str | None = None,
) -> list[Card]:
    lemma = session.get(Lemma, lemma_id)
    if lemma is None:
        raise ValueError("lemme inconnu")

    status = session.get(LemmaStatus, (user_id, lemma_id))
    # Traduction personnelle, sinon celle rédigée par l'administrateur.
    gloss = (
        gloss
        or (status.gloss if status and status.gloss else None)
        or lemma.shared_gloss
        or ""
    ).strip()
    if not gloss and "cloze" not in kinds:
        raise ValueError("une glose est requise pour les fiches de vocabulaire")

    created: list[Card] = []
    for kind in kinds:
        if kind not in KINDS:
            raise ValueError(f"type de fiche inconnu : {kind}")
        card = session.scalars(
            select(Card).where(
                Card.user_id == user_id,
                Card.lemma_id == lemma_id,
                Card.kind == kind,
            )
        ).first()

        if kind == "la_fr":
            front, back = lemma.display, gloss
        elif kind == "fr_la":
            front, back = gloss, lemma.display
        else:
            if token is None:
                raise ValueError("une fiche a trous exige un token de contexte")
            front = cloze_of(session, token)
            back = token.surface

        if card is None:
            card = Card(user_id=user_id, lemma_id=lemma_id, kind=kind, front=front, back=back)
            if kind == "cloze" and token is not None:
                card.extra = {"feats": token.feats, "token_id": token.id}
            session.add(card)
            session.flush()
            created.append(card)
        else:
            # Une fiche editee a la main n'est jamais reecrite : sinon
            # modifier la glose du lemme effacerait le travail de l'utilisateur.
            if not (card.extra or {}).get("is_custom"):
                card.front, card.back = front, back
            card.is_suspended = False

        if token is not None:
            attach_context(session, card, token)

    return created


def attach_context(session: Session, card: Card, token: TextToken) -> None:
    exists = session.scalars(
        select(CardContext).where(
            CardContext.card_id == card.id, CardContext.token_id == token.id
        )
    ).first()
    if exists:
        return
    session.add(
        CardContext(
            card_id=card.id,
            token_id=token.id,
            sentence=sentence_of(session, token),
            surface=token.surface,
        )
    )
    session.flush()
    contexts = session.scalars(
        select(CardContext)
        .where(CardContext.card_id == card.id)
        .order_by(CardContext.added_at.desc())
    ).all()
    for old in contexts[MAX_CONTEXTS:]:
        session.delete(old)


def due_queue(
    session: Session, user_id: int, *,
    now: dt.datetime | None = None, limit: int = 50,
) -> list[Card]:
    now = now or utcnow()
    rows = session.scalars(
        select(Card)
        .where(
            Card.user_id == user_id,
            Card.is_suspended.is_(False),
            Card.due_at <= now,
        )
        .order_by(Card.due_at)
    ).all()
    new_cards = [c for c in rows if c.is_new][:DAILY_NEW_LIMIT]
    reviews = [c for c in rows if not c.is_new][:DAILY_REVIEW_LIMIT]
    queue = reviews + new_cards
    queue.sort(key=lambda c: (not c.is_new, c.due_at))
    return queue[:limit]


def queue_stats(session: Session, user_id: int, now: dt.datetime | None = None) -> dict:
    now = now or utcnow()
    rows = session.scalars(
        select(Card).where(Card.user_id == user_id, Card.is_suspended.is_(False))
    ).all()
    due = [c for c in rows if c.due_at <= now]
    return {
        "total": len(rows),
        "due": len(due),
        "new": len([c for c in due if c.is_new]),
        "reviews": len([c for c in due if not c.is_new]),
    }


def review(
    session: Session, card: Card, button: str, *, elapsed_ms: int | None = None,
    now: dt.datetime | None = None,
) -> dict:
    """Enregistre une revision, met a jour SM-2 et, le cas echeant, le statut."""
    if button not in BUTTON_QUALITY:
        raise ValueError("bouton inconnu")
    quality = BUTTON_QUALITY[button]
    now = now or utcnow()

    before = Sm2State(card.ease_factor, card.interval_days, card.repetitions, card.lapses)
    result = sm2(before, quality, now=now)

    session.add(
        ReviewLog(
            card_id=card.id,
            reviewed_at=now,
            quality=quality,
            elapsed_ms=elapsed_ms,
            prev_interval=before.interval_days,
            new_interval=result.interval_days,
            prev_ef=before.ease_factor,
            new_ef=result.ease_factor,
        )
    )
    card.ease_factor = result.ease_factor
    card.interval_days = result.interval_days
    card.repetitions = result.repetitions
    card.lapses = result.lapses
    card.due_at = result.due_at

    if card.kind == "cloze":
        feats = (card.extra or {}).get("feats") or {}
        knowledge.record_morph_attempt(
            session, card.user_id, feats, success=quality >= 3
        )

    status_row = session.get(LemmaStatus, (card.user_id, card.lemma_id))
    status_changed = None
    if status_row is not None:
        siblings = session.scalars(
            select(Card).where(
                Card.user_id == card.user_id,
                Card.lemma_id == card.lemma_id,
                Card.is_suspended.is_(False),
            )
        ).all()
        new_status = next_status(
            status_row.status,
            quality=quality,
            repetitions_per_card=[c.repetitions for c in siblings],
            is_locked=status_row.is_locked,
        )
        if new_status is not None:
            status_row.status = new_status
            status_row.updated_at = now
            status_changed = new_status

    return {
        "card_id": card.id,
        "interval_days": result.interval_days,
        "due_at": result.due_at.isoformat(),
        "is_lapse": result.is_lapse,
        "lemma_status": status_changed,
    }


def update_card(
    session: Session,
    card: Card,
    *,
    front: str | None = None,
    back: str | None = None,
    suspended: bool | None = None,
    reset_schedule: bool = False,
) -> Card:
    """Edition libre du recto et du verso.

    Le contenu devient alors independant de la glose du lemme : modifier
    la traduction dans l'onglet « Mots » ne recrasera plus une fiche
    editee a la main (cf. `is_custom`).
    """
    if front is not None:
        if not front.strip():
            raise ValueError("le recto ne peut pas etre vide")
        card.front = front.strip()
        card.extra = {**(card.extra or {}), "is_custom": True}
    if back is not None:
        if not back.strip():
            raise ValueError("le verso ne peut pas etre vide")
        card.back = back.strip()
        card.extra = {**(card.extra or {}), "is_custom": True}
    if suspended is not None:
        card.is_suspended = suspended
    if reset_schedule:
        card.ease_factor = 2.5
        card.interval_days = 0
        card.repetitions = 0
        card.lapses = 0
        card.due_at = utcnow()
    return card
