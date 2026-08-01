"""Interface commune a tous les moteurs de lemmatisation.

Toute la chaine applicative ne connait que `Lemmatizer`. Les moteurs
concrets (Stanza, CLTK, factice, ou un modele tiers) sont interchangeables
par configuration : cf. app/nlp/registry.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

# Preferences de desambiguisation appliquees a score egal ou quasi egal.
# Chaque entree : cle de forme -> lemme privilegie.
# « est » est massivement sum, marginalement edo : on tranche pour sum.
LEMMA_PREFERENCES: dict[str, str] = {
    "est": "sum",
    "es": "sum",
    "esse": "sum",
    "sunt": "sum",
    "estis": "sum",
    "esset": "sum",
    "essent": "sum",
    "edit": "edo",
    "quam": "qui",
    "quae": "qui",
    "qua": "qui",
    "cum": "cum",
}


def _bare(lemma: str) -> str:
    """Lemme depouille de son indice d'homonymie : « edo#2 » -> « edo »."""
    return lemma.lower().rstrip("0123456789#")


@dataclass(slots=True)
class LemmaCandidate:
    lemma: str
    upos: str
    feats: dict[str, str] = field(default_factory=dict)
    score: float = 0.0

    def as_dict(self) -> dict:
        return {
            "lemma": self.lemma,
            "upos": self.upos,
            "feats": self.feats,
            "score": round(self.score, 4),
        }


@dataclass(slots=True)
class TokenAnalysis:
    index: int
    surface: str
    form_key: str
    char_start: int
    char_end: int
    sentence_index: int
    candidates: list[LemmaCandidate]
    parent_token_index: int | None = None
    is_word: bool = True

    @property
    def ambiguity_margin(self) -> float:
        """Ecart entre le meilleur candidat et le meilleur candidat d'un
        *autre* lemme.

        Deux analyses morphologiques du meme lemme (lingua nominatif vs
        ablatif) ne constituent pas une ambiguite lemmatique : le statut
        porte sur le lemme, donc le choix est sans effet sur la lecture.
        Retourne 1.0 quand un seul lemme est en lice.
        """
        if not self.candidates:
            return 1.0
        head = _bare(self.candidates[0].lemma)
        for cand in self.candidates[1:]:
            if _bare(cand.lemma) != head:
                return max(0.0, self.candidates[0].score - cand.score)
        return 1.0

    def distinct_lemmas(self) -> list[str]:
        seen: list[str] = []
        for cand in self.candidates:
            bare = _bare(cand.lemma)
            if bare not in seen:
                seen.append(bare)
        return seen


@dataclass(slots=True)
class AnalysisResult:
    engine: str
    engine_version: str
    tokens: list[TokenAnalysis]


class Lemmatizer(Protocol):
    name: str
    version: str

    def analyze(self, text: str) -> AnalysisResult: ...


def apply_preferences(tokens: Sequence[TokenAnalysis], margin: float = 0.15) -> None:
    """Reordonne les candidats selon LEMMA_PREFERENCES.

    N'intervient que dans la zone d'ambiguite : si le moteur est net
    (ecart > margin), sa decision contextuelle est respectee. Sinon la
    preference lexicale l'emporte, ce qui evite de voir « est » bascule
    vers edo au milieu d'une phrase copulative.
    """
    for tok in tokens:
        pref = LEMMA_PREFERENCES.get(tok.form_key)
        if not pref or len(tok.candidates) < 2:
            continue
        if tok.ambiguity_margin > margin:
            continue
        for i, cand in enumerate(tok.candidates):
            if cand.lemma.lower().rstrip("0123456789#") == pref:
                if i:
                    tok.candidates.insert(0, tok.candidates.pop(i))
                    tok.candidates[0].score = max(
                        tok.candidates[0].score, tok.candidates[1].score + 0.001
                    )
                break
