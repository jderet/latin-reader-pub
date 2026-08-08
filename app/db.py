from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .models import Base

log = logging.getLogger(__name__)

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).resolve().parents[1] / "var"))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DATA_DIR / 'latin.db'}")

engine = create_engine(
    DATABASE_URL,
    future=True,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

if DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        # Patiente jusqu'a 5 s si une autre requete ecrit, plutot que
        # d'echouer immediatement sur « database is locked ».
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()


SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


# Colonnes ajoutees apres coup. `create_all` ne modifie jamais une table
# existante : sans cette etape, une mise a jour obligerait a effacer la
# base et donc tout le vocabulaire deja saisi.
LATE_COLUMNS: dict[str, dict[str, str]] = {
    "user": {
        "is_guest": "BOOLEAN NOT NULL DEFAULT 0",
    },
    "text_token": {
        "is_enclitic": "BOOLEAN NOT NULL DEFAULT 0",
        "parent_token_id": "INTEGER",
    },
    "text": {
        "source_kind": "VARCHAR(16) NOT NULL DEFAULT 'text'",
        "video_id": "VARCHAR(16)",
        "cues": "TEXT",
        "audio_path": "VARCHAR(240)",
        "book_id": "INTEGER",
        "chapter_idx": "INTEGER NOT NULL DEFAULT 0",
        "chapter_label": "VARCHAR(120)",
    },
    "lemma": {
        "is_ignored": "BOOLEAN NOT NULL DEFAULT 0",
        "shared_gloss": "TEXT",
        "image_path": "VARCHAR(240)",
        "image_alt": "VARCHAR(240)",
    },
    "lemma_status": {
        "image_path": "VARCHAR(240)",
        "image_alt": "VARCHAR(240)",
    },
    "book": {
        "author_id": "INTEGER",
    },
}


def backfill_authors() -> int:
    """Reporte les auteurs saisis en texte libre vers la table `author`.

    Les livres portaient jusqu'ici un nom d'auteur en clair. On en tire
    la liste des auteurs, une entree par nom distinct, et on rattache
    chaque livre au sien. L'ancienne colonne n'est pas effacee : elle ne
    coute rien et permet de revenir en arriere.

    Idempotent — un livre deja rattache n'est pas touche.
    """
    # Par l'ORM et non en SQL brut : les valeurs par defaut du modele
    # (`created_at`) sont posees cote Python, un INSERT direct les
    # manquerait.
    from sqlalchemy import select

    from .models import Author, Book

    crees = 0
    with session_scope() as session:
        a_reprendre = session.scalars(
            select(Book).where(
                Book.author_id.is_(None), Book.author_name.isnot(None)
            )
        ).all()
        connus = {a.name: a for a in session.scalars(select(Author)).all()}
        for livre in a_reprendre:
            nom = (livre.author_name or "").strip()
            if not nom:
                continue
            auteur = connus.get(nom)
            if auteur is None:
                # L'epoque du premier livre rencontre : elle vaut pour
                # toute l'oeuvre, la ressaisir n'aurait pas de sens.
                auteur = Author(name=nom, era=livre.era)
                session.add(auteur)
                session.flush()
                connus[nom] = auteur
                crees += 1
            livre.author_id = auteur.id
    if crees:
        log.info("auteurs repris depuis les livres : %d", crees)
    return crees


def migrate() -> None:
    """Ajoute les colonnes manquantes, sans toucher aux donnees."""
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in LATE_COLUMNS.items():
            if table not in existing_tables:
                continue
            present = {c["name"] for c in inspector.get_columns(table)}
            for name, definition in columns.items():
                if name in present:
                    continue
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {definition}"))
                log.info("colonne ajoutée : %s.%s", table, name)


def init_db() -> None:
    # L'ordre compte : `create_all` cree la table `author`, que le report
    # remplit, mais `migrate` doit avoir ajoute `book.author_id` avant.
    migrate()
    Base.metadata.create_all(engine)
    backfill_authors()


def get_session() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def session_scope() -> Iterator[Session]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
