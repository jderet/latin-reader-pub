"""Segmentation des enclitiques, commune a tous les moteurs.

Le latin soude a la fin d'un mot quelques particules : `-que` (et),
`-ne` (interrogatif), `-ue` (ou), et la preposition `cum` postposee
apres certains pronoms (`nobiscum` = cum nobis). Les separer donne deux
mots distincts a la lecture, chacun avec son lemme et son statut.

La difficulte n'est pas de couper, mais de ne pas couper a tort :
`atque`, `neque`, `bene`, `sanguine` finissent tous par une sequence qui
ressemble a une enclitique. On exige donc que le radical obtenu soit une
forme attestee, en plus de la liste d'exceptions.
"""

from __future__ import annotations

from .base import LemmaCandidate, TokenAnalysis
from .normalize import form_key, split_enclitic

# Lemmes des enclitiques, avec leur categorie.
ENCLITIC_LEMMAS = {
    "que": ("que", "CCONJ"),
    "ne": ("ne", "PART"),
    "ue": ("ue", "CCONJ"),
    "cum": ("cum", "ADP"),
}


def _is_attested_form(key: str) -> bool:
    """Ce mot figure-t-il tel quel dans un corpus annote ?"""
    from ..services.lemma_reference import get_form_table

    table = get_form_table()
    return table.available and key in table.entries


def _analyze_part(surface: str) -> list[LemmaCandidate]:
    """Candidats pour un fragment issu d'une coupe."""
    from ..services.lemma_reference import attested_readings, propose

    key = form_key(surface)
    readings = attested_readings(key) or propose(key)
    if readings:
        return [
            LemmaCandidate(lemma=lemma, upos=upos, feats={}, score=score)
            for lemma, upos, score in readings[:4]
        ]
    return [LemmaCandidate(lemma=key, upos="X", feats={}, score=0.15)]


def split_enclitics(tokens: list[TokenAnalysis]) -> list[TokenAnalysis]:
    """Decoupe les tokens portant une enclitique et renumerote l'ensemble.

    Retourne une nouvelle liste. Les tokens inchanges sont conserves tels
    quels ; seuls les mots coupes sont remplaces par deux tokens, le
    second portant `parent_token_index` vers le premier.
    """
    out: list[TokenAnalysis] = []

    for tok in tokens:
        if not tok.is_word or len(tok.surface) < 4:
            out.append(tok)
            continue

        # Regle la plus forte : si le mot entier est une forme attestee
        # dans les corpus, on ne le coupe pas. « nomine », « sanguine » et
        # « ordine » sont des ablatifs bien connus, non des radicaux
        # suivis de l'enclitique « -ne ». Les corpus, eux, ne contiennent
        # jamais « uirumque » : les annotateurs l'ont deja segmente.
        if not _needs_no_check(tok.surface) and _is_attested_form(tok.form_key):
            out.append(tok)
            continue

        known = None if _needs_no_check(tok.surface) else _attested_set()
        split = split_enclitic(tok.surface, known=known)
        if split is None:
            out.append(tok)
            continue

        stem_surface, enclitic_surface = split
        enclitic_key = form_key(enclitic_surface)
        lemma, upos = ENCLITIC_LEMMAS.get(enclitic_key, (enclitic_key, "PART"))

        stem = TokenAnalysis(
            index=tok.index,
            surface=stem_surface,
            form_key=form_key(stem_surface),
            char_start=tok.char_start,
            char_end=tok.char_start + len(stem_surface),
            sentence_index=tok.sentence_index,
            candidates=_analyze_part(stem_surface),
            is_word=True,
        )
        enclitic = TokenAnalysis(
            index=tok.index,
            surface=enclitic_surface,
            form_key=enclitic_key,
            char_start=tok.char_start + len(stem_surface),
            char_end=tok.char_end,
            sentence_index=tok.sentence_index,
            candidates=[LemmaCandidate(lemma=lemma, upos=upos, feats={}, score=0.95)],
            is_word=True,
        )
        out.append(stem)
        out.append(enclitic)

    # Renumerotation et rattachement de l'enclitique a son porteur.
    for position, tok in enumerate(out):
        tok.index = position
    for position, tok in enumerate(out):
        if position and tok.form_key in ENCLITIC_LEMMAS:
            previous = out[position - 1]
            if previous.char_end == tok.char_start:
                tok.parent_token_index = previous.index
    return out


def _needs_no_check(surface: str) -> bool:
    """« -cum » a sa propre liste fermee : la verification est superflue."""
    return form_key(surface).endswith("cum")


_attested_cache: set[str] | None = None


def _attested_set() -> set[str]:
    """Ensemble des formes connues, pour valider un radical.

    Charge une fois : c'est l'union des formes attestees dans les corpus
    et des lemmes de la reference.
    """
    global _attested_cache
    if _attested_cache is None:
        from ..services.lemma_reference import get_form_table, get_reference

        keys: set[str] = set()
        table = get_form_table()
        if table.available:
            keys |= set(table.entries)
        reference = get_reference()
        if reference.available:
            keys |= set(reference.entries)
        _attested_cache = keys
    return _attested_cache
