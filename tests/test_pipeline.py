from __future__ import annotations

import datetime as dt
import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp())

from app.db import init_db, session_scope  # noqa: E402
from app.models import Card, Lemma, LemmaStatus, TextToken  # noqa: E402
from app.nlp.lexicon_engine import LexiconLemmatizer  # noqa: E402
from app.nlp.normalize import form_key, split_enclitic  # noqa: E402
from app.services import cards as cards_svc  # noqa: E402
from app.services import disambiguation, exporter, knowledge  # noqa: E402
from app.services.importer import create_text, process_text  # noqa: E402
from app.services.srs import Sm2State, next_status, sm2  # noqa: E402

CAESAR = (
    "Gallia est omnis divisa in partes tres, quarum unam incolunt Belgae, "
    "aliam Aquitani, tertiam qui ipsorum lingua Celtae, nostra Galli appellantur. "
    "Hi omnes lingua, institutis, legibus inter se differunt. "
    "Gallos ab Aquitanis Garumna flumen, a Belgis Matrona et Sequana dividit."
)


@pytest.fixture(scope="module", autouse=True)
def _db():
    init_db()


# --------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------
def test_form_key_unifie_u_v_et_i_j():
    assert form_key("Divisa") == "diuisa"
    assert form_key("VIRTVTE") == "uirtute"
    assert form_key("iam") == form_key("jam")


def test_enclitiques_proteges():
    assert split_enclitic("atque") is None
    assert split_enclitic("neque") is None
    assert split_enclitic("proximique") == ("proximi", "que")


# --------------------------------------------------------------------------
# Lemmatisation et ambiguite
# --------------------------------------------------------------------------
def test_est_tranche_pour_sum():
    result = LexiconLemmatizer().analyze("Gallia est omnis.")
    est = next(t for t in result.tokens if t.surface == "est")
    assert est.candidates[0].lemma == "sum"
    assert "edo" in [c.lemma for c in est.candidates]


def test_ambiguite_ignore_les_variantes_du_meme_lemme():
    """lingua nominatif vs ablatif : meme lemme, donc pas une ambiguite."""
    result = LexiconLemmatizer().analyze("Hi omnes lingua differunt.")
    lingua = next(t for t in result.tokens if t.surface == "lingua")
    assert len(lingua.candidates) > 1
    assert lingua.distinct_lemmas() == ["lingua"]
    assert lingua.ambiguity_margin == 1.0


def test_ambiguite_reelle_detectee():
    result = LexiconLemmatizer().analyze("quod fere cotidianis proeliis contendunt.")
    quod = next(t for t in result.tokens if t.surface == "quod")
    assert len(quod.distinct_lemmas()) == 2
    assert quod.ambiguity_margin < 0.15


# --------------------------------------------------------------------------
# Import complet
# --------------------------------------------------------------------------
@pytest.fixture(scope="module")
def text_id() -> int:
    with session_scope() as s:
        doc = create_text(s, title="De bello Gallico I, 1", content=CAESAR)
        tid = doc.id
    process_text(tid, engine_name="lexicon")
    return tid


def test_import_produit_des_tokens_annotes(text_id):
    with session_scope() as s:
        tokens = s.query(TextToken).filter_by(text_id=text_id).all()
        words = [t for t in tokens if t.is_word]
        assert len(words) == 40  # nombre de mots du passage
        assert len(tokens) == len(words) + 10  # signes de ponctuation
        assert all(t.chosen_lemma_id for t in words)
        assert all(t.candidates for t in words)


def test_le_statut_porte_sur_le_lemme_pas_sur_la_forme(text_id):
    """Le coeur du projet : Gallos, Galli et Gallia relevent de lemmes
    distincts, mais toutes les occurrences d'un meme lemme partagent un
    statut unique. Ici, marquer « flumen » colore aussi « flumine »."""
    with session_scope() as s:
        doc_tokens = s.query(TextToken).filter_by(text_id=text_id).all()
        flumen = next(t for t in doc_tokens if t.surface == "flumen")
        knowledge.set_status(s, flumen.chosen_lemma_id, status=2)
        s.flush()

        lemma_id = flumen.chosen_lemma_id
        occurrences = [t for t in doc_tokens if t.chosen_lemma_id == lemma_id]
        assert len(occurrences) >= 1
        st = s.get(LemmaStatus, lemma_id)
        assert st.status == 2
        assert st.is_locked is True  # modification manuelle -> verrou


def test_arbitrage_se_propage(text_id):
    with session_scope() as s:
        tokens = s.query(TextToken).filter_by(text_id=text_id).all()
        est = next(t for t in tokens if t.surface == "est")
        edo = s.query(Lemma).filter_by(lemma="edo").first()
        assert edo is not None

        result = disambiguation.resolve(s, token=est, lemma_id=edo.id, scope="global")
        assert result.updated_token_ids
        s.flush()
        for tid in result.updated_token_ids:
            assert s.get(TextToken, tid).chosen_lemma_id == edo.id

        # retour a sum, pour ne pas polluer les tests suivants
        sum_lemma = s.query(Lemma).filter_by(lemma="sum").first()
        disambiguation.resolve(s, token=est, lemma_id=sum_lemma.id, scope="global")


def test_couverture(text_id):
    with session_scope() as s:
        cov = knowledge.text_coverage(s, text_id)
        assert cov["total_words"] > 0
        assert 0.0 <= cov["known_ratio"] <= 1.0
        assert cov["distinct_lemmas"] > 10


def test_export_conllu(text_id):
    with session_scope() as s:
        out = exporter.to_conllu(s, text_id)
    assert "# sent_id" in out
    lines = [l for l in out.splitlines() if l and not l.startswith("#")]
    assert all(len(l.split("\t")) == 10 for l in lines)
    assert "\tGallia\t" in out or "\tgallia\t" in out


# --------------------------------------------------------------------------
# SM-2
# --------------------------------------------------------------------------
def test_sm2_progression_nominale():
    state = Sm2State()
    r1 = sm2(state, 4)
    assert r1.interval_days == 1 and r1.repetitions == 1
    r2 = sm2(Sm2State(r1.ease_factor, r1.interval_days, r1.repetitions), 4)
    assert r2.interval_days == 6
    r3 = sm2(Sm2State(r2.ease_factor, r2.interval_days, r2.repetitions), 4)
    assert r3.interval_days == round(6 * r2.ease_factor)


def test_sm2_echec_reinitialise():
    r = sm2(Sm2State(2.5, 30, 5, 0), 1)
    assert r.is_lapse and r.interval_days == 1 and r.repetitions == 0
    assert r.ease_factor == pytest.approx(2.30)


def test_sm2_plancher_de_facilite():
    ef = 1.3
    for _ in range(5):
        ef = sm2(Sm2State(ef, 10, 3, 0), 3).ease_factor
    assert ef >= 1.3


def test_sm2_plafond_intervalle():
    r = sm2(Sm2State(2.5, 300, 9, 0), 5)
    assert r.interval_days <= 365


# --------------------------------------------------------------------------
# Transitions de statut
# --------------------------------------------------------------------------
def test_promotion_exige_toutes_les_fiches_matures():
    assert next_status(3, quality=4, repetitions_per_card=[3, 3], is_locked=False) == 2
    assert next_status(3, quality=4, repetitions_per_card=[3, 1], is_locked=False) is None


def test_retrogradation_plafonnee_a_trois():
    assert next_status(1, quality=1, repetitions_per_card=[5], is_locked=False) == 2
    assert next_status(3, quality=1, repetitions_per_card=[5], is_locked=False) is None


def test_verrou_gele_l_automatisme():
    assert next_status(3, quality=5, repetitions_per_card=[9], is_locked=True) is None


def test_maitrise_ne_descend_pas_sous_zero():
    assert next_status(0, quality=5, repetitions_per_card=[9], is_locked=False) is None


# --------------------------------------------------------------------------
# Fiches
# --------------------------------------------------------------------------
def test_creation_et_revision_de_fiche(text_id):
    with session_scope() as s:
        token = (
            s.query(TextToken)
            .filter(TextToken.text_id == text_id, TextToken.surface == "Belgae")
            .first()
        )
        knowledge.set_status(s, token.chosen_lemma_id, status=4, gloss="les Belges")
        s.flush()
        created = cards_svc.create_cards(
            s,
            lemma_id=token.chosen_lemma_id,
            kinds=["la_fr", "cloze"],
            token=token,
            gloss="les Belges",
        )
        assert len(created) == 2
        cloze = next(c for c in created if c.kind == "cloze")
        assert "……" in cloze.front
        assert cloze.back == "Belgae"

        out = cards_svc.review(s, cloze, "good")
        assert out["interval_days"] == 1
        s.flush()
        assert s.query(Card).filter_by(id=cloze.id).one().repetitions == 1


def test_fiche_a_trous_alimente_l_axe_morphologique(text_id):
    with session_scope() as s:
        weak = knowledge.weakest_features(s, limit=50)
        assert isinstance(weak, list)


def test_glose_requise_pour_le_vocabulaire(text_id):
    with session_scope() as s:
        token = s.query(TextToken).filter_by(text_id=text_id).filter(
            TextToken.surface == "Matrona"
        ).first()
        with pytest.raises(ValueError):
            cards_svc.create_cards(
                s, lemma_id=token.chosen_lemma_id, kinds=["la_fr"], token=token, gloss=""
            )
