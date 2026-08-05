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


def _invariable_only(categories, invariables) -> bool:
    """Ce lemme n'est-il connu que sous des categories invariables ?

    Une categorie inconnue — ensemble vide — ne permet pas de conclure :
    la traiter comme invariable ecarterait tous les lemmes que le
    dictionnaire ne classe pas.
    """
    return bool(categories) and set(categories) <= invariables


def _dedupe(candidates: list) -> list:
    """Un meme lemme ne doit figurer qu'une fois dans la liste d'arbitrage.

    Les differentes categories d'un meme lemme (« cano » nom, verbe et nom
    propre) produiraient sinon trois entrees identiques a l'ecran. Deux
    analyses morphologiques distinctes du meme lemme (lingua nominatif et
    ablatif) restent en revanche deux candidats : le lemme est le meme,
    mais l'analyse affichee differe.
    """
    best: dict[tuple, LemmaCandidate] = {}
    for cand in candidates:
        key = (_bare(cand.lemma), tuple(sorted(cand.feats.items())) if cand.feats else ())
        current = best.get(key)
        if current is None or cand.score > current.score:
            best[key] = cand
    return sorted(best.values(), key=lambda c: -c.score)


def filter_invented_lemmas(tokens: Sequence[TokenAnalysis]) -> int:
    """Ecarte les candidats dont le lemme n'existe pas.

    Les moteurs qui devinent par terminaison produisent des lemmes
    inexistants (« abuteo », « quus »). On les confronte a une liste de
    reference ; un token dont tous les candidats sont ecartes conserve le
    meilleur, marque comme devine, pour ne jamais laisser un mot sans
    analyse.
    """
    from ..services.lemma_reference import filter_candidates, get_reference

    if not get_reference().available:
        return 0

    from ..services.lemma_reference import attested_readings, propose

    removed_total = 0
    for tok in tokens:
        if not tok.is_word or not tok.candidates:
            continue

        # 1. Autorite la plus forte : la forme est-elle attestee dans un
        #    corpus annote a la main ? Si oui, ces lectures priment.
        attested = attested_readings(tok.form_key)
        if attested:
            known = {lemma for lemma, _, _ in attested}
            merged = []
            for lemma, upos, score in attested:
                merged.append(LemmaCandidate(lemma=lemma, upos=upos, feats={}, score=score))
            # On conserve l'analyse morphologique du moteur quand il
            # s'accorde avec le corpus : elle est plus riche. La premiere
            # variante enrichit la lecture attestee ; les suivantes —
            # lingua nominatif puis ablatif — restent des candidats a part
            # entiere, legerement en retrait.
            for cand in tok.candidates:
                bare = _bare(cand.lemma)
                if bare in known:
                    enriched = False
                    for m in merged:
                        if _bare(m.lemma) == bare and cand.feats and not m.feats:
                            m.feats = cand.feats
                            enriched = True
                            break
                    if not enriched and cand.feats:
                        attested_score = max(
                            m.score for m in merged if _bare(m.lemma) == bare
                        )
                        merged.append(
                            LemmaCandidate(
                                cand.lemma, cand.upos, cand.feats,
                                min(cand.score, attested_score - 0.001),
                            )
                        )
                else:
                    # Le corpus ne connait pas cette lecture. On la garde si
                    # le lemme existe — un moteur contextuel peut avoir
                    # raison sur un usage rare, et l'utilisateur doit
                    # pouvoir arbitrer — mais nettement en retrait.
                    #
                    # Un mot invariable est ecarte sans discussion : « una »,
                    # adverbe, ne peut pas etre le lemme de « unam ».
                    from ..services.lemma_reference import (
                        INDECLINABLE,
                        get_reference,
                    )

                    reference = get_reference()
                    invariable = (
                        _bare(cand.lemma) != tok.form_key
                        and (
                            cand.upos in INDECLINABLE
                            or _invariable_only(
                                reference.entries.get(_bare(cand.lemma)),
                                INDECLINABLE,
                            )
                        )
                    )
                    if not invariable and reference.knows(cand.lemma):
                        merged.append(
                            LemmaCandidate(
                                cand.lemma, cand.upos, cand.feats,
                                min(cand.score, 0.45),
                            )
                        )
                    else:
                        removed_total += 1
            tok.candidates = _dedupe(merged)[:5]
            continue

        # 2. Forme inconnue des corpus : on ecarte les lemmes inexistants…
        kept, removed = filter_candidates(tok.candidates, keep_minimum=False)
        removed_total += removed

        # …et on complete par les lemmes attestes compatibles avec la forme.
        known = {_bare(c.lemma) for c in kept}
        extra = [
            LemmaCandidate(lemma=lemma, upos=upos, feats={}, score=score)
            for lemma, upos, score in propose(tok.form_key)
            if _bare(lemma) not in known
        ]

        if kept:
            # Le plafond ne vaut que si le moteur est sur de lui. Applique
            # sans condition, il bridait une proposition solide — la forme
            # elle-meme, attestee comme lemme — sous une simple devinette
            # par terminaison : « tres » cedait ainsi la place a « tris ».
            if kept[0].score >= 0.5:
                ceiling = kept[0].score * 0.95
                for cand in extra:
                    cand.score = min(cand.score, ceiling)
            tok.candidates = _dedupe(kept + extra)[:5]
        elif extra:
            tok.candidates = _dedupe(extra)[:5]
        else:
            # Rien d'atteste : on conserve le meilleur, signale comme
            # devine, pour ne jamais laisser un mot sans analyse.
            tok.candidates = tok.candidates[:1]
            tok.candidates[0].score = min(tok.candidates[0].score, 0.15)
    return removed_total
