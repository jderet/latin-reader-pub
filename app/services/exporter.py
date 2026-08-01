"""Exports : CoNLL-U pour la reutilisation scientifique, CSV pour Anki."""

from __future__ import annotations

import csv
import io

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Card, CardContext, Lemma, LemmaStatus, TextDoc, TextToken


def to_conllu(session: Session, text_id: int) -> str:
    """CoNLL-U partiel : ID, FORM, LEMMA, UPOS, FEATS, MISC.

    Les colonnes syntaxiques restent a « _ » : l'application n'analyse pas
    les dependances. Le champ MISC porte les traces utiles au controle :
    moteur, marge d'ambiguite, arbitrage manuel.
    """
    doc = session.get(TextDoc, text_id)
    if doc is None:
        raise ValueError("texte inconnu")

    tokens = session.scalars(
        select(TextToken).where(TextToken.text_id == text_id).order_by(TextToken.idx)
    ).all()

    out = io.StringIO()
    out.write(f"# newdoc id = text-{doc.id}\n")
    out.write(f"# title = {doc.title}\n")
    if doc.author:
        out.write(f"# author = {doc.author}\n")
    out.write(f"# engine = {doc.engine} {doc.engine_version}\n")

    current = None
    counter = 0
    for tok in tokens:
        if tok.sentence_idx != current:
            if current is not None:
                out.write("\n")
            current = tok.sentence_idx
            counter = 0
            sent = "".join(
                t.surface + (t.trailing or "")
                for t in tokens
                if t.sentence_idx == current
            ).strip()
            out.write(f"# sent_id = {doc.id}-{current}\n")
            out.write(f"# text = {sent}\n")
        counter += 1

        lemma = session.get(Lemma, tok.chosen_lemma_id) if tok.chosen_lemma_id else None
        feats = (
            "|".join(f"{k}={v}" for k, v in sorted((tok.feats or {}).items())) or "_"
        )
        misc = [f"Margin={round(tok.ambiguity_margin, 3)}"]
        if tok.is_resolved:
            misc.append("Resolved=Yes")
        if tok.is_guessed:
            misc.append("Guessed=Yes")
        if not (tok.trailing or "").strip() == "" :
            misc.append("SpaceAfter=No")

        out.write(
            "\t".join(
                [
                    str(counter),
                    tok.surface,
                    (lemma.lemma if lemma else "_"),
                    (lemma.upos if lemma else "_"),
                    "_",
                    feats,
                    "_",
                    "_",
                    "_",
                    "|".join(misc),
                ]
            )
            + "\n"
        )
    out.write("\n")
    return out.getvalue()


ANKI_HEADER = ["recto", "verso", "lemme", "type", "statut", "contextes"]


def to_anki_csv(session: Session) -> str:
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";", quoting=csv.QUOTE_MINIMAL)
    writer.writerow(ANKI_HEADER)

    cards = session.scalars(select(Card).order_by(Card.lemma_id, Card.kind)).all()
    for card in cards:
        lemma = session.get(Lemma, card.lemma_id)
        status = session.get(LemmaStatus, card.lemma_id)
        contexts = session.scalars(
            select(CardContext).where(CardContext.card_id == card.id)
        ).all()
        writer.writerow(
            [
                card.front,
                card.back,
                lemma.display if lemma else "",
                card.kind,
                status.status if status else "",
                " ⁃ ".join(c.sentence for c in contexts),
            ]
        )
    return out.getvalue()


def to_lemma_csv(session: Session) -> str:
    """Export du lexique personnel, independamment des fiches."""
    out = io.StringIO()
    writer = csv.writer(out, delimiter=";")
    writer.writerow(["lemme", "upos", "statut", "ignore", "glose", "note"])
    rows = session.scalars(select(LemmaStatus)).all()
    for row in rows:
        lemma = session.get(Lemma, row.lemma_id)
        writer.writerow(
            [
                lemma.lemma if lemma else "",
                lemma.upos if lemma else "",
                row.status,
                int(row.is_ignored),
                row.gloss or "",
                (row.note or "").replace("\n", " "),
            ]
        )
    return out.getvalue()
