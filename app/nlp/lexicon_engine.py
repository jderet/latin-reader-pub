"""Moteur de repli : regles de terminaison.

Il n'est pas contextuel. Il existe pour trois raisons :
1. l'application est utilisable des l'installation, sans telecharger
   le moindre modele ;
2. il sert de filet quand Stanza ou CLTK ne rendent aucun candidat ;
3. il rend les tests reproductibles et rapides.

Il proposait autrefois un petit lexique ecrit a la main, taille pour un
seul passage de Cesar. Ce lexique a ete retire : la table des formes
attestees (865 000 tokens des treebanks) et la liste de lemmes du Gaffiot
le remplacent avantageusement, et `filter_invented_lemmas` les consulte
apres coup. Ne subsistent ici que les regles de terminaison, qui ont
l'interet de porter l'analyse morphologique — la personne, le cas, le
temps — qu'aucune des deux autres ressources ne fournit.
"""

from __future__ import annotations

from .base import (
    AnalysisResult,
    LemmaCandidate,
    TokenAnalysis,
    apply_preferences,
    filter_invented_lemmas,
)
from .enclitics import split_enclitics
from .normalize import form_key, is_word
from .tokenizer import tokenize

# Regles de repli, appliquees quand la forme est inconnue du lexique.
# (suffixe, suffixe de remplacement pour le lemme, upos, feats, poids)
SUFFIX_RULES: list[tuple[str, str, str, str, float]] = [
    ("issent", "o", "VERB", "Tense=Pqp|Mood=Sub|Voice=Act|Number=Plur", 0.30),
    ("antur", "o", "VERB", "Tense=Pres|Mood=Ind|Voice=Pass|Number=Plur", 0.32),
    ("untur", "o", "VERB", "Tense=Pres|Mood=Ind|Voice=Pass|Number=Plur", 0.32),
    ("arum", "a", "NOUN", "Case=Gen|Number=Plur|Gender=Fem", 0.34),
    ("orum", "us", "NOUN", "Case=Gen|Number=Plur|Gender=Masc", 0.34),
    ("ibus", "is", "NOUN", "Case=Dat|Number=Plur", 0.30),
    ("imus", "o", "VERB", "Tense=Pres|Mood=Ind|Voice=Act|Number=Plur|Person=1", 0.30),
    ("istis", "o", "VERB", "Tense=Perf|Mood=Ind|Voice=Act|Number=Plur|Person=2", 0.28),
    ("erunt", "o", "VERB", "Tense=Perf|Mood=Ind|Voice=Act|Number=Plur|Person=3", 0.32),
    ("ntur", "o", "VERB", "Voice=Pass|Number=Plur", 0.28),
    ("mus", "o", "VERB", "Number=Plur|Person=1", 0.26),
    ("tis", "o", "VERB", "Number=Plur|Person=2", 0.24),
    ("nt", "o", "VERB", "Number=Plur|Person=3", 0.26),
    ("re", "o", "VERB", "VerbForm=Inf", 0.22),
    ("is", "is", "NOUN", "Case=Nom|Number=Sing", 0.20),
    ("es", "is", "NOUN", "Case=Nom|Number=Plur", 0.20),
    ("am", "a", "NOUN", "Case=Acc|Number=Sing|Gender=Fem", 0.24),
    ("um", "us", "NOUN", "Case=Acc|Number=Sing|Gender=Masc", 0.22),
    ("os", "us", "NOUN", "Case=Acc|Number=Plur|Gender=Masc", 0.24),
    ("as", "a", "NOUN", "Case=Acc|Number=Plur|Gender=Fem", 0.24),
    ("ae", "a", "NOUN", "Case=Gen|Number=Sing|Gender=Fem", 0.22),
    ("i", "us", "NOUN", "Case=Gen|Number=Sing|Gender=Masc", 0.18),
    ("o", "us", "NOUN", "Case=Abl|Number=Sing|Gender=Masc", 0.18),
    ("a", "a", "NOUN", "Case=Nom|Number=Sing|Gender=Fem", 0.16),
]


def parse_feats(raw: str) -> dict[str, str]:
    if not raw or raw == "_":
        return {}
    out: dict[str, str] = {}
    for chunk in raw.split("|"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            out[k.strip()] = v.strip()
    return out


class LexiconLemmatizer:
    """Moteur non contextuel, toujours disponible."""

    name = "lexicon"
    version = "1.0"

    def __init__(self) -> None:
        pass

    def candidates_for(self, key: str) -> list[LemmaCandidate]:
        """Devine par la terminaison.

        Les lemmes ainsi formes sont souvent faux ; c'est
        `filter_invented_lemmas` qui les confronte ensuite aux formes
        attestees et aux listes de reference. L'apport propre de ces
        regles est l'analyse morphologique qu'elles portent.
        """
        guesses: list[LemmaCandidate] = []
        for suffix, replacement, upos, feats, weight in SUFFIX_RULES:
            if key.endswith(suffix) and len(key) > len(suffix) + 1:
                stem = key[: -len(suffix)]
                guesses.append(
                    LemmaCandidate(stem + replacement, upos, parse_feats(feats), weight)
                )
            if len(guesses) >= 3:
                break
        if not guesses:
            guesses.append(LemmaCandidate(key, "X", {}, 0.10))
        return guesses

    def analyze(self, text: str) -> AnalysisResult:
        tokens: list[TokenAnalysis] = []
        for i, raw in enumerate(tokenize(text)):
            word = is_word(raw.surface)
            key = form_key(raw.surface)
            cands = self.candidates_for(key) if word else [
                LemmaCandidate(raw.surface, "PUNCT", {}, 1.0)
            ]
            tokens.append(
                TokenAnalysis(
                    index=i,
                    surface=raw.surface,
                    form_key=key,
                    char_start=raw.start,
                    char_end=raw.end,
                    sentence_index=raw.sentence,
                    candidates=cands,
                    is_word=word,
                )
            )
        tokens = split_enclitics(tokens)
        apply_preferences(tokens)
        filter_invented_lemmas(tokens)
        return AnalysisResult(self.name, self.version, tokens)
