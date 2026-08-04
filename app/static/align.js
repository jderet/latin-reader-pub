// Éditeur d'alignement : frappe au rythme, puis retouche.
//
// La frappe relève l'instant de chaque pression de la barre d'espace.
// Rien n'est enregistré avant la fin de la séance : on peut annuler.
const ALIGN_VERSION = 1;

function aligner() {
  const data = window.ALIGN_DATA || {};
  return {
    textId: data.textId,
    segments: data.segments || [],
    root: null,
    tapping: false,
    taps: [],
    speed: '1',
    playing: -1,
    stopAt: null,
    watcher: null,

    init() {
      console.info(`[lecteur latin] align.js v${ALIGN_VERSION} chargé`);
      this.delegate();
    },

    audio() {
      return this.$refs?.audio || null;
    },

    get aligned() {
      return this.segments.filter((s) => s.start !== null && s.start !== undefined).length;
    },

    applySpeed() {
      const lecteur = this.audio();
      if (lecteur) lecteur.playbackRate = Number(this.speed) || 1;
    },

    // --- frappe au rythme ---
    startTapping() {
      const lecteur = this.audio();
      if (!lecteur) return;
      this.taps = [];
      this.tapping = true;
      lecteur.currentTime = 0;
      this.applySpeed();
      lecteur.play();
    },

    tap() {
      const lecteur = this.audio();
      if (!lecteur || !this.tapping) return;
      // La vitesse de lecture ne change pas `currentTime` : le relevé est
      // toujours exprimé dans le temps réel de l'enregistrement.
      this.taps.push(lecteur.currentTime);
    },

    cancelTapping() {
      this.tapping = false;
      this.taps = [];
      this.audio()?.pause();
    },

    async finishTapping() {
      const lecteur = this.audio();
      lecteur?.pause();
      this.tapping = false;
      if (!this.taps.length) return;

      const out = await this.post(`/api/admin/texts/${this.textId}/segments/taps`, {
        taps: this.taps.map((t) => t.toFixed(2)).join(','),
        duration: lecteur?.duration || 0,
      });
      if (out) location.reload();
    },

    // --- écoute et retouche ---
    playSegment(index) {
      const lecteur = this.audio();
      const segment = this.segments[index];
      if (!lecteur || !segment || segment.start === null) return;
      this.stopWatching();
      this.playing = index;
      lecteur.currentTime = segment.start;
      this.applySpeed();
      lecteur.play();
      // On surveille la position pour s'arrêter à la fin du segment :
      // l'élément audio ne sait pas jouer un intervalle.
      this.stopAt = segment.end;
      this.watcher = setInterval(() => {
        if (lecteur.currentTime >= this.stopAt) {
          lecteur.pause();
          this.stopWatching();
        }
      }, 60);
    },

    stopWatching() {
      if (this.watcher) { clearInterval(this.watcher); this.watcher = null; }
      this.playing = -1;
    },

    async saveTimes(ligne) {
      const index = Number(ligne.dataset.index);
      const debut = ligne.querySelector('.seg-start').value;
      const fin = ligne.querySelector('.seg-end').value;
      const out = await this.post(
        `/api/admin/texts/${this.textId}/segments/${index}`,
        { start: debut, end: fin }
      );
      if (!out) return;
      this.segments[index].start = out.start;
      this.segments[index].end = out.end;
      ligne.classList.add('saved');
      setTimeout(() => ligne.classList.remove('saved'), 700);
    },

    // Cale le début du segment sur la position actuelle de la lecture :
    // plus rapide que de saisir un nombre à la main.
    async setHere(ligne) {
      const lecteur = this.audio();
      if (!lecteur) return;
      ligne.querySelector('.seg-start').value = lecteur.currentTime.toFixed(2);
      await this.saveTimes(ligne);
    },

    async mergeWithNext(index) {
      const out = await this.post(
        `/api/admin/texts/${this.textId}/segments/${index}/merge`, {}
      );
      if (out) location.reload();
    },

    async resetSegments() {
      if (!confirm(
        'Redécouper le texte ? L’alignement déjà effectué sera perdu.'
      )) return;
      const out = await this.post(
        `/api/admin/texts/${this.textId}/segments/reset`, {}
      );
      if (out) location.reload();
    },

    async post(url, data) {
      const body = new FormData();
      Object.entries(data || {}).forEach(([k, v]) => {
        if (v !== null && v !== undefined && v !== '') body.append(k, v);
      });
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

    delegate() {
      document.addEventListener('click', (e) => {
        const ligne = e.target.closest('.segments li');
        if (!ligne) return;
        const index = Number(ligne.dataset.index);
        if (e.target.closest('.seg-play')) { e.preventDefault(); this.playSegment(index); }
        if (e.target.closest('.seg-here')) { e.preventDefault(); this.setHere(ligne); }
        if (e.target.closest('.seg-merge')) { e.preventDefault(); this.mergeWithNext(index); }
      });

      document.addEventListener('change', (e) => {
        if (e.target.classList?.contains('seg-start') ||
            e.target.classList?.contains('seg-end')) {
          this.saveTimes(e.target.closest('.segments li'));
        }
      });
    },

    onKey(e) {
      if (['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) return;
      if (e.key === ' ' && this.tapping) {
        // Sans cela, la barre d'espace mettrait la lecture en pause.
        e.preventDefault();
        this.tap();
        return;
      }
      if (e.key === 'Escape' && this.tapping) { e.preventDefault(); this.cancelTapping(); }
    },
  };
}

// Téléversement de l'enregistrement, hors du composant Alpine : la page
// se recharge après, il n'y a pas d'état à conserver.
(function () {
  const champ = document.getElementById('audio-file');
  const retrait = document.getElementById('audio-remove');
  const textId = (window.ALIGN_DATA || {}).textId;

  async function envoyer(url, body) {
    const res = await fetch(url, { method: 'POST', body });
    if (!res.ok) { alert(`Échec (${res.status}) : ${await res.text()}`); return false; }
    return true;
  }

  champ?.addEventListener('change', async () => {
    const fichier = champ.files[0];
    if (!fichier) return;
    const body = new FormData();
    body.append('file', fichier);
    if (await envoyer(`/api/admin/texts/${textId}/audio`, body)) location.reload();
  });

  retrait?.addEventListener('click', async (e) => {
    e.preventDefault();
    if (!confirm('Retirer cet enregistrement ? L’alignement est conservé.')) return;
    if (await envoyer(`/api/admin/texts/${textId}/audio/delete`, new FormData()))
      location.reload();
  });
})();
