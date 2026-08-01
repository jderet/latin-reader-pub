"""Stockage des images associees aux lemmes.

Les fichiers sont ecrits dans DATA_DIR/images et servis par la route
/media. On accepte deux entrees : un fichier televerse depuis le disque,
ou une image collee depuis le presse-papier (transmise en base64 par le
navigateur). Dans les deux cas on aboutit au meme fichier sur disque.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import re
from pathlib import Path

from ..db import DATA_DIR

IMAGE_DIR = DATA_DIR / "images"
IMAGE_DIR.mkdir(parents=True, exist_ok=True)

ALLOWED = {"png", "jpeg", "gif", "webp"}
EXTENSION = {"png": ".png", "jpeg": ".jpg", "gif": ".gif", "webp": ".webp"}
MAX_BYTES = 4 * 1024 * 1024

_DATA_URL = re.compile(r"^data:image/([a-zA-Z0-9.+-]+);base64,(.*)$", re.DOTALL)


class ImageError(ValueError):
    """Entree invalide : format non reconnu, vide, ou trop volumineuse."""


def _sniff(payload: bytes) -> str:
    """Identifie le format par ses octets d'en-tete.

    Ecrit a la main plutot qu'avec imghdr : ce module a ete supprime de
    la bibliotheque standard en Python 3.13.
    """
    if payload[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if payload[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if payload[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "webp"
    raise ImageError(
        "format d'image non reconnu ; utilisez PNG, JPEG, GIF ou WebP"
    )


def save_bytes(payload: bytes) -> str:
    """Ecrit l'image et retourne son nom de fichier.

    Le nom derive du contenu (empreinte SHA-256) : deux ajouts de la meme
    image ne creent qu'un seul fichier.
    """
    if not payload:
        raise ImageError("image vide")
    if len(payload) > MAX_BYTES:
        raise ImageError(
            f"image trop volumineuse ({len(payload) // 1024} Ko, maximum "
            f"{MAX_BYTES // 1024} Ko)"
        )
    kind = _sniff(payload)
    digest = hashlib.sha256(payload).hexdigest()[:32]
    name = digest + EXTENSION[kind]
    path = IMAGE_DIR / name
    if not path.exists():
        path.write_bytes(payload)
    return name


def save_data_url(data_url: str) -> str:
    """Image collee depuis le presse-papier, transmise en data: URL."""
    match = _DATA_URL.match(data_url.strip())
    if not match:
        raise ImageError("contenu collé non reconnu comme une image")
    try:
        payload = base64.b64decode(match.group(2), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageError("données d'image illisibles") from exc
    return save_bytes(payload)


def delete_if_orphan(name: str | None, still_used: bool) -> None:
    """Supprime le fichier si plus aucun lemme ne le reference."""
    if not name or still_used:
        return
    path = IMAGE_DIR / name
    if path.exists() and path.is_file():
        path.unlink()


def resolve(name: str) -> Path | None:
    """Chemin sur disque, en refusant toute sortie du dossier images."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = (IMAGE_DIR / name).resolve()
    if not str(path).startswith(str(IMAGE_DIR.resolve())):
        return None
    return path if path.exists() else None
