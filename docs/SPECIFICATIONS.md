# Lecteur latin — spécifications techniques

*Version du 5 août 2026 — état du code au commit `f6b18ac`.*

## 1. Objet

Application de lecture annotée de textes latins, dans la lignée de
Learning with Texts, avec une correction de fond : **le statut de
connaissance porte sur le lemme, pas sur la forme fléchie**. Marquer
*flumen* comme acquis colore aussi *flumine* et *fluminis*, dans tous les
textes.

Deux rôles :

- le **lecteur** lit, consulte le dictionnaire, marque son vocabulaire
  (statuts 4 → 0), crée des fiches de révision (SM-2) ;
- l'**administrateur** constitue la bibliothèque : import de textes,
  arbitrage des lemmes ambigus, traductions partagées, images, audio.
  Un administrateur peut basculer en « mode lecture » et devient alors un
  lecteur ordinaire, avec ses propres statuts.

## 2. Architecture actuelle

### 2.1 Pile

| Couche | Choix | Remarques |
|---|---|---|
| Serveur | FastAPI (Python ≥ 3.11) + Uvicorn | ~2 650 lignes dans `app/main.py`, 81 routes |
| Persistance | SQLAlchemy 2 + SQLite (WAL) | `DATABASE_URL` surchargeable (PostgreSQL possible mais non testé) |
| Gabarits | Jinja2, rendu serveur | 21 gabarits |
| Interactivité | Alpine.js (CDN) + JS vanilla | `reader.js` (~1 000 l.), `admin_reader.js`, etc. |
| Sessions | Cookie signé (itsdangerous), SameSite=Lax, 30 jours | pas de jetons API |
| Déploiement | Docker (4 Dockerfiles : base, local, nlp, render) | volume `DATA_DIR` |

### 2.2 Modèle de données (16 tables)

- **Comptes** : `User` (admin ou lecteur, mot de passe haché).
- **Lexique** : `Lemma` (vedette Gaffiot, homonymes distingués par
  indice), `Form`, `FormLemma` (couples forme→lemme attestés).
- **Bibliothèque** : `Book`, `TextDoc` (statut de traitement, moteur
  employé, pagination, vidéo/audio), `TextToken` (surface, candidats
  JSON, lemme retenu, enclitiques détachées).
- **Connaissance (par utilisateur)** : `LemmaStatus` (clé composite
  `user_id + lemma_id`, statut 0–4, verrou manuel, glose personnelle,
  note, image), `PageRead`, `MorphSkill` (axe morphologique).
- **Révision** : `Card` (la_fr, fr_la, cloze), `CardContext`,
  `ReviewLog` — algorithme SM-2 (`services/srs.py`), plafond
  d'intervalle 365 j, plancher de facilité 1,3.
- **Arbitrage** : `DisambiguationOverride` (portée globale ou par
  texte).
- **Réglages** : `Setting` par utilisateur (police, ligne, couleurs…).

### 2.3 Chaîne de lemmatisation

`stanza → cltk → lexicon`, ordre réglable (`LEMMATIZER_ORDER`), le
premier moteur disponible l'emporte ; le moteur retenu est enregistré sur
le texte pour permettre la relemmatisation comparative.

1. **stanza** (optionnel, ~4 Go avec PyTorch) : contextuel, ~99 % sur la
   prose classique.
2. **cltk** (optionnel) : non contextuel (`LatinBackoffLemmatizer`).
3. **lexicon** (toujours disponible, moteur des tests) :
   - lexique choisi à la main (`data/lexicon.tsv`) — porte les
     ambiguïtés que la fréquence écrase (est → sum *et* edo) ;
   - règles de terminaison (analyse morphologique) ;
   - puis `filter_invented_lemmas` confronte les candidats aux formes
     attestées (`form_lemma.tsv.gz`, 865 k tokens des treebanks UD) et à
     la nomenclature du Gaffiot (71 841 lemmes).

Normalisation commune : u/v et i/j unifiés (`form_key`), enclitiques
détachées (-que, -ne, -ue) avec liste d'exceptions.

### 2.4 Surface HTTP

Trois familles, toutes servies par le même processus :

- **Pages HTML** (~30) : rendu serveur complet, l'état vit en base.
- **API JSON** (`/api/…`, ~35) : mutations fines déclenchées par le JS
  (statuts, fiches, révisions, arbitrage, segments audio). Réponses
  ad hoc, pas de schéma publié, authentification par le cookie de
  session.
- **Fragments HTML** (`/panel/token/{id}`, `/admin/panel/token/{id}`) :
  le panneau latéral du lecteur est rendu côté serveur et injecté par
  `x-html` — un point clé pour la suite (cf. § 4.2).
- **Exports** : CoNLL-U par texte, CSV Anki, CSV lexique.

### 2.5 Données embarquées

`data/` (~7 Mo) : Gaffiot (gloses courtes + articles), table
forme→lemme, lexique manuel, dictionnaire de secours. Les médias
(images de lemmes, audio, couvertures) vivent dans `DATA_DIR` hors dépôt.

### 2.6 Tests

21 tests (`pytest`), moteur `lexicon` uniquement, DB jetable :
normalisation, ambiguïtés, import complet, propagation de l'arbitrage,
couverture, SM-2, transitions de statut, fiches, exports.

## 3. Invariants à préserver

Toute évolution (notamment multiplateforme) doit maintenir :

1. **Le statut porte sur le lemme.** Une seule ligne `LemmaStatus` par
   couple (utilisateur, lemme) ; jamais de statut par forme.
2. **L'ambiguïté est un état assumé.** Un mot à plusieurs lemmes reste
   signalé tant qu'un humain n'a pas tranché ; l'arbitrage admin se
   propage (globalement ou par texte) et éteint l'ambiguïté pour les
   lecteurs.
3. **Le moteur est traçable.** Chaque texte connaît le moteur et la
   version qui l'ont lemmatisé.
4. **Fonctionne sans modèle lourd.** Le moteur `lexicon` garantit une
   installation en secondes et des tests reproductibles.
5. **Le vocabulaire appartient au lecteur.** Le compte admin (en mode
   gestion) n'a ni statuts ni fiches ; les traductions partagées sont
   remplaçables par une glose personnelle, pas l'inverse.

## 4. Stratégie multiplateforme

### 4.1 Cible et principe directeur

Usages visés : lire sur tablette/téléphone (canapé, transports, salle de
classe), réviser ses fiches en mobilité, administrer depuis un poste.
La lecture et la révision sont les usages mobiles ; l'administration
peut rester une affaire d'écran large.

Principe : **le serveur reste la source de vérité** (lemmatisation,
Gaffiot, arbitrage y vivent), et on amène l'interface vers les
plateformes par étapes, sans réécriture big-bang. Trois phases, chacune
utile en soi.

### 4.2 Phase 1 — PWA installable (effort : faible)

Le site est déjà responsive ; en faire une Progressive Web App le rend
installable sur iOS, Android et desktop sans toucher à l'architecture.

- `manifest.webmanifest` (nom, icônes, `display: standalone`, thème).
- Service worker : cache statique (`style.css`, JS, polices) +
  stratégie *network-first* sur les pages de lecture, avec repli sur la
  dernière version vue — on peut relire un texte déjà ouvert sans
  réseau. Les mutations (statuts, révisions) exigent le réseau à ce
  stade.
- File d'attente locale optionnelle (IndexedDB) pour les mutations
  simples et idempotentes — `POST /api/lemmas/{id}/status`,
  `POST /api/reviews` — rejouées au retour du réseau. Ces deux routes
  couvrent l'essentiel de l'usage mobile réel.
- Icônes et écrans de démarrage ; audit Lighthouse PWA.

Limites : pas de distribution par les stores, pas de notifications
locales fiables sur iOS pour rappeler les révisions dues.

### 4.3 Phase 2 — API de première classe (effort : moyen, condition du reste)

Aujourd'hui l'API est un prolongement du JS de la page (réponses ad hoc,
fragments HTML, cookie de session). Pour tout client natif il faut :

1. **Contrat JSON versionné** (`/api/v1/…`) : schémas Pydantic de
   réponse (ils existent pour les entrées, pas pour les sorties),
   OpenAPI publié — FastAPI le génère déjà, il s'agit de le rendre
   fiable.
2. **Le panneau de mot en JSON.** `/panel/token/{id}` renvoie du HTML ;
   il faut une variante `/api/v1/tokens/{id}` renvoyant la structure
   (lemme, vedette, statut, candidats, gloses, article Gaffiot) et
   laisser chaque client la mettre en forme. C'est le principal point
   où le rendu serveur fuit dans l'interactivité.
3. **Authentification par jeton** (Bearer, longue durée, révocable par
   compte) à côté du cookie — les WebView et clients natifs gèrent mal
   les cookies de session tiers.
4. **Pagination et delta-sync** : `GET /api/v1/statuses?since=…` pour
   qu'un client récupère les changements sans tout retélécharger.

Livrable : le site actuel continue de fonctionner à l'identique ;
l'API v1 est couverte par des tests de contrat.

### 4.4 Phase 3 — clients natifs (effort : selon ambition)

Deux options compatibles avec un développeur seul :

**Option A — Capacitor (recommandée).** L'interface web actuelle est
embarquée dans une coquille native iOS/Android qui pointe vers le
serveur. Coût marginal une fois les phases 1–2 faites : on réutilise
100 % de l'UI, on gagne les stores, les notifications locales (rappel de
révisions dues, calculé depuis `GET /api/v1/reviews/due`), le partage de
fichiers (import d'un .txt vers la bibliothèque). Risque faible,
entretien quasi nul.

**Option B — client léger dédié à la révision.** Si l'usage mobile se
concentre sur les fiches, une petite app (Flutter ou React Native) qui
ne parle qu'à `/api/v1/cards` + `/api/v1/reviews` avec cache local
SQLite : révision entièrement hors-ligne, synchronisation au retour du
réseau (les révisions SM-2 sont datées, la fusion est simple : on
rejoue les `ReviewLog` dans l'ordre). C'est l'app la plus utile par
heure de développement si la lecture reste sur navigateur.

**Desktop.** La PWA installée couvre déjà macOS/Windows/Linux. Un
paquet « tout-en-un » (Tauri embarquant le serveur Python via
PyInstaller, base SQLite locale) est possible pour un usage solo sans
serveur, mais il crée un deuxième mode de déploiement à entretenir —
à ne faire que si la demande existe.

### 4.5 Ce qu'on ne fait pas (et pourquoi)

- **Porter la lemmatisation sur l'appareil.** Stanza + modèles = trop
  lourd ; le moteur `lexicon` serait portable mais dégraderait la
  qualité. L'import de texte reste une opération connectée.
- **Réécrire le front en SPA.** Le rendu serveur + Alpine est simple,
  rapide et testé ; la SPA n'apporterait rien que Capacitor n'offre
  déjà, au prix d'une réécriture complète.
- **Sync multi-serveurs / CRDT.** Un seul serveur de vérité par
  déploiement ; le hors-ligne se limite à une file de mutations
  idempotentes et datées, ce qui suffit à l'usage réel.

### 4.6 Ordre de marche proposé

| Étape | Contenu | Dépend de |
|---|---|---|
| 1 | Manifest + service worker + icônes (PWA) | — |
| 2 | File hors-ligne statuts + révisions | 1 |
| 3 | API v1 : schémas de sortie, jetons, panneau JSON | — (parallélisable) |
| 4 | Tests de contrat sur l'API v1 | 3 |
| 5 | Coquille Capacitor iOS/Android + notifications de révision | 1–4 |
| 6 | (option) client révision hors-ligne | 3–4 |

Chaque étape laisse l'application dans un état livrable ; rien
n'impose de s'engager au-delà de l'étape 2 pour en retirer déjà
l'essentiel de la valeur mobile.
