# Lecteur latin — coquille mobile (Capacitor)

Phase 3 du plan multiplateforme (`docs/SPECIFICATIONS.md`) : une
application iOS/Android qui charge l'interface web existante et lui
ajoute ce que le navigateur ne donne pas — la présence sur les stores
et les notifications locales de révision.

La coquille ne contient **aucune logique métier** : elle pointe vers le
serveur (`server.url` dans `capacitor.config.ts`), qui sert la PWA de
la phase 1 et l'API v1 de la phase 2.

## Construire

Prérequis : Node ≥ 18, puis Xcode (iOS) ou Android Studio.

```bash
cd mobile
npm install
npx cap add ios        # et/ou : npx cap add android
npx cap sync
npx cap open ios       # construit et signe depuis Xcode
```

Avant de construire, renseignez `server.url` :

- production : l'URL HTTPS de votre déploiement ;
- développement : `http://<ip-locale>:8000` avec `cleartext: true`
  (et le serveur lancé avec `--host 0.0.0.0`).

## Notifications de révision

`notifications.ts` contient la fonction à appeler au lancement et au
retour au premier plan : elle interroge `/api/v1/reviews/due` et
programme une notification locale pour le lendemain 9 h s'il reste des
fiches. Le jeton s'obtient une fois via `POST /api/v1/auth/token` et se
conserve dans le stockage sûr de la plateforme (Keychain / Keystore).

## Ce que la coquille n'essaie pas de faire

- Pas de logique hors-ligne propre : le service worker et la file
  d'attente de la PWA fonctionnent tels quels dans la WebView.
- Pas de rendu natif du texte : l'interface web est l'interface.
  Si un client natif dédié devient souhaitable (révision hors-ligne
  complète), il se construira sur l'API v1 — cf. l'option B des
  spécifications.
