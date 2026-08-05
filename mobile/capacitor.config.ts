import type { CapacitorConfig } from '@capacitor/cli';

/* La coquille charge l'interface web servie par le serveur : on
   réutilise 100 % de l'existant (PWA comprise) et on gagne les stores
   et les notifications locales. Remplacez `server.url` par l'adresse
   de votre déploiement. En développement, l'IP locale de la machine
   qui exécute uvicorn convient (http autorisé par cleartext). */
const config: CapacitorConfig = {
  appId: 'org.lecteurlatin.app',
  appName: 'Lecteur latin',
  webDir: 'www',
  server: {
    url: 'https://lecteur-latin.example.org',
    cleartext: false,
  },
};

export default config;
