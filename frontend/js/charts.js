// ═══════════════ Graphique de performance (courbe P&L) ═══════════════
// SVG autonome — aucune librairie externe (compatible CSP / hors-ligne).
// Trace le P&L réel cumulé si un compte est connecté, sinon la valeur du
// portefeuille issue des analyses.
(function () {
  async function jf(url) {
    try {
      const r = await fetch(url);
      if (r.status === 401 && window._authRedirect) window._authRedirect();
      return await r.json();
    } catch { return null; }
  }

  function fmt(n) { return (n >= 0 ? '+' : '') + Number(n).toFixed(2); }

  // La devise vient du COURTIER et les libellés d'axe de la base : comme
  // partout ailleurs dans le tableau de bord (cf. auto_trader.js, mt5.js), on
  // échappe avant toute insertion en innerHTML. Repli local si app.js n'est pas
  // encore chargé — ce module s'initialise indépendamment.
  const _esc = s => (typeof esc === 'function' ? esc(s) : String(s ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;'));

  function dessiner(container, points, titre, unite) {
    if (!points || points.length < 2) {
      container.innerHTML = '<p class="perf-empty">Pas encore assez de données pour tracer une courbe. '
        + 'Lancez une analyse ou connectez un compte pour accumuler l\'historique.</p>';
      return;
    }
    const W = 600, H = 200, PADL = 8, PADR = 8, PADT = 14, PADB = 18;
    const vals = points.map(p => p.v);
    let min = Math.min(...vals), max = Math.max(...vals);
    if (min === max) { min -= 1; max += 1; }
    const range = max - min;
    const iw = W - PADL - PADR, ih = H - PADT - PADB;
    const x = i => PADL + (i / (points.length - 1)) * iw;
    const y = v => PADT + (1 - (v - min) / range) * ih;

    const pts = points.map((p, i) => `${x(i).toFixed(1)},${y(p.v).toFixed(1)}`).join(' ');
    const dernier = vals[vals.length - 1];
    const isUp = dernier >= vals[0];
    const couleur = isUp ? '#10b981' : '#ef4444';
    const fillPts = `${PADL},${H - PADB} ${pts} ${W - PADR},${H - PADB}`;

    // Ligne du zéro (uniquement si la plage la traverse — utile pour un P&L)
    let zeroLine = '';
    if (min < 0 && max > 0) {
      const yz = y(0).toFixed(1);
      zeroLine = `<line x1="${PADL}" y1="${yz}" x2="${W - PADR}" y2="${yz}" stroke="var(--border)" stroke-width="1" stroke-dasharray="4 4"/>`;
    }

    container.innerHTML = `
      <div class="perf-head">
        <span class="perf-title">${_esc(titre)}</span>
        <span class="perf-last ${isUp ? 'pos' : 'neg'}">${fmt(dernier)}${unite ? ' ' + _esc(unite) : ''}</span>
      </div>
      <svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" class="perf-svg" height="200" width="100%">
        <defs>
          <linearGradient id="perfgrad" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="${couleur}" stop-opacity="0.28"/>
            <stop offset="100%" stop-color="${couleur}" stop-opacity="0"/>
          </linearGradient>
        </defs>
        ${zeroLine}
        <polygon points="${fillPts}" fill="url(#perfgrad)"/>
        <polyline points="${pts}" fill="none" stroke="${couleur}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      </svg>
      <div class="perf-axis"><span>${_esc(points[0].label)}</span><span>${_esc(points[points.length - 1].label)}</span></div>`;
  }

  async function render() {
    const container = document.getElementById('perf-chart');
    if (!container) return;

    // 1) P&L réel cumulé si un compte est connecté
    const reel = await jf('/api/performance/reel?jours=30');
    if (reel && reel.synced && Array.isArray(reel.par_jour) && reel.par_jour.length >= 2) {
      let cumul = 0;
      const points = reel.par_jour.map(d => {
        cumul += (d.pnl || 0);
        return { label: String(d.date || '').slice(5), v: +cumul.toFixed(2) };
      });
      dessiner(container, points, 'P&L réel cumulé · 30 jours', reel.devise || 'USD');
      return;
    }

    // 2) sinon, valeur du portefeuille (analyses / simulation)
    const hist = await jf('/api/portfolio/historique?limite=100');
    if (hist && hist.historique && hist.historique.length >= 2) {
      const rows = hist.historique.filter(h => h.valeur_totale != null);
      const points = rows.map(h => ({
        label: String(h.timestamp || '').slice(5, 10),
        v: +Number(h.valeur_totale).toFixed(2),
      }));
      dessiner(container, points, 'Valeur du portefeuille', '');
      return;
    }

    dessiner(container, null);
  }

  window.renderPnlChart = render;
  document.addEventListener('DOMContentLoaded', () => setTimeout(render, 1200));
})();
