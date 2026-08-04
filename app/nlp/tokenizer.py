"""Segmentation minimale, utilisee par les moteurs qui n'en fournissent pas."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# La classe inclut les diacritiques combinants (U+0300-U+036F) : sans eux,
# un texte macronise en NFD (« i » + macron separe, ce que produit macOS)
# verrait chaque voyelle accentuee couper le mot en deux.
_LETTER = r"A-Za-z\u00c0-\u024f\u0300-\u036f"
# Les suites de chiffres forment un seul token : sans cela « 1234 »
# se decoupait en quatre, chacun affiche separement et incliquable.
_TOKEN_RE = re.compile(rf"[{_LETTER}]+|\d+|[^\s{_LETTER}\d]")
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
    # Recomposition NFC : sans elle, un macron stocke a part couperait
    # le mot. Sans effet sur un texte deja normalise a l'import.
    text = unicodedata.normalize("NFC", text)
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
