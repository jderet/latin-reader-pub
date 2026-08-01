// Onglet « Mots », affichage compact.
// Une ligne par mot ; le clic déplie le formulaire d'édition.
// Délégation d'événements : un seul écouteur quel que soit le nombre de lignes.
(function () {
  const WORDS_VERSION = 4;
  let lastRow = null;

  const rowOf = (el) => el.closest('.word-row');

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

  function feedback(row, text) {
    const el = row.querySelector('.w-feedback');
    if (!el) return;
    el.textContent = text;
    setTimeout(() => (el.textContent = ''), 1500);
  }

  function toggle(row) {
    const editor = row.querySelector('.word-edit');
    if (!editor) return;
    const opening = editor.hidden;
    document.querySelectorAll('.word-edit').forEach((e) => {
      if (e !== editor) { e.hidden = true; rowOf(e).classList.remove('open'); }
    });
    editor.hidden = !opening;
    row.classList.toggle('open', opening);
    if (opening) row.querySelector('.w-gloss')?.focus();
  }

  async function save(row) {
    const body = new FormData();
    body.append('gloss', row.querySelector('.w-gloss').value);
    body.append('note', row.querySelector('.w-note').value);
    const out = await post(`/api/lemmas/${row.dataset.lemma}/details`, body);
    if (!out) return;
    row.querySelector('.word-gloss').textContent = out.gloss || '—';
    feedback(row, 'enregistré');
  }

  async function setStatus(row, status, ignore) {
    const body = new FormData();
    if (status !== null) body.append('status', status);
    if (ignore) body.append('is_ignored', 'true');
    const out = await post(`/api/lemmas/${row.dataset.lemma}/status`, body);
    if (!out) return;
    const chip = row.querySelector('.chip');
    chip.className = `chip s${out.status}`;
    chip.textContent = out.status;
    row.querySelectorAll('.st').forEach((b) => b.classList.remove('active'));
    const target = out.is_ignored
      ? row.querySelector('.st.ign')
      : row.querySelector(`.st.s${out.status}`);
    target?.classList.add('active');
    feedback(row, 'statut mis à jour');
  }

  // L'image s'enregistre immédiatement, sans passer par « Enregistrer ».
  async function uploadFile(row, file) {
    if (!file) return;
    const body = new FormData();
    body.append('file', file);
    feedback(row, 'envoi de l’image…');
    if (await post(`/api/lemmas/${row.dataset.lemma}/image`, body)) location.reload();
  }

  async function uploadDataUrl(row, dataUrl) {
    const body = new FormData();
    body.append('data_url', dataUrl);
    feedback(row, 'envoi de l’image…');
    if (await post(`/api/lemmas/${row.dataset.lemma}/image`, body)) location.reload();
  }

  document.addEventListener('click', async (e) => {
    const row = rowOf(e.target);
    if (!row) return;
    lastRow = row;

    if (e.target.closest('.word-line')) { toggle(row); return; }
    if (e.target.closest('.w-save')) { e.preventDefault(); save(row); return; }

    const st = e.target.closest('.st');
    if (st) {
      e.preventDefault();
      if (st.dataset.ignore) setStatus(row, null, true);
      else setStatus(row, parseInt(st.dataset.status, 10), false);
      return;
    }

    if (e.target.closest('.w-img-del')) {
      e.preventDefault();
      if (await post(`/api/lemmas/${row.dataset.lemma}/image/delete`, new FormData()))
        location.reload();
      return;
    }

    if (e.target.closest('.w-forget')) {
      e.preventDefault();
      const lemma = row.querySelector('.word-lemma').textContent;
      if (!confirm(
        `Supprimer « ${lemma} » de votre liste ?\n\n` +
        `Son statut, sa traduction, sa note, son image et ses fiches seront perdus. ` +
        `Le mot réapparaîtra en bleu dans les textes.`
      )) return;
      if (await post(`/api/lemmas/${row.dataset.lemma}/forget`, new FormData()))
        row.remove();
    }
  });

  document.addEventListener('change', (e) => {
    if (!e.target.classList.contains('w-img-file')) return;
    const row = rowOf(e.target);
    if (row) uploadFile(row, e.target.files[0]);
  });

  document.addEventListener('drop', (e) => {
    const zone = e.target.closest('.word-image');
    if (!zone) return;
    e.preventDefault();
    const row = rowOf(zone);
    const file = e.dataTransfer?.files?.[0];
    if (row && file) uploadFile(row, file);
  });
  document.addEventListener('dragover', (e) => {
    if (e.target.closest('.word-image')) e.preventDefault();
  });

  document.addEventListener('paste', (e) => {
    const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
    if (!item) return;
    if (!lastRow) { alert('Cliquez d’abord sur le mot auquel rattacher cette image.'); return; }
    e.preventDefault();
    const reader = new FileReader();
    reader.onload = () => uploadDataUrl(lastRow, reader.result);
    reader.readAsDataURL(item.getAsFile());
  });

  document.addEventListener('keydown', (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 's') {
      const row = rowOf(e.target) || lastRow;
      if (row) { e.preventDefault(); save(row); }
    }
    if (e.key === 'Escape') {
      document.querySelectorAll('.word-edit').forEach((el) => {
        el.hidden = true;
        rowOf(el).classList.remove('open');
      });
    }
  });

  console.info(`[lecteur latin] words.js v${WORDS_VERSION} chargé`);
})();
