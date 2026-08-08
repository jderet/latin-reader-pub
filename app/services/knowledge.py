"""Statuts lexicaux, couverture des textes, competences morphologiques."""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..models import Lemma, LemmaStatus, MorphSkill, TextToken, utcnow

STATUS_LABELS = {
    4: "inconnu",
    3: "vaguement reconnu",
    2: "reconnu en contexte",
    1: "presque acquis",
    0: "maitrise",
}
KNOWN_THRESHOLD = 1  # statut <= 1 compte comme connu


def set_status(
    session: Session,
    user_id: int,
    lemma_id: int,
    *,
    status: int | None = None,
    is_ignored: bool | None = None,
    gloss: str | None = None,
    note: str | None = None,
    lock: bool | None = None,
    manual: bool = True,
) -> LemmaStatus:
    """Cree ou met a jour le statut d'un lemme.

    Une modification manuelle pose is_locked et gele les transitions
    automatiques jusqu'a deverrouillage explicite.
    """
    row = session.get(LemmaStatus, (user_id, lemma_id))
    if row is None:
        # Deux requetes rapprochees (poser un statut puis enregistrer une
        # glose) peuvent constater l'absence de ligne en meme temps et
        # tenter chacune de la creer. On absorbe la collision : celle qui
        # perd la course relit simplement la ligne creee par l'autre.
        candidate = LemmaStatus(
            user_id=user_id, lemma_id=lemma_id,
            status=status if status is not None else 4,
        )
        try:
            with session.begin_nested():
                session.add(candidate)
                session.flush()
            row = candidate
        except IntegrityError:
            session.expire_all()
            row = session.get(LemmaStatus, (user_id, lemma_id))
            if row is None:  # pragma: no cover - ne devrait pas arriver
                raise

    if status is not None:
        if not 0 <= status <= 4:
            raise ValueError("statut hors echelle")
        row.status = status
        # Choisir explicitement un statut sort le lemme de l'etat « ignore ».
        # Sans cela, un mot ignore restait grise quel que soit le statut
        # choisi ensuite, et le clic paraissait sans effet.
        if is_ignored is None:
            row.is_ignored = False
        if manual:
            row.is_locked = True
    if is_ignored is not None:
        row.is_ignored = is_ignored
    if gloss is not None:
        row.gloss = gloss.strip() or None
    if note is not None:
        row.note = note.strip() or None
    if lock is not None:
        row.is_locked = lock
    row.updated_at = utcnow()
    return row


def statuses_for_tokens(
    session: Session, user_id: int, token_rows: list[TextToken]
) -> dict[int, LemmaStatus]:
    ids = {t.chosen_lemma_id for t in token_rows if t.chosen_lemma_id}
    if not ids:
        return {}
    rows = session.scalars(
        select(LemmaStatus).where(
            LemmaStatus.user_id == user_id, LemmaStatus.lemma_id.in_(ids)
        )
    ).all()
    return {r.lemma_id: r for r in rows}


def is_ignored_token(tok, lemma, status) -> bool:
    """Ce mot est-il exclu du comptage ?

    Trois manieres d'etre ignore, qu'il faut traiter ensemble sous peine
    de jauges qui se contredisent :

    - le lecteur l'a ignore (`LemmaStatus.is_ignored`) ;
    - l'administrateur l'a marque nom propre (`Lemma.is_ignored`) ;
    - c'en est un, ou un chiffre romain, reconnu a l'analyse.

    Les deux derniers cas n'etaient pas exclus : un texte plein de noms
    de peuples et de fleuves affichait une large tranche « jamais
    rencontre » qu'aucune lecture n'aurait jamais resorbee.
    """
    from .importer import is_auto_ignored

    if status is not None and status.is_ignored:
        return True
    if lemma is None:
        return False
    return bool(lemma.is_ignored) or is_auto_ignored(lemma.upos, tok.surface)


def text_coverage(session: Session, user_id: int, text_id: int) -> dict:
    """Repartition des occurrences par statut, plus les inconnus frequents."""
    tokens = session.scalars(
        select(TextToken).where(
            TextToken.text_id == text_id, TextToken.is_word.is_(True)
        )
    ).all()
    statuses = statuses_for_tokens(session, user_id, tokens)
    lemmes = {
        lemme.id: lemme
        for lemme in session.scalars(
            select(Lemma).where(
                Lemma.id.in_({t.chosen_lemma_id for t in tokens if t.chosen_lemma_id})
            )
        ).all()
    }

    buckets = Counter()
    unknown_freq: Counter[int] = Counter()
    total = 0
    for tok in tokens:
        st = statuses.get(tok.chosen_lemma_id or -1)
        if is_ignored_token(tok, lemmes.get(tok.chosen_lemma_id or -1), st):
            buckets["ignore"] += 1
            continue
        total += 1
        if st is None:
            buckets["jamais vu"] += 1
            if tok.chosen_lemma_id:
                unknown_freq[tok.chosen_lemma_id] += 1
        else:
            buckets[STATUS_LABELS[st.status]] += 1
            if st.status > KNOWN_THRESHOLD:
                unknown_freq[st.lemma_id] += 1

    known = sum(
        v for k, v in buckets.items()
        if k in {STATUS_LABELS[0], STATUS_LABELS[1]}
    )
    top = []
    for lemma_id, count in unknown_freq.most_common(20):
        lemma = session.get(Lemma, lemma_id)
        if lemma:
            top.append({"lemma_id": lemma_id, "lemma": lemma.display, "count": count})

    distinct = len({t.chosen_lemma_id for t in tokens if t.chosen_lemma_id})
    # Repartition ordonnee, du mieux su au jamais vu : c'est ce qui
    # permet de dessiner une jauge lisible d'un coup d'oeil.
    spectre = []
    for statut in (0, 1, 2, 3, 4):
        nombre = buckets.get(STATUS_LABELS[statut], 0)
        if nombre:
            spectre.append(
                {
                    "key": f"s{statut}",
                    "label": STATUS_LABELS[statut],
                    "count": nombre,
                    "share": round(100 * nombre / total, 1) if total else 0,
                }
            )
    jamais = buckets.get("jamais vu", 0)
    if jamais:
        spectre.append(
            {
                "key": "unseen",
                "label": "jamais rencontré",
                "count": jamais,
                "share": round(100 * jamais / total, 1) if total else 0,
            }
        )

    return {
        "total_words": total,
        "distinct_lemmas": distinct,
        "known_ratio": round(known / total, 4) if total else 0.0,
        "buckets": dict(buckets),
        "spectrum": spectre,
        "top_unknown": top,
    }


def global_progress(session: Session, user_id: int) -> dict:
    rows = session.execute(
        select(LemmaStatus.status, func.count())
        .where(LemmaStatus.user_id == user_id, LemmaStatus.is_ignored.is_(False))
        .group_by(LemmaStatus.status)
    ).all()
    by_status = {STATUS_LABELS[s]: c for s, c in rows}
    return {
        "by_status": by_status,
        "total_tracked": sum(by_status.values()),
    }


# --------------------------------------------------------------------------
# Taille du vocabulaire, sur l'echelle du CECR
# --------------------------------------------------------------------------
# Seuils indicatifs de vocabulaire actif par niveau. Ils ne sont pas
# normatifs — le CECR decrit des competences, pas des comptes de mots —
# mais ils donnent au lecteur un ordre de grandeur de son avancee.
CEFR_LEVELS: tuple[tuple[int, str], ...] = (
    (800, "A1"),
    (1800, "A2"),
    (3500, "B1"),
    (5500, "B2"),
    (8000, "C1"),
    (10000, "C2"),
)


def vocabulary_gauge(session: Session, user_id: int) -> dict:
    """Le vocabulaire du lecteur, ventile par statut et rapporte au CECR.

    Deux nombres a ne pas confondre : la jauge dessine **tous** les
    lemmes suivis, chacun de la couleur de son statut, tandis que le
    niveau atteint ne compte que les mots reellement sus (statut 0 ou 1,
    cf. KNOWN_THRESHOLD). Un lecteur qui a marque trois mille mots en
    rouge n'est pas B1.

    L'echelle s'etire au-dela de C2 si le vocabulaire le depasse : la
    jauge ne deborde jamais, et les graduations restent a leur place
    relative.
    """
    rows = session.execute(
        select(LemmaStatus.status, func.count())
        .where(LemmaStatus.user_id == user_id, LemmaStatus.is_ignored.is_(False))
        .group_by(LemmaStatus.status)
    ).all()
    return gauge_from_counts({statut: nombre for statut, nombre in rows})


def gauge_from_counts(par_statut: dict[int, int]) -> dict:
    """Met en forme une jauge a partir d'un decompte par statut.

    Fonction pure, sans base : la jauge d'un lecteur et celles du
    classement sortent d'ici, si bien que l'echelle et les seuils ne
    peuvent pas diverger d'une page a l'autre.
    """
    total = sum(par_statut.values())
    connus = sum(n for s, n in par_statut.items() if s <= KNOWN_THRESHOLD)
    echelle = max(CEFR_LEVELS[-1][0], total)

    spectre = [
        {
            "key": f"s{statut}",
            "label": STATUS_LABELS[statut],
            "count": par_statut[statut],
            "share": round(100 * par_statut[statut] / echelle, 2),
        }
        for statut in (0, 1, 2, 3, 4)
        if par_statut.get(statut)
    ]
    graduations = [
        {
            "threshold": seuil,
            "label": nom,
            "position": round(100 * seuil / echelle, 2),
            "reached": connus >= seuil,
        }
        for seuil, nom in CEFR_LEVELS
    ]
    atteints = [g["label"] for g in graduations if g["reached"]]
    return {
        "spectrum": spectre,
        "total": total,
        "known": connus,
        "scale": echelle,
        "levels": graduations,
        "level": atteints[-1] if atteints else None,
    }


def vocabulary_ranking(session: Session, users: list) -> list[dict]:
    """Les lecteurs classes par nombre de mots connus.

    Une seule requete groupee pour tout le monde : une jauge par compte
    en aurait fait autant que de lecteurs.

    L'echelle de chaque jauge reste celle du CECR, la meme pour tous, si
    bien que les barres se comparent a l'oeil d'une ligne a l'autre.
    """
    rows = session.execute(
        select(LemmaStatus.user_id, LemmaStatus.status, func.count())
        .where(
            LemmaStatus.is_ignored.is_(False),
            LemmaStatus.user_id.in_([u.id for u in users]) if users else False,
        )
        .group_by(LemmaStatus.user_id, LemmaStatus.status)
    ).all()

    comptes: dict[int, dict[int, int]] = defaultdict(dict)
    for user_id, statut, nombre in rows:
        comptes[user_id][statut] = nombre

    classement = [
        {"user": compte, "gauge": gauge_from_counts(comptes.get(compte.id, {}))}
        for compte in users
    ]
    # A egalite de mots sus, le plus petit vocabulaire suivi passe devant :
    # savoir mille mots sur mille vaut mieux que mille sur cinq mille.
    classement.sort(key=lambda l: (-l["gauge"]["known"], l["gauge"]["total"]))
    return classement


# --------------------------------------------------------------------------
# Axe morphologique, independant du statut lexical
# --------------------------------------------------------------------------
TRACKED_FEATURES = ("Case", "Number", "Tense", "Mood", "Voice", "Degree", "VerbForm")


def feature_keys(feats: dict) -> list[str]:
    keys = [f"{k}={feats[k]}" for k in TRACKED_FEATURES if k in feats]
    if "Tense" in feats and "Mood" in feats:
        keys.append(f"Tense={feats['Tense']}|Mood={feats['Mood']}")
    return keys


def record_morph_attempt(
    session: Session, user_id: int, feats: dict, *, success: bool
) -> None:
    for key in feature_keys(feats):
        row = session.get(MorphSkill, (user_id, key))
        if row is None:
            row = MorphSkill(user_id=user_id, feature_key=key)
            session.add(row)
            session.flush()
        row.attempts += 1
        if success:
            row.successes += 1
        row.updated_at = utcnow()


def weakest_features(session: Session, user_id: int, limit: int = 10) -> list[dict]:
    rows = session.scalars(
        select(MorphSkill).where(
            MorphSkill.user_id == user_id, MorphSkill.attempts >= 3
        )
    ).all()
    rows.sort(key=lambda r: r.accuracy)
    return [
        {
            "feature": r.feature_key,
            "accuracy": round(r.accuracy, 3),
            "attempts": r.attempts,
        }
        for r in rows[:limit]
    ]


# --------------------------------------------------------------------------
# Liste de vocabulaire (onglet « Mots ») et validation en masse
# --------------------------------------------------------------------------
IMAGE_STATUSES = {4, 3}  # + « jamais vu » : voir show_image_for()


def show_image_for(status_row: "LemmaStatus | None") -> bool:  # noqa: D401
    """L'image ne s'affiche que tant que le mot est mal connu.

    Jamais rencontre, statut 4 ou statut 3. Au-dela, l'utilisateur le
    connait assez pour se passer du support visuel.
    """
    if status_row is None:
        return True
    if status_row.is_ignored:
        return False
    return status_row.status in IMAGE_STATUSES


def vocabulary(
    session: Session,
    user_id: int,
    *,
    query: str = "",
    status_filter: int | None = None,
    only_ignored: bool = False,
    with_image: bool | None = None,
    limit: int = 500,
) -> list[dict]:
    """Lemmes annotes par l'utilisateur, pour l'onglet « Mots »."""
    stmt = (
        select(LemmaStatus, Lemma)
        .join(Lemma, Lemma.id == LemmaStatus.lemma_id)
        .where(LemmaStatus.user_id == user_id)
    )

    if only_ignored:
        stmt = stmt.where(LemmaStatus.is_ignored.is_(True))
    else:
        stmt = stmt.where(LemmaStatus.is_ignored.is_(False))
    if status_filter is not None:
        stmt = stmt.where(LemmaStatus.status == status_filter)
    # L'image est desormais portee par le lemme, non par le statut.
    if with_image is True:
        stmt = stmt.where(Lemma.image_path.is_not(None))
    elif with_image is False:
        stmt = stmt.where(Lemma.image_path.is_(None))
    if query.strip():
        pattern = f"%{query.strip().lower()}%"
        stmt = stmt.where(
            func.lower(Lemma.lemma).like(pattern)
            | func.lower(func.coalesce(LemmaStatus.gloss, "")).like(pattern)
        )

    stmt = stmt.order_by(LemmaStatus.status.desc(), Lemma.lemma).limit(limit)
    rows = session.execute(stmt).all()

    # Les formes rencontrees sont chargees en une seule requete pour tous
    # les lemmes affiches. Une requete par ligne (« N+1 ») coutait 162
    # allers-retours pour 161 mots, alors qu'un seul suffit.
    lemma_ids = [lemma.id for _, lemma in rows]
    formes: dict[int, Counter] = defaultdict(Counter)
    textes: dict[int, set[int]] = defaultdict(set)
    if lemma_ids:
        observees = session.execute(
            select(
                TextToken.chosen_lemma_id,
                TextToken.surface,
                TextToken.text_id,
                func.count(),
            )
            .where(TextToken.chosen_lemma_id.in_(lemma_ids))
            .group_by(TextToken.chosen_lemma_id, TextToken.surface, TextToken.text_id)
        ).all()
        for lemma_id, surface, text_id, nombre in observees:
            formes[lemma_id][surface] += nombre
            textes[lemma_id].add(text_id)

    return [
        {
            "lemma": lemma,
            "status": status_row,
            "forms": formes[lemma.id].most_common(12),
            "occurrences": sum(formes[lemma.id].values()),
            "texts": sorted(textes[lemma.id]),
        }
        for status_row, lemma in rows
    ]


def status_counts(session: Session, user_id: int) -> dict:
    rows = session.execute(
        select(LemmaStatus.status, func.count())
        .where(LemmaStatus.user_id == user_id, LemmaStatus.is_ignored.is_(False))
        .group_by(LemmaStatus.status)
    ).all()
    counts = {s: c for s, c in rows}
    ignored = session.scalar(
        select(func.count()).select_from(LemmaStatus).where(
            LemmaStatus.user_id == user_id, LemmaStatus.is_ignored.is_(True)
        )
    )
    return {"by_status": counts, "ignored": ignored or 0}


def validate_unseen(
    session: Session, user_id: int, token_rows: list[TextToken], *, status: int = 0
) -> dict:
    """Marque d'un coup tous les lemmes encore absents de la base.

    Sert au lecteur qui connait deja une partie du vocabulaire : plutot
    que de cliquer un a un, il valide en bloc les mots « jamais vus » de
    la page. Les lemmes deja notes ne sont jamais touches, et les noms
    propres ou chiffres romains sont ecartes : les inclure fausserait les
    statistiques de couverture.
    """
    from .importer import is_auto_ignored

    candidate_ids: set[int] = set()
    for tok in token_rows:
        if not tok.is_word or not tok.chosen_lemma_id:
            continue
        lemma = session.get(Lemma, tok.chosen_lemma_id)
        if lemma is None or is_auto_ignored(lemma.upos, tok.surface):
            continue
        candidate_ids.add(tok.chosen_lemma_id)

    if not candidate_ids:
        return {"validated": 0, "lemma_ids": [], "token_ids": []}

    existing = {
        r.lemma_id
        for r in session.scalars(
            select(LemmaStatus).where(
                LemmaStatus.user_id == user_id,
                LemmaStatus.lemma_id.in_(candidate_ids),
            )
        ).all()
    }
    new_ids = candidate_ids - existing
    for lemma_id in new_ids:
        set_status(session, user_id, lemma_id, status=status, manual=True)

    token_ids = [
        t.id for t in token_rows if t.chosen_lemma_id in new_ids
    ]
    return {
        "validated": len(new_ids),
        "status": status,
        "lemma_ids": sorted(new_ids),
        "token_ids": token_ids,
    }


# --------------------------------------------------------------------------
# Menage : mots sans contenu
# --------------------------------------------------------------------------
def words_without_content(session: Session, user_id: int) -> list[LemmaStatus]:
    """Mots n'ayant ni traduction ni image.

    Une note seule ne suffit pas a retenir un mot : c'est la traduction
    ou l'image qui en fait une entree de vocabulaire exploitable.
    """
    # Ni traduction personnelle, ni glose partagee, ni image : le mot
    # n'apporte rien a l'utilisateur.
    rows = session.scalars(
        select(LemmaStatus)
        .join(Lemma, Lemma.id == LemmaStatus.lemma_id)
        .where(
            LemmaStatus.user_id == user_id,
            (LemmaStatus.gloss.is_(None) | (func.trim(LemmaStatus.gloss) == "")),
            (Lemma.shared_gloss.is_(None) | (func.trim(Lemma.shared_gloss) == "")),
            Lemma.image_path.is_(None),
        )
    ).all()
    return list(rows)


def summarize_without_content(session: Session, user_id: int) -> dict:
    """Apercu avant suppression, ventile par statut.

    Sert a montrer a l'utilisateur ce qu'il s'apprete a perdre : les mots
    valides en masse (statut 0, sans traduction) figurent dans ce lot.
    """
    rows = words_without_content(session, user_id)
    par_statut = Counter()
    ignores = 0
    avec_note = 0
    for row in rows:
        if row.is_ignored:
            ignores += 1
        else:
            par_statut[row.status] += 1
        if row.note and row.note.strip():
            avec_note += 1

    lemma_ids = [r.lemma_id for r in rows]
    cartes = 0
    if lemma_ids:
        from ..models import Card

        cartes = session.scalar(
            select(func.count()).select_from(Card).where(
                Card.user_id == user_id, Card.lemma_id.in_(lemma_ids)
            )
        ) or 0

    return {
        "total": len(rows),
        "by_status": dict(par_statut),
        "ignored": ignores,
        "with_note": avec_note,
        "cards": cartes,
    }


def purge_without_content(
    session: Session, user_id: int, *,
    keep_ignored: bool = True, keep_noted: bool = True,
) -> dict:
    """Supprime les mots sans traduction ni image.

    Par defaut on epargne deux categories, dont la suppression serait
    presque toujours une perte : les mots explicitement ignores (le
    reglage est une decision de l'utilisateur) et ceux portant une note.
    """
    from ..models import Card

    cibles = [
        row
        for row in words_without_content(session, user_id)
        if not (keep_ignored and row.is_ignored)
        and not (keep_noted and row.note and row.note.strip())
    ]
    if not cibles:
        return {"deleted": 0, "cards": 0, "lemma_ids": []}

    lemma_ids = [r.lemma_id for r in cibles]
    cartes = session.scalars(
        select(Card).where(Card.user_id == user_id, Card.lemma_id.in_(lemma_ids))
    ).all()
    for carte in cartes:
        session.delete(carte)
    for row in cibles:
        session.delete(row)
    session.flush()
    return {"deleted": len(cibles), "cards": len(cartes), "lemma_ids": lemma_ids}
