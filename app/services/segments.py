"""Decoupage d'un texte en segments, et alignement sur un enregistrement.

Un **segment** est un intervalle de caracteres du texte, eventuellement
associe a un intervalle de temps dans l'audio. C'est exactement la
structure des repliques de sous-titres : les deux mecanismes sont donc
unifies, et le surlignage pendant la lecture est le meme code.

Le decoupage vise la demi-phrase : ni le mot isole, qui multiplierait
les bornes a poser, ni la periode entiere, qui rendrait la surbrillance
inutile. On coupe sur la ponctuation forte et sur les virgules, puis on
recoud les fragments trop courts et on scinde les trop longs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Cibles empiriques, en mots. Un segment plus court que MIN est recolle
# au precedent ; plus long que MAX, il est coupe a la faveur d'un blanc.
MIN_WORDS = 3
MAX_WORDS = 12

# Abreviations qui se terminent par un point sans clore de proposition.
# Sans cette liste, « M. Tullius » ou « a. d. III kal. » seraient coupes
# apres chaque point, en autant de segments minuscules.
ABBREVIATIONS = {
    # Prenoms romains, ordinairement abreges.
    "a", "ap", "app", "c", "cn", "d", "f", "k", "l", "m", "mam", "n",
    "num", "oct", "opet", "p", "post", "pro", "q", "s", "ser", "sert",
    "sex", "sp", "st", "sta", "t", "ti", "tib", "v", "vol", "vop",
    # Calendrier.
    "kal", "kalend", "non", "id", "idib", "a.d", "ad", "pr", "prid",
    "ian", "feb", "febr", "mart", "apr", "mai", "iun", "iul", "aug",
    "sept", "sep", "oct", "nov", "dec",
    # Magistratures, formules, usages epigraphiques.
    "cos", "coss", "des", "praef", "proc", "leg", "trib", "pot", "imp",
    "aug", "caes", "divi", "fil", "pont", "max", "sen", "pop", "rom",
    "s.p.q.r", "spqr", "d.d", "h.s", "v.s.l.m", "b.m", "o.m",
    # Renvois savants, frequents dans les editions.
    "cf", "ibid", "id", "op", "cit", "loc", "sq", "sqq", "seq", "vol",
    "fasc", "ed", "edd", "trad", "not", "cap", "lib", "p", "pp", "fr",
    "etc", "e.g", "i.e", "ca", "circ", "vs", "n.b",
    # Titres d'oeuvres, tels que les editions les abregent.
    "ann", "hist", "epist", "ep", "orat", "or", "carm", "sat", "ecl",
    "georg", "aen", "met", "comm", "schol", "inscr", "tab", "col",
    "lin", "num", "sec", "saec", "art", "chap", "praef", "verg", "ov",
    "hor", "liv", "tac", "caes", "sall", "cic", "plin", "sen", "suet",
    "quint", "mart", "iuv", "lucr", "prop", "tib", "cat", "pers",
}

_STRONG = re.compile(r"[.!?;:]")
_COMMA = re.compile(r"[,]")
_WORD = re.compile(r"[A-Za-z\u00c0-\u024f\u0300-\u036f]+")


@dataclass(slots=True)
class Segment:
    char_start: int
    char_end: int
    start: float | None = None
    end: float | None = None

    @property
    def aligned(self) -> bool:
        return self.start is not None and self.end is not None

    def as_dict(self) -> dict:
        sortie = {"char_start": self.char_start, "char_end": self.char_end}
        if self.start is not None:
            sortie["start"] = round(self.start, 2)
        if self.end is not None:
            sortie["end"] = round(self.end, 2)
        return sortie

    @classmethod
    def from_dict(cls, donnees: dict) -> "Segment":
        return cls(
            char_start=int(donnees["char_start"]),
            char_end=int(donnees["char_end"]),
            start=(float(donnees["start"]) if donnees.get("start") is not None else None),
            end=(float(donnees["end"]) if donnees.get("end") is not None else None),
        )


def _count_words(texte: str) -> int:
    return len(_WORD.findall(texte))


def _is_abbreviation(texte: str, position: int) -> bool:
    """Le point en `position` clot-il une abreviation plutot qu'une phrase ?

    On regarde le mot qui precede, et l'on tient compte des abreviations
    a points multiples (« a. d. », « e. g. ») en remontant d'un cran.
    """
    debut = position
    while debut > 0 and (texte[debut - 1].isalnum() or texte[debut - 1] == "."):
        debut -= 1
    mot = texte[debut:position].lower().rstrip(".")
    if not mot:
        return False
    if mot in ABBREVIATIONS:
        return True
    # « a. d. » : le fragment courant peut n'etre qu'une lettre isolee
    # precedee d'une autre abreviation.
    return len(mot) == 1 and mot.isalpha()


def _cut_points(texte: str) -> list[int]:
    """Positions de coupe candidates, apres ponctuation."""
    points = set()
    for motif in (_STRONG, _COMMA):
        for trouve in motif.finditer(texte):
            signe = trouve.group(0)
            if signe == "." and _is_abbreviation(texte, trouve.start()):
                continue
            fin = trouve.end()
            # On avance jusqu'apres les blancs qui suivent le signe.
            while fin < len(texte) and texte[fin].isspace():
                fin += 1
            if 0 < fin < len(texte):
                points.add(fin)
    return sorted(points)


def _split_long(texte: str, debut: int, fin: int) -> list[tuple[int, int]]:
    """Scinde un fragment trop long au blanc le plus proche du milieu."""
    fragment = texte[debut:fin]
    if _count_words(fragment) <= MAX_WORDS:
        return [(debut, fin)]

    mots = list(_WORD.finditer(fragment))
    # On vise une coupe tous les MAX_WORDS mots, sans descendre sous MIN.
    morceaux: list[tuple[int, int]] = []
    curseur = debut
    for i in range(MAX_WORDS - 1, len(mots) - MIN_WORDS, MAX_WORDS):
        coupe = debut + mots[i].end()
        while coupe < fin and texte[coupe].isspace():
            coupe += 1
        if coupe > curseur:
            morceaux.append((curseur, coupe))
            curseur = coupe
    morceaux.append((curseur, fin))
    return morceaux


def split_text(texte: str) -> list[Segment]:
    """Decoupe le texte en segments d'environ une demi-phrase."""
    if not texte.strip():
        return []

    bornes = [0, *_cut_points(texte), len(texte)]
    bruts: list[tuple[int, int]] = []
    for debut, fin in zip(bornes, bornes[1:]):
        if fin > debut and texte[debut:fin].strip():
            bruts.append((debut, fin))
    if not bruts:
        bruts = [(0, len(texte))]

    # Recoud les fragments trop courts sur le precedent : « et », « sed »
    # isoles entre deux virgules ne meritent pas leur propre segment.
    recousus: list[list[int]] = []
    for debut, fin in bruts:
        if recousus and _count_words(texte[debut:fin]) < MIN_WORDS:
            recousus[-1][1] = fin
        else:
            recousus.append([debut, fin])

    # Le premier peut rester trop court si le texte commence par une
    # incise : on le fusionne alors avec le suivant.
    if len(recousus) > 1 and _count_words(texte[recousus[0][0] : recousus[0][1]]) < MIN_WORDS:
        recousus[1][0] = recousus[0][0]
        recousus.pop(0)

    segments: list[Segment] = []
    for debut, fin in recousus:
        for a, b in _split_long(texte, debut, fin):
            segments.append(Segment(char_start=a, char_end=b))
    return segments


# --------------------------------------------------------------------------
# Retouches
# --------------------------------------------------------------------------
def merge(segments: list[Segment], index: int) -> list[Segment]:
    """Fusionne un segment avec le suivant."""
    if not 0 <= index < len(segments) - 1:
        raise ValueError("aucun segment à fusionner à cette position")
    a, b = segments[index], segments[index + 1]
    fusion = Segment(
        char_start=a.char_start,
        char_end=b.char_end,
        start=a.start if a.start is not None else b.start,
        end=b.end if b.end is not None else a.end,
    )
    return segments[:index] + [fusion] + segments[index + 2 :]


def split_at(
    segments: list[Segment], index: int, position: int, texte: str
) -> list[Segment]:
    """Scinde un segment en deux a la position donnee dans le texte.

    Le temps est reparti au prorata du nombre de caracteres : approximatif,
    mais suffisant comme point de depart avant retouche.
    """
    if not 0 <= index < len(segments):
        raise ValueError("segment inconnu")
    cible = segments[index]
    if not cible.char_start < position < cible.char_end:
        raise ValueError("la coupure doit tomber à l'intérieur du segment")

    milieu = None
    if cible.aligned:
        part = (position - cible.char_start) / (cible.char_end - cible.char_start)
        milieu = cible.start + (cible.end - cible.start) * part

    gauche = Segment(cible.char_start, position, cible.start, milieu)
    droite = Segment(position, cible.char_end, milieu, cible.end)
    return segments[:index] + [gauche, droite] + segments[index + 1 :]


# --------------------------------------------------------------------------
# Alignement par frappe au rythme
# --------------------------------------------------------------------------
def apply_taps(
    segments: list[Segment], taps: list[float], duration: float | None = None
) -> list[Segment]:
    """Applique une suite d'instants relevés a la volee.

    Chaque frappe marque le **debut** d'un segment ; la fin est le debut
    du suivant. On aligne autant de segments qu'il y a de frappes : une
    seance interrompue laisse simplement le reste non aligne, sans rien
    perdre de ce qui precede.
    """
    instants = sorted(float(t) for t in taps)
    sortie = [Segment(s.char_start, s.char_end, s.start, s.end) for s in segments]

    for i, debut in enumerate(instants):
        if i >= len(sortie):
            break
        sortie[i].start = debut
        if i + 1 < len(instants):
            sortie[i].end = instants[i + 1]
        elif duration is not None:
            sortie[i].end = max(duration, debut + 0.5)
        else:
            sortie[i].end = debut + 3.0
    return sortie


def coverage(segments: list[Segment]) -> float:
    """Part des segments effectivement alignes."""
    if not segments:
        return 0.0
    return sum(1 for s in segments if s.aligned) / len(segments)


def normalize(segments: list[Segment]) -> list[Segment]:
    """Remet les bornes en ordre : pas de chevauchement ni d'inversion."""
    ordonnes = sorted(segments, key=lambda s: s.char_start)
    for precedent, suivant in zip(ordonnes, ordonnes[1:]):
        if precedent.aligned and suivant.aligned and precedent.end > suivant.start:
            precedent.end = suivant.start
    for segment in ordonnes:
        if segment.aligned and segment.end <= segment.start:
            segment.end = segment.start + 0.3
    return ordonnes
