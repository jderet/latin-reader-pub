"""Selection du moteur de lemmatisation.

Ordre de preference par defaut : stanza -> cltk -> lexicon.
Le lexique est toujours disponible, donc la chaine ne peut pas echouer
completement. Le moteur effectivement retenu est enregistre avec chaque
texte (`text.engine`), ce qui permet de comparer deux annotations du
meme passage.
"""

from __future__ import annotations

import logging
import os
import threading

from .base import Lemmatizer
from .lexicon_engine import LexiconLemmatizer

log = logging.getLogger(__name__)

_lock = threading.Lock()
_cache: dict[str, Lemmatizer] = {}

DEFAULT_ORDER = ("stanza", "cltk", "lexicon")


def _build(name: str) -> Lemmatizer:
    if name == "stanza":
        from .engines import StanzaLemmatizer

        return StanzaLemmatizer(package=os.getenv("STANZA_PACKAGE", "ittb"))
    if name == "cltk":
        from .engines import CltkLemmatizer

        return CltkLemmatizer()
    if name == "lexicon":
        return LexiconLemmatizer()
    raise ValueError(f"moteur inconnu : {name}")


def get_lemmatizer(name: str | None = None) -> Lemmatizer:
    """Retourne un moteur pret a l'emploi, en descendant la chaine de repli."""
    order = (name,) if name else tuple(
        os.getenv("LEMMATIZER_ORDER", ",".join(DEFAULT_ORDER)).split(",")
    )
    with _lock:
        for candidate in (c.strip() for c in order if c and c.strip()):
            if candidate in _cache:
                return _cache[candidate]
            try:
                engine = _build(candidate)
            except Exception as exc:  # noqa: BLE001
                log.warning("moteur %s indisponible (%s)", candidate, exc)
                continue
            _cache[candidate] = engine
            log.info("moteur de lemmatisation retenu : %s %s", engine.name, engine.version)
            return engine
        fallback = LexiconLemmatizer()
        _cache["lexicon"] = fallback
        return fallback


def available_engines() -> list[dict[str, str]]:
    out = []
    for name in DEFAULT_ORDER:
        try:
            engine = _build(name) if name not in _cache else _cache[name]
            _cache[name] = engine
            out.append({"name": engine.name, "version": engine.version, "status": "ok"})
        except Exception as exc:  # noqa: BLE001
            out.append({"name": name, "version": "-", "status": f"indisponible : {exc}"})
    return out
