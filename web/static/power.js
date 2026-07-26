/* =============================================================
   Sidekick – Power Off / Relaunch Controls
   ============================================================= */

(function () {
  'use strict';

  const API_BASE = (document.baseURI || location.href).replace(/\/+$/, '');

  // ─── Splashscreen Overlay ──────────────────────────────────
  function showSplash(type) {
    const existing = document.getElementById('powerSplash');
    if (existing) existing.remove();

    const isShutdown = type === 'shutdown';
    const icon = isShutdown
      ? '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M18.36 6.64a9 9 0 1 1-12.73 0M12 2v10"/></svg>'
      : '<svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>';

    const title = isShutdown ? 'Auf Wiedersehen!' : 'Neustart…';
    const msg = isShutdown ? 'Der Server wird heruntergefahren.' : 'Der Server wird neu gestartet.';

    const splash = document.createElement('div');
    splash.id = 'powerSplash';
    splash.innerHTML = `
      <div class="power-splash-inner">
        <div class="power-splash-icon">${icon}</div>
        <div class="power-splash-title">${title}</div>
        <div class="power-splash-msg">${msg}</div>
        <div class="power-splash-spinner"><div class="power-spinner"></div></div>
      </div>
    `;
    document.body.appendChild(splash);
  }

  // ─── API Calls ─────────────────────────────────────────────
  async function callPowerAPI(endpoint) {
    try {
      const res = await fetch(API_BASE + endpoint, {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      return await res.json();
    } catch (e) {
      // Server may have gone down — that's expected during shutdown/restart
      return { status: 'gone' };
    }
  }

  // ─── Confirm Dialog (lightweight, no external deps) ────────
  function showConfirm(title, msg, danger) {
    return new Promise((resolve) => {
      const overlay = document.createElement('div');
      overlay.className = 'power-confirm-overlay';
      overlay.innerHTML = `
        <div class="power-confirm-dialog">
          <div class="power-confirm-title">${title}</div>
          <div class="power-confirm-msg">${msg}</div>
          <div class="power-confirm-actions">
            <button class="power-confirm-btn power-confirm-cancel" id="powerConfirmCancel">Abbrechen</button>
            <button class="power-confirm-btn ${danger ? 'power-confirm-danger' : 'power-confirm-ok'}" id="powerConfirmOk">${danger ? 'Herunterfahren' : 'Neustarten'}</button>
          </div>
        </div>
      `;
      document.body.appendChild(overlay);

      overlay.querySelector('#powerConfirmCancel').onclick = () => {
        overlay.remove();
        resolve(false);
      };
      overlay.querySelector('#powerConfirmOk').onclick = () => {
        overlay.remove();
        resolve(true);
      };
      overlay.onclick = (e) => {
        if (e.target === overlay) { overlay.remove(); resolve(false); }
      };
    });
  }

  // ─── Public Functions ──────────────────────────────────────
  window.sidekickPowerOff = async function () {
    const confirmed = await showConfirm(
      'Ausschalten',
      'Möchtest du den Sidekick-Server wirklich herunterfahren? Alle laufenden Vorgänge werden beendet.',
      true
    );
    if (!confirmed) return;

    showSplash('shutdown');
    await callPowerAPI('/api/system/shutdown');
    // The server is shutting down — splash stays visible
  };

  window.sidekickRelaunch = async function () {
    const confirmed = await showConfirm(
      'Neustarten',
      'Möchtest du den Sidekick-Server wirklich neu starten?',
      false
    );
    if (!confirmed) return;

    showSplash('restart');
    await callPowerAPI('/api/system/restart');

    // Poll for server to come back up, then reload
    let attempts = 0;
    const maxAttempts = 40;
    const poll = setInterval(async () => {
      attempts++;
      try {
        const healthUrl = (document.baseURI || location.href).replace(/\/+$/, '') + '/health';
        const r = await fetch(healthUrl, { cache: 'no-store' });
        if (r.ok) {
          clearInterval(poll);
          location.reload();
        }
      } catch (_) {
        // server still restarting
      }
      if (attempts >= maxAttempts) {
        clearInterval(poll);
        const splash = document.getElementById('powerSplash');
        if (splash) {
          splash.innerHTML = `
            <div class="power-splash-inner">
              <div class="power-splash-icon"><svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg></div>
              <div class="power-splash-title">Server antwortet nicht</div>
              <div class="power-splash-msg">Der Server braucht länger als erwartet. Seite manuell neu laden?</div>
              <button class="power-splash-btn" onclick="location.reload()">Neu laden</button>
            </div>
          `;
        }
      }
    }, 750);
  };
})();
