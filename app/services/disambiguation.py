"""Arbitrage de l'ambiguite lemmatique et propagation.

Repond directement au defaut de Learning with Texts : une decision prise
une fois vaut pour toutes les occurrences de la meme forme, et le statut
qui en decoule vaut pour toutes les formes du meme lemme.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import DisambiguationOverride, Lemma, TextToken, utcnow
from .importer import link_form_lemma


@dataclass(slots=True)
class ResolutionResult:
    lemma_id: int
    scope: str
    updated_token_ids: list[int]
    updated_texts: int


def resolve(
    session: Session,
    *,
    token: TextToken,
    lemma_id: int,
    scope: str = "global",
    feats: dict | None = None,
) -> ResolutionResult:
    """Fixe le lemme d'un token et propage.

    scope = "global" : toutes les occurrences de la forme, tous textes.
    scope = "text"   : seulement dans le texte courant.
    """
    if scope not in {"global", "text"}:
        raise ValueError("scope doit valoir 'global' ou 'text'")
    if token.form_id is None:
        raise ValueError("token sans forme indexee")

    lemma = session.get(Lemma, lemma_id)
    if lemma is None:
        raise ValueError("lemme inconnu")

    feats = feats if feats is not None else _feats_from_candidates(token, lemma)

    stmt = select(DisambiguationOverride).where(
        DisambiguationOverride.form_id == token.form_id,
        DisambiguationOverride.scope == scope,
        DisambiguationOverride.text_id == (token.text_id if scope == "text" else None),
    )
    override = session.scalars(stmt).first()
    if override is None:
        override = DisambiguationOverride(
            form_id=token.form_id,
            lemma_id=lemma_id,
            scope=scope,
            text_id=token.text_id if scope == "text" else None,
            feats=feats,
        )
        session.add(override)
    else:
        override.lemma_id = lemma_id
        override.feats = feats
        override.created_at = utcnow()

    targets = select(TextToken).where(TextToken.form_id == token.form_id)
    if scope == "text":
        targets = targets.where(TextToken.text_id == token.text_id)

    updated: list[int] = []
    texts: set[int] = set()
    for other in session.scalars(targets).all():
        # Un arbitrage de portee texte ne doit pas ecraser un arbitrage
        # deja pris explicitement sur ce texte par ailleurs.
        other.chosen_lemma_id = lemma_id
        if feats:
            other.feats = feats
        other.is_resolved = True
        other.is_guessed = False
        updated.append(other.id)
        texts.add(other.text_id)

    link_form_lemma_safe(session, token.form_id, lemma, feats)

    return ResolutionResult(lemma_id, scope, updated, len(texts))


def link_form_lemma_safe(session: Session, form_id: int, lemma: Lemma, feats: dict) -> None:
    from ..models import Form

    form = session.get(Form, form_id)
    if form is not None:
        link_form_lemma(session, form, lemma, feats)


def _feats_from_candidates(token: TextToken, lemma: Lemma) -> dict:
    for cand in token.candidates or []:
        if str(cand.get("lemma", "")).lower().rstrip("0123456789#") == lemma.lemma:
            return cand.get("feats") or {}
    return {}


def pending_count(session: Session, text_id: int, margin: float) -> int:
    """Nombre de tokens signales comme ambigus et non encore arbitres."""
    stmt = select(TextToken).where(
        TextToken.text_id == text_id,
        TextToken.is_word.is_(True),
        TextToken.is_resolved.is_(False),
        TextToken.ambiguity_margin < margin,
    )
    return len(session.scalars(stmt).all())
