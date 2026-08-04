"""Acces aux ressources lexicographiques.

Le panneau de lecture interroge, dans cet ordre :

- le **Gaffiot** : vedette, genre et glose francaise ;
- les dictionnaires **StarDict** que l'utilisateur depose lui-meme dans
  data/dictionaries ;
- le lexique **Collatinus**, dictionnaire de lecture aux notices
  breves, en complement du Gaffiot.

Un petit fichier TSV ecrit a la main a longtemps servi d'amorce ; il a
ete retire, ces trois sources le couvrant entierement.

`LlmSuggester` reste un appel facultatif a l'API Anthropic pour proposer
une glose. Elle n'est jamais enregistree automatiquement : l'utilisateur
valide.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..nlp.normalize import lemma_key
from . import stardict

log = logging.getLogger(__name__)

# Chaque sous-dossier contenant un .ifo est charge automatiquement.
STARDICT_DIR = Path(__file__).resolve().parents[2] / "data" / "dictionaries"


@dataclass(slots=True)
class DictEntry:
    lemma_key: str
    headword: str
    body: str
    source: str


# --------------------------------------------------------------------------
# Suggestion par LLM, facultative
# --------------------------------------------------------------------------
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"

PROMPT = (
    "Tu es lexicographe latiniste. Donne la traduction francaise du lemme "
    "latin fourni, telle qu'elle figurerait dans un dictionnaire : deux a "
    "six mots, sans phrase, sans commentaire, sans guillemets. Tiens compte "
    "de la phrase de contexte pour choisir le sens pertinent."
)


def llm_available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def suggest_gloss(lemma: str, context: str = "", *, timeout: float = 20.0) -> str | None:
    """Retourne une proposition de glose, ou None si indisponible.

    Note de confidentialite : le lemme et la phrase de contexte sont
    transmis a un tiers. La fonction est inactive sans cle d'API.
    """
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return None

    payload = {
        "model": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
        "max_tokens": 100,
        "system": PROMPT,
        "messages": [
            {
                "role": "user",
                "content": f"Lemme : {lemma}\nContexte : {context or '(aucun)'}",
            }
        ],
    }
    req = urllib.request.Request(
        ANTHROPIC_URL,
        data=json.dumps(payload).encode(),
        headers={
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = " ".join(parts).strip()
        return text or None
    except Exception:  # noqa: BLE001
        log.exception("suggestion de glose indisponible")
        return None


# --------------------------------------------------------------------------
# Dictionnaires StarDict de l'utilisateur
# --------------------------------------------------------------------------
_stardicts: list | None = None


def get_stardicts(reload: bool = False):
    """Dictionnaires StarDict trouves dans data/dictionaries/.

    Charges une seule fois au premier appel : l'index reste en memoire,
    les articles sont lus a la demande dans le fichier .dict.
    """
    global _stardicts
    if _stardicts is None or reload:
        _stardicts = stardict.discover(STARDICT_DIR)
    return _stardicts


def lookup_all(lemma: str, surface: str = "") -> list[dict]:
    """Consulte toutes les sources : Gaffiot, Collatinus, puis StarDict.

    On essaie le lemme, puis la forme telle qu'elle apparait dans le
    texte : beaucoup de dictionnaires indexent aussi des formes flechies.
    """
    results: list[dict] = []

    # Le Gaffiot n'apparait pas ici : sa glose, deja portee par le
    # lemme, sert d'info-bulle et de proposition de traduction. La
    # repeter dans les dictionnaires ferait doublon avec ce que le
    # panneau affiche deja en tete.

    # Collatinus : notices ecrites pour l'aide a la lecture, donc breves.
    # Elles completent utilement le Gaffiot, dont un tiers des entrees
    # n'a pas de glose exploitable.
    courte = get_short_lexicon().get(lemma)
    if courte:
        results.append({"source": "Collatinus", "headword": lemma, "body": courte})

    for dictionary in get_stardicts():
        articles = dictionary.lookup(lemma)
        matched = lemma
        if not articles and surface and surface.lower() != lemma.lower():
            articles = dictionary.lookup(surface)
            matched = surface
        for body in articles:
            results.append(
                {"source": dictionary.name, "headword": matched, "body": body}
            )
    return results


def stardict_status() -> list[dict]:
    """Etat des dictionnaires, pour la page de reglages."""
    return [
        {"name": d.name, "entries": len(d.index), "file": d.dict_path.name}
        for d in get_stardicts()
    ]


# --------------------------------------------------------------------------
# Collatinus : dictionnaire de lecture, aux notices breves
# --------------------------------------------------------------------------
SHORT_GLOSS = Path(__file__).resolve().parents[2] / "data" / "short_gloss.tsv.gz"


class ShortLexicon:
    """Dictionnaire de lecture : des traductions d'un ou deux mots.

    Source : Collatinus (Ouvrard & Verkerk), GPL. Voir data/SOURCES.md.

    Il sert au lecteur, et a lui seul : dans la section « Dictionnaires »
    du panneau, et dans l'info-bulle en complement du Gaffiot. Il n'entre
    pas dans l'arbitrage des lemmes, ou seule la base de reference fait
    autorite.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or SHORT_GLOSS
        self.entries: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        import gzip

        if not self.path.exists():
            log.info("lexique court absent (%s)", self.path)
            return
        with gzip.open(self.path, "rt", encoding="utf-8") as fh:
            for line in fh:
                if not line.strip() or line.startswith("#"):
                    continue
                key, _, gloss = line.rstrip("\n").partition("\t")
                if gloss:
                    self.entries[key] = gloss
        log.info("lexique court : %d entrées", len(self.entries))

    @property
    def available(self) -> bool:
        return bool(self.entries)

    def get(self, lemma: str) -> str:
        return self.entries.get(lemma_key(lemma), "")


_short: ShortLexicon | None = None


def get_short_lexicon() -> ShortLexicon:
    global _short
    if _short is None:
        _short = ShortLexicon()
    return _short


def short_gloss(lemma: str, upos: str | None = None) -> str:
    """Traduction courte, pour l'info-bulle.

    Le Gaffiot d'abord : c'est la base de reference, et ses notices sont
    celles que l'on retrouve dans le panneau. Collatinus prend le relais
    pour les mots qu'il ne glose pas — pres d'un tiers des entrees.
    """
    from .gaffiot import get_gaffiot

    notice = get_gaffiot().gloss(lemma, upos)
    if notice:
        return _trim(notice)
    return get_short_lexicon().get(lemma)


TOOLTIP_MAX = 38


def _trim(texte: str) -> str:
    """Raccourcit une notice pour l'info-bulle.

    Le panneau lateral affiche la notice entiere ; sous un mot du texte,
    elle deborderait. On coupe au premier sens, puis a un mot entier.
    """
    premier = texte.split(" ; ")[0].strip()
    if len(premier) <= TOOLTIP_MAX:
        return premier
    coupe = premier[:TOOLTIP_MAX].rsplit(" ", 1)[0]
    return (coupe or premier[:TOOLTIP_MAX]).rstrip(" ,;:") + "…"


def short_lexicon_status() -> dict:
    lexicon = get_short_lexicon()
    return {
        "available": lexicon.available,
        "entries": len(lexicon.entries),
        "source": "Collatinus (Ouvrard & Verkerk) — GNU GPL",
    }
