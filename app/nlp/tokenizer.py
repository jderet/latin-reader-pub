"""Segmentation minimale, utilisee par les moteurs qui n'en fournissent pas."""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"[A-Za-z\u00c0-\u024f]+|[^\sA-Za-z\u00c0-\u024f]")
_SENT_END = {".", "?", "!", ";", ":"}
# Abreviations courantes qui ne terminent pas une phrase.
_ABBREV = {"c", "l", "m", "p", "q", "t", "cn", "sex", "ti", "kal", "id", "non"}


@dataclass(slots=True)
class RawToken:
    surface: str
    start: int
    end: int
    sentence: int


def tokenize(text: str) -> list[RawToken]:
    tokens: list[RawToken] = []
    sentence = 0
    for m in _TOKEN_RE.finditer(text):
        surface = m.group(0)
        tokens.append(RawToken(surface, m.start(), m.end(), sentence))
        if surface in _SENT_END:
            prev = tokens[-2].surface.lower() if len(tokens) > 1 else ""
            if not (surface == "." and prev in _ABBREV):
                sentence += 1
    return tokens


def sentence_text(text: str, tokens: list[RawToken], sentence: int) -> str:
    members = [t for t in tokens if t.sentence == sentence]
    if not members:
        return ""
    return text[members[0].start : members[-1].end]
