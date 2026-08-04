"""Controle d'acces.

Trois niveaux de dependance :

- `current_user` : le compte connecte, ou None.
- `require_user` : reserve aux lecteurs. L'administrateur en est exclu :
  son compte est purement gestionnaire et n'a ni statuts ni fiches.
- `require_admin` : reserve a l'administrateur.

Refuser l'acces d'un administrateur aux pages de lecture n'est pas une
brimade : sans statut ni fiche, ces pages n'auraient aucun sens pour lui,
et un statut cree sous son compte serait invisible de tous.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .db import get_session
from .models import User

SESSION_KEY = "user_id"
MODE_KEY = "mode"

MODE_ADMIN = "admin"
MODE_USER = "user"


def current_mode(request: Request) -> str:
    """Mode actif de l'administrateur : gestion ou lecture.

    Sans effet pour un lecteur ordinaire, qui n'a qu'un mode.
    """
    return request.session.get(MODE_KEY, MODE_ADMIN)


class RedirectToLogin(HTTPException):
    """Signale qu'une redirection vers la connexion est necessaire."""

    def __init__(self, destination: str = "/login") -> None:
        super().__init__(status_code=307, headers={"Location": destination})
        self.destination = destination


def current_user(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    user = session.get(User, user_id)
    if user is None:
        # Compte supprime pendant la session : on la vide.
        request.session.clear()
    return user


def require_user(
    request: Request, user: User | None = Depends(current_user)
) -> User:
    """Acces aux pages de lecture.

    Un administrateur y accede s'il est passe en mode lecture : il devient
    alors un lecteur comme un autre, avec ses propres statuts et fiches.
    En mode gestion, il est renvoye au tableau de bord — ces pages
    n'auraient pas de sens pour lui.
    """
    if user is None:
        raise RedirectToLogin()
    if user.is_admin and current_mode(request) != MODE_USER:
        raise RedirectToLogin("/admin")
    return user


def require_admin(user: User | None = Depends(current_user)) -> User:
    """Acces aux pages de gestion.

    Le mode n'entre pas en compte : un administrateur passe en lecture
    garde ses droits, sans quoi la bascule serait un piege.
    """
    if user is None:
        raise RedirectToLogin()
    if not user.is_admin:
        raise HTTPException(403, "réservé à l'administrateur")
    return user


def redirect(destination: str) -> RedirectResponse:
    return RedirectResponse(destination, status_code=303)
