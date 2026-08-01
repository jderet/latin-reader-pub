"""Normalisation orthographique du latin.

La forme affichee reste toujours celle du texte source. Ce module produit
la *cle de forme* utilisee pour l'indexation, la propagation des
desambiguisations et le rapprochement avec le dictionnaire.
"""

from __future__ import annotations

import re
import unicodedata

# Enclitiques que Stanza / CLTK detachent volontiers a tort.
ENCLITIC_EXCEPTIONS = {
    "que", "quisque", "quique", "quaeque", "quodque", "quemque", "cuiusque",
    "namque", "neque", "itaque", "denique", "undique", "utique", "plerumque",
    "atque", "quoque", "usque", "absque", "cumque", "quicumque", "quaecumque",
    "quocumque", "ubique", "peraeque", "susque", "ne", "bene", "paene",
    "sine", "pone", "omne", "iuvene", "mane", "vespere", "ve", "neve", "sive",
    "cave", "vae", "brevi", "suave", "grave", "nonne", "quisne",
}

_DIACRITICS = re.compile(r"[\u0300-\u036f]")
_WORD_RE = re.compile(r"[A-Za-z\u00c0-\u024f]+")


def strip_diacritics(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text)
    return unicodedata.normalize("NFC", _DIACRITICS.sub("", decomposed))


def form_key(surface: str, *, medieval: bool = False) -> str:
    """Cle de forme normalisee.

    - minuscules, NFC, macrons et diacritiques retires
    - v -> u, j -> i (graphie classique unifiee)
    - si medieval : ae/oe -> e, ti -> ci devant voyelle
    """
    key = strip_diacritics(surface).lower()
    key = key.replace("v", "u").replace("j", "i")
    if medieval:
        key = key.replace("ae", "e").replace("oe", "e")
        key = re.sub(r"ti(?=[aeiou])", "ci", key)
    return key


def lemma_key(lemma: str) -> str:
    """Cle de lemme : meme normalisation, sans variante medievale.

    Les lemmes portent parfois un indice d'homonymie (« edo1 », « edo#2 ») ;
    on le retire pour la recherche dictionnaire.
    """
    return form_key(re.sub(r"[#\d]+$", "", lemma))


def is_word(surface: str) -> bool:
    return bool(_WORD_RE.fullmatch(surface))


def split_enclitic(surface: str) -> tuple[str, str] | None:
    """Retourne (radical, enclitique) si la segmentation est plausible."""
    low = form_key(surface)
    if low in ENCLITIC_EXCEPTIONS or len(low) < 5:
        return None
    for enc in ("que", "ne", "ue"):
        if low.endswith(enc) and len(low) - len(enc) >= 3:
            return surface[: len(surface) - len(enc)], surface[len(surface) - len(enc) :]
    return None
