"""Import d'une video sous-titree en latin.

On ne telecharge pas la video : elle reste sur YouTube, incrustee dans la
page par le lecteur officiel. Seuls les sous-titres sont recuperes, puis
traites exactement comme un texte colle — meme lemmatisation, memes
statuts, memes fiches.

Chaque replique conserve ses bornes temporelles et l'intervalle de
caracteres qu'elle occupe dans le texte reconstitue. C'est ce qui permet
de suivre la lecture pendant la lecture de la video, et de sauter a un
passage en cliquant sur une phrase.

La recuperation elle-meme est isolee derriere `fetch_transcript`, pour
que le reste du module soit testable sans acces au reseau.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Iterable

log = logging.getLogger(__name__)

# Formats d'adresse acceptes : page normale, lien court, integration,
# direct, et « shorts ».
_PATTERNS = (
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/watch\?(?:.*&)?v=([\w-]{11})"),
    re.compile(r"youtu\.be/([\w-]{11})"),
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/embed/([\w-]{11})"),
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/live/([\w-]{11})"),
    re.compile(r"(?:youtube\.com|youtube-nocookie\.com)/shorts/([\w-]{11})"),
    re.compile(r"^([\w-]{11})$"),
)

# Langues de sous-titres a essayer, dans l'ordre. « la » est la norme ;
# certaines chaines etiquettent le latin autrement.
LANGUAGE_ORDER = ("la", "la-x-classical", "lat")

# Mentions techniques frequentes dans les sous-titres, sans interet ici.
_NOISE = re.compile(r"^\s*[\[(][^\])]*[\])]\s*$")
_TAGS = re.compile(r"</?[^>]+>")


class TranscriptError(Exception):
    """Sous-titres indisponibles, absents en latin, ou video inaccessible."""


@dataclass(slots=True)
class Cue:
    """Une replique : son texte et sa place, dans le temps et dans le texte."""

    start: float
    end: float
    char_start: int
    char_end: int

    def as_dict(self) -> dict:
        return {
            "start": round(self.start, 2),
            "end": round(self.end, 2),
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass(slots=True)
class VideoDocument:
    video_id: str
    language: str
    content: str
    cues: list[Cue]

    @property
    def duration(self) -> float:
        return self.cues[-1].end if self.cues else 0.0


def parse_video_id(url: str) -> str:
    """Identifiant de la video, quelle que soit la forme de l'adresse."""
    url = (url or "").strip()
    for motif in _PATTERNS:
        trouve = motif.search(url)
        if trouve:
            return trouve.group(1)
    raise TranscriptError(
        "adresse YouTube non reconnue — collez le lien de la page de la vidéo"
    )


def clean_line(texte: str) -> str:
    """Retire le balisage et les mentions entre crochets."""
    texte = _TAGS.sub("", texte)
    texte = texte.replace("\u00a0", " ")
    texte = re.sub(r"\s+", " ", texte).strip()
    return "" if _NOISE.match(texte) else texte


def build_document(
    video_id: str, entries: Iterable[dict], language: str = "la"
) -> VideoDocument:
    """Assemble les repliques en un texte continu, bornes comprises.

    Les repliques sont separees par un retour a la ligne : le decoupage en
    phrases s'en trouve plus juste qu'avec des espaces, les sous-titres
    etant rarement ponctues.
    """
    morceaux: list[str] = []
    cues: list[Cue] = []
    position = 0

    for entree in entries:
        texte = clean_line(str(entree.get("text", "")))
        if not texte:
            continue
        debut = float(entree.get("start", 0.0))
        duree = float(entree.get("duration", 0.0) or 0.0)

        morceaux.append(texte)
        cues.append(
            Cue(
                start=debut,
                end=debut + duree,
                char_start=position,
                char_end=position + len(texte),
            )
        )
        position += len(texte) + 1  # le séparateur compte

    if not cues:
        raise TranscriptError("les sous-titres ne contiennent aucun texte exploitable")

    # Une réplique sans durée déclarée s'étend jusqu'à la suivante.
    for precedente, suivante in zip(cues, cues[1:]):
        if precedente.end <= precedente.start:
            precedente.end = suivante.start
    if cues[-1].end <= cues[-1].start:
        cues[-1].end = cues[-1].start + 3.0

    return VideoDocument(
        video_id=video_id,
        language=language,
        content="\n".join(morceaux),
        cues=cues,
    )


def fetch_transcript(video_id: str) -> tuple[list[dict], str]:
    """Recupere les sous-titres latins d'une video.

    Retourne les repliques brutes et le code de langue retenu. Les
    sous-titres generes automatiquement sont acceptes en dernier recours :
    sur du latin, leur qualite est mediocre, mais mieux vaut cela que rien.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
    except ModuleNotFoundError as exc:  # pragma: no cover - dépendance absente
        raise TranscriptError(
            "la bibliothèque youtube-transcript-api n'est pas installée "
            "(.venv/bin/pip install youtube-transcript-api)"
        ) from exc

    try:
        api = YouTubeTranscriptApi()
        listing = api.list(video_id)
    except Exception as exc:  # noqa: BLE001 - l'API leve des erreurs variees
        raise TranscriptError(f"sous-titres inaccessibles : {exc}") from exc

    # D'abord les sous-titres rédigés, ensuite seulement l'automatique.
    for chercher in ("find_manually_created_transcript", "find_generated_transcript"):
        methode = getattr(listing, chercher, None)
        if methode is None:
            continue
        try:
            piste = methode(list(LANGUAGE_ORDER))
        except Exception:  # noqa: BLE001 - langue absente de cette catégorie
            continue
        try:
            donnees = piste.fetch()
        except Exception as exc:  # noqa: BLE001
            raise TranscriptError(f"téléchargement des sous-titres impossible : {exc}") from exc
        return _normalize(donnees), getattr(piste, "language_code", "la")

    disponibles = _available_languages(listing)
    raise TranscriptError(
        "cette vidéo n'a pas de sous-titres en latin"
        + (f" (disponibles : {disponibles})" if disponibles else "")
    )


def _normalize(donnees) -> list[dict]:
    """Uniformise la sortie, l'API ayant change de format entre versions."""
    sortie = []
    for element in donnees:
        if isinstance(element, dict):
            sortie.append(element)
        else:  # objets FetchedTranscriptSnippet des versions récentes
            sortie.append(
                {
                    "text": getattr(element, "text", ""),
                    "start": getattr(element, "start", 0.0),
                    "duration": getattr(element, "duration", 0.0),
                }
            )
    return sortie


def _available_languages(listing) -> str:
    try:
        return ", ".join(sorted({t.language_code for t in listing}))
    except Exception:  # noqa: BLE001
        return ""


def import_video(url: str) -> VideoDocument:
    """Chaine complete : adresse -> document pret a lemmatiser."""
    video_id = parse_video_id(url)
    entries, language = fetch_transcript(video_id)
    return build_document(video_id, entries, language)
