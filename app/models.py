"""Schema de donnees.

Point central : le statut de connaissance est porte par le LEMME
(`LemmaStatus.lemma_id`), jamais par la forme flechie. C'est ce qui
distingue cette application de Learning with Texts.

`Lemma.homonym_idx` est indispensable : sans lui, edo « manger » et
edo « publier » partageraient un statut.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class UtcDateTime(TypeDecorator):
    """DateTime toujours conscient du fuseau, meme sur SQLite.

    SQLite ne stocke pas le decalage horaire : sans ce decorateur, les
    dates ressortent « naives » et toute comparaison avec un datetime
    conscient leve TypeError. On normalise donc en UTC a l'ecriture et on
    re-attache UTC a la lecture.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class PageRead(Base):
    """Page qu'un lecteur declare avoir lue.

    Marquer une page lue et noter son vocabulaire sont deux gestes
    distincts : on peut avoir tout compris sans rien vouloir enregistrer.
    La trace sert a mesurer l'avancee dans un texte long.
    """

    __tablename__ = "page_read"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    text_id: Mapped[int] = mapped_column(
        ForeignKey("text.id", ondelete="CASCADE"), primary_key=True
    )
    page_idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    read_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    # Nombre de passages sur cette page. Relire est un geste courant en
    # langue ancienne, et la ligne existait deja : on compte plutot que
    # d'empiler une ligne par lecture.
    times: Mapped[int] = mapped_column(Integer, default=1)


# --------------------------------------------------------------------------
# Comptes
# --------------------------------------------------------------------------
class User(Base):
    """Un compte. L'administrateur est purement gestionnaire : il prepare
    les textes, arbitre les lemmes et illustre le vocabulaire, mais ne lit
    ni n'annote — c'est le propre des utilisateurs."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    # Compte ouvert d'office au premier passage d'un visiteur, sans mot de
    # passe utilisable. Il porte de vrais statuts et de vraies fiches : la
    # seule difference est qu'on ne peut pas s'y reconnecter tant qu'il n'a
    # pas ete converti en compte nomme (cf. auth.promote_guest).
    is_guest: Mapped[bool] = mapped_column(Boolean, default=False)
    display_name: Mapped[str | None] = mapped_column(String(120), default=None)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_seen: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, default=None)


class ApiToken(Base):
    """Jeton d'accès pour les clients natifs (API v1).

    Le cookie de session convient au navigateur ; une application mobile
    préfère un jeton porteur, longue durée, révocable individuellement.
    Seule l'empreinte SHA-256 est stockée : le jeton en clair n'est
    montré qu'à la création.
    """

    __tablename__ = "api_token"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(120), default=None)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    last_used: Mapped[dt.datetime | None] = mapped_column(UtcDateTime, default=None)


# --------------------------------------------------------------------------
# Lexique
# --------------------------------------------------------------------------
class Lemma(Base):
    __tablename__ = "lemma"
    __table_args__ = (
        UniqueConstraint("lemma", "upos", "homonym_idx", name="uq_lemma"),
        Index("ix_lemma_lemma", "lemma"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lemma: Mapped[str] = mapped_column(String(80))
    upos: Mapped[str] = mapped_column(String(12))
    homonym_idx: Mapped[int] = mapped_column(Integer, default=0)
    headword: Mapped[str | None] = mapped_column(String(160), default=None)
    lemma_meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Renseignes par l'administrateur, visibles de tous. Chaque utilisateur
    # peut remplacer la glose par la sienne (LemmaStatus.gloss).
    shared_gloss: Mapped[str | None] = mapped_column(Text, default=None)
    # Nom propre ou mot-outil ecarte du comptage pour tous les lecteurs.
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    image_path: Mapped[str | None] = mapped_column(String(240), default=None)
    image_alt: Mapped[str | None] = mapped_column(String(240), default=None)

    @property
    def display(self) -> str:
        return self.headword or self.lemma


class Form(Base):
    __tablename__ = "form"
    id: Mapped[int] = mapped_column(primary_key=True)
    form_key: Mapped[str] = mapped_column(String(80), unique=True, index=True)


class FormLemma(Base):
    """Formes observees rattachees a un lemme. Alimente les statistiques
    et l'affichage « toutes les formes rencontrees de ce lemme »."""

    __tablename__ = "form_lemma"
    __table_args__ = (UniqueConstraint("form_id", "lemma_id", name="uq_form_lemma"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("form.id", ondelete="CASCADE"))
    lemma_id: Mapped[int] = mapped_column(ForeignKey("lemma.id", ondelete="CASCADE"))
    feats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    freq: Mapped[int] = mapped_column(Integer, default=0)


# --------------------------------------------------------------------------
# Livres et textes
# --------------------------------------------------------------------------
class Author(Base):
    """Un auteur du catalogue.

    L'auteur etait une chaine libre saisie livre par livre, ce qui
    produisait des doublons — « Ciceron », « Cicéron », « M. T. Cicero »
    faisaient trois rayons pour un seul homme. Il est desormais choisi
    dans une liste que l'administrateur tient.

    L'epoque vit ici plutot que sur le livre : elle est la meme pour
    toute l'oeuvre d'un auteur.
    """

    __tablename__ = "author"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    era: Mapped[str | None] = mapped_column(String(64), default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)

    books: Mapped[list["Book"]] = relationship(
        back_populates="author", order_by="Book.sort_order, Book.title"
    )


class Book(Base):
    """Un recueil ordonne de textes.

    Chaque texte en est un chapitre numerote : « De bello Gallico » se lit
    livre I, livre II… L'ordre importe, c'est ce qui distingue un livre
    d'un simple etiquetage.
    """

    __tablename__ = "book"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    subtitle: Mapped[str | None] = mapped_column(String(240), default=None)
    # `author_name` porte l'ancienne colonne texte. Elle n'est plus
    # ecrite : elle sert de source au report vers la table `author`
    # (cf. db.backfill_authors) et de filet pour un livre importe avant
    # ce report.
    author_name: Mapped[str | None] = mapped_column(
        "author", String(160), default=None, index=True
    )
    author_id: Mapped[int | None] = mapped_column(
        ForeignKey("author.id"), default=None, index=True
    )
    author: Mapped["Author | None"] = relationship(back_populates="books")
    era: Mapped[str | None] = mapped_column(String(64), default=None)
    translator: Mapped[str | None] = mapped_column(String(160), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    cover_path: Mapped[str | None] = mapped_column(String(240), default=None)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)

    texts: Mapped[list["TextDoc"]] = relationship(
        back_populates="book", order_by="TextDoc.chapter_idx"
    )

    @property
    def display_author(self) -> str:
        if self.author is not None:
            return self.author.name
        return self.author_name or "Anonyme"

    @property
    def display_era(self) -> str | None:
        """L'epoque du livre, a defaut celle de son auteur."""
        return self.era or (self.author.era if self.author else None)


# --------------------------------------------------------------------------
# Textes
# --------------------------------------------------------------------------
class TextDoc(Base):
    __tablename__ = "text"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(240))
    author: Mapped[str | None] = mapped_column(String(160), default=None)
    source_note: Mapped[str | None] = mapped_column(Text, default=None)
    raw_content: Mapped[str] = mapped_column(Text)
    language_stage: Mapped[str] = mapped_column(String(24), default="classical")
    # Origine du texte : saisi, ou sous-titres d'une video.
    source_kind: Mapped[str] = mapped_column(String(16), default="text")
    video_id: Mapped[str | None] = mapped_column(String(16), default=None)
    audio_path: Mapped[str | None] = mapped_column(String(240), default=None)
    # Segments : intervalle de caracteres, et bornes temporelles quand
    # l'alignement a ete fait. Meme structure pour les sous-titres d'une
    # video et pour un enregistrement : le surlignage est le meme code.
    cues: Mapped[list[Any]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    engine: Mapped[str | None] = mapped_column(String(40), default=None)
    engine_version: Mapped[str | None] = mapped_column(String(60), default=None)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    page_count: Mapped[int] = mapped_column(Integer, default=1)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), default=None
    )
    book_id: Mapped[int | None] = mapped_column(
        ForeignKey("book.id", ondelete="SET NULL"), default=None, index=True
    )
    chapter_idx: Mapped[int] = mapped_column(Integer, default=0)
    chapter_label: Mapped[str | None] = mapped_column(String(120), default=None)

    book: Mapped["Book | None"] = relationship(back_populates="texts")

    tokens: Mapped[list["TextToken"]] = relationship(
        back_populates="text", cascade="all, delete-orphan", order_by="TextToken.idx"
    )


class TextToken(Base):
    __tablename__ = "text_token"
    __table_args__ = (
        UniqueConstraint("text_id", "idx", name="uq_token_idx"),
        Index("ix_token_page", "text_id", "page_idx", "idx"),
        Index("ix_token_lemma", "chosen_lemma_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    text_id: Mapped[int] = mapped_column(ForeignKey("text.id", ondelete="CASCADE"))
    idx: Mapped[int] = mapped_column(Integer)
    sentence_idx: Mapped[int] = mapped_column(Integer, default=0)
    page_idx: Mapped[int] = mapped_column(Integer, default=0)
    surface: Mapped[str] = mapped_column(String(120))
    char_start: Mapped[int] = mapped_column(Integer)
    char_end: Mapped[int] = mapped_column(Integer)
    trailing: Mapped[str] = mapped_column(String(40), default=" ")
    is_word: Mapped[bool] = mapped_column(Boolean, default=True)
    # Enclitique detachee (« -que », « -cum ») et lien vers son porteur.
    is_enclitic: Mapped[bool] = mapped_column(Boolean, default=False)
    parent_token_id: Mapped[int | None] = mapped_column(
        ForeignKey("text_token.id", ondelete="SET NULL"), default=None
    )
    form_id: Mapped[int | None] = mapped_column(ForeignKey("form.id"), default=None)
    chosen_lemma_id: Mapped[int | None] = mapped_column(
        ForeignKey("lemma.id"), default=None
    )
    feats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    candidates: Mapped[list[Any]] = mapped_column(JSON, default=list)
    ambiguity_margin: Mapped[float] = mapped_column(Float, default=1.0)
    is_guessed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False)

    text: Mapped[TextDoc] = relationship(back_populates="tokens")
    lemma: Mapped[Lemma | None] = relationship()


# --------------------------------------------------------------------------
# Connaissance
# --------------------------------------------------------------------------
class LemmaStatus(Base):
    """4 inconnu … 0 maitrise. Une seule ligne par lemme.

    L'absence de ligne signifie « jamais rencontre », etat distinct de 4.
    """

    __tablename__ = "lemma_status"
    __table_args__ = (
        CheckConstraint("status BETWEEN 0 AND 4", name="ck_status_range"),
        Index("ix_status_user", "user_id", "status"),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    lemma_id: Mapped[int] = mapped_column(
        ForeignKey("lemma.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[int] = mapped_column(Integer, default=4)
    is_ignored: Mapped[bool] = mapped_column(Boolean, default=False)
    is_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Glose personnelle : si elle est vide, celle du lemme s'applique.
    gloss: Mapped[str | None] = mapped_column(Text, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)
    first_seen: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)

    lemma_obj: Mapped[Lemma] = relationship()


class DisambiguationOverride(Base):
    """Decision d'arbitrage memorisee et propagee."""

    __tablename__ = "disambiguation_override"
    __table_args__ = (
        UniqueConstraint("form_id", "scope", "text_id", name="uq_override"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    form_id: Mapped[int] = mapped_column(ForeignKey("form.id", ondelete="CASCADE"))
    lemma_id: Mapped[int] = mapped_column(ForeignKey("lemma.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(8), default="global")  # global | text
    text_id: Mapped[int | None] = mapped_column(
        ForeignKey("text.id", ondelete="CASCADE"), default=None
    )
    feats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)


class MorphSkill(Base):
    """Maitrise morphologique, axe independant du statut lexical.

    Alimente par les fiches a trous ; ne colore jamais le texte.
    """

    __tablename__ = "morph_skill"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    feature_key: Mapped[str] = mapped_column(String(64), primary_key=True)
    successes: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)

    @property
    def accuracy(self) -> float:
        return self.successes / self.attempts if self.attempts else 0.0


# --------------------------------------------------------------------------
# Fiches
# --------------------------------------------------------------------------
class Card(Base):
    __tablename__ = "card"
    __table_args__ = (
        UniqueConstraint("user_id", "lemma_id", "kind", name="uq_card_lemma_kind"),
        Index("ix_card_due", "user_id", "is_suspended", "due_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"))
    lemma_id: Mapped[int] = mapped_column(ForeignKey("lemma.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(8))  # la_fr | fr_la | cloze
    front: Mapped[str] = mapped_column(Text)
    back: Mapped[str] = mapped_column(Text)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_suspended: Mapped[bool] = mapped_column(Boolean, default=False)

    ease_factor: Mapped[float] = mapped_column(Float, default=2.5)
    interval_days: Mapped[int] = mapped_column(Integer, default=0)
    repetitions: Mapped[int] = mapped_column(Integer, default=0)
    lapses: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    created_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)

    lemma: Mapped[Lemma] = relationship()
    contexts: Mapped[list["CardContext"]] = relationship(
        back_populates="card", cascade="all, delete-orphan"
    )

    @property
    def is_new(self) -> bool:
        return self.repetitions == 0 and self.lapses == 0


class CardContext(Base):
    __tablename__ = "card_context"
    __table_args__ = (UniqueConstraint("card_id", "token_id", name="uq_card_context"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"))
    token_id: Mapped[int] = mapped_column(ForeignKey("text_token.id", ondelete="CASCADE"))
    sentence: Mapped[str] = mapped_column(Text)
    surface: Mapped[str] = mapped_column(String(120))
    added_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)

    card: Mapped[Card] = relationship(back_populates="contexts")


class ReviewLog(Base):
    """Journal complet des revisions.

    Conserve integralement pour permettre, le cas echeant, de rejouer
    l'historique sous un autre ordonnanceur (FSRS) sans perte.
    """

    __tablename__ = "review_log"
    __table_args__ = (Index("ix_review_card", "card_id", "reviewed_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("card.id", ondelete="CASCADE"))
    reviewed_at: Mapped[dt.datetime] = mapped_column(UtcDateTime, default=utcnow)
    quality: Mapped[int] = mapped_column(Integer)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, default=None)
    prev_interval: Mapped[int] = mapped_column(Integer, default=0)
    new_interval: Mapped[int] = mapped_column(Integer, default=0)
    prev_ef: Mapped[float] = mapped_column(Float, default=2.5)
    new_ef: Mapped[float] = mapped_column(Float, default=2.5)


class Setting(Base):
    __tablename__ = "setting"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("user.id", ondelete="CASCADE"), primary_key=True
    )
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
