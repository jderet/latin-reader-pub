// Espace d'administration : arbitrage des lemmes, gloses partagées, images.
// Délégation d'événements, comme ailleurs dans l'application.
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

  // --- arbitrage ---
  document.addEventListener('click', async (e) => {
    const btn = e.target.closest('.cand-btn');
    if (!btn) return;
    e.preventDefault();
    const item = btn.closest('[data-token]');
    const body = new FormData();
    body.append('lemma', btn.dataset.lemma);
    body.append('upos', btn.dataset.upos);
    body.append('scope', 'global');
    const out = await post(`/api/tokens/${item.dataset.token}/resolve-by-name`, body);
    if (!out) return;
    item.querySelectorAll('.cand-btn').forEach((b) => b.classList.remove('chosen'));
    btn.classList.add('chosen');
    item.classList.add('done');
    // La forme est réglée : on la retire de la file après un instant.
    setTimeout(() => item.remove(), 400);
  });

  // --- vocabulaire partagé ---
  document.addEventListener('click', async (e) => {
    const row = e.target.closest('.word-row');
    if (!row) return;

    if (e.target.closest('.word-line')) {
      const editor = row.querySelector('.word-edit');
      const opening = editor.hidden;
      document.querySelectorAll('.word-edit').forEach((el) => {
        if (el !== editor) { el.hidden = true; el.closest('.word-row').classList.remove('open'); }
      });
      editor.hidden = !opening;
      row.classList.toggle('open', opening);
      if (opening) row.querySelector('.a-gloss')?.focus();
      return;
    }

    if (e.target.closest('.a-save')) {
      e.preventDefault();
      const body = new FormData();
      body.append('gloss', row.querySelector('.a-gloss').value);
      const out = await post(`/api/admin/lemmas/${row.dataset.lemma}/gloss`, body);
      if (!out) return;
      row.querySelector('.word-gloss').textContent = out.gloss || '—';
      const fb = row.querySelector('.a-feedback');
      if (fb) { fb.textContent = 'enregistré'; setTimeout(() => (fb.textContent = ''), 1500); }
      return;
    }

    if (e.target.closest('.a-img-del')) {
      e.preventDefault();
      if (await post(`/api/lemmas/${row.dataset.lemma}/image/delete`, new FormData()))
        location.reload();
    }
  });

  async function upload(row, file) {
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    if (await post(`/api/lemmas/${row.dataset.lemma}/image`, body)) location.reload();
  }

  document.addEventListener('change', (e) => {
    if (!e.target.classList.contains('a-img-file')) return;
    const row = e.target.closest('.word-row');
    if (row) upload(row, e.target.files[0]);
  });

  document.addEventListener('dragover', (e) => {
    if (e.target.closest('.word-image')) e.preventDefault();
  });
  document.addEventListener('drop', (e) => {
    const zone = e.target.closest('.word-image');
    if (!zone) return;
    e.preventDefault();
    upload(zone.closest('.word-row'), e.dataTransfer?.files?.[0]);
  });

  let lastRow = null;
  document.addEventListener('click', (e) => {
    const row = e.target.closest('.word-row');
    if (row) lastRow = row;
  });
  document.addEventListener('paste', (e) => {
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
    if (!item || !lastRow) return;
    e.preventDefault();
    const reader = new FileReader();
    reader.onload = async () => {
      const body = new FormData();
      body.append('data_url', reader.result);
      if (await post(`/api/lemmas/${lastRow.dataset.lemma}/image`, body)) location.reload();
    };
    reader.readAsDataURL(item.getAsFile());
  });

  console.info('[lecteur latin] admin.js chargé');
})();
