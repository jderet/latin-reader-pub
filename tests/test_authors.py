"""L'auteur d'un livre est choisi, non saisi.

Il etait une chaine libre, ce qui produisait des doublons : « Ciceron »
et « Cicéron » faisaient deux rayons pour un seul homme. C'est desormais
une table que l'administrateur tient, et le livre s'y rattache.

Le point delicat n'est pas le formulaire : c'est la reprise des donnees
existantes. Ces tests verrouillent surtout `backfill_authors`, qui doit
convertir sans perte et sans doublon, et pouvoir tourner deux fois.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import backfill_authors, init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Author, Book  # noqa: E402
from app.services.auth import create_user, get_by_username  # noqa: E402


@pytest.fixture(scope="module")
def admin():
    init_db()
    # Les modules de test partagent une meme base : on ne recree pas le
    # compte s'il est deja la.
    with session_scope() as s:
        if get_by_username(s, "conservateur") is None:
            create_user(s, "conservateur", "un-mot-de-passe", is_admin=True)
    with TestClient(app) as c:
        c.post(
            "/login",
            data={"username": "conservateur", "password": "un-mot-de-passe"},
        )
        yield c


# --------------------------------------------------------------------------
# Reprise des auteurs saisis en texte libre
# --------------------------------------------------------------------------
def test_la_reprise_cree_un_auteur_par_nom_distinct():
    init_db()
    with session_scope() as s:
        # Deux livres du meme auteur, un troisieme d'un autre : la reprise
        # doit produire deux auteurs, pas trois.
        s.add(Book(title="Bucolica", author_name="Virgile", era="Ier siècle av. J.-C."))
        s.add(Book(title="Georgica", author_name="Virgile"))
        s.add(Book(title="Amores", author_name="Ovide"))

    backfill_authors()

    with session_scope() as s:
        noms = {a.name for a in s.scalars(select(Author)).all()}
        assert {"Virgile", "Ovide"} <= noms
        livres = s.scalars(select(Book).where(Book.title.in_(
            ["Bucolica", "Georgica", "Amores"]
        ))).all()
        assert all(livre.author_id is not None for livre in livres)
        virgiles = {
            livre.author_id for livre in livres if livre.title != "Amores"
        }
        assert len(virgiles) == 1, "les deux Virgile doivent pointer le meme auteur"


def test_la_reprise_herite_de_l_epoque_du_premier_livre():
    with session_scope() as s:
        virgile = s.scalars(select(Author).where(Author.name == "Virgile")).first()
    assert virgile.era == "Ier siècle av. J.-C."


def test_la_reprise_peut_tourner_deux_fois():
    """Elle s'execute a chaque demarrage : elle ne doit rien redoubler."""
    with session_scope() as s:
        avant = len(s.scalars(select(Author)).all())
    assert backfill_authors() == 0
    with session_scope() as s:
        assert len(s.scalars(select(Author)).all()) == avant


def test_le_livre_repris_affiche_son_auteur():
    with session_scope() as s:
        livre = s.scalars(select(Book).where(Book.title == "Georgica")).first()
        assert livre.display_author == "Virgile"


# --------------------------------------------------------------------------
# Gestion des auteurs
# --------------------------------------------------------------------------
def test_l_admin_ajoute_un_auteur(admin):
    res = admin.post(
        "/admin/authors", data={"name": "Horace", "era": "Ier siècle av. J.-C."},
        follow_redirects=False,
    )
    assert res.status_code == 303
    with session_scope() as s:
        assert s.scalars(select(Author).where(Author.name == "Horace")).first()


def test_deux_auteurs_ne_portent_pas_le_meme_nom(admin):
    res = admin.post("/admin/authors", data={"name": "Horace", "era": ""})
    assert res.status_code == 400


def test_le_livre_choisit_son_auteur_dans_la_liste(admin):
    with session_scope() as s:
        horace = s.scalars(select(Author).where(Author.name == "Horace")).first()
        horace_id = horace.id

    admin.post(
        "/admin/books",
        data={"title": "Carmina", "subtitle": "Odes", "author_id": str(horace_id),
              "era": "", "translator": "", "description": ""},
    )
    with session_scope() as s:
        livre = s.scalars(select(Book).where(Book.title == "Carmina")).first()
        assert livre.author_id == horace_id
        assert livre.display_author == "Horace"
        # L'epoque n'est pas ressaisie : elle vient de l'auteur.
        assert livre.display_era == "Ier siècle av. J.-C."


def test_un_auteur_inconnu_est_refuse(admin):
    res = admin.post(
        "/admin/books",
        data={"title": "Fantôme", "subtitle": "", "author_id": "99999",
              "era": "", "translator": "", "description": ""},
    )
    assert res.status_code == 400


def test_on_ne_retire_pas_un_auteur_qui_a_des_livres(admin):
    """Sinon ses livres deviendraient anonymes sans qu'on s'en apercoive."""
    with session_scope() as s:
        horace_id = s.scalars(select(Author).where(Author.name == "Horace")).first().id
    res = admin.post(f"/admin/authors/{horace_id}/delete")
    assert res.status_code == 400
    with session_scope() as s:
        assert s.scalars(select(Author).where(Author.name == "Horace")).first()


def test_on_retire_un_auteur_sans_livre(admin):
    admin.post("/admin/authors", data={"name": "Perse", "era": ""})
    with session_scope() as s:
        perse_id = s.scalars(select(Author).where(Author.name == "Perse")).first().id
    res = admin.post(f"/admin/authors/{perse_id}/delete", follow_redirects=False)
    assert res.status_code == 303
    with session_scope() as s:
        assert s.scalars(select(Author).where(Author.name == "Perse")).first() is None


def test_le_catalogue_groupe_par_auteur(admin):
    """Le regroupement du catalogue suit la table, pas la chaine libre."""
    res = admin.get("/bibliotheque", follow_redirects=True)
    assert "Horace" in res.text
