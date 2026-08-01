// Version 3. Les boutons du panneau sont branches par DELEGATION : un seul
// ecouteur pose sur le document au chargement, qui remonte depuis l'element
// clique. Aucune dependance a $el ni au moment ou le panneau est injecte,
// donc plus de branchement qui echoue silencieusement.
const READER_VERSION = 10;

function reader() {
  // Les donnees viennent d'une variable JS et non d'un attribut HTML :
  // le JSON contient des guillemets doubles, qui cassaient l'attribut.
  const data = window.READER_DATA || {};
  return {
    textId: data.textId,
    page: data.page || 0,
    imageTargets: data.images || [],
    prefs: data.prefs || {},
    placed: [],
    layoutMode: 'banner',
    focusedLemma: null,
    unseenCount: data.unseenCount || 0,
    validating: false,
    root: null,
    panel: false,
    panelHtml: '',
    tokenId: null,
    lemmaId: null,

    init() {
      console.info(`[lecteur latin] reader.js v${READER_VERSION} chargé`);
      this.delegate();
      this.delegateImages();
      this.delegateHover();
      this.$nextTick(() => this.layoutImages());
      this.$watch('panel', () => this.$nextTick(() => this.layoutImages()));
      // Le texte se recompose au redimensionnement : les images doivent
      // suivre leur mot, donc on recalcule (en differé, pour ne pas
      // recalculer à chaque pixel).
      let timer = null;
      window.addEventListener('resize', () => {
        clearTimeout(timer);
        timer = setTimeout(() => this.layoutImages(), 150);
      });
      document.fonts?.ready?.then(() => this.layoutImages());
    },

    // Decide si les gouttieres tiennent, puis place chaque image en face
    // de son mot. Les images sont positionnees DANS le padding lateral de
    // la scene : elles ne peuvent donc jamais recouvrir le texte.
    layoutImages() {
      const stage = this.$refs?.stage || this.root?.querySelector('.text-stage');
      if (!stage) return;
      if (!this.imageTargets.length) { this.placed = []; this.layoutMode = ''; return; }

      const columns = Math.max(1, Math.min(3, this.prefs.image_columns || 2));
      const width = this.prefs.image_size || 92;
      const gap = 10;
      const gutter = columns * (width + gap) + 16;
      const minText = 300;

      // Assez de place pour deux gouttieres plus une colonne de texte
      // lisible ? Sinon, bandeau au-dessus du texte.
      const available = stage.parentElement.getBoundingClientRect().width;
      const fits = available >= 2 * gutter + minText;
      this.layoutMode = fits ? 'with-margins' : 'banner';
      if (!fits) {
        this.placed = this.imageTargets.map((img) => ({ ...img, side: 'left', offset: 0, top: 0 }));
        return;
      }

      const stageBox = stage.getBoundingClientRect();
      const heightOf = (tokenId) => {
        const fig = stage.querySelector(`.margin-image[data-for="${tokenId}"]`);
        const h = fig?.getBoundingClientRect().height;
        return h && h > 10 ? h : width + (this.prefs.image_captions ? 30 : 6);
      };

      const bottom = {
        left: new Array(columns).fill(-Infinity),
        right: new Array(columns).fill(-Infinity),
      };
      const out = [];

      this.imageTargets.forEach((img, index) => {
        const word = document.querySelector(`[data-token="${img.token_id}"]`);
        if (!word) return;
        const side = index % 2 === 0 ? 'left' : 'right';
        const wordTop = word.getBoundingClientRect().top - stageBox.top;
        const height = heightOf(img.token_id);

        let col = bottom[side].findIndex((b) => b + gap <= wordTop);
        let top = wordTop;
        if (col === -1) {
          col = bottom[side].indexOf(Math.min(...bottom[side]));
          top = bottom[side][col] + gap;
        }
        bottom[side][col] = top + height;

        // Colonne 0 la plus proche du texte, les suivantes vers l'exterieur.
        const offset = 8 + (columns - 1 - col) * (width + gap);
        out.push({ ...img, side, col, offset, top: Math.round(top) });
      });
      this.placed = out;
    },

    async validateUnseen() {
      const n = this.unseenCount;
      if (!n) return;
      if (!confirm(
        `Marquer ${n} mot(s) comme maîtrisés ?\n\n` +
        `Ce sont les mots en bleu de cette page, que vous n'avez jamais notés. ` +
        `Vous pourrez toujours les modifier ensuite.`
      )) return;

      this.validating = true;
      const out = await this.post(`/api/texts/${this.textId}/validate-unseen`, {
        page: this.page,
        status: 0,
      });
      this.validating = false;
      if (!out) return;

      out.token_ids.forEach((id) => {
        const el = document.querySelector(`[data-token="${id}"]`);
        if (el) { el.classList.remove('unseen'); el.classList.add('s0'); }
      });
      this.unseenCount = 0;
      this.layoutImages();
    },

    panelRoot() {
      return document.querySelector('.panel-inner');
    },

    async open(tokenId) {
      this.tokenId = tokenId;
      this.panel = true;
      this.panelHtml = '<p class="muted">Chargement…</p>';
      try {
        const res = await fetch(`/panel/token/${tokenId}`);
        this.panelHtml = await res.text();
      } catch (err) {
        this.panelHtml = `<p class="error">Impossible de charger le panneau : ${err}</p>`;
        return;
      }
      this.$nextTick(() => {
        const root = this.panelRoot();
        this.lemmaId = root ? root.dataset.lemma || null : null;
      });
    },

    // Un seul ecouteur, pose une fois. Il fonctionne quel que soit le
    // moment ou le contenu du panneau est remplace.
    delegate() {
      document.addEventListener('click', (e) => {
        const inPanel = e.target.closest('.panel-inner');
        if (!inPanel) return;

        const cand = e.target.closest('.cand');
        if (cand) { e.preventDefault(); this.resolve(cand.dataset.lemma); return; }

        const st = e.target.closest('.st');
        if (st) {
          e.preventDefault();
          if (st.dataset.ignore) this.setStatus(null, true);
          else this.setStatus(parseInt(st.dataset.status, 10), false);
          return;
        }

        if (e.target.closest('[data-unlock]')) { e.preventDefault(); this.unlock(); return; }
        if (e.target.closest('.save-gloss')) { e.preventDefault(); this.saveGloss(); return; }
        if (e.target.closest('.suggest')) { e.preventDefault(); this.suggestGloss(); return; }
        if (e.target.closest('.create-cards')) { e.preventDefault(); this.createCards(); return; }
        if (e.target.closest('.p-img-del')) { e.preventDefault(); this.deleteImage(); return; }
      });
    },

    // Image ajoutee depuis le panneau : fichier, glisser-deposer ou collage.
    // Elle est enregistree immediatement, sans bouton de validation.
    delegateImages() {
      document.addEventListener('change', (e) => {
        if (e.target.classList?.contains('p-img-file')) {
          this.uploadImage(e.target.files[0]);
        }
      });
      document.addEventListener('dragover', (e) => {
        if (e.target.closest?.('.panel-image')) e.preventDefault();
      });
      document.addEventListener('drop', (e) => {
        if (!e.target.closest?.('.panel-image')) return;
        e.preventDefault();
        this.uploadImage(e.dataTransfer?.files?.[0]);
      });
      document.addEventListener('paste', (e) => {
        if (!this.panel || !this.currentLemma()) return;
        const item = [...(e.clipboardData?.items || [])]
          .find((i) => i.type.startsWith('image/'));
        if (!item) return;
        e.preventDefault();
        const reader = new FileReader();
        reader.onload = () => this.uploadDataUrl(reader.result);
        reader.readAsDataURL(item.getAsFile());
      });
    },

    async uploadImage(file) {
      if (!file) return;
      const lemmaId = this.currentLemma();
      if (!lemmaId) return;
      const body = new FormData();
      body.append('file', file);
      const res = await fetch(`/api/lemmas/${lemmaId}/image`, { method: 'POST', body });
      if (!res.ok) { alert(await res.text()); return; }
      this.open(this.tokenId);
      location.reload();
    },

    async uploadDataUrl(dataUrl) {
      const lemmaId = this.currentLemma();
      if (!lemmaId) return;
      const body = new FormData();
      body.append('data_url', dataUrl);
      const res = await fetch(`/api/lemmas/${lemmaId}/image`, { method: 'POST', body });
      if (!res.ok) { alert(await res.text()); return; }
      location.reload();
    },

    async deleteImage() {
      const out = await this.post(`/api/lemmas/${this.currentLemma()}/image/delete`, {});
      if (out) location.reload();
    },

    delegateHover() {
      const lemmasWithImage = new Set(this.imageTargets.map((i) => String(i.lemma_id)));
      const body = this.root?.querySelector('.text-body');
      if (!body) return;

      body.addEventListener('mouseover', (e) => {
        const word = e.target.closest('.w');
        const lemma = word?.dataset.lemma;
        this.focusedLemma = lemma && lemmasWithImage.has(lemma) ? Number(lemma) : null;
      });
      body.addEventListener('mouseleave', () => { this.focusedLemma = null; });
    },

    async post(url, data) {
      const body = new FormData();
      Object.entries(data).forEach(([k, v]) => {
        if (v !== null && v !== undefined) body.append(k, v);
      });
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
    },

    currentLemma() {
      const root = this.panelRoot();
      return this.lemmaId || (root ? root.dataset.lemma : null);
    },

    // Un arbitrage repeint immediatement toutes les occurrences concernees.
    async resolve(lemmaId) {
      const scoped = document.querySelector('.scope-text');
      const out = await this.post(`/api/tokens/${this.tokenId}/resolve`, {
        lemma_id: lemmaId,
        scope: scoped && scoped.checked ? 'text' : 'global',
      });
      if (!out) return;
      out.updated_tokens.forEach((id) => {
        const el = document.querySelector(`[data-token="${id}"]`);
        if (el) { el.dataset.lemma = out.lemma_id; el.classList.remove('ambiguous'); }
      });
      this.open(this.tokenId);
    },

    async setStatus(status, ignore) {
      const lemmaId = this.currentLemma();
      if (!lemmaId) { alert('Aucun lemme associé à ce mot.'); return; }
      const out = await this.post(`/api/lemmas/${lemmaId}/status`, {
        status: status,
        is_ignored: ignore ? 'true' : null,
      });
      if (out) this.repaint(out);
    },

    async unlock() {
      const out = await this.post(`/api/lemmas/${this.currentLemma()}/status`, { unlock: 'true' });
      if (out) this.open(this.tokenId);
    },

    // Toutes les formes du lemme changent de couleur d'un coup : c'est le
    // comportement qui manque a Learning with Texts.
    repaint(out) {
      out.tokens.forEach((id) => {
        const el = document.querySelector(`[data-token="${id}"]`);
        if (!el) return;
        el.classList.remove('s0', 's1', 's2', 's3', 's4', 'unseen', 'ignored');
        if (out.is_ignored) el.classList.add('ignored');
        else el.classList.add(`s${out.status}`);
      });
      document.querySelectorAll('.panel-inner .st').forEach((b) => b.classList.remove('active'));
      const target = out.is_ignored
        ? document.querySelector('.panel-inner .st.ign')
        : document.querySelector(`.panel-inner .st.s${out.status}`);
      if (target) target.classList.add('active');
    },

    async saveGloss() {
      const box = document.querySelector('.panel-inner .gloss');
      const lemmaId = this.currentLemma();
      if (!box || !lemmaId) return;
      const out = await this.post(`/api/lemmas/${lemmaId}/status`, { gloss: box.value });
      if (!out) return;
      box.classList.add('saved');
      setTimeout(() => box.classList.remove('saved'), 800);
    },

    async suggestGloss() {
      const box = document.querySelector('.panel-inner .gloss');
      const ctx = document.querySelector('.panel-inner .context');
      const btn = document.querySelector('.panel-inner .suggest');
      if (btn) btn.disabled = true;
      const out = await this.post(`/api/lemmas/${this.currentLemma()}/suggest`, {
        context: ctx ? ctx.textContent.trim() : '',
      });
      if (btn) btn.disabled = false;
      if (out && box) box.value = out.gloss;
    },

    async createCards() {
      const kinds = [...document.querySelectorAll('.panel-inner .kind:checked')].map((c) => c.value);
      if (!kinds.length) { alert('Cochez au moins un type de fiche.'); return; }
      const box = document.querySelector('.panel-inner .gloss');
      const out = await this.post('/api/cards', {
        lemma_id: this.currentLemma(),
        kinds: kinds.join(','),
        token_id: this.tokenId,
        gloss: box ? box.value : '',
      });
      if (out) this.open(this.tokenId);
    },

    onKey(e) {
      if (['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
      if (e.key === 'Escape') { this.panel = false; return; }
      if (!this.panel) return;
      if ('01234'.includes(e.key)) { this.setStatus(parseInt(e.key, 10), false); e.preventDefault(); }
      if (e.key === 'i') this.setStatus(null, true);
      if (e.key === 'f') this.createCards();
    },

    pollStatus() {
      const timer = setInterval(async () => {
        const res = await fetch(`/api/texts/${this.textId}/status`);
        const data = await res.json();
        if (data.status === 'ready' || data.status === 'failed') {
          clearInterval(timer);
          location.reload();
        }
      }, 1500);
    },
  };
}
