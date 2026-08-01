"""Moteur de repli : lexique embarque + regles de terminaison.

Il n'est pas contextuel. Il existe pour trois raisons :
1. l'application est utilisable des l'installation, sans telecharger
   le moindre modele ;
2. il sert de filet quand Stanza ou CLTK ne rendent aucun candidat ;
3. il rend les tests reproductibles et rapides.

Format du lexique (data/lexicon.tsv, sans en-tete) :
    forme_normalisee <TAB> lemme <TAB> upos <TAB> feats <TAB> poids
`feats` suit la convention UD : Case=Nom|Number=Sing
Plusieurs lignes par forme = plusieurs candidats.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .base import AnalysisResult, LemmaCandidate, TokenAnalysis, apply_preferences
from .normalize import form_key, is_word
from .tokenizer import tokenize

DEFAULT_LEXICON = Path(__file__).resolve().parents[2] / "data" / "lexicon.tsv"

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


def load_lexicon(path: Path | None = None) -> dict[str, list[LemmaCandidate]]:
    path = path or DEFAULT_LEXICON
    table: dict[str, list[LemmaCandidate]] = defaultdict(list)
    if not path.exists():
        return table
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        key, lemma, upos, feats = parts[0], parts[1], parts[2], parts[3]
        weight = float(parts[4]) if len(parts) > 4 else 1.0
        table[form_key(key)].append(
            LemmaCandidate(lemma=lemma, upos=upos, feats=parse_feats(feats), score=weight)
        )
    for cands in table.values():
        cands.sort(key=lambda c: -c.score)
    return dict(table)


class LexiconLemmatizer:
    """Moteur non contextuel, toujours disponible."""

    name = "lexicon"
    version = "1.0"

    def __init__(self, path: Path | None = None) -> None:
        self.table = load_lexicon(path)

    def candidates_for(self, key: str) -> list[LemmaCandidate]:
        if key in self.table:
            return [
                LemmaCandidate(c.lemma, c.upos, dict(c.feats), c.score)
                for c in self.table[key]
            ]
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
        apply_preferences(tokens)
        return AnalysisResult(self.name, self.version, tokens)
