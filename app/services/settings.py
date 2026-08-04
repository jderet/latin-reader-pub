"""Reglages d'affichage, persistes dans la table `setting`.

Chaque reglage declare son type, sa valeur par defaut et ses bornes, ce
qui permet de generer la page de reglages automatiquement et de valider
les entrees sans code specifique a chaque champ.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Setting


@dataclass(slots=True)
class Option:
    key: str
    label: str
    kind: str  # bool | int | choice
    default: Any
    group: str
    help: str = ""
    minimum: int = 0
    maximum: int = 100
    step: int = 1
    unit: str = ""
    choices: list[tuple[str, str]] = field(default_factory=list)


OPTIONS: list[Option] = [
    # ---------------- Texte ----------------
    Option("font_family", "Police du texte latin", "choice", "serif", "Texte",
           choices=[
               ("serif", "avec empattements (Georgia)"),
               ("sans", "sans empattements"),
               ("mono", "chasse fixe"),
           ]),
    Option("font_size", "Taille du texte", "int", 122, "Texte",
           minimum=90, maximum=220, step=2, unit="%",
           help="Proportion de la taille de base."),
    Option("line_height", "Interligne", "int", 210, "Texte",
           minimum=130, maximum=320, step=5, unit="%"),
    # La largeur se mesure en caracteres, non en pixels : c'est la mesure
    # typographique, et elle suit la taille de police au lieu de la
    # contredire. Entre 55 et 75 signes par ligne, la lecture est la plus
    # aisee ; au-dela, l'oeil peine a retrouver le debut de la ligne.
    Option("line_chars", "Longueur de ligne", "int", 62, "Texte",
           minimum=38, maximum=110, step=2, unit=" signes",
           help="Nombre de caractères par ligne. Entre 55 et 75 pour un "
                "confort optimal."),
    Option("justify", "Justifier le texte", "bool", False, "Texte"),

    # ---------------- Couleurs ----------------
    Option("show_colors", "Colorer les mots selon leur statut", "bool", True, "Couleurs",
           help="Décochez pour lire sans aucune aide visuelle."),
    Option("show_unseen_blue", "Afficher en bleu les mots jamais rencontrés", "bool",
           True, "Couleurs"),
    Option("color_intensity", "Intensité des couleurs", "int", 100, "Couleurs",
           minimum=20, maximum=100, step=10, unit="%"),
    Option("show_ambiguous", "Souligner les mots à arbitrer", "bool", True, "Couleurs"),

    # ---------------- Images ----------------
    Option("show_images", "Afficher les images dans les marges", "bool", True, "Images"),
    Option("image_size", "Taille des images", "int", 92, "Images",
           minimum=48, maximum=200, step=4, unit="px",
           help="Passer le curseur dessus les agrandit."),
    Option("image_zoom", "Agrandir l'image au survol", "bool", True, "Images"),
    Option("image_zoom_size", "Taille au survol", "int", 260, "Images",
           minimum=120, maximum=460, step=20, unit="px"),
    Option("image_columns", "Colonnes d'images de chaque côté", "int", 2, "Images",
           minimum=1, maximum=3),
    Option("image_captions", "Afficher la traduction sous l'image", "bool", True, "Images"),

    # ---------------- Lecture ----------------
    Option("show_validate_bar", "Afficher le bouton de validation en masse", "bool",
           True, "Lecture"),
    Option("panel_dictionary", "Afficher le dictionnaire dans le panneau", "bool",
           True, "Lecture"),
    Option("show_help", "Mode tutoriel", "bool", True, "Lecture",
           help="Garde affichés les raccourcis, la légende des couleurs et les "
                "explications. Décochez une fois l'application en main."),
    Option("autofill_gloss", "Pré-remplir la traduction depuis le dictionnaire", "bool",
           True, "Lecture",
           help="La traduction proposée n'est enregistrée que si vous la validez."),

    # ---------------- Révision ----------------
    Option("daily_new_limit", "Nouvelles fiches par jour", "int", 20, "Révision",
           minimum=0, maximum=200, step=5),
    Option("daily_review_limit", "Révisions par jour", "int", 200, "Révision",
           minimum=10, maximum=1000, step=10),
]

BY_KEY = {o.key: o for o in OPTIONS}
GROUPS = list(dict.fromkeys(o.group for o in OPTIONS))

FONT_STACKS = {
    "serif": 'Georgia, "Iowan Old Style", "Times New Roman", serif',
    "sans": '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif',
    "mono": 'ui-monospace, "SF Mono", Menlo, monospace',
}


def _coerce(option: Option, raw: str) -> Any:
    if option.kind == "bool":
        return raw in ("1", "true", "on", "yes", "True")
    if option.kind == "int":
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            return option.default
        return max(option.minimum, min(option.maximum, value))
    if option.kind == "choice":
        valid = {c[0] for c in option.choices}
        return raw if raw in valid else option.default
    return raw


def load(session: Session, user_id: int) -> dict[str, Any]:
    """Reglages courants, defauts compris."""
    values = {o.key: o.default for o in OPTIONS}
    for row in session.scalars(
        select(Setting).where(Setting.user_id == user_id)
    ).all():
        option = BY_KEY.get(row.key)
        if option is not None:
            values[row.key] = _coerce(option, row.value)
    values["font_stack"] = FONT_STACKS.get(values["font_family"], FONT_STACKS["serif"])
    return values


def save(session: Session, user_id: int, submitted: dict[str, str]) -> dict[str, Any]:
    """Enregistre le formulaire. Les cases non cochees sont absentes du
    POST : on les traite donc comme fausses plutot que comme inchangees."""
    for option in OPTIONS:
        if option.kind == "bool":
            value = option.key in submitted
        elif option.key in submitted:
            value = _coerce(option, submitted[option.key])
        else:
            continue

        row = session.get(Setting, (user_id, option.key))
        stored = "1" if value is True else ("0" if value is False else str(value))
        if row is None:
            session.add(Setting(user_id=user_id, key=option.key, value=stored))
        else:
            row.value = stored
    session.flush()
    return load(session, user_id)


def reset(session: Session, user_id: int) -> None:
    for row in session.scalars(
        select(Setting).where(Setting.user_id == user_id)
    ).all():
        session.delete(row)
    session.flush()
