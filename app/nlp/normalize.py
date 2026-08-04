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
_WORD_RE = re.compile(r"[A-Za-z\u00c0-\u024f\u0300-\u036f]*"
                      r"[A-Za-z\u00c0-\u024f]"
                      r"[A-Za-z\u00c0-\u024f\u0300-\u036f]*")


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
    # L'indice d'homonymie ne se retire que s'il reste des lettres :
    # sans cette precaution, « 1234 » se reduisait a la chaine vide.
    sans_indice = re.sub(r"[#\d]+$", "", lemma)
    return form_key(sans_indice if sans_indice.strip() else lemma)


def is_word(surface: str) -> bool:
    return bool(_WORD_RE.fullmatch(surface))


# « -cum » n'est pas une enclitique ordinaire : c'est la preposition cum
# postposee, et elle ne se rencontre qu'apres un petit nombre d'ablatifs
# de pronoms. Une liste fermee vaut mieux qu'une regle, qui decouperait
# « circum », « secundum » ou « dicum ».
CUM_STEMS = {
    "me", "te", "se", "nobis", "uobis",
    "quo", "qua", "quibus", "qui", "quocum",
}

ENCLITICS = ("que", "ne", "ue", "cum")


def split_enclitic(surface: str, *, known: "set[str] | None" = None
                   ) -> tuple[str, str] | None:
    """Retourne (radical, enclitique) si la segmentation est plausible.

    `known` permet de n'accepter la coupe que si le radical obtenu est une
    forme reellement attestee : c'est ce qui evite de couper « bene » en
    « be » + « ne » ou « sanguine » en « sangui » + « ne ».
    """
    low = form_key(surface)
    if low in ENCLITIC_EXCEPTIONS or len(low) < 4:
        return None

    for enc in ENCLITICS:
        if not low.endswith(enc):
            continue
        stem = low[: len(low) - len(enc)]
        if len(stem) < 2:
            continue

        if enc == "cum":
            if stem not in CUM_STEMS:
                continue
        elif known is not None and stem not in known:
            # Radical inconnu : on prefere ne pas couper.
            continue

        cut = len(surface) - len(enc)
        return surface[:cut], surface[cut:]
    return None
