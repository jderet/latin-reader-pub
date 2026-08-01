"""Acces aux ressources lexicographiques.

Deux fournisseurs :
- `LocalDictionary` : fichier TSV embarque (data/dictionary.tsv), format
  « cle_lemme <TAB> vedette <TAB> glose ». C'est la ou verser un import
  de Whitaker's Words (domaine public) ou de Lewis & Short (CC BY-SA).
  Le Gaffiot est CC BY-NC-SA : utilisable ici tant que l'usage reste
  non commercial.
- `LlmSuggester` : appel facultatif a l'API Anthropic pour proposer une
  glose. Jamais enregistre automatiquement ; l'utilisateur valide.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..nlp.normalize import lemma_key

log = logging.getLogger(__name__)

DEFAULT_DICT = Path(__file__).resolve().parents[2] / "data" / "dictionary.tsv"


@dataclass(slots=True)
class DictEntry:
    lemma_key: str
    headword: str
    body: str
    source: str


class LocalDictionary:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DEFAULT_DICT
        self.entries: dict[str, list[DictEntry]] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        source = self.path.stem
        for line in self.path.read_text(encoding="utf-8").splitlines():
            line = line.rstrip("\n")
            if not line.strip() or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            key = lemma_key(parts[0])
            self.entries.setdefault(key, []).append(
                DictEntry(key, parts[1], parts[2], source)
            )

    def lookup(self, lemma: str) -> list[DictEntry]:
        return self.entries.get(lemma_key(lemma), [])


_dictionary: LocalDictionary | None = None


def get_dictionary() -> LocalDictionary:
    global _dictionary
    if _dictionary is None:
        _dictionary = LocalDictionary()
    return _dictionary


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
