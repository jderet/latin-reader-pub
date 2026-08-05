/* File d'attente hors-ligne.

   Les mutations simples et idempotentes — statut d'un lemme, révision
   d'une fiche, page lue — sont mises en attente quand le réseau manque,
   puis rejouées dans l'ordre au retour de la connexion. Le service
   worker ne cache jamais les POST : cette file est le seul mécanisme
   hors-ligne d'écriture.

   Chaque entrée : { url, data, ts }. L'horodatage n'est pas envoyé au
   serveur : les statuts sont des écritures « dernier état gagne » et
   les révisions sont datées côté serveur à la réception — un décalage
   de quelques heures est sans effet sur SM-2 à l'échelle du jour.
*/
(function () {
  const CLE = 'lecteur-latin-attente';

  function lire() {
    try {
      return JSON.parse(localStorage.getItem(CLE) || '[]');
    } catch {
      return [];
    }
  }

  function ecrire(entrees) {
    localStorage.setItem(CLE, JSON.stringify(entrees));
    signaler();
  }

  function signaler() {
    const n = lire().length;
    document.querySelectorAll('.offline-badge').forEach((el) => {
      el.textContent = n ? `${n} action(s) en attente de réseau` : '';
      el.hidden = !n;
    });
  }

  async function envoyer(entree) {
    const body = new FormData();
    Object.entries(entree.data).forEach(([k, v]) => {
      if (v !== null && v !== undefined) body.append(k, v);
    });
    const res = await fetch(entree.url, { method: 'POST', body });
    // Une réponse du serveur, même en erreur, vide l'entrée : la
    // rejouer indéfiniment ne la rendra pas meilleure. Seule une panne
    // de réseau (fetch qui lève) justifie de réessayer plus tard.
    return res;
  }

  window.fileAttente = {
    push(url, data) {
      const entrees = lire();
      entrees.push({ url, data, ts: Date.now() });
      ecrire(entrees);
    },

    async vider() {
      let entrees = lire();
      while (entrees.length) {
        try {
          await envoyer(entrees[0]);
        } catch {
          ecrire(entrees);
          return; // toujours hors-ligne : on garde le reste
        }
        entrees = entrees.slice(1);
        ecrire(entrees);
      }
    },

    taille() {
      return lire().length;
    },
  };

  window.addEventListener('online', () => window.fileAttente.vider());
  window.addEventListener('DOMContentLoaded', () => {
    signaler();
    if (navigator.onLine) window.fileAttente.vider();
  });
})();
