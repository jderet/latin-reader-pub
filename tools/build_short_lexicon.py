#!/usr/bin/env python3
"""Construit un lexique court latin -> francais, pour l'info-bulle de lecture.

Source : **Collatinus** (Yves Ouvrard, Philippe Verkerk), GNU GPL.
Fichiers `bin/data/lemmes.fr` et `lem_ext.fr` du depot
<https://github.com/biblissima/collatinus>, au format `lemme:traduction`.

Les traductions de Collatinus sont deja concises, mais donnent souvent
plusieurs sens et des precisions entre crochets. On n'en garde que les
premiers mots : l'info-bulle doit tenir sous un mot du texte sans gener
la lecture. La glose complete reste consultable dans le panneau lateral.

    python tools/build_short_lexicon.py

ATTENTION A LA LICENCE
    Les donnees de Collatinus sont sous GPL. Redistribuer le fichier
    produit impose de placer l'ensemble sous GPL. Supprimez
    data/short_gloss.tsv.gz si cela ne vous convient pas : l'application
    fonctionne sans, l'info-bulle affiche alors le lemme seul.
"""

from __future__ import annotations

import gzip
import io
import re
import tarfile
import unicodedata
from pathlib import Path
from urllib.request import urlopen

URL = "https://codeload.github.com/biblissima/collatinus/tar.gz/refs/heads/master"
FILES = ("bin/data/lemmes.fr", "bin/data/lem_ext.fr")
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "short_gloss.tsv.gz"

MAX_CHARS = 34
MAX_SENSES = 2

# Precisions entre crochets ou parentheses : utiles dans un dictionnaire,
# encombrantes dans une info-bulle.
_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)")
_SPACES = re.compile(r"\s+")
# Marques grammaticales sans interet pour une glose courte.
_NOISE = re.compile(
    r"^(?:indécl\.|adv\.|prép\.|conj\.|interj\.|pl\.|au pl\.|abs\.|impers\."
    r"|tr\.|intr\.|dép\.|passif|absolt|arch\.|poét\.)[,.]?\s*",
    re.I,
)


def normalize_key(word: str) -> str:
    """Meme cle que l'application : sans diacritiques, u=v, i=j, sans indice."""
    word = re.sub(r"[#\d]+$", "", word)
    decomposed = unicodedata.normalize("NFD", word)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower().replace("v", "u").replace("j", "i")


def shorten(gloss: str) -> str:
    """Reduit une definition a un ou deux sens, sous MAX_CHARS caracteres."""
    text = _BRACKETS.sub(" ", gloss)
    text = _SPACES.sub(" ", text).strip(" ,;:.")
    text = _NOISE.sub("", text)
    if not text:
        return ""

    senses = [s.strip() for s in re.split(r"[,;]", text) if s.strip()]
    if not senses:
        return ""

    out = senses[0]
    for extra in senses[1:MAX_SENSES]:
        candidate = f"{out}, {extra}"
        if len(candidate) <= MAX_CHARS:
            out = candidate
        else:
            break

    if len(out) > MAX_CHARS:
        # Coupe au dernier mot entier plutot qu'au milieu.
        cut = out[:MAX_CHARS].rsplit(" ", 1)[0]
        out = (cut or out[:MAX_CHARS]).rstrip(" ,;:") + "…"
    return out


def build(output: Path = OUTPUT) -> dict:
    print("téléchargement de Collatinus (environ 8 Mo) …")
    with urlopen(URL) as resp:
        payload = resp.read()

    entries: dict[str, str] = {}
    proper: dict[str, str] = {}
    skipped = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        members = {m.name: m for m in archive.getmembers()}
        # Ordre de priorite impose : lemmes.fr d'abord. Il est bien plus
        # soigne que lem_ext.fr, qui contient des noms propres et des
        # restes d'anglais.
        ordered = [
            members[name]
            for wanted in FILES
            for name in members
            if name.endswith(wanted)
        ]
        for member in ordered:
            handle = archive.extractfile(member)
            if handle is None:
                continue
            print(f"  lecture de {member.name}")
            for raw in io.TextIOWrapper(handle, encoding="utf-8", errors="replace"):
                line = raw.rstrip("\n")
                if not line or line.startswith("!") or ":" not in line:
                    continue
                lemma, _, gloss = line.partition(":")
                lemma = lemma.strip()
                key = normalize_key(lemma)
                short = shorten(gloss)
                if not key or not short:
                    skipped += 1
                    continue
                # Les noms propres cedent le pas aux noms communs de meme
                # graphie : « rex » doit donner « roi », non « Rex ».
                if lemma[:1].isupper():
                    proper.setdefault(key, short)
                else:
                    entries.setdefault(key, short)

    # Les noms propres ne servent que si aucun nom commun ne couvre la cle.
    for key, gloss in proper.items():
        entries.setdefault(key, gloss)

    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as fh:
        fh.write("# Lexique court latin → français\n")
        fh.write("# Source : Collatinus (Ouvrard & Verkerk), GNU GPL\n")
        fh.write("# https://github.com/biblissima/collatinus\n")
        for key in sorted(entries):
            fh.write(f"{key}\t{entries[key]}\n")

    lengths = [len(v) for v in entries.values()]
    return {
        "entries": len(entries),
        "skipped": skipped,
        "avg_len": sum(lengths) / len(lengths) if lengths else 0,
        "size_kb": output.stat().st_size // 1024,
    }


def main() -> None:
    stats = build()
    print(
        f"\n  {stats['entries']:,} entrées, {stats['skipped']:,} ignorées\n"
        f"  longueur moyenne : {stats['avg_len']:.0f} caractères\n"
        f"  {stats['size_kb']} Ko compressés"
    )
    print(f"  écrit dans {OUTPUT}")


if __name__ == "__main__":
    main()
