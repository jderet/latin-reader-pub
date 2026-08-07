"""Comptes, mots de passe et sessions.

Deux roles seulement :

- **administrateur** : prepare la bibliotheque. Il importe les textes,
  arbitre les lemmes ambigus, redige les gloses partagees et illustre le
  vocabulaire. Il ne lit ni n'annote : noter un mot est le propre de
  l'utilisateur, et son compte n'a donc ni statuts, ni fiches.
- **utilisateur** : lit, annote, revise. Il ne peut ni ajouter de texte
  ni modifier les images.

Le hachage utilise `hashlib.scrypt`, de la bibliotheque standard : pas de
dependance supplementaire, et un cout de calcul suffisant pour un service
local. Le mot de passe n'est jamais stocke en clair.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import unicodedata

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import User, utcnow

log = logging.getLogger(__name__)

# Parametres scrypt : compromis entre securite et temps de reponse sur un
# portable. n=2**14 represente environ 100 ms par verification.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16

MIN_PASSWORD = 6
MAX_PASSWORD = 256
USERNAME_MAX = 64

DEFAULT_ADMIN = "admin"
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

# Prefixe des comptes de passage. Reserve : personne ne peut s'inscrire
# sous un identifiant qui commence ainsi, sans quoi un visiteur pourrait
# se faire passer pour l'invite d'un autre.
GUEST_PREFIX = "invite-"


class AuthError(Exception):
    """Entree invalide : identifiant deja pris, mot de passe trop court…"""


# --------------------------------------------------------------------------
# Mots de passe
# --------------------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(SALT_BYTES)
    derived = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=int(n),
            r=int(r),
            p=int(p),
        )
    except (ValueError, TypeError):
        return False
    # Comparaison a temps constant : ne pas reveler ou la difference tombe.
    return hmac.compare_digest(derived.hex(), digest_hex)


# --------------------------------------------------------------------------
# Comptes
# --------------------------------------------------------------------------
def normalize_username(name: str) -> str:
    """Identifiant sans accents ni casse, pour eviter les quasi-doublons."""
    name = unicodedata.normalize("NFD", name.strip())
    name = "".join(c for c in name if not unicodedata.combining(c))
    return name.lower()


def validate_username(session: Session, username: str) -> str:
    """Verifie un identifiant et renvoie sa forme normalisee."""
    key = normalize_username(username)
    if not key:
        raise AuthError("l'identifiant ne peut pas être vide")
    if len(key) > USERNAME_MAX:
        raise AuthError(f"identifiant trop long (maximum {USERNAME_MAX} caractères)")
    if not key.replace("_", "").replace("-", "").replace(".", "").isalnum():
        raise AuthError(
            "l'identifiant ne peut contenir que des lettres, des chiffres, "
            "et les signes - _ ."
        )
    if key.startswith(GUEST_PREFIX):
        raise AuthError(f"« {GUEST_PREFIX} » est réservé aux comptes de passage")
    if get_by_username(session, key) is not None:
        raise AuthError("cet identifiant est déjà pris")
    return key


def get_by_username(session: Session, username: str) -> User | None:
    return session.scalars(
        select(User).where(User.username == normalize_username(username))
    ).first()


def create_user(
    session: Session,
    username: str,
    password: str,
    *,
    is_admin: bool = False,
    display_name: str | None = None,
    bootstrap: bool = False,
) -> User:
    key = validate_username(session, username)
    # Le compte administrateur initial echappe a la regle de longueur :
    # son mot de passe par defaut est volontairement memorisable, et
    # l'application rappelle de le changer tant qu'il est en place.
    if not bootstrap and not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError(f"le mot de passe doit faire au moins {MIN_PASSWORD} caractères")

    user = User(
        username=key,
        password_hash=hash_password(password),
        is_admin=is_admin,
        display_name=(display_name or username).strip() or key,
    )
    session.add(user)
    session.flush()
    log.info("compte créé : %s%s", key, " (administrateur)" if is_admin else "")
    return user


def authenticate(session: Session, username: str, password: str) -> User | None:
    user = get_by_username(session, username)
    if user is None:
        # On effectue tout de meme un calcul, pour ne pas reveler par le
        # temps de reponse qu'un identifiant n'existe pas.
        hash_password(password)
        return None
    if not verify_password(password, user.password_hash):
        return None
    user.last_seen = utcnow()
    return user


# --------------------------------------------------------------------------
# Comptes de passage
# --------------------------------------------------------------------------
def create_guest(session: Session) -> User:
    """Ouvre un compte pour un visiteur qui ne s'est pas connecte.

    Le site est lisible sans inscription : plutot que de traiter le
    visiteur comme un cas particulier partout ou l'application suppose un
    `user_id` — coloration, couverture, fiches —, on lui donne un vrai
    compte, rattache a son cookie de session. L'invariant « le statut
    porte sur le lemme, pour un utilisateur » reste entier.

    Le mot de passe est volontairement inutilisable : `verify_password`
    echoue sur cette chaine, donc nul ne peut se connecter a un compte
    invite. Seul le cookie y donne acces, et `promote_guest` en fait un
    compte nomme sans rien perdre.
    """
    user = User(
        username=f"{GUEST_PREFIX}{secrets.token_hex(6)}",
        password_hash="!",
        is_admin=False,
        is_guest=True,
        display_name="Invité",
    )
    session.add(user)
    session.flush()
    log.info("compte de passage ouvert : %s", user.username)
    return user


def promote_guest(
    session: Session, user: User, username: str, password: str
) -> User:
    """Convertit un compte de passage en compte nomme.

    On renomme le compte au lieu d'en creer un second : le vocabulaire
    marque et les fiches creees pendant la visite sont ainsi conserves,
    puisqu'ils pendent tous du meme `user_id`.
    """
    if not user.is_guest:
        raise AuthError("ce compte est déjà un compte nommé")
    key = validate_username(session, username)
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError(f"le mot de passe doit faire au moins {MIN_PASSWORD} caractères")
    user.username = key
    user.password_hash = hash_password(password)
    user.display_name = username.strip() or key
    user.is_guest = False
    session.flush()
    log.info("compte de passage converti : %s", key)
    return user


def count_guests(session: Session) -> int:
    return session.scalar(
        select(func.count()).select_from(User).where(User.is_guest.is_(True))
    ) or 0


def set_password(session: Session, user: User, password: str) -> None:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise AuthError(f"le mot de passe doit faire au moins {MIN_PASSWORD} caractères")
    user.password_hash = hash_password(password)


def count_users(session: Session, *, admins: bool | None = None) -> int:
    stmt = select(func.count()).select_from(User)
    if admins is not None:
        stmt = stmt.where(User.is_admin.is_(admins))
    return session.scalar(stmt) or 0


def list_users(session: Session, *, guests: bool = False) -> list[User]:
    """Les comptes nommes. Les comptes de passage, potentiellement
    nombreux sur un site public, sont comptes a part (`count_guests`)."""
    stmt = select(User).order_by(User.is_admin.desc(), User.username)
    if not guests:
        stmt = stmt.where(User.is_guest.is_(False))
    return list(session.scalars(stmt).all())


def ensure_default_admin(session: Session) -> User | None:
    """Cree le compte « admin » au premier demarrage.

    Le mot de passe par defaut est « admin » ; il peut etre impose par la
    variable d'environnement ADMIN_PASSWORD. L'application signale tant
    qu'il n'a pas ete change.
    """
    if count_users(session, admins=True):
        return None
    admin = create_user(
        session,
        DEFAULT_ADMIN,
        DEFAULT_ADMIN_PASSWORD,
        is_admin=True,
        display_name="Administrateur",
        bootstrap=True,
    )
    log.warning(
        "compte administrateur créé : %s / %s — changez ce mot de passe",
        DEFAULT_ADMIN,
        DEFAULT_ADMIN_PASSWORD,
    )
    return admin


def uses_default_password(session: Session) -> bool:
    """L'administrateur a-t-il conserve le mot de passe initial ?"""
    admin = get_by_username(session, DEFAULT_ADMIN)
    return bool(admin and verify_password(DEFAULT_ADMIN_PASSWORD, admin.password_hash))


# --------------------------------------------------------------------------
# Compte de démonstration
# --------------------------------------------------------------------------
DEMO_USERNAME = "moyen"
DEMO_PASSWORD = "moyenla"
DEMO_LEMMAS = 5000


def regenerate_demo_user(
    session: Session, *, count: int = DEMO_LEMMAS, seed: int | None = None
) -> dict:
    """(Re)construit le compte « moyen », lecteur de niveau intermediaire.

    Il sert a voir l'application comme la verrait quelqu'un possedant deja
    un vocabulaire : couleurs du texte, jauges de la bibliotheque, files de
    revision. Les lemmes sont tires au sort parmi ceux du Gaffiot presents
    en base, avec une repartition des statuts plutot que tout en « connu » :
    un lecteur reel n'a pas un vocabulaire uniforme.
    """
    import random

    from ..models import Lemma, LemmaStatus
    from . import knowledge

    utilisateur = get_by_username(session, DEMO_USERNAME)
    if utilisateur is None:
        utilisateur = create_user(
            session, DEMO_USERNAME, DEMO_PASSWORD, display_name="Niveau moyen"
        )
    else:
        set_password(session, utilisateur, DEMO_PASSWORD)
        # On repart d'une table rase : sans cela, deux generations
        # successives cumuleraient leurs statuts.
        for ligne in session.scalars(
            select(LemmaStatus).where(LemmaStatus.user_id == utilisateur.id)
        ).all():
            session.delete(ligne)
        session.flush()

    # Le lexique en base ne contient que les mots rencontres dans les
    # textes : bien trop peu pour figurer un vocabulaire. On puise donc
    # dans le Gaffiot, en creant les lemmes manquants.
    from .gaffiot import get_gaffiot
    from .importer import get_or_create_lemma

    tirage = random.Random(seed)
    gaffiot = get_gaffiot()
    if gaffiot.available:
        vocabulaire = [
            (cle, upos)
            for cle, categories in gaffiot.entries.items()
            for upos in categories
            if upos in ("NOUN", "VERB", "ADJ", "ADV")
        ]
        tirage.shuffle(vocabulaire)
        choisis = []
        for cle, upos in vocabulaire[:count]:
            choisis.append(get_or_create_lemma(session, cle, upos).id)
        session.flush()
    else:
        disponibles = list(session.scalars(select(Lemma.id)).all())
        tirage.shuffle(disponibles)
        choisis = disponibles[:count]

    # Un vocabulaire realiste s'etage : beaucoup de mots bien sus, quelques
    # uns encore fragiles.
    repartition = [(0, 0.45), (1, 0.30), (2, 0.15), (3, 0.10)]
    position = 0
    par_statut: dict[int, int] = {}
    for statut, part in repartition:
        fin = position + int(len(choisis) * part)
        tranche = choisis[position:fin] if statut != 3 else choisis[position:]
        for lemma_id in tranche:
            knowledge.set_status(session, utilisateur.id, lemma_id, status=statut)
        par_statut[statut] = len(tranche)
        position = fin

    session.flush()
    log.info(
        "compte de démonstration régénéré : %d lemmes", sum(par_statut.values())
    )
    return {
        "user_id": utilisateur.id,
        "username": DEMO_USERNAME,
        "password": DEMO_PASSWORD,
        "total": sum(par_statut.values()),
        "by_status": par_statut,
        "available": len(choisis),
    }
