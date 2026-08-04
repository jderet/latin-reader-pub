// Arbitrage dans le texte, au clavier.
//   ← →  passer d'un mot à plusieurs lemmes au suivant
//   ↑ ↓  choisir le lemme, enregistré aussitôt
//   i    marquer nom propre        s  corriger la segmentation
const ADMIN_READER_VERSION = 3;

function adminReader() {
  const data = window.ADMIN_READER || {};
  return {
    textId: data.textId,
    page: data.page || 0,
    root: null,
    panelHtml: '<p class="muted">Choisissez un mot, ou pressez →</p>',
    tokenId: null,
    lemmaIndex: 0,

    init() {
      console.info(`[lecteur latin] admin_reader.js v${ADMIN_READER_VERSION} chargé`);
      this.delegate();
      // On se place d'emblée sur le premier mot à trancher.
      const premier = this.ambiguous()[0];
      if (premier) this.select(Number(premier.dataset.token), true);
    },

    // Tous les mots offrant plusieurs lemmes, arbitrés ou non : revenir
    // sur un choix doit rester possible.
    ambiguous() {
      return [...(this.root?.querySelectorAll('.text-body .w[data-multi="1"]') || [])];
    },

    async select(tokenId, scroll = false) {
      this.tokenId = tokenId;
      this.root?.querySelectorAll('.text-body .w.current')
        .forEach((w) => w.classList.remove('current'));
      const el = document.querySelector(`.text-body [data-token="${tokenId}"]`);
      if (el) {
        el.classList.add('current');
        if (scroll && typeof el.scrollIntoView === 'function') {
          try { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch {}
        }
      }
      try {
        const res = await fetch(`/admin/panel/token/${tokenId}`);
        this.panelHtml = await res.text();
      } catch (err) {
        this.panelHtml = `<p class="error">Panneau indisponible : ${err}</p>`;
        return;
      }
      this.$nextTick(() => {
        // On se cale sur le lemme actuellement retenu.
        const liste = this.candidates();
        const choisi = liste.findIndex((li) => li.classList.contains('chosen'));
        this.lemmaIndex = choisi === -1 ? 0 : choisi;
        this.highlight();
      });
    },

    candidates() {
      return [...document.querySelectorAll('.admin-candidates li')];
    },

    highlight() {
      this.candidates().forEach((li, i) => li.classList.toggle('active', i === this.lemmaIndex));
    },

    move(step) {
      const mots = this.ambiguous();
      if (!mots.length) return;
      const position = mots.findIndex((w) => Number(w.dataset.token) === this.tokenId);
      const cible = position === -1
        ? (step > 0 ? 0 : mots.length - 1)
        : Math.min(mots.length - 1, Math.max(0, position + step));
      this.select(Number(mots[cible].dataset.token), true);
    },

    // Le choix est enregistré sans validation : la flèche suffit.
    async chooseLemma(step) {
      const liste = this.candidates();
      if (liste.length < 2) return;
      this.lemmaIndex = Math.min(liste.length - 1, Math.max(0, this.lemmaIndex + step));
      this.highlight();
      const bouton = liste[this.lemmaIndex].querySelector('.cand');
      if (bouton) await this.applyLemma(bouton.dataset.lemma, bouton.dataset.upos);
    },

    async post(url, data) {
      const body = new FormData();
      Object.entries(data || {}).forEach(([k, v]) => body.append(k, v));
      let res;
      try {
        res = await fetch(url, { method: 'POST', body });
      } catch (err) {
        alert(`Le serveur ne répond pas : ${err}`);
        return null;
      }
      if (!res.ok) { alert(`Échec (${res.status}) : ${await res.text()}`); return null; }
      return res.json();
    },

    async applyLemma(lemma, upos) {
      const out = await this.post(`/api/admin/tokens/${this.tokenId}/lemma`, { lemma, upos });
      if (!out) return;
      const mot = document.querySelector(`.text-body [data-token="${this.tokenId}"]`);
      if (mot) { mot.classList.add('resolved'); mot.classList.remove('flagged'); mot.title = out.lemma; }
      document.querySelectorAll('.admin-candidates li').forEach((li) => {
        li.classList.toggle(
          'chosen',
          li.querySelector('.cand')?.dataset.lemma === out.lemma
        );
      });
      const panneau = document.querySelector('.admin-panel');
      if (panneau) panneau.dataset.lemma = out.lemma_id;
    },

    delegate() {
      document.addEventListener('click', async (e) => {
        const panneau = e.target.closest('.admin-panel');
        if (!panneau) return;

        const cand = e.target.closest('.cand');
        if (cand) {
          e.preventDefault();
          const li = cand.closest('li');
          this.lemmaIndex = Number(li.dataset.index || 0);
          this.highlight();
          await this.applyLemma(cand.dataset.lemma, cand.dataset.upos);
          return;
        }
        if (e.target.closest('.set-lemma')) { e.preventDefault(); this.applyFreeLemma(); return; }
        if (e.target.closest('.save-shared')) { e.preventDefault(); this.saveShared(); return; }
        if (e.target.closest('.toggle-ignore')) { e.preventDefault(); this.toggleIgnore(); return; }
        if (e.target.closest('.split-token')) { e.preventDefault(); this.split(); return; }
        if (e.target.closest('.merge-token')) { e.preventDefault(); this.merge(); return; }
        if (e.target.closest('.toggle-word')) { e.preventDefault(); this.toggleWord(); return; }
        if (e.target.closest('.img-del')) {
          e.preventDefault();
          const id = panneau.dataset.lemma;
          if (id && await this.post(`/api/lemmas/${id}/image/delete`)) location.reload();
        }
      });

      document.addEventListener('change', (e) => {
        if (e.target.classList?.contains('img-file')) this.uploadImage(e.target.files[0]);
      });
      document.addEventListener('dragover', (e) => {
        if (e.target.closest?.('.admin-image')) e.preventDefault();
      });
      document.addEventListener('drop', (e) => {
        if (!e.target.closest?.('.admin-image')) return;
        e.preventDefault();
        this.uploadImage(e.dataTransfer?.files?.[0]);
      });
      document.addEventListener('paste', (e) => {
        const item = [...(e.clipboardData?.items || [])].find((i) => i.type.startsWith('image/'));
        if (!item) return;
        e.preventDefault();
        const reader = new FileReader();
        reader.onload = () => this.uploadDataUrl(reader.result);
        reader.readAsDataURL(item.getAsFile());
      });
    },

    // Impose un lemme absent de la liste des candidats.
    async applyFreeLemma() {
      const champ = document.querySelector('.lemma-name');
      const nom = (champ?.value || '').trim();
      if (!nom) return;
      const upos = document.querySelector('.lemma-upos')?.value || 'X';
      await this.applyLemma(nom, upos);
      this.select(this.tokenId);
    },

    lemmaId() {
      return document.querySelector('.admin-panel')?.dataset.lemma || null;
    },

    async saveShared() {
      const zone = document.querySelector('.shared-gloss');
      const id = this.lemmaId();
      if (!zone || !id) return;
      if (await this.post(`/api/admin/lemmas/${id}/gloss`, { gloss: zone.value })) {
        zone.classList.add('saved');
        setTimeout(() => zone.classList.remove('saved'), 800);
      }
    },

    async toggleIgnore() {
      const id = this.lemmaId();
      if (!id) return;
      const out = await this.post(`/api/admin/lemmas/${id}/ignore`);
      if (out) this.select(this.tokenId);
    },

    async uploadImage(file) {
      const id = this.lemmaId();
      if (!file || !id) return;
      const body = new FormData();
      body.append('file', file);
      const res = await fetch(`/api/lemmas/${id}/image`, { method: 'POST', body });
      if (!res.ok) { alert(await res.text()); return; }
      this.select(this.tokenId);
    },

    async uploadDataUrl(dataUrl) {
      const id = this.lemmaId();
      if (!id) return;
      if (await this.post(`/api/lemmas/${id}/image`, { data_url: dataUrl })) {
        this.select(this.tokenId);
      }
    },

    // Rattrape un token que le découpeur avait écarté, ou l'écarte.
    async toggleWord() {
      const out = await this.post(`/api/admin/tokens/${this.tokenId}/word`);
      if (!out) return;
      const el = document.querySelector(`[data-token="${this.tokenId}"]`);
      if (el) {
        el.classList.toggle('punct', !out.is_word);
        el.classList.toggle('w', out.is_word);
        el.title = out.lemma || '';
      }
      this.select(this.tokenId);
    },

    // La segmentation modifie la numérotation des tokens : on recharge.
    async split() {
      if (await this.post(`/api/admin/tokens/${this.tokenId}/split`)) location.reload();
    },
    async merge() {
      if (await this.post(`/api/admin/tokens/${this.tokenId}/merge`)) location.reload();
    },

    onKey(e) {
      if (['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;

      const touches = {
        ArrowRight: () => this.move(1),
        ArrowLeft: () => this.move(-1),
        ArrowDown: () => this.chooseLemma(1),
        ArrowUp: () => this.chooseLemma(-1),
        i: () => this.toggleIgnore(),
        s: () => (document.querySelector('.split-token') ? this.split() : this.merge()),
        m: () => this.toggleWord(),
      };
      if (touches[e.key]) { e.preventDefault(); touches[e.key](); }
    },
  };
}
