/**
 * Discord Full Chat View — 3-Column Layout
 * Left: Overview (Stats, Moderation, Settings, Logs)
 * Middle: Channel Tree (Categories + Channels)
 * Right: Channel Messages + Nova Chat Input
 */
(function () {
  'use strict';

  let _activeChannelId = null;
  let _channelTree = null;
  let _messageCache = {};
  let _loadingMessages = false;
  let _hasMoreMessages = true;
  let _oldestMessageId = null;
  let _hermesMode = 'discord'; // 'discord' or 'hermes'
  let _refreshTimer = null;
  let _overviewTab = 'dashboard';
  let _memberCache = [];

  const API = '/api/discord';

  function $(id) { return document.getElementById(id); }

  function esc(str) {
    const d = document.createElement('div');
    d.textContent = str || '';
    return d.innerHTML;
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

  function formatTime(ts) {
    return new Date(ts).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
  }

  function formatDate(ts) {
    const t = new Date(ts);
    const now = new Date();
    if (t.toDateString() === now.toDateString()) return 'Heute';
    const y = new Date(now); y.setDate(y.getDate()-1);
    if (t.toDateString() === y.toDateString()) return 'Gestern';
    return t.toLocaleDateString('de-DE', { weekday: 'long', day: 'numeric', month: 'long' });
  }

  function getInitials(name) { return name ? name.charAt(0).toUpperCase() : '?'; }

  function avatarColor(id) {
    const colors = ['#5865F2','#ED4245','#57F287','#FEE75C','#EB459E','#00A8FC','#FF73FA','#95EFE7'];
    let hash = 0;
    if (id) for (let i = 0; i < id.length; i++) hash = id.charCodeAt(i) + ((hash << 5) - hash);
    return colors[Math.abs(hash) % colors.length];
  }

    function getAvatarHtml(author, size) {
    if (!author || !author.id) return '';
    if (!size) size = 32;
    var id = author.id;
    var hash = author.avatar;
    var username = author.username || '?';
    var initial = getInitials(username);
    var attrs = ' style="width:100%;height:100%;object-fit:cover;border-radius:50%;"';
    var hide = ' onerror="this.style.display=';
    var nope = "'none'";

    if (hash) {
      var ext = hash.indexOf('a_') === 0 ? 'gif' : 'webp';
      var url = 'https://cdn.discordapp.com/avatars/' + id + '/' + hash + '.' + ext + '?size=' + size;
      return '<img src="' + esc(url) + '" alt=""' + attrs + hide + nope + '">';
    }

    var defIndex = 0;
    var disc = author.discriminator;
    if (disc && disc !== '0') {
      defIndex = parseInt(disc) % 5;
    } else {
      defIndex = (parseInt(id) >> 22) % 6;
    }
    var defUrl = 'https://cdn.discordapp.com/embed/avatars/' + defIndex + '.png';
    return '<img src="' + defUrl + '" alt=""' + attrs + hide + nope + '">';
  }

  function chIcon(type) {
    return type === 2 ? '🔊' : type === 5 ? '📢' : type === 13 ? '📡' : type === 15 ? '💬' : '#';
  }

  /* ─── Init ─── */
  window.discordChatInit = function () {
    const container = $('mainDiscord');
    if (!container) return;
    container.innerHTML = `
      <div class="discord-full-area">
        <!-- Column 1: Overview -->
        <div class="discord-col-overview">
          <div class="discord-tabs">
            <button class="discord-tab discord-active" data-ov-tab="dashboard" onclick="discordOvSwitchTab('dashboard')">📊 Dashboard</button>
            <button class="discord-tab" data-ov-tab="moderation" onclick="discordOvSwitchTab('moderation')">🛡️ Mod</button>
            <button class="discord-tab" data-ov-tab="settings" onclick="discordOvSwitchTab('settings')">⚙️ Einst.</button>
            <button class="discord-tab" data-ov-tab="logs" onclick="discordOvSwitchTab('logs')">📝 Logs</button>
          </div>
          <div class="discord-content" id="discordOvContent">
            <div class="discord-loading-state"><div class="discord-loading-spinner"></div><span>Lade Serverdaten...</span></div>
          </div>
        </div>
        <!-- Column 2: Channel Tree -->
        <div class="discord-col-nav">
          <div class="discord-col-nav-header">
            <div class="discord-col-nav-icon">🐾</div>
            <span id="discordNavGuildName">PawsUnited</span>
          </div>
          <div class="discord-col-nav-scroll" id="discordNavTree">
            <div class="discord-loading-state"><div class="discord-loading-spinner"></div><span>Channels...</span></div>
          </div>
        </div>
        <!-- Column 3: Messages + Nova Chat -->
        <div class="discord-col-main">
          <div class="discord-col-main-header" id="discordMainHeader">
            <span>💬 Willkommen</span>
          </div>
          <div class="discord-messages-scroll" id="discordMsgScroll">
            <div class="discord-welcome">
              <h2>🐾 PawsUnited Discord</h2>
              <p>Wähle einen Channel aus der Channel-Liste</p>
            </div>
          </div>
          <div class="discord-nova-area" id="discordNovaArea" style="display:none;">
            <div class="discord-nova-header">
              <span>💬 Nachricht an <strong id="discordNovaChannelLabel">#channel</strong></span>
              <div class="discord-nova-mode">
                <button class="discord-nova-mode-btn active" data-mode="discord" onclick="discordSetInputMode('discord')">📨 Discord</button>
                <button class="discord-nova-mode-btn" data-mode="nova" onclick="discordSetInputMode('hermes')">🤖 Nova</button>
              </div>
            </div>
            <div class="discord-nova-input-row">
              <textarea class="discord-nova-input" id="discordNovaInput"
                placeholder="Nachricht an #channel..."
                onkeydown="discordNovaKey(event)" rows="1"></textarea>
              <button class="discord-nova-send" onclick="discordNovaSend()" title="Senden">➤</button>
            </div>
          </div>
        </div>
      </div>
    `;
    loadOverviewTab('dashboard');
    loadChannelTree();
    startAutoRefresh();
  };

  window.discordChatDestroy = function () {
    stopAutoRefresh();
    teardownScrollPagination();
    _activeChannelId = null;
    _messageCache = {};
  };

  /* ═══════════════════ OVERVIEW (Column 1) ═══════════════════ */
  window.discordOvSwitchTab = function (tab) {
    _overviewTab = tab;
    document.querySelectorAll('[data-ov-tab]').forEach(t =>
      t.classList.toggle('discord-active', t.dataset.ovTab === tab));
    loadOverviewTab(tab);
  };

  async function loadOverviewTab(tab) {
    const el = $('discordOvContent');
    if (!el) return;
    if (tab === 'dashboard') return renderOvDashboard(el);
    if (tab === 'moderation') return renderOvModeration(el);
    if (tab === 'settings') return renderOvSettings(el);
    if (tab === 'logs') return renderOvLogs(el);
  }

  /* Dashboard */
  async function renderOvDashboard(el) {
    el.innerHTML = '<div class="discord-loading-state"><div class="discord-loading-spinner"></div><span>Lade...</span></div>';
    const [stats, channels, members] = await Promise.all([
      _api('/stats'), _api('/channels'), _api('/members'),
    ]);
    if (stats.error) {
      el.innerHTML = '<div style="padding:12px;color:#ed4245;text-align:center;font-size:12px;">Discord nicht verbunden<br><span style="color:var(--muted);">Bot-Token/Guild-ID in Appstore → Discord → App Settings prüfen.</span></div>';
      return;
    }
    const ch = stats.channels || {};
    el.innerHTML = `
      <div class="discord-ov-server">
        <div class="discord-ov-server-name">🐾 ${esc(stats.name)}</div>
        <div class="discord-ov-server-feats">⚡ Level ${stats.tier||0} · ${stats.boosts||0} Boosts</div>
      </div>
      <div class="discord-ov-stats-grid">
        <div class="discord-ov-stat-card"><div class="discord-ov-stat-val">${stats.member_count||'?'}</div><div class="discord-ov-stat-lbl">👥 Mitglieder</div></div>
        <div class="discord-ov-stat-card"><div class="discord-ov-stat-val">${stats.online||'?'}</div><div class="discord-ov-stat-lbl">🟢 Online</div></div>
        <div class="discord-ov-stat-card"><div class="discord-ov-stat-val">${ch.total||0}</div><div class="discord-ov-stat-lbl">💬 Channels</div></div>
        <div class="discord-ov-stat-card"><div class="discord-ov-stat-val">${stats.roles||'?'}</div><div class="discord-ov-stat-lbl">🎭 Rollen</div></div>
      </div>
      <div class="discord-ov-stat-card" style="margin-bottom:6px;">
        <div style="font-size:11px;color:var(--muted);">📁 ${ch.categories||0} Kat · 📝 ${ch.text||0} Text · 🔊 ${ch.voice||0} Voice</div>
      </div>
      <div style="font-size:11px;color:var(--muted);background:var(--surface-alt);border:1px solid var(--border);border-radius:6px;padding:8px;">
        <div style="font-weight:600;margin-bottom:4px;">🤖 Bots</div>
        ${members && members.bot_list && members.bot_list.length
          ? members.bot_list.map(b => '🤖 ' + esc(b.name)).join('<br>')
          : 'Keine Bots gefunden'}
        <div style="margin-top:4px;">👤 ${members?esc(members.humans):'?'} Menschen · 🤖 ${members?esc(members.bots):'?'} Bots</div>
      </div>
    `;
  }

  /* Moderation (mit Member Autocomplete) */
  async function renderOvModeration(el) {
    el.innerHTML = `
      <div style="font-size:12px;font-weight:600;margin-bottom:6px;">🛡️ Moderation</div>
      <div class="discord-ov-mod-form" style="position:relative;">
        <label>👤 Mitglied suchen (Name oder ID)</label>
        <div style="position:relative;">
          <input type="text" id="discordOvModUser" placeholder="Name oder User-ID..." style="font-family:monospace;width:100%;box-sizing:border-box;" autocomplete="off">
          <div id="discordOvModDropdown" style="display:none;position:absolute;top:100%;left:0;right:0;background:var(--surface,#313338);border:1px solid var(--border);border-radius:6px;max-height:200px;overflow-y:auto;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,0.3);"></div>
        </div>
        <div id="discordOvModSelected" style="font-size:11px;color:var(--muted);margin:-4px 0 6px;min-height:16px;"></div>
        <label>📋 Aktion</label>
        <select id="discordOvModAction">
          <option value="warn">⚠️ Verwarnen</option>
          <option value="timeout">🔇 Timeout</option>
          <option value="kick">👢 Kicken</option>
          <option value="ban">🔨 Bannen</option>
        </select>
        <div id="discordOvModExtra" style="display:none;">
          <label>⏱ Minuten</label>
          <input type="number" id="discordOvModMinutes" value="10" min="1" max="40320">
        </div>
        <label>📝 Grund</label>
        <textarea id="discordOvModReason" placeholder="Grund..."></textarea>
        <button class="discord-ov-btn discord-ov-btn-danger" onclick="discordOvExecMod()">▶ Ausführen</button>
        <div id="discordOvModResult" style="margin-top:4px;font-size:11px;"></div>
      </div>
      <div style="font-size:12px;font-weight:600;margin:8px 0 4px;">🧹 Purge</div>
      <div class="discord-ov-mod-form">
        <label>Channel-ID</label>
        <input type="text" id="discordOvPurgeChannel" placeholder="Channel-ID..." style="font-family:monospace;">
        <label>Anzahl (max 100)</label>
        <input type="number" id="discordOvPurgeAmount" value="20" min="1" max="100">
        <button class="discord-ov-btn discord-ov-btn-warning" onclick="discordOvExecPurge()">🧹 Löschen</button>
        <div id="discordOvPurgeResult" style="margin-top:4px;font-size:11px;"></div>
      </div>
    `;

    // Fetch members for autocomplete
    const memberData = await _api('/members');
    _memberCache = memberData.members || [];

    const userInput = document.getElementById('discordOvModUser');
    const dropdown = document.getElementById('discordOvModDropdown');
    const selected = document.getElementById('discordOvModSelected');

    if (userInput && dropdown) {
      userInput.addEventListener('input', function () {
        const val = this.value.toLowerCase().trim();
        if (!val) { dropdown.style.display = 'none'; if (selected) selected.textContent = ''; return; }

        const matches = _memberCache.filter(function (m) {
          var u = m.user || {};
          return (u.username || '').toLowerCase().indexOf(val) !== -1
            || (m.nick || '').toLowerCase().indexOf(val) !== -1
            || (u.id || '').indexOf(val) !== -1;
        }).slice(0, 20);

        if (!matches.length) { dropdown.style.display = 'none'; return; }

        dropdown.innerHTML = matches.map(function (m) {
          var u = m.user || {};
          var display = esc(m.nick || u.username || 'Unbekannt');
          var name = esc(u.username || '');
          var id = esc(u.id || '');
          var initial = getInitials(u.username);
          var bg = avatarColor(u.id);
          var display2 = (m.nick && m.nick !== u.username) ? esc(u.username) : '';
          return '<div style="display:flex;align-items:center;gap:6px;padding:5px 8px;cursor:pointer;font-size:12px;border-bottom:1px solid var(--border);"'
            + ' onmouseover="this.style.background=\'var(--hover-bg,rgba(255,255,255,0.05))\'"'
            + ' onmouseout="this.style.background=\'\'"'
            + ' onclick="selectDiscordMember(\'' + id + '\',\'' + display.replace(/'/g,"\\'") + '\')">'
            + '<span style="width:20px;height:20px;border-radius:50%;background:' + bg + ';display:flex;align-items:center;justify-content:center;font-size:10px;color:white;flex-shrink:0;">' + initial + '</span>'
            + '<span><strong>' + display + '</strong> <span style="color:var(--muted);font-size:10px;">' + display2 + '</span></span>'
            + '<span style="color:var(--muted);font-size:9px;margin-left:auto;font-family:monospace;">' + id.slice(0,10) + '</span>'
            + '</div>';
        }).join('');
        dropdown.style.display = '';
      });

      userInput.addEventListener('blur', function () {
        setTimeout(function () { dropdown.style.display = 'none'; }, 200);
      });

      userInput.addEventListener('focus', function () {
        if (this.value.trim() && _memberCache.length) {
          this.dispatchEvent(new Event('input'));
        }
      });
    }

    const sel = document.getElementById('discordOvModAction');
    if (sel) sel.onchange = function() {
      const e = document.getElementById('discordOvModExtra');
      if (e) e.style.display = this.value === 'timeout' ? 'block' : 'none';
    };
  }

  window.discordOvExecMod = async function () {
    const uid = document.getElementById('discordOvModUser')?.value.trim();
    const action = document.getElementById('discordOvModAction')?.value;
    const reason = document.getElementById('discordOvModReason')?.value.trim() || 'WebUI';
    const res = document.getElementById('discordOvModResult');
    if (!uid) { res.innerHTML = '<span style="color:#ed4245;">❌ User-ID fehlt</span>'; return; }
    res.innerHTML = '<span style="color:var(--muted);">⏳...</span>';
    let endpoint, payload;
    if (action === 'warn') { endpoint = '/warn'; payload = { user_id: uid, reason }; }
    else if (action === 'timeout') {
      endpoint = '/timeout';
      payload = { user_id: uid, minutes: parseInt(document.getElementById('discordOvModMinutes')?.value)||10, reason };
    }
    else if (action === 'kick') { endpoint = '/kick'; payload = { user_id: uid, reason }; }
    else if (action === 'ban') { endpoint = '/ban'; payload = { user_id: uid, reason, delete_days: 0 }; }
    const r = await _api(endpoint, { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) });
    res.innerHTML = r.error
      ? `<span style="color:#ed4245;">❌ ${esc(r.message||'Fehler')}</span>`
      : `<span style="color:#57f287;">✅ ${action} erfolgreich!</span>`;
  };

  window.discordOvExecPurge = async function () {
    const cid = document.getElementById('discordOvPurgeChannel')?.value.trim();
    const amt = parseInt(document.getElementById('discordOvPurgeAmount')?.value)||20;
    const res = document.getElementById('discordOvPurgeResult');
    if (!cid) { res.innerHTML = '<span style="color:#ed4245;">❌ Channel-ID fehlt</span>'; return; }
    res.innerHTML = '<span style="color:var(--muted);">⏳...</span>';
    const r = await _api('/purge', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({channel_id:cid, amount:amt}) });
    res.innerHTML = `<span style="color:#57f287;">✅ ${r.deleted||0} Nachrichten gelöscht</span>`;
  };

  /* Settings */
  /* --- Member Autocomplete --- */
  window.selectDiscordMember = function (id, display) {
    const input = document.getElementById("discordOvModUser");
    const selected = document.getElementById("discordOvModSelected");
    const dd = document.getElementById("discordOvModDropdown");
    if (input) input.value = id;
    if (selected) selected.innerHTML = "👤 <strong>" + esc(display) + "</strong> (ID: " + esc(id) + ")";
    if (dd) dd.style.display = "none";
  };

  /* Settings */
  async function renderOvSettings(el) {
    const cfgResp = await _api('/config', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'get'}) });
    const cfg = cfgResp.config || {};
    el.innerHTML = `
      <div style="font-size:12px;font-weight:600;margin-bottom:6px;">⚙️ Bot-Einstellungen</div>
      <div id="discordOvSettingsForm">
        ${settingToggle('welcome_enabled', '👋 Willkommen', cfg.welcome_enabled)}
        ${settingToggle('level_enabled', '📊 Level/XP', cfg.level_enabled)}
        ${settingToggle('auto_mod_enabled', '🛡️ Auto-Mod', cfg.auto_mod_enabled)}
        ${settingNum('xp_per_message', '⭐ XP/Nachricht', cfg.xp_per_message, 5, 100)}
        ${settingNum('spam_threshold', '📨 Spam-Schwelle', cfg.spam_threshold, 3, 20)}
        ${settingNum('spam_timeout_minutes', '🔇 Spam-Timeout', cfg.spam_timeout_minutes, 1, 1440)}
      </div>
      <button class="discord-ov-btn discord-ov-btn-success" onclick="discordOvSaveSettings()" style="margin-top:6px;">💾 Speichern</button>
      <span id="discordOvSettingsResult" style="margin-left:6px;font-size:11px;"></span>
    `;
  }

  function settingToggle(key, label, val) {
    return `<div class="discord-ov-setting-row"><span>${label}</span>
      <label style="position:relative;display:inline-block;width:32px;height:18px;">
        <input type="checkbox" data-sk="${key}" ${val?'checked':''}
          onchange="var s=this.nextElementSibling;if(s){s.style.background=this.checked?'#5865F2':'var(--border)';var d=s.querySelector('span');if(d)d.style.transform=this.checked?'translateX(14px)':'translateX(0)';}"
          style="opacity:0;width:0;height:0;">
        <span style="position:absolute;cursor:pointer;top:0;left:0;right:0;bottom:0;background:${val?'#5865F2':'var(--border)'};border-radius:9px;transition:0.3s;">
          <span style="position:absolute;height:14px;width:14px;left:2px;bottom:2px;background:white;border-radius:50%;transition:0.3s;transform:${val?'translateX(14px)':'translateX(0)'};"></span>
        </span>
      </label></div>`;
  }
  function settingNum(key, label, val, min, max) {
    return `<div class="discord-ov-setting-row"><span>${label}</span>
      <input type="number" data-sk="${key}" value="${val}" min="${min}" max="${max}"
        style="width:50px;padding:2px 4px;border:1px solid var(--border);border-radius:3px;background:var(--surface);color:var(--text);font-size:11px;text-align:center;"></div>`;
  }

  window.discordOvSaveSettings = async function () {
    const vals = {};
    document.querySelectorAll('[data-sk]').forEach(el => {
      vals[el.dataset.sk] = el.type === 'checkbox' ? el.checked : parseInt(el.value) || el.value;
    });
    const r = await _api('/config', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({action:'save',values:vals}) });
    const res = document.getElementById('discordOvSettingsResult');
    if (res) { res.innerHTML = r.status === 'saved' ? '<span style="color:#57f287;">✅</span>' : '<span style="color:#ed4245;">❌</span>'; }
  };

  /* Logs */
  async function renderOvLogs(el) {
    const warns = await _api('/warns');
    const entries = [];
    if (warns && !warns.error) {
      for (const [gid, gw] of Object.entries(warns))
        for (const [uid, uw] of Object.entries(gw))
          uw.forEach(w => entries.push({type:'warn',user_id:uid,reason:w.reason,mod:w.moderator,time:w.time}));
    }
    entries.sort((a,b) => (b.time||'').localeCompare(a.time||''));
    if (!entries.length) {
      el.innerHTML = '<div style="padding:20px;text-align:center;color:var(--muted);font-size:12px;">Keine Logs</div>';
      return;
    }
    el.innerHTML = `<div style="font-size:12px;font-weight:600;margin-bottom:6px;">📝 Logs (${entries.length})</div>
      ${entries.slice(0,50).map(e => `<div class="discord-ov-log-entry">
        <span class="discord-ov-log-time">${(e.time||'').slice(0,19).replace('T',' ')}</span>
        ⚠️ ${esc(e.reason||'?')} — ${esc(e.user_id)}</div>`).join('')}`;
  }

  /* ═══════════════════ CHANNEL TREE (Column 2) ═══════════════════ */
  async function loadChannelTree() {
    const data = await _api('/channels/tree');
    const el = $('discordNavTree');
    if (!el) return;
    if (data.error) {
      el.innerHTML = '<div class="discord-empty-state">Channels konnten nicht geladen werden.<br><span style="font-size:11px;color:var(--muted);">Discord App Settings für diesen Space prüfen.</span></div>';
      return;
    }
    _channelTree = data;
    let html = '';
    for (const cat of data.categories || []) {
      html += `<div class="discord-category"><div class="discord-category-header" onclick="discordCatToggle(this)">${esc(cat.name)}</div>`;
      for (const ch of cat.channels) {
        html += `<div class="discord-channel-item" data-cid="${esc(ch.id)}" onclick="discordSelectChannel('${esc(ch.id)}')">
          <span class="discord-channel-icon">${chIcon(ch.type)}</span>
          <span class="discord-channel-name">${esc(ch.name)}</span>
        </div>`;
      }
      html += `</div>`;
    }
    if (data.uncategorized && data.uncategorized.length) {
      html += `<div class="discord-category"><div class="discord-category-header" onclick="discordCatToggle(this)">Ohne Kategorie</div>`;
      for (const ch of data.uncategorized) {
        html += `<div class="discord-channel-item" data-cid="${esc(ch.id)}" onclick="discordSelectChannel('${esc(ch.id)}')">
          <span class="discord-channel-icon">${chIcon(ch.type)}</span>
          <span class="discord-channel-name">${esc(ch.name)}</span>
        </div>`;
      }
      html += `</div>`;
    }
    el.innerHTML = html || '<div class="discord-empty-state">Keine Channels</div>';
  }

  window.discordCatToggle = function (el) {
    const cat = el.closest('.discord-category');
    if (cat) cat.classList.toggle('collapsed');
  };

  /* ═══════════════════ MESSAGES (Column 3) ═══════════════════ */
  window.discordSelectChannel = async function (channelId) {
    _activeChannelId = channelId;
    _messageCache = {};
    _hasMoreMessages = true;
    _oldestMessageId = null;
    teardownScrollPagination();

    document.querySelectorAll('.discord-channel-item').forEach(el =>
      el.classList.toggle('active', el.dataset.cid === channelId));

    // Update header
    const chEl = document.querySelector(`.discord-channel-item[data-cid="${channelId}"]`);
    const chName = chEl ? chEl.querySelector('.discord-channel-name').textContent : channelId;
    $('discordMainHeader').innerHTML = `<span># ${esc(chName)}</span>`;

    // Show input area
    $('discordNovaArea').style.display = '';
    $('discordNovaInput').placeholder = `Nachricht an #${chName}`;
    $('discordNovaChannelLabel').textContent = '#' + chName;

    // Loading
    $('discordMsgScroll').innerHTML = '<div class="discord-loading-state"><div class="discord-loading-spinner"></div><span>Lade Nachrichten...</span></div>';

    const data = await _api(`/channel/${channelId}/messages?limit=50`);
    if (data.error) {
      $('discordMsgScroll').innerHTML = '<div class="discord-empty-state">❌ Fehler beim Laden</div>';
      return;
    }

    const msgs = data.messages || [];
    if (!msgs.length) {
      $('discordMsgScroll').innerHTML = '<div class="discord-empty-state"><div style="font-size:32px;">💬</div><span>Noch keine Nachrichten</span></div>';
      return;
    }

    _hasMoreMessages = msgs.length >= 50;
    _oldestMessageId = msgs[msgs.length - 1].id;
    for (const m of msgs) _messageCache[m.id] = m;
    renderMessages(msgs, 'replace');
    setupScrollPagination();
  };

  function renderMessages(messages, mode) {
    const scrollEl = $('discordMsgScroll');
    if (!scrollEl) return;
    const sorted = [...messages].sort((a,b) => new Date(a.timestamp) - new Date(b.timestamp));
    let html = '';
    let lastAuthor = null;
    let lastDate = null;

    for (const msg of sorted) {
      const author = msg.author || {};
      const msgDate = formatDate(msg.timestamp);
      if (msgDate !== lastDate) {
        html += `<div style="text-align:center;font-size:11px;color:var(--muted);padding:8px 0 2px;position:relative;"><span style="background:var(--bg,#313338);padding:0 6px;">${esc(msgDate)}</span></div>`;
        lastDate = msgDate;
        lastAuthor = null;
      }
      const compact = (author.id === lastAuthor) && lastAuthor !== null;
      const isBot = author.bot;
      const time = formatTime(msg.timestamp);
      const color = avatarColor(author.id);
      const content = linkify(esc(msg.content || ''));


      if (compact) {
        html += `<div class="discord-message compact ${isBot?'bot-message':''}">
          <div class="discord-message-body"><div class="discord-message-content">${content}</div></div>
        </div>`;
      } else {
        html += `<div class="discord-message ${isBot?'bot-message':''}">
          <div class="discord-message-avatar" style="background:${color};">${getAvatarHtml(author, 32)}</div>
          <div class="discord-message-body">
            <div class="discord-message-author">
              <span style="color:${color};">${esc(author.username||'Unbekannt')}</span>
              <span class="discord-message-time">${time}</span>
              ${isBot ? '<span style="font-size:9px;background:var(--accent);color:white;padding:1px 3px;border-radius:2px;">BOT</span>' : ''}
            </div>
            <div class="discord-message-content">${content}</div>
          </div>
        </div>`;
      }
      lastAuthor = author.id;
    }

    if (mode === 'replace') {
      scrollEl.innerHTML = html;
      scrollEl.scrollTop = scrollEl.scrollHeight;
    } else if (mode === 'prepend') {
      const prevHeight = scrollEl.scrollHeight;
      scrollEl.insertAdjacentHTML('afterbegin', html);
      scrollEl.scrollTop = scrollEl.scrollHeight - prevHeight;
    }
  }

  /* ═══════════════════ PAGINATION (load older messages) ═══════════════════ */
  async function loadMoreMessages() {
    if (_loadingMessages || !_hasMoreMessages || !_activeChannelId) return;
    _loadingMessages = true;

    const scrollEl = $('discordMsgScroll');
    if (!scrollEl) { _loadingMessages = false; return; }

    const beforeId = _oldestMessageId;
    const prevHeight = scrollEl.scrollHeight;

    // Loading indicator at top
    scrollEl.insertAdjacentHTML('afterbegin',
      '<div class="discord-loading-more" id="discordLoadingMore" style="text-align:center;padding:8px;font-size:11px;color:var(--muted);">' +
      '<div class="discord-loading-spinner" style="width:14px;height:14px;border-width:2px;display:inline-block;vertical-align:middle;margin-right:6px;"></div>' +
      '<span style="vertical-align:middle;">Lade ältere Nachrichten...</span></div>');

    const data = await _api(`/channel/${_activeChannelId}/messages?limit=50&before=${beforeId}`);

    const loadingEl = document.getElementById('discordLoadingMore');
    if (loadingEl) loadingEl.remove();

    const msgs = data.messages || [];
    if (!msgs.length) {
      _hasMoreMessages = false;
      _loadingMessages = false;
      scrollEl.insertAdjacentHTML('afterbegin',
        '<div style="text-align:center;padding:10px;font-size:11px;color:var(--muted);">📭 Keine weiteren Nachrichten</div>');
      return;
    }

    _hasMoreMessages = msgs.length >= 50;
    _oldestMessageId = msgs[msgs.length - 1].id;
    for (const m of msgs) _messageCache[m.id] = m;
    renderMessages(msgs, 'prepend');
    _loadingMessages = false;
  }

  function setupScrollPagination() {
    const scrollEl = $('discordMsgScroll');
    if (!scrollEl) return;
    if (scrollEl._discordScrollHandler) {
      scrollEl.removeEventListener('scroll', scrollEl._discordScrollHandler);
    }
    scrollEl._discordScrollHandler = function () {
      if (_loadingMessages || !_hasMoreMessages || !_activeChannelId) return;
      if (this.scrollTop > 100) return;
      loadMoreMessages();
    };
    scrollEl.addEventListener('scroll', scrollEl._discordScrollHandler);
  }

  function teardownScrollPagination() {
    const scrollEl = $('discordMsgScroll');
    if (!scrollEl || !scrollEl._discordScrollHandler) return;
    scrollEl.removeEventListener('scroll', scrollEl._discordScrollHandler);
    scrollEl._discordScrollHandler = null;
  }

  function linkify(text) {
    // Step 1: Replace Discord patterns with unique markers before escaping
    var markers = {};
    var i = 0;
    
    // Custom emoji <:name:id>
    text = text.replace(/<:([a-zA-Z0-9_]+):(\d+)>/g, function(m, name, id) {
      var k = '☐' + (i++) + '☐';
      markers[k] = '<span style="display:inline-flex;align-items:center;gap:2px;background:var(--surface-alt);padding:1px 4px;border-radius:3px;font-size:12px;">⬛ :' + name + ':</span>';
      return k;
    });
    // Animated emoji <a:name:id>
    text = text.replace(/<a:([a-zA-Z0-9_]+):(\d+)>/g, function(m, name, id) {
      var k = '☐' + (i++) + '☐';
      markers[k] = '<span style="display:inline-flex;align-items:center;gap:2px;background:var(--surface-alt);padding:1px 4px;border-radius:3px;font-size:12px;">⬛ :' + name + ':</span>';
      return k;
    });
    // Role mention <@&id>
    text = text.replace(/<@&(\d+)>/g, function(m, id) {
      var k = '☐' + (i++) + '☐';
      markers[k] = '<span style="background:var(--accent-bg,rgba(88,101,242,0.2));color:var(--accent,#5865F2);padding:1px 5px;border-radius:3px;font-size:12px;font-weight:500;">@role-' + id + '</span>';
      return k;
    });
    // User mention <@!id> or <@id>
    text = text.replace(/<@!?(\d+)>/g, function(m, id) {
      var k = '☐' + (i++) + '☐';
      markers[k] = '<span style="color:#5865F2;font-weight:500;">@' + id + '</span>';
      return k;
    });
    // Channel mention <#id>
    text = text.replace(/<#(\d+)>/g, function(m, id) {
      var k = '☐' + (i++) + '☐';
      markers[k] = '<span style="color:#5865F2;">#' + id + '</span>';
      return k;
    });
    // Markdown code blocks (protect from escaping)
    text = text.replace(/```(\w*)\n([\s\S]*?)```/g, function(m, lang, code) {
      var k = '☐' + (i++) + '☐';
      markers[k] = '<pre style="background:var(--surface);border:1px solid var(--border);border-radius:4px;padding:8px;font-size:12px;overflow-x:auto;"><code>' + esc(code) + '</code></pre>';
      return k;
    });
    // Inline code
    text = text.replace(/`([^`]+)`/g, function(m, code) {
      var k = '☐' + (i++) + '☐';
      markers[k] = '<code style="background:var(--surface);padding:1px 3px;border-radius:3px;font-size:12px;">' + esc(code) + '</code>';
      return k;
    });
    
    // Step 2: HTML-escape whatever's left
    text = esc(text);
    
    // Step 3: Apply markdown formatting on escaped text
    // @everyone/@here (must be after esc because @ isn't escaped)
    text = text.replace(/@(everyone|here)/g, '<span style="color:#5865F2;font-weight:600;">@$1</span>');
    // Bold
    text = text.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    // Italic
    text = text.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // Markdown headers
    text = text.replace(/^### (.+)$/gm, '<div style="font-size:13px;font-weight:600;margin:4px 0 2px;">$1</div>');
    text = text.replace(/^## (.+)$/gm, '<div style="font-size:15px;font-weight:700;margin:6px 0 2px;">$1</div>');
    text = text.replace(/^# (.+)$/gm, '<div style="font-size:17px;font-weight:700;margin:8px 0 4px;">$1</div>');
    // URLs
    text = text.replace(/(https?:\/\/[^\s<]+)/g, '<a href="$1" target="_blank" style="color:var(--link,#00a8fc);">$1</a>');
    // Line breaks
    text = text.replace(/\n/g, '<br>');
    
    // Step 4: Restore markers with their HTML replacements
    for (var k in markers) {
      text = text.split(k).join(markers[k]);
    }
    return text;
  }

  /* ═══════════════════ HERMES CHAT INPUT ═══════════════════ */
  window.discordSetInputMode = function (mode) {
    _hermesMode = mode;
    document.querySelectorAll('.discord-nova-mode-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.mode === mode));
    const input = $('discordNovaInput');
    if (mode === 'discord') {
      const chName = $('discordNovaChannelLabel')?.textContent || '#channel';
      input.placeholder = `Nachricht an ${chName}...`;
    } else {
      input.placeholder = '🤖 Frage Nova zum Channel...';
    }
  };

  window.discordNovaKey = function (e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      discordNovaSend();
    }
    const el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  };

  window.discordNovaSend = async function () {
    const input = $('discordNovaInput');
    const text = input.value.trim();
    if (!text) return;
    input.value = '';
    input.style.height = 'auto';

    if (_hermesMode === 'discord') {
      // Send to Discord channel
      if (!_activeChannelId) {
        showNovaResponse('❌ Kein Channel ausgewählt');
        return;
      }
      const scrollEl = $('discordMsgScroll');
      scrollEl.insertAdjacentHTML('beforeend', `<div class="discord-message compact"><div class="discord-message-body"><div class="discord-message-content" style="color:var(--muted);">⏳ Sende...</div></div></div>`);
      scrollEl.scrollTop = scrollEl.scrollHeight;

      const result = await _api('/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ channel_id: _activeChannelId, content: text }),
      });

      // Remove optimistic message
      const lastChild = scrollEl.lastElementChild;
      if (lastChild && lastChild.textContent.includes('⏳')) lastChild.remove();

      if (result.error) {
        showNovaResponse(`❌ ${esc(result.message||'Fehler')}`);
      } else if (result.id) {
        _messageCache[result.id] = result;
        const msg = result;
        const author = msg.author || {};
        const html = `<div class="discord-message${author.bot?' bot-message':''}">
          <div class="discord-message-avatar" style="background:${avatarColor(author.id)};">${getAvatarHtml(author, 32)}</div>
          <div class="discord-message-body">
            <div class="discord-message-author">
              <span style="color:${avatarColor(author.id)};">${esc(author.username||'Bot')}</span>
              <span class="discord-message-time">${formatTime(msg.timestamp)}</span>
            </div>
            <div class="discord-message-content">${linkify(esc(msg.content||''))}</div>
          </div>
        </div>`;
        scrollEl.insertAdjacentHTML('beforeend', html);
        scrollEl.scrollTop = scrollEl.scrollHeight;
      }
    } else {
      // Nova mode: AI Chat via WebUI Agent (POST /api/chat)
      if (!window.S || !window.S.session || !window.S.session.session_id) {
        showNovaResponse('❌ Keine aktive Chat-Session. Bitte zuerst eine Nachricht im WebUI-Chat senden.');
        return;
      }
      if (window.S.busy) {
        showNovaResponse('⏳ Nova ist gerade beschäftigt. Bitte warten bis die aktuelle Antwort fertig ist.');
        return;
      }

      const sessionId = window.S.session.session_id;
      const scrollEl = $('discordMsgScroll');

      // Show user message in Discord chat style
      const userHtml = `<div class="discord-message">
        <div class="discord-message-avatar" style="background:#3ba55c;">Du</div>
        <div class="discord-message-body">
          <div class="discord-message-author">
            <span style="color:#3ba55c;">Du</span>
            <span class="discord-message-time">${formatTime(new Date().toISOString())}</span>
          </div>
          <div class="discord-message-content">${linkify(esc(text))}</div>
        </div>
      </div>`;
      scrollEl.insertAdjacentHTML('beforeend', userHtml);

      // Show thinking indicator
      const thinkHtml = `<div class="discord-message bot-message" id="hermesThinking">
        <div class="discord-message-avatar" style="background:#5865F2;">H</div>
        <div class="discord-message-body">
          <div class="discord-message-author">
            <span style="color:#5865F2;">Sidekick</span>
            <span class="discord-message-time">${formatTime(new Date().toISOString())}</span>
          </div>
          <div class="discord-message-content"><em>denkt nach...</em></div>
        </div>
      </div>`;
      scrollEl.insertAdjacentHTML('beforeend', thinkHtml);
      scrollEl.scrollTop = scrollEl.scrollHeight;

      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: sessionId, message: text }),
        });
        const data = await resp.json();

        // Remove thinking indicator
        const thinking = document.getElementById('hermesThinking');
        if (thinking) thinking.remove();

        if (data.answer) {
          const answerHtml = `<div class="discord-message bot-message">
            <div class="discord-message-avatar" style="background:#5865F2;">H</div>
            <div class="discord-message-body">
              <div class="discord-message-author">
                <span style="color:#5865F2;">Sidekick</span>
                <span class="discord-message-time">${formatTime(new Date().toISOString())}</span>
              </div>
              <div class="discord-message-content">${linkify(esc(data.answer))}</div>
            </div>
          </div>`;
          scrollEl.insertAdjacentHTML('beforeend', answerHtml);
        } else {
          showNovaResponse('❌ Keine Antwort erhalten.');
        }
      } catch (e) {
        const thinking = document.getElementById('hermesThinking');
        if (thinking) thinking.remove();
        showNovaResponse('❌ Fehler: ' + esc(e.message));
      }
      scrollEl.scrollTop = scrollEl.scrollHeight;
    }
  };

  function showNovaResponse(msg) {
    const scrollEl = $('discordMsgScroll');
    if (!scrollEl) return;
    scrollEl.insertAdjacentHTML('beforeend', `<div class="discord-message bot-message">
      <div class="discord-message-avatar" style="background:#5865F2;">H</div>
      <div class="discord-message-body">
        <div class="discord-message-author">
          <span style="color:#5865F2;">Sidekick</span>
          <span class="discord-message-time">${formatTime(new Date().toISOString())}</span>
        </div>
        <div class="discord-message-content">${msg}</div>
      </div>
    </div>`);
    scrollEl.scrollTop = scrollEl.scrollHeight;
  }

  /* ═══════════════════ AUTO-REFRESH ═══════════════════ */
  function startAutoRefresh() {
    stopAutoRefresh();
    _refreshTimer = setInterval(async () => {
      // Pausieren wenn Tab nicht sichtbar ist
      if (document.hidden) return;
      if (_activeChannelId && !_loadingMessages) {
        const scrollEl = $('discordMsgScroll');
        const nearBottom = scrollEl && (scrollEl.scrollHeight - scrollEl.scrollTop - scrollEl.clientHeight < 80);
        const data = await _api(`/channel/${_activeChannelId}/messages?limit=5`);
        if (data.messages && data.messages.length) {
          for (const msg of data.messages) {
            if (!_messageCache[msg.id]) {
              _messageCache[msg.id] = msg;
              const author = msg.author || {};
              const html = `<div class="discord-message${author.bot?' bot-message':''}">
                <div class="discord-message-avatar" style="background:${avatarColor(author.id)};">${getAvatarHtml(author, 32)}</div>
                <div class="discord-message-body">
                  <div class="discord-message-author">
                    <span style="color:${avatarColor(author.id)};">${esc(author.username||'')}</span>
                    <span class="discord-message-time">${formatTime(msg.timestamp)}</span>
                  </div>
                  <div class="discord-message-content">${linkify(esc(msg.content||''))}</div>
                </div>
              </div>`;
              scrollEl.insertAdjacentHTML('beforeend', html);
              if (nearBottom) scrollEl.scrollTop = scrollEl.scrollHeight;
            }
          }
        }
      }
    }, 15000);
  }

  function stopAutoRefresh() {
    if (_refreshTimer) { clearInterval(_refreshTimer); _refreshTimer = null; }
  }

  /* ═══════════════════ TOGGLE FULL VIEW ═══════════════════ */
  window.toggleDiscordFullView = function () {
    // Delegiert an switchPanel fuer konsistentes Lifecycle-Management
    if (typeof switchPanel !== 'function') return;
    const discordEl = document.getElementById('mainDiscord');
    if (!discordEl) return;
    switchPanel(discordEl.style.display !== 'none' ? 'chat' : 'discord');
  };

  /* ═══════════════════ COLUMN RESIZE ═══════════════════ */
  window.initDiscordColumnResize = function () {
    const area = document.querySelector('.discord-full-area');
    if (!area) return;
    // Ensure handles exist
    if (area.querySelector('.discord-col-handle')) return;
    const cols = area.querySelectorAll('.discord-col-overview, .discord-col-nav, .discord-col-main');
    if (cols.length < 3) return;

    function createHandle(leftCol, rightCol) {
      const handle = document.createElement('div');
      handle.className = 'discord-col-handle';
      handle.style.cssText = 'width:4px;cursor:col-resize;flex-shrink:0;background:var(--border,rgba(255,255,255,0.08));transition:background .15s;position:relative;z-index:2;';
      handle.onmouseover = function () { this.style.background = 'var(--accent,#7c5cfc)'; };
      handle.onmouseout = function () { this.style.background = 'var(--border,rgba(255,255,255,0.08))'; };
      handle.onmousedown = function (e) {
        e.preventDefault();
        const startX = e.clientX;
        const startLeft = leftCol.offsetWidth;
        const startRight = rightCol.offsetWidth;
        const total = startLeft + startRight + 4; // 4px handle width

        function onMove(ev) {
          const dx = ev.clientX - startX;
          const flex = leftCol.parentElement;
          const newLeft = Math.max(120, Math.min(total - 60, startLeft + dx));
          const newRight = total - newLeft - 4;
          leftCol.style.width = newLeft + 'px';
          rightCol.style.width = newRight + 'px';
          leftCol.style.flex = '0 0 auto';
          rightCol.style.flex = '0 0 auto';
        }
        function onUp() {
          document.removeEventListener('mousemove', onMove);
          document.removeEventListener('mouseup', onUp);
          document.body.style.cursor = '';
          document.body.style.userSelect = '';
        }
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
        document.body.style.cursor = 'col-resize';
        document.body.style.userSelect = 'none';
      };
      leftCol.parentElement.insertBefore(handle, rightCol);
    }

    createHandle(cols[0], cols[1]);
    createHandle(cols[1], cols[2]);
  };

})();

