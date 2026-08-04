"""Base lexicale du Gaffiot 2016.

Numerisation de Gerard Greco et collaborateurs, sous licence
**CC BY-NC-SA** — usage non commercial. Voir data/SOURCES.md.

Elle apporte trois choses que les autres ressources ne donnaient pas :

- la **vedette** a afficher (« flūmĕn, ĭnis ») ; jusqu'ici l'application
  ne montrait que le lemme nu ;
- la **categorie** telle qu'un dictionnaire classique l'etablit, utile
  pour departager deux candidats ;
- la **liste de reference** des lemmes : elle dit ce qui existe, et sert
  a ecarter les inventions des moteurs de repli. Un mot qui n'y figure
  pas — nom propre, latin tardif — doit etre ajoute a la main par
  l'administrateur.

Regenerer : python tools/build_gaffiot.py chemin/gaffiot.csv
"""

from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass
from pathlib import Path

from ..nlp.normalize import lemma_key

log = logging.getLogger(__name__)

SOURCE = Path(__file__).resolve().parents[2] / "data" / "gaffiot.tsv.gz"

# Genres du Gaffiot, developpes pour l'affichage.
GENDERS = {"m": "masculin", "f": "féminin", "n": "neutre", "m f": "masculin ou féminin"}


@dataclass(slots=True)
class Entry:
    key: str
    headword: str
    upos: str
    gender: str
    gloss: str
    # « edo1 » manger et « edo2 » publier sont deux mots distincts.
    homonym_idx: int = 0

    @property
    def gender_label(self) -> str:
        return GENDERS.get(self.gender, self.gender)


class Gaffiot:
    """Entrees indexees par cle, puis par categorie."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or SOURCE
        # cle -> categorie -> indice d'homonymie -> entree
        self.entries: dict[str, dict[str, dict[int, Entry]]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            log.info("base Gaffiot absente (%s)", self.path)
            return
        with gzip.open(self.path, "rt", encoding="utf-8") as fh:
            for ligne in fh:
                if not ligne.strip() or ligne.startswith("#"):
                    continue
                parts = (ligne.rstrip("\n").split("\t") + [""] * 6)[:6]
                cle, vedette, upos, genre, glose, rang = parts
                if not cle:
                    continue
                indice = int(rang) if rang.isdigit() else 0
                self.entries.setdefault(cle, {}).setdefault(upos, {})[indice] = Entry(
                    cle, vedette, upos, genre, glose, indice
                )
        log.info("base Gaffiot : %d lemmes", len(self.entries))

    @property
    def available(self) -> bool:
        return bool(self.entries)

    def knows(self, lemma: str) -> bool:
        return lemma_key(lemma) in self.entries

    def get(
        self, lemma: str, upos: str | None = None, homonym_idx: int | None = None
    ) -> Entry | None:
        """Entree correspondante.

        La categorie departage d'abord, l'indice d'homonymie ensuite.
        Sans indice, on rend le premier homonyme : les moteurs ne le
        precisent jamais, c'est a l'administrateur d'arbitrer.
        """
        par_categorie = self.entries.get(lemma_key(lemma))
        if not par_categorie:
            return None

        if upos and upos in par_categorie:
            homonymes = par_categorie[upos]
        else:
            # On evite les entrees sans categorie, moins bien renseignees.
            classees = [h for cat, h in sorted(par_categorie.items()) if cat]
            homonymes = classees[0] if classees else next(iter(par_categorie.values()))

        if homonym_idx is not None and homonym_idx in homonymes:
            return homonymes[homonym_idx]
        # A defaut, celui qui porte une glose : c'est le mieux renseigne.
        avec_glose = [e for _i, e in sorted(homonymes.items()) if e.gloss]
        return avec_glose[0] if avec_glose else homonymes[min(homonymes)]

    def homonyms(self, lemma: str) -> list[Entry]:
        """Tous les homonymes d'une graphie, categories confondues."""
        par_categorie = self.entries.get(lemma_key(lemma)) or {}
        return [
            entree
            for _cat, homonymes in sorted(par_categorie.items())
            for _idx, entree in sorted(homonymes.items())
        ]

    def headword(self, lemma: str, upos: str | None = None) -> str:
        entree = self.get(lemma, upos)
        return entree.headword if entree else ""

    def gloss(self, lemma: str, upos: str | None = None) -> str:
        entree = self.get(lemma, upos)
        return entree.gloss if entree else ""


_gaffiot: Gaffiot | None = None


def get_gaffiot() -> Gaffiot:
    global _gaffiot
    if _gaffiot is None:
        _gaffiot = Gaffiot()
    return _gaffiot


def status() -> dict:
    base = get_gaffiot()
    return {
        "available": base.available,
        "entries": len(base.entries),
        "source": "Gaffiot 2016 (numérisation G. Gréco) — CC BY-NC-SA",
    }
