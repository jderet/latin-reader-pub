# Lecteur latin

Lecture annotée de textes latins, à la manière de Learning with Texts, mais avec la correction du défaut qui vous gênait : **le statut de connaissance porte sur le lemme, pas sur la forme fléchie**. Marquer *flumen* comme acquis colore aussi `flumine` et `fluminis`.

---

## Installation sur macOS

### Sans Docker (le plus simple)

```bash
cd latin-reader-pub
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python scripts/seed_demo.py     # charge De bello Gallico I, 1
.venv/bin/uvicorn app.main:app --port 8000
```

Puis <http://localhost:8000>.

### Avec Docker

```bash
docker compose up --build
docker compose exec web python scripts/seed_demo.py
```

L'image par défaut n'embarque **pas** Stanza : elle démarre en quelques secondes et fonctionne avec le lemmatiseur lexical embarqué. Pour la version contextuelle :

```bash
DOCKERFILE=Dockerfile.nlp docker compose up --build
```

Compter dix à vingt minutes de construction et environ 4 Go (PyTorch plus les modèles UD). Les modèles sont téléchargés à la construction de l'image, pas au démarrage du conteneur.

---

## Les trois moteurs de lemmatisation

La chaîne est `stanza → cltk → lexicon`, réglable par `LEMMATIZER_ORDER`. Le premier moteur disponible l'emporte, et le moteur retenu est enregistré avec chaque texte (`text.engine`), ce qui permet de relemmatiser le même passage avec un autre moteur et de comparer.

| Moteur | Contextuel | Installation | Remarque |
|---|---|---|---|
| `stanza` | oui | `pip install ".[stanza]"` puis `stanza.download('la', package='ittb')` | ~99 % sur la prose classique ; recommandé |
| `cltk` | **non** | `pip install ".[cltk]"` | `LatinBackoffLemmatizer` : dictionnaire, règles, modèle. Ne lit pas la phrase, donc ne distingue pas *sum* de *edo* sur `est` |
| `lexicon` | non | aucune | Lexique TSV embarqué plus règles de terminaison. Toujours disponible, sert de filet et rend les tests reproductibles |

Le point à retenir sur CLTK : ce n'est pas une alternative contextuelle à Stanza. Son pipeline latin moderne encapsule d'ailleurs Stanza. En repli, il est meilleur que rien, pas meilleur que Stanza.

### Préférences de désambiguïsation

`app/nlp/base.py` contient `LEMMA_PREFERENCES`. Conformément à votre consigne, `est`, `es`, `esse`, `sunt` sont tranchés en faveur de `sum`. La préférence ne s'applique que dans la zone d'ambiguïté : si un moteur contextuel est net, sa décision est respectée. C'est éditable à la main.

---

## L'échelle de statut

| Valeur | Sens | Rendu |
|---|---|---|
| 4 | inconnu | rouge |
| 3 | vaguement reconnu | orange |
| 2 | reconnu en contexte | jaune |
| 1 | presque acquis | vert pâle |
| 0 | maîtrisé | sans couleur |
| — | jamais rencontré | souligné pointillé |
| — | ignoré | italique, exclu des statistiques |

« Jamais rencontré » est l'absence de ligne en base, pas une sixième valeur : rien n'est écrit tant que vous n'avez pas cliqué. Les noms propres et les chiffres romains sont ignorés par défaut, réactivables individuellement.

**Transitions automatiques.** Une bonne révision fait descendre d'un cran, à condition que toutes les fiches actives du lemme aient atteint trois répétitions. Un échec fait remonter d'un cran, jamais au-delà de 3. Toute modification manuelle pose un verrou (`is_locked`) qui gèle l'automatisme jusqu'à déverrouillage explicite.

---

## Deux axes indépendants

Vous vouliez le statut au lemme *et* des exercices morphologiques. Ce sont deux dimensions séparées :

- `lemma_status` — un enregistrement par lemme, pilote la coloration et les statistiques de couverture ;
- `morph_skill` — un compteur par trait morphologique (`Case=Abl`, `Tense=Plup|Mood=Sub`), alimenté par les fiches à trous, n'affecte jamais la couleur du texte.

Sans cette séparation, il aurait fallu un statut par forme fléchie, c'est-à-dire l'explosion combinatoire que vous vouliez précisément éviter.

---

## Désambiguïsation

L'application n'impose jamais de file d'attente d'arbitrages : le texte est lisible immédiatement. Un token n'est signalé que si l'écart de score entre les deux meilleurs lemmes **distincts** est inférieur à `AMBIGUITY_MARGIN` (0,15, dans `app/services/importer.py`).

Deux analyses morphologiques du même lemme ne comptent pas comme une ambiguïté : `lingua` nominatif ou ablatif relève de `lingua` dans les deux cas, donc le choix n'a aucun effet sur la lecture.

Sur votre passage de César : **3 tokens signalés sur 178**, soit `quod` deux fois et `quam` une fois. `est` n'est pas signalé, sa marge valant 0,24.

Un arbitrage est mémorisé dans `disambiguation_override` et propagé à toutes les occurrences de la forme, dans tous les textes (portée `global`) ou dans le seul texte courant (case à cocher, portée `text`). Les overrides survivent à une relemmatisation.

---

## Raccourcis clavier du lecteur

| Touche | Effet |
|---|---|
| `4` `3` `2` `1` `0` | statut du lemme |
| `i` | ignorer |
| `f` | créer les fiches cochées |
| `Échap` | fermer le panneau |

En révision : `espace` révèle la réponse, `1` à `4` notent (à revoir, difficile, correct, facile).

---

## Fiches et révision

SM-2 fidèle à l'original, avec les garde-fous d'usage d'Anki : plancher de facilité à 1,3, plafond d'intervalle à 365 jours, échec ramenant l'intervalle à 1 jour et réinjectant la fiche en fin de session sans toucher à son échéance.

Trois types, créés sur action explicite : `la_fr`, `fr_la`, `cloze`. Une seule glose par lemme, comme demandé — la contrainte `UNIQUE (lemma_id, kind)` l'impose. Les contextes s'accumulent, plafonnés à cinq, le plus récent en tête.

`review_log` conserve intégralement l'historique (qualité, intervalles, facteurs avant et après). C'est ce qui rendrait possible, plus tard, de rejouer tout l'historique sous FSRS sans perte de données. Si vous voulez changer d'ordonnanceur un jour, ne purgez pas cette table.

---

## Dictionnaire

`data/dictionary.tsv`, au format `clé_lemme <TAB> vedette <TAB> glose`. Le fichier livré est une amorce d'une vingtaine d'entrées. Pour l'étoffer, convertissez une source au même format :

| Ressource | Licence | Usage gratuit | Usage commercial |
|---|---|---|---|
| Whitaker's Words | domaine public | oui | oui |
| Lewis & Short (Perseus) | CC BY-SA 3.0 | oui, attribution et partage à l'identique | oui |
| Gaffiot 2016 (G. Gréco) | CC BY-NC-SA | oui | **non** |

Suggestion de glose par LLM : facultative, inactive sans `ANTHROPIC_API_KEY`. Elle transmet le lemme et la phrase de contexte à un tiers, jamais le texte entier, et n'enregistre rien sans votre validation.

---

## Exports

- `/export/texts/{id}.conllu` — CoNLL-U ; colonnes syntaxiques à `_` (l'application n'analyse pas les dépendances), champ MISC portant `Margin`, `Resolved`, `Guessed`
- `/export/anki.csv` — point-virgule, avec contextes
- `/export/lemmas.csv` — lexique personnel avec statuts et gloses

---

## Structure

```
app/
  main.py              routes FastAPI
  models.py            schéma SQLAlchemy
  db.py                session, PRAGMA SQLite
  nlp/
    base.py            interface Lemmatizer, LEMMA_PREFERENCES, marge d'ambiguïté
    registry.py        chaîne de repli entre moteurs
    engines.py         adaptateurs Stanza et CLTK
    lexicon_engine.py  moteur embarqué
    normalize.py       u/v, i/j, macrons, enclitiques
  services/
    importer.py        lemmatisation et persistance
    disambiguation.py  arbitrage et propagation
    knowledge.py       statuts, couverture, axe morphologique
    cards.py           fiches, file, notation
    srs.py             SM-2 et transitions (fonctions pures)
    dictionary.py      dictionnaire local, suggestion LLM
    exporter.py        CoNLL-U, CSV
data/lexicon.tsv       lexique du moteur embarqué
data/dictionary.tsv    gloses
```

`srs.py` ne touche pas la base : les transitions de statut et l'algorithme SM-2 sont des fonctions pures, testables par table de vérité.

---

## Tests

```bash
.venv/bin/pytest -q     # 21 tests
```

Ils couvrent la normalisation, la détection d'ambiguïté, l'import complet du passage de César, la propagation d'un arbitrage, la couverture, l'export CoNLL-U, SM-2 (progression, échec, plancher, plafond), les transitions de statut et la création de fiches.

Le test central s'appelle `test_le_statut_porte_sur_le_lemme_pas_sur_la_forme`.

---

## Limites connues

**Les adaptateurs Stanza et CLTK n'ont pas pu être exécutés.** Ils ont été écrits contre les API publiques des deux bibliothèques, mais l'environnement de développement n'avait pas accès aux poids des modèles. Tout le reste a été exécuté et testé. Attendez-vous à une mise au point sur `app/nlp/engines.py` au premier lancement avec `LEMMATIZER_ORDER=stanza`.

**Le moteur lexical n'est pas contextuel.** Son lexique couvre le passage de César et un noyau courant ; au-delà, il retombe sur des règles de terminaison et devine. C'est un filet, pas une solution.

**Pas d'authentification.** L'application écoute sur `127.0.0.1`. Ne l'exposez pas sans ajouter au minimum une authentification et TLS.

**Pas de migrations.** Le schéma est créé par `create_all`. Dès que vous aurez des données auxquelles vous tenez, ajoutez Alembic avant de modifier `models.py`.

**Pagination à 500 tokens.** Suffisant pour un chapitre ; un livre entier collé d'un coup sera lent à l'import.

---

## Ce qui n'est pas fait

Import de fichiers, URL, TEI-XML. Traduction parallèle en regard. Scansion. Synthèse vocale. Exercices morphologiques générés — l'axe `morph_skill` est alimenté et consultable dans `/stats`, mais aucun exercice ne l'exploite encore.
