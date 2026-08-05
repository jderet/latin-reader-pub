// Version 3. Les boutons du panneau sont branches par DELEGATION : un seul
// ecouteur pose sur le document au chargement, qui remonte depuis l'element
// clique. Aucune dependance a $el ni au moment ou le panneau est injecte,
// donc plus de branchement qui echoue silencieusement.
const READER_VERSION = 28;

function reader() {
  // Les donnees viennent d'une variable JS et non d'un attribut HTML :
  // le JSON contient des guillemets doubles, qui cassaient l'attribut.
  const data = window.READER_DATA || {};
  return {
    textId: data.textId,
    page: data.page || 0,
    imageTargets: data.images || [],
    prefs: data.prefs || {},
    videoId: data.videoId || null,
    audioName: data.audio || null,
    cues: data.cues || [],
    placed: [],
    layoutMode: 'inline',
    focusedLemma: null,
    selected: null,
    restoreTimer: null,
    // `videoId`, `audioName` et `cues` sont initialises plus haut depuis
    // READER_DATA. Les redeclarer ici les remettait a vide : dans un
    // objet JavaScript, la derniere cle l'emporte, sans le moindre
    // avertissement. Les segments n'atteignaient donc jamais le script.
    audioSpeed: '1',
    loopSegment: false,
    audioTimer: null,
    frame: null,
    playerReady: false,
    // Le defilement suit la lecture, sans reglage : le double-clic
    // sur un mot suffit a reprendre la video ou ailleurs.
    follow: true,
    activeCue: -1,
    cueTimer: null,
    unseenCount: data.unseenCount || 0,
    pageRead: Boolean(data.pageRead),
    validating: false,
    root: null,
    panel: false,
    panelRequest: 0,
    panelFading: false,
    panelHtml: '',
    tokenId: null,
    lemmaId: null,

    init() {
      console.info(`[lecteur latin] reader.js v${READER_VERSION} chargé`);
      this.delegate();
      this.delegateImages();
      this.delegateHover();
      this.delegateOutsideClick();
      this.delegateDoubleClick();
      // Meme precaution que pour l'audio : l'iframe doit exister.
      if (this.videoId) this.$nextTick(() => this.setupPlayer());
      // `$refs` n'est renseigné qu'une fois les enfants parcourus :
      // appelé tout de suite, setupAudio ne trouvait pas le lecteur et
      // n'attachait aucun écouteur — d'où l'absence de suivi.
      if (this.audioName) this.$nextTick(() => this.setupAudio());
      this.$nextTick(() => this.layoutImages());
      // Aucun recalcul a l'ouverture du panneau : il vit dans une marge
      // reservee en permanence, si bien que le texte ne bouge pas.
      // C'est ce recalcul qui redimensionnait la colonne a chaque clic.
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

      const columns = Math.max(1, Math.min(3, this.prefs.image_columns || 2));
      const width = this.prefs.image_size || 92;
      const gap = 10;

      // Le script ne mesure plus aucune largeur : c'est la feuille de
      // style qui décide, à partir des mêmes trois mesures que la mise en
      // page. Elle met les gouttières à zéro quand la place manque, et
      // l'on se contente de lire sa décision. Mesurer ici, comme
      // auparavant, revenait à trancher deux fois — d'où des largeurs
      // imprévisibles.
      const marge = parseFloat(
        getComputedStyle(this.root).getPropertyValue('--marge')
      ) || 0;
      const fits = marge > 0;
      this.layoutMode = fits ? 'with-margins' : 'inline';

      // Le mode se calcule toujours, meme sans image : c'est lui qui
      // decide aussi de la place du panneau. L'oublier ici laissait le
      // panneau en colonne sur un texte non illustre, donc invisible sur
      // ecran etroit.
      if (!this.imageTargets.length) {
        this.placed = [];
        this.root?.querySelectorAll('.inline-slot.for-image').forEach((n) => n.remove());
        if (fits) this.restorePanel();
        return;
      }

      if (!fits) {
        // Colonne étroite : les images ne tiennent pas dans une marge.
        // On les place dans le flux, sous la ligne de leur mot. Alpine ne
        // les gère plus (`placed` reste vide) : ces nœuds sont construits
        // à la main, car ils vivent au milieu du texte.
        this.placed = [];
        this.renderInlineImages();
        return;
      }
      this.clearInline();

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
        const word = this.wordEl(img.token_id);
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
        // On memorise le bord INTERIEUR, cote texte. L'image est ancree
        // par lui, si bien qu'un agrandissement s'etend vers l'exterieur
        // et ne peut jamais recouvrir le texte.
        const inner = 8 + (columns - 1 - col) * (width + gap) + width;
        out.push({ ...img, side, col, inner, top: Math.round(top) });
      });
      this.placed = out;
    },

    // Annule la selection : plus de cadre, plus d'info-bulle, panneau
    // referme. Le mot selectionne et le panneau vont de pair.
    deselect() {
      this.root?.querySelectorAll('.text-body .w.selected')
        .forEach((w) => w.classList.remove('selected', 'tip-left', 'tip-right'));
      this.selected = null;
      this.focusedLemma = null;
      this.panel = false;
      // La remise en place deplace le panneau dans le document : la faire
      // tout de suite couperait la transition de fermeture.
      clearTimeout(this.restoreTimer);
      this.restoreTimer = setTimeout(() => this.restorePanel(), 220);
    },

    // --- enregistrement associé ---

    // Un texte peut avoir un enregistrement sans être aligné : il reste
    // alors lisible, avec un simple lecteur et aucune surbrillance.
    get aligned() {
      return this.alignedCues.length > 0;
    },

    // Une seance de frappe interrompue laisse la fin du texte sans
    // bornes : on surligne ce qui est aligne, et rien au-dela.
    get alignedCues() {
      return this.cues.filter((c) => typeof c.start === 'number');
    },

    get alignedCount() {
      return this.alignedCues.length;
    },

    audioEl() {
      // On retombe sur une recherche directe : plus sûr qu'une référence
      // Alpine, dont le moment de disponibilité dépend du parcours.
      return this.$refs?.audio || this.root?.querySelector('audio') || null;
    },

    setupAudio() {
      const lecteur = this.audioEl();
      if (!lecteur) return;
      lecteur.addEventListener('play', () => this.startAudioFollow());
      lecteur.addEventListener('pause', () => this.stopAudioFollow());
      lecteur.addEventListener('timeupdate', () => this.syncAudio());
      lecteur.addEventListener('seeked', () => this.syncAudio());
      lecteur.addEventListener('loadedmetadata', () => this.syncAudio());
      // Position initiale : le premier segment se marque avant meme la
      // premiere lecture, pour montrer que l'alignement existe.
      this.syncAudio();
      lecteur.addEventListener('ended', () => {
        this.stopAudioFollow();
        this.highlightCue(-1);
      });
    },

    applyAudioSpeed() {
      const lecteur = this.audioEl();
      if (lecteur) lecteur.playbackRate = Number(this.audioSpeed) || 1;
    },

    startAudioFollow() {
      this.stopAudioFollow();
      if (!this.aligned) return;
      this.audioTimer = setInterval(() => this.syncAudio(), 120);
    },

    stopAudioFollow() {
      if (this.audioTimer) { clearInterval(this.audioTimer); this.audioTimer = null; }
    },

    syncAudio() {
      const lecteur = this.audioEl();
      if (!lecteur) return;
      const temps = lecteur.currentTime;

      // Répétition : on revient au début dès la fin du segment courant.
      if (this.loopSegment && this.activeCue >= 0) {
        const courant = this.cues[this.activeCue];
        if (courant && temps >= courant.end) {
          lecteur.currentTime = courant.start;
          return;
        }
      }
      const index = this.cueAt(temps);
      if (index === this.activeCue) return;
      this.activeCue = index;
      this.highlightCue(index);
    },

    // Écoute du segment contenant un mot : c'est le geste naturel après
    // avoir cliqué un mot que l'on n'a pas compris.
    playSegmentOf(tokenId) {
      const lecteur = this.audioEl();
      const mot = this.wordEl(tokenId);
      if (!lecteur || !mot) return;
      const index = this.cueForChar(Number(mot.dataset.cs));
      if (index < 0 || this.cues[index].start === undefined) return;
      this.activeCue = index;
      this.highlightCue(index);
      lecteur.currentTime = this.cues[index].start;
      this.applyAudioSpeed();
      lecteur.play();
    },

    // --- vidéo sous-titrée ---

    // Le lecteur officiel est chargé à la demande. On passe par le
    // domaine sans cookie : rien n'est déposé tant que la vidéo n'est
    // pas lancée.
    // La vidéo est déjà affichée par une iframe ordinaire : le script ne
    // sert qu'à la synchronisation. S'il échoue, la vidéo reste lisible,
    // seul le suivi du texte est perdu.
    // Dialogue direct avec l'iframe, sans le script officiel de YouTube.
    //
    // La bibliotheque `iframe_api` ne faisait qu'envelopper ce meme
    // echange de messages, et elle refusait de s'attacher pour des
    // raisons invisibles — parametre `origin`, moment du chargement.
    // Le lecteur embarque accepte directement des commandes, et emet sa
    // position quatre fois par seconde des qu'on la lui demande.
    setupPlayer() {
      const cadre = document.getElementById('yt-player');
      if (!cadre) return;
      this.frame = cadre;

      window.addEventListener('message', (e) => this.onPlayerMessage(e));

      // On reclame les informations jusqu'a obtenir une reponse : le
      // lecteur ne repond pas avant d'etre pret, et rien ne signale ce
      // moment de l'exterieur.
      let essais = 0;
      const reclamer = () => {
        if (this.playerReady || essais > 40) return;
        essais += 1;
        this.postToPlayer({ event: 'listening', id: 'yt-player' });
        setTimeout(reclamer, 500);
      };
      cadre.addEventListener('load', reclamer);
      reclamer();
    },

    postToPlayer(message) {
      try {
        this.frame?.contentWindow?.postMessage(
          JSON.stringify(message),
          'https://www.youtube-nocookie.com'
        );
      } catch { /* iframe pas encore prete */ }
    },

    // Envoie une commande au lecteur : lecture, pause, deplacement.
    commandPlayer(nom, args = []) {
      this.postToPlayer({ event: 'command', func: nom, args });
    },

    onPlayerMessage(e) {
      if (!this.frame || e.source !== this.frame.contentWindow) return;
      let donnees;
      try {
        donnees = typeof e.data === 'string' ? JSON.parse(e.data) : e.data;
      } catch { return; }
      if (!donnees || typeof donnees !== 'object') return;

      if (!this.playerReady) {
        this.playerReady = true;
        console.info('[lecteur latin] lecteur vidéo relié');
      }
      const instant = donnees.info?.currentTime;
      if (typeof instant === 'number') this.applyVideoTime(instant);
    },

    // La position vient du lecteur lui-meme : aucun relevé à provoquer.
    applyVideoTime(temps) {
      const index = this.cueAt(temps);
      if (index === this.activeCue) return;
      this.activeCue = index;
      this.highlightCue(index);
    },

    // Réplique en cours de lecture. La recherche part de la précédente :
    // la vidéo avance, donc on est presque toujours au bon endroit ou
    // juste après.
    cueAt(temps) {
      // On parcourt tous les segments : un texte partiellement aligne en
      // comporte sans bornes, qu'il faut simplement enjamber.
      for (let i = 0; i < this.cues.length; i++) {
        const c = this.cues[i];
        if (typeof c.start !== 'number') continue;
        if (temps >= c.start && temps < c.end) return i;
      }
      return -1;
    },

    highlightCue(index) {
      const body = this.root?.querySelector('.text-body');
      if (!body) return;
      body.querySelectorAll('.w.spoken').forEach((w) => w.classList.remove('spoken'));
      if (index < 0) return;

      const cue = this.cues[index];
      let premier = null;
      body.querySelectorAll('.w[data-cs]').forEach((w) => {
        const position = Number(w.dataset.cs);
        if (position >= cue.char_start && position < cue.char_end) {
          w.classList.add('spoken');
          if (!premier) premier = w;
        }
      });
      if (premier && this.follow && typeof premier.scrollIntoView === 'function') {
        try { premier.scrollIntoView({ block: 'center', behavior: 'smooth' }); } catch {}
      }
    },

    // Réplique contenant un mot donné, par sa position dans le texte.
    cueForChar(position) {
      return this.cues.findIndex(
        (c) => position >= c.char_start && position < c.char_end
      );
    },

    // Le mot du texte portant cet identifiant.
    //
    // La classe `.w` est indispensable : le panneau porte lui aussi un
    // attribut `data-token`, et une fois insere dans le texte il serait
    // trouve a la place du mot — on tenterait alors de l'inserer dans
    // lui-meme, ce qui casse tout jusqu'au rechargement de la page.
    wordEl(tokenId) {
      return this.root?.querySelector(`.text-body .w[data-token="${tokenId}"]`) || null;
    },

    // --- placement dans le flux, en colonne étroite ---

    // Dernier mot de la ligne visuelle où se trouve `el`. Une ligne n'est
    // pas un élément du document : on la reconnaît à la position verticale
    // partagée par les mots qui la composent.
    lineEnd(el) {
      const body = this.root?.querySelector('.text-body');
      if (!body || typeof el.getBoundingClientRect !== 'function') return el;
      const reference = el.getBoundingClientRect();
      if (!reference.height) return el;

      let dernier = el;
      let noeud = el.nextSibling;
      while (noeud) {
        if (noeud.nodeType === 1 && typeof noeud.getBoundingClientRect === 'function') {
          const boite = noeud.getBoundingClientRect();
          // Tolérance : les mots d'une même ligne ne sont pas parfaitement
          // alignés (exposants, ponctuation, tailles différentes).
          if (boite.height && boite.top > reference.top + reference.height * 0.5) break;
          dernier = noeud;
        }
        noeud = noeud.nextSibling;
      }
      return dernier;
    },

    // Insère un bloc juste après la ligne du mot : étant en `display:block`,
    // il rompt la ligne et s'affiche dessous, le texte reprenant en dessous.
    insertAfterLine(wordEl, node) {
      const fin = this.lineEnd(wordEl);
      // Garde-fou : inserer un bloc a l'interieur de lui-meme leve une
      // exception qui interromprait toute la lecture.
      if (!fin.parentNode || node.contains(fin)) return false;
      fin.parentNode.insertBefore(node, fin.nextSibling);
      return true;
    },

    clearInline() {
      // Ordre important : le panneau est un enfant de son bloc. Supprimer
      // les blocs d'abord l'emporterait avec eux, et il disparaitrait du
      // document au moindre elargissement de la fenetre.
      this.restorePanel();
      this.root?.querySelectorAll('.inline-slot').forEach((n) => n.remove());
    },

    renderInlineImages() {
      this.root?.querySelectorAll('.inline-slot.for-image').forEach((n) => n.remove());
      // Plusieurs images peuvent tomber sur la même ligne : on les groupe.
      const parLigne = new Map();
      this.imageTargets.forEach((img) => {
        const mot = this.wordEl(img.token_id);
        if (!mot) return;
        const fin = this.lineEnd(mot);
        if (!parLigne.has(fin)) parLigne.set(fin, []);
        parLigne.get(fin).push(img);
      });

      parLigne.forEach((images, fin) => {
        const bloc = document.createElement('div');
        bloc.className = 'inline-slot for-image';
        images.forEach((img) => {
          const figure = document.createElement('figure');
          figure.className = 'inline-image';
          figure.dataset.lemma = img.lemma_id;
          figure.innerHTML =
            `<img src="${img.url}" alt="" loading="lazy">` +
            `<figcaption><b></b><span></span></figcaption>`;
          figure.querySelector('b').textContent = img.lemma || '';
          figure.querySelector('span').textContent = img.gloss || '';
          figure.addEventListener('click', () => this.open(img.token_id));
          bloc.appendChild(figure);
        });
        fin.parentNode.insertBefore(bloc, fin.nextSibling);
      });
    },

    // Le panneau descend sous la ligne du mot cliqué, au lieu d'occuper
    // une colonne latérale qui n'existe pas sur écran étroit.
    placePanelInline(tokenId) {
      const panneau = this.root?.querySelector('.panel');
      const mot = this.wordEl(tokenId);
      if (!panneau || !mot) return;

      let logement = this.root.querySelector('.inline-slot.for-panel');
      if (!logement) {
        logement = document.createElement('div');
        logement.className = 'inline-slot for-panel';
      } else {
        logement.remove();
      }
      if (!this.insertAfterLine(mot, logement)) {
        // Placement impossible : le panneau reprend sa colonne plutot
        // que de disparaitre.
        this.restorePanel();
        return;
      }
      logement.appendChild(panneau);
      panneau.classList.add('inline');
    },

    restorePanel() {
      const panneau = this.root?.querySelector('.panel');
      if (!panneau) return;
      panneau.classList.remove('inline');
      // Le panneau reprend sa place de colonne, en dernier enfant de la
      // grille de lecture.
      const grille = this.root?.querySelector('.reader') || this.root;
      if (panneau.parentNode !== grille) grille.appendChild(panneau);
      this.root.querySelectorAll('.inline-slot.for-panel').forEach((n) => n.remove());
    },

    // Signale la page comme lue. Marquer le vocabulaire est un geste
    // distinct : on peut avoir tout compris sans rien vouloir noter.
    async markPageRead() {
      let marquer = false;
      if (this.unseenCount > 0) {
        marquer = confirm(
          `Marquer les ${this.unseenCount} mot(s) bleus de cette page ` +
          `comme connus ?\n\n` +
          `Ce sont ceux que vous n'avez jamais notés. Répondez Annuler ` +
          `pour signaler la page comme lue sans rien marquer.`
        );
      }

      this.validating = true;
      const out = await this.post(
        `/api/texts/${this.textId}/pages/${this.page}/read`,
        { mark_unseen: marquer ? '1' : '' },
        { horsLigne: true }
      );
      this.validating = false;
      if (!out) return;

      this.pageRead = true;
      if (marquer && (out.validated || out.queued)) {
        // On repeint les mots concernes sans recharger la page.
        this.root?.querySelectorAll('.text-body .w.unseen').forEach((el) => {
          el.classList.remove('unseen');
          el.classList.add('s0');
        });
        this.unseenCount = 0;
        this.refreshGauge();
      }
    },

    panelRoot() {
      return document.querySelector('.panel-inner');
    },

    async open(tokenId) {
      clearTimeout(this.restoreTimer);
      this.select(tokenId);
      this.tokenId = tokenId;
      this.panel = true;

      // Fondu enchaîné : l'ancien contenu reste affiché jusqu'à ce que le
      // nouveau soit prêt. Le vider tout de suite faisait clignoter le
      // panneau — un blanc, puis le remplissage.
      const demande = ++this.panelRequest;
      let html;
      try {
        const res = await fetch(`/panel/token/${tokenId}`);
        html = await res.text();
      } catch (err) {
        html = `<p class="error">Panneau indisponible : ${err}</p>`;
      }
      // Une réponse tardive ne doit pas écraser un mot cliqué depuis.
      if (demande !== this.panelRequest) return;

      this.panelFading = true;
      await new Promise((r) => setTimeout(r, 90));
      this.panelHtml = html;
      this.panelFading = false;

      this.$nextTick(() => {
        const root = this.panelRoot();
        this.lemmaId = root ? root.dataset.lemma || null : null;
        this.restoreSections();
        if (this.layoutMode === 'inline') this.placePanelInline(tokenId);
      });
    },

    // Un seul ecouteur, pose une fois. Il fonctionne quel que soit le
    // moment ou le contenu du panneau est remplace.
    delegate() {
      document.addEventListener('click', (e) => {
        const inPanel = e.target.closest('.panel-inner');
        if (!inPanel) return;

        const st = e.target.closest('.st');
        if (st) {
          e.preventDefault();
          if (st.dataset.ignore) this.setStatus(null, true);
          else this.setStatus(parseInt(st.dataset.status, 10), false);
          return;
        }

        if (e.target.closest('[data-unlock]')) { e.preventDefault(); this.unlock(); return; }
        if (e.target.closest('.save-gloss')) { e.preventDefault(); this.saveGloss(); return; }
        if (e.target.closest('.save-note')) { e.preventDefault(); this.saveNote(); return; }
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

    // Cliquer hors d'un mot annule la selection. On epargne le panneau
    // et tout element interactif : on ne veut pas perdre sa place en
    // cochant une case ou en suivant un lien.
    delegateOutsideClick() {
      document.addEventListener('click', (e) => {
        if (!this.selected && !this.panel) return;
        const cible = e.target;
        if (
          cible.closest('.text-body .w') ||
          cible.closest('.panel') ||
          cible.closest('a, button, input, textarea, select, label, form, figure')
        ) {
          return;
        }
        this.deselect();
      });
    },

    // Double-clic sur un mot : l'audio saute au debut de son segment.
    delegateDoubleClick() {
      const corps = this.root?.querySelector('.text-body');
      if (!corps) return;
      corps.addEventListener('dblclick', (e) => {
        const mot = e.target.closest('.w[data-cs]');
        if (!mot) return;
        e.preventDefault();
        // Un double-clic selectionne le mot dans le navigateur : on
        // efface cette selection, sans interet ici.
        window.getSelection?.()?.removeAllRanges?.();
        this.seekToWord(Number(mot.dataset.token));
      });
    },

    // Place la lecture au debut du segment contenant ce mot, dans
    // l'audio comme dans la video.
    seekToWord(tokenId) {
      const mot = this.wordEl(tokenId);
      if (!mot) return;
      const index = this.cueForChar(Number(mot.dataset.cs));
      if (index < 0 || typeof this.cues[index].start !== 'number') return;

      this.activeCue = index;
      this.highlightCue(index);

      const debut = this.cues[index].start;

      const lecteur = this.audioEl();
      if (lecteur) {
        lecteur.currentTime = debut;
        this.applyAudioSpeed();
        lecteur.play();
        return;
      }

      if (this.frame) {
        this.commandPlayer('seekTo', [debut, true]);
        this.commandPlayer('playVideo');
        return;
      }

      // Repli : sans iframe reliee, on la recharge au bon instant.
      if (this.videoId) this.reloadVideoAt(debut);
    },

    reloadVideoAt(seconde) {
      const cadre = document.getElementById('yt-player');
      if (!cadre) return;
      const base = 'https://www.youtube-nocookie.com/embed/' + this.videoId;
      cadre.src =
        `${base}?enablejsapi=1&rel=0&modestbranding=1&playsinline=1` +
        `&autoplay=1&start=${Math.floor(seconde)}`;
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

    // --- selection et navigation au clavier ---
    words() {
      return [...(this.root?.querySelectorAll('.text-body .w') || [])];
    },

    select(tokenId, { scroll = false } = {}) {
      const el = this.wordEl(tokenId);
      if (!el) return;
      this.root?.querySelectorAll('.text-body .w.selected')
        .forEach((w) => w.classList.remove('selected', 'tip-left', 'tip-right'));
      el.classList.add('selected');
      this.selected = tokenId;
      this.focusedLemma = el.dataset.lemma ? Number(el.dataset.lemma) : null;
      this.placeTip(el);
      // Defensif : une exception ici interromprait la navigation.
      if (scroll && typeof el.scrollIntoView === 'function') {
        try {
          el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        } catch { /* environnements sans support */ }
      }
    },

    // Ramene l'info-bulle vers l'interieur quand le mot touche un bord.
    placeTip(el) {
      const tip = el.querySelector('.tip');
      if (!tip || typeof el.getBoundingClientRect !== 'function') return;
      const body = this.root?.querySelector('.text-body');
      if (!body) return;
      const zone = body.getBoundingClientRect();
      const word = el.getBoundingClientRect();
      if (!zone.width || !word.width) return;
      const half = tip.offsetWidth / 2 || 90;
      if (word.left + word.width / 2 - half < zone.left) el.classList.add('tip-right');
      else if (word.left + word.width / 2 + half > zone.right) el.classList.add('tip-left');
    },

    move(step) {
      const words = this.words();
      if (!words.length) return;
      const current = words.findIndex(
        (w) => Number(w.dataset.token) === this.selected
      );
      const next = current === -1
        ? (step > 0 ? 0 : words.length - 1)
        : Math.min(words.length - 1, Math.max(0, current + step));
      const tokenId = Number(words[next].dataset.token);
      this.select(tokenId, { scroll: true });
      // Le panneau suit la selection s'il est deja ouvert.
      if (this.panel) this.open(tokenId);
    },

    // Deplacement vertical : on vise le mot le plus proche horizontalement
    // sur la ligne precedente ou suivante.
    moveLine(direction) {
      const words = this.words();
      const currentEl = words.find(
        (w) => Number(w.dataset.token) === this.selected
      );
      if (!currentEl) { this.move(direction); return; }
      const from = currentEl.getBoundingClientRect();
      const centre = from.left + from.width / 2;

      const candidates = words
        .map((w) => ({ el: w, box: w.getBoundingClientRect() }))
        .filter(({ box }) =>
          direction > 0 ? box.top > from.bottom - 2 : box.bottom < from.top + 2
        );
      if (!candidates.length) return;

      const edge = direction > 0
        ? Math.min(...candidates.map((c) => c.box.top))
        : Math.max(...candidates.map((c) => c.box.bottom));
      const line = candidates.filter(({ box }) =>
        direction > 0 ? box.top < edge + 4 : box.bottom > edge - 4
      );
      const best = line.reduce((a, b) =>
        Math.abs(b.box.left + b.box.width / 2 - centre)
          < Math.abs(a.box.left + a.box.width / 2 - centre) ? b : a
      );
      const tokenId = Number(best.el.dataset.token);
      this.select(tokenId, { scroll: true });
      if (this.panel) this.open(tokenId);
    },

    // horsLigne : cette mutation est idempotente et peut attendre le
    // retour du réseau ; on la met en file et on rend un accusé local.
    async post(url, data, { horsLigne = false } = {}) {
      const body = new FormData();
      Object.entries(data).forEach(([k, v]) => {
        if (v !== null && v !== undefined) body.append(k, v);
      });
      let res;
      try {
        res = await fetch(url, { method: 'POST', body });
      } catch (err) {
        if (horsLigne && window.fileAttente) {
          window.fileAttente.push(url, data);
          return { queued: true };
        }
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

    async setStatus(status, ignore) {
      const lemmaId = this.currentLemma();
      if (!lemmaId) { alert('Aucun lemme associé à ce mot.'); return; }
      const out = await this.post(`/api/lemmas/${lemmaId}/status`, {
        status: status,
        is_ignored: ignore ? 'true' : null,
      }, { horsLigne: true });
      if (out && out.queued) {
        // Hors-ligne : on repeint depuis la page, le serveur suivra.
        const tokens = Array.from(
          this.root?.querySelectorAll(`.text-body .w[data-lemma="${lemmaId}"]`) || []
        ).map((el) => parseInt(el.dataset.token, 10));
        this.repaint({ tokens, status, is_ignored: !!ignore });
        return;
      }
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
        const el = this.wordEl(id);
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
      this.refreshGauge();
    },

    // La jauge d'en-tete reflete les couleurs des mots : on la recompte
    // depuis la page elle-meme, sans attendre un rechargement.
    refreshGauge() {
      const jauge = document.querySelector('.page-gauge');
      if (!jauge) return;
      const libelles = {
        s0: 'maîtrisé', s1: 'presque su', s2: 'en cours',
        s3: 'fragile', s4: 'inconnu', unseen: 'jamais rencontré',
      };
      const compte = {};
      let total = 0;
      this.root?.querySelectorAll('.text-body .w').forEach((el) => {
        if (!el.dataset.lemma || el.classList.contains('ignored')) return;
        total += 1;
        const cle = ['s0', 's1', 's2', 's3', 's4']
          .find((c) => el.classList.contains(c)) || 'unseen';
        compte[cle] = (compte[cle] || 0) + 1;
      });
      if (!total) return;

      const barre = jauge.querySelector('.gauge');
      if (barre) {
        barre.innerHTML = ['s0', 's1', 's2', 's3', 's4', 'unseen']
          .filter((cle) => compte[cle])
          .map((cle) => {
            const part = (100 * compte[cle] / total).toFixed(1);
            const titre = `${libelles[cle]} — ${compte[cle]} mots`;
            return `<span class="g-${cle}" style="width: ${part}%" title="${titre}"></span>`;
          })
          .join('');
      }
      const part = Math.round(100 * ((compte.s0 || 0) + (compte.s1 || 0)) / total);
      const legende = jauge.querySelector('.gauge-legend b');
      if (legende) {
        legende.textContent = `${part} %`;
        legende.className = part >= 90 ? 'ready' : (part >= 70 ? 'near' : '');
      }
      jauge.setAttribute(
        'aria-label',
        `${part} % du vocabulaire de cette page déjà connu`
      );
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

    async saveNote() {
      const zone = document.querySelector('.panel-inner .note');
      const lemmaId = this.currentLemma();
      if (!zone || !lemmaId) return;
      if (await this.post(`/api/lemmas/${lemmaId}/note`, { note: zone.value })) {
        zone.classList.add('saved');
        setTimeout(() => zone.classList.remove('saved'), 800);
      }
    },

    // Les sections sont repliees par defaut ; on retient celles que
    // l'utilisateur ouvre, d'un mot a l'autre et d'une session a l'autre.
    restoreSections() {
      let ouvertes = [];
      try {
        ouvertes = JSON.parse(localStorage.getItem('panneau-ouvert') || '[]');
      } catch { ouvertes = []; }
      this.root?.querySelectorAll('.panel-inner details.pan').forEach((bloc) => {
        bloc.open = ouvertes.includes(bloc.dataset.section);
        bloc.addEventListener('toggle', () => this.rememberSections());
      });
    },

    rememberSections() {
      const ouvertes = [...(this.root?.querySelectorAll('.panel-inner details.pan') || [])]
        .filter((b) => b.open)
        .map((b) => b.dataset.section);
      try {
        localStorage.setItem('panneau-ouvert', JSON.stringify(ouvertes));
      } catch { /* stockage indisponible : sans conséquence */ }
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

      // Navigation de mot en mot, panneau ouvert ou non.
      const arrows = {
        ArrowRight: () => this.move(1),
        ArrowLeft: () => this.move(-1),
        ArrowDown: () => this.moveLine(1),
        ArrowUp: () => this.moveLine(-1),
      };
      if (arrows[e.key]) { e.preventDefault(); arrows[e.key](); return; }

      if (e.key === 'Enter' && this.selected) {
        e.preventDefault();
        this.open(this.selected);
        return;
      }

      if (e.key === 'Escape') {
        this.deselect();
        return;
      }

      // Les raccourcis de statut agissent sur le mot selectionne, meme
      // sans panneau ouvert : on peut ainsi annoter au seul clavier.
      if (!this.panel && !this.selected) return;
      if (!this.panel && this.selected && '01234if'.includes(e.key)) {
        this.open(this.selected);
      }
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
