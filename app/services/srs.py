"""SM-2 et transitions de statut. Fonctions pures, sans acces base."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

MIN_EASE = 1.3
MAX_INTERVAL_DAYS = 365
RELEARN_DELAY_MIN = 10

# Boutons de l'interface projetes sur l'echelle SM-2 (0..5).
BUTTON_QUALITY = {"again": 1, "hard": 3, "good": 4, "easy": 5}


@dataclass(slots=True)
class Sm2State:
    ease_factor: float = 2.5
    interval_days: int = 0
    repetitions: int = 0
    lapses: int = 0


@dataclass(slots=True)
class Sm2Result:
    ease_factor: float
    interval_days: int
    repetitions: int
    lapses: int
    is_lapse: bool
    due_at: dt.datetime


def sm2(state: Sm2State, quality: int, *, now: dt.datetime | None = None) -> Sm2Result:
    """Algorithme SM-2 d'origine, avec les garde-fous d'usage d'Anki.

    quality < 3 : echec, l'intervalle repart a 1 jour et le facteur de
    facilite est penalise. quality >= 3 : progression normale.
    """
    if not 0 <= quality <= 5:
        raise ValueError("quality doit etre compris entre 0 et 5")
    now = now or dt.datetime.now(dt.timezone.utc)

    if quality < 3:
        ef = max(MIN_EASE, state.ease_factor - 0.20)
        return Sm2Result(
            ease_factor=round(ef, 4),
            interval_days=1,
            repetitions=0,
            lapses=state.lapses + 1,
            is_lapse=True,
            due_at=now + dt.timedelta(minutes=RELEARN_DELAY_MIN),
        )

    ef = state.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    ef = max(MIN_EASE, ef)

    if state.repetitions == 0:
        interval = 1
    elif state.repetitions == 1:
        interval = 6
    else:
        interval = round(state.interval_days * ef)

    interval = max(1, min(interval, MAX_INTERVAL_DAYS))
    return Sm2Result(
        ease_factor=round(ef, 4),
        interval_days=interval,
        repetitions=state.repetitions + 1,
        lapses=state.lapses,
        is_lapse=False,
        due_at=now + dt.timedelta(days=interval),
    )


# --------------------------------------------------------------------------
# Transitions de statut lexical
# --------------------------------------------------------------------------
STATUS_UNKNOWN = 4
STATUS_MASTERED = 0
AUTO_DEMOTE_FLOOR = 3  # un echec ne fait jamais remonter au-dela de 3
PROMOTE_MIN_REPETITIONS = 3


def next_status(
    current: int,
    *,
    quality: int,
    repetitions_per_card: list[int],
    is_locked: bool,
) -> int | None:
    """Nouveau statut, ou None si rien ne change.

    Promotion (vers 0) si la revision est bonne ET si toutes les fiches
    actives du lemme ont atteint PROMOTE_MIN_REPETITIONS.
    Retrogradation d'un cran en cas d'echec, plafonnee a AUTO_DEMOTE_FLOOR.
    Le verrou manuel gele toute automatisation.
    """
    if is_locked:
        return None

    if quality <= 2:
        target = min(AUTO_DEMOTE_FLOOR, current + 1)
        return target if target != current else None

    if quality >= 4 and repetitions_per_card:
        if all(r >= PROMOTE_MIN_REPETITIONS for r in repetitions_per_card):
            target = max(STATUS_MASTERED, current - 1)
            return target if target != current else None
    return None
