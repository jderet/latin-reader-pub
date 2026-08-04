// Gestion des livres : couvertures et rattachement des chapitres.
(function () {
  async function poster(url, body) {
    const res = await fetch(url, { method: 'POST', body });
    if (!res.ok) { alert(`Échec (${res.status}) : ${await res.text()}`); return null; }
    return res.json();
  }

  document.addEventListener('change', async (e) => {
    if (!e.target.classList?.contains('cover-file')) return;
    const ligne = e.target.closest('.book-row');
    const fichier = e.target.files[0];
    if (!ligne || !fichier) return;
    const body = new FormData();
    body.append('file', fichier);
    if (await poster(`/api/admin/books/${ligne.dataset.book}/cover`, body)) location.reload();
  });

  // Les formulaires de rattachement passent par l'API : on reste sur la
  // page, ce qui permet d'en enchaîner plusieurs.
  document.addEventListener('submit', async (e) => {
    const form = e.target.closest('form.attach, form.detach');
    if (!form) return;
    e.preventDefault();
    if (await poster(form.action, new FormData(form))) location.reload();
  });

  console.info('[lecteur latin] books.js chargé');
})();
