"""Le site se lit sans inscription.

Un visiteur qui arrive sans compte n'est pas renvoye vers /login : on lui
ouvre un compte de passage, rattache a son cookie, qui porte ses statuts
et ses fiches comme n'importe quel compte. S'il s'inscrit ensuite, ce
compte est renomme plutot que double — ce qu'il a marque en lisant le
suit.

Ce que ces tests verrouillent surtout : l'ouverture s'arrete a la
lecture. Ni la gestion, ni l'API v1 ne s'ouvrent aux anonymes.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Lemma, LemmaStatus, User  # noqa: E402
from app.services.auth import GUEST_PREFIX, get_by_username  # noqa: E402
from app.services.importer import create_text, process_text  # noqa: E402

CAESAR = "Gallia est omnis divisa in partes tres."


@pytest.fixture(scope="module")
def texte() -> int:
    init_db()
    with session_scope() as s:
        doc = create_text(s, title="De bello Gallico", content=CAESAR)
        tid = doc.id
    process_text(tid, engine_name="lexicon")
    return tid


@pytest.fixture
def visiteur():
    """Un client neuf : aucun cookie, donc aucun compte."""
    with TestClient(app) as c:
        yield c


# --------------------------------------------------------------------------
# Lecture libre
# --------------------------------------------------------------------------
def test_la_bibliotheque_s_ouvre_sans_compte(visiteur, texte):
    res = visiteur.get("/")
    assert res.status_code == 200
    assert "De bello Gallico" in res.text


def test_le_texte_se_lit_sans_compte(visiteur, texte):
    assert visiteur.get(f"/texts/{texte}").status_code == 200


def test_un_compte_de_passage_est_ouvert(visiteur, texte):
    visiteur.get("/")
    with session_scope() as s:
        invites = s.scalars(select(User).where(User.is_guest.is_(True))).all()
    assert any(u.username.startswith(GUEST_PREFIX) for u in invites)


def test_le_visiteur_garde_son_compte_d_une_page_a_l_autre(visiteur, texte):
    """Sans quoi chaque clic ouvrirait un compte et perdrait le precedent."""
    visiteur.get("/")
    with session_scope() as s:
        avant = len(s.scalars(select(User).where(User.is_guest.is_(True))).all())
    visiteur.get(f"/texts/{texte}")
    visiteur.get("/")
    with session_scope() as s:
        apres = len(s.scalars(select(User).where(User.is_guest.is_(True))).all())
    assert apres == avant


def test_un_invite_annote_comme_un_lecteur(visiteur, texte):
    visiteur.get("/")
    with session_scope() as s:
        lemma_id = s.scalars(select(Lemma.id)).first()
    res = visiteur.post(f"/api/lemmas/{lemma_id}/status", data={"status": 2})
    assert res.status_code == 200
    with session_scope() as s:
        ligne = s.scalars(
            select(LemmaStatus).where(LemmaStatus.lemma_id == lemma_id)
        ).first()
    assert ligne is not None and ligne.status == 2


# --------------------------------------------------------------------------
# Conversion en compte nomme
# --------------------------------------------------------------------------
def test_l_inscription_conserve_le_vocabulaire_de_l_invite(visiteur, texte):
    """Le point de toute l'affaire : s'inscrire ne repart pas de zero."""
    visiteur.get("/")
    with session_scope() as s:
        lemma_id = s.scalars(select(Lemma.id)).first()
    visiteur.post(f"/api/lemmas/{lemma_id}/status", data={"status": 3})

    res = visiteur.post(
        "/register",
        data={"username": "nouvelle", "password": "un-mot-de-passe",
              "password2": "un-mot-de-passe"},
        follow_redirects=False,
    )
    assert res.status_code == 303

    with session_scope() as s:
        compte = get_by_username(s, "nouvelle")
        assert compte is not None
        assert compte.is_guest is False
        statut = s.scalars(
            select(LemmaStatus).where(
                LemmaStatus.user_id == compte.id, LemmaStatus.lemma_id == lemma_id
            )
        ).first()
    assert statut is not None and statut.status == 3


def test_on_ne_s_inscrit_pas_sous_un_nom_reserve(visiteur, texte):
    res = visiteur.post(
        "/register",
        data={"username": f"{GUEST_PREFIX}abc", "password": "un-mot-de-passe",
              "password2": "un-mot-de-passe"},
    )
    assert res.status_code == 400


def test_un_compte_de_passage_n_est_pas_joignable_par_mot_de_passe(visiteur, texte):
    """Son mot de passe est inutilisable : seul le cookie y donne acces."""
    visiteur.get("/")
    with session_scope() as s:
        invite = s.scalars(select(User).where(User.is_guest.is_(True))).first()
        nom = invite.username
    res = visiteur.post(
        "/login", data={"username": nom, "password": "!"}, follow_redirects=False
    )
    assert res.status_code == 401


# --------------------------------------------------------------------------
# L'ouverture s'arrete a la lecture
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "chemin", ["/admin", "/admin/users", "/admin/books", "/admin/arbitrate"]
)
def test_la_gestion_reste_fermee(visiteur, texte, chemin):
    res = visiteur.get(chemin, follow_redirects=False)
    assert res.status_code == 303
    assert res.headers["location"] == "/login"


def test_l_api_v1_reste_fermee(visiteur, texte):
    assert visiteur.get("/api/v1/me").status_code == 401
