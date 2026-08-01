"""Import d'un texte : lemmatisation, persistance, application des overrides."""

from __future__ import annotations

import logging
import re
import threading

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db import session_scope
from ..models import (
    DisambiguationOverride,
    Form,
    FormLemma,
    Lemma,
    TextDoc,
    TextToken,
    utcnow,
)
from ..nlp.base import AnalysisResult, TokenAnalysis, _bare
from ..nlp.registry import get_lemmatizer

log = logging.getLogger(__name__)

TOKENS_PER_PAGE = 500
AMBIGUITY_MARGIN = 0.15  # en deca, le token est signale comme a arbitrer
IGNORED_UPOS = {"PUNCT", "SYM", "X"}
PROPER_UPOS = {"PROPN"}
_ROMAN_RE = re.compile(r"^[IVXLCDM]+$")


# --------------------------------------------------------------------------
# Acces au lexique partage
# --------------------------------------------------------------------------
def get_or_create_lemma(
    session: Session, lemma: str, upos: str, homonym_idx: int = 0
) -> Lemma:
    bare = _bare(lemma)
    stmt = select(Lemma).where(
        Lemma.lemma == bare, Lemma.upos == upos, Lemma.homonym_idx == homonym_idx
    )
    obj = session.scalars(stmt).first()
    if obj is None:
        obj = Lemma(lemma=bare, upos=upos, homonym_idx=homonym_idx)
        session.add(obj)
        session.flush()
    return obj


def get_or_create_form(session: Session, key: str) -> Form:
    obj = session.scalars(select(Form).where(Form.form_key == key)).first()
    if obj is None:
        obj = Form(form_key=key)
        session.add(obj)
        session.flush()
    return obj


def link_form_lemma(session: Session, form: Form, lemma: Lemma, feats: dict) -> None:
    stmt = select(FormLemma).where(
        FormLemma.form_id == form.id, FormLemma.lemma_id == lemma.id
    )
    link = session.scalars(stmt).first()
    if link is None:
        link = FormLemma(form_id=form.id, lemma_id=lemma.id, feats=feats, freq=1)
        session.add(link)
    else:
        link.freq += 1
        if not link.feats and feats:
            link.feats = feats


# --------------------------------------------------------------------------
# Import
# --------------------------------------------------------------------------
def create_text(
    session: Session, *, title: str, content: str, author: str | None = None,
    source_note: str | None = None, language_stage: str = "classical",
) -> TextDoc:
    doc = TextDoc(
        title=title.strip() or "Sans titre",
        author=author,
        source_note=source_note,
        raw_content=content,
        language_stage=language_stage,
        status="pending",
    )
    session.add(doc)
    session.flush()
    return doc


def process_text(text_id: int, engine_name: str | None = None) -> None:
    """Lemmatise un texte et persiste ses tokens. Idempotent : relance
    possible, les tokens existants sont remplaces."""
    with session_scope() as session:
        doc = session.get(TextDoc, text_id)
        if doc is None:
            return
        doc.status = "processing"
        content = doc.raw_content
        session.commit()

    try:
        lemmatizer = get_lemmatizer(engine_name)
        result = lemmatizer.analyze(content)
    except Exception as exc:  # noqa: BLE001
        log.exception("echec de lemmatisation du texte %s", text_id)
        with session_scope() as session:
            doc = session.get(TextDoc, text_id)
            if doc:
                doc.status = "failed"
                doc.error_message = str(exc)[:500]
        return

    with session_scope() as session:
        doc = session.get(TextDoc, text_id)
        if doc is None:
            return
        for old in session.scalars(
            select(TextToken).where(TextToken.text_id == text_id)
        ).all():
            session.delete(old)
        session.flush()
        _persist(session, doc, result, content)


def _persist(
    session: Session, doc: TextDoc, result: AnalysisResult, content: str
) -> None:
    overrides = _load_overrides(session, doc.id)
    words = 0

    for analysis in result.tokens:
        page = analysis.index // TOKENS_PER_PAGE
        trailing = content[analysis.char_end : analysis.char_end + 1]
        if trailing and not trailing.isspace():
            trailing = ""

        token = TextToken(
            text_id=doc.id,
            idx=analysis.index,
            sentence_idx=analysis.sentence_index,
            page_idx=page,
            surface=analysis.surface,
            char_start=analysis.char_start,
            char_end=analysis.char_end,
            trailing=trailing or ("\n" if "\n" in content[analysis.char_end : analysis.char_end + 2] else " "),
            is_word=analysis.is_word,
            candidates=[c.as_dict() for c in analysis.candidates],
            ambiguity_margin=analysis.ambiguity_margin,
        )

        if not analysis.is_word or not analysis.candidates:
            session.add(token)
            continue

        words += 1
        form = get_or_create_form(session, analysis.form_key)
        token.form_id = form.id

        chosen = analysis.candidates[0]
        override = overrides.get(form.id)
        if override is not None:
            token.chosen_lemma_id = override.lemma_id
            token.feats = override.feats or chosen.feats
            token.is_resolved = True
            lemma = session.get(Lemma, override.lemma_id)
        else:
            lemma = get_or_create_lemma(session, chosen.lemma, chosen.upos)
            token.chosen_lemma_id = lemma.id
            token.feats = chosen.feats
            token.is_guessed = chosen.score < 0.2

        if lemma is not None:
            link_form_lemma(session, form, lemma, token.feats)

        # Les autres candidats sont crees aussi : l'arbitrage doit pouvoir
        # designer un lemme sans creation a la volee.
        for cand in analysis.candidates[1:]:
            get_or_create_lemma(session, cand.lemma, cand.upos)

        session.add(token)

    doc.status = "ready"
    doc.engine = result.engine
    doc.engine_version = result.engine_version
    doc.token_count = len(result.tokens)
    doc.word_count = words
    doc.page_count = max(1, (len(result.tokens) + TOKENS_PER_PAGE - 1) // TOKENS_PER_PAGE)
    doc.error_message = None


def _load_overrides(
    session: Session, text_id: int
) -> dict[int, DisambiguationOverride]:
    """Les overrides de portee « texte » priment sur ceux de portee globale."""
    rows = session.scalars(
        select(DisambiguationOverride).where(
            (DisambiguationOverride.scope == "global")
            | (DisambiguationOverride.text_id == text_id)
        )
    ).all()
    out: dict[int, DisambiguationOverride] = {}
    for row in sorted(rows, key=lambda r: r.scope == "text"):
        out[row.form_id] = row
    return out


def is_auto_ignored(upos: str, surface: str) -> bool:
    """Noms propres et chiffres romains : exclus par defaut du comptage."""
    return upos in PROPER_UPOS or bool(_ROMAN_RE.fullmatch(surface))


def process_in_background(text_id: int, engine_name: str | None = None) -> None:
    thread = threading.Thread(
        target=process_text, args=(text_id, engine_name), daemon=True
    )
    thread.start()
