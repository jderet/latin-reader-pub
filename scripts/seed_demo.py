#!/usr/bin/env python3
"""Charge un texte de demonstration et lance la lemmatisation.

    python scripts/seed_demo.py            # De bello Gallico I, 1
    python scripts/seed_demo.py mon.txt    # un autre fichier
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db import init_db, session_scope  # noqa: E402
from app.services import knowledge  # noqa: E402
from app.services.importer import create_text, process_text  # noqa: E402

CAESAR = """Gallia est omnis divisa in partes tres, quarum unam incolunt Belgae, aliam Aquitani, tertiam qui ipsorum lingua Celtae, nostra Galli appellantur. Hi omnes lingua, institutis, legibus inter se differunt. Gallos ab Aquitanis Garumna flumen, a Belgis Matrona et Sequana dividit. Horum omnium fortissimi sunt Belgae, propterea quod a cultu atque humanitate provinciae longissime absunt, minimeque ad eos mercatores saepe commeant atque ea quae ad effeminandos animos pertinent important, proximique sunt Germanis, qui trans Rhenum incolunt, quibuscum continenter bellum gerunt. Qua de causa Helvetii quoque reliquos Gallos virtute praecedunt, quod fere cotidianis proeliis cum Germanis contendunt, cum aut suis finibus eos prohibent aut ipsi in eorum finibus bellum gerunt. Eorum una, pars, quam Gallos obtinere dictum est, initium capit a flumine Rhodano, continetur Garumna flumine, Oceano, finibus Belgarum, attingit etiam ab Sequanis et Helvetiis flumen Rhenum, vergit ad septentriones. Belgae ab extremis Galliae finibus oriuntur, pertinent ad inferiorem partem fluminis Rheni, spectant in septentrionem et orientem solem. Aquitania a Garumna flumine ad Pyrenaeos montes et eam partem Oceani quae est ad Hispaniam pertinet; spectat inter occasum solis et septentriones."""


def main() -> None:
    init_db()
    if len(sys.argv) > 1:
        path = Path(sys.argv[1])
        content = path.read_text(encoding="utf-8")
        title = path.stem
        author = None
    else:
        content, title, author = CAESAR, "De bello Gallico I, 1", "César"

    with session_scope() as session:
        doc = create_text(session, title=title, author=author, content=content)
        text_id = doc.id

    print(f"texte {text_id} créé, lemmatisation en cours…")
    process_text(text_id)

    with session_scope() as session:
        from app.models import TextDoc, TextToken

        doc = session.get(TextDoc, text_id)
        print(f"  statut         : {doc.status}")
        print(f"  moteur         : {doc.engine} {doc.engine_version}")
        print(f"  mots           : {doc.word_count}")
        if doc.status == "failed":
            print(f"  erreur         : {doc.error_message}")
            return

        cov = knowledge.text_coverage(session, user_id=1, text_id=text_id)
        print(f"  lemmes distincts : {cov['distinct_lemmas']}")

        ambiguous = [
            t
            for t in session.query(TextToken).filter_by(text_id=text_id).all()
            if t.is_word and t.ambiguity_margin < 0.15
        ]
        print(f"  signalés ambigus : {len(ambiguous)} ({', '.join(sorted({t.surface for t in ambiguous}))})")

    print(f"\nOuvrez http://localhost:8000/texts/{text_id}")


if __name__ == "__main__":
    main()
