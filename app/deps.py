"""Controle d'acces.

Trois niveaux de dependance :

- `current_user` : le compte connecte, ou None.
- `require_user` : les pages de lecture. Elles sont ouvertes a tous : un
  visiteur sans compte s'en voit ouvrir un, de passage. L'administrateur
  en est exclu tant qu'il est en mode gestion : son compte est purement
  gestionnaire et n'a ni statuts ni fiches.
- `require_admin` : reserve a l'administrateur. Aucun compte de passage
  n'y accede : l'ouverture du site s'arrete a la lecture.

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
from .services import auth

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


def bearer_user(request: Request, session: Session) -> User | None:
    """Compte porteur d'un jeton API (Authorization: Bearer …)."""
    import hashlib

    from .models import ApiToken, utcnow

    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None
    raw = header[7:].strip()
    if not raw:
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    token = session.query(ApiToken).filter_by(token_hash=digest).first()
    if token is None:
        return None
    token.last_used = utcnow()
    return session.get(User, token.user_id)


def current_user(
    request: Request, session: Session = Depends(get_session)
) -> User | None:
    # Un jeton API prime sur le cookie : c'est le mode des clients natifs.
    user = bearer_user(request, session)
    if user is not None:
        return user
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    user = session.get(User, user_id)
    if user is None:
        # Compte supprime pendant la session : on la vide.
        request.session.clear()
    return user


def require_user(
    request: Request,
    user: User | None = Depends(current_user),
    session: Session = Depends(get_session),
) -> User:
    """Acces aux pages de lecture.

    Le site se lit sans inscription : un visiteur inconnu se voit ouvrir
    un compte de passage, rattache a son cookie, qui porte ses statuts et
    ses fiches comme n'importe quel compte. Il pourra le convertir en
    compte nomme sans rien perdre (cf. /register).

    Un administrateur y accede s'il est passe en mode lecture : il devient
    alors un lecteur comme un autre, avec ses propres statuts et fiches.
    En mode gestion, il est renvoye au tableau de bord — ces pages
    n'auraient pas de sens pour lui.
    """
    if user is None:
        user = auth.create_guest(session)
        request.session[SESSION_KEY] = user.id
        # Le middleware `_attach_user` s'est execute avant nous et n'a rien
        # trouve : sans cette ligne, la barre de navigation serait vide sur
        # la toute premiere page vue par un visiteur.
        request.state.user = user
        return user
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
