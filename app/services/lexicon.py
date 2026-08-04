"""Constitution du lexique.

La table des lemmes **est** le Gaffiot : chaque entree du fichier derive
y a sa ligne, homonymes compris — `edo1` manger et `edo2` publier sont
deux mots, avec chacun son statut chez le lecteur.

Le fichier extrait du Gaffiot n'est jamais modifie. Les mots qu'il ignore
— noms propres, latin medieval, coquilles d'edition — sont ajoutes a la
main par l'administrateur et conserves dans un fichier distinct,
`data/lemmes_ajoutes.tsv`, pour qu'on ne les melange pas a la source.
Ils survivent ainsi a une reconstruction du lexique.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from ..db import DATA_DIR
from ..models import Card, Lemma, LemmaStatus, TextToken
from ..nlp.normalize import lemma_key
from .gaffiot import get_gaffiot

log = logging.getLogger(__name__)

ADDITIONS = DATA_DIR.parent / "data" / "lemmes_ajoutes.tsv"
if not ADDITIONS.parent.exists():  # pragma: no cover - installation atypique
    ADDITIONS = DATA_DIR / "lemmes_ajoutes.tsv"


# --------------------------------------------------------------------------
# Lemmes ajoutes a la main
# --------------------------------------------------------------------------
def load_additions() -> list[dict]:
    """Lemmes que l'administrateur a ajoutes hors Gaffiot."""
    if not ADDITIONS.exists():
        return []
    sortie = []
    for ligne in ADDITIONS.read_text(encoding="utf-8").splitlines():
        if not ligne.strip() or ligne.startswith("#"):
            continue
        parts = (ligne.split("\t") + [""] * 4)[:4]
        cle, upos, vedette, glose = parts
        if cle:
            sortie.append(
                {
                    "key": cle,
                    "upos": upos or "X",
                    "headword": vedette or cle,
                    "gloss": glose,
                }
            )
    return sortie


def save_addition(key: str, upos: str, headword: str = "", gloss: str = "") -> dict:
    """Inscrit un lemme d'appoint dans le fichier des ajouts."""
    cle = lemma_key(key)
    if not cle:
        raise ValueError("le lemme ne peut pas être vide")

    existants = load_additions()
    if any(a["key"] == cle and a["upos"] == upos for a in existants):
        return {"key": cle, "upos": upos, "created": False}

    ADDITIONS.parent.mkdir(parents=True, exist_ok=True)
    nouveau = not ADDITIONS.exists()
    with ADDITIONS.open("a", encoding="utf-8") as fh:
        if nouveau:
            fh.write("# Lemmes ajoutés à la main, absents du Gaffiot.\n")
            fh.write("# format : clé <TAB> upos <TAB> vedette <TAB> glose\n")
        fh.write(f"{cle}\t{upos}\t{(headword or key).strip()}\t{gloss.strip()}\n")
    log.info("lemme d'appoint enregistré : %s (%s)", cle, upos)
    return {"key": cle, "upos": upos, "created": True}


def remove_addition(key: str, upos: str) -> bool:
    cle = lemma_key(key)
    restants = [a for a in load_additions() if not (a["key"] == cle and a["upos"] == upos)]
    if len(restants) == len(load_additions()):
        return False
    lignes = [
        "# Lemmes ajoutés à la main, absents du Gaffiot.",
        "# format : clé <TAB> upos <TAB> vedette <TAB> glose",
    ]
    lignes += [f"{a['key']}\t{a['upos']}\t{a['headword']}\t{a['gloss']}" for a in restants]
    ADDITIONS.write_text("\n".join(lignes) + "\n", encoding="utf-8")
    return True


# --------------------------------------------------------------------------
# Constitution de la table
# --------------------------------------------------------------------------
def seed_lemmas(session: Session, *, reset: bool = False) -> dict:
    """Remplit la table des lemmes depuis le Gaffiot et les ajouts.

    Avec `reset`, la table est videe au prealable — et avec elle les
    statuts et les fiches, qui pointent vers des identifiants de lemmes.
    C'est destructif, et l'appelant doit l'avoir fait confirmer.
    """
    gaffiot = get_gaffiot()
    if not gaffiot.available:
        raise RuntimeError(
            "base Gaffiot absente : lancez tools/build_gaffiot.py avant"
        )

    efface = {}
    if reset:
        efface = {
            "cards": session.query(Card).count(),
            "statuses": session.query(LemmaStatus).count(),
            "lemmas": session.query(Lemma).count(),
        }
        # Les tokens des textes pointent vers les lemmes : on les detache
        # avant de vider, sinon ils designeraient des lignes disparues.
        session.query(TextToken).update(
            {TextToken.chosen_lemma_id: None}, synchronize_session=False
        )
        session.execute(delete(Card))
        session.execute(delete(LemmaStatus))
        session.execute(delete(Lemma))
        session.flush()

    connus = {
        (l.lemma, l.upos, l.homonym_idx)
        for l in session.scalars(select(Lemma)).all()
    }

    ajoutes = 0
    lots: list[Lemma] = []
    for cle, par_categorie in gaffiot.entries.items():
        for upos, homonymes in par_categorie.items():
            for entree in homonymes.values():
                reference = (cle, upos or "X", entree.homonym_idx)
                if reference in connus:
                    continue
                lots.append(
                    Lemma(
                        lemma=cle,
                        upos=upos or "X",
                        homonym_idx=entree.homonym_idx,
                        headword=entree.headword or cle,
                    )
                )
                connus.add(reference)
                ajoutes += 1
                # On ecrit par paquets : soixante-dix mille objets d'un
                # coup saturent la memoire de la session.
                if len(lots) >= 2000:
                    session.bulk_save_objects(lots)
                    session.flush()
                    lots = []
    if lots:
        session.bulk_save_objects(lots)
        session.flush()

    appoints = 0
    for ajout in load_additions():
        reference = (ajout["key"], ajout["upos"], 0)
        if reference in connus:
            continue
        session.add(
            Lemma(
                lemma=ajout["key"],
                upos=ajout["upos"],
                homonym_idx=0,
                headword=ajout["headword"],
                shared_gloss=ajout["gloss"] or None,
            )
        )
        connus.add(reference)
        appoints += 1
    session.flush()

    log.info("lexique : %d lemmes ajoutés, %d d'appoint", ajoutes, appoints)
    return {"added": ajoutes, "additions": appoints, "cleared": efface}


# Empreinte des sources : c'est elle qui declenche une mise a jour.
STAMP = DATA_DIR / "lexicon.stamp"


def _fingerprint() -> str:
    """Marque des fichiers sources : taille et date de derniere ecriture.

    Compter les lemmes en base ne suffit pas a decider s'il faut
    completer : d'autres chemins en creent aussi, et le total depasse
    alors ce que les fichiers contiennent, masquant une mise a jour.
    """
    from .gaffiot import SOURCE

    parts = []
    for chemin in (SOURCE, ADDITIONS):
        if chemin.exists():
            etat = chemin.stat()
            parts.append(f"{chemin.name}:{etat.st_size}:{int(etat.st_mtime)}")
    return "|".join(parts)


def ensure_seeded(session: Session) -> dict | None:
    """Complete le lexique au demarrage, sans rien detruire.

    Une correction du fichier derive doit parvenir a la base sans qu'on
    ait a tout refaire — ce qui effacerait les statuts et les fiches.
    """
    empreinte = _fingerprint()
    if not session.query(Lemma).count():
        rapport = seed_lemmas(session)
    elif STAMP.exists() and STAMP.read_text(encoding="utf-8").strip() == empreinte:
        return None
    else:
        rapport = seed_lemmas(session)

    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(empreinte, encoding="utf-8")
    return rapport
