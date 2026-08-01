// Edition libre du recto et du verso des fiches. Delegation d'evenements.
(function () {
  async function post(url, body) {
    let res;
    try {
      res = await fetch(url, { method: 'POST', body });
    } catch (err) {
      alert(`Le serveur ne répond pas : ${err}`);
      return null;
    }
    if (!res.ok) {
      alert(`Échec (${res.status}) : ${await res.text()}`);
      return null;
    }
    return res.json();
  }

  document.addEventListener('click', async (e) => {
    const row = e.target.closest('.card-row');
    if (!row) return;
    const id = row.dataset.card;

    if (e.target.closest('.c-save')) {
      e.preventDefault();
      const body = new FormData();
      body.append('front', row.querySelector('.c-front').value);
      body.append('back', row.querySelector('.c-back').value);
      if (row.querySelector('.c-reset').checked) body.append('reset_schedule', 'true');
      const out = await post(`/api/cards/${id}/edit`, body);
      const fb = row.querySelector('.c-feedback');
      if (out && fb) {
        fb.textContent = 'enregistré';
        setTimeout(() => (fb.textContent = ''), 1500);
      }
      return;
    }

    if (e.target.closest('.c-suspend')) {
      e.preventDefault();
      if (await post(`/api/cards/${id}/suspend`, new FormData())) location.reload();
      return;
    }

    if (e.target.closest('.c-delete')) {
      e.preventDefault();
      if (!confirm('Supprimer définitivement cette fiche ?')) return;
      if (await post(`/api/cards/${id}/delete`, new FormData())) location.reload();
    }
  });

  console.info('[lecteur latin] cards.js chargé');
})();
