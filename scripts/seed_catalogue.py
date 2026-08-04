#!/usr/bin/env python3
"""Peuple le catalogue avec des oeuvres reelles et de courts extraits.

Il s'agit de donner une idee de ce a quoi ressemble une bibliotheque
fournie, non de constituer un corpus : chaque chapitre ne contient que
quelques lignes. Les textes sont antiques, donc du domaine public.

    python scripts/seed_catalogue.py            # ajoute ce qui manque
    python scripts/seed_catalogue.py --purge    # retire les livres de demo

Les livres crees ici portent la marque `is_demo` : l'administrateur peut
les retirer d'un coup lorsque le vrai catalogue est en place.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.models import Book  # noqa: E402
from app.services.importer import create_text, process_text  # noqa: E402

CATALOGUE = [
    {
        "title": "De bello Gallico",
        "subtitle": "La Guerre des Gaules",
        "author": "Jules César",
        "era": "Ier siècle av. J.-C.",
        "description": "Le récit des campagnes de César en Gaule, "
        "prose limpide et modèle du genre.",
        "chapters": [
            ("Livre I, 1 — La Gaule et ses peuples",
             "Gallia est omnis divisa in partes tres, quarum unam incolunt "
             "Belgae, aliam Aquitani, tertiam qui ipsorum lingua Celtae, "
             "nostra Galli appellantur. Hi omnes lingua, institutis, legibus "
             "inter se differunt."),
            ("Livre I, 2 — Orgétorix",
             "Apud Helvetios longe nobilissimus fuit et ditissimus "
             "Orgetorix. Is coniurationem nobilitatis fecit et civitati "
             "persuasit ut de finibus suis cum omnibus copiis exirent."),
        ],
    },
    {
        "title": "In Catilinam",
        "subtitle": "Les Catilinaires",
        "author": "Cicéron",
        "era": "Ier siècle av. J.-C.",
        "description": "Quatre discours prononcés au Sénat contre la "
        "conjuration de Catilina.",
        "chapters": [
            ("Première Catilinaire, exorde",
             "Quo usque tandem abutere, Catilina, patientia nostra? Quam diu "
             "etiam furor iste tuus nos eludet? Quem ad finem sese effrenata "
             "iactabit audacia?"),
        ],
    },
    {
        "title": "Aeneis",
        "subtitle": "L'Énéide",
        "author": "Virgile",
        "era": "Ier siècle av. J.-C.",
        "description": "L'épopée des origines de Rome, en hexamètres.",
        "chapters": [
            ("Chant I — Le proème",
             "Arma virumque cano, Troiae qui primus ab oris Italiam fato "
             "profugus Laviniaque venit litora, multum ille et terris "
             "iactatus et alto vi superum."),
        ],
    },
    {
        "title": "Metamorphoses",
        "subtitle": "Les Métamorphoses",
        "author": "Ovide",
        "era": "Ier siècle",
        "description": "Quinze livres de transformations, du chaos "
        "originel à l'apothéose de César.",
        "chapters": [
            ("Livre I — Le proème",
             "In nova fert animus mutatas dicere formas corpora; di, "
             "coeptis, nam vos mutastis et illas, adspirate meis primaque ab "
             "origine mundi ad mea perpetuum deducite tempora carmen."),
        ],
    },
    {
        "title": "Ab urbe condita",
        "subtitle": "Histoire romaine",
        "author": "Tite-Live",
        "era": "Ier siècle",
        "description": "L'histoire de Rome depuis sa fondation.",
        "chapters": [
            ("Livre I — Préface",
             "Facturusne operae pretium sim si a primordio urbis res populi "
             "Romani perscripserim nec satis scio nec, si sciam, dicere "
             "ausim."),
        ],
    },
    {
        "title": "Annales",
        "subtitle": "Annales",
        "author": "Tacite",
        "era": "IIe siècle",
        "description": "L'histoire du principat, d'Auguste à Néron, "
        "d'une concision redoutable.",
        "chapters": [
            ("Livre I, 1 — Les origines du pouvoir",
             "Urbem Romam a principio reges habuere; libertatem et "
             "consulatum Lucius Brutus instituit. Dictaturae ad tempus "
             "sumebantur."),
        ],
    },
    {
        "title": "Fabulae",
        "subtitle": "Fables",
        "author": "Phèdre",
        "era": "Ier siècle",
        "description": "Les fables ésopiques mises en vers latins, "
        "d'un abord aisé pour commencer.",
        "chapters": [
            ("Livre I — Le loup et l'agneau",
             "Ad rivum eundem lupus et agnus venerant siti compulsi. "
             "Superior stabat lupus, longeque inferior agnus."),
        ],
    },
    {
        "title": "Vulgata",
        "subtitle": "La Bible latine",
        "author": "Jérôme de Stridon",
        "era": "IVe siècle",
        "translator": "Jérôme",
        "description": "La traduction latine des Écritures, longtemps "
        "la porte d'entrée du latin.",
        "chapters": [
            ("Genèse I — La création",
             "In principio creavit Deus caelum et terram. Terra autem erat "
             "inanis et vacua, et tenebrae erant super faciem abyssi."),
        ],
    },
]


def purge() -> int:
    """Retire les livres de demonstration et leurs textes."""
    with session_scope() as session:
        livres = session.scalars(select(Book).where(Book.is_demo.is_(True))).all()
        retires = 0
        for livre in livres:
            for texte in list(livre.texts):
                session.delete(texte)
            session.delete(livre)
            retires += 1
    return retires


def seed() -> dict:
    init_db()
    crees, chapitres = 0, []

    for fiche in CATALOGUE:
        with session_scope() as session:
            existant = session.scalars(
                select(Book).where(Book.title == fiche["title"])
            ).first()
            if existant is not None:
                continue
            livre = Book(
                title=fiche["title"],
                subtitle=fiche.get("subtitle"),
                author=fiche["author"],
                era=fiche.get("era"),
                translator=fiche.get("translator"),
                description=fiche.get("description"),
                is_demo=True,
            )
            session.add(livre)
            session.flush()
            book_id = livre.id
            crees += 1

            for rang, (intitule, contenu) in enumerate(fiche["chapters"], start=1):
                doc = create_text(
                    session,
                    title=f"{fiche['title']} — {intitule}",
                    content=contenu,
                    author=fiche["author"],
                )
                doc.book_id = book_id
                doc.chapter_idx = rang
                doc.chapter_label = intitule
                session.flush()
                chapitres.append(doc.id)

    for text_id in chapitres:
        process_text(text_id)

    return {"books": crees, "chapters": len(chapitres)}


def main() -> None:
    if "--purge" in sys.argv:
        init_db()
        print(f"  {purge()} livre(s) de démonstration retiré(s)")
        return
    stats = seed()
    print(
        f"  {stats['books']} livre(s) et {stats['chapters']} chapitre(s) ajoutés"
        if stats["books"]
        else "  le catalogue de démonstration est déjà en place"
    )


if __name__ == "__main__":
    main()
