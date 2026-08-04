"""Stockage des enregistrements associes aux textes.

Les fichiers sont ecrits dans DATA_DIR/audio et servis par la route
/audio. Le nom derive du contenu : reimporter le meme enregistrement ne
cree pas de doublon.

Le format est reconnu par les octets d'en-tete, jamais par l'extension :
un fichier renomme ne doit pas passer pour ce qu'il n'est pas.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ..db import DATA_DIR

AUDIO_DIR = DATA_DIR / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

MAX_BYTES = 50 * 1024 * 1024
EXTENSION = {"mp3": ".mp3", "m4a": ".m4a"}
MEDIA_TYPE = {".mp3": "audio/mpeg", ".m4a": "audio/mp4"}


class AudioError(ValueError):
    """Format non pris en charge, fichier vide ou trop volumineux."""


def _sniff(payload: bytes) -> str:
    """Identifie le format par ses octets d'en-tete."""
    if payload[:3] == b"ID3":
        return "mp3"
    # Trame MPEG : 11 bits a 1, puis version et couche.
    if len(payload) > 1 and payload[0] == 0xFF and (payload[1] & 0xE0) == 0xE0:
        return "mp3"
    # Conteneur MP4 : « ftyp » au quatrieme octet.
    if payload[4:8] == b"ftyp":
        marque = payload[8:12]
        if marque[:3] in (b"M4A", b"M4B", b"mp4", b"iso", b"isom", b"dash"[:3]):
            return "m4a"
        return "m4a"
    raise AudioError(
        "format non reconnu — seuls les fichiers MP3 et M4A sont acceptés"
    )


def save_bytes(payload: bytes) -> str:
    """Ecrit l'enregistrement et retourne son nom de fichier."""
    if not payload:
        raise AudioError("fichier vide")
    if len(payload) > MAX_BYTES:
        raise AudioError(
            f"fichier trop volumineux ({len(payload) // (1024 * 1024)} Mo, "
            f"maximum {MAX_BYTES // (1024 * 1024)} Mo)"
        )
    format_ = _sniff(payload)
    empreinte = hashlib.sha256(payload).hexdigest()[:32]
    nom = empreinte + EXTENSION[format_]
    chemin = AUDIO_DIR / nom
    if not chemin.exists():
        chemin.write_bytes(payload)
    return nom


def resolve(nom: str) -> Path | None:
    """Chemin sur disque, en refusant toute sortie du dossier audio."""
    if not nom or "/" in nom or "\\" in nom or nom.startswith("."):
        return None
    chemin = (AUDIO_DIR / nom).resolve()
    if not str(chemin).startswith(str(AUDIO_DIR.resolve())):
        return None
    return chemin if chemin.exists() else None


def media_type(nom: str) -> str:
    return MEDIA_TYPE.get(Path(nom).suffix, "application/octet-stream")


def delete_if_orphan(nom: str | None, still_used: bool) -> None:
    if not nom or still_used:
        return
    chemin = AUDIO_DIR / nom
    if chemin.exists() and chemin.is_file():
        chemin.unlink()
