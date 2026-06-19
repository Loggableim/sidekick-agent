/**
 * Discord Bot Panel — WebUI Integration
 * Follows the Gmail panel pattern with sub-navigation tabs.
 */
(function () {
  'use strict';

  let _initialized = false;
  let _guildData = null;
  let _memberCache = [];

  const API = '/api/discord';

  function $(id) {
    return document.getElementById(id);
  }

  async function _api(path, opts) {
    try {
      let url = API + path;
      if (typeof getActiveSpaceQuery === 'function') {
        const qs = getActiveSpaceQuery();
        if (qs) url += (url.includes('?') ? '&' + qs.slice(1) : qs);
      }
      const resp = await fetch(url, opts || {});
      return await resp.json();
    } catch (e) {
      return { error: true, message: e.message };
    }
  }

  function esc(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
  }

  /* ─── Init ─── */
  function init() {
    if (_initialized) return;
    _initialized = true;

    const panel = document.getElementById('panelDiscord');
    if (!panel) return;

    // Panel already has the head and tabs from index.html
    // Just wire up tab switching
    panel.querySelectorAll('.discord-tab').forEach((tab) => {
      if (tab.getAttribute('onclick') || tab.dataset.discordBound === '1') return;
      tab.dataset.discordBound = '1';
      tab.addEventListener('click', function () {
        discordSwitchTab(this.getAttribute('data-discord-tab'));
      });
    });
  }

  /* ─── Tab Switching ─── */
  function discordSwitchTab(tab) {
    const panel = document.getElementById('panelDiscord');
    panel.querySelectorAll('.discord-tab').forEach((t) =>
      t.classList.toggle('discord-active', t.getAttribute('data-discord-tab') === tab)
    );
    loadDiscordTab(tab);
  }

  async function loadDiscordTab(tab) {
    const content = document.getElementById('discordContent');
    if (!content) return;

    switch (tab) {
      case 'dashboard':
        renderDashboard(content);
        break;
      case 'moderation':
        renderModeration(content);
        break;
      case 'settings':
        renderSettings(content);
        break;
      case 'logs':
        renderLogs(content);
        break;
      default:
        renderDashboard(content);
    }
  }

  window.discordSwitchTab = discordSwitchTab;
  window.loadDiscordPanel = function () {
    init();
    const btn = document.querySelector('.discord-tab.discord-active');
    const tab = btn ? btn.getAttribute('data-discord-tab') : 'dashboard';
    loadDiscordTab(tab);
  };

  /* ─── DASHBOARD ─── */
  async function renderDashboard(el) {
    el.innerHTML = '<div class="discord-loading"><div class="discord-shimmer"></div><span>Loading server stats...</span></div>';

    const [stats, channels, members] = await Promise.all([
      _api('/stats'),
      _api('/channels'),
      _api('/members'),
    ]);

    if (stats.error) {
      el.innerHTML = '<div style="padding:20px;color:var(--red);text-align:center;">❌ Discord nicht verbunden.<br><span style="font-size:12px;color:var(--muted);">Bitte im Appstore unter Discord → App Settings Bot-Token und Guild-ID für diesen Space prüfen.</span></div>';
      return;
    }

    const mCount = stats.member_count || '?';
    const online = stats.online || '?';
    const ch = stats.channels || {};
    const boosts = stats.boosts || 0;
    const tier = stats.tier || 0;

    el.innerHTML = `
      <div class="discord-server-header">
        <div class="discord-server-name">🐾 ${esc(stats.name)}</div>
        <div class="discord-server-features">
          ⚡ Level ${tier} · ${boosts} Boosts
          ${stats.features && stats.features.includes('COMMUNITY') ? '· 🌍 Community Server' : ''}
          ${stats.features && stats.features.includes('VANITY_URL') ? '· 🔗 Vanity URL' : ''}
        </div>
      </div>

      <div class="discord-stats-grid">
        <div class="discord-stat-card">
          <div class="discord-stat-value">${mCount}</div>
          <div class="discord-stat-label">👥 Mitglieder</div>
        </div>
        <div class="discord-stat-card">
          <div class="discord-stat-value">${online}</div>
          <div class="discord-stat-label">🟢 Online</div>
        </div>
        <div class="discord-stat-card">
          <div class="discord-stat-value">${ch.total || '?'}</div>
          <div class="discord-stat-label">💬 Channels</div>
        </div>
        <div class="discord-stat-card">
          <div class="discord-stat-value">${stats.roles || '?'}</div>
          <div class="discord-stat-label">🎭 Rollen</div>
        </div>
      </div>

      <div class="discord-stats-grid" style="grid-template-columns:repeat(3,1fr);">
        <div class="discord-stat-card" style="font-size:12px;">
          📁 <strong>${ch.categories || 0}</strong> Kategorien
        </div>
        <div class="discord-stat-card" style="font-size:12px;">
          📝 <strong>${ch.text || 0}</strong> Text
        </div>
        <div class="discord-stat-card" style="font-size:12px;">
          🔊 <strong>${ch.voice || 0}</strong> Voice
        </div>
      </div>

      <div style="margin-top:12px;font-size:12px;color:var(--muted);background:var(--surface-alt);border:1px solid var(--border);border-radius:8px;padding:10px;">
        <div style="font-weight:600;margin-bottom:6px;">🤖 Bots auf dem Server</div>
        ${members && members.bot_list && members.bot_list.length
          ? members.bot_list.map(b => '🤖 ' + esc(b.name) + '#' + esc(b.discriminator || '')).join('<br>')
          : 'Lade...'}
        <div style="margin-top:6px;color:var(--muted);">
          ${members ? esc(members.humans) + ' Menschen · ' + esc(members.bots) + ' Bots': ''}
          von ${members ? esc(members.total) : '?'} insgesamt
        </div>
      </div>
    `;
  }

  /* ─── MODERATION ─── */
  async function renderModeration(el) {
    // Search members
    let memberOptions = '';
    try {
      const data = await _api('/members');
      if (data && data.members) {
        _memberCache = data.members;
        memberOptions = data.members
          .filter(m => !m.user?.bot)
          .slice(0, 50)
          .map(m => `<option value="${m.user.id}">${esc(m.user.username)} (${esc(m.nick || m.user.username)})</option>`)
          .join('');
      }
    } catch (e) {}

    el.innerHTML = `
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;">🛡️ Moderation</div>

      <div class="discord-mod-form">
        <label>👤 Mitglied (ID)</label>
        <input type="text" id="discordModUserId" placeholder="User-ID eingeben..." style="font-family:monospace;font-size:12px;">
        <div style="font-size:11px;color:var(--muted);margin:-4px 0 8px;">Oder Member-ID aus dem Server kopieren</div>

        <label>📋 Aktion</label>
        <select id="discordModAction">
          <option value="warn">⚠️ Verwarnen</option>
          <option value="timeout">🔇 Timeout (Minuten)</option>
          <option value="kick">👢 Kicken</option>
          <option value="ban">🔨 Bannen</option>
        </select>

        <div id="discordModExtra" style="display:none;">
          <label>⏱ Minuten</label>
          <input type="number" id="discordModMinutes" value="10" min="1" max="40320">
        </div>

        <label>📝 Grund</label>
        <textarea id="discordModReason" placeholder="Grund für die Aktion..."></textarea>

        <button class="discord-btn discord-btn-danger" onclick="executeDiscordMod()">▶ Ausführen</button>
        <div id="discordModResult" style="margin-top:6px;font-size:12px;"></div>
      </div>

      <div style="font-size:13px;font-weight:600;margin:12px 0 8px;">🧹 Channel-Aktionen</div>
      <div class="discord-mod-form">
        <label>Nachrichten löschen (Channel-ID)</label>
        <input type="text" id="discordPurgeChannel" placeholder="Channel-ID..." style="font-family:monospace;font-size:12px;">
        <label>Anzahl (max 100)</label>
        <input type="number" id="discordPurgeAmount" value="20" min="1" max="100">
        <button class="discord-btn discord-btn-warning" onclick="executeDiscordPurge()">🧹 Löschen</button>
        <div id="discordPurgeResult" style="margin-top:6px;font-size:12px;"></div>
      </div>
    `;

    // Show minutes field when timeout selected
    const actionSelect = document.getElementById('discordModAction');
    const extraDiv = document.getElementById('discordModExtra');
    actionSelect.addEventListener('change', function() {
      extraDiv.style.display = this.value === 'timeout' ? 'block' : 'none';
    });
  }

  window.executeDiscordMod = async function () {
    const userId = document.getElementById('discordModUserId').value.trim();
    const action = document.getElementById('discordModAction').value;
    const reason = document.getElementById('discordModReason').value.trim() || 'Kein Grund';
    const resultDiv = document.getElementById('discordModResult');

    if (!userId) {
      resultDiv.innerHTML = '<span style="color:#ed4245;">❌ Bitte User-ID eingeben</span>';
      return;
    }

    resultDiv.innerHTML = '<span style="color:var(--muted);">⏳ Wird ausgeführt...</span>';

    let endpoint, payload;

    switch (action) {
      case 'warn':
        endpoint = '/warn';
        payload = { user_id: userId, reason };
        break;
      case 'timeout':
        const minutes = parseInt(document.getElementById('discordModMinutes').value) || 10;
        endpoint = '/timeout';
        payload = { user_id: userId, minutes, reason };
        break;
      case 'kick':
        endpoint = '/kick';
        payload = { user_id: userId, reason };
        break;
      case 'ban':
        endpoint = '/ban';
        payload = { user_id: userId, reason, delete_days: 0 };
        break;
    }

    const result = await _api(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    if (result.error) {
      resultDiv.innerHTML = `<span style="color:#ed4245;">❌ ${esc(result.message || 'Fehler')}</span>`;
    } else {
      const emoji = action === 'warn' ? '⚠️' : action === 'timeout' ? '🔇' : action === 'kick' ? '👢' : '🔨';
      resultDiv.innerHTML = `<span style="color:#57f287;">✅ ${emoji} ${action} erfolgreich!</span>`;
    }
  };

  window.executeDiscordPurge = async function () {
    const channelId = document.getElementById('discordPurgeChannel').value.trim();
    const amount = parseInt(document.getElementById('discordPurgeAmount').value) || 20;
    const resultDiv = document.getElementById('discordPurgeResult');

    if (!channelId) {
      resultDiv.innerHTML = '<span style="color:#ed4245;">❌ Bitte Channel-ID eingeben</span>';
      return;
    }

    resultDiv.innerHTML = '<span style="color:var(--muted);">⏳ Lösche...</span>';
    const result = await _api('/purge', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ channel_id: channelId, amount }),
    });

    resultDiv.innerHTML = `<span style="color:#57f287;">✅ ${result.deleted || 0} Nachrichten gelöscht</span>`;
  };

  /* ─── SETTINGS ─── */
  async function renderSettings(el) {
    el.innerHTML = '<div class="discord-loading"><div class="discord-shimmer"></div><span>Loading config...</span></div>';

    const cfgResp = await _api('/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'get' }),
    });

    const cfg = cfgResp.config || {};

    el.innerHTML = `
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;">⚙️ Bot-Einstellungen</div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:12px;">Änderungen werden sofort gespeichert.</div>
      <div id="discordSettingsForm">
        ${renderSettingToggle('welcome_enabled', '👋 Willkommensnachrichten', cfg.welcome_enabled)}
        ${renderSettingToggle('level_enabled', '📊 Level/XP System', cfg.level_enabled)}
        ${renderSettingToggle('auto_mod_enabled', '🛡️ Auto-Mod', cfg.auto_mod_enabled)}
        ${renderSettingNumber('xp_per_message', '⭐ XP pro Nachricht', cfg.xp_per_message, 5, 100)}
        ${renderSettingNumber('spam_threshold', '📨 Spam-Schwelle (Msgs/10s)', cfg.spam_threshold, 3, 20)}
        ${renderSettingNumber('spam_timeout_minutes', '🔇 Spam-Timeout (Minuten)', cfg.spam_timeout_minutes, 1, 1440)}
      </div>
      <div style="margin-top:8px;">
        <button class="discord-btn discord-btn-success" onclick="saveDiscordSettings()">💾 Alle speichern</button>
        <span id="discordSettingsResult" style="margin-left:8px;font-size:12px;"></span>
      </div>
    `;
  }

  function renderSettingToggle(key, label, value) {
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);">
        <span style="font-size:12px;">${label}</span>
        <label style="position:relative;display:inline-block;width:36px;height:20px;">
          <input type="checkbox" data-cfg-key="${key}" ${value ? 'checked' : ''}
            onchange="discordSettingChanged(this)" style="opacity:0;width:0;height:0;">
          <span style="position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:${value ? '#5865F2' : 'var(--border)'};border-radius:10px;transition:0.3s;">
            <span style="position:absolute;content:'';height:16px;width:16px;left:2px;bottom:2px;background:white;border-radius:50%;transition:0.3s;transform:${value ? 'translateX(16px)' : 'translateX(0)'};"></span>
          </span>
        </label>
      </div>
    `;
  }

  function renderSettingNumber(key, label, value, min, max) {
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border);">
        <span style="font-size:12px;">${label}</span>
        <input type="number" data-cfg-key="${key}" value="${value}" min="${min}" max="${max}"
          style="width:60px;padding:3px 6px;border:1px solid var(--border);border-radius:4px;background:var(--surface);color:var(--text);font-size:12px;text-align:center;">
      </div>
    `;
  }

  window.discordSettingChanged = function (el) {
    const span = el.nextElementSibling.querySelector('span') || el.nextElementSibling;
    if (span) {
      const isOn = el.checked;
      span.style.background = isOn ? '#5865F2' : 'var(--border)';
      const dot = span.querySelector('span');
      if (dot) dot.style.transform = isOn ? 'translateX(16px)' : 'translateX(0)';
    }
  };

  window.saveDiscordSettings = async function () {
    const values = {};
    document.querySelectorAll('[data-cfg-key]').forEach((el) => {
      const key = el.getAttribute('data-cfg-key');
      if (el.type === 'checkbox') {
        values[key] = el.checked;
      } else {
        values[key] = parseInt(el.value) || el.value;
      }
    });

    const result = await _api('/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: 'save', values }),
    });

    const resEl = document.getElementById('discordSettingsResult');
    if (result.status === 'saved') {
      resEl.innerHTML = '<span style="color:#57f287;">✅ Gespeichert!</span>';
      setTimeout(() => { resEl.innerHTML = ''; }, 3000);
    } else {
      resEl.innerHTML = '<span style="color:#ed4245;">❌ Fehler</span>';
    }
  };

  /* ─── LOGS ─── */
  async function renderLogs(el) {
    el.innerHTML = '<div class="discord-loading"><div class="discord-shimmer"></div><span>Loading logs...</span></div>';

    const warns = await _api('/warns');
    const entries = [];

    if (warns && !warns.error) {
      for (const [guildId, guildWarns] of Object.entries(warns)) {
        for (const [userId, userWarns] of Object.entries(guildWarns)) {
          userWarns.forEach((w) => {
            entries.push({
              type: 'warn',
              user_id: userId,
              reason: w.reason,
              moderator: w.moderator,
              time: w.time,
            });
          });
        }
      }
    }

    entries.sort((a, b) => (b.time || '').localeCompare(a.time || ''));

    if (entries.length === 0) {
      el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted);font-size:13px;">📝 Keine Moderation-Logs vorhanden.</div>';
      return;
    }

    el.innerHTML = `
      <div style="font-size:13px;font-weight:600;margin-bottom:8px;">📝 Moderations-Logs (${entries.length})</div>
      <div style="font-size:11px;color:var(--muted);margin-bottom:8px;">Letzte Aktionen zuerst</div>
      <div style="border:1px solid var(--border);border-radius:6px;overflow:hidden;">
        ${entries.slice(0, 100).map((e) => `
          <div class="discord-log-entry">
            <span class="discord-log-time">${(e.time || '').slice(0, 19).replace('T', ' ')}</span>
            <span class="discord-log-action">⚠️ Warn</span>
            für <span class="discord-log-user">${esc(e.user_id)}</span>
            · Grund: ${esc(e.reason || '?')}
            · von ${esc(e.moderator || 'System')}
          </div>
        `).join('')}
      </div>
    `;
  }
})();
