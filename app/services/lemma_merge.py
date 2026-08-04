"""Fusion des lemmes en double.

Deux normalisations coexistaient : l'une se contentait de mettre en
minuscules, l'autre unifiait aussi u/v et i/j. Stanza rendant
« provincia » et le lexique embarque « prouincia », le meme mot pouvait
donner deux entrees — donc deux statuts, deux traductions, deux images.

Ce module ramene chaque groupe a une seule entree, en conservant les
annotations : les statuts sont reportes, les fiches suivent, et rien
n'est perdu. En cas de conflit (deux traductions pour le meme mot), la
plus ancienne l'emporte, la seconde etant signalee dans le journal.
"""

from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Card,
    DisambiguationOverride,
    FormLemma,
    Lemma,
    LemmaStatus,
    TextToken,
)
from ..nlp.normalize import lemma_key

log = logging.getLogger(__name__)


def find_duplicates(session: Session) -> dict[tuple[str, str, int], list[Lemma]]:
    """Groupes de lemmes qui n'auraient du en former qu'un."""
    groupes: dict[tuple[str, str, int], list[Lemma]] = defaultdict(list)
    for lemma in session.scalars(select(Lemma)).all():
        groupes[(lemma_key(lemma.lemma), lemma.upos, lemma.homonym_idx)].append(lemma)
    return {cle: rangs for cle, rangs in groupes.items() if len(rangs) > 1}


def merge_all(session: Session) -> dict:
    """Fusionne tous les doublons. Retourne un compte rendu."""
    doublons = find_duplicates(session)
    fusionnes = statuts_reportes = fiches_deplacees = conflits = 0

    for (cle, _upos, _idx), groupe in doublons.items():
        # On garde le plus ancien : c'est celui auquel les annotations
        # les plus anciennes se rattachent le plus souvent.
        groupe.sort(key=lambda l: l.id)
        garde, absorbes = groupe[0], groupe[1:]
        # La graphie definitive n'est posee qu'apres suppression des
        # doublons : la renommer maintenant entrerait en collision avec
        # l'entree qu'elle doit remplacer.
        graphie = next((l.headword for l in groupe if l.headword), garde.lemma)

        for perdu in absorbes:
            # Les annotations partagees ne sont reprises que si le lemme
            # conserve n'en a pas deja.
            if not garde.shared_gloss and perdu.shared_gloss:
                garde.shared_gloss = perdu.shared_gloss
            elif perdu.shared_gloss and perdu.shared_gloss != garde.shared_gloss:
                conflits += 1
                log.info(
                    "traduction écartée pour %s : %r (conservée : %r)",
                    cle, perdu.shared_gloss, garde.shared_gloss,
                )
            if not garde.image_path and perdu.image_path:
                garde.image_path, garde.image_alt = perdu.image_path, perdu.image_alt
            garde.is_ignored = garde.is_ignored or perdu.is_ignored

            # Les tokens des textes pointent vers le lemme conserve.
            for tok in session.scalars(
                select(TextToken).where(TextToken.chosen_lemma_id == perdu.id)
            ).all():
                tok.chosen_lemma_id = garde.id

            for lien in session.scalars(
                select(FormLemma).where(FormLemma.lemma_id == perdu.id)
            ).all():
                existant = session.scalars(
                    select(FormLemma).where(
                        FormLemma.form_id == lien.form_id,
                        FormLemma.lemma_id == garde.id,
                    )
                ).first()
                if existant is None:
                    lien.lemma_id = garde.id
                else:
                    existant.freq += lien.freq
                    session.delete(lien)

            for regle in session.scalars(
                select(DisambiguationOverride).where(
                    DisambiguationOverride.lemma_id == perdu.id
                )
            ).all():
                regle.lemma_id = garde.id

            # Statuts : un par utilisateur. On reporte ce qui manque.
            for statut in session.scalars(
                select(LemmaStatus).where(LemmaStatus.lemma_id == perdu.id)
            ).all():
                cible = session.get(LemmaStatus, (statut.user_id, garde.id))
                if cible is None:
                    session.add(
                        LemmaStatus(
                            user_id=statut.user_id,
                            lemma_id=garde.id,
                            status=statut.status,
                            is_ignored=statut.is_ignored,
                            is_locked=statut.is_locked,
                            gloss=statut.gloss,
                            note=statut.note,
                            first_seen=statut.first_seen,
                        )
                    )
                    statuts_reportes += 1
                else:
                    # Le mot le moins su l'emporte : mieux vaut le revoir
                    # une fois de trop qu'une fois de trop peu.
                    cible.status = max(cible.status, statut.status)
                    if not cible.gloss and statut.gloss:
                        cible.gloss = statut.gloss
                    if not cible.note and statut.note:
                        cible.note = statut.note
                session.delete(statut)

            for fiche in session.scalars(
                select(Card).where(Card.lemma_id == perdu.id)
            ).all():
                jumelle = session.scalars(
                    select(Card).where(
                        Card.user_id == fiche.user_id,
                        Card.lemma_id == garde.id,
                        Card.kind == fiche.kind,
                    )
                ).first()
                if jumelle is None:
                    fiche.lemma_id = garde.id
                    fiches_deplacees += 1
                else:
                    session.delete(fiche)

            session.flush()
            session.delete(perdu)
            session.flush()
            fusionnes += 1

        garde.lemma = cle
        if garde.headword is None:
            garde.headword = graphie
        session.flush()

    session.flush()
    return {
        "groups": len(doublons),
        "merged": fusionnes,
        "statuses_moved": statuts_reportes,
        "cards_moved": fiches_deplacees,
        "gloss_conflicts": conflits,
    }


def backfill_headwords(session: Session) -> int:
    """Renseigne les vedettes manquantes depuis le Gaffiot.

    Les lemmes crees avant l'arrivee de cette base n'affichaient que leur
    graphie nue : « flumen » au lieu de « flūmĕn, ĭnis ».
    """
    from .gaffiot import get_gaffiot

    gaffiot = get_gaffiot()
    if not gaffiot.available:
        return 0

    corriges = 0
    for lemma in session.scalars(select(Lemma)).all():
        vedette = gaffiot.headword(lemma.lemma, lemma.upos)
        if vedette and vedette != lemma.headword:
            lemma.headword = vedette
            corriges += 1
    session.flush()
    return corriges
