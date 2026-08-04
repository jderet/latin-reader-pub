"""Moteurs contextuels : Stanza, puis CLTK en repli.

Aucun des deux n'est importe au chargement du module : leurs dependances
sont lourdes et facultatives. L'import a lieu dans __init__, de sorte
qu'un moteur indisponible echoue proprement et laisse le registre passer
au suivant.
"""

from __future__ import annotations

import logging

from .base import (
    AnalysisResult,
    LemmaCandidate,
    TokenAnalysis,
    apply_preferences,
    filter_invented_lemmas,
)
from .lexicon_engine import LexiconLemmatizer
from .enclitics import split_enclitics
from .normalize import form_key, is_word
from .tokenizer import tokenize

log = logging.getLogger(__name__)


class StanzaLemmatizer:
    """Lemmatisation contextuelle par Stanza (paquet UD Latin).

    Stanza ne renvoie qu'un lemme par token ; les candidats concurrents
    proviennent du lexique embarque, ce qui permet a l'utilisateur
    d'arbitrer meme quand le modele est categorique.
    """

    name = "stanza"

    def __init__(self, package: str = "ittb", fallback: LexiconLemmatizer | None = None):
        import stanza  # import tardif : dependance facultative

        self.version = getattr(stanza, "__version__", "unknown")
        self.package = package
        self.fallback = fallback or LexiconLemmatizer()
        self.pipeline = stanza.Pipeline(
            lang="la",
            package=package,
            processors="tokenize,pos,lemma",
            download_method=None,
            logging_level="WARN",
        )

    def analyze(self, text: str) -> AnalysisResult:
        doc = self.pipeline(text)
        tokens: list[TokenAnalysis] = []
        idx = 0
        for s_i, sent in enumerate(doc.sentences):
            for word in sent.words:
                surface = word.text
                key = form_key(surface)
                start = getattr(word, "start_char", None)
                end = getattr(word, "end_char", None)
                if start is None:
                    start = text.find(surface, tokens[-1].char_end if tokens else 0)
                    end = start + len(surface)

                feats = _parse_ud_feats(word.feats)
                primary = LemmaCandidate(
                    lemma=word.lemma or key,
                    upos=word.upos or "X",
                    feats=feats,
                    score=0.90,
                )
                cands = [primary]
                for alt in self.fallback.candidates_for(key):
                    if alt.lemma.lower() != primary.lemma.lower():
                        alt.score *= 0.55
                        cands.append(alt)
                cands.sort(key=lambda c: -c.score)

                tokens.append(
                    TokenAnalysis(
                        index=idx,
                        surface=surface,
                        form_key=key,
                        char_start=start,
                        char_end=end,
                        sentence_index=s_i,
                        candidates=cands,
                        is_word=is_word(surface),
                    )
                )
                idx += 1
        tokens = split_enclitics(tokens)
        apply_preferences(tokens)
        filter_invented_lemmas(tokens)
        return AnalysisResult(self.name, f"{self.version}/{self.package}", tokens)


class CltkLemmatizer:
    """Repli CLTK : LatinBackoffLemmatizer.

    Attention : cette chaine n'est pas contextuelle. Elle ne distingue pas
    sum de edo sur « est » ; la preference lexicale de base.py s'en charge.
    """

    name = "cltk"

    def __init__(self, fallback: LexiconLemmatizer | None = None):
        from cltk.lemmatize.lat import LatinBackoffLemmatizer  # import tardif

        import cltk

        self.version = getattr(cltk, "__version__", "unknown")
        self.lemmatizer = LatinBackoffLemmatizer()
        self.fallback = fallback or LexiconLemmatizer()

    def analyze(self, text: str) -> AnalysisResult:
        raw_tokens = tokenize(text)
        words = [t.surface for t in raw_tokens]
        try:
            pairs = self.lemmatizer.lemmatize(words)
        except Exception:  # noqa: BLE001 - le repli doit toujours repondre
            log.exception("CLTK a echoue, repli sur le lexique")
            pairs = [(w, None) for w in words]

        tokens: list[TokenAnalysis] = []
        for i, (raw, (_, lemma)) in enumerate(zip(raw_tokens, pairs)):
            key = form_key(raw.surface)
            cands = self.fallback.candidates_for(key)
            if lemma:
                bare = lemma.lower()
                match = next((c for c in cands if c.lemma.lower() == bare), None)
                if match:
                    match.score = min(1.0, match.score + 0.25)
                else:
                    cands.insert(0, LemmaCandidate(lemma, "X", {}, 0.70))
                cands.sort(key=lambda c: -c.score)
            tokens.append(
                TokenAnalysis(
                    index=i,
                    surface=raw.surface,
                    form_key=key,
                    char_start=raw.start,
                    char_end=raw.end,
                    sentence_index=raw.sentence,
                    candidates=cands,
                    is_word=is_word(raw.surface),
                )
            )
        tokens = split_enclitics(tokens)
        apply_preferences(tokens)
        filter_invented_lemmas(tokens)
        return AnalysisResult(self.name, self.version, tokens)


def _parse_ud_feats(raw: str | None) -> dict[str, str]:
    if not raw or raw == "_":
        return {}
    out: dict[str, str] = {}
    for chunk in raw.split("|"):
        if "=" in chunk:
            k, v = chunk.split("=", 1)
            out[k] = v
    return out
