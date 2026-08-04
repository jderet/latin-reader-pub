#!/usr/bin/env python3
"""Construit une table (forme flechie -> lemme) attestee, depuis les
treebanks Universal Dependencies pour le latin.

Une liste de lemmes permet de verifier qu'un lemme existe, jamais
qu'une forme donnee s'y rattache. Les
treebanks apportent precisement ce chainon : pres de 900 000 tokens
annotes a la main, chacun donnant un couple forme/lemme verifie.

    python tools/build_form_lemma.py           # les 5 corpus (CC BY-NC-SA)
    python tools/build_form_lemma.py --free    # LLCT seul (CC BY-SA 4.0)

ATTENTION AUX LICENCES
    ITTB, PROIEL, Perseus et UDante sont en CC BY-NC-SA : la table qui en
    derive ne peut pas etre utilisee a des fins commerciales. LLCT seul
    est en CC BY-SA 4.0, sans cette restriction, mais sa couverture est
    bien moindre (environ 50 % contre 90 %).

Produit data/form_lemma.tsv.gz :
    forme <TAB> lemme:UPOS:frequence|lemme2:UPOS:frequence
"""

from __future__ import annotations

import gzip
import io
import sys
import tarfile
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from urllib.request import urlopen

TREEBANKS = {
    "UD_Latin-ITTB": "CC BY-NC-SA 3.0",
    "UD_Latin-PROIEL": "CC BY-NC-SA 3.0",
    "UD_Latin-Perseus": "CC BY-NC-SA 2.5",
    "UD_Latin-LLCT": "CC BY-SA 4.0",
    "UD_Latin-UDante": "CC BY-NC-SA 3.0",
}
FREE_ONLY = {"UD_Latin-LLCT"}

URL = "https://codeload.github.com/UniversalDependencies/{}/tar.gz/refs/heads/master"
OUTPUT = Path(__file__).resolve().parents[1] / "data" / "form_lemma.tsv.gz"

SKIP_POS = {"PUNCT", "SYM", "X", "NUM"}
MIN_FREQ = 1


def normalize(word: str) -> str:
    decomposed = unicodedata.normalize("NFD", word)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower().replace("v", "u").replace("j", "i")


def read_treebank(name: str) -> list[tuple[str, str, str]]:
    """Telecharge une archive et en extrait les triplets (forme, lemme, POS)."""
    print(f"  {name} …", end=" ", flush=True)
    with urlopen(URL.format(name)) as resp:
        payload = resp.read()

    triples: list[tuple[str, str, str]] = []
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.name.endswith(".conllu"):
                continue
            handle = archive.extractfile(member)
            if handle is None:
                continue
            for raw in io.TextIOWrapper(handle, encoding="utf-8", errors="replace"):
                if not raw.strip() or raw[0] == "#":
                    continue
                cols = raw.rstrip("\n").split("\t")
                # On ignore les lignes de tokens composes (« 1-2 ») et les
                # noeuds vides (« 3.1 ») : ce ne sont pas des mots reels.
                if len(cols) < 4 or "-" in cols[0] or "." in cols[0]:
                    continue
                form, lemma, upos = cols[1], cols[2], cols[3]
                if lemma in ("_", "") or upos in SKIP_POS:
                    continue
                triples.append((normalize(form), normalize(lemma), upos))
    print(f"{len(triples):,} tokens")
    return triples


def build(free_only: bool = False, output: Path = OUTPUT) -> dict:
    names = sorted(FREE_ONLY if free_only else TREEBANKS)
    print("téléchargement des treebanks :")

    counts: dict[str, Counter] = defaultdict(Counter)
    total = 0
    for name in names:
        for form, lemma, upos in read_treebank(name):
            counts[form][(lemma, upos)] += 1
            total += 1

    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(output, "wt", encoding="utf-8") as fh:
        fh.write("# Table forme -> lemme, attestée dans les treebanks UD latins\n")
        for name in names:
            fh.write(f"# {name} — {TREEBANKS[name]}\n")
        fh.write("# format : forme <TAB> lemme:UPOS:fréquence|…\n")
        for form in sorted(counts):
            entries = counts[form].most_common()
            payload = "|".join(f"{l}:{u}:{n}" for (l, u), n in entries if n >= MIN_FREQ)
            if payload:
                fh.write(f"{form}\t{payload}\n")

    ambiguous = sum(
        1 for form in counts if len({l for l, _ in counts[form]}) > 1
    )
    return {
        "tokens": total,
        "forms": len(counts),
        "ambiguous": ambiguous,
        "size_kb": output.stat().st_size // 1024,
        "licence": "CC BY-SA 4.0" if free_only else "CC BY-NC-SA (usage non commercial)",
    }


def main() -> None:
    free_only = "--free" in sys.argv
    stats = build(free_only)
    print(
        f"\n  {stats['tokens']:,} tokens, {stats['forms']:,} formes distinctes\n"
        f"  {stats['ambiguous']:,} formes rattachées à plusieurs lemmes\n"
        f"  {stats['size_kb']} Ko compressés — licence : {stats['licence']}"
    )
    print(f"  écrit dans {OUTPUT}")


if __name__ == "__main__":
    main()
