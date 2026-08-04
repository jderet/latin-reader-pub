#!/usr/bin/env python3
"""Construit la base lexicale a partir du Gaffiot 2016.

Source : le Gaffiot numerise par Gerard Greco et collaborateurs, sous
licence **CC BY-NC-SA** — usage non commercial.

Le fichier fourni contient, pour 72 000 lemmes : la categorie, le genre,
la graphie avec quantites, la vedette complete (« flūmĕn, ĭnis ») et une
traduction francaise. On en tire quatre choses :

1. une **liste de lemmes** du latin classique, plus resserree que le LiLa
   Lemma Bank et donc plus sure pour departager des candidats ;
2. les **vedettes** a afficher, que l'application n'avait pas ;
3. la graphie **avec macrons**, pour la lecture ;
4. une **glose francaise**, en complement du lexique Collatinus.

    python tools/build_gaffiot.py chemin/gaffiot.csv

Produit data/gaffiot.tsv.gz :
    cle <TAB> vedette <TAB> upos <TAB> genre <TAB> glose
"""

from __future__ import annotations

import csv
import gzip
import re
import sys
import unicodedata
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / "data" / "gaffiot.tsv.gz"

# Categories du Gaffiot vers les etiquettes universelles utilisees partout
# ailleurs dans l'application.
POS = {
    "n": "NOUN",
    "adj": "ADJ",
    "v": "VERB",
    "adv": "ADV",
    "prép": "ADP",
    "conj": "CCONJ",
    "interj": "INTJ",
}

# Aucune entree n'est ecartee : le fichier extrait du Gaffiot fait foi,
# et la table des lemmes doit le refleter exactement. Une premiere version
# laissait de cote les notices peu sures et sans traduction : elle perdait
# ainsi 354 mots, dont « accedo », des plus courants. La confiance sert
# desormais a departager les homonymes, non a exclure.

_ETYM = re.compile(r"^\s*\([^)]{0,40}\)[,;:]?\s*")      # « (fluo), masse d'eau… »
# « I porter », ou « I » seul quand la notice se reduit a une numerotation.
_SENSE_NUM = re.compile(r"^\s*(?:[IVX]+|\d+)[°.)]?(?:\s+|$)")
_BRACKETS = re.compile(r"\[[^\]]*\]")
# Marques grammaticales et fragments de conjugaison en tete de notice :
# « āre (incola), tr., habiter » doit se reduire a « habiter ».
_GRAM = re.compile(
    r"^\s*(?:tr\.|intr\.|abs\.|absol\.|impers\.|dep\.|dép\.|passif|poet\.|poét\.|arch\.|n\.|m\.|f\.|adj\.|adv\.|indécl\.)[,;:]?\s*",
    re.I,
)
# Formes principales citees en tete de notice : « āre », « ŭī, ĕre ».
# Le critere est la presence d'une quantite : aucun mot francais n'en
# porte, si bien qu'une glose ne peut pas etre prise pour une desinence.
# Sans cette precaution, « roi, souverain » perdait son premier mot.
_INFLECTION = re.compile(
    r"^\s*(?=[a-z]*[āēīōūȳăĕĭŏŭ])[a-zāēīōūȳăĕĭŏŭæœ]{1,7}[,.]?(?=\s|$)", re.I
)
_SPACES = re.compile(r"\s+")
_TRAILING_INDEX = re.compile(r"[#\d]+$")

MAX_GLOSS = 60


def normalize_key(mot: str) -> str:
    """Meme cle que l'application : minuscules, sans quantites, u=v, i=j."""
    sans_indice = _TRAILING_INDEX.sub("", mot)
    mot = sans_indice if sans_indice.strip() else mot
    decompose = unicodedata.normalize("NFD", mot)
    depouille = "".join(c for c in decompose if not unicodedata.combining(c))
    return depouille.lower().replace("v", "u").replace("j", "i")


def clean_gloss(texte: str) -> str:
    """Reduit une notice a une glose lisible.

    Les notices du Gaffiot commencent souvent par l'etymologie entre
    parentheses ou par un numero de sens : ni l'une ni l'autre ne sert
    dans une info-bulle.
    """
    texte = _BRACKETS.sub(" ", texte or "")
    # On epluche l'en-tete de notice couche par couche : etymologie,
    # numero de sens, marques grammaticales, restes de conjugaison.
    for _ in range(4):
        avant = texte
        texte = _ETYM.sub("", texte)
        texte = _SENSE_NUM.sub("", texte)
        texte = _GRAM.sub("", texte)
        # Une forme principale n'est retiree que s'il reste du texte
        # derriere : sinon la notice se viderait entierement.
        candidat = _INFLECTION.sub("", texte, count=1)
        if candidat.strip(" ,;:."):
            texte = candidat
        if texte == avant:
            break
    texte = _SPACES.sub(" ", texte).strip(" ,;:.")
    if not texte:
        return ""
    if len(texte) > MAX_GLOSS:
        coupe = texte[:MAX_GLOSS].rsplit(" ", 1)[0]
        texte = (coupe or texte[:MAX_GLOSS]).rstrip(" ,;:") + "…"
    return texte


def clean_headword(texte: str, secours: str) -> str:
    """Vedette d'affichage : « flūmĕn, ĭnis »."""
    texte = _SPACES.sub(" ", (texte or "").replace("\n", " ")).strip(" ,;")
    return texte or secours


def build(source: Path, output: Path = OUTPUT) -> dict:
    print(f"lecture de {source} …")
    entrees: dict[tuple[str, str], dict] = {}
    lus = ignores = 0

    with source.open(encoding="utf-8", newline="") as fh:
        for ligne in csv.DictReader(fh):
            lus += 1
            lemme = (ligne.get("lemme") or "").strip()
            if not lemme:
                continue
            confiance = (ligne.get("confiance") or "").strip()
            nature = (ligne.get("nature") or "").strip()
            traduction = (ligne.get("traduction") or "").strip()

            cle = normalize_key(lemme)
            if not cle:
                ignores += 1
                continue
            # « edo1 » et « edo2 » sont deux mots : manger et publier. On
            # conserve l'indice, qui devient la marque de l'homonyme.
            indice = re.search(r"(\d+)$", lemme)
            rang = int(indice.group(1)) if indice else 0

            # Les entrees sans categorie sont surtout des renvois et des
            # formes flechies : elles attestent l'existence du mot, mais
            # ne doivent fournir ni vedette ni glose.
            upos = POS.get(nature, "")
            candidat = {
                "headword": clean_headword(
                    ligne.get("lemme_avec_infos", ""),
                    (ligne.get("lemme_macrons") or lemme).strip(),
                ),
                "upos": upos,
                "gender": (ligne.get("genre") or "").strip(),
                "gloss": clean_gloss(traduction),
                "rank": (0 if confiance == "ok" else 1, 0 if traduction else 1),
            }

            # Une entree par couple (mot, categorie) : « edo » verbe et
            # « edo » nom sont deux mots. Les homonymes de meme categorie
            # partagent leur vedette — « cælum, ī » vaut pour le burin
            # comme pour le ciel — seule la glose differe, et l'on garde
            # alors la notice la mieux etablie.
            reference = (cle, upos, rang)
            ancien = entrees.get(reference)
            if ancien is None or candidat["rank"] < ancien["rank"]:
                if ancien:
                    candidat["senses"] = ancien["senses"]
                else:
                    candidat["senses"] = []
                entrees[reference] = candidat
                ancien = candidat
            # Les homonymes de meme categorie gardent leurs sens cote a
            # cote : « cælum » est le burin ET le ciel, et rien ne permet
            # de choisir automatiquement lequel presenter.
            if candidat["gloss"] and candidat["gloss"] not in ancien["senses"]:
                ancien["senses"].append(candidat["gloss"])

    # Une entree classee sans glose emprunte celle de l'entree sans
    # categorie : chez incolo, c'est elle qui porte « habiter ».
    for (cle, upos, rang), entree in entrees.items():
        if upos and not entree["senses"]:
            secours = next(
                (
                    e
                    for (c, u, _r), e in entrees.items()
                    if c == cle and not u and e["senses"]
                ),
                None,
            )
            if secours:
                entree["senses"] = list(secours["senses"])

    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as fh:
        fh.write("# Base lexicale — Gaffiot 2016 (numérisation Gérard Gréco)\n")
        fh.write("# Licence : CC BY-SA-NC — usage non commercial\n")
        fh.write(
            "# format : clé <TAB> vedette <TAB> upos <TAB> genre <TAB> glose "
            "<TAB> indice d'homonymie\n"
        )
        for cle, upos, rang in sorted(entrees):
            e = entrees[(cle, upos, rang)]
            glose = " ; ".join(e["senses"][:3])
            fh.write(
                f"{cle}\t{e['headword']}\t{upos}\t{e['gender']}\t{glose}\t{rang}\n"
            )

    avec_glose = sum(1 for e in entrees.values() if e["senses"])
    return {
        "read": lus,
        "skipped": ignores,
        "keys": len({cle for cle, _u, _r in entrees}),
        "entries": len(entrees),
        "glossed": avec_glose,
        "size_kb": output.stat().st_size // 1024,
    }


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit("indiquez le chemin du fichier CSV du Gaffiot")
    stats = build(Path(sys.argv[1]))
    print(
        f"  {stats['read']:,} lignes lues, {stats['skipped']:,} écartées\n"
        f"  -> {stats['keys']:,} lemmes, {stats['entries']:,} entrées "f"(catégories distinctes), dont {stats['glossed']:,} avec glose\n"
        f"  {stats['size_kb']} Ko compressés"
    )
    print(f"  écrit dans {OUTPUT}")


if __name__ == "__main__":
    main()
