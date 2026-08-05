"""Tests de contrat de l'API v1.

Ils passent par le client HTTP complet : authentification par jeton,
schémas de sortie, codes d'erreur. C'est le contrat sur lequel les
clients natifs (PWA, Capacitor) peuvent compter.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.services.auth import create_user  # noqa: E402
from app.services.importer import create_text, process_text  # noqa: E402

CAESAR = (
    "Gallia est omnis divisa in partes tres, quarum unam incolunt Belgae, "
    "aliam Aquitani, tertiam qui ipsorum lingua Celtae, nostra Galli appellantur."
)


@pytest.fixture(scope="module")
def client():
    init_db()
    with session_scope() as s:
        create_user(s, "lectrice", "un-mot-de-passe-solide")
        doc = create_text(s, title="De bello Gallico", content=CAESAR)
        tid = doc.id
    process_text(tid, engine_name="lexicon")
    with TestClient(app) as c:
        c.text_id = tid
        yield c


@pytest.fixture(scope="module")
def bearer(client) -> dict:
    res = client.post(
        "/api/v1/auth/token",
        json={"username": "lectrice", "password": "un-mot-de-passe-solide",
              "label": "tests"},
    )
    assert res.status_code == 200
    token = res.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Authentification
# --------------------------------------------------------------------------
def test_sante_sans_authentification(client):
    assert client.get("/api/v1/health").json() == {"status": "ok", "api": "v1"}


def test_mauvais_identifiants(client):
    res = client.post(
        "/api/v1/auth/token", json={"username": "lectrice", "password": "faux"}
    )
    assert res.status_code == 401


def test_acces_refuse_sans_jeton(client):
    assert client.get("/api/v1/me").status_code == 401


def test_jeton_invalide(client):
    res = client.get("/api/v1/me", headers={"Authorization": "Bearer nimporte"})
    assert res.status_code == 401


def test_me(client, bearer):
    out = client.get("/api/v1/me", headers=bearer).json()
    assert out["username"] == "lectrice"


# --------------------------------------------------------------------------
# Bibliothèque et panneau
# --------------------------------------------------------------------------
def test_liste_des_textes(client, bearer):
    out = client.get("/api/v1/texts", headers=bearer).json()
    assert any(t["title"] == "De bello Gallico" for t in out)
    texte = next(t for t in out if t["title"] == "De bello Gallico")
    assert texte["status"] == "ready"
    assert texte["word_count"] > 0


def test_page_de_texte(client, bearer):
    out = client.get(
        f"/api/v1/texts/{client.text_id}/pages/0", headers=bearer
    ).json()
    assert out["page_count"] >= 1
    mots = [t for t in out["tokens"] if t["is_word"]]
    assert len(mots) > 10
    assert all(t["lemma_id"] for t in mots)
    # « est » garde deux candidats (sum / edo) mais le moteur est net :
    # il n'est pas signalé à arbitrer (marge au-dessus du seuil).
    est = next(t for t in out["tokens"] if t["surface"] == "est")
    assert est["ambiguous"] is False


def test_texte_inconnu(client, bearer):
    assert client.get("/api/v1/texts/999/pages/0", headers=bearer).status_code == 404


def test_panneau_de_mot(client, bearer):
    page = client.get(
        f"/api/v1/texts/{client.text_id}/pages/0", headers=bearer
    ).json()
    est = next(t for t in page["tokens"] if t["surface"] == "est")
    out = client.get(f"/api/v1/tokens/{est['id']}", headers=bearer).json()
    assert out["surface"] == "est"
    assert out["lemma_id"] == est["lemma_id"]
    lemmes = [c["display"] for c in out["candidates"]]
    assert any("sum" in l for l in lemmes)
    assert out["status"] is None  # jamais rencontré


# --------------------------------------------------------------------------
# Statuts et synchronisation différentielle
# --------------------------------------------------------------------------
def test_statut_et_delta(client, bearer):
    page = client.get(
        f"/api/v1/texts/{client.text_id}/pages/0", headers=bearer
    ).json()
    gallia = next(t for t in page["tokens"] if t["surface"] == "Gallia")

    res = client.post(
        f"/api/v1/lemmas/{gallia['lemma_id']}/status",
        json={"status": 2}, headers=bearer,
    )
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == 2 and body["is_locked"] is True
    horodatage = body["updated_at"]

    tout = client.get("/api/v1/statuses", headers=bearer).json()
    assert any(r["lemma_id"] == gallia["lemma_id"] for r in tout)

    # Un `since` postérieur à la modification ne rend rien.
    apres = client.get(
        "/api/v1/statuses", params={"since": "2999-01-01T00:00:00Z"},
        headers=bearer,
    ).json()
    assert apres == []

    # Un `since` antérieur rend la modification.
    avant = client.get(
        "/api/v1/statuses", params={"since": "2000-01-01T00:00:00Z"},
        headers=bearer,
    ).json()
    assert any(r["updated_at"] == horodatage for r in avant)


def test_statut_lemme_inconnu(client, bearer):
    res = client.post(
        "/api/v1/lemmas/999999/status", json={"status": 2}, headers=bearer
    )
    assert res.status_code == 404


def test_statut_hors_bornes(client, bearer):
    page = client.get(
        f"/api/v1/texts/{client.text_id}/pages/0", headers=bearer
    ).json()
    mot = next(t for t in page["tokens"] if t["is_word"])
    res = client.post(
        f"/api/v1/lemmas/{mot['lemma_id']}/status",
        json={"status": 7}, headers=bearer,
    )
    assert res.status_code == 422


# --------------------------------------------------------------------------
# Révision
# --------------------------------------------------------------------------
def test_file_de_revision_et_revue(client, bearer):
    from app.models import TextToken
    from app.services import cards as cards_svc
    from app.services import knowledge

    with session_scope() as s:
        tok = (
            s.query(TextToken)
            .filter(TextToken.surface == "Belgae")
            .first()
        )
        with session_scope() as s2:
            from app.models import User

            uid = s2.query(User).filter_by(username="lectrice").one().id
        knowledge.set_status(s, uid, tok.chosen_lemma_id, status=4,
                             gloss="les Belges")
        s.flush()
        cards_svc.create_cards(s, uid, lemma_id=tok.chosen_lemma_id,
                               kinds=["la_fr"], token=tok, gloss="les Belges")

    due = client.get("/api/v1/reviews/due", headers=bearer).json()
    assert due, "la fiche neuve doit être due"
    fiche = due[0]
    assert set(fiche) >= {"id", "kind", "front", "back", "is_new"}

    out = client.post(
        "/api/v1/reviews",
        json={"card_id": fiche["id"], "button": "good", "elapsed_ms": 1200},
        headers=bearer,
    )
    assert out.status_code == 200
    assert out.json()["interval_days"] == 1


def test_revision_fiche_inconnue(client, bearer):
    res = client.post(
        "/api/v1/reviews", json={"card_id": 999999, "button": "good"},
        headers=bearer,
    )
    assert res.status_code == 404


# --------------------------------------------------------------------------
# Le compte gestionnaire n'a pas de données de lecture
# --------------------------------------------------------------------------
def test_administrateur_refuse(client):
    with session_scope() as s:
        create_user(s, "gestionnaire", "un-mot-de-passe-solide", is_admin=True)
    res = client.post(
        "/api/v1/auth/token",
        json={"username": "gestionnaire", "password": "un-mot-de-passe-solide"},
    )
    assert res.status_code == 200
    entetes = {"Authorization": f"Bearer {res.json()['token']}"}
    assert client.get("/api/v1/reviews/due", headers=entetes).status_code == 403
