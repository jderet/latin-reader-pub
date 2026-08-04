"""Lecture des dictionnaires au format StarDict.

Un dictionnaire StarDict est un dossier contenant au minimum :

    nom.ifo    metadonnees (nom, nombre d'entrees, taille de l'index)
    nom.idx    index : suite de « mot \\0 offset size », en gros-boutiste
    nom.dict   corps des articles, parfois compresse en .dict.dz

Le .dict.dz est un « dictzip » : un gzip valide, donc lisible par le
module standard. On le decompresse une fois vers un fichier voisin mis
en cache, ce qui evite de tout garder en memoire a chaque demarrage.

Le fichier .syn (synonymes et formes flechies), s'il existe, est lu
aussi : c'est lui qui permet souvent de retrouver un article a partir
d'une forme declinee.
"""

from __future__ import annotations

import gzip
import logging
import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

from ..nlp.normalize import form_key

log = logging.getLogger(__name__)

# Types d'articles textuels selon la specification. Les autres (images,
# sons, ressources) sont ignores.
TEXT_TYPES = set("mlgtxykwhnr")

_TAGS = re.compile(r"<[^>]+>")
_ENTITIES = {
    "&lt;": "<", "&gt;": ">", "&amp;": "&", "&quot;": '"',
    "&apos;": "'", "&nbsp;": " ", "&#39;": "'",
}


@dataclass
class StarDictInfo:
    name: str
    word_count: int = 0
    idx_offset_bits: int = 32
    same_type_sequence: str = ""
    description: str = ""


@dataclass
class StarDict:
    """Un dictionnaire charge, interrogeable par mot."""

    info: StarDictInfo
    dict_path: Path
    index: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return self.info.name

    def lookup(self, word: str) -> list[str]:
        """Articles correspondant au mot, deja nettoyes du balisage."""
        entries = self.index.get(form_key(word))
        if not entries:
            return []
        out: list[str] = []
        with self.dict_path.open("rb") as fh:
            for offset, size in entries[:4]:
                fh.seek(offset)
                raw = fh.read(size)
                text = _decode_entry(raw, self.info.same_type_sequence)
                if text:
                    out.append(text)
        return out


class StarDictError(Exception):
    pass


# --------------------------------------------------------------------------
# Lecture des fichiers
# --------------------------------------------------------------------------
def _read_ifo(path: Path) -> StarDictInfo:
    info = StarDictInfo(name=path.stem)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if key == "bookname":
            info.name = value or info.name
        elif key == "wordcount":
            info.word_count = int(value or 0)
        elif key == "idxoffsetbits":
            info.idx_offset_bits = int(value or 32)
        elif key == "sametypesequence":
            info.same_type_sequence = value
        elif key == "description":
            info.description = value
    return info


def _read_bytes(path: Path) -> bytes:
    if path.suffix == ".gz":
        with gzip.open(path, "rb") as fh:
            return fh.read()
    return path.read_bytes()


def _decompress_dict(path: Path) -> Path:
    """Retourne un .dict lisible en acces direct.

    Un .dict.dz est decompresse une fois vers un fichier voisin ; les
    lancements suivants reutilisent ce cache.
    """
    if path.suffix != ".dz":
        return path
    cache = path.with_suffix("")  # retire « .dz »
    if cache.exists() and cache.stat().st_mtime >= path.stat().st_mtime:
        return cache
    log.info("décompression de %s (une seule fois)", path.name)
    with gzip.open(path, "rb") as src:
        cache.write_bytes(src.read())
    return cache


def _parse_index(data: bytes, offset_bits: int) -> dict[str, list[tuple[int, int]]]:
    index: dict[str, list[tuple[int, int]]] = {}
    step = 8 if offset_bits == 64 else 4
    fmt = ">Q" if offset_bits == 64 else ">I"
    pos, size = 0, len(data)

    while pos < size:
        end = data.find(b"\x00", pos)
        if end == -1:
            break
        word = data[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        if pos + step + 4 > size:
            break
        entry_offset = struct.unpack(fmt, data[pos : pos + step])[0]
        entry_size = struct.unpack(">I", data[pos + step : pos + step + 4])[0]
        pos += step + 4
        index.setdefault(form_key(word), []).append((entry_offset, entry_size))
    return index


def _parse_syn(data: bytes, index: dict[str, list[tuple[int, int]]],
               idx_words: list[str]) -> None:
    """Ajoute les synonymes et formes flechies au meme article."""
    pos, size = 0, len(data)
    while pos < size:
        end = data.find(b"\x00", pos)
        if end == -1:
            break
        word = data[pos:end].decode("utf-8", errors="replace")
        pos = end + 1
        if pos + 4 > size:
            break
        target = struct.unpack(">I", data[pos : pos + 4])[0]
        pos += 4
        if 0 <= target < len(idx_words):
            entries = index.get(idx_words[target])
            if entries:
                index.setdefault(form_key(word), []).extend(entries)


def _decode_entry(raw: bytes, same_type: str) -> str:
    """Extrait le texte d'un article, quel que soit son decoupage en types."""
    if same_type:
        # Tous les champs suivent la meme sequence de types : pas de
        # marqueur dans les donnees, sauf pour le dernier champ.
        parts: list[str] = []
        pos = 0
        for i, kind in enumerate(same_type):
            last = i == len(same_type) - 1
            if last:
                chunk, pos = raw[pos:], len(raw)
            else:
                end = raw.find(b"\x00", pos)
                if end == -1:
                    end = len(raw)
                chunk, pos = raw[pos:end], end + 1
            if kind in TEXT_TYPES:
                parts.append(chunk.decode("utf-8", errors="replace"))
        return clean(" ".join(p for p in parts if p.strip()))

    # Sinon chaque champ est precede d'un octet indiquant son type.
    parts = []
    pos = 0
    while pos < len(raw):
        kind = chr(raw[pos])
        pos += 1
        if kind.isupper():  # champ binaire prefixe de sa taille
            if pos + 4 > len(raw):
                break
            length = struct.unpack(">I", raw[pos : pos + 4])[0]
            pos += 4 + length
            continue
        end = raw.find(b"\x00", pos)
        if end == -1:
            end = len(raw)
        if kind in TEXT_TYPES:
            parts.append(raw[pos:end].decode("utf-8", errors="replace"))
        pos = end + 1
    return clean(" ".join(p for p in parts if p.strip()))


def clean(text: str) -> str:
    """Retire le balisage HTML ou Pango et normalise les espaces."""
    text = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = _TAGS.sub("", text)
    for entity, char in _ENTITIES.items():
        text = text.replace(entity, char)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# --------------------------------------------------------------------------
# Chargement
# --------------------------------------------------------------------------
def load_dictionary(folder: Path) -> StarDict:
    """Charge un dictionnaire depuis son dossier."""
    ifo = next(folder.glob("*.ifo"), None)
    if ifo is None:
        raise StarDictError(f"aucun fichier .ifo dans {folder}")

    idx = next(folder.glob("*.idx"), None) or next(folder.glob("*.idx.gz"), None)
    if idx is None:
        raise StarDictError(f"aucun fichier .idx dans {folder}")

    dict_file = (
        next(folder.glob("*.dict.dz"), None)
        or next(folder.glob("*.dict"), None)
    )
    if dict_file is None:
        raise StarDictError(f"aucun fichier .dict dans {folder}")

    info = _read_ifo(ifo)
    index = _parse_index(_read_bytes(idx), info.idx_offset_bits)

    syn = next(folder.glob("*.syn"), None)
    if syn is not None:
        try:
            idx_words = list(index.keys())
            _parse_syn(_read_bytes(syn), index, idx_words)
        except Exception:  # noqa: BLE001 - un .syn casse ne doit rien bloquer
            log.exception("fichier .syn illisible dans %s", folder)

    return StarDict(info=info, dict_path=_decompress_dict(dict_file), index=index)


def discover(root: Path) -> list[StarDict]:
    """Charge tous les dictionnaires presents sous `root`.

    Chaque sous-dossier contenant un .ifo est considere comme un
    dictionnaire. Un dictionnaire illisible est signale sans interrompre
    le chargement des autres.
    """
    found: list[StarDict] = []
    if not root.exists():
        return found

    candidates = [root] if any(root.glob("*.ifo")) else sorted(
        p for p in root.iterdir() if p.is_dir()
    )
    for folder in candidates:
        if not any(folder.glob("*.ifo")):
            continue
        try:
            dictionary = load_dictionary(folder)
        except Exception as exc:  # noqa: BLE001
            log.warning("dictionnaire ignoré (%s) : %s", folder.name, exc)
            continue
        log.info("dictionnaire chargé : %s (%d entrées)", dictionary.name, len(dictionary.index))
        found.append(dictionary)
    return found
