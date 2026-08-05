/* Rappel des révisions dues — à intégrer dans la coquille native.

   La logique : au lancement (et à chaque retour au premier plan),
   interroger /api/v1/reviews/due avec le jeton du lecteur, et
   programmer une notification locale pour le lendemain matin s'il
   restera des fiches dues. Aucune infrastructure de push : tout est
   local à l'appareil, fidèle à l'esprit de l'application.

   Prérequis : le jeton est obtenu une fois via /api/v1/auth/token
   (écran de connexion natif ou WebView) et conservé dans le stockage
   sûr de la plateforme.
*/
import { LocalNotifications } from '@capacitor/local-notifications';

const SERVEUR = 'https://lecteur-latin.example.org';

export async function programmerRappel(jeton: string): Promise<void> {
  const res = await fetch(`${SERVEUR}/api/v1/reviews/due`, {
    headers: { Authorization: `Bearer ${jeton}` },
  });
  if (!res.ok) return;
  const fiches: unknown[] = await res.json();
  if (!fiches.length) return;

  const demain = new Date();
  demain.setDate(demain.getDate() + 1);
  demain.setHours(9, 0, 0, 0);

  await LocalNotifications.requestPermissions();
  await LocalNotifications.schedule({
    notifications: [
      {
        id: 1,
        title: 'Lecteur latin',
        body: `${fiches.length} fiche(s) à réviser.`,
        schedule: { at: demain },
      },
    ],
  });
}
