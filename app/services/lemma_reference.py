"""Validation des lemmes contre une liste de reference scientifique.

Le moteur de repli devine des lemmes par terminaison ; il en produit
donc d'inexistants (« abuteo » pour abutere, « quus » pour quo). Ce
module confronte chaque candidat a une liste de lemmes attestes, ce qui
permet d'ecarter les fantaisies avant de les proposer a l'utilisateur.

Reference : le **Gaffiot**, deja charge pour les vedettes et les gloses.
Une liste plus large a servi jusqu'a son arrivee ; elle couvrait le latin
medieval et la prosopographie, au prix de 91 Mo en memoire et d'une
permissivite genante. Les mots que le Gaffiot ignore — noms propres,
latin tardif — se creent a la main, ce qui est le comportement voulu.

Regenerer la base : python tools/build_gaffiot.py
"""

from __future__ import annotations

import gzip
import logging
from pathlib import Path

from ..nlp.normalize import lemma_key

log = logging.getLogger(__name__)

# La liste de reference est desormais le Gaffiot lui-meme. Le LiLa Lemma
# Bank a servi jusqu'a son arrivee : 282 000 lemmes, Du Cange et
# Forcellini compris, pour 91 Mo en memoire — le plus gros poste de
# l'application, alors que son role s'etait reduit a un filet. Le Gaffiot
# fait foi, et il est deja charge.


class LemmaReference:
    """Liste de lemmes attestes, adossee au Gaffiot.

    L'interface est celle qu'attendent les moteurs : `knows` pour
    l'existence, `entries` pour les categories d'un lemme.
    """

    @property
    def _gaffiot(self):
        from .gaffiot import get_gaffiot

        return get_gaffiot()

    @property
    def available(self) -> bool:
        return self._gaffiot.available

    @property
    def entries(self) -> "dict[str, set[str]]":
        """Categories connues, par cle. Vue construite a la demande."""
        return _CategoryView(self._gaffiot)

    def knows(self, lemma: str) -> bool:
        return self._gaffiot.knows(lemma)

    def knows_as(self, lemma: str, upos: str) -> bool:
        """Le lemme existe-t-il ?

        Le filtrage porte sur l'existence, **jamais** sur la categorie :
        les inventaires divergent trop. sum est AUX pour Stanza et verbe
        pour un dictionnaire ; rejeter la-dessus faisait basculer « est »
        de sum vers edo. La categorie ne sert qu'a departager.
        """
        return self.knows(lemma)

    def agrees_on_pos(self, lemma: str, upos: str) -> bool:
        """La categorie annoncee est-elle coherente avec la reference ?"""
        tags = self.entries.get(lemma_key(lemma))
        if not tags:
            return True
        if upos in tags:
            return True
        equivalents = {
            "AUX": {"VERB"}, "VERB": {"AUX"},
            "ADJ": {"VERB", "NOUN"}, "PROPN": {"NOUN"}, "NOUN": {"PROPN"},
            "DET": {"PRON", "ADJ"}, "PRON": {"DET"},
            "SCONJ": {"CCONJ", "ADV"}, "CCONJ": {"SCONJ", "ADV"},
        }
        return bool(equivalents.get(upos, set()) & tags)


class _CategoryView:
    """Acces en lecture aux categories du Gaffiot, sans rien recopier."""

    __slots__ = ("_base",)

    def __init__(self, base) -> None:
        self._base = base

    def get(self, cle: str, defaut=None):
        par_categorie = self._base.entries.get(cle)
        if par_categorie is None:
            return defaut
        # Une entree sans categorie declaree ne contraint rien.
        return {upos for upos in par_categorie if upos} or set()

    def __contains__(self, cle: str) -> bool:
        return cle in self._base.entries

    def __len__(self) -> int:
        return len(self._base.entries)

    def __iter__(self):
        return iter(self._base.entries)


_reference: LemmaReference | None = None


def get_reference() -> LemmaReference:
    global _reference
    if _reference is None:
        _reference = LemmaReference()
    return _reference


# Categories que la reference n'inventorie pas : on ne filtre pas dessus.
# « X » en est volontairement exclu : c'est precisement la categorie que
# les moteurs attribuent aux formes qu'ils n'ont pas su analyser, donc
# celle ou se logent les lemmes inventes.
UNFILTERED_POS = {"PUNCT", "SYM", "NUM"}


def filter_candidates(candidates: list, *, keep_minimum: bool = True) -> tuple[list, int]:
    """Ecarte les candidats dont le lemme n'est pas atteste.

    Retourne (candidats retenus, nombre d'ecartes). Si tous sont ecartes
    et `keep_minimum` est vrai, on conserve le meilleur : mieux vaut un
    lemme douteux, signale comme tel, que pas de lemme du tout.
    """
    reference = get_reference()
    if not reference.available or not candidates:
        return candidates, 0

    kept = [
        c for c in candidates
        if c.upos in UNFILTERED_POS or reference.knows_as(c.lemma, c.upos)
    ]
    removed = len(candidates) - len(kept)

    # Un candidat dont la categorie contredit la reference est conserve,
    # mais recule dans le classement.
    for cand in kept:
        if not reference.agrees_on_pos(cand.lemma, cand.upos):
            cand.score *= 0.85
    kept.sort(key=lambda c: -c.score)

    if not kept:
        return (candidates[:1], len(candidates) - 1) if keep_minimum else ([], removed)
    return kept, removed


def status() -> dict:
    reference = get_reference()
    return {
        "available": reference.available,
        "entries": len(reference.entries),
        "source": "Gaffiot 2016 — la base lexicale fait référence",
    }


# --------------------------------------------------------------------------
# Recherche contrainte : proposer des lemmes attestes
# --------------------------------------------------------------------------
# (terminaison observee, terminaisons possibles du lemme, categorie, poids)
# L'idee : au lieu de deviner un lemme et d'esperer qu'il existe, on
# engendre les candidats plausibles et on ne retient que ceux qui figurent
# dans la reference. La liste transforme la devinette en verification.
ENDING_RULES: list[tuple[str, tuple[str, ...], str, float]] = [
    # --- verbes, formes personnelles ---
    ("ierunt", ("eo",), "VERB", 0.50),
    ("erunt", ("o", "or"), "VERB", 0.52),
    ("erant", ("o", "or"), "VERB", 0.52),
    ("abant", ("o",), "VERB", 0.52),
    ("ebant", ("eo", "o"), "VERB", 0.52),
    ("iebant", ("io",), "VERB", 0.50),
    ("untur", ("or", "o"), "VERB", 0.50),
    ("antur", ("or", "o"), "VERB", 0.50),
    ("ntur", ("or", "o"), "VERB", 0.45),
    ("mus", ("o", "or"), "VERB", 0.44),
    ("tis", ("o", "or"), "VERB", 0.42),
    ("unt", ("o",), "VERB", 0.50),
    ("ant", ("o",), "VERB", 0.50),
    ("ent", ("eo", "o"), "VERB", 0.48),
    ("auit", ("o",), "VERB", 0.56),
    ("euit", ("eo", "o"), "VERB", 0.54),
    ("iuit", ("io", "eo"), "VERB", 0.54),
    ("uit", ("o", "eo", "uo"), "VERB", 0.50),
    ("sit", ("o", "do", "to"), "VERB", 0.44),
    ("xit", ("go", "ho", "co"), "VERB", 0.46),
    ("iui", ("io", "eo"), "VERB", 0.46),
    ("aui", ("o",), "VERB", 0.48),
    ("eui", ("eo", "o"), "VERB", 0.46),
    ("it", ("o", "eo", "io"), "VERB", 0.40),
    ("et", ("eo", "o"), "VERB", 0.38),
    ("at", ("o",), "VERB", 0.38),
    ("t", ("o", "eo", "io", "or"), "VERB", 0.26),
    # --- verbes, formes nominales ---
    ("ndus", ("o", "or"), "VERB", 0.40),
    ("ndos", ("o", "or"), "VERB", 0.40),
    ("ntem", ("o", "eo", "io"), "VERB", 0.40),
    ("ntis", ("o", "eo", "io"), "VERB", 0.40),
    ("ns", ("o", "eo", "io"), "VERB", 0.36),
    ("tus", ("o", "or"), "VERB", 0.34),
    ("sus", ("o", "or", "do"), "VERB", 0.32),
    ("tum", ("o", "or"), "VERB", 0.32),
    ("are", ("o",), "VERB", 0.44),
    ("ere", ("o", "eo"), "VERB", 0.44),
    ("ire", ("io",), "VERB", 0.44),
    # --- noms et adjectifs ---
    ("ges", ("x",), "NOUN", 0.46),
    ("gis", ("x",), "NOUN", 0.46),
    ("gem", ("x",), "NOUN", 0.46),
    ("ces", ("x",), "NOUN", 0.44),
    ("cis", ("x",), "NOUN", 0.44),
    ("cem", ("x",), "NOUN", 0.44),
    ("ora", ("us",), "NOUN", 0.46),
    ("oribus", ("us", "or"), "NOUN", 0.46),
    ("oris", ("us", "or", "os"), "NOUN", 0.44),
    ("orum", ("us", "um", "er"), "NOUN", 0.48),
    ("arum", ("a",), "NOUN", 0.48),
    ("ibus", ("is", "s", "us", "er", "en", "or", "o", "x"), "NOUN", 0.44),
    ("ium", ("is", "s", "e"), "NOUN", 0.36),
    ("em", ("is", "s", "o", "or", "en", "us", "es", "x", ""), "NOUN", 0.36),
    ("es", ("is", "es", "s", "a"), "NOUN", 0.34),
    ("is", ("is", "s", "us", "er", "e"), "NOUN", 0.32),
    ("os", ("us", "os", "or"), "NOUN", 0.36),
    ("as", ("a", "as"), "NOUN", 0.36),
    ("am", ("a",), "NOUN", 0.40),
    ("um", ("um", "us", "a", "is", "", "er"), "NOUN", 0.34),
    ("ae", ("a",), "NOUN", 0.40),
    ("us", ("us", "um"), "NOUN", 0.32),
    ("i", ("us", "um", "er", "ius"), "NOUN", 0.30),
    ("o", ("us", "um", "o"), "NOUN", 0.28),
    ("a", ("a", "um", "us"), "NOUN", 0.26),
    ("e", ("is", "us", "e"), "NOUN", 0.24),
    # --- adjectifs et adverbes ---
    ("issimus", ("us",), "ADJ", 0.40),
    ("issime", ("us", "e"), "ADV", 0.38),
    ("ior", ("us", "is"), "ADJ", 0.38),
    ("iter", ("is", "er"), "ADV", 0.34),
]

MAX_PROPOSALS = 4


ENCLITICS = ("que", "ne", "ue")


def propose(form: str, max_results: int = MAX_PROPOSALS, _depth: int = 0) -> list[tuple[str, str, float]]:
    """Lemmes attestes compatibles avec cette forme flechie.

    Retourne des triplets (lemme, categorie, score). La forme elle-meme
    est proposee en premier si elle est attestee comme lemme : beaucoup
    de formes de textes sont deja des nominatifs ou des infinitifs.
    """
    reference = get_reference()
    if not reference.available:
        return []

    key = lemma_key(form)
    seen: dict[tuple[str, str], float] = {}

    tags = reference.entries.get(key)
    if tags is not None:
        for tag in sorted(tags) or ["X"]:
            seen[(key, tag)] = 0.55

    for ending, replacements, upos, weight in ENDING_RULES:
        if not key.endswith(ending) or len(key) - len(ending) < 2:
            continue
        stem = key[: len(key) - len(ending)]
        for replacement in replacements:
            candidate = stem + replacement
            entry = reference.entries.get(candidate)
            if entry is None:
                continue
            # Un mot invariable n'a pas de forme flechie : proposer
            # l'adverbe « una » comme lemme de « unam » est impossible.
            # Un ensemble vide signifie « categorie inconnue », non
            # « invariable » : sans cette nuance, tous les lemmes que le
            # dictionnaire ne classe pas etaient ecartes, « incolo »
            # compris.
            if entry and entry <= INDECLINABLE:
                continue
            # Une categorie coherente avec la regle inspire plus confiance.
            score = weight + (0.06 if upos in entry else 0.0)
            # Categorie inconnue : on retient celle que la regle suppose,
            # plutot que de trier un ensemble vide.
            chosen = upos if (upos in entry or not entry) else sorted(entry)[0]
            pair = (candidate, chosen)
            if score > seen.get(pair, 0):
                seen[pair] = score

    # Enclitiques : « uirumque » n'est pas un lemme, « uir » l'est.
    # On ne retente qu'une fois, et seulement si rien n'a ete trouve.
    if not seen and _depth == 0:
        from ..nlp.normalize import ENCLITIC_EXCEPTIONS

        if key not in ENCLITIC_EXCEPTIONS:
            for enclitic in ENCLITICS:
                if key.endswith(enclitic) and len(key) - len(enclitic) >= 3:
                    inner = propose(key[: -len(enclitic)], max_results, _depth + 1)
                    if inner:
                        return [(l, u, s * 0.9) for l, u, s in inner]

    ranked = sorted(seen.items(), key=lambda kv: (-kv[1], len(kv[0][0])))
    return [(lemma, upos, score) for (lemma, upos), score in ranked[:max_results]]


# --------------------------------------------------------------------------
# Table forme -> lemme, attestee dans des corpus annotes
# --------------------------------------------------------------------------
FORM_TABLE = Path(__file__).resolve().parents[2] / "data" / "form_lemma.tsv.gz"

# Categories sans flexion : leur lemme est leur seule forme possible.
INDECLINABLE = {"ADV", "ADP", "CCONJ", "SCONJ", "PART", "INTJ"}


class FormLemmaTable:
    """Couples (forme flechie, lemme) releves dans les treebanks UD.

    C'est le chainon que le Lemma Bank ne fournit pas : il atteste qu'une
    forme donnee se rattache effectivement a tel lemme, et non seulement
    que ce lemme existe quelque part.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or FORM_TABLE
        self.entries: dict[str, list[tuple[str, str, int]]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            log.info("table forme→lemme absente (%s)", self.path)
            return
        with gzip.open(self.path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip() or line.startswith("#"):
                    continue
                form, _, payload = line.rstrip("\n").partition("\t")
                readings: list[tuple[str, str, int]] = []
                for chunk in payload.split("|"):
                    parts = chunk.split(":")
                    if len(parts) == 3:
                        readings.append((parts[0], parts[1], int(parts[2])))
                if readings:
                    self.entries[form] = readings
        log.info("table forme→lemme : %d formes attestées", len(self.entries))

    @property
    def available(self) -> bool:
        return bool(self.entries)

    def readings(self, form: str) -> list[tuple[str, str, float]]:
        """Lectures attestees d'une forme, ponderees par leur frequence.

        Les corpus comportent quelques erreurs d'annotation : sur 865 000
        tokens etiquetes a la main, « unam » est rattache 177 fois a unus
        et 1 fois a una, adverbe invariable qui ne peut avoir de forme
        flechie. On ecarte donc les lectures marginales et celles dont le
        lemme n'a aucun radical commun avec la forme. La lecture
        dominante est toujours conservee : elle porte la suppletion
        (est -> sum), ou aucun radical n'est partage.
        """
        key = lemma_key(form)
        readings = self.entries.get(key)
        if not readings:
            return []

        total = sum(n for _, _, n in readings)
        best = max(n for _, _, n in readings)
        out = []
        for lemma, upos, count in readings:
            share = count / total
            if count < best and not self._plausible(key, lemma, upos, count, share):
                continue
            # 0.95 pour une lecture unique, moins si la forme est ambigue.
            out.append((lemma, upos, round(0.60 + 0.35 * share, 3)))
        return out

    # Une lecture secondaire doit etre suffisamment attestee ET
    # morphologiquement plausible pour etre retenue.
    MIN_COUNT = 3
    MIN_SHARE = 0.02

    @staticmethod
    def _shares_stem(form: str, lemma: str) -> bool:
        """La forme et le lemme partagent-ils un debut de radical ?

        « unam »/« unus » partagent « un » ; « hi »/« is » ne partagent
        rien, et « is » n'a effectivement aucune forme « hi ».
        """
        common = 0
        for a, b in zip(form, lemma):
            if a != b:
                break
            common += 1
        return common >= min(2, len(lemma))

    def _plausible(
        self, form: str, lemma: str, upos: str, count: int, share: float
    ) -> bool:
        if count < self.MIN_COUNT or share < self.MIN_SHARE:
            return False
        if form == lemma:
            return True
        # Un mot invariable n'a pas d'autre forme que lui-meme.
        if upos in INDECLINABLE:
            return False
        return self._shares_stem(form, lemma)


_form_table: FormLemmaTable | None = None


def get_form_table() -> FormLemmaTable:
    global _form_table
    if _form_table is None:
        _form_table = FormLemmaTable()
    return _form_table


def attested_readings(form: str) -> list[tuple[str, str, float]]:
    return get_form_table().readings(form)


def form_table_status() -> dict:
    table = get_form_table()
    return {
        "available": table.available,
        "forms": len(table.entries),
        "path": str(table.path),
        "source": "Treebanks Universal Dependencies latins — CC BY-NC-SA",
    }
