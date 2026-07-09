let _currentPanel = 'chat';
let _renamingAppTitlebar = false;  // guard against re-entrant rename
let _kanbanBoard = null;
let _kanbanLatestEventId = 0;
let _kanbanPollTimer = null;
let _kanbanCurrentTaskId = null;
let _kanbanLanesByProfile = false;
// Multi-board state. _kanbanCurrentBoard is the slug of the active board
// the UI is currently viewing. null means "use whatever the server reports
// as active" (i.e. don't pin a specific board in API calls). The UI
// persists the last-viewed slug to localStorage so refresh stays put.
let _kanbanCurrentBoard = null;
let _kanbanBoardsList = null;
let _kanbanBoardMenuOpen = false;
let _kanbanIsDispatching = false;
// SSE event stream — replaces the 30s polling cadence with a long-lived
// /api/kanban/events/stream connection. Falls back to polling when the
// EventSource fails to connect (proxy that strips text/event-stream, etc).
let _kanbanEventSource = null;
let _kanbanEventSourceFailures = 0;
let _skillsData = null; // cached skills list
let _cronList = null; // cached cron jobs (array)
let _currentCronDetail = null; // full cron job object
let _cronMode = 'empty'; // 'empty' | 'read' | 'create' | 'edit'
let _cronPreFormDetail = null; // snapshot of prior selection when entering a form
let _currentWorkspaceDetail = null; // { path, name, is_default }
let _workspaceMode = 'empty'; // 'empty' | 'read' | 'create' | 'edit'
let _workspacePreFormDetail = null;
let _workspacesData = null; // cached workspace list for filtering
let _currentProfileDetail = null; // full profile object
let _profileMode = 'empty'; // 'empty' | 'read' | 'create'
let _profilePreFormDetail = null;
const _switchPanelTimers = [];
let _pendingSettingsTargetPanel = null; // destination selected while settings had unsaved changes
let _logsAutoRefreshTimer = null;
let _lastLogsLines = [];
let _logsSeverityFilter = 'all';

function _renderNovaRouteStatus(payload){
  const el = $('settingsModelRouteStatus');
  if(!el) return;
  if(!payload || payload.healthy === false){
    const reason = payload && payload.reason ? String(payload.reason) : 'unavailable';
    el.innerHTML = `<strong>Nova worker routes</strong><div style="margin-top:6px;color:var(--danger)">Unavailable: ${esc(reason)}</div>`;
    return;
  }
  const preferred = (payload && payload.preferred_routes) || {};
  const auth = (payload && payload.auth_pool_summary) || {};
  const roleCandidates = (payload && payload.role_candidates) || {};
  const authCount = (primaryKey, fallbackKey) => {
    const direct = Number(auth[primaryKey]);
    if(Number.isFinite(direct) && direct >= 0) return direct;
    const fallback = Number(auth[fallbackKey]);
    if(Number.isFinite(fallback) && fallback >= 0) return fallback;
    return 0;
  };
  const roleCount = (role) => {
    const items = roleCandidates[role];
    return Array.isArray(items) ? items.length : 0;
  };
  const routeLine = (label, key) => {
    const route = preferred[key] || {};
    const count = roleCount(key);
    if(!route.selected){
      return `<div><strong>${esc(label)}:</strong> unavailable <span style="color:var(--muted)">(${count} ready)</span></div>`;
    }
    const provider = route.provider || 'unknown';
    const model = route.model || 'unknown';
    const slotId = route.slot_id || route.slot || '';
    const slotText = slotId ? ` <span style="color:var(--muted)">[${esc(slotId)}]</span>` : '';
    return `<div><strong>${esc(label)}:</strong> ${esc(provider)} / ${esc(model)}${slotText} <span style="color:var(--muted)">(${count} ready)</span></div>`;
  };
  const routerCandidates = Array.isArray(payload.router_candidates) ? payload.router_candidates.length : 0;
  const freeOllama = authCount('ollama_free_ready', 'ollama-cloud');
  const opencodeReady = authCount('opencode_ready', 'opencode-go');
  const openrouterReady = authCount('openrouter_ready', 'openrouter');
  const cheapReady = roleCount('cheap');
  const strongReady = roleCount('strong');
  const contentReady = roleCount('content');
  el.innerHTML =
    `<strong>Nova worker routes</strong>`
    + `<div style="margin-top:6px">${routeLine('Router', 'router')}</div>`
    + `${routeLine('Simple / research', 'research')}`
    + `${routeLine('Stronger tasks', 'strong')}`
    + `${routeLine('Content', 'content')}`
    + `${routeLine('Subagent', 'subagent')}`
    + `<div style="margin-top:8px">`
    + `<strong>Ready now:</strong> cheap ${cheapReady} · strong ${strongReady} · content ${contentReady}`
    + `</div>`
    + `<div style="margin-top:8px;color:var(--muted)">`
    + `Router candidates: ${routerCandidates} · Free Ollama workers ready: ${freeOllama} · OpenRouter workers ready: ${openrouterReady} · Opencode workers ready: ${opencodeReady}`
    + `</div>`;
}

async function _loadNovaRouteStatus(){
  const el = $('settingsModelRouteStatus');
  if(!el) return;
  el.textContent = 'Loading Nova worker routes...';
  try{
    const payload = await api('/api/nova/routes');
    _renderNovaRouteStatus(payload);
  }catch(err){
    _renderNovaRouteStatus({healthy:false,reason:err && err.message ? err.message : String(err)});
  }
}

// Map of panel names → i18n keys for the app titlebar label.
const APP_TITLEBAR_KEYS = {
  chat: 'tab_chat', tasks: 'tab_tasks', skills: 'tab_skills',
  memory: 'tab_memory', workspaces: 'tab_workspaces',
  profiles: 'tab_profiles', todos: 'tab_todos', insights: 'tab_insights', logs: 'tab_logs', settings: 'tab_settings',
  agents: 'tab_agents',
};

/**
 * Update the top app titlebar to reflect the current page or selected conversation.
 * On the chat panel, a selected session's title takes precedence over the page name.
 */
function syncAppTitlebar() {
  const titleEl = document.getElementById('appTitlebarTitle');
  const subEl = document.getElementById('appTitlebarSub');
  if (!titleEl) return;
  const panel = (typeof _currentPanel === 'string' && _currentPanel) ? _currentPanel : 'chat';
  let mainText = '';
  let subText = '';
  let sourceLabel = '';
  if (panel === 'chat' && typeof S !== 'undefined' && S && S.session) {
    const rawTitle = (S.session.title || '').trim();
    mainText = rawTitle && rawTitle !== 'Untitled'
      ? rawTitle
      : (typeof t === 'function' ? t('new_chat') : 'New chat');
    const vis = Array.isArray(S.messages) ? S.messages.filter(m => m && m.role && m.role !== 'tool') : [];
    if (vis.length && typeof t === 'function') subText = t('n_messages', vis.length);
    if (S.session.is_cli_session) sourceLabel = S.session.source_label || S.session.source_tag || S.session.raw_source || '';
  } else if (panel === 'chat') {
    mainText = typeof t === 'function' ? t('new_chat') : 'New chat';
  } else {
    const key = APP_TITLEBAR_KEYS[panel];
    mainText = key && typeof t === 'function' ? t(key) : (panel.charAt(0).toUpperCase() + panel.slice(1));
  }

  // Don't touch the element while an inline rename is in progress — replacing
  // the span with an input would fire a MutationObserver that calls
  // syncAppTitlebar again, destroying the input before the user finishes.
  if (_renamingAppTitlebar) return;

  titleEl.textContent = mainText;
  if (subEl) {
    if (subText) {
      subEl.textContent = subText;
      if (sourceLabel) {
        const badge = document.createElement('span');
        badge.className = 'topbar-source-badge';
        badge.textContent = sourceLabel + (S.session && S.session.read_only ? ' · read-only' : '');
        subEl.appendChild(document.createTextNode(' '));
        subEl.appendChild(badge);
      }
      subEl.hidden = false;
    }
    else { subEl.textContent = ''; subEl.hidden = true; }
  }

  // Double-click on the titlebar title → rename the active session (same behaviour
  // as double-clicking a session title in the sidebar).  Only active on the chat
  // panel when a session is open.
  titleEl.ondblclick = null;  // remove any previous handler before adding a fresh one
  if (panel === 'chat' && typeof S !== 'undefined' && S && S.session && !(S.session.read_only || S.session.is_read_only)) {
    titleEl.ondblclick = (e) => {
      e.stopPropagation();
      e.preventDefault();
      if (_renamingAppTitlebar) return;
      _renamingAppTitlebar = true;

      const inp = document.createElement('input');
      inp.type = 'text';
      inp.className = 'app-titlebar-rename-input';
      inp.value = (S.session.title && S.session.title !== 'Untitled') ? S.session.title : '';

      // Prevent click/dblclick on the input from bubbling — we don't want
      // panel switches, session switches, or any other handler firing.
      ['click', 'mousedown', 'dblclick', 'pointerdown'].forEach(ev =>
        inp.addEventListener(ev, e2 => e2.stopPropagation())
      );

      const finish = async (save) => {
        _renamingAppTitlebar = false;
        if (save) {
          const newTitle = inp.value.trim() || (typeof t === 'function' ? t('new_chat') : 'New chat');
          S.session.title = newTitle;
          syncTopbar();   // update #topbarTitle in the chat header
          syncAppTitlebar();
          // Update the sidebar list so the renamed title appears immediately.
          // _renderOneSession reads from _allSessions cache, so patch it there too.
          try {
            const _cached = typeof _allSessions !== 'undefined' && _allSessions.find(s => s && s.session_id === S.session.session_id);
            if (_cached) _cached.title = newTitle;
          } catch (_) {}
          if (typeof renderSessionListFromCache === 'function') renderSessionListFromCache();
          try {
            await api('/api/session/rename', {
              method: 'POST',
              body: JSON.stringify({ session_id: S.session.session_id, title: newTitle })
            });
          } catch (err) {
            if (typeof setStatus === 'function') setStatus('Rename failed: ' + err.message);
          }
        }
        inp.replaceWith(titleEl);
        syncAppTitlebar();
      };

      inp.onkeydown = e2 => {
        if (e2.key === 'Enter') { e2.preventDefault(); e2.stopPropagation(); finish(true); }
        if (e2.key === 'Escape') { e2.preventDefault(); e2.stopPropagation(); finish(false); }
      };
      inp.onblur = () => finish(false);

      titleEl.replaceWith(inp);
      inp.focus();
      inp.select();
    };
  }
}

function _beginSettingsPanelSession() {
  _settingsDirty = false;
  _settingsThemeOnOpen = localStorage.getItem('sidekick-theme') || 'dark';
  _settingsSkinOnOpen = localStorage.getItem('sidekick-skin') || 'default';
  _settingsFontSizeOnOpen = localStorage.getItem('sidekick-font-size') || 'default';
  _pendingSettingsTargetPanel = null;
  if (_settingsAppearanceAutosaveTimer) {
    clearTimeout(_settingsAppearanceAutosaveTimer);
    _settingsAppearanceAutosaveTimer = null;
  }
  _settingsAppearanceAutosaveRetryPayload = null;
  _resetSettingsPanelState();
}

function _beforePanelSwitch(nextPanel) {
  if (_currentPanel !== 'settings' || nextPanel === 'settings') return true;
  if (_settingsDirty) {
    _pendingSettingsTargetPanel = nextPanel || 'chat';
    _showSettingsUnsavedBar();
    return false;
  }
  _revertSettingsPreview();
  _pendingSettingsTargetPanel = null;
  _resetSettingsPanelState();
  return true;
}

function _consumeSettingsTargetPanel(fallback = 'chat') {
  const target = (_pendingSettingsTargetPanel && _pendingSettingsTargetPanel !== 'settings')
    ? _pendingSettingsTargetPanel
    : fallback;
  _pendingSettingsTargetPanel = null;
  return target;
}

async function switchPanel(name, opts = {}) {
  const nextPanel = name || 'chat';
  const prevPanel = _currentPanel;
  // Clean up stale timers from the previous panel (problem #26)
  if (prevPanel !== nextPanel) {
    _switchPanelTimers.forEach(clearInterval);
    _switchPanelTimers.length = 0;
  }
  // ── Desktop sidebar collapse toggle (rail-click only) ──
  // If the click came from a rail icon AND we're on desktop, the rail icon
  // does double duty: clicking the already-active panel collapses the sidebar;
  // clicking any panel while collapsed expands first. Programmatic switches
  // (no opts.fromRailClick) are unaffected so legacy callers preserve
  // behaviour exactly.
  if (opts.fromRailClick && prevPanel === nextPanel && typeof _isSidebarCollapsed === 'function'
      && typeof _isDesktopWidth === 'function' && _isDesktopWidth()) {
    toggleSidebar(!_isSidebarCollapsed());
    return false;
  }
  if (!opts.bypassSettingsGuard && !_beforePanelSwitch(nextPanel)) return false;
  if (prevPanel !== 'settings' && nextPanel === 'settings') _beginSettingsPanelSession();
  if (nextPanel === 'review' && typeof ensureReviewPanel === 'function') ensureReviewPanel();
  // Close any long-lived Kanban SSE stream when leaving the kanban panel
  // so we don't keep a stale connection open in the background.
  if (prevPanel === 'kanban' && nextPanel !== 'kanban') {
    if (typeof _kanbanStopPolling === 'function') _kanbanStopPolling();
  }
  // Stop Gmail poll interval when leaving the gmail panel
  if (prevPanel === 'gmail' && nextPanel !== 'gmail') {
    if (typeof GMAIL !== 'undefined' && GMAIL.pollInterval) {
      clearInterval(GMAIL.pollInterval);
      GMAIL.pollInterval = null;
    }
  }
  _currentPanel = nextPanel;
  if (opts.fromRailClick && typeof closeMobileSidebar === 'function'
      && typeof _isDesktopWidth === 'function' && !_isDesktopWidth()) {
    closeMobileSidebar();
  }
  // Update nav tabs (rail + mobile sidebar-nav share data-panel)
  document.querySelectorAll('[data-panel]').forEach(t => t.classList.toggle('active', t.dataset.panel === nextPanel));
  // Refresh aria-expanded on the newly-active rail button to mirror sidebar state.
  if (typeof _syncSidebarAria === 'function') _syncSidebarAria();
  // Update panel views
  document.querySelectorAll('.panel-view').forEach(p => p.classList.remove('active'));
  const panelEl = $('panel' + nextPanel.charAt(0).toUpperCase() + nextPanel.slice(1));
  if (panelEl) panelEl.classList.add('active');
  // ── Discord full view lifecycle: swap #mainChat ↔ #mainDiscord ──
  const discordMain = document.getElementById('mainDiscord');
  const browserMain = document.getElementById('mainBrowser');
  const chatMain = document.getElementById('mainChat');
  if (nextPanel === 'discord') {
    // Activate full view
    if (chatMain) chatMain.style.display = 'none';
    if (discordMain) discordMain.style.display = '';
    // Rebuild loading state so discordChatInit has clean DOM
    if (discordMain) discordMain.innerHTML = '<div class="discord-loading-state" id="discordInitLoading"><div class="discord-loading-spinner"></div><span>Discord wird geladen...</span></div>';
    // Hide the empty rightpanel — Discord has its own 3-column layout
    document.body.classList.add('discord-mode');
    // Init resizable columns
    setTimeout(function() { initDiscordColumnResize(); }, 200);
  } else if (prevPanel === 'discord') {
    // Deactivate full view
    if (discordMain) {
      discordMain.style.display = 'none';
      if (typeof discordChatDestroy === 'function') discordChatDestroy();
    }
    if (chatMain) chatMain.style.display = '';
    // Restore rightpanel
    document.body.classList.remove('discord-mode');
  }
  // ── Kanban-Detail-Lifecycle ──
  if (nextPanel === 'kanban' && prevPanel !== 'kanban') {
    // Beim Aktivieren von Kanban: nichts tun, rightpanel wird via CSS umgeschaltet
    // closeKanbanTaskDetail wird beim Verlassen aufgerufen
  } else if (prevPanel === 'kanban' && nextPanel !== 'kanban') {
    // Beim Verlassen von Kanban: Detail schließen
    if (typeof closeKanbanTaskDetail === 'function') {
      closeKanbanTaskDetail();
    }
  }
  // ── Memory-Detail-Lifecycle ──
  if (nextPanel === 'memory' && prevPanel !== 'memory') {
    document.body.classList.add('showing-memory');
    if (typeof openMemoryTools === 'function') {
      setTimeout(openMemoryTools, 50);
    }
  } else if (prevPanel === 'memory' && nextPanel !== 'memory') {
    document.body.classList.remove('showing-memory');
  }
  if (nextPanel === 'browser') {
    if (chatMain) chatMain.style.display = 'none';
    if (browserMain) browserMain.style.display = '';
    document.body.classList.add('showing-browser');
    if (typeof browserResearchPanelActivated === 'function') {
      setTimeout(browserResearchPanelActivated, 0);
    }
  } else if (prevPanel === 'browser') {
    if (browserMain) browserMain.style.display = 'none';
    if (chatMain && !['discord', 'gmail', 'agents', 'appstore'].includes(nextPanel)) chatMain.style.display = '';
    document.body.classList.remove('showing-browser');
    if (typeof browserResearchPanelDeactivated === 'function') {
      browserResearchPanelDeactivated();
    }
  }
  // ── Gmail full view lifecycle: swap #mainChat ↔ #mainGmail ──
  const mailMain = document.getElementById('mainMail');
   if (nextPanel === 'mail') {
     if (chatMain) chatMain.style.display = 'none';
     if (mailMain) mailMain.style.display = '';
     loadMailPanel().catch(function(err) {
       console.warn('[mail] panel load failed:', err && err.message ? err.message : err);
     });
   } else if (prevPanel === 'mail') {
     if (mailMain) mailMain.style.display = 'none';
     if (chatMain) chatMain.style.display = '';
   }

  // ── Agents Dashboard lifecycle: swap #mainChat ↔ #mainAgents ──
  const agentsMain = document.getElementById('mainAgents');
  if (nextPanel === 'agents') {
    // Activate dashboard view
    if (chatMain) chatMain.style.display = 'none';
    if (agentsMain) agentsMain.style.display = '';
    document.body.classList.add('showing-agents');
    startDashboardRefresh();
  } else if (prevPanel === 'agents') {
    // Deactivate dashboard view
    if (typeof restoreAgentChatHome === 'function') restoreAgentChatHome();
    if (agentsMain) agentsMain.style.display = 'none';
    if (chatMain) chatMain.style.display = '';
    document.body.classList.remove('showing-agents');
    stopDashboardRefresh();
  }
  // ── Appstore full view lifecycle: swap #mainChat ↔ #mainAppstore ──
  const appstoreMain = document.getElementById('mainAppstore');
  if (nextPanel === 'appstore') {
    if (chatMain) chatMain.style.display = 'none';
    if (appstoreMain) appstoreMain.style.display = 'flex';
    loadAppstorePanel().catch(function(err) {
      console.warn('[appstore] panel load failed:', err && err.message ? err.message : err);
    });
  } else if (prevPanel === 'appstore') {
    if (appstoreMain) appstoreMain.style.display = 'none';
    if (chatMain) chatMain.style.display = '';
  }
  // Update main content view. Each entry in MAIN_VIEW_PANELS gets a matching
  // showing-<name> class on <main>; no class means chat (the default).
  const mainEl = document.querySelector('main.main');
  if (mainEl) {
    ['settings','skills','memory','tasks','kanban','workspaces','review','subagents','profiles','insights','logs','gmail','mail','browser','discord','agents','todos','appstore'].forEach(p => {
      mainEl.classList.toggle('showing-' + p, nextPanel === p);
    });
  }
  if (typeof resetAppShellScroll === 'function') resetAppShellScroll();
  if (typeof syncWorkspacePanelForActivePanel === 'function') syncWorkspacePanelForActivePanel(nextPanel);
  // Chat-panel controls: mode toggle stays visible, compact toggle is only relevant there.
  const isChat = nextPanel === 'chat' || !nextPanel;
  const modeToggle = document.getElementById('modeToggle');
  if (modeToggle) modeToggle.style.display = isChat ? '' : 'none';
  const compactBtn = document.getElementById('compactToggleBtn');
  if (compactBtn) compactBtn.style.display = isChat ? '' : 'none';
  // Lazy-load panel data
  if (nextPanel === 'tasks') await loadCrons();
  if (nextPanel === 'kanban') await loadKanban();
  if (nextPanel === 'skills') await loadSkills();
  if (nextPanel === 'memory') await loadMemory();
  if (nextPanel === 'workspaces') { if (typeof renderSpacesPanel === 'function') renderSpacesPanel(); else await loadWorkspacesPanel(); }
  if (nextPanel === 'profiles') await loadProfilesPanel();
  if (nextPanel === 'todos') loadTodos();
  if (nextPanel === 'insights') await loadInsights();
  if (nextPanel === 'logs') await loadLogs();
  if (nextPanel === 'gmail') loadGmailPanel();
  if (nextPanel === 'discord') setTimeout(function() {
    if (typeof discordChatInit === 'function') discordChatInit();
    if (typeof loadDiscordPanel === 'function') loadDiscordPanel();
  }, 100);
  if (nextPanel === 'agents') {
    if (typeof loadAgents === 'function') loadAgents();
    loadAgentsDashboard();
  }
  _syncLogsAutoRefresh();
  if (typeof _syncSystemHealthMonitorVisibility === 'function') _syncSystemHealthMonitorVisibility();
  if (nextPanel === 'settings') {
    switchSettingsSection(_currentSettingsSection);
    loadSettingsPanel();
  }
  syncAppTitlebar();
  return true;
}

// ── Cron panel ──
function _isRecurringCronJob(job) {
  const kind = job && job.schedule && job.schedule.kind;
  return kind === 'cron' || kind === 'interval';
}

function _cronScheduleKindForInput(value) {
  const schedule = String(value || '').trim();
  if (!schedule) return '';
  const lower = schedule.toLowerCase();
  if (lower.startsWith('every ')) return 'interval';
  if (lower.startsWith('@')) return 'cron';
  const parts = schedule.split(/\s+/);
  if (parts.length >= 5 && parts.slice(0, 5).every(p => /^[\d*\-,/]+$/.test(p))) return 'cron';
  if (schedule.includes('T') || /^\d{4}-\d{2}-\d{2}/.test(schedule)) return 'once';
  if (/^\d+\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$/i.test(schedule)) return 'once';
  return '';
}

function _syncCronScheduleWarning() {
  const input = $('cronFormSchedule');
  const warning = $('cronFormScheduleOnceWarning');
  if (!input || !warning) return;
  warning.style.display = _cronScheduleKindForInput(input.value) === 'once' ? '' : 'none';
}

function _hasUnlimitedRepeat(job) {
  return !!(job && job.repeat && job.repeat.times == null);
}

function _isCronNeedsAttention(job) {
  return _isRecurringCronJob(job) &&
    _hasUnlimitedRepeat(job) &&
    job.enabled === false &&
    job.state === 'completed' &&
    !job.next_run_at;
}

function _isCronScheduleError(job) {
  return _isRecurringCronJob(job) &&
    !job.next_run_at &&
    (job.state === 'error' || job.last_status === 'error');
}

function _cronStatusMeta(job) {
  if (_isCronNeedsAttention(job)) return {
    state: 'needs_attention',
    listClass: 'attention',
    detailClass: 'warn',
    label: t('cron_status_needs_attention'),
  };
  if (_isCronScheduleError(job)) return {
    state: 'schedule_error',
    listClass: 'attention',
    detailClass: 'warn',
    label: t('cron_status_needs_attention'),
  };
  if (job.state === 'paused') return {
    state: 'paused',
    listClass: 'paused',
    detailClass: 'warn',
    label: t('cron_status_paused'),
  };
  if (job.enabled === false) return {
    state: 'off',
    listClass: 'disabled',
    detailClass: 'warn',
    label: t('cron_status_off'),
  };
  if (job.last_status === 'error') return {
    state: 'error',
    listClass: 'error',
    detailClass: 'err',
    label: t('cron_status_error'),
  };
  return {
    state: 'active',
    listClass: 'active',
    detailClass: 'ok',
    label: t('cron_status_active'),
  };
}


function _cronProfileName(profile){
  return (profile || '').toString().trim();
}

function _cronProfileLabel(profile){
  const name = _cronProfileName(profile);
  return name || (t('cron_profile_server_default') || 'server default');
}

function _cronProfileTitle(profile){
  const name = _cronProfileName(profile);
  if (name) return (t('cron_profile_label') || 'Profile') + ': ' + name;
  return t('cron_profile_server_default_hint') || 'Uses the WebUI server default profile at run time';
}

async function loadCronProfiles(){
  if (_cronProfilesCache) return _cronProfilesCache;
  try {
    const data = await api('/api/profiles');
    _cronProfilesCache = Array.isArray(data.profiles) ? data.profiles : [];
  } catch(e) {
    _cronProfilesCache = [];
  }
  return _cronProfilesCache;
}

function _cronProfileOptions(selected){
  const current = _cronProfileName(selected);
  const profiles = Array.isArray(_cronProfilesCache) ? _cronProfilesCache : [];
  const seen = new Set(['']);
  const opts = [`<option value=""${current ? '' : ' selected'}>${esc(t('cron_profile_server_default') || 'server default')}</option>`];
  for (const p of profiles) {
    const name = _cronProfileName(p && p.name);
    if (!name || seen.has(name)) continue;
    seen.add(name);
    const label = p && p.is_default ? `${name} (${t('default') || 'default'})` : name;
    opts.push(`<option value="${esc(name)}"${current === name ? ' selected' : ''}>${esc(label)}</option>`);
  }
  if (current && !seen.has(current)) {
    opts.push(`<option value="${esc(current)}" selected>${esc(current)} (${esc(t('not_available') || 'not available')})</option>`);
  }
  return opts.join('');
}

function _refreshCronProfileSelect(selected){
  const sel = $('cronFormProfile');
  if (!sel) return;
  const keep = selected === undefined ? sel.value : selected;
  sel.innerHTML = _cronProfileOptions(keep);
}

function _cronDiagnostics(job) {
  const fields = {
    id: job.id,
    name: job.name || null,
    schedule: job.schedule || null,
    schedule_display: job.schedule_display || null,
    enabled: job.enabled,
    state: job.state,
    next_run_at: job.next_run_at || null,
    last_run_at: job.last_run_at || null,
    last_status: job.last_status || null,
    last_error: job.last_error || null,
    last_delivery_error: job.last_delivery_error || null,
    repeat: job.repeat || null,
    deliver: job.deliver || null,
  };
  return JSON.stringify(fields, null, 2);
}

async function loadCrons(animate) {
  const box = $('cronList');
  const refreshBtn = $('cronRefreshBtn');
  if (animate && refreshBtn) {
    refreshBtn.style.opacity = '0.5';
    refreshBtn.disabled = true;
  }
  try {
    await loadCronProfiles();
    const data = await api('/api/crons');
    _cronList = data.jobs || [];
    if (!_cronList.length) {
      box.innerHTML = `<div style="padding:16px;color:var(--muted);font-size:12px">${esc(t('cron_no_jobs'))}</div>`;
      if (_cronMode !== 'create' && _cronMode !== 'edit') _renderCronOverview([]);
      return;
    }
    box.innerHTML = '';
    for (const job of _cronList) {
      const item = document.createElement('div');
      item.className = 'cron-item';
      item.id = 'cron-' + job.id;
      const status = _cronStatusMeta(job);
      const isNewRun = _cronNewJobIds.has(String(job.id));
      const profileLabel = _cronProfileLabel(job.profile);
      const profileTitle = _cronProfileTitle(job.profile);
      item.innerHTML = `
        <div class="cron-header">
          ${isNewRun ? '<span class="cron-new-dot" title="New run"></span>' : ''}
          <span class="cron-name" title="${esc(job.name)}">${esc(job.name)}</span>
          <span class="cron-profile-badge" title="${esc(profileTitle)}">${esc(profileLabel)}</span>
          <span class="cron-status ${status.listClass}">${esc(status.label)}</span>
        </div>`;
      item.onclick = () => openCronDetail(job.id, item);
      if (_currentCronDetail && _currentCronDetail.id === job.id) item.classList.add('active');
      box.appendChild(item);
    }
    // Re-render current detail with fresh data if we have one and we're not in a form
    if (_currentCronDetail && _cronMode !== 'create' && _cronMode !== 'edit') {
      const refreshed = _cronList.find(j => j.id === _currentCronDetail.id);
      if (refreshed) _renderCronDetail(refreshed);
      else _renderCronOverview(_cronList);
    } else if (_cronMode !== 'create' && _cronMode !== 'edit') {
      _renderCronOverview(_cronList);
    }
  } catch(e) { box.innerHTML = `<div style="padding:12px;color:var(--accent);font-size:12px">${esc(t('error_prefix'))}${esc(e.message)}</div>`; }
  finally {
    if (animate && refreshBtn) {
      refreshBtn.style.opacity = '';
      refreshBtn.disabled = false;
    }
  }
}

function _cronOverviewActionButtons(job){
  const id = esc(job.id);
  const paused = job.enabled === false || job.state === 'paused';
  const pauseResume = paused
    ? `<button class="cron-overview-btn" onclick="event.stopPropagation();cronResume('${id}')">Resume</button>`
    : `<button class="cron-overview-btn" onclick="event.stopPropagation();cronPause('${id}')">Pause</button>`;
  return `
    <button class="cron-overview-btn primary" onclick="event.stopPropagation();cronRun('${id}')">Run</button>
    ${pauseResume}
    <button class="cron-overview-btn" onclick="event.stopPropagation();openCronDetail('${id}')">Info</button>
    <button class="cron-overview-btn" onclick="event.stopPropagation();openCronDetail('${id}');editCurrentCron()">Edit</button>
    <button class="cron-overview-btn" onclick="event.stopPropagation();openCronDetail('${id}');duplicateCurrentCron()">Duplicate</button>
    <button class="cron-overview-btn danger" onclick="event.stopPropagation();openCronDetail('${id}');deleteCurrentCron()">Delete</button>`;
}

function _renderCronOverview(jobs){
  _currentCronDetail = null;
  _cronMode = 'overview';
  _stopCronWatch();
  _setCronHeaderButtons('empty');
  const title = $('taskDetailTitle');
  const body = $('taskDetailBody');
  const empty = $('taskDetailEmpty');
  if (title) title.textContent = 'Scheduled jobs';
  if (empty) empty.style.display = 'none';
  if (!body) return;
  body.style.display = '';
  const list = Array.isArray(jobs) ? jobs : [];
  const active = list.filter(j => j.enabled !== false && j.state !== 'paused').length;
  const paused = list.filter(j => j.enabled === false || j.state === 'paused').length;
  const attention = list.filter(j => {
    const s = _cronStatusMeta(j);
    return s.state === 'needs_attention' || s.state === 'schedule_error' || s.state === 'error';
  }).length;
  if (!list.length) {
    body.innerHTML = `
      <div class="main-view-content cron-overview">
        <div class="cron-overview-hero">
          <div>
            <div class="cron-overview-kicker">Scheduled jobs</div>
            <h2>No scheduled jobs yet</h2>
            <p>Create a scheduled job from the left panel or from chat when you need Sidekick to run something later.</p>
          </div>
          <button class="btn primary" onclick="openCronCreate()">New job</button>
        </div>
      </div>`;
    return;
  }
  body.innerHTML = `
    <div class="main-view-content cron-overview">
      <div class="cron-overview-hero">
        <div>
          <div class="cron-overview-kicker">Scheduled jobs</div>
          <h2>${esc(String(list.length))} jobs across this runtime</h2>
          <p>Use the left list for quick selection. This overview exposes the same safe actions without needing the workspace panel.</p>
        </div>
        <div class="cron-overview-metrics">
          <span><strong>${active}</strong> active</span>
          <span><strong>${paused}</strong> paused</span>
          <span><strong>${attention}</strong> attention</span>
        </div>
      </div>
      <div class="cron-overview-grid">
        ${list.map(job => {
          const status = _cronStatusMeta(job);
          const nextRun = job.next_run_at ? new Date(job.next_run_at).toLocaleString() : t('not_available');
          const lastRun = job.last_run_at ? new Date(job.last_run_at).toLocaleString() : t('never');
          const schedule = job.schedule_display || (job.schedule && job.schedule.expression) || '';
          return `<article class="cron-overview-card" onclick="openCronDetail('${esc(job.id)}')">
            <div class="cron-overview-card-head">
              <div>
                <h3>${esc(job.name || schedule || '(unnamed)')}</h3>
                <p>${esc(schedule || 'manual')}</p>
              </div>
              <span class="cron-status ${status.listClass}">${esc(status.label)}</span>
            </div>
            <div class="cron-overview-card-meta">
              <span>Next: ${esc(nextRun)}</span>
              <span>Last: ${esc(lastRun)}</span>
              <span>Profile: ${esc(_cronProfileLabel(job.profile))}</span>
            </div>
            <div class="cron-overview-actions">${_cronOverviewActionButtons(job)}</div>
          </article>`;
        }).join('')}
      </div>
    </div>`;
}

function _renderCronDetail(job){
  _currentCronDetail = job;
  const title = $('taskDetailTitle');
  const body = $('taskDetailBody');
  const empty = $('taskDetailEmpty');
  if (!title || !body) return;
  title.textContent = job.name || job.schedule_display || '(unnamed)';
  const status = _cronStatusMeta(job);
  const nextRun = job.next_run_at ? new Date(job.next_run_at).toLocaleString() : t('not_available');
  const lastRun = job.last_run_at ? new Date(job.last_run_at).toLocaleString() : t('never');
  const schedule = job.schedule_display || (job.schedule && job.schedule.expression) || '';
  const skills = Array.isArray(job.skills) && job.skills.length ? job.skills.join(', ') : '—';
  const deliver = job.deliver || 'local';
  const isNoAgent = !!job.no_agent;
  const cronJobMode = isNoAgent ? 'no-agent' : 'agent';
  const script = job.script || '';
  const profileLabel = _cronProfileLabel(job.profile);
  const profileTitle = _cronProfileTitle(job.profile);
  const lastError = job.last_error ? `<div class="detail-row"><div class="detail-row-label">${esc(t('error_prefix').replace(/:\s*$/,''))}</div><div class="detail-row-value" style="color:var(--accent-text)">${esc(job.last_error)}</div></div>` : '';
  const attention = status.state === 'needs_attention' || status.state === 'schedule_error';
  const croniterHint = job.last_error && /croniter/i.test(job.last_error)
    ? `<p>${esc(t('cron_attention_croniter_hint'))}</p>`
    : '';
  const attentionBanner = attention ? `
      <div class="detail-alert cron-attention-panel">
        <div class="detail-alert-title">${esc(t('cron_status_needs_attention'))}</div>
        <p>${esc(t('cron_attention_desc'))}</p>
        ${croniterHint}
        <div class="detail-alert-actions">
          <button type="button" class="cron-btn run" onclick="resumeCurrentCron()">${esc(t('cron_attention_resume'))}</button>
          <button type="button" class="cron-btn" onclick="runCurrentCron()">${esc(t('cron_attention_run_once'))}</button>
          <button type="button" class="cron-btn" onclick="copyCurrentCronDiagnostics()">${esc(t('cron_attention_copy_diagnostics'))}</button>
        </div>
      </div>` : '';
  const toastNotifications = job.toast_notifications !== false;
  body.innerHTML = `
    <div class="main-view-content">
      ${attentionBanner}
      <div class="detail-card">
        <div class="detail-card-title">${esc(t('cron_status_active').replace(/./,c=>c.toUpperCase()))}</div>
        <div class="detail-row"><div class="detail-row-label">Status</div><div class="detail-row-value"><span class="detail-badge ${status.detailClass}">${esc(status.label)}</span></div></div>
        <div class="detail-row"><div class="detail-row-label">Schedule</div><div class="detail-row-value"><code>${esc(schedule)}</code></div></div>
        <div class="detail-row"><div class="detail-row-label">${esc(t('cron_next'))}</div><div class="detail-row-value">${esc(nextRun)}</div></div>
        <div class="detail-row"><div class="detail-row-label">${esc(t('cron_last'))}</div><div class="detail-row-value">${esc(lastRun)}</div></div>
        <div class="detail-row"><div class="detail-row-label">Deliver</div><div class="detail-row-value">${esc(deliver)}</div></div>
        <div class="detail-row"><div class="detail-row-label">Mode</div><div class="detail-row-value"><span class="detail-badge" id="cronJobMode">${esc(cronJobMode)}</span></div></div>
        ${isNoAgent ? `<div class="detail-row"><div class="detail-row-label">No-agent script</div><div class="detail-row-value"><code>${esc(script || '—')}</code></div></div>` : ''}
        <div class="detail-row"><div class="detail-row-label">${esc(t('cron_profile_label') || 'Profile')}</div><div class="detail-row-value"><span class="detail-badge active" title="${esc(profileTitle)}">${esc(profileLabel)}</span></div></div>
        <div class="detail-row"><div class="detail-row-label">${esc(t('cron_toast_notifications_label') || 'Completion toasts')}</div><div class="detail-row-value"><span class="detail-badge ${toastNotifications ? 'active' : ''}">${esc(toastNotifications ? (t('cron_toast_notifications_enabled') || 'Enabled') : (t('cron_toast_notifications_disabled') || 'Disabled'))}</span></div></div>
        <div class="detail-row"><div class="detail-row-label">Skills</div><div class="detail-row-value">${esc(skills)}</div></div>
        ${lastError}
      </div>
      <div class="detail-card">
        <div class="detail-card-title">Prompt</div>
        <div class="detail-prompt">${esc(job.prompt || '')}</div>
      </div>
      <div class="detail-card ${_cronNewJobIds.has(String(job.id)) ? 'has-new-run' : ''}" id="cronDetailRuns">
        <div class="detail-card-title">${esc(t('cron_last_output'))}</div>
        <div style="color:var(--muted);font-size:12px">${esc(t('loading'))}</div>
      </div>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _cronMode = 'read';
  _setCronHeaderButtons('read', job);
  // Load runs asynchronously
  _loadCronDetailRuns(job.id);
}

function _setCronHeaderButtons(mode, job) {
  const runBtn = $('btnRunTaskDetail');
  const pauseBtn = $('btnPauseTaskDetail');
  const resumeBtn = $('btnResumeTaskDetail');
  const editBtn = $('btnEditTaskDetail');
  const dupBtn = $('btnDuplicateTaskDetail');
  const delBtn = $('btnDeleteTaskDetail');
  const cancelBtn = $('btnCancelTaskDetail');
  const saveBtn = $('btnSaveTaskDetail');
  const hide = b => b && (b.style.display = 'none');
  const show = b => b && (b.style.display = '');
  if (mode === 'read') {
    show(runBtn);
    const status = job ? _cronStatusMeta(job) : null;
    const resumable = job && (
      job.state === 'paused' ||
      (status && (status.state === 'needs_attention' || status.state === 'schedule_error'))
    );
    if (resumable) { hide(pauseBtn); show(resumeBtn); }
    else { show(pauseBtn); hide(resumeBtn); }
    show(editBtn); show(dupBtn); show(delBtn); hide(cancelBtn); hide(saveBtn);
  } else if (mode === 'create' || mode === 'edit') {
    hide(runBtn); hide(pauseBtn); hide(resumeBtn); hide(editBtn); hide(dupBtn); hide(delBtn);
    show(cancelBtn); show(saveBtn);
  } else {
    [runBtn,pauseBtn,resumeBtn,editBtn,dupBtn,delBtn,cancelBtn,saveBtn].forEach(hide);
  }
}

async function _loadCronDetailRuns(jobId){
  try {
    const data = await api(`/api/crons/history?job_id=${encodeURIComponent(jobId)}&limit=50`);
    if (!_currentCronDetail || _currentCronDetail.id !== jobId) return;
    const card = $('cronDetailRuns');
    if (!card) return;
    if (!data.runs || !data.runs.length) {
      card.innerHTML = `<div class="detail-card-title">${esc(t('cron_last_output'))}</div><div style="color:var(--muted);font-size:12px">${esc(t('cron_no_runs_yet'))}</div>`;
      return;
    }
    const rows = data.runs.map((run, i) => {
      const ts = run.filename.replace('.md','').replace(/_/g,' ');
      const sizeStr = run.size > 1024 ? (run.size/1024).toFixed(1)+' KB' : run.size+' B';
      const dateStr = new Date(run.modified * 1000).toLocaleString();
      const rid = `cron-det-run-${jobId}-${i}`;
      return `<div class="detail-run-item" id="${rid}">
        <div class="detail-run-head" onclick="_loadRunContent('${esc(jobId)}','${esc(run.filename)}','${rid}')">
          <span><span style="opacity:.7">${esc(ts)}</span> <span style="opacity:.4;font-size:11px">${esc(sizeStr)}</span></span>
          <span style="opacity:.6">▸</span>
        </div>
        <div class="detail-run-body" style="color:var(--muted);font-size:12px">${esc(t('loading'))}</div>
      </div>`;
    }).join('');
    const countLabel = data.total > 50 ? ` (${data.total} runs, showing latest 50)` : ` (${data.total} runs)`;
    card.innerHTML = `<div class="detail-card-title">${esc(t('cron_last_output'))}${countLabel}</div>${rows}`;
  } catch(e) { /* ignore */ }
}

async function _loadRunContent(jobId, filename, runId){
  const body = document.querySelector(`#${runId} .detail-run-body`);
  if (!body) return;
  const item = document.getElementById(runId);
  if (!item.classList.contains('open')) {
    item.classList.add('open');
  }
  body.innerHTML = `<span style="opacity:.5">${esc(t('loading'))}</span>`;
  try {
    const data = await api(`/api/crons/run?job_id=${encodeURIComponent(jobId)}&filename=${encodeURIComponent(filename)}`);
    if (data.error) {
      body.textContent = data.error;
      return;
    }
    // Render markdown content using the same renderer as chat messages
    if (typeof renderMd === 'function') {
      body.innerHTML = renderMd(data.snippet || data.content);
    } else {
      body.textContent = data.snippet || data.content;
    }
    // Show "View full output" button if content was truncated
    if (data.content && data.snippet && data.content.length > data.snippet.length) {
      const btn = document.createElement('button');
      btn.style.cssText = 'margin-top:8px;padding:4px 12px;border-radius:var(--radius-btn);border:1px solid var(--border-subtle);background:var(--surface-subtle);color:var(--text-secondary);cursor:pointer;font-size:12px';
      btn.textContent = t('cron_view_full_output') || 'View full output';
      btn.onclick = () => {
        body.innerHTML = renderMd ? renderMd(data.content) : '';
        btn.remove();
      };
      body.appendChild(btn);
    }
  } catch(e) {
    body.textContent = 'Error: ' + e.message;
  }
}

function openCronDetail(id, el){
  const job = _cronList ? _cronList.find(j => j.id === id) : null;
  if (!job) return;
  document.querySelectorAll('.cron-item').forEach(e => e.classList.remove('active'));
  const target = el || $('cron-' + id);
  if (target) target.classList.add('active');
  // Remove new-run dot from this job since user is now viewing it
  _clearCronUnreadForJob(id);
  const dot = target && target.querySelector('.cron-new-dot');
  if (dot) dot.remove();
  _cronPreFormDetail = null;
  _editingCronId = null;
  _stopCronWatch();
  _renderCronDetail(job);
  _checkCronWatchOnDetail(id);
}

function _clearCronDetail(){
  _currentCronDetail = null;
  _cronMode = 'empty';
  _stopCronWatch();
  const title = $('taskDetailTitle');
  const body = $('taskDetailBody');
  const empty = $('taskDetailEmpty');
  if (title) title.textContent = '';
  if (body) { body.innerHTML = ''; body.style.display = 'none'; }
  if (empty) empty.style.display = '';
  _setCronHeaderButtons('empty');
}

async function runCurrentCron(){ if (_currentCronDetail) await cronRun(_currentCronDetail.id); }
async function pauseCurrentCron(){ if (_currentCronDetail) await cronPause(_currentCronDetail.id); }
async function resumeCurrentCron(){ if (_currentCronDetail) await cronResume(_currentCronDetail.id); }
async function copyCurrentCronDiagnostics(){
  if (!_currentCronDetail) return;
  try {
    await _copyText(_cronDiagnostics(_currentCronDetail));
    showToast(t('cron_diagnostics_copied'));
  } catch(e) { showToast(t('copy_failed'), 4000); }
}
function editCurrentCron(){
  if (!_currentCronDetail) return;
  openCronEdit(_currentCronDetail);
}
function duplicateCurrentCron(){
  if (!_currentCronDetail) return;
  const job = _currentCronDetail;
  if (typeof switchPanel === 'function' && _currentPanel !== 'tasks') switchPanel('tasks');
  _cronPreFormDetail = { ...job };
  _editingCronId = null;
  _cronMode = 'create';
  _cronIsDuplicate = true;
  _cronSelectedSkills = Array.isArray(job.skills) ? [...job.skills] : [];
  // Deduplicate name: append "(copy)", "(copy 2)", "(copy 3)" etc.
  const baseName = job.name || '';
  let dupName = baseName + ' (copy)';
  if (_cronList && _cronList.length) {
    const taken = new Set(_cronList.filter(j => j.name).map(j => j.name));
    if (taken.has(dupName)) {
      let n = 2;
      while (taken.has(baseName + ' (copy ' + n + ')')) n++;
      dupName = baseName + ' (copy ' + n + ')';
    }
  }
  _renderCronForm({
    name: dupName,
    schedule: job.schedule_display || (job.schedule && job.schedule.expression) || '',
    prompt: job.prompt || '',
    deliver: job.deliver || 'local',
    profile: job.profile || '',
    toast_notifications: job.toast_notifications !== false,
    isEdit: false,
  });
  if (!_cronSkillsCache) {
    api('/api/skills').then(d=>{_cronSkillsCache=d.skills||[]; _bindCronSkillPicker();}).catch(()=>{});
  } else {
    _bindCronSkillPicker();
  }
}
async function deleteCurrentCron(){
  if (!_currentCronDetail) return;
  const id = _currentCronDetail.id;
  const _ok = await showConfirmDialog({title:t('cron_delete_confirm_title'),message:t('cron_delete_confirm_message'),confirmLabel:t('delete_title'),danger:true,focusCancel:true});
  if(!_ok) return;
  try {
    await api('/api/crons/delete', {method:'POST', body: JSON.stringify({job_id: id})});
    showToast(t('cron_job_deleted'));
    _clearCronDetail();
    await loadCrons();
  } catch(e) { showToast(t('delete_failed') + e.message, 4000); }
}

let _cronSelectedSkills=[];
let _cronIsDuplicate = false;
let _cronSkillsCache=null;
let _cronProfilesCache=null;

function openCronCreate(){
  if (typeof switchPanel === 'function' && _currentPanel !== 'tasks') switchPanel('tasks');
  _cronPreFormDetail = _currentCronDetail ? { ..._currentCronDetail } : null;
  _editingCronId = null;
  _cronMode = 'create';
  _cronIsDuplicate = false;
  _cronSelectedSkills = [];
  _renderCronForm({ name:'', schedule:'', prompt:'', deliver:'local', profile:'', toast_notifications:true, isEdit:false });
  _cronSkillsCache = null;
  api('/api/skills').then(d=>{_cronSkillsCache=d.skills||[]; _bindCronSkillPicker();}).catch(()=>{});
  loadCronProfiles().then(()=>_refreshCronProfileSelect('')).catch(()=>{});
}

function openCronEdit(job){
  if (!job) return;
  _cronPreFormDetail = { ...job };
  _editingCronId = job.id;
  _cronMode = 'edit';
  _cronSelectedSkills = Array.isArray(job.skills) ? [...job.skills] : [];
  _renderCronForm({
    name: job.name || '',
    schedule: job.schedule_display || (job.schedule && job.schedule.expression) || '',
    prompt: job.prompt || '',
    deliver: job.deliver || 'local',
    profile: job.profile || '',
    toast_notifications: job.toast_notifications !== false,
    no_agent: !!job.no_agent,
    script: job.script || '',
    isEdit: true,
  });
  if (!_cronSkillsCache) {
    api('/api/skills').then(d=>{_cronSkillsCache=d.skills||[]; _bindCronSkillPicker();}).catch(()=>{});
  } else {
    _bindCronSkillPicker();
  }
  loadCronProfiles().then(()=>_refreshCronProfileSelect(job.profile || '')).catch(()=>{});
}

function _renderCronForm({ name, schedule, prompt, deliver, profile, toast_notifications=true, no_agent=false, script='', isEdit }){
  const title = $('taskDetailTitle');
  const body = $('taskDetailBody');
  const empty = $('taskDetailEmpty');
  if (!body || !title) return;
  const isNoAgent = !!no_agent;
  const toastNotifications = toast_notifications !== false;
  title.textContent = isEdit ? (t('edit') + ' · ' + (name || schedule || t('scheduled_jobs'))) : t('new_job');
  const deliverOpt = (v,l) => `<option value="${v}"${deliver===v?' selected':''}>${esc(l)}</option>`;
  body.innerHTML = `
    <div class="main-view-content">
      <form class="detail-form" onsubmit="event.preventDefault(); saveCronForm();">
        <div class="detail-form-row">
          <label for="cronFormName">${esc(t('cron_name_label') || 'Name')}</label>
          <input type="text" id="cronFormName" value="${esc(name || '')}" placeholder="${esc(t('cron_name_placeholder') || 'Optional')}" autocomplete="off">
        </div>
        <div class="detail-form-row">
          <label for="cronFormSchedule">${esc(t('cron_schedule_label') || 'Schedule')}</label>
          <input type="text" id="cronFormSchedule" value="${esc(schedule || '')}" placeholder="0 9 * * *  —  every 1h  —  @daily" autocomplete="off" required>
          <div class="detail-form-hint">${esc(t('cron_schedule_hint') || "Cron expression or shorthand like 'every 1h'.")}</div>
          <div id="cronFormScheduleOnceWarning" class="detail-form-warning cron-once-warning" style="display:none">${esc(t('cron_schedule_once_warning') || "Duration forms like '30m' run once and are removed after running. Use 'every 30m' to keep a recurring job.")}</div>
        </div>
        <div class="detail-form-row ${isNoAgent ? 'cron-no-agent-prompt-row' : ''}">
          <label for="cronFormPrompt">${esc(t('cron_prompt_label') || 'Prompt')}</label>
          <textarea id="cronFormPrompt" rows="6" placeholder="${esc(t('cron_prompt_placeholder') || 'Must be self-contained')}"${isNoAgent ? ' disabled' : ' required'}>${esc(prompt || '')}</textarea>
          ${isNoAgent ? `<div class="detail-form-hint cron-no-agent-hint">No-agent mode runs the configured script directly; Prompt is unused. No-agent script: <code>${esc(script || '—')}</code></div>` : ''}
        </div>
        <div class="detail-form-row">
          <label for="cronFormDeliver">${esc(t('cron_deliver_label') || 'Deliver output to')}</label>
          <select id="cronFormDeliver" ${isEdit ? 'disabled' : ''}>
            ${deliverOpt('local', t('cron_deliver_local') || 'Local (save output only)')}
            ${deliverOpt('discord','Discord')}
            ${deliverOpt('telegram','Telegram')}
            ${deliverOpt('slack','Slack')}
          </select>
        </div>
        <div class="detail-form-row">
          <label for="cronFormProfile">${esc(t('cron_profile_label') || 'Profile')}</label>
          <select id="cronFormProfile">
            ${_cronProfileOptions(profile)}
          </select>
          <div class="detail-form-hint">${esc(t('cron_profile_server_default_hint') || 'Uses the WebUI server default profile at run time')}</div>
        </div>
        <div class="detail-form-row">
          <label for="cronFormToastNotifications">${esc(t('cron_toast_notifications_label') || 'Completion toasts')}</label>
          <label class="detail-form-check" for="cronFormToastNotifications">
            <input type="checkbox" id="cronFormToastNotifications" ${toastNotifications ? 'checked' : ''}>
            <span>${esc(t('cron_toast_notifications_hint') || 'Show a toast when this cron finishes.')}</span>
          </label>
        </div>
        <div class="detail-form-row">
          <label for="cronFormSkillSearch">${esc(t('cron_skills_label') || 'Skills')}</label>
          <div class="skill-picker-wrap">
            <input type="text" id="cronFormSkillSearch" placeholder="${esc(t('cron_skills_placeholder') || 'Add skills (optional)...')}" autocomplete="off" ${isEdit ? 'disabled' : ''}>
            <div id="cronFormSkillDropdown" class="skill-picker-dropdown" style="display:none"></div>
            <div id="cronFormSkillTags" class="skill-picker-tags"></div>
          </div>
          ${isEdit ? `<div class="detail-form-hint">${esc(t('cron_skills_edit_hint') || 'Skill list is not editable after creation.')}</div>` : ''}
        </div>
        <div id="cronFormError" class="detail-form-error" style="display:none"></div>
      </form>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _setCronHeaderButtons(isEdit ? 'edit' : 'create');
  _renderCronSkillTags();
  const scheduleEl = $('cronFormSchedule');
  if (scheduleEl) {
    scheduleEl.addEventListener('input', _syncCronScheduleWarning);
    scheduleEl.addEventListener('change', _syncCronScheduleWarning);
    _syncCronScheduleWarning();
  }
  const focusEl = $('cronFormName');
  if (focusEl) focusEl.focus();
}

function _renderCronSkillTags(){
  const wrap=$('cronFormSkillTags');
  if(!wrap)return;
  wrap.innerHTML='';
  for(const name of _cronSelectedSkills){
    const tag=document.createElement('span');
    tag.className='skill-tag';
    tag.dataset.skill=name;
    const rm=document.createElement('span');
    rm.className='remove-tag';rm.textContent='×';
    rm.onclick=()=>{_cronSelectedSkills=_cronSelectedSkills.filter(s=>s!==name);tag.remove();};
    tag.appendChild(document.createTextNode(name));
    tag.appendChild(rm);
    wrap.appendChild(tag);
  }
}

function _bindCronSkillPicker(){
  const search=$('cronFormSkillSearch');
  const dropdown=$('cronFormSkillDropdown');
  if(!search||!dropdown)return;
  search.oninput=()=>{
    const q=search.value.trim().toLowerCase();
    if(!q||!_cronSkillsCache){dropdown.style.display='none';return;}
    const matches=_cronSkillsCache.filter(s=>
      !_cronSelectedSkills.includes(s.name)&&
      (s.name.toLowerCase().includes(q)||(s.category||'').toLowerCase().includes(q))
    ).slice(0,8);
    if(!matches.length){dropdown.style.display='none';return;}
    dropdown.innerHTML='';
    for(const s of matches){
      const opt=document.createElement('div');
      opt.className='skill-opt';
      opt.textContent=s.name+(s.category?' ('+s.category+')':'');
      opt.onclick=()=>{
        _cronSelectedSkills.push(s.name);
        _renderCronSkillTags();
        search.value='';
        dropdown.style.display='none';
      };
      dropdown.appendChild(opt);
    }
    dropdown.style.display='';
  };
  search.onblur=()=>setTimeout(()=>{dropdown.style.display='none';},150);
}

function cancelCronForm(){
  _editingCronId = null;
  if (_cronPreFormDetail) {
    const snap = _cronPreFormDetail;
    _cronPreFormDetail = null;
    _renderCronDetail(snap);
    return;
  }
  _cronPreFormDetail = null;
  _clearCronDetail();
}

async function saveCronForm(){
  const nameEl=$('cronFormName');
  const schEl=$('cronFormSchedule');
  const promptEl=$('cronFormPrompt');
  const delivEl=$('cronFormDeliver');
  const profileEl=$('cronFormProfile');
  const toastEl=$('cronFormToastNotifications');
  const errEl=$('cronFormError');
  if(!schEl||!promptEl||!errEl) return;
  const name=(nameEl?nameEl.value:'').trim();
  const schedule=schEl.value.trim();
  const prompt=promptEl.value.trim();
  const deliver=delivEl?delivEl.value:'local';
  const profile=profileEl?profileEl.value:'';
  const toastNotifications=toastEl?!!toastEl.checked:true;
  const isNoAgent = !!(_cronPreFormDetail && _cronPreFormDetail.no_agent);
  errEl.style.display='none';
  if(!schedule){errEl.textContent=t('cron_schedule_required_example');errEl.style.display='';return;}
  if(!isNoAgent && !prompt){errEl.textContent=t('cron_prompt_required');errEl.style.display='';return;}
  try{
    if (_editingCronId) {
      const updates = {job_id: _editingCronId, schedule, profile: profile, toast_notifications: toastNotifications};
      if (!isNoAgent) updates.prompt = prompt;
      if (name) updates.name = name;
      await api('/api/crons/update', {method:'POST', body: JSON.stringify(updates)});
      const editedId = _editingCronId;
      _editingCronId = null;
      _cronPreFormDetail = null;
      showToast(t('cron_job_updated'));
      await loadCrons();
      const job = _cronList && _cronList.find(j => j.id === editedId);
      if (job) openCronDetail(editedId);
      return;
    }
    const body={schedule,prompt,deliver,profile: profile, toast_notifications: toastNotifications};
    if(_cronIsDuplicate) body.enabled=false;
    if(name)body.name=name;
    if(_cronSelectedSkills.length)body.skills=_cronSelectedSkills;
    const res = await api('/api/crons/create',{method:'POST',body:JSON.stringify(body)});
    _cronPreFormDetail = null;
    _cronIsDuplicate = false;
    showToast(t('cron_job_created'));
    await loadCrons();
    const newId = res && (res.id || (res.job && res.job.id));
    if (newId) openCronDetail(newId);
    else if (_cronList && _cronList.length) openCronDetail(_cronList[_cronList.length - 1].id);
  }catch(e){
    errEl.textContent=t('error_prefix')+e.message;errEl.style.display='';
  }
}

// Back-compat aliases for any stale callers
const submitCronCreate = saveCronForm;
function toggleCronForm(){ openCronCreate(); }

function _cronOutputSnippet(content) {
  // Extract the response body from a cron output .md file
  const lines = content.split('\n');
  const responseIdx = lines.findIndex(l => l.startsWith('## Response') || l.startsWith('# Response'));
  const body = (responseIdx >= 0 ? lines.slice(responseIdx + 1) : lines).join('\n').trim();
  return body.slice(0, 600) || '(empty)';
}

// ── Cron run watch ────────────────────────────────────────────────────────────
let _cronWatchInterval = null;
let _cronWatchStart = null;
let _cronWatchTimerInterval = null;

function _startCronWatch(jobId) {
  _stopCronWatch();
  _cronWatchStart = Date.now();
  _cronWatchInterval = setInterval(async () => {
    try {
      const data = await api(`/api/crons/status?job_id=${encodeURIComponent(jobId)}`);
      if (!data.running) {
        _stopCronWatch();
        if (_currentCronDetail && _currentCronDetail.id === jobId) {
          _loadCronDetailRuns(jobId);
        }
        return;
      }
      // Still running — update elapsed
      if (_currentCronDetail && _currentCronDetail.id === jobId) {
        const el = $('cronRunningIndicator');
        if (el) el.querySelector('.cron-watch-elapsed').textContent = _formatElapsed(data.elapsed);
      }
    } catch(e) { /* ignore poll errors */ }
  }, 3000);
  // Timer update every second
  _cronWatchTimerInterval = setInterval(() => {
    if (_currentCronDetail && _cronWatchStart) {
      const el = $('cronRunningIndicator');
      if (el) el.querySelector('.cron-watch-elapsed').textContent = _formatElapsed((Date.now() - _cronWatchStart) / 1000);
    }
  }, 1000);
  // Inject running indicator into detail card
  if (_currentCronDetail && _currentCronDetail.id === jobId) {
    _injectRunningIndicator();
  }
}

function _stopCronWatch() {
  if (_cronWatchInterval) { clearInterval(_cronWatchInterval); _cronWatchInterval = null; }
  if (_cronWatchTimerInterval) { clearInterval(_cronWatchTimerInterval); _cronWatchTimerInterval = null; }
  _cronWatchStart = null;
  const el = $('cronRunningIndicator');
  if (el) el.remove();
}

function _injectRunningIndicator() {
  const card = $('cronDetailRuns');
  if (!card || $('cronRunningIndicator')) return;
  const div = document.createElement('div');
  div.id = 'cronRunningIndicator';
  div.className = 'cron-running-indicator';
  div.innerHTML = `<span class="cron-watch-spinner"></span><span>${esc(t('cron_status_running'))}</span><span class="cron-watch-elapsed">0s</span>`;
  card.insertAdjacentElement('beforebegin', div);
}

function _formatElapsed(seconds) {
  if (seconds < 60) return Math.round(seconds) + 's';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m + 'm ' + s + 's';
}

function _checkCronWatchOnDetail(jobId) {
  // When opening a detail view, check if job is running
  api(`/api/crons/status?job_id=${encodeURIComponent(jobId)}`).then(data => {
    if (data.running && _currentCronDetail && _currentCronDetail.id === jobId) {
      _startCronWatch(jobId);
    }
  }).catch(() => {});
}

async function cronRun(id) {
  try {
    await api('/api/crons/run', {method:'POST', body: JSON.stringify({job_id: id})});
    showToast(t('cron_job_triggered'));
    _startCronWatch(id);
  } catch(e) { showToast(t('failed_colon') + e.message, 4000); }
}

async function cronPause(id) {
  try {
    await api('/api/crons/pause', {method:'POST', body: JSON.stringify({job_id: id})});
    showToast(t('cron_job_paused'));
    await loadCrons();
  } catch(e) { showToast(t('failed_colon') + e.message, 4000); }
}

async function cronResume(id) {
  try {
    await api('/api/crons/resume', {method:'POST', body: JSON.stringify({job_id: id})});
    showToast(t('cron_job_resumed'));
    await loadCrons();
  } catch(e) { showToast(t('failed_colon') + e.message, 4000); }
}

let _editingCronId = null;

// ── Kanban panel (read-only) ──
function _kanbanColumnLabel(name){ return t('kanban_status_' + name) || name; }
function _kanbanTaskTitle(task){ return task.title || task.summary || task.id || t('kanban_task'); }
function _kanbanTaskBody(task){ return task.body || task.description || task.prompt || ''; }
function _kanbanTaskMeta(task){
  const bits = [];
  if (task.assignee) bits.push(task.assignee);
  if (task.tenant) bits.push(task.tenant);
  if (task.priority !== undefined && task.priority !== null) bits.push('P' + task.priority);
  if (task.comment_count) bits.push('💬 ' + task.comment_count);
  if (task.link_counts && task.link_counts.children) bits.push('↳ ' + task.link_counts.children);
  return bits;
}

function _kanbanCurrentFilters(){
  const q = $('kanbanSearch') ? $('kanbanSearch').value.trim().toLowerCase() : '';
  const assigneeEl = $('kanbanAssigneeFilter');
  const tenantEl = $('kanbanTenantFilter');
  const assignee = assigneeEl ? (assigneeEl.value || assigneeEl.dataset.defaultValue || '') : '';
  const tenant = tenantEl ? (tenantEl.value || tenantEl.dataset.defaultValue || '') : '';
  const includeArchived = !!($('kanbanIncludeArchived') && $('kanbanIncludeArchived').checked);
  const onlyMine = !!($('kanbanOnlyMine') && $('kanbanOnlyMine').checked);
  return {q, assignee, tenant, includeArchived, onlyMine};
}

function _kanbanApplyConfigDefaults(config){
  if (!config || _kanbanConfigApplied) return;
  if ($('kanbanTenantFilter') && config.default_tenant) $('kanbanTenantFilter').dataset.defaultValue = config.default_tenant;
  if ($('kanbanIncludeArchived') && config.include_archived_by_default === true) $('kanbanIncludeArchived').checked = true;
  if (config.lane_by_profile === true) _kanbanLanesByProfile = true;
  _kanbanConfigApplied = true;
}
let _kanbanConfigApplied = false;

function _kanbanSetSelectOptions(el, values, allLabelKey){
  if (!el) return;
  const current = el.value || el.dataset.defaultValue || '';
  const opts = [`<option value="">${esc(t(allLabelKey))}</option>`]
    .concat((values || []).map(v => `<option value="${esc(v)}">${esc(v)}</option>`));
  el.innerHTML = opts.join('');
  if ([...el.options].some(o => o.value === current)) el.value = current;
}

function _kanbanVisibleTasks(){
  const filters = _kanbanCurrentFilters();
  const columns = (_kanbanBoard && _kanbanBoard.columns) || [];
  return columns.map(col => {
    const tasks = (col.tasks || []).filter(task => {
      if (!filters.q) return true;
      const haystack = [task.id, _kanbanTaskTitle(task), _kanbanTaskBody(task), task.assignee, task.tenant]
        .filter(Boolean).join(' ').toLowerCase();
      return haystack.includes(filters.q);
    });
    return {...col, tasks};
  });
}

function _kanbanRenderSidebar(columns){
  const list = $('kanbanList');
  if (!list) return;
  const tasks = columns.flatMap(col => (col.tasks || []).map(task => ({...task, status: task.status || col.name})));
  if (!tasks.length) {
    list.innerHTML = `<div class="kanban-empty" data-i18n="kanban_no_matching_tasks">${esc(t('kanban_no_matching_tasks'))}</div>`;
    return;
  }
  list.innerHTML = tasks.map(task => {
    const meta = _kanbanTaskMeta(task);
    return `<button class="kanban-list-item" onclick="loadKanbanTask('${esc(task.id)}')">
      <span class="kanban-list-status" style="color:var(--kanban-${esc(task.status || 'todo')})">● ${esc(_kanbanColumnLabel(task.status))}</span>
      <span class="kanban-list-title">${esc(_kanbanTaskTitle(task))}</span>
      ${meta.length ? `<span class="kanban-meta">${esc(meta.join(' · '))}</span>` : ''}
    </button>`;
  }).join('');
}


function _kanbanRenderMarkdownInline(escaped){
  return String(escaped || '')
    .replace(/`([^`\n]+)`/g, (_m, code) => `<code>${code}</code>`)
    .replace(/\*\*([^*\n]+)\*\*/g, (_m, text) => `<strong>${text}</strong>`)
    .replace(/(^|[^*])\*([^*\n]+)\*/g, (_m, prefix, text) => `${prefix}<em>${text}</em>`)
    .replace(/\[([^\]\n]+)\]\((https?:\/\/[^\s)]+|mailto:[^\s)]+)\)/g, (_m, text, href) => `<a href="${href}" target="_blank" rel="noopener noreferrer">${text}</a>`);
}

function _kanbanRenderMarkdown(source){
  if (!source) return '';
  return `<div class="hermes-kanban-md">${esc(source).split(/\r?\n/).map(line => line.trim() ? `<p>${_kanbanRenderMarkdownInline(line)}</p>` : '').join('')}</div>`;
}

function _kanbanFormatDuration(seconds){
  const n = Number(seconds);
  if (!Number.isFinite(n) || n <= 0) return '';
  if (n < 60) return Math.round(n) + 's';
  if (n < 3600) return Math.round(n / 60) + 'm';
  if (n < 86400) return Math.round(n / 3600) + 'h';
  return Math.round(n / 86400) + 'd';
}

function _kanbanTaskAge(task){
  const age = task && (task.age_seconds || task.age);
  if (Number.isFinite(Number(age))) return _kanbanFormatDuration(age);
  return '';
}

function _kanbanCardStalenessClass(task){
  const age = Number(task && (task.age_seconds || task.age));
  const status = task && task.status;
  if (!Number.isFinite(age)) return '';
  if ((status === 'running' && age > 3600) || (status === 'blocked' && age > 86400)) return 'kanban-card-stale-red';
  if ((status === 'running' && age > 600) || (status === 'ready' && age > 3600) || (status === 'blocked' && age > 3600)) return 'kanban-card-stale-amber';
  return '';
}

function _kanbanCardQuickActions(task){
  const id = esc(task.id || '');
  const status = task.status || '';
  const complete = status !== 'done' && status !== 'archived' ? `<button type="button" class="kanban-card-action" onclick="quickKanbanCardAction(event,'${id}','done')">${esc(t('kanban_card_complete'))}</button>` : '';
  const archive = status !== 'archived' ? `<button type="button" class="kanban-card-action danger" onclick="quickKanbanCardAction(event,'${id}','archived')">${esc(t('kanban_card_archive'))}</button>` : '';
  return `<div class="kanban-card-actions" onclick="event.stopPropagation()">${complete}${archive}</div>`;
}

async function quickKanbanCardAction(event, taskId, status){
  if (event) event.stopPropagation();
  return updateKanbanTask(taskId, {status});
}

function dragKanbanTask(event, taskId){
  if (!event.dataTransfer) return;
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', taskId);
}

function allowKanbanDrop(event){
  // Don't accept drops into the 'running' column. Entering 'running' is owned
  // by the dispatcher/claim_task path (sets claim_lock + claim_expires +
  // started_at + worker_pid). A drag-drop would bypass that contract and the
  // bridge would reject the resulting PATCH with HTTP 400 anyway. Refuse the
  // drop visually so users see immediate feedback.
  const target = event.currentTarget;
  if (target && target.dataset && target.dataset.kanbanStatus === 'running') {
    if (event.dataTransfer) event.dataTransfer.dropEffect = 'none';
    return;
  }
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'move';
}

function clearKanbanDrop(event){
  if (event && event.currentTarget) event.currentTarget.classList.remove('drop-target');
}

async function dropKanbanTask(event, status){
  event.preventDefault();
  clearKanbanDrop(event);
  const taskId = event.dataTransfer ? event.dataTransfer.getData('text/plain') : '';
  if (taskId && status) await updateKanbanTask(taskId, {status});
}

function _kanbanLaneNames(columns){
  const names = new Set();
  columns.forEach(col => (col.tasks || []).forEach(task => names.add(task.assignee || t('kanban_unassigned'))));
  return Array.from(names).sort((a, b) => String(a).localeCompare(String(b)));
}

function _kanbanRenderColumn(col){
  const tasks = col.tasks || [];
  return `<section class="kanban-column" data-status="${esc(col.name)}" data-kanban-status="${esc(col.name)}" ondragover="allowKanbanDrop(event)" ondragenter="event.currentTarget.classList.add('drop-target')" ondragleave="clearKanbanDrop(event)" ondrop="dropKanbanTask(event, '${esc(col.name)}')">
      <div class="kanban-column-head">
        <span>${esc(_kanbanColumnLabel(col.name))}</span>
        <span class="kanban-count">${tasks.length}</span>
      </div>
      <div class="kanban-column-body">
        ${tasks.length ? tasks.map(task => _kanbanCard(task, col.name)).join('') : `<div class="kanban-empty">${esc(t('kanban_empty'))}</div>`}
      </div>
    </section>`;
}

function _kanbanRenderProfileLanes(columns){
  const lanes = _kanbanLaneNames(columns);
  if (!lanes.length) return columns.map(_kanbanRenderColumn).join('');
  return `<div class="kanban-profile-lanes">${lanes.map(lane => {
    const laneCols = columns.map(col => ({...col, tasks: (col.tasks || []).filter(task => (task.assignee || t('kanban_unassigned')) === lane)}));
    const count = laneCols.reduce((sum, col) => sum + (col.tasks || []).length, 0);
    return `<section class="kanban-profile-lane" data-kanban-lane="${esc(lane)}"><header class="kanban-profile-lane-head"><span>${esc(lane)}</span><span class="kanban-count">${count}</span></header><div class="kanban-board kanban-board-in-lane">${laneCols.map(_kanbanRenderColumn).join('')}</div></section>`;
  }).join('')}</div>`;
}

function _kanbanEmptyBoardHtml(){
  return `<div class="main-view-empty"><div class="main-view-empty-title">${esc(t('kanban_no_data'))}</div><div class="main-view-empty-sub">${esc(t('kanban_work_queue_hint'))}</div></div>`;
}

function _kanbanRenderBoard(){
  const board = $('kanbanBoard');
  if (!board) return;
  if (!_kanbanBoard || !_kanbanBoard.columns) {
    board.innerHTML = _kanbanEmptyBoardHtml();
    return;
  }
  const columns = _kanbanVisibleTasks();
  const total = columns.reduce((n, col) => n + (col.tasks || []).length, 0);
  if ($('kanbanSummary')) $('kanbanSummary').textContent = String(t('kanban_visible_tasks')).replace('{0}', total);
  _kanbanRenderSidebar(columns);
  if (total === 0) {
    board.innerHTML = _kanbanEmptyBoardHtml();
    return;
  }
  board.innerHTML = _kanbanLanesByProfile ? _kanbanRenderProfileLanes(columns) : columns.map(_kanbanRenderColumn).join('');
}

function _kanbanCard(task, status){
  const priority = Number(task.priority || 0);
  const links = task.link_counts || {};
  const linkTotal = Number(links.parents || 0) + Number(links.children || 0);
  const comments = Number(task.comment_count || 0);
  const age = _kanbanTaskAge(task);
  const stale = _kanbanCardStalenessClass(task);
  const body = _kanbanTaskBody(task);
  const assignee = task.assignee ? `<span class="kanban-card-assignee">@${esc(task.assignee)}</span>` : `<span class="kanban-card-unassigned">${esc(t('kanban_unassigned'))}</span>`;
  return `<article class="kanban-card kanban-card--${esc(status || 'todo')} ${esc(stale)}" data-kanban-task-id="${esc(task.id)}" draggable="true" ondragstart="dragKanbanTask(event, '${esc(task.id)}')" onclick="loadKanbanTask('${esc(task.id)}')" tabindex="0" role="button" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();loadKanbanTask('${esc(task.id)}')}">
    <div class="kanban-card-topline"><span class="kanban-card-id">${esc(task.id || '')}</span>${priority ? `<span class="kanban-badge priority">P${priority}</span>` : ''}${task.tenant ? `<span class="kanban-badge tenant">${esc(task.tenant)}</span>` : ''}</div>
    <div class="kanban-card-title">${esc(_kanbanTaskTitle(task))}</div>
    ${body ? `<div class="kanban-card-body">${_kanbanRenderMarkdown(body)}</div>` : ''}
    <div class="kanban-card-meta">${assignee}${comments ? `<span class="kanban-card-metric">💬 ${comments}</span>` : ''}${linkTotal ? `<span class="kanban-card-metric">↔ ${linkTotal}</span>` : ''}${age ? `<span class="kanban-card-age">${esc(age)}</span>` : ''}</div>
    ${_kanbanCardQuickActions(task)}
  </article>`;
}

async function hardRefreshWebUIClient(){
  try {
    if (navigator.serviceWorker) {
      const regs = await navigator.serviceWorker.getRegistrations();
      await Promise.all(regs.map(r => r.unregister()));
    }
  } catch(_) {}
  try {
    if (window.caches) {
      const keys = await caches.keys();
      await Promise.all(keys.map(k => caches.delete(k)));
    }
  } catch(_) {}
  window.location.reload();
}

function _kanbanLooksLikeStaleClientError(err){
  const msg = String((err && err.message) || err || '').toLowerCase();
  return !!(err && err.status === 404 && (
    msg === 'not found' ||
    msg.includes('unknown kanban endpoint') ||
    msg.includes('stale cached bundle')
  ));
}

function _kanbanUnavailableHtml(err){
  const raw = String((err && err.message) || err || '');
  if (_kanbanLooksLikeStaleClientError(err)) {
    return `<div class="main-view-empty"><div class="main-view-empty-title">Kanban needs a hard refresh</div><div class="main-view-empty-subtitle">The server rejected an obsolete Kanban endpoint. This usually means the browser or Mac app is still running a stale cached WebUI bundle after an update.</div><button class="btn primary" type="button" onclick="hardRefreshWebUIClient()">Hard refresh now</button><div class="main-view-empty-subtitle">Original error: ${esc(raw || 'not found')}</div></div>`;
  }
  const msg = `${esc(t('kanban_unavailable'))}: ${esc(raw)}`;
  return `<div class="main-view-empty"><div class="main-view-empty-title">${msg}</div></div>`;
}

async function loadKanban(animate){
  const spaceLoadKey = (typeof _activeSpaceLoadKey === 'function') ? _activeSpaceLoadKey() : '';
  const stillCurrentSpace = () => !spaceLoadKey || typeof isActiveSpaceLoadKey !== 'function' || isActiveSpaceLoadKey(spaceLoadKey);
  const board = $('kanbanBoard');
  const list = $('kanbanList');
  try {
    if (animate && board) board.innerHTML = `<div style="padding:16px;color:var(--muted);font-size:13px">${esc(t('loading'))}</div>`;
    // Resolve the active board before board-scoped requests. If another CLI or
    // tab archived the previous board, /boards can fall back to default instead
    // of leaving config/board pinned to a ghost slug.
    const boardsLoaded = await loadKanbanBoards(spaceLoadKey);
    if (boardsLoaded === false || !stillCurrentSpace()) return;
    const config = await api('/api/kanban/config' + _kanbanBoardQuery());
    if (!stillCurrentSpace()) return;
    let assignees = null;
    try { assignees = await api('/api/kanban/assignees' + _kanbanBoardQuery()); } catch(e) { assignees = null; }
    if (!stillCurrentSpace()) return;
    _kanbanApplyConfigDefaults(config);
    const filters = _kanbanCurrentFilters();
    const params = new URLSearchParams();
    if (typeof getActiveSpaceQuery === 'function') {
      const activeSpace = new URLSearchParams(getActiveSpaceQuery().slice(1)).get('workspace');
      if (activeSpace) params.set('workspace', activeSpace);
    }
    if (filters.assignee) params.set('assignee', filters.assignee);
    if (filters.tenant) params.set('tenant', filters.tenant);
    if (filters.includeArchived) params.set('include_archived', '1');
    if (filters.onlyMine) params.set('only_mine', '1');
    if (_kanbanCurrentBoard) params.set('board', _kanbanCurrentBoard);
    const path = '/api/kanban/board' + (params.toString() ? '?' + params.toString() : '');
    const data = await api(path);
    if (!stillCurrentSpace()) return;
    if (data && data.changed === false && _kanbanBoard) { _kanbanRenderBoard(); return; }
    _kanbanBoard = data || {columns: []};
    if ((!_kanbanBoard.columns || !_kanbanBoard.columns.length) && config && config.columns) {
      _kanbanBoard.columns = config.columns.map(name => ({name, tasks: []}));
    }
    _kanbanLatestEventId = Number(_kanbanBoard.latest_event_id || 0);
    // Toggle the "Read-only view" banner based on the bridge's read_only flag.
    // Bridge sets read_only=true only when the kanban_db connection cannot accept
    // writes (e.g. dispatcher contention or library missing). Hide otherwise.
    try {
      const ro = document.querySelector('.kanban-readonly');
      if (ro) ro.style.display = _kanbanBoard.read_only ? '' : 'none';
    } catch(_) {}
    _kanbanSetSelectOptions($('kanbanAssigneeFilter'), _kanbanBoard.assignees || (assignees && assignees.assignees) || (config && config.assignees), 'kanban_all_assignees');
    _kanbanSetSelectOptions($('kanbanTenantFilter'), _kanbanBoard.tenants, 'kanban_all_tenants');
    await loadKanbanStats(spaceLoadKey);
    if (!stillCurrentSpace()) return;
    // Note: PR #1828 (v0.51.20) moved the boards refresh to the start of
    // loadKanban() so the active board is resolved BEFORE board-scoped
    // requests fire. The previous tail-of-function refresh has been removed
    // to avoid doubling /api/kanban/boards traffic during SSE-driven
    // refreshes (debounced at 250ms via _scheduleKanbanRefresh). The
    // Keep live updates active without reopening an already healthy stream
    // on every render refresh.
    _kanbanEnsurePollingActive();
    _kanbanRenderBoard();
  } catch(e) {
    if (!stillCurrentSpace()) return;
    const html = _kanbanUnavailableHtml(e);
    if (board) board.innerHTML = html;
    if (list) list.innerHTML = html;
  }
}

function filterKanban(){ _kanbanRenderBoard(); }

async function loadKanbanStats(spaceLoadKey){
  const stillCurrentSpace = () => !spaceLoadKey || typeof isActiveSpaceLoadKey !== 'function' || isActiveSpaceLoadKey(spaceLoadKey);
  try {
    const stats = await api('/api/kanban/stats' + _kanbanBoardQuery());
    if (!stillCurrentSpace()) return;
    const el = $('kanbanStats');
    if (!el) return;
    const byStatus = (stats && stats.by_status) || {};
    const total = Object.values(byStatus).reduce((a, b) => a + Number(b || 0), 0);
    const cells = Object.entries(byStatus).sort(([a], [b]) => a.localeCompare(b)).map(([status, count]) =>
      `<span class="kanban-stat-cell"><strong>${esc(String(count))}</strong> ${esc(_kanbanColumnLabel(status))}</span>`
    ).join('');
    el.innerHTML = `<div class="kanban-stats-grid"><span class="kanban-stat-cell total"><strong>${esc(String(total))}</strong> ${esc(t('kanban_stats'))}</span>${cells}</div>`;
  } catch(e) { /* stats are best-effort */ }
}

async function refreshKanbanEvents(){
  if (_currentPanel !== 'kanban' || !_kanbanLatestEventId) return;
  try {
    const eventsEndpoint = '/api/kanban/events';
    const events = await api(eventsEndpoint + _kanbanBoardQuery({since: _kanbanLatestEventId}));
    if (events && Array.isArray(events.events) && events.events.length) {
      _kanbanLatestEventId = Number(events.latest_event_id || events.cursor || _kanbanLatestEventId);
      await loadKanban(true);
      if (_kanbanCurrentTaskId && events.events.some(ev => ev.task_id === _kanbanCurrentTaskId)) await loadKanbanTask(_kanbanCurrentTaskId);
    }
  } catch(e) { /* polling should not spam toasts */ }
}

function _kanbanStartPolling(){
  // Prefer SSE for low-latency live updates. Fall back to polling on
  // browsers without EventSource or after repeated stream failures.
  if (typeof EventSource === 'undefined' || _kanbanEventSourceFailures >= 3) {
    if (_kanbanPollTimer) return;
    _kanbanPollTimer = setInterval(refreshKanbanEvents, 30000);
    return;
  }
  _kanbanStartEventStream();
}

function _kanbanEnsurePollingActive(){
  if (_kanbanPollTimer || _kanbanEventSource) return;
  _kanbanStartPolling();
}

function _kanbanStopPolling(){
  if (_kanbanPollTimer) { clearInterval(_kanbanPollTimer); _kanbanPollTimer = null; }
  if (_kanbanEventSource) { try { _kanbanEventSource.close(); } catch(_) {} _kanbanEventSource = null; }
}

function _kanbanStartEventStream(){
  // Tear down any prior stream before opening a new one (board switch,
  // login change, etc.).
  if (_kanbanEventSource) { try { _kanbanEventSource.close(); } catch(_) {} _kanbanEventSource = null; }
  const since = Number(_kanbanLatestEventId || 0);
  let url = '/api/kanban/events/stream' + _kanbanBoardQuery({since: since});
  let es;
  try {
    es = new EventSource(_eventSourceUrl(url));
  } catch(e) {
    _kanbanEventSourceFailures += 1;
    if (_kanbanEventSourceFailures < 3 && !_kanbanPollTimer) {
      _kanbanPollTimer = setInterval(refreshKanbanEvents, 30000);
    }
    return;
  }
  _kanbanEventSource = es;
  es.addEventListener('hello', (ev) => {
    // Reset the failure counter on a successful handshake.
    _kanbanEventSourceFailures = 0;
  });
  es.addEventListener('events', async (ev) => {
    if (_currentPanel !== 'kanban') return;  // ignore while user is on another panel
    let data;
    try { data = JSON.parse(ev.data); } catch(_) { return; }
    if (!data || !Array.isArray(data.events) || !data.events.length) return;
    _kanbanLatestEventId = Number(data.cursor || _kanbanLatestEventId);
    // Re-fetch the board so the visual state reflects the new events.
    // Throttle: if events are arriving faster than ~1/sec we coalesce.
    _scheduleKanbanRefresh(data.events);
  });
  es.onerror = () => {
    _kanbanEventSourceFailures += 1;
    if (_kanbanEventSourceFailures >= 3) {
      // Give up on SSE for this session — fall back to HTTP polling.
      try { es.close(); } catch(_) {}
      _kanbanEventSource = null;
      if (!_kanbanPollTimer) _kanbanPollTimer = setInterval(refreshKanbanEvents, 30000);
    }
    // EventSource auto-reconnects under the hood; nothing more to do here
    // until we hit the failure limit.
  };
}

let _kanbanRefreshScheduled = false;
let _kanbanRefreshPendingTaskIds = new Set();
function _scheduleKanbanRefresh(events){
  for (const ev of events) {
    if (ev && ev.task_id) _kanbanRefreshPendingTaskIds.add(ev.task_id);
  }
  if (_kanbanRefreshScheduled) return;
  _kanbanRefreshScheduled = true;
  // 250ms debounce — keeps a burst of N events from triggering N reloads.
  setTimeout(async () => {
    _kanbanRefreshScheduled = false;
    const taskIds = Array.from(_kanbanRefreshPendingTaskIds);
    _kanbanRefreshPendingTaskIds.clear();
    if (_currentPanel !== 'kanban') return;
    try {
      await loadKanban(true);
      if (_kanbanCurrentTaskId && taskIds.includes(_kanbanCurrentTaskId)) {
        await loadKanbanTask(_kanbanCurrentTaskId);
      }
    } catch(_) { /* swallow — SSE refresh shouldn't toast */ }
  }, 250);
}

// Build a "?board=<slug>" or "?since=N&board=<slug>" query string fragment
// based on the active board. Empty when the user is on the default board
// AND nobody has explicitly switched (so we don't pin to "default" and
// override a hypothetical server-side switch).
function _kanbanBoardQuery(extra){
  const params = new URLSearchParams();
  if (typeof getActiveSpaceQuery === 'function') {
    const activeSpace = new URLSearchParams(getActiveSpaceQuery().slice(1)).get('workspace');
    if (activeSpace) params.set('workspace', activeSpace);
  }
  if (extra) {
    for (const [k, v] of Object.entries(extra)) {
      if (v !== null && v !== undefined && v !== '') params.set(k, String(v));
    }
  }
  if (_kanbanCurrentBoard) params.set('board', _kanbanCurrentBoard);
  const s = params.toString();
  return s ? '?' + s : '';
}

async function nudgeKanbanDispatcher(){
  if (_kanbanIsDispatching) return;
  // Dry-run dispatch: show what WOULD be spawned, without actually spawning
  // workers.  Uses ?dry_run=1 so the dispatcher reports its plan without
  // mutating the board.  The result shape includes spawned/skipped_unassigned/
  // skipped_nonspawnable/promoted/auto_blocked so users can diagnose why a
  // Ready task isn't being picked up before they commit to a real run.
  _kanbanIsDispatching = true;
  _setKanbanDispatcherButtonsDisabled(true);
  try {
    const dispatchEndpoint = '/api/kanban/dispatch';
    const result = await api(
      dispatchEndpoint + _kanbanBoardQuery({dry_run: 1, max: 8}),
      {method: 'POST'},
    );
    showToast(_kanbanFormatDispatchResult(result, true), 'info', 6000);
    await loadKanban(true);
  } catch(e) {
    showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error');
  } finally {
    _kanbanIsDispatching = false;
    _setKanbanDispatcherButtonsDisabled(false);
  }
}

async function runKanbanDispatcher(){
  if (_kanbanIsDispatching) return;
  // Real dispatch: claims Ready tasks and spawns worker subprocesses
  // (one `hermes -p <assignee>` per claimed row, up to max=8 per call).
  // Confirmation dialog first because this actually consumes API budget on
  // each spawned worker.  Result toast surfaces what happened so users see
  // the dispatcher actually doing work.
  if (!_kanbanCurrentBoard) {
    showToast(t('kanban_unavailable') || 'Kanban unavailable', 'error');
    return;
  }

  _kanbanIsDispatching = true;
  _setKanbanDispatcherButtonsDisabled(true);
  try {
    const ok = await showConfirmDialog({
      title: t('kanban_run_dispatcher') || 'Run dispatcher',
      message: t('kanban_run_dispatcher_confirm')
        || 'This will claim Ready tasks on this board and spawn worker subprocesses (one per task, up to 8 per click). Continue?',
      confirmLabel: t('kanban_run_dispatcher') || 'Run dispatcher',
    });
    if (!ok) return;
    const dispatchEndpoint = '/api/kanban/dispatch';
    const result = await api(dispatchEndpoint + _kanbanBoardQuery({max: 8}), {method: 'POST'});
    showToast(_kanbanFormatDispatchResult(result, false), 'info', 8000);
    await loadKanban(true);
  } catch(e) {
    showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error');
  } finally {
    _kanbanIsDispatching = false;
    _setKanbanDispatcherButtonsDisabled(false);
  }
}

function _setKanbanDispatcherButtonsDisabled(disabled){
  document.querySelectorAll('.kanban-run-dispatch-btn, .kanban-nudge-dispatch-btn').forEach((btn) => {
    btn.disabled = !!disabled;
    btn.classList.toggle('disabled', !!disabled);
  });
}

function _kanbanFormatDispatchResult(result, dryRun){
  // Produce a human-readable one-line summary of dispatch_once's output so
  // users can see exactly what happened rather than a generic "OK" toast.
  const r = result || {};
  const spawned = (r.spawned || []).length;
  const promoted = r.promoted || 0;
  const reclaimed = r.reclaimed || 0;
  const skippedUnassigned = (r.skipped_unassigned || []).length;
  const skippedNonspawnable = (r.skipped_nonspawnable || []).length;
  const autoBlocked = (r.auto_blocked || []).length;
  const timedOut = (r.timed_out || []).length;
  const crashed = (r.crashed || []).length;
  const verb = dryRun ? (t('kanban_dispatch_preview_prefix') || 'Preview:') : (t('kanban_dispatch_run_prefix') || 'Dispatched:');
  const parts = [];
  parts.push(spawned + ' ' + (t('kanban_dispatch_spawned') || 'spawned'));
  if (promoted) parts.push(promoted + ' ' + (t('kanban_dispatch_promoted') || 'promoted'));
  if (reclaimed) parts.push(reclaimed + ' ' + (t('kanban_dispatch_reclaimed') || 'reclaimed'));
  if (skippedUnassigned) parts.push(skippedUnassigned + ' ' + (t('kanban_dispatch_skipped_unassigned') || 'skipped (no assignee)'));
  if (skippedNonspawnable) parts.push(skippedNonspawnable + ' ' + (t('kanban_dispatch_skipped_nonspawnable') || 'skipped (unknown profile)'));
  if (autoBlocked) parts.push(autoBlocked + ' ' + (t('kanban_dispatch_auto_blocked') || 'auto-blocked'));
  if (timedOut) parts.push(timedOut + ' ' + (t('kanban_dispatch_timed_out') || 'timed out'));
  if (crashed) parts.push(crashed + ' ' + (t('kanban_dispatch_crashed') || 'crashed'));
  return verb + ' ' + parts.join(', ');
}

function _kanbanSelectedTaskIds(){
  const selected = Array.from(document.querySelectorAll('.kanban-card.selected')).map(card => card.dataset.kanbanTaskId).filter(Boolean);
  return selected.length ? selected : (_kanbanCurrentTaskId ? [_kanbanCurrentTaskId] : []);
}

async function bulkUpdateKanban(){
  const ids = _kanbanSelectedTaskIds();
  const status = $('kanbanBulkStatus') ? $('kanbanBulkStatus').value : '';
  if (!ids.length || !status) return;
  try {
    await api('/api/kanban/tasks/bulk' + _kanbanBoardQuery(), {method: 'POST', body: JSON.stringify({ids, status})});
    showToast(t('kanban_bulk_action'));
    await loadKanban(true);
  } catch(e) { showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error'); }
}

async function blockKanbanTask(taskId){
  try {
    await api('/api/kanban/tasks/' + encodeURIComponent(taskId) + '/block' + _kanbanBoardQuery(), {method: 'POST', body: JSON.stringify({reason: 'blocked from WebUI'})});
    await loadKanbanTask(taskId);
    await loadKanban(true);
  } catch(e) { showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error'); }
}

async function unblockKanbanTask(taskId){
  try {
    await api('/api/kanban/tasks/' + encodeURIComponent(taskId) + '/unblock' + _kanbanBoardQuery(), {method: 'POST', body: JSON.stringify({})});
    await loadKanbanTask(taskId);
    await loadKanban(true);
  } catch(e) { showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error'); }
}

function closeKanbanTaskDetail(){
  _kanbanCurrentTaskId = null;
  const preview = $('kanbanTaskPreview');
  if (preview) {
    preview.style.display = 'none';
    preview.innerHTML = '';
  }
  const board = $('kanbanBoard');
  if (board) board.querySelectorAll('.kanban-card').forEach(card => card.classList.remove('selected'));
}

function _kanbanFormatTimestamp(value){
  if (value === undefined || value === null || value === '') return '';
  let date = null;
  if (typeof value === 'number') date = new Date(value > 100000000000 ? value : value * 1000);
  else if (/^\d+(?:\.\d+)?$/.test(String(value).trim())) {
    const n = Number(value);
    date = new Date(n > 100000000000 ? n : n * 1000);
  } else {
    date = new Date(value);
  }
  if (!date || Number.isNaN(date.getTime())) return String(value);
  try { return date.toLocaleString(); } catch(e) { return date.toISOString(); }
}

function _kanbanEventSummary(event){
  const kind = event.kind || event.type || 'event';
  const payload = event.payload || event.data || {};
  if (payload && typeof payload === 'object') {
    const parts = [];
    if (payload.status) parts.push(String(payload.status));
    if (payload.reason) parts.push(String(payload.reason));
    if (payload.summary) parts.push(String(payload.summary));
    if (payload.fields && Array.isArray(payload.fields)) parts.push(payload.fields.join(', '));
    if (parts.length) return `${kind}: ${parts.join(' · ')}`;
  }
  return String(kind);
}

function _kanbanFormatDetailValue(value){
  if (value === undefined || value === null || value === '') return '';
  if (typeof value === 'object') {
    try { return JSON.stringify(value, null, 2); } catch(e) { return String(value); }
  }
  return String(value);
}

function _kanbanDetailSection(cls, title, inner, emptyKey){
  const content = inner || `<div class="kanban-detail-empty">${esc(t(emptyKey))}</div>`;
  return `<section class="kanban-detail-section ${cls}">
    <h3>${esc(title)}</h3>
    ${content}
  </section>`;
}

function _kanbanCommentHtml(comment){
  const body = comment.body || comment.text || comment.content || '';
  const by = comment.author || comment.created_by || comment.actor || '';
  const at = _kanbanFormatTimestamp(comment.created_at || comment.ts || '');
  return `<div class="kanban-detail-row">
    <div class="kanban-detail-row-main">${esc(body)}</div>
    <div class="kanban-detail-row-meta">${esc([by, at].filter(Boolean).join(' · '))}</div>
  </div>`;
}

function _kanbanEventHtml(event){
  const at = _kanbanFormatTimestamp(event.created_at || event.ts || '');
  const payload = _kanbanFormatDetailValue(event.payload || event.data || '');
  return `<div class="kanban-detail-row">
    <div class="kanban-detail-row-main">${esc(_kanbanEventSummary(event))}</div>
    ${payload ? `<pre class="kanban-detail-pre">${esc(payload)}</pre>` : ''}
    <div class="kanban-detail-row-meta">${esc(at)}</div>
  </div>`;
}

function _kanbanRunHtml(run){
  const status = run.status || run.state || run.result || '';
  const label = run.run_id || run.id || run.worker || t('kanban_task');
  const started = _kanbanFormatTimestamp(run.started_at || run.created_at || '');
  const finished = _kanbanFormatTimestamp(run.finished_at || run.completed_at || '');
  const detail = run.error || run.summary || run.log_tail || '';
  return `<div class="kanban-detail-row">
    <div class="kanban-detail-row-main">${esc(label)}${status ? ` · ${esc(status)}` : ''}</div>
    ${detail ? `<pre class="kanban-detail-pre">${esc(_kanbanFormatDetailValue(detail))}</pre>` : ''}
    <div class="kanban-detail-row-meta">${esc([started, finished].filter(Boolean).join(' → '))}</div>
  </div>`;
}

function _kanbanLinksHtml(links){
  const parents = (links && links.parents) || [];
  const children = (links && links.children) || [];
  if (!parents.length && !children.length) return '';
  const item = id => `<code>${esc(id)}</code>`;
  return `<div class="kanban-detail-links-grid">
    <div><strong>${esc(t('kanban_parents'))}</strong><div>${parents.length ? parents.map(item).join(' ') : esc(t('kanban_empty'))}</div></div>
    <div><strong>${esc(t('kanban_children'))}</strong><div>${children.length ? children.map(item).join(' ') : esc(t('kanban_empty'))}</div></div>
  </div>`;
}

async function createKanbanTask(){
  const input = document.getElementById('kanbanNewTaskTitle');
  const title = input ? input.value.trim() : '';
  if (!title) {
    // Empty inline input (or a click on the panel-head "+" via openKanbanCreate)
    // — open the full create-task modal so the user has somewhere obvious to
    // type and configure the task. Mirrors the cron / skills pattern of routing
    // header "+" clicks through to a clearly-modal create surface.
    openKanbanCreate();
    return;
  }
  try {
    const created = await api('/api/kanban/tasks' + _kanbanBoardQuery(), {
      method: 'POST',
      body: JSON.stringify({title}),
    });
    if (input) input.value = '';
    await loadKanban(true);
    if (created && created.task && created.task.id) await loadKanbanTask(created.task.id);
  } catch(e) { showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error'); }
}

// ────────────────────────────────────────────────────────────────────────────
// Kanban: create-task modal (panel-head "+" button entry point).
//
// Same `.kanban-modal-overlay` shell as openKanbanCreateBoard() so the two
// flows look and behave identically (centered card, dim backdrop, ESC closes,
// click-on-backdrop closes). The modal markup lives in static/index.html as
// #kanbanTaskModal — see the section just above </body>. Submit hits the
// existing /api/kanban/tasks POST endpoint (which already accepts title, body,
// assignee, tenant, priority, status — see api/kanban_bridge.py:306).
// ────────────────────────────────────────────────────────────────────────────

// ────────────────────────────────────────────────────────────────────────────
// Kanban: create-task / edit-task modal (panel-head "+" + task-detail Edit
// button entry points).
//
// Single modal serves both flows.  Title + submit-button labels and the
// underlying submit verb (POST vs PATCH) flip based on `_kanbanTaskModalMode`.
//
// Same `.kanban-modal-overlay` shell as openKanbanCreateBoard() so the two
// flows look and behave identically (centered card, dim backdrop, ESC closes,
// click-on-backdrop closes). The modal markup lives in static/index.html as
// #kanbanTaskModal — see the section just above </body>.
//
// The assignee field auto-completes against the union of (a) live Sidekick
// profile names from /api/profiles and (b) historical assignees on the
// active board, with an inline hint that explains the dispatcher claim
// contract — most users will pick a profile name from the dropdown rather
// than type one.
// ────────────────────────────────────────────────────────────────────────────

let _kanbanTaskModalMode = 'create';   // 'create' | 'edit'
let _kanbanTaskModalEditingId = null;  // task id when mode === 'edit'
let _kanbanProfileNamesCache = null;   // populated lazily on first modal open
let _kanbanProfileNamesCacheAt = 0;
const _KANBAN_PROFILE_NAMES_CACHE_TTL_MS = 30000;
function _invalidateKanbanProfileCache() {
  _kanbanProfileNamesCache = null;
  _kanbanProfileNamesCacheAt = 0;
}
let _kanbanTaskModalFocusCleanup = null;
// Status the modal *displayed* on edit-mode open.  If the user doesn't touch
// the dropdown, we must NOT send `status` in the PATCH payload — otherwise
// editing a task whose real status is non-editable in this dropdown
// (running/blocked/done/archived → mapped to 'triage' for display) would
// silently demote the task on save.  See the regression caught during PR
// review: editing a 'running' task without touching status was reclaiming
// the worker and moving the task back to triage.
let _kanbanTaskModalInitialDisplayedStatus = null;
let _kanbanBoardModalFocusCleanup = null;

async function _kanbanLoadProfileNames(){
  // Hit /api/profiles once per session and cache for a short TTL.
  // Returns an array of profile names (sorted, default first if present).
  const hasFreshCache = (
    Array.isArray(_kanbanProfileNamesCache) &&
    (Date.now() - _kanbanProfileNamesCacheAt) < _KANBAN_PROFILE_NAMES_CACHE_TTL_MS
  );
  if (hasFreshCache) return _kanbanProfileNamesCache;
  try {
    const data = await api('/api/profiles');
    const profiles = Array.isArray(data && data.profiles) ? data.profiles : [];
    const names = profiles.map(p => p && p.name).filter(Boolean);
    // Stable order: default first, then alphabetical.
    names.sort((a, b) => {
      if (a === 'default') return -1;
      if (b === 'default') return 1;
      return a.localeCompare(b);
    });
    _kanbanProfileNamesCache = names;
    _kanbanProfileNamesCacheAt = Date.now();
    return names;
  } catch(_) {
    _kanbanProfileNamesCache = [];
    _kanbanProfileNamesCacheAt = Date.now();
    return [];
  }
}

async function _kanbanPopulateAssigneeSelect(currentValue){
  const sel = document.getElementById('kanbanTaskModalAssignee');
  if (!sel) return;
  // Profile names: the canonical set the dispatcher can claim.
  const profileNames = await _kanbanLoadProfileNames();
  // Historical assignees from the active board: include them so users who
  // assigned to a CLI lane (e.g. orion-cc) before still see those values.
  const historicalAssignees = (_kanbanBoard && Array.isArray(_kanbanBoard.assignees))
    ? _kanbanBoard.assignees
    : [];
  // Build a final ordered list, deduping.  Profiles come first, then any
  // historical assignees that aren't profiles (rare but keeps round-tripping
  // correct for tasks created via CLI).
  const seen = new Set();
  const profiles = [];
  for (const name of profileNames) {
    if (!seen.has(name)) { profiles.push(name); seen.add(name); }
  }
  const extras = [];
  for (const name of historicalAssignees) {
    if (name && !seen.has(name)) { extras.push(name); seen.add(name); }
  }
  // If the current value isn't in either bucket (e.g. an old CLI-created
  // assignee that's since been deleted), preserve it as a final option so
  // editing the task doesn't silently change its assignee.
  if (currentValue && !seen.has(currentValue)) {
    extras.push(currentValue);
    seen.add(currentValue);
  }
  // The empty value maps to null on submit (intentionally unassigned).  Keep
  // it last so the default-selected option is the first profile, not "no one".
  let html = '';
  if (profiles.length) {
    html += `<optgroup label="${esc(t('kanban_assignee_profiles_label') || 'Nova profiles')}">`;
    html += profiles.map(v => `<option value="${esc(v)}"${v === currentValue ? ' selected' : ''}>${esc(v)}</option>`).join('');
    html += '</optgroup>';
  }
  if (extras.length) {
    html += `<optgroup label="${esc(t('kanban_assignee_other_label') || 'Other (CLI lanes / removed profiles)')}">`;
    html += extras.map(v => `<option value="${esc(v)}"${v === currentValue ? ' selected' : ''}>${esc(v)}</option>`).join('');
    html += '</optgroup>';
  }
  // Final "no assignee" fallthrough — explicit so users know what they're choosing.
  html += `<option value=""${(!currentValue) ? ' selected' : ''}>${esc(t('kanban_assignee_unassigned') || '— Unassigned (won\u2019t auto-run) —')}</option>`;
  sel.innerHTML = html;
}

function openKanbanCreate(){
  // Make sure the user is on the kanban panel so the resulting board reload is
  // visible behind the modal.
  if (typeof switchPanel === 'function' && _currentPanel !== 'kanban') switchPanel('kanban');
  const modal = document.getElementById('kanbanTaskModal');
  if (!modal) return;
  _kanbanTaskModalMode = 'create';
  _kanbanTaskModalEditingId = null;
  _kanbanTaskModalInitialDisplayedStatus = null;  // create mode: always send status
  // Default new tasks to "ready" so they're immediately claimable by the
  // dispatcher (assuming the user picks an assignee).  Triage is for staging
  // tasks that need human review before being marked actionable; users who
  // want it can still pick it from the status dropdown.
  _kanbanResetTaskModalFields({status: 'ready'});
  _kanbanSetTaskModalStatusHint(null);
  _kanbanSetTaskModalLabels('create');
  _kanbanPopulateAssigneeSelect('').then(() => {
    // After the dropdown is populated, default-select the first profile (not
    // the "Unassigned" fallthrough).  This is the right hint: most users want
    // to assign to *something* — they can pick "Unassigned" deliberately.
    const sel = document.getElementById('kanbanTaskModalAssignee');
    if (sel && sel.options.length > 0 && sel.value === '') {
      const firstProfile = Array.from(sel.options).find(opt => opt.value !== '');
      if (firstProfile) sel.value = firstProfile.value;
    }
  });
  _kanbanPopulateTenantDatalist();
  modal.hidden = false;
  if (_kanbanTaskModalFocusCleanup) {
    _kanbanTaskModalFocusCleanup();
    _kanbanTaskModalFocusCleanup = null;
  }
  _kanbanTaskModalFocusCleanup = _trapModalFocus(modal);
  setTimeout(() => {
    const titleEl = document.getElementById('kanbanTaskModalTitleInput');
    if (titleEl) titleEl.focus();
  }, 50);
  document.addEventListener('keydown', _kanbanTaskModalKey);
}

async function openKanbanEdit(taskId){
  // Triggered by the Edit button on the task detail view.  Fetches the task
  // (rather than relying on whatever's cached locally) so the modal always
  // reflects authoritative server state.
  if (!taskId) return;
  if (typeof switchPanel === 'function' && _currentPanel !== 'kanban') switchPanel('kanban');
  const modal = document.getElementById('kanbanTaskModal');
  if (!modal) return;
  let task = null;
  try {
    const data = await api('/api/kanban/tasks/' + encodeURIComponent(taskId) + _kanbanBoardQuery());
    task = data && data.task;
  } catch(e) {
    showToast((t('kanban_unavailable') || 'Kanban unavailable') + ': ' + (e.message || e), 'error');
    return;
  }
  if (!task) return;
  _kanbanTaskModalMode = 'edit';
  _kanbanTaskModalEditingId = task.id;
  // Track the displayed status so submitKanbanTaskModal can detect whether
  // the user actually picked a new value vs. the dropdown's mapped default.
  // Without this, editing a 'running'/'blocked'/'done'/'archived' task whose
  // real status maps to 'triage' for display would silently demote the task
  // (the mapped 'triage' would land in the PATCH payload, and _patch_task
  // would call _set_status_direct → reclaim worker → move to triage).
  const initialDisplayedStatus = _kanbanEditableStatusFor(task.status);
  const originalStatus = task.status || initialDisplayedStatus;
  _kanbanTaskModalInitialDisplayedStatus = initialDisplayedStatus;
  _kanbanResetTaskModalFields({
    title: task.title || '',
    body: task.body || '',
    status: initialDisplayedStatus,
    tenant: task.tenant || '',
    priority: typeof task.priority === 'number' ? task.priority : 0,
  });
  // Populate the assignee select AFTER reset so the option exists when we
  // call sel.value = currentAssignee.
  await _kanbanPopulateAssigneeSelect(task.assignee || '');
  _kanbanSetTaskModalStatusHint(originalStatus, initialDisplayedStatus);
  _kanbanSetTaskModalLabels('edit');
  _kanbanPopulateTenantDatalist();
  modal.hidden = false;
  if (_kanbanTaskModalFocusCleanup) {
    _kanbanTaskModalFocusCleanup();
    _kanbanTaskModalFocusCleanup = null;
  }
  _kanbanTaskModalFocusCleanup = _trapModalFocus(modal);
  setTimeout(() => {
    const titleEl = document.getElementById('kanbanTaskModalTitleInput');
    if (titleEl) { titleEl.focus(); titleEl.select(); }
  }, 50);
  document.addEventListener('keydown', _kanbanTaskModalKey);
}

function _kanbanEditableStatusFor(status){
  // The modal's status select only offers triage/todo/ready (the user-writable
  // states).  blocked/running/done/archived are reached via the detail-view
  // status buttons or the dispatcher.  Map non-editable states to a sensible
  // default so the user can still change them via the buttons after saving.
  const editable = new Set(['triage', 'todo', 'ready']);
  return editable.has(status) ? status : 'triage';
}

function _kanbanResetTaskModalFields(values){
  const v = values || {};
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.value = (val == null ? '' : String(val));
  };
  set('kanbanTaskModalTitleInput', v.title || '');
  set('kanbanTaskModalBody', v.body || '');
  set('kanbanTaskModalStatus', v.status || 'triage');
  // Assignee handled separately by _kanbanPopulateAssigneeSelect() because
  // it's a <select> populated from /api/profiles + board history; setting
  // .value before the options exist would silently fail.
  set('kanbanTaskModalTenant', v.tenant || '');
  set('kanbanTaskModalPriority', v.priority != null ? v.priority : 0);
  const errEl = document.getElementById('kanbanTaskModalError');
  if (errEl) { errEl.textContent = ''; delete errEl.dataset.warningShown; }
  const submitBtn = document.getElementById('kanbanTaskModalSubmit');
  if (submitBtn) submitBtn.disabled = false;
}

function _kanbanSetTaskModalLabels(mode){
  const titleH = document.getElementById('kanbanTaskModalTitle');
  const submitBtn = document.getElementById('kanbanTaskModalSubmit');
  if (mode === 'edit') {
    if (titleH) titleH.textContent = t('kanban_edit_task') || 'Edit task';
    if (submitBtn) submitBtn.textContent = t('save') || 'Save';
  } else {
    if (titleH) titleH.textContent = t('kanban_new_task') || 'New task';
    if (submitBtn) submitBtn.textContent = t('create') || 'Create';
  }
}

function _kanbanSetTaskModalStatusHint(realStatus, editableStatus){
  const hintEl = document.getElementById('kanbanTaskModalStatusOriginalHint');
  if (!hintEl) return;
  if (!realStatus || realStatus === editableStatus) {
    hintEl.hidden = true;
    hintEl.textContent = '';
    return;
  }
  const statusLabel = t(`kanban_status_${realStatus}`) || realStatus;
  hintEl.textContent = String(t('kanban_status_original_hint')).replace('{0}', statusLabel);
  hintEl.hidden = false;
}

function _kanbanPopulateTenantDatalist(){
  const tenants = (_kanbanBoard && Array.isArray(_kanbanBoard.tenants)) ? _kanbanBoard.tenants : [];
  const tList = document.getElementById('kanbanTaskModalTenantList');
  if (tList) tList.innerHTML = tenants.map(v => `<option value="${esc(v)}"></option>`).join('');
}

function _trapModalFocus(modalEl){
  if (!modalEl) return () => {};
  const selector = 'a[href], button, textarea, input, select, summary, [tabindex]:not([tabindex="-1"])';
  const collect = () => {
    const candidates = Array.from(modalEl.querySelectorAll(selector));
    return candidates.filter((el) => {
      if (el.disabled || el.hidden) return false;
      const style = getComputedStyle(el);
      if (style.display === 'none' || style.visibility === 'hidden') return false;
      return el.tabIndex >= 0;
    });
  };
  let focusableEls = collect();
  const onKeyDown = (ev) => {
    if (ev.key !== 'Tab') return;
    if (!focusableEls.length) {
      ev.preventDefault();
      return;
    }
    const current = document.activeElement;
    let idx = focusableEls.indexOf(current);
    if (idx === -1) {
      ev.preventDefault();
      focusableEls[0].focus();
      return;
    }
    if (ev.shiftKey) idx -= 1;
    else idx += 1;
    idx = (idx + focusableEls.length) % focusableEls.length;
    ev.preventDefault();
    focusableEls[idx].focus();
  };
  modalEl.addEventListener('keydown', onKeyDown);
  return () => {
    modalEl.removeEventListener('keydown', onKeyDown);
  };
}

function closeKanbanTaskModal(){
  const modal = document.getElementById('kanbanTaskModal');
  if (modal) modal.hidden = true;
  _kanbanTaskModalMode = 'create';
  _kanbanTaskModalEditingId = null;
  _kanbanTaskModalInitialDisplayedStatus = null;
  _kanbanSetTaskModalStatusHint(null, null);
  if (_kanbanTaskModalFocusCleanup) {
    _kanbanTaskModalFocusCleanup();
    _kanbanTaskModalFocusCleanup = null;
  }
  document.removeEventListener('keydown', _kanbanTaskModalKey);
}

function _kanbanTaskModalKey(ev){
  if (ev.key === 'Escape') {
    ev.preventDefault();
    closeKanbanTaskModal();
    return;
  }
  if (ev.key === 'Enter' && !ev.shiftKey) {
    // Enter submits except when the focus is in the description textarea
    // (where Enter should insert a newline).
    const target = ev.target;
    if (target && target.tagName === 'TEXTAREA') return;
    const modal = document.getElementById('kanbanTaskModal');
    if (modal && !modal.hidden) {
      ev.preventDefault();
      submitKanbanTaskModal();
    }
  }
}

async function submitKanbanTaskModal(){
  const titleEl = document.getElementById('kanbanTaskModalTitleInput');
  const bodyEl = document.getElementById('kanbanTaskModalBody');
  const statusEl = document.getElementById('kanbanTaskModalStatus');
  const assigneeEl = document.getElementById('kanbanTaskModalAssignee');
  const tenantEl = document.getElementById('kanbanTaskModalTenant');
  const priorityEl = document.getElementById('kanbanTaskModalPriority');
  const errEl = document.getElementById('kanbanTaskModalError');
  const submitBtn = document.getElementById('kanbanTaskModalSubmit');
  const title = titleEl ? titleEl.value.trim() : '';
  if (!title) {
    if (errEl) errEl.textContent = t('kanban_title_required') || 'Title is required.';
    if (titleEl) titleEl.focus();
    return;
  }
  // Build payload — for create we omit defaulted fields so the backend chooses;
  // for edit we send every field so users can clear assignee/tenant/body.
  const isEdit = _kanbanTaskModalMode === 'edit';
  const payload = {title};
  const bodyVal = bodyEl ? bodyEl.value : '';
  const assigneeVal = assigneeEl ? assigneeEl.value.trim() : '';
  const tenantVal = tenantEl ? tenantEl.value.trim() : '';
  const statusVal = statusEl ? statusEl.value : '';
  const priorityRaw = priorityEl ? priorityEl.value : '';
  if (isEdit) {
    payload.body = bodyVal;
    payload.assignee = assigneeVal || null;
    payload.tenant = tenantVal || null;
    // Only send status if the user actually changed the dropdown from the
    // value the modal opened with.  Otherwise editing a 'running'/'blocked'/
    // 'done'/'archived' task — whose real status maps to the dropdown's
    // 'triage' default — would silently demote the task on every save.
    if (statusVal && statusVal !== _kanbanTaskModalInitialDisplayedStatus) {
      payload.status = statusVal;
    }
    const n = parseInt(priorityRaw, 10);
    payload.priority = Number.isNaN(n) ? 0 : n;
  } else {
    if (bodyVal.trim()) payload.body = bodyVal;
    if (statusVal) payload.status = statusVal;
    if (assigneeVal) payload.assignee = assigneeVal;
    if (tenantVal) payload.tenant = tenantVal;
    if (priorityRaw !== '' && priorityRaw !== '0') {
      const n = parseInt(priorityRaw, 10);
      if (!Number.isNaN(n)) payload.priority = n;
    }
  }
  // Soft warning: a Ready task with the explicit "Unassigned" option will sit
  // forever because the dispatcher skips unassigned rows (kanban_db.py:3567).
  // The dropdown now makes this an explicit choice (the user picked "—
  // Unassigned (won't auto-run) —"), but we still surface a one-time confirm
  // so they don't lose work to a typo.
  if (statusVal === 'ready' && !assigneeVal) {
    if (errEl && !errEl.dataset.warningShown) {
      errEl.textContent = t('kanban_ready_needs_assignee')
        || 'You picked Unassigned + Ready. The dispatcher will skip this task. Submit again to confirm, or pick a profile.';
      errEl.dataset.warningShown = '1';
      const sel = document.getElementById('kanbanTaskModalAssignee');
      if (sel) sel.focus();
      return;
    }
  }
  if (submitBtn) submitBtn.disabled = true;
  if (errEl) { errEl.textContent = ''; delete errEl.dataset.warningShown; }
  try {
    let saved;
    if (isEdit && _kanbanTaskModalEditingId) {
      saved = await api(
        '/api/kanban/tasks/' + encodeURIComponent(_kanbanTaskModalEditingId) + _kanbanBoardQuery(),
        {method: 'PATCH', body: JSON.stringify(payload)},
      );
    } else {
      saved = await api('/api/kanban/tasks' + _kanbanBoardQuery(), {
        method: 'POST',
        body: JSON.stringify(payload),
      });
    }
    closeKanbanTaskModal();
    await loadKanban(true);
    const savedId = saved && saved.task && saved.task.id;
    if (savedId) {
      await loadKanbanTask(savedId);
    } else if (isEdit && _kanbanTaskModalEditingId) {
      await loadKanbanTask(_kanbanTaskModalEditingId);
    }
  } catch(e) {
    if (errEl) errEl.textContent = (e.message || String(e));
    if (submitBtn) submitBtn.disabled = false;
  }
}

async function updateKanbanTask(taskId, patch){
  if (!taskId || !patch) return;
  try {
    const updated = await api('/api/kanban/tasks/' + encodeURIComponent(taskId) + _kanbanBoardQuery(), {
      method: 'PATCH',
      body: JSON.stringify(patch),
    });
    await loadKanban(true);
    await loadKanbanTask((updated && updated.task && updated.task.id) || taskId);
  } catch(e) { showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error'); }
}

async function addKanbanComment(taskId){
  const input = document.getElementById('kanbanCommentInput');
  const body = input ? input.value.trim() : '';
  if (!taskId || !body) return;
  try {
    await api('/api/kanban/tasks/' + encodeURIComponent(taskId) + '/comments' + _kanbanBoardQuery(), {
      method: 'POST',
      body: JSON.stringify({body}),
    });
    if (input) input.value = '';
    await loadKanbanTask(taskId);
  } catch(e) { showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error'); }
}

function _kanbanRenderTaskDetail(data){
  const task = data.task || {};
  const log = data.log || {};
  const title = _kanbanTaskTitle(task);
  const body = _kanbanTaskBody(task) || t('kanban_no_description');
  const meta = _kanbanTaskMeta(task);
  const comments = data.comments || [];
  const events = data.events || [];
  const links = data.links || {};
  const runs = data.runs || [];
  // Note: 'running' is intentionally absent — entering 'running' is the
  // dispatcher/claim_task path's responsibility, not a user UI write. The
  // bridge rejects PATCH status='running' with HTTP 400 to match the agent
  // dashboard plugin's contract. UI users want to claim/promote a ready task
  // via the dispatcher Nudge button, not flip it to running by hand.
  const statusButtons = ['triage', 'todo', 'ready', 'blocked', 'done', 'archived'].map(status =>
    `<button class="btn secondary" onclick="updateKanbanTask('${esc(task.id)}',{status:'${status}'})">${esc(_kanbanColumnLabel(status))}</button>`
  ).join('') + `<button class="btn secondary" onclick="blockKanbanTask('${esc(task.id)}')">${esc(t('kanban_block'))}</button><button class="btn secondary" onclick="unblockKanbanTask('${esc(task.id)}')">${esc(t('kanban_unblock'))}</button>`;
  return `<div class="kanban-task-preview-header">
      <button class="btn secondary kanban-back-btn" onclick="closeKanbanTaskDetail()">${esc(t('kanban_back_to_board'))}</button>
      <div class="kanban-task-preview-title">${esc(title)}</div>
      <button class="btn secondary kanban-edit-btn" onclick="openKanbanEdit('${esc(task.id)}')" data-i18n="kanban_edit_task" title="${esc(t('kanban_edit_task') || 'Edit task')}">${esc(t('kanban_edit_task') || 'Edit task')}</button>
    </div>
    <div class="kanban-task-preview-body">${esc(body)}</div>
    ${meta.length ? `<div class="kanban-meta">${esc(meta.join(' · '))}</div>` : ''}
    <div class="kanban-status-actions">${statusButtons}</div>
    <div class="kanban-detail-grid">
      ${_kanbanDetailSection('kanban-detail-comments', String(t('kanban_comments_count')).replace('{0}', comments.length), comments.map(_kanbanCommentHtml).join(''), 'kanban_no_comments')}
      ${_kanbanDetailSection('kanban-detail-events', String(t('kanban_events_count')).replace('{0}', events.length), events.map(_kanbanEventHtml).join(''), 'kanban_no_events')}
      ${_kanbanDetailSection('kanban-detail-links', t('kanban_links'), _kanbanLinksHtml(links), 'kanban_empty')}
      ${_kanbanDetailSection('kanban-detail-runs', String(t('kanban_runs_count')).replace('{0}', runs.length), runs.map(_kanbanRunHtml).join(''), 'kanban_no_runs')}
      ${_kanbanDetailSection('kanban-detail-log', t('kanban_worker_log'), log.content ? `<pre class="kanban-detail-pre">${esc(log.content)}</pre>` : '', 'kanban_empty')}
    </div>
    <div class="kanban-comment-form">
      <textarea id="kanbanCommentInput" rows="2" placeholder="${esc(t('kanban_add_comment'))}"></textarea>
      <button class="btn primary" onclick="addKanbanComment('${esc(task.id)}')">${esc(t('kanban_add_comment'))}</button>
    </div>`;
}

function closeKanbanTaskDetail(){
  _kanbanCurrentTaskId = null;
  const detailPanel = $('kanbanDetailPanel');
  if (detailPanel) {
    const hint = typeof t === 'function' ? String(t('kanban_select_task_hint') || '') : '';
    const text = hint && hint !== 'kanban_select_task_hint' ? hint : 'Select a task card to view details.';
    detailPanel.innerHTML = `<div style="padding:24px;text-align:center;color:var(--muted);font-size:12px">${esc(text)}</div>`;
  }
  // Deselect all cards on board
  const board = $('kanbanBoard');
  if (board) {
    board.querySelectorAll('.kanban-card.selected').forEach(c => c.classList.remove('selected'));
  }
  // Hide banner fallback if visible
  const preview = $('kanbanTaskPreview');
  if (preview && preview.style.display !== 'none') {
    preview.style.display = 'none';
  }
}

async function loadKanbanTask(taskId){
  if (!taskId) return;
  try {
    const data = await api('/api/kanban/tasks/' + encodeURIComponent(taskId) + _kanbanBoardQuery());
    const logEndpoint = '/api/kanban/tasks/' + encodeURIComponent(taskId) + '/log' + _kanbanBoardQuery();
    try { data.log = await api(logEndpoint + '?tail=65536'); } catch(e) { data.log = {}; }
    _kanbanCurrentTaskId = taskId;
    const task = data.task || {};
    const title = _kanbanTaskTitle(task);
    const board = $('kanbanBoard');
    if (board) {
      board.querySelectorAll('.kanban-card').forEach(card => card.classList.remove('selected'));
      Array.from(board.querySelectorAll('.kanban-card')).find(card => card.dataset.kanbanTaskId === taskId)?.classList.add('selected');
    }
    
    // Render task detail directly in the Kanban main view for all viewports.
    // The workspace panel is intentionally not used in Kanban mode.
    const preview = $('kanbanTaskPreview');
    if (preview) {
      preview.style.display = '';
      preview.innerHTML = _kanbanRenderTaskDetail(data);
    }
    const detailPanel = $('kanbanDetailPanel');
    if (detailPanel) {
      const hint = typeof t === 'function' ? String(t('kanban_select_task_hint') || '') : '';
      const text = hint && hint !== 'kanban_select_task_hint' ? hint : 'Select a task card to view details.';
      detailPanel.innerHTML = `<div style="padding:24px;text-align:center;color:var(--muted);font-size:12px">${esc(text)}</div>`;
    }
    showToast(`${t('kanban_task')}: ${title}`);
  } catch(e) { showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error'); }
}

function _renderTodosMainBoard(todos) {
  const board = $('todoMainBoard');
  if (!board) return;
  const items = Array.isArray(todos) ? todos : [];
  if (!items.length) {
    board.innerHTML = `<div class="todos-empty-card">
      <div class="todos-empty-icon">${li('list-todo',24)}</div>
      <div class="todos-empty-title">${esc(t('todos_no_active'))}</div>
      <div class="todos-empty-subtitle">Todos appear here when the active chat or agent run emits a task list.</div>
    </div>`;
    return;
  }
  const groups = {
    pending: items.filter(t => (t.status || 'pending') === 'pending'),
    in_progress: items.filter(t => t.status === 'in_progress'),
    completed: items.filter(t => t.status === 'completed' || t.status === 'cancelled'),
  };
  const total = items.length;
  const done = groups.completed.length;
  const active = groups.in_progress.length;
  const pending = groups.pending.length;
  const column = (key, label, rows) => `<section class="todos-column ${esc(key)}">
    <div class="todos-column-head"><span>${esc(label)}</span><strong>${rows.length}</strong></div>
    <div class="todos-column-list">
      ${rows.length ? rows.map(todo => {
        const status = todo.status || 'pending';
        return `<article class="todo-main-card ${esc(status)}">
          <div class="todo-main-title">${esc(todo.content || todo.title || todo.id || '')}</div>
          <div class="todo-main-meta"><span>${esc(todo.id || '')}</span><span>${esc(status)}</span></div>
        </article>`;
      }).join('') : `<div class="todos-column-empty">No items</div>`}
    </div>
  </section>`;
  board.innerHTML = `
    <div class="todos-workspace-layout">
      <div class="todos-active-area">
        <div class="todos-overview-grid">
          <div class="todos-metric-card"><span>Total</span><strong>${total}</strong></div>
          <div class="todos-metric-card"><span>Active</span><strong>${active}</strong></div>
          <div class="todos-metric-card"><span>Done</span><strong>${done}</strong></div>
          <div class="todos-metric-card"><span>Pending</span><strong>${pending}</strong></div>
        </div>
        <div class="todos-columns">
          ${column('pending', 'Pending', groups.pending)}
          ${column('in_progress', 'In progress', groups.in_progress)}
        </div>
      </div>
      <aside class="todos-completed-panel" aria-label="Completed tasks">
        ${column('completed', 'Completed / cancelled', groups.completed)}
      </aside>
    </div>`;
}

function loadTodos() {
  const panel = $('todoPanel');
  const mainBoard = $('todoMainBoard');
  if (!panel && !mainBoard) return;
  const sourceMessages = (S.session && Array.isArray(S.session.messages) && S.session.messages.length) ? S.session.messages : S.messages;
  // Parse the most recent todo state from message history
  let todos = [];
  for (let i = sourceMessages.length - 1; i >= 0; i--) {
    const m = sourceMessages[i];
    if (m && m.role === 'tool') {
      try {
        const d = JSON.parse(typeof m.content === 'string' ? m.content : JSON.stringify(m.content));
        if (d && Array.isArray(d.todos) && d.todos.length) {
          todos = d.todos;
          break;
        }
      } catch(e) {}
    }
  }
  if (!todos.length) {
    if (panel) panel.innerHTML = `<div style="color:var(--muted);font-size:12px;padding:4px 0">${esc(t('todos_no_active'))}</div>`;
    _renderTodosMainBoard([]);
    return;
  }
  const statusIcon = {pending:li('square',14), in_progress:li('loader',14), completed:li('check',14), cancelled:li('x',14)};
  const statusColor = {pending:'var(--muted)', in_progress:'var(--blue)', completed:'rgba(100,200,100,.8)', cancelled:'rgba(200,100,100,.5)'};
  if (panel) panel.innerHTML = todos.map(t => `
    <div style="display:flex;align-items:flex-start;gap:10px;padding:6px 0;border-bottom:1px solid var(--border);">
      <span style="font-size:14px;display:inline-flex;align-items:center;flex-shrink:0;margin-top:1px;color:${statusColor[t.status]||'var(--muted)'}">${statusIcon[t.status]||li('square',14)}</span>
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;color:${t.status==='completed'?'var(--muted)':t.status==='in_progress'?'var(--text)':'var(--text)'};${t.status==='completed'?'text-decoration:line-through;opacity:.5':''};line-height:1.4">${esc(t.content)}</div>
        <div style="font-size:10px;color:var(--muted);margin-top:2px;opacity:.6">${esc(t.id)} · ${esc(t.status)}</div>
      </div>
    </div>`).join('');
  _renderTodosMainBoard(todos);
}

// ────────────────────────────────────────────────────────────────────────────
// Kanban: multi-board switcher + create/rename/archive modal
// ────────────────────────────────────────────────────────────────────────────
//
// The bridge exposes /api/kanban/boards (GET/POST), /boards/<slug>
// (PATCH/DELETE), and /boards/<slug>/switch (POST). The UI surfaces these
// as a "Default ▾" dropdown next to the Board title — clicking it opens
// a menu listing every board (current first, with task counts), plus
// actions to create / rename / archive.

const KANBAN_BOARD_LS_KEY = 'sidekick-kanban-active-board';

function _kanbanGetSavedBoard(){
  try { return localStorage.getItem(KANBAN_BOARD_LS_KEY) || null; } catch(_) { return null; }
}

function _kanbanSetSavedBoard(slug){
  try {
    if (slug && slug !== 'default') localStorage.setItem(KANBAN_BOARD_LS_KEY, slug);
    else localStorage.removeItem(KANBAN_BOARD_LS_KEY);
  } catch(_) {}
}

async function loadKanbanBoards(spaceLoadKey){
  const stillCurrentSpace = () => !spaceLoadKey || typeof isActiveSpaceLoadKey !== 'function' || isActiveSpaceLoadKey(spaceLoadKey);
  // Fetches the boards list and updates the switcher UI. Best-effort —
  // failures hide the switcher rather than blocking the panel from rendering.
  const switcher = document.getElementById('kanbanBoardSwitcher');
  if (!switcher) return;
  let data;
  try {
    data = await api('/api/kanban/boards' + _kanbanBoardQuery());
  } catch(e) {
    if (!stillCurrentSpace()) return false;
    // Hide switcher on error so the user isn't stuck with a half-broken UI.
    switcher.hidden = true;
    return;
  }
  if (!stillCurrentSpace()) return false;
  const boards = (data && data.boards) || [];
  const serverCurrent = (data && data.current) || 'default';
  const currentSource = (data && data.current_source) || 'explicit';
  _kanbanBoardsList = boards;
  // Resolution chain for the active board:
  //   server's `current` → localStorage hint only when the API reports a
  //   fallback current (stale/archived pointer fell back to default).
  // The on-disk/current-board pointer stays the source of truth; the
  // browser cache only keeps the last viewed board alive when the server
  // could not preserve an explicit non-default pointer.
  const saved = _kanbanGetSavedBoard();
  const savedExists = !!(saved && boards.some(b => b.slug === saved));
  let active = serverCurrent;
  if (currentSource === 'fallback' && savedExists) {
    active = saved;
  }
  if (active === 'default') {
    if (saved) _kanbanSetSavedBoard('default');
  } else if (saved !== active) {
    _kanbanSetSavedBoard(active);
  }
  _kanbanCurrentBoard = (active === 'default') ? null : active;
  // The switcher is visible whenever ≥1 non-default board exists OR the
  // current board is non-default. (If you only have 'default', a switcher
  // adds clutter without value.)
  const hasMultiple = boards.length > 1 || (active !== 'default');
  switcher.hidden = !hasMultiple;
  if (!hasMultiple) return;
  // Update the toggle label/icon
  const activeMeta = boards.find(b => b.slug === active) || {slug: active, name: active, icon: '', color: ''};
  const nameEl = document.getElementById('kanbanBoardSwitcherName');
  const iconEl = document.getElementById('kanbanBoardSwitcherIcon');
  if (nameEl) nameEl.textContent = activeMeta.name || activeMeta.slug || 'Default';
  if (iconEl) {
    iconEl.textContent = activeMeta.icon || '';
    if (activeMeta.color) iconEl.style.color = activeMeta.color;
    else iconEl.style.color = '';
  }
  // Re-render the menu (in case it was open or changed)
  _renderKanbanBoardMenu(boards, active);
}

// Restrict board.color to CSS hex codes or simple named colors before
// interpolating into a `style=""` attribute. esc() HTML-escapes but
// does not block CSS-context injection (`color:red;background:url(...)`
// would otherwise exfiltrate page state via an attacker-controlled URL,
// since neither this bridge nor the agent's kanban_db validates color).
function _kanbanSafeColor(c){
  if (typeof c !== 'string') return '';
  const s = c.trim();
  if (!s) return '';
  if (/^#[0-9a-fA-F]{3,8}$/.test(s)) return s;
  if (/^[a-zA-Z]{3,32}$/.test(s)) return s;
  return '';
}

function _renderKanbanBoardMenu(boards, current){
  const menu = document.getElementById('kanbanBoardSwitcherMenu');
  if (!menu) return;
  const items = boards.map(b => {
    const isCurrent = b.slug === current;
    const total = (b.total != null) ? b.total : (b.counts ? Object.values(b.counts).reduce((a,c)=>a+Number(c||0),0) : 0);
    const icon = b.icon ? esc(b.icon) : '';
    const safeColor = _kanbanSafeColor(b.color);
    const colorStyle = safeColor ? `color:${safeColor}` : '';
    return `<button type="button" class="kanban-board-switcher-item ${isCurrent ? 'is-current' : ''}" role="menuitem" data-board-slug="${esc(b.slug)}" onclick="switchKanbanBoard('${esc(b.slug)}')">
      <span class="kanban-board-switcher-item-icon" style="${colorStyle}">${icon || (isCurrent ? '✓' : '')}</span>
      <span class="kanban-board-switcher-item-name">${esc(b.name || b.slug)}</span>
      <span class="kanban-board-switcher-item-count">${esc(String(total))}</span>
    </button>`;
  }).join('');
  // Actions row — disable rename/archive when the only option is `default`
  // (the default board's display metadata is editable but the slug isn't,
  // and `default` cannot be archived).
  const renameDisabled = current === 'default';
  const archiveDisabled = current === 'default';
  const actions = `
    <div class="kanban-board-switcher-divider" role="separator"></div>
    <button type="button" class="kanban-board-switcher-action" onclick="openKanbanCreateBoard()" data-i18n="kanban_new_board">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
      <span>${esc(t('kanban_new_board') || 'New board…')}</span>
    </button>
    <button type="button" class="kanban-board-switcher-action" onclick="openKanbanRenameBoard()" ${renameDisabled ? 'disabled' : ''} data-i18n="kanban_rename_board">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
      <span>${esc(t('kanban_rename_board') || 'Rename current board…')}</span>
    </button>
    <button type="button" class="kanban-board-switcher-action danger" onclick="archiveKanbanBoard()" ${archiveDisabled ? 'disabled' : ''} data-i18n="kanban_archive_board">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/></svg>
      <span>${esc(t('kanban_archive_board') || 'Archive current board…')}</span>
    </button>
  `;
  menu.innerHTML = items + actions;
}

function toggleKanbanBoardMenu(ev){
  if (ev) ev.stopPropagation();
  const menu = document.getElementById('kanbanBoardSwitcherMenu');
  const toggle = document.getElementById('kanbanBoardSwitcherToggle');
  if (!menu || !toggle) return;
  _kanbanBoardMenuOpen = !_kanbanBoardMenuOpen;
  menu.hidden = !_kanbanBoardMenuOpen;
  toggle.setAttribute('aria-expanded', String(_kanbanBoardMenuOpen));
  if (_kanbanBoardMenuOpen) {
    // Click-away close
    setTimeout(() => {
      document.addEventListener('click', _kanbanCloseBoardMenuOnOutside, {once: true, capture: true});
    }, 0);
  }
}

function _kanbanCloseBoardMenuOnOutside(ev){
  const switcher = document.getElementById('kanbanBoardSwitcher');
  if (!switcher || !switcher.contains(ev.target)) {
    _kanbanBoardMenuOpen = false;
    const menu = document.getElementById('kanbanBoardSwitcherMenu');
    const toggle = document.getElementById('kanbanBoardSwitcherToggle');
    if (menu) menu.hidden = true;
    if (toggle) toggle.setAttribute('aria-expanded', 'false');
  } else {
    // Re-arm the listener — the user clicked inside the switcher, possibly
    // the toggle button which we want to handle through its own onclick.
    setTimeout(() => {
      document.addEventListener('click', _kanbanCloseBoardMenuOnOutside, {once: true, capture: true});
    }, 0);
  }
}

async function switchKanbanBoard(slug){
  if (!slug) return;
  const newBoard = (slug === 'default') ? null : slug;
  if (newBoard === _kanbanCurrentBoard) {
    // No-op switch — just close the menu.
    _kanbanBoardMenuOpen = false;
    const menu = document.getElementById('kanbanBoardSwitcherMenu');
    if (menu) menu.hidden = true;
    return;
  }
  _kanbanBoardMenuOpen = false;
  const menu = document.getElementById('kanbanBoardSwitcherMenu');
  if (menu) menu.hidden = true;
  // Tell the server too (sets the on-disk active-board pointer for CLI/dashboard).
  try {
    await api('/api/kanban/boards/' + encodeURIComponent(slug) + '/switch' + _kanbanBoardQuery(), {method: 'POST'});
  } catch(e) {
    // Keep the current board pinned to the server's last confirmed state.
    // A failed switch should not make the UI render a board that the shared
    // on-disk pointer never accepted.
    await loadKanbanBoards();
    showToast((t('kanban_unavailable') || 'Kanban unavailable') + ': ' + (e.message || e), 'error');
    return;
  }
  _kanbanCurrentBoard = newBoard;
  _kanbanSetSavedBoard(slug);
  _kanbanLatestEventId = 0;  // reset cursor — new board has its own event sequence
  // Re-open the SSE stream on the new board.
  _kanbanStopPolling();
  await loadKanban(true);
  await loadKanbanBoards();
  _kanbanEnsurePollingActive();
}

// ── Create / rename / archive board modals ──────────────────────────────────

function openKanbanCreateBoard(){
  const modal = document.getElementById('kanbanBoardModal');
  if (!modal) return;
  document.getElementById('kanbanBoardModalMode').value = 'create';
  document.getElementById('kanbanBoardModalSlug').value = '';
  document.getElementById('kanbanBoardModalTitle').textContent = t('kanban_new_board') || 'New board';
  document.getElementById('kanbanBoardModalName').value = '';
  document.getElementById('kanbanBoardModalSlugInput').value = '';
  document.getElementById('kanbanBoardModalSlugInput').disabled = false;
  document.getElementById('kanbanBoardModalSlugRow').style.display = '';
  document.getElementById('kanbanBoardModalDesc').value = '';
  document.getElementById('kanbanBoardModalIcon').value = '';
  document.getElementById('kanbanBoardModalColor').value = '#7aa2ff';
  document.getElementById('kanbanBoardModalError').textContent = '';
  modal.hidden = false;
  if (_kanbanBoardModalFocusCleanup) {
    _kanbanBoardModalFocusCleanup();
    _kanbanBoardModalFocusCleanup = null;
  }
  _kanbanBoardModalFocusCleanup = _trapModalFocus(modal);
  // Auto-focus name field
  setTimeout(() => document.getElementById('kanbanBoardModalName').focus(), 50);
  // Auto-suggest slug from name as user types
  const nameEl = document.getElementById('kanbanBoardModalName');
  const slugEl = document.getElementById('kanbanBoardModalSlugInput');
  let userEditedSlug = false;
  slugEl.addEventListener('input', () => { userEditedSlug = true; }, {once: false});
  const onName = () => {
    if (!userEditedSlug) {
      slugEl.value = String(nameEl.value || '').toLowerCase().replace(/[^a-z0-9-_ ]+/g, '').replace(/\s+/g, '-').slice(0, 48);
    }
  };
  nameEl.removeEventListener('input', nameEl._kanbanOnNameInput || (() => {}));
  nameEl._kanbanOnNameInput = onName;
  nameEl.addEventListener('input', onName);
  // Close on Escape
  document.addEventListener('keydown', _kanbanBoardModalEsc);
}

function openKanbanRenameBoard(){
  const modal = document.getElementById('kanbanBoardModal');
  if (!modal) return;
  const current = _kanbanCurrentBoard || 'default';
  if (current === 'default') return;  // default's slug is immutable
  const meta = (_kanbanBoardsList || []).find(b => b.slug === current);
  if (!meta) return;
  document.getElementById('kanbanBoardModalMode').value = 'rename';
  document.getElementById('kanbanBoardModalSlug').value = current;
  document.getElementById('kanbanBoardModalTitle').textContent = t('kanban_rename_board') || 'Rename board';
  document.getElementById('kanbanBoardModalName').value = meta.name || '';
  document.getElementById('kanbanBoardModalSlugInput').value = current;
  document.getElementById('kanbanBoardModalSlugInput').disabled = true;  // slug is immutable
  // Hide the slug row — it's locked, less visual noise.
  document.getElementById('kanbanBoardModalSlugRow').style.display = 'none';
  document.getElementById('kanbanBoardModalDesc').value = meta.description || '';
  document.getElementById('kanbanBoardModalIcon').value = meta.icon || '';
  document.getElementById('kanbanBoardModalColor').value = meta.color || '#7aa2ff';
  document.getElementById('kanbanBoardModalError').textContent = '';
  modal.hidden = false;
  if (_kanbanBoardModalFocusCleanup) {
    _kanbanBoardModalFocusCleanup();
    _kanbanBoardModalFocusCleanup = null;
  }
  _kanbanBoardModalFocusCleanup = _trapModalFocus(modal);
  setTimeout(() => document.getElementById('kanbanBoardModalName').focus(), 50);
  document.addEventListener('keydown', _kanbanBoardModalEsc);
}

function _kanbanBoardModalEsc(ev){
  if (ev.key === 'Escape') closeKanbanBoardModal();
}

function closeKanbanBoardModal(){
  const modal = document.getElementById('kanbanBoardModal');
  if (modal) modal.hidden = true;
  if (_kanbanBoardModalFocusCleanup) {
    _kanbanBoardModalFocusCleanup();
    _kanbanBoardModalFocusCleanup = null;
  }
  document.removeEventListener('keydown', _kanbanBoardModalEsc);
}

async function submitKanbanBoardModal(){
  const errEl = document.getElementById('kanbanBoardModalError');
  errEl.textContent = '';
  const mode = document.getElementById('kanbanBoardModalMode').value;
  const name = (document.getElementById('kanbanBoardModalName').value || '').trim();
  const slugInput = (document.getElementById('kanbanBoardModalSlugInput').value || '').trim();
  const description = (document.getElementById('kanbanBoardModalDesc').value || '').trim();
  const icon = (document.getElementById('kanbanBoardModalIcon').value || '').trim();
  const color = (document.getElementById('kanbanBoardModalColor').value || '').trim();
  const submitBtn = document.getElementById('kanbanBoardModalSubmit');
  if (!name) {
    errEl.textContent = t('kanban_board_name_required') || 'Name is required';
    return;
  }
  if (mode === 'create') {
    if (!slugInput) {
      errEl.textContent = t('kanban_board_slug_required') || 'Slug is required';
      return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
      const res = await api('/api/kanban/boards' + _kanbanBoardQuery(), {
        method: 'POST',
        body: JSON.stringify({slug: slugInput, name, description, icon, color, switch: true}),
      });
      closeKanbanBoardModal();
      // Switch to the new board and reload
      const newSlug = (res && res.board && res.board.slug) || slugInput;
      _kanbanCurrentBoard = (newSlug === 'default') ? null : newSlug;
      _kanbanSetSavedBoard(newSlug);
      _kanbanLatestEventId = 0;
      _kanbanStopPolling();
      await loadKanban(true);
      await loadKanbanBoards();
      _kanbanEnsurePollingActive();
    } catch(e) {
      errEl.textContent = (e && (e.message || e.error)) || String(e);
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  } else if (mode === 'rename') {
    const slug = document.getElementById('kanbanBoardModalSlug').value;
    if (!slug) { errEl.textContent = 'Missing slug'; return; }
    if (submitBtn) submitBtn.disabled = true;
    try {
      await api('/api/kanban/boards/' + encodeURIComponent(slug) + _kanbanBoardQuery(), {
        method: 'PATCH',
        body: JSON.stringify({name, description, icon, color}),
      });
      closeKanbanBoardModal();
      await loadKanbanBoards();  // refresh switcher label/icon
    } catch(e) {
      errEl.textContent = (e && (e.message || e.error)) || String(e);
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  }
}

async function archiveKanbanBoard(){
  const current = _kanbanCurrentBoard || 'default';
  if (current === 'default') return;
  const meta = (_kanbanBoardsList || []).find(b => b.slug === current);
  const label = meta && meta.name ? meta.name : current;
  const ok = await showConfirmDialog({
    title: t('kanban_archive_board') || 'Archive board',
    message: (t('kanban_archive_board_confirm') || 'Archive board "{name}"? Tasks remain on disk and the board can be restored from kanban/boards/_archived/.').replace('{name}', label),
    confirmLabel: t('kanban_archive_board') || 'Archive',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  // CRITICAL: stop the SSE stream BEFORE the archive call. The library's
  // kb.connect(board=<slug>) auto-creates the on-disk directory + DB on
  // first call — so any in-flight stream that polls task_events while
  // we're archiving will silently re-materialise the directory we just
  // moved to _archived/. Tearing down the stream first avoids that race.
  _kanbanStopPolling();
  try {
    await api('/api/kanban/boards/' + encodeURIComponent(current) + _kanbanBoardQuery(), {method: 'DELETE'});
    // Server falls back to default — match that locally.
    _kanbanCurrentBoard = null;
    _kanbanSetSavedBoard('default');
    _kanbanLatestEventId = 0;
    await loadKanban(true);
    await loadKanbanBoards();
    _kanbanEnsurePollingActive();
    showToast(t('kanban_board_archived') || 'Board archived');
  } catch(e) {
    // Restart the stream on failure so the UI doesn't go stale.
    _kanbanStartPolling();
    showToast(t('kanban_unavailable') + ': ' + (e.message || e), 'error');
  }
}


// ── Logs panel ──
function _selectedLogsFile() {
  const el = $('logsFile');
  const value = (el && el.value) || 'agent';
  return ['agent','errors','gateway'].includes(value) ? value : 'agent';
}

function _selectedLogsTail() {
  const el = $('logsTail');
  const value = Number((el && el.value) || 200);
  return [100,200,500,1000].includes(value) ? value : 200;
}

function _severityForLine(line) {
  const text = String(line || '').toUpperCase();
  if (/\b(ERROR|CRITICAL|TRACEBACK)\b/.test(text)) return 'error';
  if (/\b(WARNING|WARN)\b/.test(text)) return 'warning';
  if (/\b(DEBUG)\b/.test(text)) return 'debug';
  if (/\b(INFO)\b/.test(text)) return 'info';
  return 'other';
}

function _filteredLogsLines() {
  if (_logsSeverityFilter === 'all') return _lastLogsLines;
  return _lastLogsLines.filter(line => {
    const sev = _severityForLine(line);
    if (_logsSeverityFilter === 'errors') return sev === 'error';
    if (_logsSeverityFilter === 'warnings') return sev === 'warning' || sev === 'error';
    return true;
  });
}

function _applyLogsSeverityFilter() {
  const el = $('logsSeverityFilter');
  _logsSeverityFilter = (el && el.value) || 'all';
  // Re-render from cached lines without re-fetching
  _renderLogs({ lines: _lastLogsLines, hint: '', truncated: false, _fromFilter: true });
}

function _logLineSeverityClass(line) {
  const text = String(line || '').toUpperCase();
  if (/\b(WARNING|WARN)\b/.test(text)) return 'log-line-warning';
  if (/\b(DEBUG)\b/.test(text)) return 'log-line-debug';
  if (/\b(INFO)\b/.test(text)) return 'log-line-info';
  if (/\b(ERROR|CRITICAL|TRACEBACK)\b/.test(text)) return 'log-line-error';
  return '';
}

function _syncLogsWrap() {
  const out = $('logsOutput');
  const wrap = $('logsWrap');
  if (out && wrap) out.classList.toggle('wrap', !!wrap.checked);
}

async function loadLogs(animate) {
  const box = $('logsOutput');
  const status = $('logsStatus');
  const refreshBtn = $('logsRefreshBtn');
  if (!box) return;
  if (animate && refreshBtn) {
    refreshBtn.style.opacity = '0.5';
    refreshBtn.disabled = true;
  }
  const file = _selectedLogsFile();
  const tail = _selectedLogsTail();
  try {
    if (status) status.textContent = t('logs_loading');
    const data = await api('/api/logs?file=' + encodeURIComponent(file) + '&tail=' + encodeURIComponent(tail));
    _renderLogs(data);
  } catch(e) {
    _lastLogsLines = [];
    box.innerHTML = `<div class="logs-empty">${esc(t('error_prefix') + e.message)}</div>`;
    if (status) status.textContent = t('logs_load_failed');
  } finally {
    if (animate && refreshBtn) {
      refreshBtn.style.opacity = '';
      refreshBtn.disabled = false;
    }
    _syncLogsAutoRefresh();
  }
}

function _renderLogs(data) {
  const box = $('logsOutput');
  const status = $('logsStatus');
  if (!box) return;
  const rawLines = Array.isArray(data && data.lines) ? data.lines : [];
  // Only update cache when loading fresh data (not when re-rendering from filter)
  if (data && !data._fromFilter) _lastLogsLines = rawLines.slice();
  const displayLines = _filteredLogsLines();
  const hint = data && data.hint ? `<div class="logs-hint">${esc(data.hint)}</div>` : '';
  const truncated = data && data.truncated ? `<div class="logs-hint warn">${esc(t('logs_truncated_hint'))}</div>` : '';
  const filterNote = _logsSeverityFilter !== 'all'
    ? `<div class="logs-hint">${esc(displayLines.length + ' / ' + _lastLogsLines.length + ' ' + t('logs_filter_active'))}</div>`
    : '';
  if (!displayLines.length) {
    box.innerHTML = `${hint}${truncated}${filterNote}<div class="logs-empty">${esc(t('logs_empty'))}</div>`;
  } else {
    box.innerHTML = `${hint}${truncated}${filterNote}` + displayLines.map(line => {
      const cls = _logLineSeverityClass(line);
      return `<div class="log-line ${cls}">${esc(line)}</div>`;
    }).join('');
  }
  _syncLogsWrap();
  if (status) {
    const bytes = data && Number(data.total_bytes || 0);
    const when = data && data.mtime ? new Date(data.mtime * 1000).toLocaleString() : t('logs_no_mtime');
    status.textContent = `${rawLines.length} / ${data.tail || _selectedLogsTail()} lines · ${bytes.toLocaleString()} bytes · ${when}`;
  }
}

function _startLogsAutoRefresh() {
  if (_logsAutoRefreshTimer) return;
  _logsAutoRefreshTimer = setInterval(() => {
    if (_currentPanel !== 'logs') { _stopLogsAutoRefresh(); return; }
    const toggle = $('logsAutoRefresh');
    if (toggle && !toggle.checked) return;
    loadLogs(false);
  }, 5000);
}

function _stopLogsAutoRefresh() {
  if (_logsAutoRefreshTimer) {
    clearInterval(_logsAutoRefreshTimer);
    _logsAutoRefreshTimer = null;
  }
}

function _syncLogsAutoRefresh() {
  const toggle = $('logsAutoRefresh');
  if (_currentPanel === 'logs' && (!toggle || toggle.checked)) _startLogsAutoRefresh();
  else _stopLogsAutoRefresh();
}

async function copyLogsAll() {
  const lines = _filteredLogsLines();
  const text = lines.join('\n');
  try {
    await _copyText(text);
    showToast(t('logs_copied'));
  } catch(e) {
    showToast(t('copy_failed'), 'error');
  }
}

// ── Insights panel ──
async function loadInsights(animate) {
  const box = $('insightsContent');
  const refreshBtn = $('insightsRefreshBtn');
  if (!box) return;
  if (animate && refreshBtn) {
    refreshBtn.style.opacity = '0.5';
    refreshBtn.disabled = true;
  }
  const period = ($('insightsPeriod') || {}).value || '30';
  try {
    const [data, wikiStatus] = await Promise.all([
      api(`/api/insights?days=${period}`),
      api('/api/wiki/status').catch(err => ({status:'error', error: err.message || String(err)})),
    ]);
    _renderInsights(data, box, wikiStatus);
    if (typeof _syncSystemHealthMonitorVisibility === 'function') _syncSystemHealthMonitorVisibility();
    if (typeof pollSystemHealth === 'function') void pollSystemHealth();
  } catch(e) {
    box.innerHTML = `<div style="color:var(--accent);font-size:12px">${esc(t('error_prefix') + e.message)}</div>`;
  } finally {
    if (animate && refreshBtn) {
      refreshBtn.style.opacity = '';
      refreshBtn.disabled = false;
    }
  }
}

function _formatLlmWikiTimestamp(value) {
  if (!value) return 'Never';
  try { return new Date(value).toLocaleString(); }
  catch (_) { return String(value); }
}

function _renderSystemHealthPanel() {
  return `
    <section class="insights-card system-health-panel loading" id="systemHealthPanel" aria-label="Host resource health" aria-live="polite">
      <div class="system-health-head">
        <div>
          <div class="insights-card-title">System health</div>
          <div class="system-health-sub">Current VPS resource usage</div>
        </div>
        <span class="system-health-status" id="systemHealthStatus"><span class="system-health-dot" aria-hidden="true"></span>Loading…</span>
      </div>
      <div class="system-health-metrics">
        <div class="system-health-metric" data-system-health-metric="cpu">
          <div class="system-health-label"><span>CPU</span><span class="system-health-value" data-system-health-value>—</span></div>
          <div class="system-health-bar" role="progressbar" aria-label="CPU usage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="system-health-bar-fill"></div></div>
        </div>
        <div class="system-health-metric" data-system-health-metric="memory">
          <div class="system-health-label"><span>RAM</span><span class="system-health-value" data-system-health-value>—</span></div>
          <div class="system-health-bar" role="progressbar" aria-label="RAM usage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="system-health-bar-fill"></div></div>
        </div>
        <div class="system-health-metric" data-system-health-metric="disk">
          <div class="system-health-label"><span>Disk</span><span class="system-health-value" data-system-health-value>—</span></div>
          <div class="system-health-bar" role="progressbar" aria-label="Disk usage" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><div class="system-health-bar-fill"></div></div>
        </div>
      </div>
      <div class="system-health-foot">Live snapshot only; historical resource charts can build on this surface later.</div>
    </section>`;
}

function _renderLlmWikiStatus(d) {
  const status = d || {status:'error'};
  const isReady = status.available && status.status === 'ready';
  const isEmpty = status.available && status.status === 'empty';
  const isError = status.status === 'error';
  const badgeClass = isReady ? 'ok' : isError ? 'err' : isEmpty ? 'warn' : 'muted';
  const badgeText = isReady ? 'Available' : isError ? 'Error' : isEmpty ? 'Empty' : 'Unavailable';
  const rawDocsUrl = status.docs_url || 'https://lastbrowser.com/sidekick/skills/bundled/research/research-llm-wiki';
  // Guard against unsafe URL schemes (e.g. js: / data:) if docs_url ever
  // becomes config-driven. esc() HTML-escapes but doesn't validate URL scheme.
  const docsUrl = /^https?:\/\//i.test(rawDocsUrl) ? rawDocsUrl : '#';
  const toggleNote = status.toggle_available
    ? 'Toggle available from configured Nova setting.'
    : (status.toggle_reason || 'No stable LLM Wiki on/off config flag was detected, so this panel is read-only.');
  const statusNote = isReady
    ? 'LLM Wiki is configured and page metadata is visible without exposing wiki content.'
    : isEmpty
      ? 'LLM Wiki exists but has no entity, concept, comparison, or query pages yet.'
      : isError
        ? `Unable to inspect LLM Wiki status${status.error ? ': ' + status.error : ''}.`
        : 'No LLM Wiki directory was found. Set WIKI_PATH or skills.config.wiki.path to enable status visibility.';
  return `
    <div class="insights-card wiki-status-card" id="llmWikiStatusCard">
      <div class="wiki-status-head">
        <div>
          <div class="insights-card-title">LLM Wiki</div>
          <div class="wiki-status-sub">Knowledge-base observability</div>
        </div>
        <span class="wiki-status-badge ${badgeClass}">${esc(badgeText)}</span>
      </div>
      <div class="wiki-status-note">${esc(statusNote)}</div>
      <div class="wiki-status-grid">
        <div><span>Enabled</span><strong>${status.enabled ? 'Yes' : 'No'}</strong></div>
        <div><span>Entries</span><strong>${Number(status.entry_count || 0).toLocaleString()}</strong></div>
        <div><span>Pages</span><strong>${Number(status.page_count || 0).toLocaleString()}</strong></div>
        <div><span>raw/ files</span><strong>${Number(status.raw_source_count || 0).toLocaleString()}</strong></div>
        <div><span>Last updated</span><strong>${esc(_formatLlmWikiTimestamp(status.last_updated))}</strong></div>
        <div><span>Last writer</span><strong>${esc(status.last_writer || 'Not available')}</strong></div>
      </div>
      <div class="wiki-status-footer">
        <span>${esc(toggleNote)}</span>
        <a href="${esc(docsUrl)}" target="_blank" rel="noopener noreferrer">Docs</a>
      </div>
    </div>`;
}

function _renderInsights(d, box, wikiStatus) {
  const fmtNum = n => Number(n || 0).toLocaleString();
  const fmtCost = c => {
    const value = Number(c || 0);
    return value > 0 ? '$' + value.toFixed(value < 1 ? 4 : 2) : t('insights_no_cost');
  };
  const fmtTokens = n => {
    const value = Number(n || 0);
    return value >= 1e6 ? (value/1e6).toFixed(1) + 'M' : value >= 1e3 ? (value/1e3).toFixed(1) + 'K' : fmtNum(value);
  };
  const safe = v => Number(v || 0);

  // ── Derived metrics ──
  const avgTokensPerSession = safe(d.total_tokens) && safe(d.total_sessions)
    ? fmtTokens(Math.round(safe(d.total_tokens) / safe(d.total_sessions))) : '—';
  const avgMessagesPerSession = safe(d.total_messages) && safe(d.total_sessions)
    ? (safe(d.total_messages) / safe(d.total_sessions)).toFixed(1) : '—';

  // Top model by cost or tokens
  let topModelName = '—', topModelCost = 0;
  if (Array.isArray(d.models) && d.models.length) {
    const sorted = [...d.models].sort((a, b) => safe(b.cost) - safe(a.cost));
    topModelName = sorted[0].model;
    topModelCost = sorted[0].cost || 0;
  }

  // Peak day from daily_tokens
  let peakDay = '—', peakDayTokens = 0;
  if (Array.isArray(d.daily_tokens) && d.daily_tokens.length) {
    const pk = d.daily_tokens.reduce((a, b) => (safe(b.input_tokens) + safe(b.output_tokens)) > (safe(a.input_tokens) + safe(a.output_tokens)) ? b : a, d.daily_tokens[0]);
    peakDay = pk.date || '—';
    peakDayTokens = safe(pk.input_tokens) + safe(pk.output_tokens);
  }

  // Cost coverage heuristic
  const totalEst = safe(d.total_estimated_cost);
  const totalAct = safe(d.total_actual_cost);
  const costCoverage = totalAct && totalEst ? Math.round((totalAct / totalEst) * 100) + '% matched' : totalAct ? 'Actual' : totalEst ? 'Estimated only' : 'N/A';

  // Data source label
  const sourceLabel = d.data_source === 'state_db' ? 'Analytics DB' : d.data_source === 'index_json_fallback' ? 'Session Index' : d.data_source === 'empty' ? 'Empty' : d.data_source || 'Unknown';
  const sourceColor = d.data_source === 'state_db' ? 'ok' : 'warn';

  // ── KPI Row (Main) ──
  const kpiRow = `<div class="insights-kpi-row">
    <div class="insights-kpi-card"><div class="insights-kpi-value">${fmtNum(d.total_sessions)}</div><div class="insights-kpi-label">${esc(t('insights_sessions'))}</div></div>
    <div class="insights-kpi-card"><div class="insights-kpi-value">${fmtNum(d.total_messages)}</div><div class="insights-kpi-label">${esc(t('insights_messages'))}</div></div>
    <div class="insights-kpi-card"><div class="insights-kpi-value">${fmtTokens(d.total_tokens)}</div><div class="insights-kpi-label">${esc(t('insights_tokens'))}</div></div>
    <div class="insights-kpi-card"><div class="insights-kpi-value">${fmtCost(d.total_cost)}</div><div class="insights-kpi-label">${esc(t('insights_cost'))}</div></div>
    <div class="insights-kpi-card"><div class="insights-kpi-value">${avgTokensPerSession}</div><div class="insights-kpi-label">Avg tokens/session</div></div>
  </div>`;

  // ── Daily token trend (Main) ──
  const dailyTokens = Array.isArray(d.daily_tokens) ? d.daily_tokens : [];
  let dailyHtml = '';
  if (dailyTokens.length) {
    const maxDailyTokens = Math.max(...dailyTokens.map(r => Number(r.input_tokens || 0) + Number(r.output_tokens || 0)), 1);
    const labelEvery = Math.max(Math.ceil(dailyTokens.length / 7), 1);
    dailyHtml = `<div class="insights-card"><div class="insights-card-title">${esc(t('insights_daily_tokens'))}</div><div class="insights-daily-token-chart">` +
      dailyTokens.map((r, idx) => {
        const input = Number(r.input_tokens || 0);
        const output = Number(r.output_tokens || 0);
        const inputPct = Math.max((input / maxDailyTokens) * 100, input ? 2 : 0).toFixed(1);
        const outputPct = Math.max((output / maxDailyTokens) * 100, output ? 2 : 0).toFixed(1);
        const showLabel = idx === 0 || idx === dailyTokens.length - 1 || idx % labelEvery === 0;
        const title = `${r.date} · ${fmtTokens(input)} ${t('insights_input_tokens')} · ${fmtTokens(output)} ${t('insights_output_tokens')} · ${fmtCost(r.cost)} · ${fmtNum(r.sessions)} ${t('insights_sessions')}`;
        return `<div class="insights-daily-bar" title="${esc(title)}"><div class="insights-daily-stack" aria-label="${esc(title)}"><div class="insights-daily-bar-output" style="height:${outputPct}%"></div><div class="insights-daily-bar-input" style="height:${inputPct}%"></div></div><span>${showLabel ? esc(String(r.date).slice(5)) : ''}</span></div>`;
      }).join('') +
      `</div><div class="insights-daily-legend"><span><i class="insights-daily-legend-input"></i>${esc(t('insights_input_tokens'))}</span><span><i class="insights-daily-legend-output"></i>${esc(t('insights_output_tokens'))}</span></div></div>`;
  } else {
    dailyHtml = `<div class="insights-card"><div class="insights-card-title">${esc(t('insights_daily_tokens'))}</div><div class="insights-empty">${esc(t('insights_no_usage_data'))}</div></div>`;
  }

  // ── Models table (Main grid left) ──
  let modelsHtml = '';
  if (d.models && d.models.length) {
    modelsHtml = `<div class="insights-card"><div class="insights-card-title">${esc(t('insights_models'))}</div><div class="insights-table insights-model-table"><div class="insights-table-head"><span>${esc(t('insights_model_name'))}</span><span>${esc(t('insights_model_sessions'))}</span><span>${esc(t('insights_model_tokens'))}</span><span>${esc(t('insights_model_cost'))}</span><span>${esc(t('insights_model_share'))}</span></div>` +
      d.models.map(m => {
        const share = Number(m.cost_share || m.token_share || m.session_share || 0);
        const title = `${m.model} · ${fmtTokens(m.input_tokens)} ${t('insights_input_tokens')} · ${fmtTokens(m.output_tokens)} ${t('insights_output_tokens')}`;
        return `<div class="insights-table-row"><span class="insights-model-name" title="${esc(m.model)}">${esc(m.model)}</span><span>${fmtNum(m.sessions)}</span><span class="insights-model-tokens" title="${esc(title)}">${fmtTokens(m.total_tokens || 0)}</span><span class="insights-model-cost">${fmtCost(m.cost)}</span><span>${share}%</span></div>`;
      }).join('') +
      `</div></div>`;
  } else {
    modelsHtml = `<div class="insights-card"><div class="insights-card-title">${esc(t('insights_models'))}</div><div class="insights-empty">${esc(t('insights_no_usage_data'))}</div></div>`;
  }

  // ── Activity side-by-side (Main grid right) ──
  // Activity by day of week
  let dowHtml = '';
  if (d.activity_by_day) {
    const maxDow = Math.max(...d.activity_by_day.map(x => x.sessions), 1);
    dowHtml = `<div class="insights-card" style="margin-bottom:10px"><div class="insights-card-title">${esc(t('insights_activity_by_day'))}</div><div class="insights-bars">` +
      d.activity_by_day.map(r => {
        const pct = (r.sessions / maxDow * 100).toFixed(0);
        return `<div class="insights-bar-row"><span class="insights-bar-label">${r.day}</span><div class="insights-bar-track"><div class="insights-bar-fill" style="width:${pct}%"></div></div><span class="insights-bar-value">${r.sessions}</span></div>`;
      }).join('') +
      `</div></div>`;
  }
  // Activity by hour
  let hodHtml = '';
  if (d.activity_by_hour) {
    const maxHod = Math.max(...d.activity_by_hour.map(x => x.sessions), 1);
    const peakHour = d.activity_by_hour.reduce((a, b) => b.sessions > a.sessions ? b : a, {hour:0,sessions:0});
    hodHtml = `<div class="insights-card"><div class="insights-card-title">${esc(t('insights_activity_by_hour'))} <span style="font-weight:400;font-size:10px;color:var(--muted)">${esc(t('insights_peak_hour').replace('{hour}', peakHour.hour + ':00'))}</span></div><div class="insights-bars" style="max-height:240px;overflow-y:auto">` +
      d.activity_by_hour.map(r => {
        const pct = (r.sessions / maxHod * 100).toFixed(0);
        const isPeak = r.hour === peakHour.hour && peakHour.sessions > 0;
        return `<div class="insights-bar-row"><span class="insights-bar-label">${String(r.hour).padStart(2,'0')}</span><div class="insights-bar-track"><div class="insights-bar-fill${isPeak ? ' insights-bar-peak' : ''}" style="width:${pct}%"></div></div><span class="insights-bar-value">${r.sessions}</span></div>`;
      }).join('') +
      `</div></div>`;
  }
  const activityHtml = dowHtml || hodHtml ? `<div>${dowHtml}${hodHtml}</div>` : '';

  // ── Token breakdown (Main, below grid) ──
  const hasExtraTokens = d.total_cache_read_tokens > 0 || d.total_reasoning_tokens > 0;
  const tokenCards = `<div class="insights-card">
    <div class="insights-card-title">${esc(t('insights_token_breakdown'))}</div>
    <div class="insights-token-row">
      <span class="insights-token-label">${esc(t('insights_input_tokens'))}</span>
      <span class="insights-token-value">${fmtTokens(d.total_input_tokens)}</span>
    </div>
    <div class="insights-token-row">
      <span class="insights-token-label">${esc(t('insights_output_tokens'))}</span>
      <span class="insights-token-value">${fmtTokens(d.total_output_tokens)}</span>
    </div>
    ${hasExtraTokens ? `
    <div class="insights-token-row">
      <span class="insights-token-label">Cache read</span>
      <span class="insights-token-value">${fmtTokens(d.total_cache_read_tokens || 0)}</span>
    </div>
    <div class="insights-token-row">
      <span class="insights-token-label">Reasoning</span>
      <span class="insights-token-value">${fmtTokens(d.total_reasoning_tokens || 0)}</span>
    </div>` : ''}
    <div class="insights-token-row insights-token-total">
      <span class="insights-token-label">${esc(t('insights_total'))}</span>
      <span class="insights-token-value">${fmtTokens(d.total_tokens)}</span>
    </div>
  </div>`;

  // ── Data Quality + Warnings (Inspector) ──
  const warningsHtml = Array.isArray(d.warnings) && d.warnings.length
    ? d.warnings.map(w => `<div class="insights-warning-box" style="margin-bottom:8px">⚠ ${esc(w)}</div>`).join('')
    : '';

  const dataQualityHtml = `<div class="insights-inspector-compact">
    <div class="insights-inspector-title">Data Quality</div>
    <div class="insights-inspector-row"><span class="label">Data source</span><span class="value"><span class="insights-source-badge ${sourceColor}">${esc(sourceLabel)}</span></span></div>
    <div class="insights-inspector-row"><span class="label">Window type</span><span class="value">${esc(d.window_type || '—')}</span></div>
    <div class="insights-inspector-row"><span class="label">Period</span><span class="value">${safe(d.period_days)} days</span></div>
    <div class="insights-inspector-row"><span class="label">Sessions</span><span class="value">${fmtNum(d.total_sessions)}</span></div>
    <div class="insights-inspector-row"><span class="label">Cost coverage</span><span class="value">${costCoverage}</span></div>
    <div class="insights-inspector-row"><span class="label">Top model</span><span class="value" style="max-width:100px;overflow:hidden;text-overflow:ellipsis">${esc(topModelName)}</span></div>
    <div class="insights-inspector-row"><span class="label">Peak day</span><span class="value">${peakDay !== '—' ? peakDay : '—'}</span></div>
    ${d.total_estimated_cost !== undefined ? `<div class="insights-inspector-row"><span class="label">Est. cost</span><span class="value">${fmtCost(d.total_estimated_cost)}</span></div>` : ''}
    ${d.total_actual_cost !== undefined ? `<div class="insights-inspector-row"><span class="label">Actual cost</span><span class="value">${fmtCost(d.total_actual_cost)}</span></div>` : ''}
    ${d.cutoff_ts ? `<div class="insights-inspector-row"><span class="label">Cutoff</span><span class="value">${new Date(d.cutoff_ts * 1000).toLocaleDateString()}</span></div>` : ''}
  </div>`;

  // ── System Health compact (Inspector) ──
  const systemHealthCompact = `<div class="insights-inspector-compact" style="padding:10px">
    <div class="insights-inspector-title">System Status</div>
    <div id="systemHealthPanelCompact">${_renderSystemHealthPanel()}</div>
  </div>`;

  // ── LLM Wiki compact (Inspector) ──
  const wikiCompact = `<div class="insights-inspector-compact" style="padding:10px">
    <div class="insights-inspector-title">LLM Wiki</div>
    <div id="wikiStatusCompact">${_renderLlmWikiStatus(wikiStatus)}</div>
  </div>`;

  // ── Period buttons ──
  const periods = [7, 30, 90, 365];
  const currentPeriod = safe(d.period_days) || 30;
  const periodBtns = periods.map(p =>
    `<button class="insights-period-btn${p === currentPeriod ? ' active' : ''}" onclick="document.getElementById('insightsPeriod').value=${p};loadInsights(true)">${p}d</button>`
  ).join('');

  const viewNavItems = ['Overview', 'Models', 'Cost', 'Tokens', 'Activity'];
  const viewNav = viewNavItems.map(v =>
    `<button class="insights-nav-btn active">${v}</button>`
  ).join('');

  // ── Left Controls ──
  const controlsHtml = `<div class="insights-controls">
    <div class="insights-control-section">
      <div class="insights-control-title">Period</div>
      <div class="insights-period-group">${periodBtns}</div>
    </div>
    <div class="insights-control-section">
      <div class="insights-control-title">Views</div>
      <div class="insights-nav">${viewNav}</div>
    </div>
    <div class="insights-control-section">
      <div class="insights-control-title">Quick Facts</div>
      <div class="insights-quickfact"><span class="insights-quickfact-label">Top model</span><span class="insights-quickfact-value" title="${esc(topModelName)}">${esc(topModelName)}</span></div>
      <div class="insights-quickfact"><span class="insights-quickfact-label">Peak day</span><span class="insights-quickfact-value">${peakDay !== '—' ? peakDay : '—'}</span></div>
      <div class="insights-quickfact"><span class="insights-quickfact-label">Avg msg/session</span><span class="insights-quickfact-value">${avgMessagesPerSession}</span></div>
      <div class="insights-quickfact"><span class="insights-quickfact-label">Cost coverage</span><span class="insights-quickfact-value">${costCoverage}</span></div>
    </div>
    <div class="insights-control-section">
      <div class="insights-control-title">Source</div>
      <div style="display:flex;align-items:center;gap:6px;font-size:10px">
        <span class="insights-source-badge ${sourceColor}">${esc(sourceLabel)}</span>
        <span style="color:var(--muted)">${esc(d.window_type || 'calendar_days')}</span>
      </div>
    </div>
  </div>`;

  // ── Main Header ──
  const mainHeader = `<div class="insights-header">
    <div class="insights-header-title">${esc(t('insights_title') || 'Usage Analytics')}</div>
    <div class="insights-header-meta">
      <span class="insights-source-badge ${sourceColor}">${esc(sourceLabel)}</span>
      <span>${safe(d.period_days)} days</span>
      <span>·</span>
      <span>${fmtNum(d.total_sessions)} sessions</span>
    </div>
  </div>`;

  // ── Inspector ──
  const inspectorHtml = `<div class="insights-inspector">
    ${warningsHtml}
    ${dataQualityHtml}
    ${wikiCompact}
    ${systemHealthCompact}
  </div>`;

  // ── Assemble ──
  box.innerHTML = `<div class="insights-shell">
    ${controlsHtml}
    <div class="insights-main-column">
      ${mainHeader}
      ${kpiRow}
      ${dailyHtml}
      <div class="insights-main-grid">
        ${modelsHtml}
        ${tokenCards}
      </div>
      ${activityHtml ? `<div class="insights-main-grid">${activityHtml}</div>` : ''}
    </div>
    ${inspectorHtml}
  </div>`;

  // Restore system health polling by re-connecting the panel ID
  if (typeof _syncSystemHealthMonitorVisibility === 'function') _syncSystemHealthMonitorVisibility();
  if (typeof pollSystemHealth === 'function') void pollSystemHealth();
}

async function clearConversation() {
  if(!S.session) return;
  const _clrMsg=await showConfirmDialog({title:t('clear_conversation_title'),message:t('clear_conversation_message'),confirmLabel:t('clear'),danger:true,focusCancel:true});
  if(!_clrMsg) return;
  try {
    const data = await api('/api/session/clear', {method:'POST',
      body: JSON.stringify({session_id: S.session.session_id})});
    S.session = data.session;
    S.messages = [];
    S.toolCalls = [];
    syncTopbar();
    renderMessages();
    showToast(t('conversation_cleared'));
  } catch(e) { setStatus(t('clear_failed') + e.message); }
}

// ── Skills panel ──
async function loadSkills() {
  if (_skillsData) { renderSkills(_skillsData); return; }
  const box = $('skillsList');
  try {
    const data = await api('/api/skills');
    _skillsData = data.skills || [];
    // Prune collapsed state to only keep categories present in fresh data,
    // avoiding stale keys when categories are renamed or removed server-side.
    const liveCats = new Set(_skillsData.map(s => s.category || '(general)'));
    for (const c of _collapsedCats) { if (!liveCats.has(c)) _collapsedCats.delete(c); }
    renderSkills(_skillsData);
  } catch(e) { box.innerHTML = `<div style="padding:12px;color:var(--accent);font-size:12px">Error: ${esc(e.message)}</div>`; }
}

let _collapsedCats = new Set(); // persisted collapsed state across re-renders

function _toggleCatCollapse(cat) {
  if (_collapsedCats.has(cat)) _collapsedCats.delete(cat);
  else _collapsedCats.add(cat);
  // Toggle DOM without full re-render
  document.querySelectorAll('.skills-category').forEach(sec => {
    const header = sec.querySelector('.skills-cat-header');
    if (header && header.dataset.cat === cat) {
      const collapsed = _collapsedCats.has(cat);
      sec.classList.toggle('collapsed', collapsed);
      header.querySelector('.cat-chevron').style.transform = collapsed ? '' : 'rotate(90deg)';
      sec.querySelectorAll('.skill-item').forEach(el => el.style.display = collapsed ? 'none' : '');
    }
  });
}

function renderSkills(skills) {
  const query = ($('skillsSearch').value || '').toLowerCase();
  const filtered = query ? skills.filter(s =>
    (s.name||'').toLowerCase().includes(query) ||
    (s.description||'').toLowerCase().includes(query) ||
    (s.category||'').toLowerCase().includes(query)
  ) : skills;
  // Group by category
  const cats = {};
  for (const s of filtered) {
    const cat = s.category || '(general)';
    if (!cats[cat]) cats[cat] = [];
    cats[cat].push(s);
  }
  const box = $('skillsList');
  box.innerHTML = '';
  if (!filtered.length) { box.innerHTML = `<div style="padding:12px;color:var(--muted);font-size:12px">${esc(t('skills_no_match'))}</div>`; return; }
  for (const [cat, items] of Object.entries(cats).sort()) {
    const collapsed = _collapsedCats.has(cat);
    const sec = document.createElement('div');
    sec.className = 'skills-category' + (collapsed ? ' collapsed' : '');
    const hdr = document.createElement('div');
    hdr.className = 'skills-cat-header';
    hdr.dataset.cat = cat;
    hdr.innerHTML = `<span class="cat-chevron" style="display:inline-flex;transition:transform .15s;${collapsed ? '' : 'transform:rotate(90deg)'}">${li('chevron-right',12)}</span> ${esc(cat)} <span style="opacity:.5">(${items.length})</span>`;
    hdr.onclick = () => _toggleCatCollapse(cat);
    sec.appendChild(hdr);
    for (const skill of items.sort((a,b) => a.name.localeCompare(b.name))) {
      const el = document.createElement('div');
      el.className = 'skill-item';
      el.style.display = collapsed ? 'none' : '';
      el.innerHTML = `<span class="skill-name">${esc(skill.name)}</span><span class="skill-desc">${esc(skill.description||'')}</span>`;
      el.onclick = () => openSkill(skill.name, el);
      sec.appendChild(el);
    }
    box.appendChild(sec);
  }
}

function filterSkills() {
  if (_skillsData) renderSkills(_skillsData);
}

// Currently selected skill detail — kept across panel switches so re-entering
// the Skills view shows the last-viewed skill.
let _currentSkillDetail = null; // { name, category, content }
let _skillMode = 'empty'; // 'empty' | 'read' | 'create' | 'edit'
let _skillPreFormDetail = null; // snapshot of previously-viewed skill when entering a form
let _editingSkillName = null;

function _stripYamlFrontmatter(content) {
  if (!content) return { frontmatter: null, body: '' };
  const m = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(content);
  if (!m) return { frontmatter: null, body: content };
  return { frontmatter: m[1], body: content.slice(m[0].length) };
}

function _renderSkillDetail(name, content, linkedFiles) {
  const title = $('skillDetailTitle');
  const body = $('skillDetailBody');
  const empty = $('skillDetailEmpty');
  const editBtn = $('btnEditSkillDetail');
  const delBtn = $('btnDeleteSkillDetail');
  if (title) title.textContent = name;
  const { frontmatter, body: markdownBody } = _stripYamlFrontmatter(content);
  let html = '';
  if (frontmatter) {
    html += `<details class="skill-frontmatter"><summary>${esc(t('skill_metadata'))}</summary><pre><code>${esc(frontmatter)}</code></pre></details>`;
  }
  html += renderMd(markdownBody || '(no content)');
  const lf = linkedFiles || {};
  const categories = Object.entries(lf).filter(([,files]) => files && files.length > 0);
  if (categories.length) {
    html += `<div class="skill-linked-files"><div style="font-size:11px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.05em;margin-bottom:8px">${esc(t('linked_files'))}</div>`;
    for (const [cat, files] of categories) {
      html += `<div class="skill-linked-section"><h4>${esc(cat)}</h4>`;
      for (const f of files) {
        html += `<a class="skill-linked-file" href="#" data-skill-name="${esc(name)}" data-skill-file="${esc(f)}">${esc(f)}</a>`;
      }
      html += '</div>';
    }
    html += '</div>';
  }
  body.innerHTML = `<div class="main-view-content skill-detail-content">${html}</div>`;
  body.querySelectorAll('.skill-linked-file').forEach(a => {
    a.addEventListener('click', e => { e.preventDefault(); openSkillFile(a.dataset.skillName, a.dataset.skillFile); });
  });
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _skillMode = 'read';
  _setSkillHeaderButtons('read');
}

function _setSkillHeaderButtons(mode) {
  const editBtn = $('btnEditSkillDetail');
  const delBtn = $('btnDeleteSkillDetail');
  const cancelBtn = $('btnCancelSkillDetail');
  const saveBtn = $('btnSaveSkillDetail');
  const show = b => b && (b.style.display = '');
  const hide = b => b && (b.style.display = 'none');
  if (mode === 'read') { show(editBtn); show(delBtn); hide(cancelBtn); hide(saveBtn); }
  else if (mode === 'create' || mode === 'edit') { hide(editBtn); hide(delBtn); show(cancelBtn); show(saveBtn); }
  else { hide(editBtn); hide(delBtn); hide(cancelBtn); hide(saveBtn); }
}

async function openSkill(name, el) {
  // Highlight active skill in the sidebar list
  document.querySelectorAll('.skill-item').forEach(e => e.classList.remove('active'));
  if (el) el.classList.add('active');
  _skillPreFormDetail = null;
  _editingSkillName = null;
  try {
    const data = await api(`/api/skills/content?name=${encodeURIComponent(name)}`);
    _currentSkillDetail = { name, content: data.content || '', linked_files: data.linked_files || {} };
    _renderSkillDetail(name, data.content || '', data.linked_files || {});
  } catch(e) { setStatus(t('skill_load_failed') + e.message); }
}

async function openSkillFile(skillName, filePath) {
  try {
    const data = await api(`/api/skills/content?name=${encodeURIComponent(skillName)}&file=${encodeURIComponent(filePath)}`);
    const body = $('skillDetailBody');
    if (!body) return;
    const ext = (filePath.split('.').pop() || '').toLowerCase();
    const isMd = ['md','markdown'].includes(ext);
    const backLabel = t('skills_back_to').replace('{0}', skillName);
    const header = `<div class="skill-file-breadcrumb"><a href="#" class="skill-file-back" data-skill-name="${esc(skillName)}">&larr; ${esc(backLabel)}</a><span class="skill-file-path">${esc(filePath)}</span></div>`;
    let content;
    if (isMd) {
      content = `<div class="main-view-content">${renderMd(data.content || '')}</div>`;
    } else {
      const escaped = esc(data.content || '');
      content = `<pre class="skill-file-code"><code>${escaped}</code></pre>`;
    }
    body.innerHTML = header + content;
    body.style.display = '';
    const empty = $('skillDetailEmpty');
    if (empty) empty.style.display = 'none';
    body.querySelectorAll('.skill-file-back').forEach(a => {
      a.addEventListener('click', e => {
        e.preventDefault();
        if (_currentSkillDetail && _currentSkillDetail.name === a.dataset.skillName) {
          _renderSkillDetail(_currentSkillDetail.name, _currentSkillDetail.content, _currentSkillDetail.linked_files);
        } else {
          openSkill(a.dataset.skillName, null);
        }
      });
    });
    if (!isMd) requestAnimationFrame(() => { if (typeof highlightCode === 'function') highlightCode(); });
  } catch(e) { setStatus(t('skill_file_load_failed') + e.message); }
}

function editCurrentSkill() {
  if (!_currentSkillDetail) return;
  const s = _currentSkillDetail;
  let category = '';
  if (_skillsData) {
    const match = _skillsData.find(x => x.name === s.name);
    if (match) category = match.category || '';
  }
  _skillPreFormDetail = { name: s.name, content: s.content, linked_files: s.linked_files };
  _editingSkillName = s.name;
  _skillMode = 'edit';
  _renderSkillForm({ name: s.name, category, content: s.content || '', isEdit: true });
}

function openSkillCreate() {
  if (typeof switchPanel === 'function' && _currentPanel !== 'skills') switchPanel('skills');
  _skillPreFormDetail = _currentSkillDetail ? { ..._currentSkillDetail } : null;
  _editingSkillName = null;
  _skillMode = 'create';
  _renderSkillForm({ name: '', category: '', content: '', isEdit: false });
}

function _renderSkillForm({ name, category, content, isEdit }) {
  const title = $('skillDetailTitle');
  const body = $('skillDetailBody');
  const empty = $('skillDetailEmpty');
  if (!body || !title) return;
  title.textContent = isEdit ? t('skills_edit') + ' · ' + name : t('new_skill');
  const nameDisabled = isEdit ? 'disabled' : '';
  const nameHint = isEdit ? `<div class="detail-form-hint">${esc(t('skill_rename_not_supported') || 'Renaming a skill is not supported. Create a new skill and delete the old one to rename.')}</div>` : '';
  body.innerHTML = `
    <div class="main-view-content">
      <form class="detail-form" onsubmit="event.preventDefault(); saveSkillForm();">
        <div class="detail-form-row">
          <label for="skillFormName">${esc(t('skill_name') || 'Name')}</label>
          <input type="text" id="skillFormName" value="${esc(name || '')}" placeholder="my-skill" autocomplete="off" ${nameDisabled} required>
          ${nameHint}
        </div>
        <div class="detail-form-row">
          <label for="skillFormCategory">${esc(t('skill_category') || 'Category')}</label>
          <input type="text" id="skillFormCategory" value="${esc(category || '')}" placeholder="${esc(t('skill_category_placeholder') || 'Optional, e.g. devops')}" autocomplete="off">
        </div>
        <div class="detail-form-row">
          <label for="skillFormContent">${esc(t('skill_content') || 'SKILL.md content')}</label>
          <textarea id="skillFormContent" rows="18" placeholder="${esc(t('skill_content_placeholder') || 'YAML frontmatter + markdown body')}">${esc(content || '')}</textarea>
        </div>
        <div id="skillFormError" class="detail-form-error" style="display:none"></div>
      </form>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _setSkillHeaderButtons(isEdit ? 'edit' : 'create');
  const focusEl = isEdit ? $('skillFormCategory') : $('skillFormName');
  if (focusEl) focusEl.focus();
}

function cancelSkillForm() {
  _editingSkillName = null;
  if (_skillPreFormDetail) {
    const snap = _skillPreFormDetail;
    _skillPreFormDetail = null;
    _currentSkillDetail = snap;
    _renderSkillDetail(snap.name, snap.content || '', snap.linked_files || {});
    return;
  }
  // Revert to empty state
  _skillPreFormDetail = null;
  _currentSkillDetail = null;
  _skillMode = 'empty';
  const body = $('skillDetailBody');
  const empty = $('skillDetailEmpty');
  const title = $('skillDetailTitle');
  if (body) { body.innerHTML = ''; body.style.display = 'none'; }
  if (empty) empty.style.display = '';
  if (title) title.textContent = '';
  _setSkillHeaderButtons('empty');
}

async function saveSkillForm() {
  const nameInput = $('skillFormName');
  const catInput = $('skillFormCategory');
  const contentInput = $('skillFormContent');
  const errEl = $('skillFormError');
  if (!nameInput || !contentInput || !errEl) return;
  const name = (nameInput.value || '').trim().toLowerCase().replace(/\s+/g, '-');
  const category = (catInput ? (catInput.value || '').trim() : '');
  const content = contentInput.value;
  errEl.style.display = 'none';
  if (!name) { errEl.textContent = t('skill_name_required'); errEl.style.display = ''; return; }
  if (!content.trim()) { errEl.textContent = t('content_required'); errEl.style.display = ''; return; }
  try {
    await api('/api/skills/save', {method:'POST', body: JSON.stringify({name, category: category||undefined, content})});
    showToast(_editingSkillName ? t('skill_updated') : t('skill_created'));
    _skillsData = null;
    _cronSkillsCache = null;
    _editingSkillName = null;
    _skillPreFormDetail = null;
    await loadSkills();
    // Reload the saved skill in read mode with fresh content
    const row = document.querySelector(`.skill-item .skill-name`);
    const match = document.querySelectorAll('.skill-item');
    let targetEl = null;
    match.forEach(el => {
      const nm = el.querySelector('.skill-name');
      if (nm && nm.textContent === name) targetEl = el;
    });
    await openSkill(name, targetEl);
  } catch(e) { errEl.textContent = t('error_prefix') + e.message; errEl.style.display = ''; }
}

// Back-compat aliases (delete flow + any old callers)
const submitSkillSave = saveSkillForm;
function toggleSkillForm(){ openSkillCreate(); }

async function deleteCurrentSkill() {
  if (!_currentSkillDetail) return;
  const name = _currentSkillDetail.name;
  const message = t('skill_delete_confirm')
    ? t('skill_delete_confirm').replace('{0}', name)
    : `Delete skill "${name}"?`;
  const ok = await showConfirmDialog({
    title: t('delete_title') || 'Delete',
    message,
    confirmLabel: t('delete_title') || 'Delete',
    danger: true,
    focusCancel: true,
  });
  if (!ok) return;
  try {
    await api('/api/skills/delete', { method:'POST', body: JSON.stringify({ name }) });
    _currentSkillDetail = null;
    _skillPreFormDetail = null;
    _skillsData = null;
    _cronSkillsCache = null;
    _skillMode = 'empty';
    const body = $('skillDetailBody');
    const empty = $('skillDetailEmpty');
    const title = $('skillDetailTitle');
    if (body) { body.innerHTML = ''; body.style.display = 'none'; }
    if (empty) empty.style.display = '';
    if (title) title.textContent = '';
    _setSkillHeaderButtons('empty');
    await loadSkills();
    showToast(t('skill_deleted') || 'Skill deleted');
  } catch(e) { setStatus(t('error_prefix') + e.message); }
}

// ── Memory (main view) ──
let _memoryData = null;
let _currentMemorySection = null; // 'memory' | 'user'
let _memoryMode = 'empty'; // 'empty' | 'read' | 'edit'

const MEMORY_SECTIONS = [
  { key: 'memory', labelKey: 'my_notes', emptyKey: 'no_notes_yet', iconKey: 'brain' },
  { key: 'user',   labelKey: 'user_profile', emptyKey: 'no_profile_yet', iconKey: 'user' },
  { key: 'supermemory', labelKey: 'supermemory_search', emptyKey: 'supermemory_empty', iconKey: 'cloud' },
  { key: 'hybrid', labelKey: 'hybrid_search', emptyKey: 'hybrid_empty', iconKey: 'layers' },
];

function _memorySectionMeta(key) {
  return MEMORY_SECTIONS.find(s => s.key === key) || MEMORY_SECTIONS[0];
}

function _memorySectionContent(key) {
  if (!_memoryData) return '';
  if (key === 'supermemory') return '';
  return key === 'user' ? (_memoryData.user || '') : (_memoryData.memory || '');
}

function _memorySectionMtime(key) {
  if (!_memoryData) return 0;
  return key === 'user' ? (_memoryData.user_mtime || 0) : (_memoryData.memory_mtime || 0);
}

function _setMemoryHeaderButtons(mode) {
  const show = b => b && (b.style.display = '');
  const hide = b => b && (b.style.display = 'none');
  const editBtn = $('btnEditMemoryDetail');
  const cancelBtn = $('btnCancelMemoryDetail');
  const saveBtn = $('btnSaveMemoryDetail');
  if (mode === 'read') { show(editBtn); hide(cancelBtn); hide(saveBtn); }
  else if (mode === 'edit') { hide(editBtn); show(cancelBtn); show(saveBtn); }
  else { hide(editBtn); hide(cancelBtn); hide(saveBtn); }
}

function _renderMemoryDetail(section) {
  const meta = _memorySectionMeta(section);
  const title = $('memoryDetailTitle');
  const body = $('memoryDetailBody');
  const empty = $('memoryDetailEmpty');
  if (!title || !body) return;
  title.textContent = t(meta.labelKey);
  const content = _memorySectionContent(section);
  const mtime = _memorySectionMtime(section);
  const mtimeStr = mtime ? new Date(mtime * 1000).toLocaleString() : '';
  const mtimeHtml = mtimeStr ? `<div class="memory-detail-mtime">${esc(mtimeStr)}</div>` : '';
  const inner = content
    ? `<div class="memory-chat-actions"><button class="primary-btn" type="button" onclick="chatWithCurrentMemory()">Chat with this memory</button></div><div class="memory-content preview-md">${renderMd(content)}</div>`
    : `<div class="memory-empty">${esc(t(meta.emptyKey))}</div>`;
  body.innerHTML = `<div class="main-view-content">${mtimeHtml}${inner}</div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _memoryMode = 'read';
  _setMemoryHeaderButtons('read');
}

function _renderMemoryEdit(section) {
  const meta = _memorySectionMeta(section);
  const title = $('memoryDetailTitle');
  const body = $('memoryDetailBody');
  const empty = $('memoryDetailEmpty');
  if (!title || !body) return;
  title.textContent = t(meta.labelKey);
  const content = _memorySectionContent(section);
  body.innerHTML = `
    <div class="main-view-content">
      <form class="detail-form" onsubmit="event.preventDefault(); submitMemorySave();">
        <div class="mem-editor-toolbar">
          <button type="button" class="mem-tb-btn" onclick="_memInsertMarkdown('**','**')" title="Bold" style="font-weight:700">B</button>
          <button type="button" class="mem-tb-btn" onclick="_memInsertMarkdown('*','*')" title="Italic"><em>I</em></button>
          <button type="button" class="mem-tb-btn mono" onclick="_memInsertMarkdown('\`','\`')" title="Code">&lt;/&gt;</button>
          <button type="button" class="mem-tb-btn" onclick="_memInsertMarkdown('- ',' ')" title="List">• List</button>
          <button type="button" class="mem-tb-btn" onclick="_memInsertMarkdown('## ',' ')" title="Heading">H2</button>
          <button type="button" class="mem-tb-btn" onclick="_memInsertMarkdown('---\\n','')" title="Divider">—</button>
          <span class="mem-tb-spacer"></span>
          <span class="mem-tb-wordcount" id="memWordCount">0 words · 0 chars</span>
        </div>
        <div class="detail-form-row">
          <label for="memEditContent">${esc(t('memory_notes_label'))}</label>
          <textarea id="memEditContent" rows="20" spellcheck="false" oninput="_memUpdateWordCount()">${esc(content)}</textarea>
        </div>
        <div id="memEditError" class="detail-form-error" style="display:none"></div>
      </form>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _memoryMode = 'edit';
  _setMemoryHeaderButtons('edit');
  const ta = $('memEditContent');
  if (ta) ta.focus();
}

function openMemorySection(section, el) {
  _currentMemorySection = section;
  document.querySelectorAll('#memoryPanel .side-menu-item').forEach(e => e.classList.remove('active'));
  if (el) el.classList.add('active');
  if (section === 'supermemory') {
    _renderSupermemoryView();
  } else if (section === 'hybrid') {
    _renderHybridView();
  } else {
    _renderMemoryDetail(section);
  }
}

function editCurrentMemory() {
  if (!_currentMemorySection) return;
  _renderMemoryEdit(_currentMemorySection);
}

function cancelMemoryEdit() {
  if (!_currentMemorySection) return;
  _renderMemoryDetail(_currentMemorySection);
}

// Legacy alias (kept for any stale references)
function toggleMemoryEdit() { editCurrentMemory(); }
function closeMemoryEdit() { cancelMemoryEdit(); }

async function chatWithCurrentMemory() {
  if (!_currentMemorySection || _currentMemorySection === 'supermemory') return;
  const content = _memorySectionContent(_currentMemorySection).trim();
  if (!content) {
    if (typeof showToast === 'function') showToast('No memory content to chat with', 2500, 'warning');
    return;
  }
  if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard:true});
  const msg = $('msg');
  if (!msg) return;
  const label = _currentMemorySection === 'user' ? 'user memory' : 'workspace memory';
  msg.value = `Use this ${label} as context and help me reason about it:\n\n${content.slice(0, 6000)}\n\nQuestion: `;
  if (typeof autoResize === 'function') autoResize();
  msg.focus();
}

async function submitMemorySave() {
  if (!_currentMemorySection || _currentMemorySection === 'supermemory') return;
  const ta = $('memEditContent');
  const errEl = $('memEditError');
  if (!ta) return;
  if (errEl) errEl.style.display = 'none';
  try {
    const qs = typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery() : '';
    await api('/api/memory/write' + qs, {method:'POST', body: JSON.stringify({section: _currentMemorySection, content: ta.value})});
    showToast(t('memory_saved'));
    await loadMemory(true);
    _renderMemoryDetail(_currentMemorySection);
  } catch(e) {
    if (errEl) { errEl.textContent = t('error_prefix') + e.message; errEl.style.display = ''; }
  }
}

// ── Supermemory search view ──

let _smSearchTimer = null;

function _renderSupermemoryView() {
  const title = $('memoryDetailTitle');
  const body = $('memoryDetailBody');
  const empty = $('memoryDetailEmpty');
  if (!title || !body) return;
  title.textContent = t('supermemory_search');
  body.innerHTML = `
    <div class="main-view-content">
      <div class="sm-search-bar">
        <input id="smSearchInput" type="text" class="sm-search-input" placeholder="${esc(t('supermemory_placeholder'))}"
               autocomplete="off" spellcheck="false">
        <button id="smSearchBtn" class="sm-search-btn" onclick="_doSupermemorySearch()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <span>${esc(t('supermemory_search_btn'))}</span>
        </button>
      </div>
      <div class="sm-status" id="smStatus"></div>
      <div class="sm-results" id="smResults"></div>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _memoryMode = 'read';
  _setMemoryHeaderButtons('empty');

  // Load status on render
  _checkSupermemoryStatus();

  // Enter-key handler
  const input = $('smSearchInput');
  if (input) {
    input.onkeydown = e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        _doSupermemorySearch();
      }
    };
    // Focus after a tick
    setTimeout(() => input.focus(), 50);
  }
}

async function _checkSupermemoryStatus() {
  const statusEl = $('smStatus');
  if (!statusEl) return;
  try {
    const qs = typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery() : '';
    const data = await api('/api/memory/supermemory/status' + qs);
    if (data.connected) {
      statusEl.innerHTML = `<span class="sm-status-ok">✓ ${esc(t('supermemory_connected'))}</span>`;
    } else if (data.configured) {
      statusEl.innerHTML = `<span class="sm-status-warn">⚠ ${esc(t('supermemory_config_error'))}</span>`;
    } else {
      statusEl.innerHTML = `<span class="sm-status-off">✗ ${esc(t('supermemory_not_configured'))}</span>`;
    }
  } catch(e) {
    statusEl.innerHTML = `<span class="sm-status-off">✗ ${esc(t('supermemory_not_configured'))}</span>`;
  }
}

let _smResultsCache = null;

async function _doSupermemorySearch() {
  const input = $('smSearchInput');
  const resultsEl = $('smResults');
  const btn = $('smSearchBtn');
  if (!input || !resultsEl) return;
  const q = input.value.trim();
  if (!q) {
    resultsEl.innerHTML = `<div class="sm-empty">${esc(t('supermemory_type_query'))}</div>`;
    return;
  }
  btn.disabled = true;
  btn.innerHTML = `<span>${esc(t('supermemory_searching'))}</span>`;
  resultsEl.innerHTML = `<div class="sm-spinner">${esc(t('supermemory_searching'))}…</div>`;
  try {
    const qs = typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery() : '';
    const data = await api('/api/memory/supermemory/search' + qs, {
      method: 'POST',
      body: JSON.stringify({q, limit: 10})
    });
    _smResultsCache = data;
    _renderSupermemoryResults(data, q);
  } catch(e) {
    resultsEl.innerHTML = `<div class="sm-error">${esc(t('error_prefix'))}${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><span>${esc(t('supermemory_search_btn'))}</span>`;
  }
}

function _renderSupermemoryResults(data, query) {
  const resultsEl = $('smResults');
  if (!resultsEl) return;
  // Navigate the response structure: data.results may have .data array or .memories array
  const raw = data.results || {};
  const items = raw.data || raw.memories || raw.results || [];
  if (!Array.isArray(items) || items.length === 0) {
    resultsEl.innerHTML = `<div class="sm-empty">${esc(t('supermemory_no_results'))}</div>`;
    return;
  }
  let html = `<div class="sm-result-count">${items.length} ${esc(t('supermemory_results_for'))} "<strong>${esc(query)}</strong>"</div>`;
  for (const item of items) {
    const content = item.content || item.text || item.snippet || '';
    const title = item.title || '';
    const score = item.score || item.relevance || null;
    const createdAt = item.created_at || item.createdAt || null;
    const scoreHtml = score !== null ? `<span class="sm-score">${Math.round(score * 100)}%</span>` : '';
    const timeHtml = createdAt ? `<span class="sm-time">${new Date(createdAt).toLocaleString()}</span>` : '';
    const snippet = content.length > 300 ? content.slice(0, 300) + '…' : content;
    html += `<div class="sm-result-item">
      ${title ? `<div class="sm-result-title">${esc(title)}</div>` : ''}
      <div class="sm-result-body">${esc(snippet)}</div>
      <div class="sm-result-meta">${scoreHtml} ${timeHtml}</div>
    </div>`;
  }
  resultsEl.innerHTML = html;
}

// ── Hybrid Memory Search ──

function _renderHybridView() {
  const title = $('memoryDetailTitle');
  const body = $('memoryDetailBody');
  const empty = $('memoryDetailEmpty');
  if (!title || !body) return;
  title.textContent = t('hybrid_search');
  body.innerHTML = `
    <div class="main-view-content">
      <div class="sm-search-bar">
        <input id="hybridSearchInput" type="text" class="sm-search-input" placeholder="${esc(t('hybrid_placeholder'))}"
               autocomplete="off" spellcheck="false">
        <button id="hybridSearchBtn" class="sm-search-btn" onclick="_doHybridSearch()">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
          <span>${esc(t('hybrid_search_btn'))}</span>
        </button>
      </div>
      <div class="sm-status" id="hybridStatus"></div>
      <div class="hybrid-results" id="hybridResults"></div>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _memoryMode = 'read';
  _setMemoryHeaderButtons('empty');

  _checkHybridStatus();

  const input = $('hybridSearchInput');
  if (input) {
    input.onkeydown = e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        _doHybridSearch();
      }
    };
    setTimeout(() => input.focus(), 50);
  }
}

async function _checkHybridStatus() {
  const statusEl = $('hybridStatus');
  if (!statusEl) return;
  try {
    const qs = typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery() : '';
    const smStatus = await api('/api/memory/supermemory/status' + qs);
    let html = '';
    if (smStatus.connected) {
      html += `<span class="sm-status-ok">✓ Supermemory verbunden</span>`;
    } else if (smStatus.configured) {
      html += `<span class="sm-status-warn">⚠ Supermemory konfiguriert, aber nicht verbunden</span>`;
    } else {
      html += `<span class="sm-status-off">✗ Supermemory nicht konfiguriert</span>`;
    }
    html += ' &middot; <span class="sm-status-ok">✓ Lokaler Speicher aktiv</span>';
    statusEl.innerHTML = html;
  } catch(e) {
    statusEl.innerHTML = `<span class="sm-status-ok">✓ Lokaler Speicher aktiv</span>`;
  }
}

async function _doHybridSearch() {
  const input = $('hybridSearchInput');
  const resultsEl = $('hybridResults');
  const btn = $('hybridSearchBtn');
  if (!input || !resultsEl) return;
  const q = input.value.trim();
  if (!q) {
    resultsEl.innerHTML = `<div class="sm-empty">Bitte gib einen Suchbegriff ein.</div>`;
    return;
  }
  btn.disabled = true;
  btn.innerHTML = `<span>Suche…</span>`;
  resultsEl.innerHTML = `<div class="sm-spinner">Suche in lokalem Speicher & Supermemory…</div>`;
  try {
    const qs = typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery() : '';
    const data = await api('/api/memory/hybrid/search' + qs, {
      method: 'POST',
      body: JSON.stringify({q, limit: 15})
    });
    _renderHybridResults(data, q);
  } catch(e) {
    resultsEl.innerHTML = `<div class="sm-error">${esc(t('error_prefix'))}${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg><span>${esc(t('hybrid_search_btn'))}</span>`;
  }
}

function _renderHybridResults(data, query) {
  const resultsEl = $('hybridResults');
  if (!resultsEl) return;
  const hits = data.hits || [];
  if (!Array.isArray(hits) || hits.length === 0) {
    resultsEl.innerHTML = `<div class="sm-empty">Keine Ergebnisse für "${esc(query)}"</div>`;
    return;
  }

  // Group by category
  const groups = {};
  for (const hit of hits) {
    const cat = hit.category || 'Allgemein';
    if (!groups[cat]) groups[cat] = [];
    groups[cat].push(hit);
  }

  let html = `<div class="sm-result-count">${hits.length} Ergebnisse für "<strong>${esc(query)}</strong>"</div>`;
  const categoryNames = Object.keys(groups).sort();
  for (const cat of categoryNames) {
    const items = groups[cat];
    html += `<div class="hybrid-category"><div class="hybrid-category-title">📁 ${esc(cat)} (${items.length})</div>`;
    for (const item of items) {
      const source = item.source === 'local' ? '💾 Lokal' : '☁️ Supermemory';
      const score = item.score != null ? Math.round(item.score * 100) + '%' : '';
      html += `<div class="hybrid-item">
        <div class="hybrid-item-source">${source}</div>
        <div class="hybrid-item-body">${esc(item.content.length > 250 ? item.content.slice(0, 250) + '…' : item.content)}</div>
        <div class="hybrid-item-meta">
          ${score ? `<span class="sm-score">${score}</span>` : ''}
          <span class="hybrid-item-tag">${esc(cat)}</span>
        </div>
      </div>`;
    }
    html += `</div>`;
  }
  resultsEl.innerHTML = html;
}

// ── File Tree Panel ──
/**
 * Toggle the file tree panel in the chat layout between open and minimized.
 * When minimized, the panel collapses to 0px width and a tab icon is shown.
 * Width is restored to the last saved width or default 260px.
 */
window.toggleFileTreePanel = function(){
  const panel = $('chatFileTreePanel');
  if(!panel) return;
  const root = document.documentElement;
  const minimized = panel.classList.contains('file-tree-panel--minimized');
  if(minimized){
    // Restore — read saved width from localStorage
    const saved = parseInt(localStorage.getItem('sidekick-file-tree-w')) || 260;
    root.style.setProperty('--file-tree-width', saved + 'px');
    panel.classList.remove('file-tree-panel--minimized');
  }else{
    // Save current width before collapsing
    const curW = parseInt(root.style.getPropertyValue('--file-tree-width')) || panel.getBoundingClientRect().width || 260;
    if(curW > 0) localStorage.setItem('sidekick-file-tree-w', curW);
    root.style.setProperty('--file-tree-width', '0px');
    panel.classList.add('file-tree-panel--minimized');
  }
};

// ── Workspace management ──
let _workspaceList = [];  // cached from /api/workspaces
let _wsSuggestTimer = null;
let _wsSuggestReq = 0;
let _wsSuggestIndex = -1;

function closeWorkspacePathSuggestions(){
  const box=$('workspaceFormPathSuggestions');
  if(box){
    box.innerHTML='';
    box.style.display='none';
  }
  _wsSuggestIndex=-1;
}

function _applyWorkspaceSuggestion(path){
  const input=$('workspaceFormPath');
  const next=(path||'').endsWith('/')?(path||''):`${path||''}/`;
  if(input){
    input.value=next;
    input.focus();
    input.setSelectionRange(next.length, next.length);
  }
  scheduleWorkspacePathSuggestions();
}

function _highlightWorkspaceSuggestion(idx){
  const box=$('workspaceFormPathSuggestions');
  if(!box)return;
  const items=[...box.querySelectorAll('.ws-suggest-item')];
  items.forEach((el,i)=>{
    const active=i===idx;
    el.classList.toggle('active', active);
    if(active) el.scrollIntoView({block:'nearest'});
  });
}

function _renderWorkspacePathSuggestions(paths){
  const box=$('workspaceFormPathSuggestions');
  if(!box)return;
  box.innerHTML='';
  if(!paths || !paths.length){
    box.style.display='none';
    _wsSuggestIndex=-1;
    return;
  }
  paths.forEach((path, idx)=>{
    const pathParts=(path||'').split('/').filter(Boolean);
    const leaf=pathParts[pathParts.length-1]||path;
    const parent=pathParts.length>1?`/${pathParts.slice(0,-1).join('/')}`:'/';
    const item=document.createElement('button');
    item.type='button';
    item.className='ws-suggest-item';
    item.innerHTML=`<span class="ws-suggest-leaf">${esc(leaf)}</span><span class="ws-suggest-parent">${esc(parent)}</span>`;
    item.dataset.path=path;
    item.onmouseenter=()=>{_wsSuggestIndex=idx;_highlightWorkspaceSuggestion(idx);};
    item.onmousedown=(e)=>{e.preventDefault();_applyWorkspaceSuggestion(path);};
    box.appendChild(item);
  });
  box.style.display='block';
  _wsSuggestIndex=0;
  _highlightWorkspaceSuggestion(_wsSuggestIndex);
}

async function _loadWorkspacePathSuggestions(prefix){
  const reqId=++_wsSuggestReq;
  try{
    const qs=new URLSearchParams({prefix:prefix||''}).toString();
    const data=await api(`/api/workspaces/suggest?${qs}`);
    if(reqId!==_wsSuggestReq)return;
    _renderWorkspacePathSuggestions(data.suggestions||[]);
  }catch(_){
    if(reqId!==_wsSuggestReq)return;
    closeWorkspacePathSuggestions();
  }
}

function scheduleWorkspacePathSuggestions(){
  const input=$('workspaceFormPath');
  if(!input)return;
  const prefix=input.value.trim();
  if(!prefix){
    closeWorkspacePathSuggestions();
    return;
  }
  if(_wsSuggestTimer) clearTimeout(_wsSuggestTimer);
  _wsSuggestTimer=setTimeout(()=>{
    _loadWorkspacePathSuggestions(prefix);
  }, 120);
}

function getWorkspaceFriendlyName(path){
  // Look up the friendly name from the workspace list cache, fallback to last path segment
  if(_workspaceList && _workspaceList.length){
    const match=_workspaceList.find(w=>w.path===path);
    if(match && match.name) return match.name;
  }
  return String(path||'').replace(/\\/g,'/').split('/').filter(Boolean).pop()||path;
}

function syncWorkspaceDisplays(){
  const hasSession=!!(S.session&&S.session.workspace);
  // Fall back to the profile default workspace when no session is active yet.
  // S._profileDefaultWorkspace is set during boot and profile switches from /api/settings.
  const defaultWs=(typeof S._profileDefaultWorkspace==='string'&&S._profileDefaultWorkspace)||'';
  const ws=hasSession?S.session.workspace:(defaultWs||'');
  const hasWorkspace=!!(ws);
  const label=hasWorkspace?getWorkspaceFriendlyName(ws):t('no_workspace');
  const activeSpaceDir=String(window._activeSpaceConfig&&window._activeSpaceConfig.project_dir||'').trim();
  const displayLabel=hasWorkspace
    ? (label + (S._bootReady && activeSpaceDir && ws===activeSpaceDir ? ' *' : ''))
    : t('no_workspace');
  const headerLabel=hasWorkspace
    ? displayLabel
    : (S._bootReady ? t('no_workspace') : 'workspace loading');
  const headerTitle=hasWorkspace
    ? ('Workspace: '+ws+(label&&label!==ws?' ('+label+')':'')+'. Click to open workspaces.')
    : (S._bootReady ? 'No workspace selected. Click to open workspaces.' : 'Workspace loading. Click to open workspaces.');

  const composerChip=$('composerWorkspaceChip');
  const composerLabel=$('composerWorkspaceLabel');
  const mobileAction=$('composerMobileWorkspaceAction');
  const mobileLabel=$('composerMobileWorkspaceLabel');
  const composerDropdown=$('composerWsDropdown');
  const headerBadge=$('workspaceStatusBadge');
  const headerValue=$('workspaceStatusValue');
  const canChooseWorkspace = hasWorkspace || ((Array.isArray(_workspaceList) ? _workspaceList.length : 0) > 0);
  if(!hasWorkspace && composerDropdown) composerDropdown.classList.remove('open');
  // Only show workspace label once boot has finished to prevent
  // flash of "No workspace" before the saved session finishes loading.
  if(composerLabel) composerLabel.textContent=(S._bootReady?displayLabel:'');
  if(mobileLabel) mobileLabel.textContent=S._bootReady?displayLabel:'';
  if(composerChip){
    composerChip.disabled=!canChooseWorkspace;
    composerChip.classList.toggle('active',!!(composerDropdown&&composerDropdown.classList.contains('open')));
  }
  if(mobileAction){
    mobileAction.classList.toggle('active',!!(composerDropdown&&composerDropdown.classList.contains('open')));
  }
  if(headerBadge){
    ['workspace-state-loading','workspace-state-active','workspace-state-empty'].forEach(cls=>headerBadge.classList.remove(cls));
    const headerState=hasWorkspace ? 'workspace-state-active' : (S._bootReady ? 'workspace-state-empty' : 'workspace-state-loading');
    headerBadge.classList.add(headerState);
    headerBadge.hidden=false;
    headerBadge.disabled=false;
    headerBadge.setAttribute('aria-label',headerTitle);
  }
  if(headerValue){
    headerValue.textContent=headerLabel;
  }
}

async function loadWorkspaceList(){
  try{
    const data = await api('/api/workspaces');
    _workspaceList = data.workspaces || [];
    syncWorkspaceDisplays();
    return data;
  }catch(e){ return {workspaces:[], last:''}; }
}

function _renderWorkspaceAction(label, meta, iconSvg, onClick){
  const opt=document.createElement('div');
  opt.className='ws-opt ws-opt-action';
  opt.innerHTML=`<span class="ws-opt-icon">${iconSvg}</span><span><span class="ws-opt-name">${esc(label)}</span>${meta?`<span class="ws-opt-meta">${esc(meta)}</span>`:''}</span>`;
  opt.onclick=onClick;
  return opt;
}

function _positionComposerWsDropdown(){
  const dd=$('composerWsDropdown');
  const chip=$('composerWorkspaceGroup')||$('composerWorkspaceChip');
  const mobileAction=$('composerMobileWorkspaceAction');
  const panel=$('composerMobileConfigPanel');
  const footer=document.querySelector('.composer-footer');
  // While the mobile config panel is open, anchor to #composerMobileWorkspaceAction instead of only the desktop workspace chip.
  const anchor=(panel&&panel.classList.contains('open')&&mobileAction)?mobileAction:chip;
  if(!dd||!anchor||!footer)return;
  const chipRect=anchor.getBoundingClientRect();
  const footerRect=footer.getBoundingClientRect();
  let left=chipRect.left-footerRect.left;
  const maxLeft=Math.max(0, footer.clientWidth-dd.offsetWidth);
  left=Math.max(0, Math.min(left, maxLeft));
  dd.style.left=`${left}px`;
}

function _positionProfileDropdown(){
  const dd=$('profileDropdown');
  const chip=$('profileChip');
  const footer=document.querySelector('.composer-footer');
  if(!dd||!chip||!footer)return;
  const chipRect=chip.getBoundingClientRect();
  const footerRect=footer.getBoundingClientRect();
  let left=chipRect.left-footerRect.left;
  const maxLeft=Math.max(0, footer.clientWidth-dd.offsetWidth);
  left=Math.max(0, Math.min(left, maxLeft));
  dd.style.left=`${left}px`;
}

function renderWorkspaceDropdownInto(dd, workspaces, currentWs){
  if(!dd)return;
  dd.innerHTML='';

  // ── Search row ──────────────────────────────────────────────────────────
  const searchRow=document.createElement('div');
  searchRow.className='ws-search-row';
  searchRow.innerHTML=`<input class="ws-search-input" type="text" placeholder="${esc(t('ws_search_placeholder')||'Search workspaces…')}" spellcheck="false" autocomplete="off"><button class="ws-search-clear" title="Clear search">${li('x',10)}</button>`;
  const si=searchRow.querySelector('.ws-search-input');
  const sc=searchRow.querySelector('.ws-search-clear');
  dd.appendChild(searchRow);

  // ── Workspace list ──────────────────────────────────────────────────────
  // Sort alphabetically by name (case-insensitive) before rendering.
  const sorted=[...workspaces].sort((a,b)=>(a.name||'').localeCompare(b.name||''));
  const listContainer=document.createElement('div');
  listContainer.className='ws-list-container';
  dd.appendChild(listContainer);

  // Pre-create noResults element so filterWs can reference it safely from the start.
  const noResults=document.createElement('div');
  noResults.className='ws-no-results';
  noResults.textContent=t('ws_no_results')||'No workspaces found';
  noResults.style.display='none';

  function filterWs(term){
    term=(term||'').trim().toLowerCase();
    let visible=0;
    const opts=listContainer.querySelectorAll('.ws-opt');
    for(const opt of opts){
      const name=(opt.dataset.name||'').toLowerCase();
      const path=(opt.dataset.path||'').toLowerCase();
      const show=!term||name.includes(term)||path.includes(term);
      opt.style.display=show?'':'none';
      if(show) visible++;
    }
    noResults.style.display=visible?'none':'';
  }

  function renderList(){
    listContainer.innerHTML='';
    for(const w of sorted){
      const opt=document.createElement('div');
      opt.className='ws-opt'+(w.path===currentWs?' active':'');
      opt.dataset.name=w.name||'';
      opt.dataset.path=w.path||'';
      opt.innerHTML=`<span class="ws-opt-name">${esc(w.name)}</span><span class="ws-opt-path">${esc(w.path)}</span>`;
      opt.onclick=()=>switchToWorkspace(w.path,w.name);
      // ── Space-Default Pin-Button ──
      const spaceDefaultPath = window._activeSpaceConfig?.project_dir || '';
      const isWsDefault = spaceDefaultPath && w.path === spaceDefaultPath;
      opt.classList.toggle('is-space-default', !!isWsDefault);
      const pinBtn = document.createElement('button');
      pinBtn.className = 'ws-pin-btn';
      pinBtn.innerHTML = isWsDefault ? '✅' : '📌';
      pinBtn.title = isWsDefault ? 'Space-Standard-Pfad entfernen' : 'Als Standard-Arbeitspfad für diesen Space';
      pinBtn.onclick = async (e) => {
        e.stopPropagation();
        const slug = typeof _activeSpace !== 'undefined' ? _activeSpace : '';
        if (!slug) return;
        const currentDefault = window._activeSpaceConfig?.project_dir || '';
        const newDefault = (currentDefault === w.path) ? '' : w.path;
        const currentConfig = window._activeSpaceConfig || {};
        await api('/api/space/config', {
          method: 'POST',
          body: JSON.stringify({ slug, project_dir: newDefault, ...(currentConfig.model ? { model: currentConfig.model } : {}) })
        });
        if (!window._activeSpaceConfig) window._activeSpaceConfig = {};
        window._activeSpaceConfig.project_dir = newDefault;
        // Re-render if dropdown is open
        const dd = document.getElementById('composerWsDropdown');
        if (dd && dd.classList.contains('open')) {
          try {
            const data = await api('/api/workspaces');
            if (data && data.workspaces) {
              renderWorkspaceDropdownInto(dd, data.workspaces, currentWs);
            }
          } catch(e) {}
        }
        showToast(newDefault ? '📁 Pfad als Space-Standard gesetzt' : 'Space-Standard-Pfad entfernt');
      };
      opt.insertBefore(pinBtn, opt.firstChild);
      listContainer.appendChild(opt);
    }
    listContainer.appendChild(noResults);
  }

  renderList();
  filterWs('');

  si.addEventListener('input',()=>{ filterWs(si.value); });
  sc.addEventListener('click',()=>{ si.value=''; filterWs(''); si.focus(); });

  // ── Footer actions ────────────────────────────────────────────────────────
  dd.appendChild(document.createElement('div')).className='ws-divider';
  dd.appendChild(_renderWorkspaceAction(
    t('workspace_new_worktree_conversation'),
    t('workspace_new_worktree_conversation_meta'),
    li('git-branch',12),
    async()=>{
      closeWsDropdown();
      try{
        await newSession(false,{worktree:true});
        await renderSessionList();
        const msg=$('msg');
        if(msg)msg.focus();
        showToast(t('workspace_worktree_created'));
      }catch(e){
        showToast(t('workspace_worktree_failed')+(e&&e.message?e.message:e),'error');
      }
    }
  ));
  dd.appendChild(document.createElement('div')).className='ws-divider';
  dd.appendChild(_renderWorkspaceAction(
    t('workspace_choose_path'),
    t('workspace_choose_path_meta'),
    li('folder',12),
    ()=>promptWorkspacePath()
  ));
  const div=document.createElement('div');div.className='ws-divider';dd.appendChild(div);
  dd.appendChild(_renderWorkspaceAction(
    t('workspace_manage'),
    t('workspace_manage_meta'),
    li('settings',12),
    ()=>{closeWsDropdown();mobileSwitchPanel('workspaces');}
  ));
}

function toggleWsDropdown(){
  toggleComposerWsDropdown();
}

function toggleComposerWsDropdown(){
  const dd=$('composerWsDropdown');
  const chip=$('composerWorkspaceChip');
  const mobileAction=$('composerMobileWorkspaceAction');
  const panel=$('composerMobileConfigPanel');
  const usingMobileAction=!!(panel&&panel.classList.contains('open')&&mobileAction);
  if(!dd||(!usingMobileAction&&(!chip||chip.disabled)))return;
  const open=dd.classList.contains('open');
  if(open){closeWsDropdown();}
  else{
    closeProfileDropdown();
    if(typeof closeModelDropdown==='function') closeModelDropdown();
    if(typeof closeReasoningDropdown==='function') closeReasoningDropdown();
    loadWorkspaceList().then(data=>{
      renderWorkspaceDropdownInto(dd, data.workspaces, S.session?S.session.workspace:'');
      dd.classList.add('open');
      _positionComposerWsDropdown();
      if(chip) chip.classList.add('active');
      if(mobileAction) mobileAction.classList.add('active');
    });
  }
}

function workflowOpenWorkspacePanel(event){
  if(event&&event.preventDefault) event.preventDefault();
  if(event&&event.stopPropagation) event.stopPropagation();
  const chip=$('composerWorkspaceChip');
  if(chip && !chip.disabled && typeof toggleComposerWsDropdown==='function'){
    toggleComposerWsDropdown();
    return false;
  }
  if(typeof switchPanel==='function'){
    switchPanel('workspaces',{fromRailClick:true});
    return false;
  }
  return false;
}
if (typeof window !== 'undefined') {
  window.workflowOpenWorkspacePanel = workflowOpenWorkspacePanel;
}

function closeWsDropdown(){
  const composerDd=$('composerWsDropdown');
  const composerChip=$('composerWorkspaceChip');
  const mobileAction=$('composerMobileWorkspaceAction');
  if(composerDd)composerDd.classList.remove('open');
  if(composerChip)composerChip.classList.remove('active');
  if(mobileAction)mobileAction.classList.remove('active');
}
document.addEventListener('click',e=>{
  if(
    !e.target.closest('#composerWorkspaceChip') &&
    !e.target.closest('#composerMobileWorkspaceAction') &&
    !e.target.closest('#composerWsDropdown')
  ) closeWsDropdown();
});
window.addEventListener('resize',()=>{
  const dd=$('composerWsDropdown');
  if(dd&&dd.classList.contains('open')) _positionComposerWsDropdown();
});

async function loadWorkspacesPanel(){
  const panel=$('workspacesPanel');
  if(!panel)return;
  const data=await loadWorkspaceList();
  _workspacesData = data.workspaces;
  const query = ($('workspaceSearch') ? $('workspaceSearch').value || '' : '').toLowerCase();
  renderWorkspacesPanel(query ? _workspacesData.filter(w =>
    (w.name||'').toLowerCase().includes(query) ||
    (w.path||'').toLowerCase().includes(query)
  ) : _workspacesData);
}

function filterWorkspaces() {
  if (_workspacesData) loadWorkspacesPanel();
}

function renderWorkspacesPanel(workspaces){
  const panel=$('workspacesPanel');
  panel.innerHTML='';
  if (!workspaces || !workspaces.length) {
    const empty = document.createElement('div');
    empty.style.cssText='padding:24px 12px;text-align:center;color:var(--muted);font-size:12px;line-height:1.6';
    empty.textContent = ($('workspaceSearch') && $('workspaceSearch').value) ? t('ws_no_results') : t('workspaces_empty_title');
    panel.appendChild(empty);
    _clearWorkspaceDetail();
    return;
  }
  const activePath = S.session ? S.session.workspace : '';
  for(let i=0;i<workspaces.length;i++){
    const w=workspaces[i];
    const row=document.createElement('div');
    row.className='ws-row';
    row.dataset.path = w.path;
    row.draggable=true;
    const isActive = w.path === activePath;
    const activeBadge = isActive ? `<span class="detail-badge active" style="margin-left:6px;font-size:9px;padding:1px 6px">${esc(t('profile_active'))}</span>` : '';
    row.innerHTML=`
      <span class="ws-drag-handle" title="${esc(t('workspace_drag_hint'))}">${li('grip-vertical',12)}</span>
      <div class="ws-row-info">
        <div class="ws-row-name">${esc(w.name)}${activeBadge}</div>
        <div class="ws-row-path">${esc(w.path)}</div>
      </div>`;
    // Click on info area only — not on drag handle
    const info=row.querySelector('.ws-row-info');
    if(info) info.onclick = (e) => { e.stopPropagation(); openWorkspaceDetail(w.path, row); };
    if (_currentWorkspaceDetail && _currentWorkspaceDetail.path === w.path) row.classList.add('active');

    // ── Drag-and-drop reorder ──
    row.addEventListener('dragstart', (e) => {
      // Only allow drag from the grip handle or the row itself
      row.classList.add('dragging');
      e.dataTransfer.effectAllowed='move';
      e.dataTransfer.setData('text/plain', w.path);
      // Required for Firefox drag ghost
      if(e.dataTransfer.setDragImage) e.dataTransfer.setDragImage(row, 0, 0);
    });
    row.addEventListener('dragend', () => {
      row.classList.remove('dragging');
      panel.querySelectorAll('.ws-row.drag-over').forEach(r => r.classList.remove('drag-over'));
    });
    row.addEventListener('dragover', (e) => {
      e.preventDefault();
      e.dataTransfer.dropEffect='move';
      // Highlight drop target
      panel.querySelectorAll('.ws-row.drag-over').forEach(r => r.classList.remove('drag-over'));
      if(!row.classList.contains('dragging')) row.classList.add('drag-over');
    });
    row.addEventListener('dragleave', () => {
      row.classList.remove('drag-over');
    });
    row.addEventListener('drop', async (e) => {
      e.preventDefault();
      row.classList.remove('drag-over');
      const fromPath = e.dataTransfer.getData('text/plain');
      const toPath = w.path;
      if(fromPath === toPath) return; // Same item, no-op
      // Compute new order
      const currentPaths = workspaces.map(ws => ws.path);
      const fromIdx = currentPaths.indexOf(fromPath);
      const toIdx = currentPaths.indexOf(toPath);
      if(fromIdx < 0 || toIdx < 0) return;
      currentPaths.splice(fromIdx, 1);
      currentPaths.splice(toIdx, 0, fromPath);
      try {
        const res = await api('/api/workspaces/reorder', {
          method: 'POST',
          body: JSON.stringify({ paths: currentPaths })
        });
        if(res && res.ok){
          _workspacesData = res.workspaces;
          renderWorkspacesPanel(res.workspaces);
          // Also refresh sidebar dropdown
          loadWorkspaceList().then(() => {});
        }
      } catch(err){
        showToast(t('workspace_reorder_failed'), 'error');
      }
    });

    panel.appendChild(row);
  }
  const hint=document.createElement('div');
  hint.style.cssText='font-size:11px;color:var(--muted);padding:8px 0';
  hint.textContent=t('workspace_paths_validated_hint');
  panel.appendChild(hint);
  // Re-render detail if we have one cached and we're not in a form
  if (_currentWorkspaceDetail && _workspaceMode !== 'create' && _workspaceMode !== 'edit') {
    const refreshed = workspaces.find(w => w.path === _currentWorkspaceDetail.path);
    if (refreshed) _renderWorkspaceDetail(refreshed);
    else _clearWorkspaceDetail();
  }
}

function _renderWorkspaceDetail(ws){
  _currentWorkspaceDetail = ws;
  const title = $('workspaceDetailTitle');
  const body = $('workspaceDetailBody');
  const empty = $('workspaceDetailEmpty');
  if (!title || !body) return;
  title.textContent = ws.name || ws.path;
  const activePath = S.session ? S.session.workspace : '';
  const isActive = ws.path === activePath;
  const isDefault = !!ws.is_default;
  const statusBadge = isActive
    ? `<span class="detail-badge active">${esc(t('profile_active'))}</span>`
    : `<span class="detail-badge">Inactive</span>`;
  const defaultBadge = isDefault ? ` <span class="detail-badge">${esc(t('profile_default_label'))}</span>` : '';
  body.innerHTML = `
    <div class="main-view-content">
      <div class="detail-card">
        <div class="detail-card-title">Space</div>
        <div class="detail-row"><div class="detail-row-label">Name</div><div class="detail-row-value">${esc(ws.name || '')}</div></div>
        <div class="detail-row"><div class="detail-row-label">Path</div><div class="detail-row-value"><code>${esc(ws.path)}</code></div></div>
        <div class="detail-row"><div class="detail-row-label">Status</div><div class="detail-row-value">${statusBadge}${defaultBadge}</div></div>
      </div>
      <div class="detail-card" style="margin-top:12px">
        <div class="detail-card-title">${esc(t('checkpoint_title'))}</div>
        <div id="checkpointListContainer">
          <div style="color:var(--muted);font-size:12px;padding:8px 0">${esc(t('checkpoint_loading'))}</div>
        </div>
      </div>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _workspaceMode = 'read';
  _setWorkspaceHeaderButtons('read', ws);
  _loadCheckpoints(ws.path);
}

function _setWorkspaceHeaderButtons(mode, ws){
  const actBtn = $('btnActivateWorkspaceDetail');
  const editBtn = $('btnEditWorkspaceDetail');
  const delBtn = $('btnDeleteWorkspaceDetail');
  const cancelBtn = $('btnCancelWorkspaceDetail');
  const saveBtn = $('btnSaveWorkspaceDetail');
  const show = b => b && (b.style.display = '');
  const hide = b => b && (b.style.display = 'none');
  if (mode === 'read') {
    const activePath = S.session ? S.session.workspace : '';
    const isActive = ws && ws.path === activePath;
    const isDefault = !!(ws && ws.is_default);
    if (isActive) hide(actBtn); else show(actBtn);
    show(editBtn);
    if (isDefault) hide(delBtn); else show(delBtn);
    hide(cancelBtn); hide(saveBtn);
  } else if (mode === 'create' || mode === 'edit') {
    hide(actBtn); hide(editBtn); hide(delBtn); show(cancelBtn); show(saveBtn);
  } else {
    [actBtn, editBtn, delBtn, cancelBtn, saveBtn].forEach(hide);
  }
}

function openWorkspaceDetail(path, el){
  if (!_workspaceList) return;
  const ws = _workspaceList.find(w => w.path === path);
  if (!ws) return;
  document.querySelectorAll('.ws-row').forEach(e => e.classList.remove('active'));
  const target = el || document.querySelector(`.ws-row[data-path="${CSS.escape(path)}"]`);
  if (target) target.classList.add('active');
  _workspacePreFormDetail = null;
  _renderWorkspaceDetail(ws);
}

function _clearWorkspaceDetail(){
  _currentWorkspaceDetail = null;
  _workspaceMode = 'empty';
  const title = $('workspaceDetailTitle');
  const body = $('workspaceDetailBody');
  const empty = $('workspaceDetailEmpty');
  if (title) title.textContent = '';
  if (body) { body.innerHTML = ''; body.style.display = 'none'; }
  if (empty) empty.style.display = '';
  _setWorkspaceHeaderButtons('empty');
}

async function activateCurrentWorkspace(){
  if (!_currentWorkspaceDetail) return;
  await switchToWorkspace(_currentWorkspaceDetail.path, _currentWorkspaceDetail.name);
  // Re-render detail after activation so the active badge updates
  _renderWorkspaceDetail(_currentWorkspaceDetail);
}

async function deleteCurrentWorkspace(){
  if (!_currentWorkspaceDetail) return;
  const path = _currentWorkspaceDetail.path;
  const _ok = await showConfirmDialog({title:t('workspace_remove_confirm_title'),message:t('workspace_remove_confirm_message',path),confirmLabel:t('remove'),danger:true,focusCancel:true});
  if(!_ok) return;
  try{
    const data=await api('/api/workspaces/remove',{method:'POST',body:JSON.stringify({path})});
    _workspaceList=data.workspaces;
    _clearWorkspaceDetail();
    renderWorkspacesPanel(data.workspaces);
    showToast(t('workspace_removed'));
  }catch(e){setStatus(t('remove_failed')+e.message);}
}

function openWorkspaceCreate(){
  if (typeof switchPanel === 'function' && _currentPanel !== 'workspaces') switchPanel('workspaces');
  _workspacePreFormDetail = _currentWorkspaceDetail ? { ..._currentWorkspaceDetail } : null;
  _workspaceMode = 'create';
  _renderWorkspaceForm({ name:'', path:'', isEdit:false });
}

function editCurrentWorkspace(){
  if (!_currentWorkspaceDetail) return;
  _workspacePreFormDetail = { ..._currentWorkspaceDetail };
  _workspaceMode = 'edit';
  _renderWorkspaceForm({ name: _currentWorkspaceDetail.name || '', path: _currentWorkspaceDetail.path || '', isEdit: true });
}

function _renderWorkspaceForm({ name, path, isEdit }){
  const title = $('workspaceDetailTitle');
  const body = $('workspaceDetailBody');
  const empty = $('workspaceDetailEmpty');
  if (!title || !body) return;
  title.textContent = isEdit ? (t('edit') + ' · ' + (name || path)) : (t('workspace_new_title') || 'New space');
  const pathDisabled = isEdit ? 'disabled' : '';
  const pathHint = isEdit
    ? `<div class="detail-form-hint">${esc(t('workspace_path_readonly') || 'Path cannot be changed. Rename only.')}</div>`
    : `<div class="detail-form-hint">${esc(t('workspace_paths_validated_hint'))}</div>`;
  body.innerHTML = `
    <div class="main-view-content">
      <form class="detail-form" onsubmit="event.preventDefault(); saveWorkspaceForm();">
        <div class="detail-form-row">
          <label for="workspaceFormName">${esc(t('workspace_name_label') || 'Name')}</label>
          <input type="text" id="workspaceFormName" value="${esc(name || '')}" placeholder="${esc(t('workspace_name_placeholder') || 'Optional friendly name')}" autocomplete="off">
        </div>
        <div class="detail-form-row">
          <label for="workspaceFormPath">${esc(t('workspace_path_label') || 'Path')}</label>
          <div class="workspace-form-path-wrap" style="position:relative">
            <input type="text" id="workspaceFormPath" value="${esc(path || '')}" placeholder="${esc(t('workspace_add_path_placeholder') || '/absolute/path/to/folder')}" autocomplete="off" ${pathDisabled} required>
            <div id="workspaceFormPathSuggestions" class="ws-suggestions" style="display:none"></div>
          </div>
          ${pathHint}
        </div>
        <div id="workspaceFormError" class="detail-form-error" style="display:none"></div>
      </form>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _setWorkspaceHeaderButtons(isEdit ? 'edit' : 'create');
  if (!isEdit) _wireWorkspaceFormPathSuggestions();
  const focus = isEdit ? $('workspaceFormName') : $('workspaceFormPath');
  if (focus) focus.focus();
}

function cancelWorkspaceForm(){
  closeWorkspacePathSuggestions();
  if (_workspacePreFormDetail) {
    const snap = _workspacePreFormDetail;
    _workspacePreFormDetail = null;
    _renderWorkspaceDetail(snap);
    return;
  }
  _clearWorkspaceDetail();
}

async function saveWorkspaceForm(){
  const nameEl = $('workspaceFormName');
  const pathEl = $('workspaceFormPath');
  const errEl = $('workspaceFormError');
  if (!pathEl || !errEl) return;
  const name = (nameEl ? nameEl.value : '').trim();
  const path = (pathEl.value || '').trim();
  errEl.style.display = 'none';
  if (!path) { errEl.textContent = t('workspace_path_required') || 'Path is required'; errEl.style.display = ''; return; }
  try {
    if (_workspaceMode === 'edit' && _currentWorkspaceDetail) {
      const targetPath = _currentWorkspaceDetail.path;
      const newName = name || _currentWorkspaceDetail.name || '';
      await api('/api/workspaces/rename', { method:'POST', body: JSON.stringify({ path: targetPath, name: newName }) });
      // Refresh list and re-render detail
      const data = await api('/api/workspaces');
      _workspaceList = data.workspaces || [];
      _workspacePreFormDetail = null;
      showToast(t('workspace_renamed') || t('workspace_added'));
      renderWorkspacesPanel(_workspaceList);
      openWorkspaceDetail(targetPath);
      return;
    }
    const data = await api('/api/workspaces/add', { method:'POST', body: JSON.stringify({ path }) });
    _workspaceList = data.workspaces || [];
    _workspacePreFormDetail = null;
    // Apply rename if a friendly name was supplied
    if (name) {
      try { await api('/api/workspaces/rename', { method:'POST', body: JSON.stringify({ path, name }) }); } catch(_) {}
      const refreshed = await api('/api/workspaces');
      _workspaceList = refreshed.workspaces || _workspaceList;
    }
    renderWorkspacesPanel(_workspaceList);
    showToast(t('workspace_added'));
    const added = _workspaceList.find(w => w.path === path) || _workspaceList[_workspaceList.length - 1];
    if (added) openWorkspaceDetail(added.path);
  } catch (e) {
    errEl.textContent = t('error_prefix') + e.message;
    errEl.style.display = '';
  }
}

// Back-compat: any legacy caller of addWorkspace() opens the new form instead.
function addWorkspace(){ openWorkspaceCreate(); }

function _wireWorkspaceFormPathSuggestions(){
  const input=$('workspaceFormPath');
  if(!input) return;
  input.oninput=()=>scheduleWorkspacePathSuggestions();
  input.onfocus=()=>{
    if(input.value.trim()) scheduleWorkspacePathSuggestions();
    else closeWorkspacePathSuggestions();
  };
  input.onkeydown=(e)=>{
    const box=$('workspaceFormPathSuggestions');
    const items=box?[...box.querySelectorAll('.ws-suggest-item')]:[];
    if(!items.length){
      return;
    }
    if(e.key==='ArrowDown'){
      e.preventDefault();
      _wsSuggestIndex=Math.min(items.length-1,Math.max(-1,_wsSuggestIndex)+1);
      _highlightWorkspaceSuggestion(_wsSuggestIndex);
      return;
    }
    if(e.key==='ArrowUp'){
      e.preventDefault();
      _wsSuggestIndex=_wsSuggestIndex<=0?0:_wsSuggestIndex-1;
      _highlightWorkspaceSuggestion(_wsSuggestIndex);
      return;
    }
    if(e.key==='Escape'){
      e.preventDefault();
      closeWorkspacePathSuggestions();
      return;
    }
    if(e.key==='Enter' && _wsSuggestIndex>=0 && items[_wsSuggestIndex]){
      e.preventDefault();
      _applyWorkspaceSuggestion(items[_wsSuggestIndex].dataset.path||'');
      return;
    }
    if(e.key==='Tab' && _wsSuggestIndex>=0 && items[_wsSuggestIndex]){
      e.preventDefault();
      _applyWorkspaceSuggestion(items[_wsSuggestIndex].dataset.path||'');
      return;
    }
  };
}

document.addEventListener('click',e=>{
  if(!e.target.closest('.workspace-form-path-wrap')) closeWorkspacePathSuggestions();
});

async function removeWorkspace(path){
  const _rmWs=await showConfirmDialog({title:t('workspace_remove_confirm_title'),message:t('workspace_remove_confirm_message',path),confirmLabel:t('remove'),danger:true,focusCancel:true});
  if(!_rmWs) return;
  try{
    const data=await api('/api/workspaces/remove',{method:'POST',body:JSON.stringify({path})});
    _workspaceList=data.workspaces;
    renderWorkspacesPanel(data.workspaces);
    showToast(t('workspace_removed'));
  }catch(e){setStatus(t('remove_failed')+e.message);}
}

async function promptWorkspacePath(){
  // Opus review Q6: if called from blank page (no session), auto-create one first.
  if(!S.session){
    const ws=(typeof S._profileDefaultWorkspace==='string'&&S._profileDefaultWorkspace)||'';
    if(!ws)return;
    try{
      const r=await api('/api/session/new',{method:'POST',body:JSON.stringify({workspace:ws})});
      if(r&&r.session){S.session=r.session;S.messages=[];if(typeof syncTopbar==='function')syncTopbar();if(typeof renderMessages==='function')renderMessages();if(typeof renderSessionList==='function')await renderSessionList();}
    }catch(e){showToast(t('workspace_switch_failed')+e.message);return;}
    if(!S.session)return;
  }
  const value=await showPromptDialog({
    title:t('workspace_switch_prompt_title'),
    message:t('workspace_switch_prompt_message'),
    confirmLabel:t('workspace_switch_prompt_confirm'),
    placeholder:t('workspace_switch_prompt_placeholder'),
    value:S.session.workspace||''
  });
  const path=(value||'').trim();
  if(!path)return;
  try{
    const data=await api('/api/workspaces/add',{method:'POST',body:JSON.stringify({path})});
    _workspaceList=data.workspaces||[];
    const target=_workspaceList[_workspaceList.length-1];
    if(!target) throw new Error(t('workspace_not_added'));
    await switchToWorkspace(target.path,target.name);
  }catch(e){
    if(String(e.message||'').includes('Workspace already in list')){
      showToast(t('workspace_already_saved'));
      return;
    }
    showToast(t('workspace_switch_failed')+e.message);
  }
}

async function switchToWorkspace(path,name){
  // Opus review Q6: if called from blank page, auto-create a session bound to
  // the requested workspace so the switch doesn't silently no-op.
  if(!S.session){
    const ws=path||(typeof S._profileDefaultWorkspace==='string'&&S._profileDefaultWorkspace)||'';
    if(!ws){showToast(t('no_workspace'));return;}
    try{
      const r=await api('/api/session/new',{method:'POST',body:JSON.stringify({workspace:ws})});
      if(r&&r.session){S.session=r.session;S.messages=[];if(typeof syncTopbar==='function')syncTopbar();if(typeof renderMessages==='function')renderMessages();if(typeof renderSessionList==='function')await renderSessionList();}
    }catch(e){if(typeof setStatus==='function')setStatus(t('switch_failed')+e.message);return;}
    if(!S.session)return;
  }
  if(S.busy){
    showToast(t('workspace_busy_switch'));
    return;
  }
  if(typeof _previewDirty!=='undefined'&&_previewDirty){
    const discard=await showConfirmDialog({
      title:t('discard_file_edits_title'),
      message:t('discard_file_edits_message'),
      confirmLabel:t('discard'),
      danger:true
    });
    if(!discard)return;
    if(typeof cancelEditMode==='function')cancelEditMode();
    if(typeof clearPreview==='function')clearPreview();
  }
  try{
    closeWsDropdown();
    await api('/api/session/update',{method:'POST',body:JSON.stringify({
      session_id:S.session.session_id, workspace:path, model:S.session.model, model_provider:S.session.model_provider||null
    })});
    S.session.workspace=path;
    // Explicit workspace switch = user overriding any pending profile-switch default.
    // Clear the one-shot flag so a subsequent newSession() inherits this choice instead.
    S._profileSwitchWorkspace=null;
    syncTopbar();
    await loadDir('.');
    showToast(t('workspace_switched_to',name||getWorkspaceFriendlyName(path)));
  }catch(e){setStatus(t('switch_failed')+e.message);}
}

// ── Profile panel + dropdown ──
let _profilesCache = null;

async function loadProfilesPanel() {
  const panel = $('profilesPanel');
  if (!panel) return;
  try {
    const data = await api('/api/profiles');
    _profilesCache = data;
    panel.innerHTML = '';
    if (!data.profiles || !data.profiles.length) {
      panel.innerHTML = `<div style="padding:16px;color:var(--muted);font-size:12px">${esc(t('profiles_no_profiles'))}</div>`;
      if (_profileMode !== 'create') _clearProfileDetail();
      return;
    }
    const activeName = (S.activeProfile && data.profiles.some(p => p.name === S.activeProfile))
      ? S.activeProfile
      : (data.active || 'default');
    for (const p of data.profiles) {
      const card = document.createElement('div');
      card.className = 'profile-card';
      card.dataset.name = p.name;
      const meta = [];
      if (p.model) meta.push(p.model.split('/').pop());
      if (p.provider) meta.push(p.provider);
      if (p.skill_count) meta.push(t('profile_skill_count', p.skill_count));
      const gwDot = p.gateway_running
        ? `<span class="profile-opt-badge running" title="${esc(t('profile_gateway_running'))}"></span>`
        : `<span class="profile-opt-badge stopped" title="${esc(t('profile_gateway_stopped'))}"></span>`;
      const isActive = p.name === activeName;
      const activeBadge = isActive ? `<span style="color:var(--link);font-size:10px;font-weight:600;margin-left:6px">${esc(t('profile_active'))}</span>` : '';
      const defaultBadge = p.is_default ? ` <span style="opacity:.5">${esc(t('profile_default_label'))}</span>` : '';
      card.innerHTML = `
        <div class="profile-card-header">
          <div style="min-width:0;flex:1">
            <div class="profile-card-name${isActive ? ' is-active' : ''}">${gwDot}${esc(p.name)}${defaultBadge}${activeBadge}</div>
            ${meta.length ? `<div class="profile-card-meta">${esc(meta.join(' \u00b7 '))}</div>` : `<div class="profile-card-meta">${esc(t('profile_no_configuration'))}</div>`}
          </div>
        </div>`;
      card.onclick = () => openProfileDetail(p.name, card);
      if (_currentProfileDetail && _currentProfileDetail.name === p.name) card.classList.add('active');
      panel.appendChild(card);
    }
    // Re-render detail with fresh data if we have one and we're not in a form
    if (_currentProfileDetail && _profileMode !== 'create') {
      const refreshed = data.profiles.find(p => p.name === _currentProfileDetail.name);
      if (refreshed) _renderProfileDetail(refreshed, data.active);
      else _clearProfileDetail();
    }
  } catch (e) {
    panel.innerHTML = `<div style="color:var(--accent);font-size:12px;padding:12px">${esc(t('error_prefix'))}${esc(e.message)}</div>`;
  }
}

function _renderProfileDetail(p, activeName){
  _currentProfileDetail = p;
  const title = $('profileDetailTitle');
  const body = $('profileDetailBody');
  const empty = $('profileDetailEmpty');
  if (!title || !body) return;
  title.textContent = p.name;
  const isActive = p.name === activeName;
  const isDefault = !!p.is_default;
  const statusBadge = isActive
    ? `<span class="detail-badge active">${esc(t('profile_active'))}</span>`
    : `<span class="detail-badge">Inactive</span>`;
  const defaultBadge = isDefault ? ` <span class="detail-badge">${esc(t('profile_default_label'))}</span>` : '';
  const gwBadge = p.gateway_running
    ? `<span class="detail-badge ok">${esc(t('profile_gateway_running'))}</span>`
    : `<span class="detail-badge">${esc(t('profile_gateway_stopped'))}</span>`;
  const rows = [];
  rows.push(`<div class="detail-row"><div class="detail-row-label">Status</div><div class="detail-row-value">${statusBadge}${defaultBadge}</div></div>`);
  rows.push(`<div class="detail-row"><div class="detail-row-label">Gateway</div><div class="detail-row-value">${gwBadge}</div></div>`);
  if (p.model) rows.push(`<div class="detail-row"><div class="detail-row-label">Model</div><div class="detail-row-value"><code>${esc(p.model)}</code></div></div>`);
  if (p.provider) rows.push(`<div class="detail-row"><div class="detail-row-label">Provider</div><div class="detail-row-value">${esc(p.provider)}</div></div>`);
  if (p.base_url) rows.push(`<div class="detail-row"><div class="detail-row-label">Base URL</div><div class="detail-row-value"><code>${esc(p.base_url)}</code></div></div>`);
  rows.push(`<div class="detail-row"><div class="detail-row-label">API key</div><div class="detail-row-value">${p.has_env ? esc(t('profile_api_keys_configured')) : '<span style="color:var(--muted)">Not configured</span>'}</div></div>`);
  if (typeof p.skill_count === 'number') rows.push(`<div class="detail-row"><div class="detail-row-label">Skills</div><div class="detail-row-value">${esc(t('profile_skill_count', p.skill_count))}</div></div>`);
  if (p.default_workspace) rows.push(`<div class="detail-row"><div class="detail-row-label">Default space</div><div class="detail-row-value"><code>${esc(p.default_workspace)}</code></div></div>`);
  body.innerHTML = `
    <div class="main-view-content">
      <div class="detail-card">
        <div class="detail-card-title">Profile</div>
        ${rows.join('')}
      </div>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _profileMode = 'read';
  _setProfileHeaderButtons('read', p, activeName);
}

function _setProfileHeaderButtons(mode, p, activeName){
  const actBtn = $('btnActivateProfileDetail');
  const delBtn = $('btnDeleteProfileDetail');
  const cancelBtn = $('btnCancelProfileDetail');
  const saveBtn = $('btnSaveProfileDetail');
  const show = b => b && (b.style.display = '');
  const hide = b => b && (b.style.display = 'none');
  if (mode === 'read') {
    const isActive = p && p.name === activeName;
    const isDefault = !!(p && p.is_default);
    if (isActive) hide(actBtn); else show(actBtn);
    if (isDefault) hide(delBtn); else show(delBtn);
    hide(cancelBtn); hide(saveBtn);
  } else if (mode === 'create') {
    hide(actBtn); hide(delBtn); show(cancelBtn); show(saveBtn);
  } else {
    [actBtn, delBtn, cancelBtn, saveBtn].forEach(hide);
  }
}

function openProfileDetail(name, el){
  if (!_profilesCache || !_profilesCache.profiles) return;
  const p = _profilesCache.profiles.find(x => x.name === name);
  if (!p) return;
  document.querySelectorAll('.profile-card').forEach(e => e.classList.remove('active'));
  const target = el || document.querySelector(`.profile-card[data-name="${CSS.escape(name)}"]`);
  if (target) target.classList.add('active');
  _profilePreFormDetail = null;
  _renderProfileDetail(p, _profilesCache.active);
}

function _clearProfileDetail(){
  _currentProfileDetail = null;
  _profileMode = 'empty';
  const title = $('profileDetailTitle');
  const body = $('profileDetailBody');
  const empty = $('profileDetailEmpty');
  if (title) title.textContent = '';
  if (body) { body.innerHTML = ''; body.style.display = 'none'; }
  if (empty) empty.style.display = '';
  _setProfileHeaderButtons('empty');
}

async function activateCurrentProfile(){
  if (!_currentProfileDetail) return;
  await switchToProfile(_currentProfileDetail.name);
}

async function deleteCurrentProfile(){
  if (!_currentProfileDetail) return;
  const name = _currentProfileDetail.name;
  const _ok = await showConfirmDialog({title:t('profile_delete_confirm_title',name),message:t('profile_delete_confirm_message'),confirmLabel:t('delete_title'),danger:true,focusCancel:true});
  if(!_ok) return;
  try {
    await api('/api/profile/delete', { method: 'POST', body: JSON.stringify({ name }) });
    _invalidateKanbanProfileCache();
    _clearProfileDetail();
    await loadProfilesPanel();
    showToast(t('profile_deleted', name));
  } catch (e) { showToast(t('delete_failed') + e.message); }
}

function renderProfileDropdown(data) {
  const dd = $('profileDropdown');
  if (!dd) return;
  dd.innerHTML = '';
  const profiles = data.profiles || [];
  const active = (S.activeProfile && profiles.some(p => p.name === S.activeProfile))
    ? S.activeProfile
    : (data.active || 'default');
  for (const p of profiles) {
    const opt = document.createElement('div');
    opt.className = 'profile-opt' + (p.name === active ? ' active' : '');
    const meta = [];
    if (p.model) meta.push(p.model.split('/').pop());
    if (p.skill_count) meta.push(t('profile_skill_count', p.skill_count));
    const gwDot = `<span class="profile-opt-badge ${p.gateway_running ? 'running' : 'stopped'}"></span>`;
    const checkmark = p.name === active ? ' <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--link)" stroke-width="3" style="vertical-align:-1px"><polyline points="20 6 9 17 4 12"/></svg>' : '';
    const defaultBadge = p.is_default ? ` <span style="opacity:.5;font-weight:400">${esc(t('profile_default_label'))}</span>` : '';
    opt.innerHTML = `<div class="profile-opt-name">${gwDot}${esc(p.name)}${defaultBadge}${checkmark}</div>` +
      (meta.length ? `<div class="profile-opt-meta">${esc(meta.join(' \u00b7 '))}</div>` : '');
    opt.onclick = async () => {
      closeProfileDropdown();
      if (p.name === active) return;
      await switchToProfile(p.name);
    };
    dd.appendChild(opt);
  }
  // Divider + Manage link
  const div = document.createElement('div'); div.className = 'ws-divider'; dd.appendChild(div);
  const mgmt = document.createElement('div'); mgmt.className = 'profile-opt ws-manage';
  mgmt.innerHTML = `${li('settings',12)} ${esc(t('manage_profiles'))}`;
  mgmt.onclick = () => { closeProfileDropdown(); mobileSwitchPanel('profiles'); };
  dd.appendChild(mgmt);
}

function toggleProfileDropdown() {
  const dd = $('profileDropdown');
  if (!dd) return;
  if (dd.classList.contains('open')) { closeProfileDropdown(); return; }
  closeWsDropdown(); // close workspace dropdown if open
  if(typeof closeModelDropdown==='function') closeModelDropdown();
  api('/api/profiles').then(data => {
    renderProfileDropdown(data);
    dd.classList.add('open');
    _positionProfileDropdown();
    const chip=$('profileChip');
    if(chip) chip.classList.add('active');
  }).catch(e => { showToast(t('profiles_load_failed')); });
}

function closeProfileDropdown() {
  const dd = $('profileDropdown');
  if (dd) dd.classList.remove('open');
  const chip=$('profileChip');
  if(chip) chip.classList.remove('active');
}
document.addEventListener('click', e => {
  if (!e.target.closest('#profileChipWrap') && !e.target.closest('#profileDropdown')) closeProfileDropdown();
});
window.addEventListener('resize',()=>{
  const dd=$('profileDropdown');
  if(dd&&dd.classList.contains('open')) _positionProfileDropdown();
});

async function switchToProfile(name) {
  // Profile switches are per-client cookie/TLS scoped, so a running stream in
  // the current session can safely continue while this tab moves to another
  // profile. The in-flight session stays attached to its original profile.

  // ── Loading indicator ───────────────────────────────────────────────────
  // Show spinner on the profile chip immediately so the user gets visual
  // feedback while the async switch is in progress.
  const _chip = $('profileChip');
  const _chipLabel = $('profileChipLabel');
  const _prevProfileName = S.activeProfile || 'default';
  if (_chip) { _chip.classList.add('switching'); _chip.disabled = true; }
  // Optimistic name update — shows the target name right away
  if (_chipLabel) _chipLabel.textContent = name;

  // Determine whether the current session has any messages.
  // A session with messages is "in progress" and belongs to the current profile —
  // we must not retag it.  We'll start a fresh session for the new profile instead.
  const sessionInProgress = S.session && (
    (S.messages && S.messages.length > 0) ||
    S.session.active_stream_id ||
    S.session.pending_user_message
  );

  try {
    const data = await api('/api/profile/switch', { method: 'POST', body: JSON.stringify({ name }) });
    S.activeProfile = data.active || name;

    // Update composer placeholder and title bar while the core profile-switch
    // state is still close to the profile API response.
    if (typeof applyBotName === 'function') applyBotName();

    // ── Model + Workspace (parallelized) ───────────────────────────────────
    // populateModelDropdown hits /api/models; loadWorkspaceList hits /api/workspaces.
    // They are fully independent — run both simultaneously to cut switch time ~50%.
    if(typeof _clearPersistedModelState==='function') _clearPersistedModelState();
    else localStorage.removeItem('sidekick-webui-model');
    _skillsData = null;
    _workspaceList = null;
    await Promise.all([populateModelDropdown(), loadWorkspaceList()]);

    // ── Apply model ────────────────────────────────────────────────────────
    if (data.default_model) {
      const sel = $('modelSelect');
      const resolved = _applyModelToDropdown(data.default_model, sel, window._activeProvider||null);
      const modelToUse = resolved || data.default_model;
      const modelState = (typeof _modelStateForSelect==='function')
        ? _modelStateForSelect(sel, modelToUse)
        : {model:modelToUse,model_provider:null};
      S._pendingProfileModel = modelToUse;
      S._pendingProfileModelProvider = modelState.model_provider||null;
      // Only patch the in-memory session model if we're NOT about to replace the session
      if (S.session && !sessionInProgress) {
        S.session.model = modelToUse;
        S.session.model_provider = modelState.model_provider||null;
      }
    }

    // ── Apply workspace ────────────────────────────────────────────────────
    if (data.default_workspace) {
      // Always store the persistent profile default — used for blank-page display
      // and workspace auto-bind throughout the session lifecycle (#804, #823).
      S._profileDefaultWorkspace = data.default_workspace;
      // Also set the one-shot flag consumed by newSession() so the first new
      // session after a profile switch inherits this workspace (#424).
      S._profileSwitchWorkspace = data.default_workspace;

      if (S.session && !sessionInProgress) {
        // Empty session (no messages yet) — safe to update it in place
        try {
          await api('/api/session/update', { method: 'POST', body: JSON.stringify({
            session_id: S.session.session_id,
            workspace: data.default_workspace,
            model: S.session.model,
            model_provider: S.session.model_provider||null,
          })});
          S.session.workspace = data.default_workspace;
        } catch (_) {}
      }
    }

    // ── Session ────────────────────────────────────────────────────────────
    _showAllProfiles = false;

    if (sessionInProgress) {
      // The current session has messages and belongs to the previous profile.
      // Start a new session for the new profile so nothing gets cross-tagged.
      await newSession(false);
      // Apply profile default workspace to the newly created session (fixes #424)
      if (S._profileDefaultWorkspace && S.session) {
        try {
          await api('/api/session/update', { method: 'POST', body: JSON.stringify({
            session_id: S.session.session_id,
            workspace: S._profileDefaultWorkspace,
            model: S.session.model,
            model_provider: S.session.model_provider||null,
          })});
          S.session.workspace = S._profileDefaultWorkspace;
        } catch (_) {}
      }
      // Keep topbar chips (workspace/profile) in sync after creating the
      // new profile-scoped session.
      syncTopbar();
      await renderSessionList();
      showToast(t('profile_switched_new_conversation', name));
    } else {
      // No messages yet — just refresh the list and topbar in place
      await renderSessionList();
      syncTopbar();
      // Refresh workspace file tree so the right panel shows the new
      // profile's workspace, not the previous one (#1214).
      if (S.session && S.session.workspace) loadDir('.');
      showToast(t('profile_switched', name));
    }

    // ── Sidebar panels ─────────────────────────────────────────────────────
    if (_currentPanel === 'skills') await loadSkills();
    if (_currentPanel === 'memory') await loadMemory();
    if (_currentPanel === 'tasks') await loadCrons();
    if (_currentPanel === 'kanban') await loadKanban();
    if (_currentPanel === 'profiles') await loadProfilesPanel();
    if (_currentPanel === 'workspaces') await loadWorkspacesPanel();

  } catch (e) {
    // Revert the optimistic name update on error
    if (_chipLabel) _chipLabel.textContent = _prevProfileName;
    showToast(t('switch_failed') + e.message);
  } finally {
    // Always remove loading indicator regardless of success or failure
    if (_chip) { _chip.classList.remove('switching'); _chip.disabled = false; }
  }
}

function openProfileCreate(){
  if (typeof switchPanel === 'function' && _currentPanel !== 'profiles') switchPanel('profiles');
  _profilePreFormDetail = _currentProfileDetail ? { ..._currentProfileDetail } : null;
  _profileMode = 'create';
  _renderProfileForm();
}

function _renderProfileForm(){
  const title = $('profileDetailTitle');
  const body = $('profileDetailBody');
  const empty = $('profileDetailEmpty');
  if (!title || !body) return;
  title.textContent = t('new_profile');
  body.innerHTML = `
    <div class="main-view-content">
      <form class="detail-form" onsubmit="event.preventDefault(); saveProfileForm();">
        <div class="detail-form-row">
          <label for="profileFormName">${esc(t('profile_name_label') || 'Name')}</label>
          <input type="text" id="profileFormName" placeholder="${esc(t('profile_name_placeholder') || 'lowercase, a-z 0-9 hyphens')}" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false" required>
          <div class="detail-form-hint">${esc(t('profile_name_rule') || 'Lowercase letters, numbers, hyphens, underscores only.')}</div>
        </div>
        <div class="detail-form-row">
          <label class="detail-form-check" for="profileFormClone">
            <input type="checkbox" id="profileFormClone"> <span>${esc(t('profile_clone_label') || 'Clone config from active profile')}</span>
          </label>
        </div>
        <div class="detail-form-row">
          <label for="profileFormBaseUrl">${esc(t('profile_base_url_label') || 'Base URL')}</label>
          <input type="text" id="profileFormBaseUrl" placeholder="${esc(t('profile_base_url_placeholder') || 'Optional, e.g. http://localhost:11434')}" autocomplete="off" autocapitalize="none" autocorrect="off" spellcheck="false">
        </div>
        <div class="detail-form-row">
          <label for="profileFormApiKey">${esc(t('profile_api_key_label') || 'API key')}</label>
          <input type="password" id="profileFormApiKey" placeholder="${esc(t('profile_api_key_placeholder') || 'Optional')}" autocomplete="off">
        </div>
        <div id="profileFormError" class="detail-form-error" style="display:none"></div>
      </form>
    </div>`;
  body.style.display = '';
  if (empty) empty.style.display = 'none';
  _setProfileHeaderButtons('create');
  const n = $('profileFormName');
  if (n) n.focus();
}

function cancelProfileForm(){
  if (_profilePreFormDetail) {
    const snap = _profilePreFormDetail;
    _profilePreFormDetail = null;
    const activeName = _profilesCache ? _profilesCache.active : null;
    _renderProfileDetail(snap, activeName);
    return;
  }
  _clearProfileDetail();
}

async function saveProfileForm(){
  const nameEl = $('profileFormName');
  const cloneEl = $('profileFormClone');
  const baseEl = $('profileFormBaseUrl');
  const apiKeyEl = $('profileFormApiKey');
  const errEl = $('profileFormError');
  if (!nameEl || !errEl) return;
  const name = (nameEl.value || '').trim().toLowerCase();
  const cloneConfig = !!(cloneEl && cloneEl.checked);
  errEl.style.display = 'none';
  if (!name) { errEl.textContent = t('name_required'); errEl.style.display = ''; return; }
  if (!/^[a-z0-9][a-z0-9_-]{0,63}$/.test(name)) { errEl.textContent = t('profile_name_rule'); errEl.style.display = ''; return; }
  const baseUrl = (baseEl ? (baseEl.value || '') : '').trim();
  const apiKey = (apiKeyEl ? (apiKeyEl.value || '') : '').trim();
  if (baseUrl && !/^https?:\/\//.test(baseUrl)) { errEl.textContent = t('profile_base_url_rule'); errEl.style.display = ''; return; }
  try {
    const payload = { name, clone_config: cloneConfig };
    if (baseUrl) payload.base_url = baseUrl;
    if (apiKey) payload.api_key = apiKey;
    await api('/api/profile/create', { method: 'POST', body: JSON.stringify(payload) });
    _invalidateKanbanProfileCache();
    _profilePreFormDetail = null;
    await loadProfilesPanel();
    showToast(t('profile_created', name));
    openProfileDetail(name);
  } catch (e) {
    errEl.textContent = e.message || t('create_failed');
    errEl.style.display = '';
  }
}

// Back-compat
const submitProfileCreate = saveProfileForm;
function toggleProfileForm(){ openProfileCreate();
}

async function deleteProfile(name) {
  const _delProf=await showConfirmDialog({title:t('profile_delete_confirm_title',name),message:t('profile_delete_confirm_message'),confirmLabel:t('delete_title'),danger:true,focusCancel:true});
  if(!_delProf) return;
  try {
    await api('/api/profile/delete', { method: 'POST', body: JSON.stringify({ name }) });
    _invalidateKanbanProfileCache();
    await loadProfilesPanel();
    showToast(t('profile_deleted', name));
  } catch (e) { showToast(t('delete_failed') + e.message); }
}

// ── Memory panel ──
async function loadMemory(force) {
  const spaceLoadKey = (typeof _activeSpaceLoadKey === 'function') ? _activeSpaceLoadKey() : '';
  const stillCurrentSpace = () => !spaceLoadKey || typeof isActiveSpaceLoadKey !== 'function' || isActiveSpaceLoadKey(spaceLoadKey);
  const panel = $('memoryPanel');
  try {
    const qs = typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery() : '';
    const data = await api('/api/memory' + qs);
    if (!stillCurrentSpace()) return;
    _memoryData = data;
    if (panel) {
      panel.innerHTML = '';
      for (const s of MEMORY_SECTIONS) {
        const el = document.createElement('button');
        el.type = 'button';
        el.className = 'side-menu-item';
        el.dataset.memoryKey = s.key;
        if (_currentMemorySection === s.key) el.classList.add('active');
        el.innerHTML = `${li(s.iconKey,16)}<span>${esc(t(s.labelKey))}</span>`;
        // Subtitle-Container für mtime
        const sub = document.createElement('span');
        sub.className = 'mem-subtitle';
        sub.id = 'memSubtitle-' + s.key;
        el.appendChild(sub);
        el.onclick = () => openMemorySection(s.key, el);
        panel.appendChild(el);
      }
      // Sidebar-Status-Dots und Rightpanel-Stats aktualisieren
      _memorySidebarDots();
      if (typeof _updateMemoryToolsStats === 'function') _updateMemoryToolsStats();
    }
    if (_currentMemorySection && _memoryMode !== 'edit') {
      if (_currentMemorySection === 'supermemory') {
        _renderSupermemoryView();
      } else if (_currentMemorySection === 'hybrid') {
        _renderHybridView();
      } else {
        _renderMemoryDetail(_currentMemorySection);
      }
    }
  } catch(e) {
    if (!stillCurrentSpace()) return;
    if (panel) panel.innerHTML = `<div style="padding:12px;color:var(--accent);font-size:12px">${esc(t('error_prefix'))}${esc(e.message)}</div>`;
  }
}



// ── Settings panel ───────────────────────────────────────────────────────────

let _settingsDirty = false;
let _settingsThemeOnOpen = null; // track theme at open time for discard revert
let _settingsSkinOnOpen = null; // track skin at open time for discard revert
let _settingsFontSizeOnOpen = null; // track font size at open time for discard revert
let _settingsSidekickDefaultModelOnOpen = '';
let _settingsSection = 'conversation';
let _currentSettingsSection = 'conversation';
let _settingsAppearanceAutosaveTimer = null;
let _settingsAppearanceAutosaveRetryPayload = null;
let _settingsPreferencesAutosaveTimer = null;
let _settingsPreferencesAutosaveRetryPayload = null;

function switchSettingsSection(name){
  const section=(name==='appearance'||name==='preferences'||name==='providers'||name==='plugins'||name==='system')?name:'conversation';
  _settingsSection=section;
  _currentSettingsSection=section;
  const map={conversation:'Conversation',appearance:'Appearance',preferences:'Preferences',providers:'Providers',plugins:'Plugins',system:'System'};
  // Sidebar menu items
  document.querySelectorAll('#settingsMenu .side-menu-item').forEach(it=>{
    it.classList.toggle('active', it.dataset.settingsSection===section);
  });
  // Panes in main
  ['conversation','appearance','preferences','providers','plugins','system'].forEach(key=>{
    const pane=$('settingsPane'+map[key]);
    if(pane) pane.classList.toggle('active', key===section);
  });
  // Sync mobile dropdown
  const dd=$('settingsSectionDropdown');
  if(dd && dd.value!==section) dd.value=section;
  // Lazy-load integration panels when their tabs are opened
  if(section==='providers') loadProvidersPanel();
  if(section==='plugins') loadPluginsPanel();
  if(section==='system'){loadMcpServers();loadMcpTools();loadGatewayStatus();loadSubagentStatus();}
}

function _syncSidekickPanelSessionActions(){
  const hasSession=!!S.session;
  const visibleMessages=hasSession?(S.messages||[]).filter(m=>m&&m.role&&m.role!=='tool').length:0;
  const title=hasSession
    ? ((S.session.title && S.session.title !== 'Untitled') ? S.session.title : (typeof t === 'function' ? t('new_chat') : 'New chat'))
    : t('active_conversation_none');
  const meta=$('sidekickSessionMeta');
  if(meta){
    meta.textContent=hasSession
      ? t('active_conversation_meta', title, visibleMessages)
      : t('active_conversation_none');
  }
  const setDisabled=(id,disabled)=>{
    const el=$(id);
    if(!el)return;
    el.disabled=!!disabled;
    el.classList.toggle('disabled',!!disabled);
  };
  setDisabled('btnDownload',!hasSession||visibleMessages===0);
  setDisabled('btnExportJSON',!hasSession);
  setDisabled('btnClearConvModal',!hasSession||visibleMessages===0);
}

// Thin wrapper: settings now live in the main content area. External callers
// (keyboard shortcuts, commands) keep working through this name.
function toggleSettings(){
  if(_currentPanel==='settings'){
    _closeSettingsPanel();
  } else {
    switchPanel('settings');
  }
}

function _resetSettingsPanelState(){
  const bar=$('settingsUnsavedBar');
  if(bar) bar.style.display='none';
  _setAppearanceAutosaveStatus('');
}

function _hideSettingsPanel(){
  _resetSettingsPanelState();
  const target = _consumeSettingsTargetPanel('chat');
  if(_currentPanel==='settings') switchPanel(target, {bypassSettingsGuard:true});
}

// Close with unsaved-changes check. If dirty, show a confirm dialog.
function _closeSettingsPanel(){
  if(!_settingsDirty){
    _revertSettingsPreview();
    _hideSettingsPanel();
    return;
  }
  _pendingSettingsTargetPanel = _pendingSettingsTargetPanel || 'chat';
  _showSettingsUnsavedBar();
}

// Revert live DOM/localStorage to what they were when the panel opened
function _revertSettingsPreview(){
  // Appearance controls autosave immediately. Closing/discarding the settings
  // panel must not roll back theme, skin, or font-size after the user sees the
  // inline saved state.
}

// Show the "Unsaved changes" bar inside the settings panel
function _showSettingsUnsavedBar(){
  let bar = $('settingsUnsavedBar');
  if(bar){ bar.style.display=''; return; }
  // Create it
  bar = document.createElement('div');
  bar.id = 'settingsUnsavedBar';
  bar.style.cssText = 'display:flex;align-items:center;justify-content:space-between;gap:8px;background:rgba(233,69,96,.12);border:1px solid rgba(233,69,96,.3);border-radius:8px;padding:10px 14px;margin:0 0 12px;font-size:13px;';
  bar.innerHTML = `<span style="color:var(--text)">${esc(t('settings_unsaved_changes'))}</span>`
    + '<span style="display:flex;gap:8px">'
    + `<button onclick="_discardSettings()" style="padding:5px 12px;border-radius:6px;border:1px solid var(--border2);background:rgba(255,255,255,.06);color:var(--muted);cursor:pointer;font-size:12px;font-weight:600">${esc(t('discard'))}</button>`
    + `<button onclick="saveSettings(true)" style="padding:5px 12px;border-radius:6px;border:none;background:var(--accent);color:#fff;cursor:pointer;font-size:12px;font-weight:600">${esc(t('save'))}</button>`
    + '</span>';
  const body = document.querySelector('#mainSettings .settings-main') || document.querySelector('.settings-main');
  if(body) body.prepend(bar);
}

function _discardSettings(){
  _revertSettingsPreview();
  _settingsDirty = false;
  _hideSettingsPanel();
}

// Mark settings as dirty whenever anything changes
function _markSettingsDirty(){
  _settingsDirty = true;
}

// Apply TTS enabled state: toggles a body class so the CSS rule
// `body.tts-enabled .msg-tts-btn` shows/hides the speaker icon. We toggle the
// body class instead of writing inline `style.display` because the parent
// `.msg-action-btn` has no display rule, so clearing the inline style let the
// `.msg-tts-btn{display:none;}` cascade re-hide the button (#1409).
function _applyTtsEnabled(enabled){
  document.body.classList.toggle('tts-enabled', !!enabled);
}

function _appearancePayloadFromUi(){
  return {
    theme: ($('settingsTheme')||{}).value || localStorage.getItem('sidekick-theme') || 'dark',
    skin: ($('settingsSkin')||{}).value || localStorage.getItem('sidekick-skin') || 'default',
    font_size: ($('settingsFontSize')||{}).value || localStorage.getItem('sidekick-font-size') || 'default',
    session_jump_buttons: !!($('settingsSessionJumpButtons')||{}).checked,
    session_endless_scroll: !!($('settingsSessionEndlessScroll')||{}).checked,
  };
}

function _setAppearanceAutosaveStatus(state){
  const el=$('settingsAppearanceAutosaveStatus');
  if(!el) return;
  el.className='settings-autosave-status';
  if(!state){
    el.textContent='';
    return;
  }
  el.classList.add('is-'+state);
  if(state==='saving'){
    el.textContent=t('settings_autosave_saving');
  }else if(state==='saved'){
    el.textContent=t('settings_autosave_saved');
  }else if(state==='failed'){
    el.innerHTML=`<span>${esc(t('settings_autosave_failed'))}</span> <button type="button" onclick="_retryAppearanceAutosave()">${esc(t('settings_autosave_retry'))}</button>`;
  }
}

function _rememberAppearanceSaved(payload){
  if(!payload) return;
  _settingsThemeOnOpen=payload.theme||localStorage.getItem('sidekick-theme')||'dark';
  _settingsSkinOnOpen=payload.skin||localStorage.getItem('sidekick-skin')||'default';
  _settingsFontSizeOnOpen=payload.font_size||localStorage.getItem('sidekick-font-size')||'default';
}

function _scheduleAppearanceAutosave(){
  const payload=_appearancePayloadFromUi();
  // Keep discard/close behavior aligned with the new mental model: appearance
  // changes are committed immediately instead of treated as preview-only edits.
  _rememberAppearanceSaved(payload);
  _settingsAppearanceAutosaveRetryPayload=payload;
  _setAppearanceAutosaveStatus('saving');
  if(_settingsAppearanceAutosaveTimer) clearTimeout(_settingsAppearanceAutosaveTimer);
  _settingsAppearanceAutosaveTimer=setTimeout(()=>_autosaveAppearanceSettings(payload),350);
}

async function _autosaveAppearanceSettings(payload){
  try{
    const saved=await api('/api/settings',{method:'POST',body:JSON.stringify(payload)});
    _settingsAppearanceAutosaveRetryPayload=null;
    _rememberAppearanceSaved(payload);
    if(saved&&saved.font_size){
      localStorage.setItem('sidekick-font-size',saved.font_size);
    }
    if(saved){
      window._sessionJumpButtonsEnabled=!!saved.session_jump_buttons;
      if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
    }
    window._sessionEndlessScrollEnabled=!!(saved&&saved.session_endless_scroll);
    _setAppearanceAutosaveStatus('saved');
  }catch(e){
    console.warn('[settings] appearance autosave failed', e);
    _setAppearanceAutosaveStatus('failed');
  }
}

function _retryAppearanceAutosave(){
  const payload=_settingsAppearanceAutosaveRetryPayload||_appearancePayloadFromUi();
  _setAppearanceAutosaveStatus('saving');
  _autosaveAppearanceSettings(payload);
}

// ── Phase 2: Preferences autosave (Issue #1003) ───────────────────────

function syncGameModeButton(){
  if(typeof window._gameModeEnabled==='undefined'){
    try{
      const raw=localStorage.getItem('sidekick-game-mode-enabled');
      if(raw==='1'||raw==='true') window._gameModeEnabled=true;
      else if(raw==='0'||raw==='false') window._gameModeEnabled=false;
    }catch(_){}
  }
  const enabled=window._gameModeEnabled===true;
  const btn=$('btnGameModeToggle');
  if(btn){
    btn.classList.toggle('active',enabled);
    btn.setAttribute('aria-pressed',String(enabled));
    const label=typeof t==='function'
      ? t(enabled?'game_mode_on':'game_mode_off')
      : (enabled ? 'Game mode on' : 'Game mode off');
    btn.setAttribute('data-i18n-aria-label',enabled?'game_mode_on':'game_mode_off');
    btn.setAttribute('data-i18n-title',enabled?'game_mode_on':'game_mode_off');
    btn.setAttribute('title',label);
    btn.setAttribute('aria-label',label);
  }
  const cb=$('settingsGameModeEnabled');
  if(cb) cb.checked=enabled;
  document.documentElement.classList.toggle('game-mode-enabled',enabled);
}

function _persistGameModeUiState(enabled){
  try{localStorage.setItem('sidekick-game-mode-enabled',enabled?'1':'0');}catch(_){}
}

function _gameModeGpuUserLabel(item){
  if(!item||typeof item!=='object') return '';
  const name=String(item.process||'unknown').trim()||'unknown';
  const mb=Number(item.used_gpu_memory_mb||0);
  const mem=mb>=1024?`${(mb/1024).toFixed(1)} GB`:`${Math.max(1,Math.round(mb))} MB`;
  return `${name} ${mem}`;
}

function _gameModeGpuUsersSummary(snapshot, key){
  if(!snapshot||snapshot.available!==true) return '';
  const source=Array.isArray(snapshot[key])?snapshot[key]:[];
  const labels=source.slice(0,3).map(_gameModeGpuUserLabel).filter(Boolean);
  return labels.join(', ');
}

function _gameModeReleaseSummary(release){
  if(!release||typeof release!=='object') return '';
  const parts=[];
  const cancelled=Array.isArray(release.cancelled_local_streams)?release.cancelled_local_streams.length:0;
  const unloaded=release.ollama&&Array.isArray(release.ollama.unloaded)?release.ollama.unloaded.filter(item=>!item||item.ok!==false).length:0;
  const servers=Array.isArray(release.local_model_servers)?release.local_model_servers.filter(item=>item&&item.ok!==false&&!item.skipped).length:0;
  const image=release.image_generation_queue||{};
  const imageTerminated=Array.isArray(image.terminated)?image.terminated.filter(item=>item&&item.ok!==false&&!item.skipped).length:0;
  const queueSkipped=Array.isArray(image.queues)?image.queues.filter(item=>item&&item.flush&&item.flush.skipped).length:0;
  const gpuAfter=release.gpu_processes&&release.gpu_processes.after;
  const remainingGpu=_gameModeGpuUsersSummary(gpuAfter,'non_sidekick_top');
  const localGpu=_gameModeGpuUsersSummary(gpuAfter,'local_gpu_workloads');
  const remainingSuffix=remainingGpu?` Top remaining GPU users: ${remainingGpu}.`:'';
  if(cancelled) parts.push(`${cancelled} stream${cancelled===1?'':'s'} cancelled`);
  if(unloaded) parts.push(`${unloaded} Ollama model${unloaded===1?'':'s'} unloaded`);
  if(servers) parts.push(`${servers} local model server${servers===1?'':'s'} stopped`);
  if(imageTerminated) parts.push(`${imageTerminated} image queue process${imageTerminated===1?'':'es'} stopped`);
  if(parts.length) return ` Released: ${parts.join(', ')}.${remainingSuffix}`;
  if(localGpu) return ` Local GPU workload still detected: ${localGpu}.${remainingSuffix}`;
  if(queueSkipped||(gpuAfter&&gpuAfter.available===true)) return ` No Sidekick local GPU processes found.${remainingSuffix}`;
  return '';
}

async function toggleGameMode(){
  const previous=window._gameModeEnabled===true;
  const next=!previous;
  window._gameModeEnabled=next;
  syncGameModeButton();
  try{
    const saved=await api('/api/settings',{method:'POST',body:JSON.stringify({game_mode_enabled:next})});
    window._gameModeEnabled=!!(saved&&saved.game_mode_enabled);
    _persistGameModeUiState(window._gameModeEnabled);
    syncGameModeButton();
    if(typeof showToast==='function'){
      let message=t(window._gameModeEnabled?'game_mode_enabled_toast':'game_mode_disabled_toast');
      if(window._gameModeEnabled) message+=_gameModeReleaseSummary(saved&&saved.game_mode_release);
      showToast(message,window._gameModeEnabled?5000:undefined);
    }
  }catch(e){
    window._gameModeEnabled=previous;
    syncGameModeButton();
    if(typeof showToast==='function') showToast(t('settings_save_failed')+(e&&e.message?e.message:e));
  }
}

window.syncGameModeButton=syncGameModeButton;
window.toggleGameMode=toggleGameMode;
document.addEventListener('DOMContentLoaded',syncGameModeButton);

function _preferencesPayloadFromUi(){
  const payload={};
  const sendKeySel=$('settingsSendKey');
  if(sendKeySel) payload.send_key=sendKeySel.value;
  const langSel=$('settingsLanguage');
  if(langSel) payload.language=langSel.value;
  const gameModeCb=$('settingsGameModeEnabled');
  if(gameModeCb) payload.game_mode_enabled=gameModeCb.checked;
  const showUsageCb=$('settingsShowTokenUsage');
  if(showUsageCb) payload.show_token_usage=showUsageCb.checked;
  const showTpsCb=$('settingsShowTps');
  if(showTpsCb) payload.show_tps=showTpsCb.checked;
  const simplifiedToolCb=$('settingsSimplifiedToolCalling');
  if(simplifiedToolCb) payload.simplified_tool_calling=simplifiedToolCb.checked;
  const apiRedactCb=$('settingsApiRedact');
  if(apiRedactCb) payload.api_redact_enabled=apiRedactCb.checked;
  const showCliCb=$('settingsShowCliSessions');
  if(showCliCb) payload.show_cli_sessions=showCliCb.checked;
  const showOpenrouterPaidCb=$('settingsShowOpenrouterPaid');
  if(showOpenrouterPaidCb) payload.show_openrouter_paid=showOpenrouterPaidCb.checked;
  const syncCb=$('settingsSyncInsights');
  if(syncCb) payload.sync_to_insights=syncCb.checked;
  const updateCb=$('settingsCheckUpdates');
  if(updateCb) payload.check_for_updates=updateCb.checked;
  const soundCb=$('settingsSoundEnabled');
  if(soundCb) payload.sound_enabled=soundCb.checked;
  const notifCb=$('settingsNotificationsEnabled');
  if(notifCb) payload.notifications_enabled=notifCb.checked;
  const sidebarDensitySel=$('settingsSidebarDensity');
  if(sidebarDensitySel) payload.sidebar_density=sidebarDensitySel.value;
  const autoTitleRefreshSel=$('settingsAutoTitleRefresh');
  if(autoTitleRefreshSel) payload.auto_title_refresh_every=parseInt(autoTitleRefreshSel.value,10);
  const busyInputModeSel=$('settingsBusyInputMode');
  if(busyInputModeSel) payload.busy_input_mode=busyInputModeSel.value;
  const botNameField=$('settingsBotName');
  if(botNameField) payload.bot_name=botNameField.value;
  return payload;
}

function _setPreferencesAutosaveStatus(state){
  const el=$('settingsPreferencesAutosaveStatus');
  if(!el) return;
  el.className='settings-autosave-status';
  if(!state){
    el.textContent='';
    return;
  }
  el.classList.add('is-'+state);
  if(state==='saving'){
    el.textContent=t('settings_autosave_saving');
  }else if(state==='saved'){
    el.textContent=t('settings_autosave_saved');
  }else if(state==='failed'){
    el.innerHTML=`<span>${esc(t('settings_autosave_failed'))}</span> <button type=\"button\" onclick=\"_retryPreferencesAutosave()\">${esc(t('settings_autosave_retry'))}</button>`;
  }
}

function _rememberPreferencesSaved(payload){
  if(!payload) return;
  if(payload.send_key!==undefined) localStorage.setItem('sidekick-pref-send_key',payload.send_key);
  if(payload.language!==undefined) localStorage.setItem('sidekick-pref-language',payload.language);
}

function _schedulePreferencesAutosave(){
  const payload=_preferencesPayloadFromUi();
  _rememberPreferencesSaved(payload);
  _settingsPreferencesAutosaveRetryPayload=payload;
  _setPreferencesAutosaveStatus('saving');
  if(_settingsPreferencesAutosaveTimer) clearTimeout(_settingsPreferencesAutosaveTimer);
  _settingsPreferencesAutosaveTimer=setTimeout(()=>_autosavePreferencesSettings(payload),350);
}

async function _autosavePreferencesSettings(payload){
  try{
    const saved=await api('/api/settings',{method:'POST',body:JSON.stringify(payload)});
    if(payload&&payload.simplified_tool_calling!==undefined){
      window._simplifiedToolCalling=(saved&&saved.simplified_tool_calling!==false);
      if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
      if(typeof renderMessages==='function') renderMessages();
    }
    if(payload&&payload.show_tps!==undefined){
      window._showTps=!!(saved&&saved.show_tps);
      if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
      if(typeof renderMessages==='function') renderMessages();
    }
    if(payload&&payload.game_mode_enabled!==undefined){
      window._gameModeEnabled=!!(saved&&saved.game_mode_enabled);
      syncGameModeButton();
    }
    _settingsPreferencesAutosaveRetryPayload=null;
    _setPreferencesAutosaveStatus('saved');
    // Only clear the global dirty flag and hide the unsaved-changes bar when
    // there is no pending edit on a manually-saved field. Password and model
    // are still committed via the explicit "Save Settings" button (password
    // for security; model goes through /api/default-model). Without this
    // guard, autosaving a checkbox right after a user typed in the password
    // field would silently dismiss the password edit. (Opus pre-release
    // review of v0.50.250, SHOULD-FIX Q1.)
    const pwField=$('settingsPassword');
    const pwDirty=!!(pwField&&pwField.value);
    const modelSel=$('settingsModel');
    const modelDirty=!!(modelSel&&((modelSel.value||'')!==(_settingsSidekickDefaultModelOnOpen||'')));
    if(!pwDirty&&!modelDirty){
      _settingsDirty=false;
      const bar=$('settingsUnsavedBar');
      if(bar) bar.style.display='none';
    }
  }catch(e){
    console.warn('[settings] preferences autosave failed', e);
    _setPreferencesAutosaveStatus('failed');
  }
}

function _retryPreferencesAutosave(){
  const payload=_settingsPreferencesAutosaveRetryPayload||_preferencesPayloadFromUi();
  _setPreferencesAutosaveStatus('saving');
  _autosavePreferencesSettings(payload);
}

function _setPreferencesControlsBusy(busy){
  const pane=$('settingsPanePreferences');
  if(!pane) return;
  pane.classList.toggle('settings-pane-loading',!!busy);
  pane.setAttribute('aria-busy',String(!!busy));
  pane.querySelectorAll('input,select,textarea,button').forEach(el=>{
    if(busy){
      if(!el.disabled){
        el.dataset.settingsLoadingDisabled='1';
        el.disabled=true;
      }
    }else if(el.dataset.settingsLoadingDisabled==='1'){
      el.disabled=false;
      delete el.dataset.settingsLoadingDisabled;
    }
  });
}

async function loadSettingsPanel(){
  _setPreferencesControlsBusy(true);
  try{
    const settings=await api('/api/settings');
    // Populate the version badges from the server — keeps them in sync with git
    // tags automatically without any manual release step.
    const webuiBadge = $('settings-webui-version-badge');
    if(webuiBadge){
      webuiBadge.textContent = `WebUI: ${settings.webui_version || 'not detected'}`;
    }
    const agentBadge = $('settings-agent-version-badge');
    if(agentBadge){
      const agentVersion = (settings.agent_version || 'not detected').toString().trim() || 'not detected';
      agentBadge.textContent = `Agent: ${agentVersion}`;
    }
    // Hydrate appearance controls first so a slow /api/models request
    // cannot overwrite an in-progress theme/skin selection.
    const themeSel=$('settingsTheme');
    const themeVal=settings.theme||'dark';
    if(themeSel) themeSel.value=themeVal;
    if(typeof _syncThemePicker==='function') _syncThemePicker(themeVal);
    const skinVal=(settings.skin||'default').toLowerCase();
    const skinSel=$('settingsSkin');
    if(skinSel) skinSel.value=skinVal;
    if(typeof _buildSkinPicker==='function') _buildSkinPicker(skinVal);
    const fontSizeVal=settings.font_size||localStorage.getItem('sidekick-font-size')||'default';
    localStorage.setItem('sidekick-font-size',fontSizeVal);
    if(typeof _applyFontSize==='function') _applyFontSize(fontSizeVal);
    const fontSizeSel=$('settingsFontSize');
    if(fontSizeSel) fontSizeSel.value=fontSizeVal;
    if(typeof _syncFontSizePicker==='function') _syncFontSizePicker(fontSizeVal);
    const syntaxThemeVal=localStorage.getItem('sidekick-syntax-theme')||'';
    const syntaxThemeSel=$('settingsSyntaxTheme');
    if(syntaxThemeSel) syntaxThemeSel.value=syntaxThemeVal;
    if(typeof _syncSyntaxThemePicker==='function') _syncSyntaxThemePicker(syntaxThemeVal);
    const jumpButtonsCb=$('settingsSessionJumpButtons');
    if(jumpButtonsCb){
      jumpButtonsCb.checked=!!settings.session_jump_buttons;
      window._sessionJumpButtonsEnabled=jumpButtonsCb.checked;
      jumpButtonsCb.onchange=function(){
        window._sessionJumpButtonsEnabled=this.checked;
        if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
        _scheduleAppearanceAutosave();
      };
    }
    if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
    // File tree panel default-open toggle (localStorage-backed)
    const wsPanelCb=$('settingsWorkspacePanelOpen');
    if(wsPanelCb){
      wsPanelCb.checked=localStorage.getItem('sidekick-webui-workspace-panel-pref')!=='closed';
      wsPanelCb.onchange=function(){
        const open=this.checked;
        localStorage.setItem('sidekick-webui-workspace-panel-pref',open?'open':'closed');
        // Toggle the file tree panel in chat to match preference
        const panel=$('chatFileTreePanel');
        if(panel){
          if(open&&panel.classList.contains('file-tree-panel--minimized')){
            if(typeof window.toggleFileTreePanel==='function') window.toggleFileTreePanel();
          }else if(!open&&!panel.classList.contains('file-tree-panel--minimized')){
            if(typeof window.toggleFileTreePanel==='function') window.toggleFileTreePanel();
          }
        }
      };
    }
    const endlessScrollCb=$('settingsSessionEndlessScroll');
    if(endlessScrollCb){
      endlessScrollCb.checked=!!settings.session_endless_scroll;
      window._sessionEndlessScrollEnabled=endlessScrollCb.checked;
      endlessScrollCb.onchange=function(){
        window._sessionEndlessScrollEnabled=this.checked;
        _scheduleAppearanceAutosave();
      };
    }
    const resolvedLanguage=(typeof resolvePreferredLocale==='function')
      ? resolvePreferredLocale(settings.language, localStorage.getItem('sidekick-lang'))
      : (settings.language || localStorage.getItem('sidekick-lang') || 'en');
    // Keep settings modal and current page strings in sync with the resolved locale.
    if(typeof setLocale==='function'){
      setLocale(resolvedLanguage);
      if(typeof applyLocaleToDOM==='function') applyLocaleToDOM();
    }
    // Populate model dropdown from /api/models + live model fetch (#872)
    const modelSel=$('settingsModel');
    if(modelSel){
      modelSel.innerHTML='';
      let models=null;
      try{
        models=await api('/api/models');
        for(const g of ((models||{}).groups||[])){
          const og=document.createElement('optgroup');
          og.label=g.provider;
          if(g.provider_id) og.dataset.provider=g.provider_id;
          for(const m of g.models){
            const opt=document.createElement('option');
            opt.value=m.id;opt.textContent=m.label;
            og.appendChild(opt);
          }
          modelSel.appendChild(og);
        }
        // Append live-fetched models for the active provider, same as the
        // chat-header dropdown does via _fetchLiveModels() (#872).
        if(models.active_provider && typeof _fetchLiveModels==='function'){
          _fetchLiveModels(models.active_provider, modelSel);
        }
      }catch(e){}
      _settingsSidekickDefaultModelOnOpen=(models&&models.default_model)||'';
      // Use the smart matcher so a saved bare form like "anthropic/claude-opus-4.6"
      // (what the CLI's `hermes model` command writes) still selects the matching
      // `@nous:anthropic/claude-opus-4.6` option on a Nous setup. Without this, the
      // picker renders blank for any user whose default was persisted without the
      // @-prefix — CLI-first users, legacy installs, etc.
      if(typeof _applyModelToDropdown==='function'){
        _applyModelToDropdown(_settingsSidekickDefaultModelOnOpen, modelSel, (models&&models.active_provider)||window._activeProvider||null);
      }else{
        modelSel.value=_settingsSidekickDefaultModelOnOpen;
      }
      modelSel.addEventListener('change',_markSettingsDirty,{once:false});
    }
    await _loadNovaRouteStatus();
    // Send key preference
    const sendKeySel=$('settingsSendKey');
    if(sendKeySel){sendKeySel.value=settings.send_key||'enter';sendKeySel.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    // Language preference — populate from LOCALES bundle
    const langSel=$('settingsLanguage');
    if(langSel){
      langSel.innerHTML='';
      if(typeof LOCALES!=='undefined'){
        for(const [code,bundle] of Object.entries(LOCALES)){
          const opt=document.createElement('option');
          opt.value=code;opt.textContent=bundle._label||code;
          langSel.appendChild(opt);
        }
      }
      langSel.value=resolvedLanguage;
      langSel.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    window._gameModeEnabled=!!settings.game_mode_enabled;
    syncGameModeButton();
    const gameModeCb=$('settingsGameModeEnabled');
    if(gameModeCb){
      gameModeCb.checked=window._gameModeEnabled;
      gameModeCb.addEventListener('change',function(){
        window._gameModeEnabled=this.checked;
        syncGameModeButton();
        _schedulePreferencesAutosave();
      },{once:false});
    }
    const showUsageCb=$('settingsShowTokenUsage');
    if(showUsageCb){showUsageCb.checked=!!settings.show_token_usage;showUsageCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const showTpsCb=$('settingsShowTps');
    if(showTpsCb){showTpsCb.checked=!!settings.show_tps;showTpsCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const simplifiedToolCb=$('settingsSimplifiedToolCalling');
    if(simplifiedToolCb){simplifiedToolCb.checked=settings.simplified_tool_calling!==false;simplifiedToolCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const apiRedactCb=$('settingsApiRedact');
    if(apiRedactCb){apiRedactCb.checked=settings.api_redact_enabled!==false;apiRedactCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const showCliCb=$('settingsShowCliSessions');
    if(showCliCb){showCliCb.checked=!!settings.show_cli_sessions;showCliCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const showOpenrouterPaidCb=$('settingsShowOpenrouterPaid');
    if(showOpenrouterPaidCb){showOpenrouterPaidCb.checked=!!settings.show_openrouter_paid;showOpenrouterPaidCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const syncCb=$('settingsSyncInsights');
    if(syncCb){syncCb.checked=!!settings.sync_to_insights;syncCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const updateCb=$('settingsCheckUpdates');
    if(updateCb){updateCb.checked=settings.check_for_updates!==false;updateCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    const soundCb=$('settingsSoundEnabled');
    if(soundCb){soundCb.checked=!!settings.sound_enabled;soundCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    // TTS settings (localStorage-only, no server round-trip needed)
    const ttsEnabledCb=$('settingsTtsEnabled');
    if(ttsEnabledCb){ttsEnabledCb.checked=localStorage.getItem('sidekick-tts-enabled')==='true';ttsEnabledCb.onchange=function(){localStorage.setItem('sidekick-tts-enabled',this.checked?'true':'false');_applyTtsEnabled(this.checked);};}
    const ttsAutoReadCb=$('settingsTtsAutoRead');
    if(ttsAutoReadCb){ttsAutoReadCb.checked=localStorage.getItem('sidekick-tts-auto-read')==='true';ttsAutoReadCb.onchange=function(){localStorage.setItem('sidekick-tts-auto-read',this.checked?'true':'false');};}
    // Voice-mode button visibility (#1488). localStorage-only; no server round-trip.
    // Toggling re-applies immediately via the boot.js helper so the user sees
    // the audio-waveform button appear/disappear without a reload.
    const voiceModeCb=$('settingsVoiceModeEnabled');
    if(voiceModeCb){
      voiceModeCb.checked=localStorage.getItem('sidekick-voice-mode-button')==='true';
      voiceModeCb.onchange=function(){
        localStorage.setItem('sidekick-voice-mode-button',this.checked?'true':'false');
        if(typeof window._applyVoiceModePref==='function') window._applyVoiceModePref();
      };
    }
    // Populate voice selector from speechSynthesis
    const ttsVoiceSel=$('settingsTtsVoice');
    if(ttsVoiceSel&&'speechSynthesis' in window){
      const populateVoices=()=>{
        const voices=speechSynthesis.getVoices();
        const current=localStorage.getItem('sidekick-tts-voice')||'';
        ttsVoiceSel.innerHTML='<option value="">Default system voice</option>';
        voices.forEach(v=>{
          const opt=document.createElement('option');
          opt.value=v.name;opt.textContent=v.name+(v.lang?' ('+v.lang+')':'');
          if(v.name===current) opt.selected=true;
          ttsVoiceSel.appendChild(opt);
        });
      };
      populateVoices();
      speechSynthesis.addEventListener('voiceschanged',populateVoices,{once:true});
      ttsVoiceSel.onchange=function(){localStorage.setItem('sidekick-tts-voice',this.value);};
    }
    // TTS rate/pitch sliders
    const ttsRateSlider=$('settingsTtsRate');
    const ttsRateValue=$('settingsTtsRateValue');
    if(ttsRateSlider){
      const savedRate=localStorage.getItem('sidekick-tts-rate');
      ttsRateSlider.value=savedRate||'1';
      if(ttsRateValue) ttsRateValue.textContent=parseFloat(ttsRateSlider.value).toFixed(1)+'x';
      ttsRateSlider.oninput=function(){if(ttsRateValue)ttsRateValue.textContent=parseFloat(this.value).toFixed(1)+'x';localStorage.setItem('sidekick-tts-rate',this.value);};
    }
    const ttsPitchSlider=$('settingsTtsPitch');
    const ttsPitchValue=$('settingsTtsPitchValue');
    if(ttsPitchSlider){
      const savedPitch=localStorage.getItem('sidekick-tts-pitch');
      ttsPitchSlider.value=savedPitch||'1';
      if(ttsPitchValue) ttsPitchValue.textContent=parseFloat(ttsPitchSlider.value).toFixed(1);
      ttsPitchSlider.oninput=function(){if(ttsPitchValue)ttsPitchValue.textContent=parseFloat(this.value).toFixed(1);localStorage.setItem('sidekick-tts-pitch',this.value);};
    }
    const notifCb=$('settingsNotificationsEnabled');
    if(notifCb){notifCb.checked=!!settings.notifications_enabled;notifCb.addEventListener('change',_schedulePreferencesAutosave,{once:false});}
    // show_thinking has no settings panel checkbox — controlled via /reasoning show|hide
    const sidebarDensitySel=$('settingsSidebarDensity');
    if(sidebarDensitySel){
      sidebarDensitySel.value=settings.sidebar_density==='detailed'?'detailed':'compact';
      sidebarDensitySel.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    const autoTitleRefreshSel=$('settingsAutoTitleRefresh');
    if(autoTitleRefreshSel){
      const val=String(settings.auto_title_refresh_every||'0');
      autoTitleRefreshSel.value=['0','5','10','20'].includes(val)?val:'0';
      autoTitleRefreshSel.addEventListener('change',_schedulePreferencesAutosave,{once:false});
    }
    // Busy input mode
    const busyInputModeSel=$('settingsBusyInputMode');
    if(busyInputModeSel){
      const val=String(settings.busy_input_mode||'queue');
      busyInputModeSel.value=['queue','interrupt','steer'].includes(val)?val:'queue';
      busyInputModeSel.addEventListener('change',function(){
        window._busyInputMode=this.value;
        if(typeof updateComposerModeChips==='function') updateComposerModeChips();
        if(typeof updateSendBtn==='function') updateSendBtn();
        _schedulePreferencesAutosave();
      },{once:false});
    }
    // Bot name — debounced autosave (text input)
    const botNameField=$('settingsBotName');
    if(botNameField){
      botNameField.value=settings.bot_name||'Nova';
      let botNameTimer=null;
      botNameField.addEventListener('input',()=>{
        if(botNameTimer) clearTimeout(botNameTimer);
        botNameTimer=setTimeout(_schedulePreferencesAutosave,500);
      },{once:false});
    }
    _setPreferencesControlsBusy(false);
    // Password field: always blank (we don't send hash back)
    const pwField=$('settingsPassword');
    if(pwField){pwField.value='';pwField.addEventListener('input',_markSettingsDirty,{once:false});}
    // #1560: when HERMES_WEBUI_PASSWORD env var is set, the settings password
    // field silently no-ops. Disable it + reveal the lock banner so the UI
    // tells the truth before a user tries (and the backend now also returns
    // 409 as defense-in-depth).
    const pwEnvLocked=!!settings.password_env_var;
    const pwLockBanner=$('settingsPasswordEnvLock');
    if(pwField){
      pwField.disabled=pwEnvLocked;
      if(pwEnvLocked){
        pwField.value='';
        pwField.placeholder=t('password_env_var_locked_placeholder')||pwField.placeholder;
      }
    }
    if(pwLockBanner) pwLockBanner.style.display=pwEnvLocked?'block':'none';
    // Show auth buttons only when auth is active
    try{
      const authStatus=await api('/api/auth/status');
      _setSettingsAuthButtonsVisible(!!authStatus.auth_enabled);
    }catch(e){}
    // #1560: env-var-locked password also disables the Disable Auth button —
    // clearing settings.password_hash is silent no-op when the env var is set,
    // and the backend now returns 409 anyway, so don't offer the action.
    // Sign Out remains available since it only clears the session cookie.
    if(pwEnvLocked){
      const disableBtn=$('btnDisableAuth');
      if(disableBtn) disableBtn.style.display='none';
    }
    _syncSidekickPanelSessionActions();
    if(typeof loadDashboardSettings==='function') loadDashboardSettings();
    if(typeof loadWorktreeSettings==='function') loadWorktreeSettings();
    loadProvidersPanel(); // load provider cards in background
    loadPluginsPanel(); // load plugin/hook visibility in background
    switchSettingsSection(_settingsSection);
  }catch(e){
    _setPreferencesControlsBusy(false);
    showToast(t('settings_load_failed')+e.message);
  }
}


// ── Plugins panel (read-only plugin/hook visibility) ───────────────────────

async function loadPluginsPanel(){
  const list=$('pluginsList');
  const empty=$('pluginsEmpty');
  if(!list) return;
  try{
    const data=await api('/api/plugins');
    const plugins=Array.isArray((data||{}).plugins)?data.plugins:[];
    list.innerHTML='';
    if(plugins.length===0){
      list.style.display='none';
      if(empty) empty.style.display='';
      return;
    }
    if(empty) empty.style.display='none';
    list.style.display='';
    for(const plugin of plugins){
      list.appendChild(_buildPluginCard(plugin));
    }
  }catch(e){
    list.innerHTML='<div style="color:var(--error);padding:12px;font-size:13px">Failed to load plugins: '+esc(e.message||String(e))+'</div>';
  }
}

function _buildPluginCard(plugin){
  const card=document.createElement('div');
  card.className='provider-card plugin-card';
  card.dataset.plugin=(plugin&&plugin.key)||'';
  const hooks=Array.isArray(plugin&&plugin.hooks)?plugin.hooks:[];
  const hookHtml=hooks.length
    ? hooks.map(h=>`<span class="plugin-hook-badge">${esc(h)}</span>`).join('')
    : '<span class="plugin-hook-empty">No registered lifecycle hooks</span>';
  const version=(plugin&&plugin.version)?` · v${esc(plugin.version)}`:'';
  const desc=(plugin&&plugin.description)?esc(plugin.description):'No description provided.';
  const enabled=plugin&&plugin.enabled!==false;
  card.innerHTML=`
    <div class="provider-card-header plugin-card-header">
      <div class="provider-card-info">
        <div class="provider-card-name">${esc((plugin&&plugin.name)||'Unnamed plugin')}</div>
        <div class="provider-card-meta">${esc((plugin&&plugin.key)||'plugin')}${version}</div>
      </div>
      <span class="provider-card-badge ${enabled?'':'plugin-card-badge-disabled'}">${enabled?'Enabled':'Disabled'}</span>
    </div>
    <div class="provider-card-body plugin-card-body">
      <div class="provider-card-hint">${desc}</div>
      <div class="provider-card-label">Registered hooks</div>
      <div class="plugin-hook-list">${hookHtml}</div>
    </div>
  `;
  return card;
}

// ── Appstore panel – dynamic frontend from API + offline fallback ──────────

// Offline fallback — kept so the UI still works when the API is unreachable.
const _APPSTORE_FALLBACK_APPS = [
  { key: 'discord', name: 'Discord', icon: '💬', cat: 'Messaging', catIcon: '📬',
    dev: 'Sidekick Team', version: '2.1.0', size: '1.2 MB',
    desc: 'Nachrichten, Slash-Commands und Server-Management.',
    fullDesc: 'Integriere Sidekick mit Discord. Sende und empfange Nachrichten, führe Slash-Commands aus, verwalte Server und Kanäle. Volle Gateway-Integration mit Thread-Support, Embed-Rendering und Rolle-Management.',
    status: 'available', tags: ['gateway', 'messaging', 'notifications'],
    screenshots: ['Chat-Übersicht', 'Server-Liste', 'Einstellungen'],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'telegram', name: 'Telegram', icon: '✈️', cat: 'Messaging', catIcon: '📬',
    dev: 'Sidekick Team', version: '1.8.0', size: '0.9 MB',
    desc: 'Gateway für Benachrichtigungen und Bot-Interaktionen.',
    fullDesc: 'Verbinde Sidekick mit Telegram. Erhalte Benachrichtigungen, interagiere mit Gruppen, verwalte Kanäle und nutze Inline-Buttons für schnelle Aktionen.',
    status: 'available', tags: ['gateway', 'messaging', 'bot'],
    screenshots: ['Chat-Ansicht', 'Inline-Menü'],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'slack', name: 'Slack', icon: '💼', cat: 'Messaging', catIcon: '📬',
    dev: 'Community', version: '0.6.0', size: '0.6 MB',
    desc: 'Team-Kommunikation und Benachrichtigungen.',
    fullDesc: 'Binde Sidekick in deinen Slack-Workspace ein. Sende Nachrichten in Channels, reagiere auf Threads und verwalte Benachrichtigungen zentral.',
    status: 'planned', tags: ['gateway', 'team', 'notifications'],
    screenshots: [],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'signal', name: 'Signal', icon: '🔒', cat: 'Messaging', catIcon: '📬',
    dev: 'Community', version: '0.4.0', size: '0.5 MB',
    desc: 'Verschlüsselte Nachrichten via Signal-Gateway.',
    fullDesc: 'Sichere Kommunikation mit Ende-zu-Ende-Verschlüsselung. Signal-Integration für vertrauliche Nachrichten und Medien.',
    status: 'planned', tags: ['gateway', 'encryption', 'privacy'],
    screenshots: [],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'whatsapp', name: 'WhatsApp', icon: '📱', cat: 'Messaging', catIcon: '📬',
    dev: 'Community', version: '0.3.0', size: '0.4 MB',
    desc: 'Integration für Nachrichten und Medien.',
    fullDesc: 'WhatsApp-Gateway für Sidekick. Sende und empfange Nachrichten, teile Medien und verwalte Kontakte.',
    status: 'planned', tags: ['gateway', 'messaging', 'media'],
    screenshots: [],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'spotify', name: 'Spotify', icon: '🎵', cat: 'Media', catIcon: '🎵',
    dev: 'Sidekick Team', version: '1.5.0', size: '0.8 MB',
    desc: 'Wiedergabe, Playlists und Suche per Sprachbefehl.',
    fullDesc: 'Steuere Spotify direkt aus Sidekick. Durchsuche die Musikbibliothek, erstelle und verwalte Playlists, steuere die Wiedergabe und entdecke neue Musik – alles per Chat-Befehl.',
    status: 'available', tags: ['music', 'playback', 'playlist'],
    screenshots: ['Player-Ansicht', 'Playlist-Browser', 'Suche'],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'soundcloud', name: 'SoundCloud', icon: '🎧', cat: 'Media', catIcon: '🎵',
    dev: 'Community', version: '0.5.0', size: '0.5 MB',
    desc: 'Tracks suchen, Playlists verwalten.',
    fullDesc: 'SoundCloud-Integration für Sidekick. Durchsuche Millionen von Tracks, verwalte deine Playlists und entdecke neue Künstler.',
    status: 'planned', tags: ['music', 'tracks', 'discover'],
    screenshots: [],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'gmail', name: 'Gmail', icon: '📧', cat: 'Productivity', catIcon: '⚡',
    dev: 'Sidekick Team', version: '1.3.0', size: '0.7 MB',
    desc: 'E-Mails lesen, senden und durchsuchen.',
    fullDesc: 'Integriere dein Gmail-Konto mit Sidekick. Lies ungelesene E-Mails, durchsuche dein Postfach, sende Nachrichten und verwalte Labels – alles aus dem Chat.',
    status: 'available', tags: ['email', 'google', 'productivity'],
    screenshots: ['Inbox-Ansicht', 'E-Mail-Detail', 'Compose'],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'imap-mail', name: 'Mail', icon: '📧', cat: 'Productivity', catIcon: '⚡',
    dev: 'Sidekick Team', version: '1.0.0', size: '0.7 MB',
    desc: 'IMAP/SMTP-Mail automatisch einrichten und im Space aktivieren.',
    fullDesc: 'Verbinde dein Mail-Konto mit Sidekick. Die App erkennt bekannte Anbieter automatisch, schreibt die IMAP/SMTP-Konfiguration in den aktiven Space und aktiviert den Mail-Zugriff im Hintergrund.',
    status: 'available', tags: ['email', 'imap', 'smtp', 'auto-setup'],
    screenshots: ['Automatische Einrichtung', 'Inbox-Übersicht', 'Verbindungsstatus'],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'calendar', name: 'Google Calendar', icon: '📅', cat: 'Productivity', catIcon: '⚡',
    dev: 'Community', version: '0.7.0', size: '0.5 MB',
    desc: 'Termine verwalten und Kalender einsehen.',
    fullDesc: 'Verbinde Google Calendar mit Sidekick. Erstelle Termine, prüfe deinen Tagesplan, verwalte Erinnerungen und teile Termine mit anderen.',
    status: 'planned', tags: ['calendar', 'google', 'scheduling'],
    screenshots: [],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'github', name: 'GitHub', icon: '🐙', cat: 'Developer Tools', catIcon: '🛠️',
    dev: 'Sidekick Team', version: '2.0.0', size: '1.1 MB',
    desc: 'Repository-Verwaltung, Issues und Pull Requests.',
    fullDesc: 'Verwalte deine GitHub-Repositories direkt aus Sidekick. Erstelle Issues, reviewe Pull Requests, durchsuche Code und verwalte Projekte.',
    status: 'available', tags: ['git', 'development', 'ci/cd'],
    screenshots: ['Repository-Ansicht', 'Issue-Tracker', 'PR-Detail'],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'vscode', name: 'VS Code', icon: '💻', cat: 'Developer Tools', catIcon: '🛠️',
    dev: 'Community', version: '0.5.0', size: '0.6 MB',
    desc: 'Editor-Integration und Dateimanagement.',
    fullDesc: 'Steuere VS Code aus Sidekick heraus. Öffne Dateien, führe Befehle aus, navigiere im Projektbaum und verwalte Workspaces.',
    status: 'planned', tags: ['editor', 'development', 'files'],
    screenshots: [],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'comfyui', name: 'ComfyUI', icon: '🎨', cat: 'AI', catIcon: '🤖',
    dev: 'Community', version: '0.8.0', size: '0.9 MB',
    desc: 'Bildgenerierung mit Stabil Diffusion Workflows.',
    fullDesc: 'Verbinde Sidekick mit ComfyUI. Erstelle und steuere Bildgenerierungs-Workflows, verwalte Checkpoints und generiere Bilder per Chat-Befehl.',
    status: 'planned', tags: ['ai', 'image-gen', 'stable-diffusion'],
    screenshots: [],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
  { key: 'openrouter', name: 'OpenRouter', icon: '🧠', cat: 'AI', catIcon: '🤖',
    dev: 'Sidekick Team', version: '1.1.0', size: '0.4 MB',
    desc: 'Multi-Provider LLM-Zugriff und Modell-Routing.',
    fullDesc: 'Nutze OpenRouter als Fallback-Provider für Sidekick. Greife auf 100+ Modelle von 20+ Anbietern zu, mit automatischem Failover und Cost-Tracking.',
    status: 'available', tags: ['ai', 'llm', 'provider'],
    screenshots: ['Modell-Liste', 'Usage-Dashboard'],
    setup_steps: [], config_changes: [], env_writes: {}, gateway_restart: false, tools_enable: [] },
];

const _APPSTORE_CATEGORIES = [
  { key: 'Messaging', icon: '📬', label: 'Messaging' },
  { key: 'Media', icon: '🎵', label: 'Media & Music' },
  { key: 'Productivity', icon: '⚡', label: 'Productivity' },
  { key: 'Developer Tools', icon: '🛠️', label: 'Developer' },
  { key: 'AI', icon: '🤖', label: 'AI & Agents' },
  { key: 'AI & LLM', icon: '🤖', label: 'AI & LLM' },
];

function _appstoreText(key, fallback) {
  if (typeof t === 'function') {
    try {
      const value = String(t(key) || '');
      if (value && value !== key) return value;
    } catch (_) {}
  }
  return fallback;
}

function _appstoreCategoryMeta(catKey) {
  const known = _APPSTORE_CATEGORIES.find(c => c.key === catKey);
  if (known) return known;
  return { key: catKey || 'Other', icon: '📦', label: catKey || 'Other' };
}

function _appstoreCategories() {
  const seen = new Map();
  for (const cat of _APPSTORE_CATEGORIES) {
    const count = _appstoreAppsCache.filter(a => a.cat === cat.key).length;
    if (count > 0) seen.set(cat.key, Object.assign({ count }, cat));
  }
  for (const app of _appstoreAppsCache) {
    const key = app.cat || 'Other';
    if (!seen.has(key)) {
      const meta = _appstoreCategoryMeta(key);
      seen.set(key, Object.assign({ count: 0 }, meta));
    }
    seen.get(key).count += 1;
  }
  return Array.from(seen.values());
}

function _appstoreNormalizeApp(app) {
  const normalized = Object.assign({}, app || {});
  normalized.key = normalized.key || normalized.id || '';
  normalized.name = normalized.name || normalized.key || 'App';
  normalized.icon = normalized.icon || '📦';
  normalized.cat = normalized.cat || normalized.category || 'Other';
  normalized.dev = normalized.dev || normalized.developer || 'Unknown';
  normalized.desc = normalized.desc || normalized.description || '';
  normalized.fullDesc = normalized.fullDesc || normalized.full_desc || normalized.desc;
  normalized.version = normalized.version || normalized.ver || 'unknown';
  normalized.ver = normalized.ver || normalized.version;
  normalized.size = normalized.size || '—';
  normalized.tags = Array.isArray(normalized.tags) ? normalized.tags : [];
  normalized.screenshots = Array.isArray(normalized.screenshots) ? normalized.screenshots : [];
  normalized.setup_steps = Array.isArray(normalized.setup_steps) ? normalized.setup_steps : [];
  normalized.config_changes = Array.isArray(normalized.config_changes) ? normalized.config_changes : [];
  normalized.env_writes = normalized.env_writes && typeof normalized.env_writes === 'object' ? normalized.env_writes : {};
  normalized.tools_enable = Array.isArray(normalized.tools_enable) ? normalized.tools_enable : [];
  if (typeof normalized.status !== 'object' || normalized.status === null) {
    normalized.availability = normalized.status || normalized.availability || 'available';
    normalized.status = {
      installed: false,
      version_installed: null,
      version_available: normalized.version,
    };
  } else {
    normalized.availability = normalized.availability || 'available';
  }
  return normalized;
}

// Runtime state
let _appstoreAppsCache = [];          // populated from API, fallback to _APPSTORE_FALLBACK_APPS
let _appstoreOffline = false;          // true when API failed
let _appstoreInstalledCount = 0;

let _appstoreCurrentPage = 'home';
let _appstoreSelectedApp = null;

async function loadAppstorePanel() {
  _appstoreSelectedApp = null;
  _appstoreOffline = false;

  // Show loading state
  const content = document.getElementById('appstoreContent');
  if (content) {
    content.innerHTML =
      '<div class="appstore-skeleton-loading">' +
        '<div class="appstore-setup-spinner"></div>' +
        '<span>' + (typeof t === 'function' ? t('appstore_loading') : 'Lade Appstore…') + '</span>' +
      '</div>' +
      '<div style="display:flex;gap:12px;padding:0 24px 24px;overflow:hidden;">' +
        '<div class="appstore-skeleton appstore-skeleton-card"></div>' +
        '<div class="appstore-skeleton appstore-skeleton-card"></div>' +
        '<div class="appstore-skeleton appstore-skeleton-card"></div>' +
        '<div class="appstore-skeleton appstore-skeleton-card"></div>' +
      '</div>';
  }

  // Fetch from API with fallback + timeout
  try {
    const data = await _appstoreApiWithTimeout('/api/appstore', 10000);
    if (data && Array.isArray(data.apps)) {
      _appstoreAppsCache = data.apps.map(_appstoreNormalizeApp);
      _appstoreInstalledCount = data.installed_count || 0;
    } else {
      throw new Error('Unexpected response format');
    }
  } catch (err) {
    console.warn('[appstore] API fetch failed, using fallback:', err.message);
    _appstoreOffline = true;
    _appstoreAppsCache = _APPSTORE_FALLBACK_APPS.map(_appstoreNormalizeApp);
    _appstoreInstalledCount = 0;
    // Show error state instead of empty panel when completely offline and no fallback
    if (!_APPSTORE_FALLBACK_APPS || _APPSTORE_FALLBACK_APPS.length === 0) {
      if (content) {
        content.innerHTML = _renderAppstoreError(err.message);
        return;
      }
    }
  }
  _appstoreNavigate('home');
}

// API call with timeout
async function _appstoreApiWithTimeout(url, timeoutMs = 10000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { signal: controller.signal });
    clearTimeout(timeoutId);
    if (!response.ok) {
      throw new Error('HTTP ' + response.status);
    }
    return await response.json();
  } catch (err) {
    clearTimeout(timeoutId);
    if (err.name === 'AbortError') {
      throw new Error(typeof t === 'function' ? t('appstore_timeout') : 'Request timed out. The server may be busy.');
    }
    throw err;
  }
}

// Error state renderer
function _renderAppstoreError(errMsg) {
  return '<div class="appstore-error-state">' +
    '<div class="appstore-error-state-icon">⚠️</div>' +
    '<div class="appstore-error-state-title">' + (typeof t === 'function' ? t('appstore_error_title') : 'Appstore konnte nicht geladen werden') + '</div>' +
    '<div class="appstore-error-state-desc">' +
      (typeof t === 'function' ? t('appstore_error_desc') : 'Der Server antwortet nicht. Bitte versuche es erneut.') +
      (errMsg ? '<br><span style="font-size:11px;color:var(--error);">' + esc(errMsg) + '</span>' : '') +
    '</div>' +
    '<button class="appstore-error-state-btn" onclick="loadAppstorePanel()">' +
      '🔄 ' + (typeof t === 'function' ? t('appstore_retry') : 'Erneut versuchen') +
    '</button>' +
    '</div>';
}

// Offline banner (shown at top when API is unreachable)
function _appstoreOfflineBanner() {
  if (!_appstoreOffline) return '';
  return '<div style="padding:6px 16px;background:var(--warning);color:var(--bg);font-size:11px;font-weight:500;display:flex;align-items:center;gap:8px;">' +
    '<span>⚠️</span><span>Offline-Modus — API nicht erreichbar. Zeige zwischengespeicherte App-Daten.</span>' +
    '<button style="margin-left:auto;background:rgba(0,0,0,.15);border:none;color:var(--bg);padding:2px 8px;border-radius:4px;cursor:pointer;font-size:10px;" onclick="loadAppstorePanel()">Erneut versuchen</button>' +
    '</div>';
}

function _appstoreNavigate(page) {
  _appstoreCurrentPage = page;
  const content = document.getElementById('appstoreContent');
  const right = document.getElementById('appstoreRight');
  if (!content) return;

  // Update sidebar active state
  document.querySelectorAll('#appstoreSbNav .appstore-sb-item').forEach(el => {
    el.classList.toggle('active', el.dataset.page === page);
  });

  // Update breadcrumb
  const bc = document.getElementById('appstoreBreadcrumb');
  if (!bc) return;

  if (page === 'home') {
    bc.innerHTML = '<span class="appstore-breadcrumb-item" onclick="_appstoreNavigate(\'home\')">Start</span>';
    _renderAppstoreHome(content);
    _renderAppstoreRight(null);
    document.getElementById('appstoreTopbarCount').textContent = _appstoreAppsCache.length + ' Apps' + (_appstoreOffline ? ' (offline)' : '');
  } else if (page.startsWith('category:')) {
    const catKey = page.slice(9);
    const cat = _appstoreCategoryMeta(catKey);
    const apps = _appstoreAppsCache.filter(a => a.cat === catKey);
    bc.innerHTML =
      '<span class="appstore-breadcrumb-item" onclick="_appstoreNavigate(\'home\')">Start</span>' +
      '<span class="appstore-breadcrumb-sep">›</span>' +
      '<span class="appstore-breadcrumb-current">' + esc(cat ? cat.label : catKey) + '</span>';
    _renderAppstoreCategory(content, catKey);
    _renderAppstoreRight(null);
    document.getElementById('appstoreTopbarCount').textContent = apps.length + ' Apps';
  } else if (page.startsWith('app:')) {
    const appKey = page.slice(4);
    const app = _appstoreAppsCache.find(a => a.key === appKey);
    if (!app) { _appstoreNavigate('home'); return; }
    const cat = _appstoreCategoryMeta(app.cat);
    bc.innerHTML =
      '<span class="appstore-breadcrumb-item" onclick="_appstoreNavigate(\'home\')">Start</span>' +
      '<span class="appstore-breadcrumb-sep">›</span>' +
      '<span class="appstore-breadcrumb-item" onclick="_appstoreNavigate(\'category:' + esc(app.cat) + '\')">' + esc(cat ? cat.label : app.cat) + '</span>' +
      '<span class="appstore-breadcrumb-sep">›</span>' +
      '<span class="appstore-breadcrumb-current">' + esc(app.name) + '</span>';
    _renderAppstoreAppPage(content, app);
    _renderAppstoreRight(app);
    document.getElementById('appstoreTopbarCount').textContent = app.name;
  } else if (page === 'my-apps') {
    bc.innerHTML =
      '<span class="appstore-breadcrumb-item" onclick="_appstoreNavigate(\'home\')">Start</span>' +
      '<span class="appstore-breadcrumb-sep">›</span>' +
      '<span class="appstore-breadcrumb-current">' + _appstoreText('appstore_my_apps', 'Meine Apps') + '</span>';
    _renderAppstoreMyApps(content);
    _renderAppstoreRight(null);
    const myCount = _appstoreAppsCache.filter(a => a.status && a.status.installed).length;
    document.getElementById('appstoreTopbarCount').textContent = myCount + ' ' + _appstoreText('appstore_installed', 'installiert');
  } else if (page === 'sdk') {
    bc.innerHTML =
      '<span class="appstore-breadcrumb-item" onclick="_appstoreNavigate(\'home\')">Start</span>' +
      '<span class="appstore-breadcrumb-sep">›</span>' +
      '<span class="appstore-breadcrumb-current">' + _appstoreText('appstore_sdk_docs', 'SDK Dokumentation') + '</span>';
    _renderAppstoreSdk(content);
    _renderAppstoreRight(null);
    document.getElementById('appstoreTopbarCount').textContent = 'SDK';
  } else if (page === 'submit') {
    bc.innerHTML =
      '<span class="appstore-breadcrumb-item" onclick="_appstoreNavigate(\'home\')">Start</span>' +
      '<span class="appstore-breadcrumb-sep">›</span>' +
      '<span class="appstore-breadcrumb-current">' + _appstoreText('appstore_submit_plugin', 'Plugin einreichen') + '</span>';
    _renderAppstoreSubmit(content);
    _renderAppstoreRight(null);
    document.getElementById('appstoreTopbarCount').textContent = 'Submit';
  }
}

function _renderAppstoreHome(container) {
  // Offline banner
  let html = _appstoreOfflineBanner();

  // Hero
  html +=
    '<div class="appstore-hero">' +
      '<div class="appstore-hero-content">' +
        '<div class="appstore-hero-badge">Sidekick Integrations</div>' +
        '<h1 class="appstore-hero-title">🛍️ Appstore</h1>' +
        '<p class="appstore-hero-sub">Erweitere Sidekick mit Plugins, Gateways und Tools. Ein Klick – los geht\'s.</p>' +
      '</div>' +
    '</div>';

  if (_appstoreAppsCache.length === 0) {
    html +=
      '<div class="appstore-empty-state">' +
        '<div class="appstore-empty-state-icon">📦</div>' +
        '<div class="appstore-empty-state-title">' + _appstoreText('appstore_empty_catalog_title', 'Keine Apps verfügbar') + '</div>' +
        '<div class="appstore-empty-state-desc">' +
          _appstoreText('appstore_empty_catalog_desc', 'Dieser Appstore-Katalog enthält aktuell keine Apps. Prüfe dein aktives Profil oder füge Manifestdateien hinzu.') +
        '</div>' +
      '</div>';
  }

  // Featured apps (empfohlene Apps mit featured:true)
  const featuredApps = _appstoreAppsCache.filter(a => a.featured === true);
  if (featuredApps.length > 0) {
    html +=
      '<div class="appstore-section">' +
        '<div class="appstore-section-head">' +
          '<h2 class="appstore-section-title">⭐ ' + (typeof t === 'function' ? t('appstore_recommended') : 'Empfohlen') + '</h2>' +
        '</div>' +
        '<div class="appstore-hscroll">';
    for (const app of featuredApps) {
      html += _buildAppstoreCardHtml(app);
    }
    html += '</div></div>';
  }

  // Recently installed (max 5)
  const recentlyInstalled = _appstoreAppsCache
    .filter(a => a.status && a.status.installed && a.installation_date)
    .sort((a, b) => new Date(b.installation_date) - new Date(a.installation_date))
    .slice(0, 5);
  if (recentlyInstalled.length > 0) {
    html +=
      '<div class="appstore-section">' +
        '<div class="appstore-section-head">' +
          '<h2 class="appstore-section-title">🕐 ' + (typeof t === 'function' ? t('appstore_recently_installed') : 'Zuletzt installiert') + '</h2>' +
        '</div>' +
        '<div class="appstore-hscroll">';
    for (const app of recentlyInstalled) {
      html += _buildAppstoreCardHtml(app);
    }
    html += '</div></div>';
  }

  // Featured sections per category
  for (const cat of _appstoreCategories()) {
    const apps = _appstoreAppsCache.filter(a => a.cat === cat.key);
    if (apps.length === 0) continue;
    html +=
      '<div class="appstore-section">' +
        '<div class="appstore-section-head">' +
          '<h2 class="appstore-section-title">' + cat.icon + ' ' + esc(cat.label) + '</h2>' +
          '<a class="appstore-section-link" onclick="_appstoreNavigate(\'category:' + esc(cat.key) + '\')">Alle anzeigen →</a>' +
        '</div>' +
        '<div class="appstore-hscroll">';
    for (const app of apps) {
      html += _buildAppstoreCardHtml(app);
    }
    html += '</div></div>';
  }

  // Featured bottom banner (SDK call-to-action)
  html +=
    '<div class="appstore-featured">' +
      '<div class="appstore-featured-icon">🔧</div>' +
      '<div class="appstore-featured-body">' +
        '<div class="appstore-featured-title">Eigenes Plugin bauen?</div>' +
        '<div class="appstore-featured-desc">Nutze das Sidekick Plugin SDK und veröffentliche deine eigene Integration im Appstore.</div>' +
      '</div>' +
      '<button class="appstore-featured-btn" onclick="_appstoreNavigate(\'sdk\')">SDK ansehen →</button>' +
    '</div>';

  container.innerHTML = html;
  _buildSbNav();
}

function _renderAppstoreCategory(container, catKey) {
  const cat = _appstoreCategoryMeta(catKey);
  const apps = _appstoreAppsCache.filter(a => a.cat === catKey);
  const catIcon = cat ? cat.icon : '📁';
  const catLabel = cat ? cat.label : catKey;

  let html = _appstoreOfflineBanner() +
    '<div class="appstore-cat-header">' +
      '<span class="appstore-cat-header-icon">' + catIcon + '</span>' +
      '<div>' +
        '<h2 class="appstore-cat-header-title">' + esc(catLabel) + '</h2>' +
        '<span class="appstore-cat-header-count">' + apps.length + ' Apps</span>' +
      '</div>' +
    '</div>' +
    '<div class="appstore-grid">';

  for (const app of apps) {
    const isInstalled = app.status && app.status.installed;
    const isActuallyPlanned = app.availability === 'planned';
    const isMailApp = app.key === 'imap-mail';
    let btnClass, btnLabel, btnAction;

    if (isInstalled) {
      btnClass = 'appstore-card-btn appstore-card-btn-success';
      btnLabel = '✓ Installiert';
      btnAction = 'onclick="event.stopPropagation();_appstoreUninstall(\'' + esc(app.key) + '\')"';
    } else if (isActuallyPlanned) {
      btnClass = 'appstore-card-btn appstore-card-btn-disabled';
      btnLabel = 'Demnächst';
      btnAction = 'disabled';
    } else if (isMailApp) {
      btnClass = 'appstore-card-btn ' + (app.space_active ? 'appstore-card-btn-success' : 'appstore-card-btn-primary');
      btnLabel = app.space_active ? 'Mail verwalten' : 'Mail einrichten';
      btnAction = 'onclick="event.stopPropagation();_appstoreOpenMailSettings()"';
    } else {
      btnClass = 'appstore-card-btn appstore-card-btn-primary';
      btnLabel = 'Installieren';
      btnAction = 'onclick="event.stopPropagation();_appstoreStartInstall(\'' + esc(app.key) + '\')"';
    }

    const tagsHtml = (app.tags || []).map(t => '<span class="appstore-card-tag">' + esc(t) + '</span>').join('');
    html +=
      '<div class="appstore-grid-card" onclick="_appstoreNavigate(\'app:' + esc(app.key) + '\')">' +
        '<div style="display:flex;align-items:center;gap:12px;">' +
          '<div class="appstore-card-icon-wrap">' + app.icon + '</div>' +
          '<div class="appstore-card-body">' +
            '<div class="appstore-card-name">' + esc(app.name) + '</div>' +
            '<div class="appstore-card-dev">' + esc(app.dev) + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="appstore-card-desc">' + esc(app.desc) + '</div>' +
        '<div class="appstore-card-tags">' + tagsHtml + '</div>' +
        '<div class="appstore-card-actions">' +
          '<button class="' + btnClass + '" ' + btnAction + '>' + btnLabel + '</button>' +
        '</div>' +
      '</div>';
  }
  html += '</div>';
  container.innerHTML = html;
  _buildSbNav();
}

// ── Meine Apps Dashboard ───────────────────────────────────────────────
function _renderAppstoreMyApps(container) {
  const myApps = _appstoreAppsCache.filter(a => a.status && a.status.installed);
  let html = _appstoreOfflineBanner();
  if (myApps.length === 0) {
    html += '<div style="padding:48px;text-align:center;color:var(--muted);font-size:14px;">' +
      '<div style="font-size:48px;margin-bottom:12px;">📦</div>' +
      '<div style="font-weight:600;color:var(--text);margin-bottom:4px;">' + _appstoreText('appstore_my_apps', 'Meine Apps') + '</div>' +
      '<div>' + _appstoreText('appstore_empty_my_apps', 'Noch keine Apps installiert. Stöbere im Store!') + '</div>' +
      '</div>';
    container.innerHTML = html;
    _buildSbNav();
    return;
  }
  html += '<div class="appstore-grid">';
  for (const app of myApps) {
    html += _buildAppstoreGridCardHtml(app);
  }
  html += '</div>';
  container.innerHTML = html;
  _buildSbNav();
}

function _renderAppstoreAppPage(container, app) {
  const isInstalled = app.status && app.status.installed;
  const isPlanned = app.availability === 'planned';
  const isSpaceActive = app.space_active === true;
  const isMailApp = app.key === 'imap-mail';
  let installLabel, installDisabled, installAction, uninstallAction;

  if (isMailApp) {
    installLabel = isSpaceActive ? 'Mail verwalten' : 'Mail einrichten';
    installDisabled = '';
    installAction = 'onclick="_appstoreOpenMailSettings()"';
    uninstallAction = '';
  } else if (isInstalled) {
    installLabel = '✓ Installiert · v' + esc(app.status.version_installed || app.version || '?');
    installDisabled = 'disabled';
    installAction = '';
    uninstallAction = 'onclick="_appstoreUninstall(\'' + esc(app.key) + '\')"';
  } else if (isPlanned) {
    installLabel = 'Demnächst verfügbar';
    installDisabled = 'disabled';
    installAction = '';
    uninstallAction = '';
  } else {
    installLabel = 'Installieren';
    installDisabled = '';
    installAction = 'onclick="_appstoreStartInstall(\'' + esc(app.key) + '\')"';
    uninstallAction = '';
  }

  const tagsHtml = (app.tags || []).map(t => '<span class="appstore-card-tag">' + esc(t) + '</span>').join('');
  const screenshotsHtml = (app.screenshots || []).length > 0
    ? '<div class="appstore-detail-section-title">📸 Screenshots</div>' +
      '<div class="appstore-detail-screenshots">' +
        app.screenshots.map(s => '<div class="appstore-detail-screenshot">' + esc(s) + '</div>').join('') +
      '</div>'
    : '<div class="appstore-detail-section-title">📸 Screenshots</div>' +
      '<div style="font-size:11px;color:var(--muted);padding:12px;text-align:center;background:var(--code-bg);border-radius:8px;border:1px dashed var(--border2);">Screenshots in Vorbereitung</div>';

  const versionStr = app.version || app.ver || '?';
  const sizeStr = app.size || '—';

  // Space activation toggle (only for apps that support per-space activation)
  const spaceToggleHtml = (app.key === 'imap-mail')
    ? '<div style="margin-top:12px;padding:12px;background:var(--code-bg);border-radius:8px;border:1px solid var(--border2);">' +
        '<div style="display:flex;align-items:center;gap:10px;">' +
          '<label class="appstore-toggle-label" style="flex:1;font-size:13px;font-weight:500;">In diesem Space aktivieren</label>' +
          '<button class="appstore-card-btn ' + (isSpaceActive ? 'appstore-card-btn-success' : 'appstore-card-btn-outline') + '" style="padding:6px 14px;font-size:12px;width:auto;" ' +
            'onclick="_appstoreToggleSpace(\'' + esc(app.key) + '\', ' + (isSpaceActive ? 'false' : 'true') + ')">' +
            (isSpaceActive ? '✓ Aktiv' : 'Aktivieren') +
          '</button>' +
        '</div>' +
        '<div style="font-size:11px;color:var(--muted);margin-top:6px;">Sidekick schreibt die Mail-Konfiguration in diesen Space und schaltet den Mail-Zugriff hier an oder aus.</div>' +
      '</div>'
    : '';

  let html =
    // Back button + app header
    '<div style="padding:16px 24px 0;display:flex;align-items:center;gap:8px;">' +
      '<button class="appstore-card-btn appstore-card-btn-outline" style="width:auto;padding:5px 12px;" onclick="_appstoreNavigate(\'category:' + esc(app.cat) + '\')">← Zurück</button>' +
    '</div>' +
    // App hero section
    '<div style="display:flex;gap:20px;padding:20px 24px;border-bottom:1px solid var(--border2);">' +
      '<div class="appstore-card-icon-wrap" style="width:64px;height:64px;border-radius:14px;font-size:32px;background:var(--card-bg);border:1px solid var(--border2);">' + app.icon + '</div>' +
      '<div style="flex:1;">' +
        '<div class="appstore-card-name-lg">' + esc(app.name) + '</div>' +
        '<div class="appstore-card-dev" style="margin:2px 0 8px;">' + esc(app.dev) + '</div>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap;">' +
          '<span class="appstore-detail-meta-item">v' + esc(versionStr) + '</span>' +
          '<span class="appstore-detail-meta-item">' + esc(sizeStr) + '</span>' +
          '<span class="appstore-detail-meta-item" style="background:var(--accent);color:var(--accent-text);border-color:var(--accent);">' + esc(app.cat) + '</span>' +
        '</div>' +
        '<div style="margin-top:12px;display:flex;gap:8px;">' +
          '<button class="appstore-card-btn ' + (isInstalled ? 'appstore-card-btn-success' : (isPlanned ? 'appstore-card-btn-disabled' : 'appstore-card-btn-primary')) + '" style="padding:10px 24px;font-size:14px;width:auto;"' +
            (installDisabled ? ' disabled' : '') + ' ' + installAction + '>' + installLabel +
          '</button>' +
          (app.key === 'imap-mail'
            ? '<button class="appstore-card-btn appstore-card-btn-outline" style="padding:10px 16px;width:auto;font-size:13px;" onclick="_appstoreOpenMailSettings()">Mail einrichten</button>'
            : app.key === 'gmail'
            ? '<button class="appstore-card-btn appstore-card-btn-outline" style="padding:10px 16px;width:auto;font-size:13px;" onclick="_appstoreOpenGmailSettings()">App Settings</button>'
            : app.key === 'discord'
              ? '<button class="appstore-card-btn appstore-card-btn-outline" style="padding:10px 16px;width:auto;font-size:13px;" onclick="_appstoreOpenDiscordSettings()">App Settings</button>'
            : (app.settings_url
              ? '<button class="appstore-card-btn appstore-card-btn-outline" style="padding:10px 16px;width:auto;font-size:13px;" onclick="_appstoreOpenAppSettings(\'' + esc(app.key) + '\',\'' + esc(app.settings_url) + '\')">⚙️ Einstellungen</button>'
              : '')) +
          (isInstalled
            ? '<button class="appstore-card-btn appstore-card-btn-danger" style="padding:10px 16px;width:auto;font-size:13px;" ' + uninstallAction + '>Deinstallieren</button>'
            : '') +
        '</div>' +
        (isInstalled ? '<div style="margin-top:8px;font-size:11px;color:var(--success);"><span>✓ Installiert (Version ' + esc(app.status.version_installed || versionStr) + ')</span></div>' : '') +
        spaceToggleHtml +
      '</div>' +
    '</div>' +

    // Full description
    '<div style="padding:20px 24px;border-bottom:1px solid var(--border2);">' +
      '<div class="appstore-detail-section-title">Beschreibung</div>' +
      '<div class="appstore-detail-desc">' + esc(app.fullDesc) + '</div>' +
    '</div>' +

    // Developer section
    '<div style="padding:20px 24px;border-bottom:1px solid var(--border2);">' +
      '<div class="appstore-detail-section-title">' + _appstoreText('appstore_from_developer', 'Vom Entwickler') + '</div>' +
      '<div style="display:flex;align-items:center;gap:10px;padding:8px 0;">' +
        '<div style="width:36px;height:36px;border-radius:50%;background:var(--surface);border:1px solid var(--border2);display:flex;align-items:center;justify-content:center;font-size:16px;">👤</div>' +
        '<div>' +
          '<div style="font-size:13px;font-weight:600;color:var(--text);">' + esc(app.dev) + '</div>' +
          '<div style="font-size:11px;color:var(--muted);">Entwickler</div>' +
        '</div>' +
      '</div>' +
      '<div style="margin-top:8px;display:flex;flex-wrap:wrap;gap:8px;">' +
        '<button class="appstore-card-btn appstore-card-btn-outline" style="width:auto;padding:6px 12px;font-size:12px;" onclick="_appstoreNavigate(\'sdk\')">📖 ' + _appstoreText('appstore_sdk_docs', 'SDK Dokumentation') + '</button>' +
        '<button class="appstore-card-btn appstore-card-btn-outline" style="width:auto;padding:6px 12px;font-size:12px;" onclick="_appstoreNavigate(\'submit\')">✍️ ' + _appstoreText('appstore_submit_plugin', 'Plugin einreichen') + '</button>' +
      '</div>' +
    '</div>' +

    // Screenshots
    '<div style="padding:20px 24px;border-bottom:1px solid var(--border2);">' +
      screenshotsHtml +
    '</div>' +

    // Tags
    '<div style="padding:20px 24px;">' +
      '<div class="appstore-detail-section-title">Kategorien</div>' +
      '<div class="appstore-detail-tags">' + tagsHtml + '</div>' +
    '</div>';

  container.innerHTML = html;
}

function _renderAppstoreRight(app) {
  const right = document.getElementById('appstoreRight');
  if (!right) return;
  if (!app) {
    right.innerHTML =
      '<div class="appstore-right-empty">' +
        '<div class="appstore-right-empty-icon">🛍️</div>' +
        '<div class="appstore-right-empty-title">App auswählen</div>' +
        '<div class="appstore-right-empty-desc">Klicke links auf eine App, um Details zu sehen und sie zu installieren.</div>' +
        '<div class="appstore-right-featured-steps">' +
          '<div class="appstore-right-step"><div class="appstore-right-step-num">1</div><div class="appstore-right-step-label">App durchsuchen</div></div>' +
          '<div class="appstore-right-step"><div class="appstore-right-step-num">2</div><div class="appstore-right-step-label">Details prüfen</div></div>' +
          '<div class="appstore-right-step"><div class="appstore-right-step-num">3</div><div class="appstore-right-step-label">Installieren</div></div>' +
        '</div>' +
      '</div>';
    return;
  }

  const isInstalled = app.status && app.status.installed;
  const isPlanned = app.availability === 'planned';

  let installLabel, installDisabled, installAction, uninstallAction;
  if (isInstalled) {
    installLabel = '✓ Installiert · v' + esc(app.status.version_installed || app.version || '?');
    installDisabled = 'disabled';
    installAction = '';
    uninstallAction = 'onclick="_appstoreUninstall(\'' + esc(app.key) + '\')"';
  } else if (isPlanned) {
    installLabel = 'Demnächst verfügbar';
    installDisabled = 'disabled';
    installAction = '';
    uninstallAction = '';
  } else {
    installLabel = 'Installieren';
    installDisabled = '';
    installAction = 'onclick="_appstoreStartInstall(\'' + esc(app.key) + '\')"';
    uninstallAction = '';
  }

  const tagsHtml = (app.tags || []).map(t => '<span class="appstore-detail-tag">' + esc(t) + '</span>').join('');
  const screenshotsHtml = (app.screenshots || []).length > 0
    ? '<div class="appstore-detail-section-title">📸 Screenshots</div>' +
      '<div class="appstore-detail-screenshots">' +
        app.screenshots.map(s => '<div class="appstore-detail-screenshot">' + esc(s) + '</div>').join('') +
      '</div>'
    : '';

  const versionStr = app.version || app.ver || '?';
  const sizeStr = app.size || '—';

  right.innerHTML =
    '<div class="appstore-detail">' +
      // Top: Icon + Name + Meta
      '<div class="appstore-detail-top">' +
        '<div class="appstore-detail-icon-big">' + app.icon + '</div>' +
        '<div style="flex:1;">' +
          '<div class="appstore-detail-name">' + esc(app.name) + '</div>' +
          '<div class="appstore-detail-dev">' + esc(app.dev) + '</div>' +
          '<div class="appstore-detail-meta">' +
            '<span class="appstore-detail-meta-item">v' + esc(versionStr) + '</span>' +
            '<span class="appstore-detail-meta-item">' + esc(sizeStr) + '</span>' +
          '</div>' +
        '</div>' +
      '</div>' +

      // Install/Uninstall button
      '<button class="appstore-detail-install-btn appstore-detail-install-btn--' + (isInstalled ? 'installed' : (isPlanned ? 'planned' : 'available')) + '"' +
        (installDisabled ? ' disabled' : '') + ' ' + installAction + '>' + installLabel +
      '</button>' +
      (isInstalled
        ? '<button class="appstore-detail-uninstall-btn" ' + uninstallAction + '>Deinstallieren</button>'
        : '') +
      (app.key === 'gmail'
        ? '<button class="appstore-detail-uninstall-btn" style="border-color:var(--accent);color:var(--accent);" onclick="_appstoreOpenGmailSettings()">App Settings</button>'
        : app.key === 'discord'
          ? '<button class="appstore-detail-uninstall-btn" style="border-color:var(--accent);color:var(--accent);" onclick="_appstoreOpenDiscordSettings()">App Settings</button>'
        : app.key === 'imap-mail'
          ? ''
          : (app.settings_url
          ? '<button class="appstore-detail-uninstall-btn" style="border-color:var(--accent);color:var(--accent);" onclick="_appstoreOpenAppSettings(\'' + esc(app.key) + '\',\'' + esc(app.settings_url) + '\')">⚙️ Einstellungen</button>'
          : '')) +

      // Description
      '<div class="appstore-detail-desc">' + esc(app.fullDesc || app.desc) + '</div>' +

      // Screenshots
      screenshotsHtml +

      // Info Grid
      '<div>' +
        '<div class="appstore-detail-section-title">Informationen</div>' +
        '<div class="appstore-detail-info-grid">' +
          '<div class="appstore-detail-info-item"><span class="appstore-detail-info-label">Kategorie</span><span class="appstore-detail-info-value">' + esc(app.cat) + '</span></div>' +
          '<div class="appstore-detail-info-item"><span class="appstore-detail-info-label">Entwickler</span><span class="appstore-detail-info-value">' + esc(app.dev) + '</span></div>' +
          '<div class="appstore-detail-info-item"><span class="appstore-detail-info-label">Version</span><span class="appstore-detail-info-value">' + esc(versionStr) + '</span></div>' +
          '<div class="appstore-detail-info-item"><span class="appstore-detail-info-label">Größe</span><span class="appstore-detail-info-value">' + esc(sizeStr) + '</span></div>' +
        '</div>' +
      '</div>' +

      // Tags
      (tagsHtml ? '<div><div class="appstore-detail-section-title">Tags</div><div class="appstore-detail-tags">' + tagsHtml + '</div></div>' : '') +
    '</div>';
}

function _buildAppstoreCardHtml(app) {
  const isInstalled = app.status && app.status.installed;
  const isPlanned = app.availability === 'planned';
  const isMailApp = app.key === 'imap-mail';
  let btnClass, btnLabel, btnAction;

    if (isMailApp) {
      btnClass = 'appstore-card-btn ' + (app.space_active ? 'appstore-card-btn-success' : 'appstore-card-btn-primary');
      btnLabel = app.space_active ? 'Mail verwalten' : 'Mail einrichten';
      btnAction = 'onclick="event.stopPropagation();_appstoreOpenMailSettings()"';
    } else if (isInstalled) {
      btnClass = 'appstore-card-btn appstore-card-btn-success';
      btnLabel = '✓ Installiert';
      btnAction = 'onclick="event.stopPropagation();_appstoreUninstall(\'' + esc(app.key) + '\')"';
    } else if (isPlanned) {
      btnClass = 'appstore-card-btn appstore-card-btn-disabled';
      btnLabel = 'Demnächst';
      btnAction = 'disabled';
    } else {
      btnClass = 'appstore-card-btn appstore-card-btn-primary';
      btnLabel = 'Installieren';
      btnAction = 'onclick="event.stopPropagation();_appstoreStartInstall(\'' + esc(app.key) + '\')"';
    }
  return '<div class="appstore-card" onclick="_appstoreNavigate(\'app:' + esc(app.key) + '\')">' +
    '<div class="appstore-card-icon-wrap">' + app.icon + '</div>' +
    '<div class="appstore-card-body">' +
      '<div class="appstore-card-name">' + esc(app.name) + '</div>' +
      '<div class="appstore-card-desc">' + esc(app.desc) + '</div>' +
    '</div>' +
    '<div class="appstore-card-actions">' +
      '<button class="' + btnClass + '" ' + btnAction + '>' + btnLabel +
      '</button>' +
    '</div>' +
    (app.update_available
      ? '<div class="appstore-card-actions" style="margin-top:4px;">' +
        '<button class="appstore-card-btn appstore-card-btn-primary" style="font-size:10px;padding:3px 8px;width:auto;" ' +
        'onclick="event.stopPropagation();_appstoreStartInstall(\'' + esc(app.key) + '\')">⬆ ' +
        (typeof t === 'function' ? t('appstore_update_btn') : 'Update') +
        '</button></div>'
      : '') +
  '</div>';
}

function _buildAppstoreGridCardHtml(app) {
  const isInstalled = app.status && app.status.installed;
  const isPlanned = app.availability === 'planned';
  const isMailApp = app.key === 'imap-mail';
  let btnClass, btnLabel, btnAction;
  if (isMailApp) {
    btnClass = 'appstore-card-btn ' + (app.space_active ? 'appstore-card-btn-success' : 'appstore-card-btn-primary');
    btnLabel = app.space_active ? 'Mail verwalten' : 'Mail einrichten';
    btnAction = 'onclick="event.stopPropagation();_appstoreOpenMailSettings()"';
  } else if (isInstalled) {
    btnClass = 'appstore-card-btn appstore-card-btn-success';
    btnLabel = (typeof t === 'function' ? t('appstore_installed') : '✓ Installiert');
    btnAction = 'onclick="event.stopPropagation();_appstoreUninstall(\'' + esc(app.key) + '\')"';
  } else if (isPlanned) {
    btnClass = 'appstore-card-btn appstore-card-btn-disabled';
    btnLabel = 'Demnächst';
    btnAction = 'disabled';
  } else {
    btnClass = 'appstore-card-btn appstore-card-btn-primary';
    btnLabel = (typeof t === 'function' ? t('appstore_install') : 'Installieren');
    btnAction = 'onclick="event.stopPropagation();_appstoreStartInstall(\'' + esc(app.key) + '\')"';
  }
  const tagsHtml = (app.tags || []).map(t => '<span class="appstore-card-tag">' + esc(t) + '</span>').join('');
  let html = '<div class="appstore-grid-card" onclick="_appstoreNavigate(\'app:' + esc(app.key) + '\')">' +
    '<div style="display:flex;align-items:center;gap:12px;">' +
      '<div class="appstore-card-icon-wrap">' + app.icon + '</div>' +
      '<div class="appstore-card-body">' +
        '<div class="appstore-card-name">' + esc(app.name) + '</div>' +
        '<div class="appstore-card-dev">' + esc(app.dev) + '</div>' +
      '</div>' +
    '</div>' +
    '<div class="appstore-card-desc">' + esc(app.desc) + '</div>' +
    '<div class="appstore-card-tags">' + tagsHtml + '</div>' +
    '<div class="appstore-card-actions">' +
      '<button class="' + btnClass + '" ' + btnAction + '>' + btnLabel + '</button>' +
    '</div>';
  if (isInstalled && app.update_available) {
    html += '<div class="appstore-card-actions" style="margin-top:2px;">' +
      '<button class="appstore-card-btn appstore-card-btn-primary" style="font-size:10px;padding:3px 8px;width:auto;" ' +
      'onclick="event.stopPropagation();_appstoreStartInstall(\'' + esc(app.key) + '\')">⬆ ' +
      (typeof t === 'function' ? t('appstore_update_btn') : 'Update') +
      '</button></div>';
  }
  html += '</div>';
  return html;
}

function _buildSbNav() {
  const nav = document.getElementById('appstoreSbNav');
  if (!nav) return;
  let html = '';
  html += '<button class="appstore-sb-item' + (_appstoreCurrentPage === 'home' ? ' active' : '') +
    '" data-page="home" onclick="_appstoreNavigate(\'home\')">' +
    '<span class="appstore-sb-icon">🏠</span><span>' + _appstoreText('appstore_all', 'Alle') + '</span></button>';

  // "Meine Apps" section with live count
  const myAppsCount = _appstoreAppsCache.filter(a => a.status && a.status.installed).length;
  html += '<button class="appstore-sb-item' +
    (_appstoreCurrentPage === 'my-apps' ? ' active' : '') +
    '" data-page="my-apps" onclick="_appstoreNavigate(\'my-apps\')">' +
    '<span class="appstore-sb-icon">📦</span><span>' + _appstoreText('appstore_my_apps', 'Meine Apps') + '</span>' +
    '<span class="appstore-sb-badge">' + myAppsCount + '</span></button>';

  for (const cat of _appstoreCategories()) {
    const count = cat.count || _appstoreAppsCache.filter(a => a.cat === cat.key).length;
    html += '<button class="appstore-sb-item' +
      (_appstoreCurrentPage === 'category:' + cat.key ? ' active' : '') +
      '" data-page="category:' + esc(cat.key) + '" onclick="_appstoreNavigate(\'category:' + esc(cat.key) + '\')">' +
      '<span class="appstore-sb-icon">' + cat.icon + '</span><span>' + esc(cat.label) + '</span>' +
      '<span class="appstore-sb-badge">' + count + '</span></button>';
  }
  html += '<button class="appstore-sb-item' + (_appstoreCurrentPage === 'sdk' ? ' active' : '') +
    '" data-page="sdk" onclick="_appstoreNavigate(\'sdk\')">' +
    '<span class="appstore-sb-icon">📖</span><span>' + _appstoreText('appstore_sdk_docs', 'SDK Dokumentation') + '</span></button>';
  html += '<button class="appstore-sb-item' + (_appstoreCurrentPage === 'submit' ? ' active' : '') +
    '" data-page="submit" onclick="_appstoreNavigate(\'submit\')">' +
    '<span class="appstore-sb-icon">✍️</span><span>' + _appstoreText('appstore_submit_plugin', 'Plugin einreichen') + '</span></button>';
  nav.innerHTML = html;
}

async function _renderAppstoreSdk(container) {
  if (!container) return;
  container.innerHTML = _appstoreOfflineBanner() +
    '<div class="appstore-section appstore-docs">' +
      '<div class="appstore-section-head">' +
        '<h2 class="appstore-section-title">📖 ' + _appstoreText('appstore_sdk_docs', 'SDK Dokumentation') + '</h2>' +
      '</div>' +
      '<div class="appstore-skeleton-loading"><div class="appstore-setup-spinner"></div><span>Lade SDK...</span></div>' +
    '</div>';
  try {
    const data = await _appstoreApiWithTimeout('/api/appstore/sdk', 10000);
    if (!data || !data.success) throw new Error((data && data.error) || 'SDK konnte nicht geladen werden');
    container.innerHTML = _appstoreOfflineBanner() +
      '<div class="appstore-section appstore-docs">' +
        '<div class="appstore-section-head">' +
          '<h2 class="appstore-section-title">📖 ' + _appstoreText('appstore_sdk_docs', 'SDK Dokumentation') + '</h2>' +
          '<button class="appstore-card-btn appstore-card-btn-outline" style="width:auto;padding:6px 12px;" onclick="_appstoreNavigate(\'submit\')">Plugin einreichen</button>' +
        '</div>' +
        '<pre class="appstore-sdk-doc">' + esc(data.markdown || '') + '</pre>' +
      '</div>';
  } catch (err) {
    container.innerHTML = _renderAppstoreError(err.message || 'SDK konnte nicht geladen werden');
  }
  _buildSbNav();
}

function _renderAppstoreSubmit(container) {
  if (!container) return;
  const example = {
    key: "my_plugin",
    name: "My Plugin",
    icon: "🧩",
    cat: "Developer Tools",
    dev: "Your Name",
    version: "0.1.0",
    description: "Kurze Beschreibung der Integration.",
    setup_steps: []
  };
  container.innerHTML = _appstoreOfflineBanner() +
    '<div class="appstore-section appstore-submit">' +
      '<div class="appstore-section-head">' +
        '<h2 class="appstore-section-title">✍️ ' + _appstoreText('appstore_submit_plugin', 'Plugin einreichen') + '</h2>' +
      '</div>' +
      '<p class="appstore-submit-copy">Manifest als JSON einfügen. Es wird lokal unter <code>home/appstore/submitted/</code> abgelegt und nicht automatisch aktiviert.</p>' +
      '<textarea id="appstoreSubmitManifest" class="appstore-submit-textarea" spellcheck="false">' + esc(JSON.stringify(example, null, 2)) + '</textarea>' +
      '<div class="appstore-submit-actions">' +
        '<button class="appstore-card-btn appstore-card-btn-primary" style="width:auto;padding:8px 16px;" onclick="_appstoreSubmitManifest(this)">Manifest prüfen und einreichen</button>' +
        '<button class="appstore-card-btn appstore-card-btn-outline" style="width:auto;padding:8px 16px;" onclick="_appstoreNavigate(\'sdk\')">SDK lesen</button>' +
      '</div>' +
      '<div id="appstoreSubmitResult" class="appstore-submit-result" aria-live="polite"></div>' +
    '</div>';
  _buildSbNav();
}

async function _appstoreSubmitManifest(btn) {
  const input = document.getElementById('appstoreSubmitManifest');
  const resultEl = document.getElementById('appstoreSubmitResult');
  if (!input || !resultEl) return;
  let manifest;
  try {
    manifest = JSON.parse(input.value || '{}');
  } catch (err) {
    resultEl.className = 'appstore-submit-result error';
    resultEl.textContent = 'Ungültiges JSON: ' + (err.message || err);
    return;
  }
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Reiche ein...';
  }
  try {
    const response = await api('/api/appstore/submit', {
      method: 'POST',
      body: JSON.stringify({ manifest }),
    });
    if (!response || !response.success) throw new Error((response && response.error) || 'Einreichen fehlgeschlagen');
    resultEl.className = 'appstore-submit-result success';
    resultEl.textContent = response.message + (response.path ? ': ' + response.path : '');
  } catch (err) {
    resultEl.className = 'appstore-submit-result error';
    resultEl.textContent = err.message || 'Einreichen fehlgeschlagen';
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Manifest prüfen und einreichen';
    }
  }
}

// ── App-Suche (Echtzeit-Filter) ──
function _appstoreSearch(query) {
  const container = document.getElementById('appstoreContent');
  if (!container) return;
  const q = (query || '').trim().toLowerCase();
  if (!q) {
    // Clear filter → navigate back to current page
    _appstoreNavigate(_appstoreCurrentPage);
    return;
  }
  // Filter cache by name, description, tags
  const results = _appstoreAppsCache.filter(a => {
    const name = (a.name || '').toLowerCase();
    const desc = (a.desc || '').toLowerCase();
    const fullDesc = (a.fullDesc || '').toLowerCase();
    const tags = (a.tags || []).join(' ').toLowerCase();
    return name.includes(q) || desc.includes(q) || fullDesc.includes(q) || tags.includes(q);
  });
  // Show results
  let html = '<div class="appstore-section">' +
    '<div class="appstore-section-head">' +
      '<h2 class="appstore-section-title">🔍 Suchergebnisse für "' + esc(q) + '"</h2>' +
      '<span class="appstore-cat-header-count">' + results.length + ' Treffer</span>' +
    '</div>' +
    '<div class="appstore-grid">';
  if (results.length === 0) {
    html += '<div style="grid-column:1/-1;padding:32px;text-align:center;color:var(--muted);font-size:13px;">' +
      'Keine Apps gefunden für "' + esc(q) + '"' +
      '</div>';
  } else {
    for (const app of results) {
      html += _buildAppstoreGridCardHtml(app);
    }
  }
  html += '</div></div>';
  container.innerHTML = html;
  // Update breadcrumb
  const bc = document.getElementById('appstoreBreadcrumb');
  if (bc) {
    bc.innerHTML = '<span class="appstore-breadcrumb-item" onclick="_appstoreNavigate(\'home\')">Start</span>' +
      '<span class="appstore-breadcrumb-sep">›</span>' +
      '<span class="appstore-breadcrumb-current">Suche: ' + esc(q) + '</span>';
  }
  document.getElementById('appstoreTopbarCount').textContent = results.length + ' Treffer';
}

// ── Setup-Assistent (Multi-Step-Overlay) ──────────────────────────────────

let _appstoreSetupApp = null;          // the app being installed
let _appstoreSetupStep = 0;            // current step index
let _appstoreSetupValues = {};         // collected values {env_key: value}
let _appstoreSetupInstalling = false;  // true while POST is in flight

function _appstoreStartInstall(appKey) {
  const app = _appstoreAppsCache.find(a => a.key === appKey);
  if (!app) return;
  if (app.key === 'imap-mail') {
    _appstoreOpenMailSettings();
    return;
  }
  if (app.status && app.status.installed) {
    alert('Diese App ist bereits installiert.');
    return;
  }
  _appstoreSetupApp = app;
  _appstoreSetupStep = 0;
  _appstoreSetupValues = {};
  for (const step of (app.setup_steps || [])) {
    if (step && step.env_key && step.default !== undefined) {
      _appstoreSetupValues[step.env_key] = String(step.default);
    }
  }
  _appstoreSetupInstalling = false;
  _appstoreRenderSetupOverlay();
}

function _appstoreCloseSetup() {
  const overlay = document.getElementById('appstoreSetupOverlay');
  if (overlay) overlay.classList.add('appstore-overlay-closing');
  setTimeout(() => {
    if (overlay) overlay.remove();
    _appstoreSetupApp = null;
    _appstoreSetupStep = 0;
    _appstoreSetupValues = {};
    _appstoreSetupInstalling = false;
  }, 200);
}

function _appstoreRenderSetupOverlay() {
  const app = _appstoreSetupApp;
  if (!app) return;

  // Remove existing overlay if any
  const existing = document.getElementById('appstoreSetupOverlay');
  if (existing) existing.remove();

  const steps = app.setup_steps || [];
  const totalSteps = steps.length + 1; // +1 for review step
  const currentStep = _appstoreSetupStep;
  const isReview = currentStep >= steps.length;
  const isLastStep = currentStep >= totalSteps - 1;
  const isInstalling = _appstoreSetupInstalling;

  // Build overlay
  const overlay = document.createElement('div');
  overlay.id = 'appstoreSetupOverlay';
  overlay.className = 'appstore-overlay';

  // Step indicator dots
  let stepDotsHtml = '';
  for (let i = 0; i < totalSteps; i++) {
    const label = i < steps.length ? (steps[i].label || 'Schritt ' + (i+1)) : 'Übersicht';
    const cls = i < currentStep ? 'appstore-step-dot done' : (i === currentStep ? 'appstore-step-dot active' : 'appstore-step-dot');
    stepDotsHtml += '<div class="' + cls + '" title="' + esc(label) + '"><span class="appstore-step-dot-num">' + (i < steps.length ? (i+1) : '✓') + '</span></div>';
    if (i < totalSteps - 1) {
      stepDotsHtml += '<div class="appstore-step-line' + (i < currentStep ? ' done' : '') + '"></div>';
    }
  }

  // Build body (current step form or review)
  let bodyHtml = '';
  if (!isInstalling) {
    if (!isReview) {
      // Render a setup step
      const step = steps[currentStep];
      if (!step) {
        bodyHtml = '<div style="padding:24px;color:var(--error)">Fehler: Schritt-Definition fehlt.</div>';
      } else {
        bodyHtml = _appstoreBuildStepForm(step, currentStep);
      }
    } else {
      // Render review step
      bodyHtml = _appstoreBuildReviewStep(app, steps);
    }
  } else {
    // Show installation in progress
    bodyHtml =
      '<div class="appstore-setup-installing">' +
        '<div class="appstore-setup-spinner"></div>' +
        '<div class="appstore-setup-installing-text">Installiere ' + esc(app.name) + '...</div>' +
      '</div>';
  }

  // Footer buttons
  let footerHtml = '';
  if (!isInstalling) {
    if (!isReview) {
      footerHtml =
        '<button class="appstore-step-btn appstore-step-btn-outline" onclick="_appstoreCloseSetup()" type="button">Abbrechen</button>' +
        (currentStep > 0 ? '<button class="appstore-step-btn" onclick="_appstorePrevStep()" type="button">← Zurück</button>' : '') +
        '<button class="appstore-step-btn appstore-step-btn-primary" onclick="_appstoreNextStep()" type="button" style="margin-left:auto;">' +
          (currentStep === steps.length - 1 ? 'Weiter zur Übersicht' : 'Weiter') +
        ' →</button>';
    } else {
      footerHtml =
        '<button class="appstore-step-btn appstore-step-btn-outline" onclick="_appstoreCloseSetup()" type="button">Abbrechen</button>' +
        (steps.length > 0 ? '<button class="appstore-step-btn" onclick="_appstorePrevStep()" type="button">← Zurück</button>' : '') +
        '<button class="appstore-step-btn appstore-step-btn-success" onclick="_appstoreDoInstall()" type="button" style="margin-left:auto;font-weight:700;">⬇ Installieren</button>';
    }
  } else {
    footerHtml = '<div style="padding:8px 0;text-align:center;color:var(--muted);font-size:12px;">Bitte warten...</div>';
  }

  overlay.innerHTML =
    '<div class="appstore-modal">' +
      '<div class="appstore-modal-header">' +
        '<div class="appstore-modal-header-left">' +
          '<span class="appstore-modal-header-icon">' + app.icon + '</span>' +
          '<div>' +
            '<div class="appstore-modal-header-title">' + esc(app.name) + '</div>' +
            '<div class="appstore-modal-header-sub">' + (!isInstalling ? (isReview ? 'Übersicht' : 'Schritt ' + (currentStep + 1) + ' von ' + totalSteps) : 'Installiere...') + '</div>' +
          '</div>' +
        '</div>' +
        (!isInstalling ? '<button class="appstore-modal-close" onclick="_appstoreCloseSetup()">✕</button>' : '') +
      '</div>' +
      '<div class="appstore-step-indicator">' + stepDotsHtml + '</div>' +
      '<div class="appstore-modal-body">' + bodyHtml + '</div>' +
      '<div class="appstore-modal-footer">' + footerHtml + '</div>' +
    '</div>';

  document.body.appendChild(overlay);

  // Focus first input
  if (!isInstalling && !isReview) {
    const firstInput = overlay.querySelector('input, select');
    if (firstInput) setTimeout(() => firstInput.focus(), 100);
  }
}

function _appstoreBuildStepForm(step, stepIndex) {
  const val = _appstoreSetupValues[step.env_key] || '';
  let inputHtml = '';

  if (step.type === 'password') {
    inputHtml =
      '<div class="appstore-step-password-wrap">' +
        '<input type="password" class="appstore-step-input" id="appstoreSetupInput_' + stepIndex + '"' +
          ' data-env-key="' + esc(step.env_key) + '"' +
          ' value="' + esc(val) + '"' +
          ' placeholder="' + esc(step.label || '') + '"' +
          ' oninput="_appstoreSetupValueChange(this)"' +
          (step.required !== false ? ' required' : '') + '>' +
        '<button class="appstore-step-password-toggle" type="button" onclick="_appstoreTogglePassword(this)" title="Anzeigen">👁️</button>' +
      '</div>';
  } else if (step.type === 'toggle') {
    inputHtml =
      '<label class="appstore-step-toggle-wrap">' +
        '<input type="checkbox" class="appstore-step-toggle" id="appstoreSetupInput_' + stepIndex + '"' +
          ' data-env-key="' + esc(step.env_key) + '"' +
          (val === 'true' || val === true ? ' checked' : '') +
          ' onchange="_appstoreSetupValueChange(this)">' +
        '<span class="appstore-step-toggle-slider"></span>' +
        '<span class="appstore-step-toggle-label">' + esc(step.label || '') + '</span>' +
      '</label>';
  } else {
    const inputType = step.type === 'number' ? 'number' : 'text';
    inputHtml =
      '<input type="' + inputType + '" class="appstore-step-input" id="appstoreSetupInput_' + stepIndex + '"' +
        ' data-env-key="' + esc(step.env_key) + '"' +
        ' value="' + esc(val) + '"' +
        (step.type === 'number' ? ' inputmode="numeric" step="1"' : '') +
        ' placeholder="' + esc(step.label || '') + '"' +
        ' oninput="_appstoreSetupValueChange(this)"' +
        (step.required !== false ? ' required' : '') + '>';
  }

  return '<div class="appstore-step-form">' +
    '<label class="appstore-step-label">' + esc(step.label || 'Schritt ' + (stepIndex + 1)) + '</label>' +
    (step.hint ? '<div class="appstore-step-hint">' + esc(step.hint) + '</div>' : '') +
    inputHtml +
  '</div>';
}

function _appstoreBuildReviewStep(app, steps) {
  // Collected values table
  let valuesHtml = '';
  for (const step of steps) {
    const val = _appstoreSetupValues[step.env_key] || '';
    valuesHtml += '<tr><td class="appstore-review-label">' + esc(step.label || step.env_key) + '</td>' +
      '<td class="appstore-review-value">' + (step.type === 'password' ? '••••••••' : esc(val)) + '</td></tr>';
  }

  // Config changes
  let configHtml = '';
  const configChanges = app.config_changes || [];
  if (configChanges.length > 0) {
    configHtml = '<div class="appstore-review-section"><div class="appstore-review-section-title">⚙️ Config-Änderungen</div>' +
      '<table class="appstore-review-table"><thead><tr><th>Pfad</th><th>Wert</th></tr></thead><tbody>';
    for (const ch of configChanges) {
      configHtml += '<tr><td class="appstore-review-config-path">' + esc(ch.path || '') + '</td>' +
        '<td class="appstore-review-config-value">' + esc(JSON.stringify(ch.value)) + '</td></tr>';
    }
    configHtml += '</tbody></table></div>';
  }

  // Tools to enable
  let toolsHtml = '';
  const tools = app.tools_enable || [];
  if (tools.length > 0) {
    toolsHtml = '<div class="appstore-review-section"><div class="appstore-review-section-title">🔧 Tools aktivieren</div>' +
      '<div class="appstore-review-tools">' +
      tools.map(t => '<span class="appstore-review-tool-badge">' + esc(t) + '</span>').join('') +
      '</div></div>';
  }

  // Gateway restart hint
  let gatewayHtml = '';
  if (app.gateway_restart) {
    gatewayHtml = '<div class="appstore-review-section" style="background:var(--warning);padding:8px 12px;border-radius:8px;font-size:12px;color:var(--bg);">' +
      '⚠️ Nach der Installation wird ein <strong>Gateway-Neustart</strong> erforderlich sein.' +
      '</div>';
  }

  return '<div class="appstore-review">' +
    (valuesHtml ? '<div class="appstore-review-section"><div class="appstore-review-section-title">📋 Konfiguration</div>' +
      '<table class="appstore-review-table"><thead><tr><th>Feld</th><th>Wert</th></tr></thead><tbody>' +
      valuesHtml + '</tbody></table></div>' : '') +
    configHtml +
    toolsHtml +
    gatewayHtml +
  '</div>';
}

function _appstoreSetupValueChange(el) {
  const key = el.dataset.envKey;
  if (!key) return;
  if (el.type === 'checkbox') {
    _appstoreSetupValues[key] = el.checked ? 'true' : 'false';
  } else {
    _appstoreSetupValues[key] = el.value;
  }
}

function _appstoreTogglePassword(btn) {
  const wrap = btn.parentElement;
  const input = wrap.querySelector('input');
  if (!input) return;
  if (input.type === 'password') {
    input.type = 'text';
    btn.textContent = '🙈';
  } else {
    input.type = 'password';
    btn.textContent = '👁️';
  }
}

function _appstoreNextStep() {
  const app = _appstoreSetupApp;
  if (!app) return;
  const steps = app.setup_steps || [];

  // Validate current step
  if (_appstoreSetupStep < steps.length) {
    const step = steps[_appstoreSetupStep];
    if (step.required !== false) {
      const val = _appstoreSetupValues[step.env_key];
      if (!val || (typeof val === 'string' && !val.trim())) {
        alert('Bitte fülle das Feld "' + (step.label || step.env_key) + '" aus.');
        const input = document.getElementById('appstoreSetupInput_' + _appstoreSetupStep);
        if (input) { input.focus(); input.style.borderColor = 'var(--error)'; }
        return;
      }
    }
  }

  _appstoreSetupStep++;
  _appstoreRenderSetupOverlay();
}

function _appstorePrevStep() {
  if (_appstoreSetupStep > 0) {
    _appstoreSetupStep--;
    _appstoreRenderSetupOverlay();
  }
}

async function _appstoreDoInstall() {
  const app = _appstoreSetupApp;
  if (!app || _appstoreSetupInstalling) return;
  _appstoreSetupInstalling = true;
  _appstoreRenderSetupOverlay();

  try {
    const result = await api('/api/appstore/install', {
      method: 'POST',
      body: JSON.stringify({ key: app.key, values: _appstoreSetupValues }),
    });

    if (result && result.success) {
      // Show success
      _appstoreShowInstallSuccess(app, result);
    } else {
      const errMsg = (result && result.error) || 'Unbekannter Fehler';
      _appstoreShowInstallError(app, errMsg);
    }
  } catch (err) {
    _appstoreShowInstallError(app, err.message || 'Netzwerkfehler');
  }
}

function _appstoreShowInstallSuccess(app, result) {
  const overlay = document.getElementById('appstoreSetupOverlay');
  if (!overlay) return;

  overlay.querySelector('.appstore-modal-header-title').textContent = app.name;
  overlay.querySelector('.appstore-modal-header-sub').textContent = 'Installation erfolgreich ✓';

  overlay.querySelector('.appstore-modal-body').innerHTML =
    '<div class="appstore-setup-success">' +
      '<div class="appstore-setup-success-icon">✅</div>' +
      '<div class="appstore-setup-success-title">' + esc(app.name) + ' wurde installiert!</div>' +
      '<div class="appstore-setup-success-files">' +
        (result.changed_files && result.changed_files.length > 0
          ? result.changed_files.map(f => '<div class="appstore-setup-success-file">📄 ' + esc(f) + '</div>').join('')
          : '') +
      '</div>' +
      (app.gateway_restart
        ? '<div class="appstore-setup-success-restart"><span>⚠️</span> Der Gateway muss neu gestartet werden, damit die Änderungen wirksam werden.<br><button class="appstore-step-btn appstore-step-btn-primary" style="margin-top:8px;" onclick="_appstoreRestartGateway(this)">Gateway neustarten</button></div>'
        : '') +
    '</div>';

  overlay.querySelector('.appstore-modal-footer').innerHTML =
    '<button class="appstore-step-btn appstore-step-btn-primary" onclick="_appstoreCloseSetup(); loadAppstorePanel();" style="margin-left:auto;">Fertig</button>';

  // Update cache: mark as installed
  const cached = _appstoreAppsCache.find(a => a.key === app.key);
  if (cached) {
    cached.status = cached.status || {};
    cached.status.installed = true;
    cached.status.version_installed = app.version || '?';
  }
}

function _appstoreShowInstallError(app, errMsg) {
  const overlay = document.getElementById('appstoreSetupOverlay');
  if (!overlay) return;

  overlay.querySelector('.appstore-modal-header-title').textContent = app.name;
  overlay.querySelector('.appstore-modal-header-sub').textContent = 'Fehler bei der Installation';

  overlay.querySelector('.appstore-modal-body').innerHTML =
    '<div class="appstore-setup-error">' +
      '<div class="appstore-setup-error-icon">❌</div>' +
      '<div class="appstore-setup-error-title">Installation fehlgeschlagen</div>' +
      '<div class="appstore-setup-error-msg">' + esc(errMsg) + '</div>' +
    '</div>';

  overlay.querySelector('.appstore-modal-footer').innerHTML =
    '<button class="appstore-step-btn appstore-step-btn-outline" onclick="_appstoreCloseSetup()">Schließen</button>' +
    '<button class="appstore-step-btn appstore-step-btn-primary" onclick="_appstoreSetupInstalling = false; _appstoreSetupStep = _appstoreSetupStep; _appstoreRenderSetupOverlay();" style="margin-left:auto;">Erneut versuchen</button>';

  _appstoreSetupInstalling = false;
}

async function _appstoreRestartGateway(btn) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Starte neu...';
  }
  try {
    // Use the hermes CLI through the gateway restart endpoint
    const result = await api('/api/gateway/restart', { method: 'POST' });
    if (btn) {
      btn.textContent = '✓ Gateway wird neu gestartet';
      btn.style.background = 'var(--success)';
    }
  } catch (err) {
    if (btn) {
      btn.textContent = 'Gateway-Neustart nicht verfügbar. Bitte starte Sidekick manuell neu.';
      btn.disabled = false;
      btn.style.background = 'var(--warning)';
    }
  }
}

async function _appstoreOpenGmailSettings() {
  _appstoreCloseOverlay('gmailAppSettingsOverlay');
  let spaces = [];
  try {
    const data = await fetchJson('api/spaces');
    spaces = data.spaces || [];
  } catch {}
  const overlay = document.createElement('div');
  overlay.className = 'appstore-setup-overlay';
  overlay.id = 'gmailAppSettingsOverlay';
  const rows = spaces.map(s => {
    const slug = esc(s.slug || s.name || '');
    const name = esc(s.name || s.slug || '');
    const slugMeta = slug && slug !== name
      ? '<span style="margin-left:auto;color:var(--muted);font-size:11px;">' + slug + '</span>'
      : '';
    return '<label style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--border2);border-radius:8px;background:var(--surface);">' +
      '<input type="checkbox" class="gmail-app-space" value="' + slug + '" ' + (slug === 'testspace-chatgpt' ? 'checked' : '') + '> ' +
      '<span style="font-weight:600;color:var(--text);">' + name + '</span>' +
      slugMeta +
    '</label>';
  }).join('');
  overlay.innerHTML =
    '<div class="appstore-setup-modal" style="max-width:720px;">' +
      '<div class="appstore-setup-header">' +
        '<div><div class="appstore-setup-title">📧 Gmail App Settings</div>' +
        '<div class="appstore-setup-subtitle">Accounts verbinden und Space-Zugriff festlegen.</div></div>' +
        '<button class="appstore-modal-close" onclick="var e=document.getElementById(\'gmailAppSettingsOverlay\');if(e)e.style.display=\'none\'">✕</button>' +
      '</div>' +
      '<div class="appstore-setup-body" style="display:grid;gap:14px;">' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">' +
          '<label class="appstore-step-field"><span>Account-ID</span><input id="gmailAppAccountId" class="appstore-step-input" value="dominik" placeholder="dominik"></label>' +
          '<label class="appstore-step-field"><span>Gmail-Adresse</span><input id="gmailAppEmail" class="appstore-step-input" type="email" placeholder="name@gmail.com"></label>' +
        '</div>' +
        '<label class="appstore-step-field"><span>Google App-Passwort</span><input id="gmailAppPassword" class="appstore-step-input" type="password" placeholder="xxxx xxxx xxxx xxxx"></label>' +
        '<div><div style="font-size:12px;color:var(--muted);margin-bottom:8px;">Spaces mit Zugriff</div>' +
          '<div style="display:grid;gap:8px;max-height:240px;overflow:auto;">' + (rows || '<div style="color:var(--muted);">Keine Spaces gefunden.</div>') + '</div>' +
        '</div>' +
        '<div id="gmailAppSettingsStatus" style="font-size:12px;color:var(--muted);"></div>' +
      '</div>' +
      '<div class="appstore-setup-footer">' +
        '<button class="appstore-step-btn appstore-step-btn-outline" onclick="var e=document.getElementById(\'gmailAppSettingsOverlay\');if(e)e.style.display=\'none\'">Abbrechen</button>' +
        '<button class="appstore-step-btn appstore-step-btn-primary" onclick="_appstoreSaveGmailSettings(this)" style="margin-left:auto;">Speichern</button>' +
      '</div>' +
    '</div>';
  overlay.querySelectorAll('.appstore-modal-close,.appstore-step-btn-outline').forEach(btn => {
    btn.onclick = function(ev) {
      ev.preventDefault();
      _appstoreCloseOverlay('gmailAppSettingsOverlay');
    };
  });
  document.body.appendChild(overlay);
}

async function _appstoreSaveGmailSettings(btn) {
  const status = document.getElementById('gmailAppSettingsStatus');
  const accountId = (document.getElementById('gmailAppAccountId')?.value || '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-');
  const email = (document.getElementById('gmailAppEmail')?.value || '').trim();
  const password = document.getElementById('gmailAppPassword')?.value || '';
  const selected = [...document.querySelectorAll('.gmail-app-space:checked')].map(i => i.value).filter(Boolean);
  if (!accountId || !email || !password || selected.length === 0) {
    if (status) status.textContent = 'Bitte Account-ID, Gmail-Adresse, App-Passwort und mindestens einen Space ausfüllen.';
    return;
  }
  if (btn) btn.disabled = true;
  try {
    for (const slug of selected) {
      let current = {};
      try {
        const cfg = await fetchJson('api/space/config?slug=' + encodeURIComponent(slug));
        current = cfg.config || {};
      } catch {}
      const gmail = current.gmail && typeof current.gmail === 'object' ? current.gmail : {};
      const accounts = gmail.accounts && typeof gmail.accounts === 'object' ? gmail.accounts : {};
      accounts[accountId] = { email, password };
      await fetchJson('api/space/config', {
        method: 'POST',
        body: JSON.stringify({ slug, gmail: { ...gmail, enabled: true, accounts } }),
      });
    }
    localStorage.setItem('sidekick-app-gmail-enabled', '1');
    if (status) status.textContent = 'Gmail gespeichert und für ' + selected.length + ' Space(s) aktiviert.';
    if (typeof gmailToast === 'function') gmailToast('Gmail-App gespeichert', 'success');
  } catch (e) {
    if (status) status.textContent = e.message || String(e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function _appstoreOpenMailSettings() {
  _appstoreCloseOverlay('mailAppSettingsOverlay');

  let currentConfig = { inboxes: [] };
  try {
    const res = await api('/api/mail/config');
    if (res && res.success && res.config && Array.isArray(res.config.inboxes)) {
      currentConfig = res.config;
    }
  } catch (err) {
    console.warn('[appstore] failed to load current mail config:', err);
  }

  const inbox = Array.isArray(currentConfig.inboxes) && currentConfig.inboxes.length > 0
    ? (currentConfig.inboxes.find(inbox => inbox && inbox.default) || currentConfig.inboxes[0] || {})
    : {};
  const overlay = document.createElement('div');
  overlay.className = 'appstore-setup-overlay';
  overlay.id = 'mailAppSettingsOverlay';
  overlay.innerHTML =
    '<div class="appstore-setup-modal" style="max-width:760px;">' +
      '<div class="appstore-setup-header">' +
        '<div><div class="appstore-setup-title">📧 Mail einrichten</div>' +
        '<div class="appstore-setup-subtitle">E-Mail und Passwort eingeben. Sidekick erkennt den Anbieter automatisch, richtet den Space ein und aktiviert Mail im Hintergrund.</div></div>' +
        '<button class="appstore-modal-close" onclick="var e=document.getElementById(\'mailAppSettingsOverlay\');if(e)e.style.display=\'none\'">✕</button>' +
      '</div>' +
      '<div class="appstore-setup-body" style="display:grid;gap:14px;">' +
        '<div class="appstore-step-hint">Bekannte Anbieter werden automatisch erkannt. Bei unbekannten Domains versucht Sidekick generische IMAP/SMTP-Hostnamen. Du musst nur E-Mail und Passwort eingeben.</div>' +
        '<label class="appstore-step-field"><span>E-Mail-Adresse</span><input id="mailAppEmail" class="appstore-step-input" type="email" value="' + esc(inbox.imap_user || inbox.smtp_user || '') + '" placeholder="name@example.com"></label>' +
        '<label class="appstore-step-field"><span>Passwort</span><input id="mailAppPassword" class="appstore-step-input" type="password" placeholder="App-Passwort oder normales Passwort"></label>' +
        '<div id="mailAppSettingsStatus" style="font-size:12px;color:var(--muted);line-height:1.5;"></div>' +
      '</div>' +
      '<div class="appstore-setup-footer">' +
        '<button class="appstore-step-btn appstore-step-btn-outline" onclick="var e=document.getElementById(\'mailAppSettingsOverlay\');if(e)e.style.display=\'none\'">Abbrechen</button>' +
        '<button class="appstore-step-btn appstore-step-btn-primary" onclick="_appstoreSaveMailSettings(this)" style="margin-left:auto;">Mail einrichten</button>' +
      '</div>' +
    '</div>';

  document.body.appendChild(overlay);
  overlay.querySelectorAll('.appstore-modal-close,.appstore-step-btn-outline').forEach(btn => {
    btn.onclick = function(ev) {
      ev.preventDefault();
      _appstoreCloseOverlay('mailAppSettingsOverlay');
    };
  });
}

async function _appstoreSaveMailSettings(btn) {
  const status = document.getElementById('mailAppSettingsStatus');
  const email = (document.getElementById('mailAppEmail')?.value || '').trim();
  const password = document.getElementById('mailAppPassword')?.value || '';

  if (!email || !password) {
    if (status) status.textContent = 'Bitte E-Mail-Adresse und Passwort ausfüllen.';
    return;
  }

  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Einrichten...';
  }
  if (status) status.textContent = 'Erkenne Anbieter und schreibe Mail-Konfiguration...';

  try {
    const result = await api('/api/mail/setup', {
      method: 'POST',
      body: JSON.stringify({
        email: email,
        password: password,
        activate: true,
      }),
    });

    if (!result || !result.success) {
      throw new Error((result && result.error) || 'Unbekannter Fehler');
    }

    if (status) {
      const warnings = Array.isArray(result.warnings) && result.warnings.length > 0 ? ' ' + result.warnings.join(' ') : '';
      status.textContent = 'Mail erfolgreich eingerichtet: ' + (result.provider || 'Mail') + '.' + warnings;
    }
    if (typeof showToast === 'function') showToast('Mail eingerichtet', 'success');
    loadAppstorePanel();
    if (typeof loadMailPanel === 'function') loadMailPanel();
    setTimeout(function() {
      _appstoreCloseOverlay('mailAppSettingsOverlay');
    }, 350);
  } catch (err) {
    if (status) status.textContent = 'Mail-Setup fehlgeschlagen: ' + (err && err.message ? err.message : String(err));
    if (typeof showToast === 'function') showToast('Mail-Setup fehlgeschlagen', 'error');
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = 'Mail einrichten';
    }
  }
}

async function _appstoreOpenDiscordSettings() {
  _appstoreCloseOverlay('discordAppSettingsOverlay');
  let spaces = [];
  try {
    const data = await fetchJson('api/spaces');
    spaces = data.spaces || [];
  } catch {}
  const overlay = document.createElement('div');
  overlay.className = 'appstore-setup-overlay';
  overlay.id = 'discordAppSettingsOverlay';
  const rows = spaces.map(s => {
    const slug = esc(s.slug || s.name || '');
    const name = esc(s.name || s.slug || '');
    const slugMeta = slug && slug !== name
      ? '<span style="margin-left:auto;color:var(--muted);font-size:11px;">' + slug + '</span>'
      : '';
    return '<label style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--border2);border-radius:8px;background:var(--surface);">' +
      '<input type="checkbox" class="discord-app-space" value="' + slug + '"> ' +
      '<span style="font-weight:600;color:var(--text);">' + name + '</span>' +
      slugMeta +
    '</label>';
  }).join('');
  overlay.innerHTML =
    '<div class="appstore-setup-modal" style="max-width:760px;">' +
      '<div class="appstore-setup-header">' +
        '<div><div class="appstore-setup-title">💬 Discord App Settings</div>' +
        '<div class="appstore-setup-subtitle">Bot verbinden und Space-Zugriff festlegen.</div></div>' +
        '<button class="appstore-modal-close" onclick="var e=document.getElementById(\'discordAppSettingsOverlay\');if(e)e.style.display=\'none\'">✕</button>' +
      '</div>' +
      '<div class="appstore-setup-body" style="display:grid;gap:14px;">' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">' +
          '<label class="appstore-step-field"><span>Bot-ID / Alias</span><input id="discordAppBotId" class="appstore-step-input" value="default" placeholder="default"></label>' +
          '<label class="appstore-step-field"><span>Guild-ID</span><input id="discordAppGuildId" class="appstore-step-input" placeholder="Discord Server ID"></label>' +
        '</div>' +
        '<label class="appstore-step-field"><span>Bot-Token</span><input id="discordAppToken" class="appstore-step-input" type="password" placeholder="Bot Token"></label>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">' +
          '<label class="appstore-step-field"><span>Public Key</span><input id="discordAppPublicKey" class="appstore-step-input" type="password" placeholder="optional"></label>' +
          '<label class="appstore-step-field"><span>Client ID</span><input id="discordAppClientId" class="appstore-step-input" placeholder="optional"></label>' +
        '</div>' +
        '<div><div style="font-size:12px;color:var(--muted);margin-bottom:8px;">Spaces mit Zugriff</div>' +
          '<div style="display:grid;gap:8px;max-height:240px;overflow:auto;">' + (rows || '<div style="color:var(--muted);">Keine Spaces gefunden.</div>') + '</div>' +
        '</div>' +
        '<div id="discordAppSettingsStatus" style="font-size:12px;color:var(--muted);"></div>' +
      '</div>' +
      '<div class="appstore-setup-footer">' +
        '<button class="appstore-step-btn appstore-step-btn-outline" onclick="var e=document.getElementById(\'discordAppSettingsOverlay\');if(e)e.style.display=\'none\'">Abbrechen</button>' +
        '<button class="appstore-step-btn appstore-step-btn-primary" onclick="_appstoreSaveDiscordSettings(this)" style="margin-left:auto;">Speichern</button>' +
      '</div>' +
    '</div>';
  overlay.querySelectorAll('.appstore-modal-close,.appstore-step-btn-outline').forEach(btn => {
    btn.onclick = function(ev) {
      ev.preventDefault();
      _appstoreCloseOverlay('discordAppSettingsOverlay');
    };
  });
  document.body.appendChild(overlay);
}

async function _appstoreSaveDiscordSettings(btn) {
  const status = document.getElementById('discordAppSettingsStatus');
  const botId = (document.getElementById('discordAppBotId')?.value || '').trim().toLowerCase().replace(/[^a-z0-9_-]/g, '-');
  const guildId = (document.getElementById('discordAppGuildId')?.value || '').trim();
  const token = document.getElementById('discordAppToken')?.value || '';
  const publicKey = (document.getElementById('discordAppPublicKey')?.value || '').trim();
  const clientId = (document.getElementById('discordAppClientId')?.value || '').trim();
  const selected = [...document.querySelectorAll('.discord-app-space:checked')].map(i => i.value).filter(Boolean);
  if (!botId || !guildId || !token || selected.length === 0) {
    if (status) status.textContent = 'Bitte Bot-ID, Guild-ID, Bot-Token und mindestens einen Space ausfüllen.';
    return;
  }
  if (btn) btn.disabled = true;
  try {
    for (const slug of selected) {
      let current = {};
      try {
        const cfg = await fetchJson('api/space/config?slug=' + encodeURIComponent(slug));
        current = cfg.config || {};
      } catch {}
      const discord = current.discord && typeof current.discord === 'object' ? current.discord : {};
      const bots = discord.bots && typeof discord.bots === 'object' ? discord.bots : {};
      bots[botId] = { guild_id: guildId, token, public_key: publicKey, client_id: clientId };
      await fetchJson('api/space/config', {
        method: 'POST',
        body: JSON.stringify({ slug, discord: { ...discord, enabled: true, active_bot: botId, bots } }),
      });
    }
    localStorage.setItem('sidekick-app-discord-enabled', '1');
    if (status) status.textContent = 'Discord gespeichert und für ' + selected.length + ' Space(s) aktiviert.';
    if (typeof gmailToast === 'function') gmailToast('Discord-App gespeichert', 'success');
  } catch (e) {
    if (status) status.textContent = e.message || String(e);
  } finally {
    if (btn) btn.disabled = false;
  }
}

function _appstoreCloseOverlay(idOrElement) {
  const el = typeof idOrElement === 'string' ? document.getElementById(idOrElement) : idOrElement;
  if (el && el.parentNode) el.parentNode.removeChild(el);
}

window._appstoreOpenGmailSettings = _appstoreOpenGmailSettings;
window._appstoreSaveGmailSettings = _appstoreSaveGmailSettings;
window._appstoreOpenMailSettings = _appstoreOpenMailSettings;
window._appstoreSaveMailSettings = _appstoreSaveMailSettings;
window._appstoreOpenDiscordSettings = _appstoreOpenDiscordSettings;
window._appstoreSaveDiscordSettings = _appstoreSaveDiscordSettings;
window._appstoreCloseOverlay = _appstoreCloseOverlay;

document.addEventListener('click', function(e) {
  const btn = e.target && e.target.closest ? e.target.closest('.appstore-modal-close,.appstore-step-btn-outline') : null;
  if (!btn) return;
  const overlay = btn.closest && btn.closest('.appstore-setup-overlay');
  if (!overlay) return;
  e.preventDefault();
  e.stopPropagation();
  _appstoreCloseOverlay(overlay);
}, true);

async function _appstoreOpenAppSettings(appKey, settingsUrl) {
  const overlay = document.createElement('div');
  overlay.className = 'appstore-setup-overlay';
  overlay.id = 'appstoreSettingsOverlay';
  overlay.innerHTML = '<div class="appstore-setup-modal" style="max-width:600px;"><div style="text-align:center;padding:40px;color:var(--muted);"><div style="font-size:32px;margin-bottom:12px;">⏳</div><div>Einstellungen werden geladen...</div></div></div>';
  document.body.appendChild(overlay);
  
  try {
    const res = await fetch(settingsUrl);
    if (!res.ok) throw new Error('Einstellungen nicht verfügbar (' + res.status + ')');
    const settings = await res.json();
    if (settings && settings.success === false) {
      throw new Error(settings.error || 'Einstellungen nicht verfügbar');
    }
    
    let fieldsHtml = '';
    for (const [key, val] of Object.entries(settings)) {
      const label = key.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
      const type = typeof val === 'boolean' ? 'checkbox' : typeof val === 'number' ? 'number' : 'text';
      const checked = type === 'checkbox' && val ? 'checked' : '';
      fieldsHtml += '<label class="appstore-step-field" style="margin-bottom:12px;">' +
        '<span>' + esc(label) + '</span>' +
        (type === 'checkbox'
          ? '<input type="checkbox" id="sett_' + esc(key) + '" class="appstore-step-input" style="width:auto;height:20px;margin-left:8px;" ' + checked + '>'
          : '<input type="' + type + '" id="sett_' + esc(key) + '" class="appstore-step-input" value="' + esc(String(val)) + '" step="1">') +
      '</label>';
    }
    
    overlay.innerHTML =
      '<div class="appstore-setup-modal" style="max-width:600px;">' +
        '<div class="appstore-setup-header">' +
          '<div><div class="appstore-setup-title">⚙️ ' + esc(appKey) + ' Einstellungen</div>' +
          '<div class="appstore-setup-subtitle">Konfiguriere Auflösung, Overlays und mehr.</div></div>' +
          '<button class="appstore-modal-close" onclick="this.closest(\'.appstore-setup-overlay\').remove()">✕</button>' +
        '</div>' +
        '<div class="appstore-setup-body">' +
          fieldsHtml +
          '<div id="appstoreSettingsStatus" style="font-size:12px;color:var(--muted);margin-top:8px;"></div>' +
        '</div>' +
        '<div class="appstore-setup-footer">' +
          '<button class="appstore-step-btn appstore-step-btn-outline" onclick="var e=document.getElementById(\'appstoreSettingsOverlay\');if(e)e.style.display=\'none\'">Schliessen</button>' +
          '<button class="appstore-step-btn appstore-step-btn-primary" onclick="_appstoreSaveSettings(\'' + esc(settingsUrl) + '\')" style="margin-left:auto;">Speichern</button>' +
        '</div>' +
      '</div>';
  } catch (e) {
    overlay.innerHTML = '<div class="appstore-setup-modal" style="max-width:600px;"><div class="appstore-setup-header"><div><div class="appstore-setup-title">⚙️ ' + esc(appKey) + ' Einstellungen</div><div class="appstore-setup-subtitle">Konfiguration konnte nicht geladen werden.</div></div><button class="appstore-modal-close" onclick="_appstoreCloseOverlay(&quot;appstoreSettingsOverlay&quot;)">✕</button></div><div style="text-align:center;padding:40px;color:var(--error);"><div style="font-size:32px;margin-bottom:12px;">⚠️</div><div>Fehler beim Laden: ' + esc(e.message || String(e)) + '</div></div><div class="appstore-setup-footer"><button class="appstore-step-btn appstore-step-btn-outline" onclick="_appstoreCloseOverlay(&quot;appstoreSettingsOverlay&quot;)">Schliessen</button></div></div>';
  }
}

async function _appstoreSaveSettings(settingsUrl) {
  const status = document.getElementById('appstoreSettingsStatus');
  const inputs = document.querySelectorAll('#appstoreSettingsOverlay [id^="sett_"]');
  const payload = {};
  for (const inp of inputs) {
    const key = inp.id.replace('sett_', '');
    if (inp.type === 'checkbox') {
      payload[key] = inp.checked;
    } else if (inp.type === 'number') {
      payload[key] = inp.value ? Number(inp.value) : 0;
    } else {
      payload[key] = inp.value;
    }
  }
  try {
    const res = await fetch(settingsUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (res.ok) {
      if (status) status.textContent = '✅ Gespeichert';
      if (typeof gmailToast === 'function') gmailToast('Einstellungen gespeichert', 'success');
    } else {
      throw new Error(data.error || 'Fehler beim Speichern');
    }
  } catch (e) {
    if (status) status.textContent = '❌ ' + esc(e.message || String(e));
  }
}

async function _appstoreUninstall(appKey) {
  if (!confirm('Möchtest du "' + appKey + '" wirklich deinstallieren?')) return;

  try {
    const result = await api('/api/appstore/uninstall', {
      method: 'POST',
      body: JSON.stringify({ key: appKey }),
    });

    if (result && result.success) {
      // Update cache
      const cached = _appstoreAppsCache.find(a => a.key === appKey);
      if (cached) {
        cached.status = cached.status || {};
        cached.status.installed = false;
        cached.status.version_installed = null;
      }
      // Reload panel
      loadAppstorePanel();
    } else {
      const errMsg = (result && result.error) || 'Unbekannter Fehler';
      alert('Deinstallation fehlgeschlagen: ' + errMsg);
    }
  } catch (err) {
    alert('Deinstallation fehlgeschlagen: ' + (err.message || 'Netzwerkfehler'));
  }
}

// ── Providers panel ───────────────────────────────────────────────────────

const _providerCardEls = new Map(); // providerId → {card, statusDot, input, saveBtn, removeBtn}

function _providerText(key, fallback, ...args){
  try{
    if(typeof t === 'function'){
      const value = t(key, ...args);
      if(value && value !== key) return value;
    }
  }catch(_){}
  return typeof fallback === 'function' ? fallback(...args) : fallback;
}

async function loadProvidersPanel(){
  const list=$('providersList');
  const empty=$('providersEmpty');
  if(!list) return;
  try{
    const data=await api('/api/providers');
    const quota=await api('/api/provider/quota').catch(e=>({ok:false,status:'unavailable',quota:null,message:e.message||'Quota status unavailable'}));
    const providers=(data.providers||[]).filter(p=>p.configurable||p.is_oauth);
    list.innerHTML='';
    _providerCardEls.clear();
    const quotaCard=_buildProviderQuotaCard(quota);
    if(quotaCard) list.appendChild(quotaCard);
    if(providers.length===0){
      list.style.display='none';
      if(empty) empty.style.display='';
      return;
    }
    if(empty) empty.style.display='none';
    list.style.display='';
    for(const p of providers){
      list.appendChild(_buildProviderCard(p));
    }
  }catch(e){
    list.textContent='Failed to load providers: '+((e && e.message) || String(e));
    list.style.color='var(--error)';
    list.style.padding='12px';
    list.style.fontSize='13px';
  }
}

function _formatProviderQuotaMoney(value){
  if(value===null||value===undefined||value==='') return '—';
  const n=Number(value);
  if(!Number.isFinite(n)) return '—';
  return '$'+n.toFixed(2);
}

function _formatProviderQuotaPercent(value){
  if(value===null||value===undefined||value==='') return '—';
  if(typeof value==='string' && value.trim()){
    const n=Number(value);
    if(!Number.isFinite(n)) return value.trim();
  }
  const n=Number(value);
  if(!Number.isFinite(n)) return '—';
  return Math.max(0,Math.min(100,Math.round(n)))+'%';
}

function _formatProviderQuotaReset(value){
  if(!value) return '';
  const d=new Date(value);
  if(Number.isNaN(d.getTime())) return '';
  try{return d.toLocaleString();}catch(e){return value;}
}

function _formatProviderQuotaWindowLabel(accountLimits,w){
  const raw=((w&&w.label)||'Window').trim();
  const provider=((accountLimits&&accountLimits.provider)||'').toLowerCase();
  if(provider==='openai-codex'){
    if(raw.toLowerCase()==='session') return '5-hour limit';
    if(raw.toLowerCase()==='weekly') return 'Weekly limit';
  }
  return raw||'Window';
}

function _buildProviderQuotaCard(status){
  if(!status) return null;
  const card=document.createElement('div');
  const state=(status.status||'unavailable').replace(/[^a-z0-9_-]/gi,'').toLowerCase()||'unavailable';
  card.className='provider-quota-card provider-quota-card-'+state;
  const accountLimits=status.account_limits||null;
  const providerBase=status.display_name||status.provider||'Active provider';
  const provider=(accountLimits&&accountLimits.plan)?`${providerBase} · ${accountLimits.plan}`:providerBase;
  const quota=status.quota||null;
  let body='';
  if(status.status==='available'&&accountLimits){
    const windows=Array.isArray(accountLimits.windows)?accountLimits.windows:[];
    const details=Array.isArray(accountLimits.details)?accountLimits.details:[];
    const windowHtml=windows.map(w=>{
      const used=_formatProviderQuotaPercent(w&&w.used_percent);
      const reset=_formatProviderQuotaReset(w&&w.reset_at);
      const meta=[];
      if(used!=='—') meta.push(`${used} used`);
      if(reset) meta.push(`resets ${reset}`);
      if(w&&w.detail) meta.push(w.detail);
      return `
        <div class="provider-quota-metric provider-quota-window">
          <span>${esc(_formatProviderQuotaWindowLabel(accountLimits,w))}</span>
          <strong>${esc(_formatProviderQuotaPercent(w&&w.remaining_percent))}</strong>
          ${meta.length?`<small>${esc(meta.join(' · '))}</small>`:''}
        </div>
      `;
    }).join('');
    const detailHtml=details.length
      ? `<div class="provider-quota-details">${details.map(d=>`<span>${esc(d)}</span>`).join('')}</div>`
      : '';
    body=windowHtml+detailHtml;
    if(!body) body=`<div class="provider-quota-message">${esc(status.message||'Account limits loaded.')}</div>`;
  }else if(status.status==='available'&&quota){
    body=`
      <div class="provider-quota-metric"><span>Remaining</span><strong>${esc(_formatProviderQuotaMoney(quota.limit_remaining))}</strong></div>
      <div class="provider-quota-metric"><span>Used</span><strong>${esc(_formatProviderQuotaMoney(quota.usage))}</strong></div>
      <div class="provider-quota-metric"><span>Limit</span><strong>${esc(_formatProviderQuotaMoney(quota.limit))}</strong></div>
    `;
  }else{
    body=`<div class="provider-quota-message">${esc(status.message||'Quota status unavailable')}</div>`;
  }
  card.innerHTML=`
    <div class="provider-quota-header">
      <div>
        <div class="provider-quota-title">Active provider quota</div>
        <div class="provider-quota-subtitle">${esc(provider)}</div>
      </div>
      <span class="provider-quota-badge">${esc(state.replace(/_/g,' '))}</span>
    </div>
    <div class="provider-quota-body">${body}</div>
  `;
  return card;
}

function _buildProviderCard(p){
  const card=document.createElement('div');
  card.className='provider-card';
  card.dataset.provider=p.id;
  // Use the is_oauth flag from the backend — it reflects _OAUTH_PROVIDERS in providers.py.
  // key_source can be 'oauth' (hermes auth), 'config_yaml' (token in config.yaml), or 'none'.
  const isOauth=p.is_oauth===true;
  // models_total reflects the complete catalog (e.g. 396 for a large-tier
  // Nous Portal account). The "models" array may be trimmed to a featured
  // subset for UI scannability — fall back to its length only when the
  // server didn't supply models_total (older builds, custom providers).
  const modelCount=Number.isFinite(p.models_total)
    ? p.models_total
    : (Array.isArray(p.models) ? p.models.length : 0);
  const sourceLabel=p.key_source==='oauth'
    ? _providerText('providers_status_oauth', 'OAuth')
    : p.key_source==='config_yaml'
      ? _providerText('providers_status_configured', 'Configured')
      : (p.has_key ? _providerText('providers_status_api_key', 'API key') : _providerText('providers_status_not_configured_label', 'Not configured'));
  const metaParts=[];
  if(modelCount>0) metaParts.push(modelCount+(modelCount===1?' model':' models'));
  metaParts.push(sourceLabel);
  const metaText=metaParts.join(' · ');

  // Clickable header (toggles body)
  const header=document.createElement('button');
  header.type='button';
  header.className='provider-card-header';
  header.innerHTML=`
    <div class="provider-card-info">
      <div class="provider-card-name">${esc(p.display_name)}</div>
      <div class="provider-card-meta">${esc(metaText)}</div>
    </div>
    ${p.has_key?`<span class="provider-card-badge">${esc(_providerText('providers_status_configured', 'Configured'))}</span>`:''}
    <svg class="provider-card-chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" width="16" height="16"><path d="M6 9l6 6 6-6"/></svg>
  `;
  card.appendChild(header);

  const body=document.createElement('div');
  body.className='provider-card-body';

  if(isOauth){
    const hint=document.createElement('div');
    hint.className='provider-card-hint';
    if(p.key_source==='config_yaml'){
      hint.textContent=_providerText('providers_oauth_config_yaml_hint', 'Token configured via config.yaml. To update, edit the providers section in your config.yaml or run sidekick auth.');
    } else if(p.auth_error){
      hint.textContent=p.auth_error;
      hint.style.color='var(--accent)';
    } else if(p.has_key){
      hint.textContent=_providerText('providers_oauth_hint', 'Authenticated via OAuth. No API key needed.');
    } else {
      hint.textContent=_providerText('providers_oauth_not_configured_hint', 'Not authenticated. Run sidekick auth in the terminal to configure this provider.');
      hint.style.color='var(--muted)';
    }
    body.appendChild(hint);
    card.appendChild(body);
    header.addEventListener('click',()=>card.classList.toggle('open'));
    return card;
  }

  const field=document.createElement('div');
  field.className='provider-card-field';
  const label=document.createElement('label');
  label.className='provider-card-label';
  label.textContent=_providerText('providers_status_api_key', 'API key');
  field.appendChild(label);

  const row=document.createElement('form');
  row.className='provider-card-row';
  row.noValidate=true;
  row.addEventListener('submit',e=>{
    e.preventDefault();
    _saveProviderKey(p.id);
  });
  const input=document.createElement('input');
  input.type='password';
  input.className='provider-card-input';
  input.placeholder=p.has_key
    ? _providerText('providers_key_placeholder_replace', 'Enter new key to replace…')
    : _providerText('providers_key_placeholder_new', 'sk-...');
  input.autocomplete='off';
  const toggleBtn=document.createElement('button');
  toggleBtn.type='button';
  toggleBtn.className='provider-card-btn provider-card-btn-ghost';
  toggleBtn.textContent='Show';
  toggleBtn.onclick=()=>{
    const revealed=input.type==='text';
    input.type=revealed?'password':'text';
    toggleBtn.textContent=revealed?'Show':'Hide';
  };
  const saveBtn=document.createElement('button');
  saveBtn.type='submit';
  saveBtn.className='provider-card-btn provider-card-btn-primary';
  saveBtn.textContent=_providerText('providers_save', 'Save');
  saveBtn.disabled=true;
  row.appendChild(input);
  row.appendChild(toggleBtn);
  row.appendChild(saveBtn);
  if(p.has_key){
    const removeBtn=document.createElement('button');
    removeBtn.type='button';
    removeBtn.className='provider-card-btn provider-card-btn-danger';
    removeBtn.textContent=_providerText('providers_remove', 'Remove');
    removeBtn.onclick=()=>_removeProviderKey(p.id);
    row.appendChild(removeBtn);
  }
  field.appendChild(row);
  body.appendChild(field);

  // Model list — show when provider has known models
  if(modelCount>0){
    const modelSection=document.createElement('div');
    modelSection.className='provider-card-models';
    const modelLabel=document.createElement('div');
    modelLabel.className='provider-card-label';
    modelLabel.textContent='Models';
    modelSection.appendChild(modelLabel);
    const modelList=document.createElement('div');
    modelList.className='provider-card-model-tags';
    const renderedModels=Array.isArray(p.models)?p.models:[];
    for(const m of renderedModels){
      const tag=document.createElement('span');
      tag.className='provider-card-model-tag';
      tag.textContent=m.id||m.label||m;
      modelList.appendChild(tag);
    }
    // When the rendered list is a strict subset of the total catalog (Nous
    // Portal large-tier accounts hit this with ~400-model catalogs), show
    // a "+N more" trailing pill so the user knows the picker is intentionally
    // capped — and they can still reach the full catalog via the /model
    // slash command (its autocomplete consumes the un-trimmed list from
    // /api/models's extra_models field). #1567.
    const totalCount=Number.isFinite(p.models_total)?p.models_total:renderedModels.length;
    const hiddenCount=Math.max(0, totalCount - renderedModels.length);
    if(hiddenCount>0){
      const more=document.createElement('span');
      more.className='provider-card-model-tag provider-card-model-tag-more';
      more.textContent='+'+hiddenCount+' more';
      more.title='The /model slash command can autocomplete every model in this provider\'s catalog.';
      modelList.appendChild(more);
    }
    modelSection.appendChild(modelList);
    body.appendChild(modelSection);
  }

  // Refresh models for this provider
  const refreshRow=document.createElement('div');
  refreshRow.className='provider-card-row';
  refreshRow.style.marginTop='6px';
  const refreshBtn=document.createElement('button');
  refreshBtn.type='button';
  refreshBtn.className='provider-card-btn provider-card-btn-ghost';
  refreshBtn.style.display='flex';
  refreshBtn.style.alignItems='center';
  refreshBtn.style.gap='5px';
  refreshBtn.innerHTML=`<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/></svg> ${_providerText('providers_refresh_models', 'Refresh models')}`;
  refreshBtn.onclick=()=>_refreshProviderModels(p.id, refreshBtn);
  refreshRow.appendChild(refreshBtn);
  body.appendChild(refreshRow);
  card.appendChild(body);

  _providerCardEls.set(p.id,{card,input,saveBtn,hasKey:p.has_key});
  input.addEventListener('input',()=>{saveBtn.disabled=!input.value.trim();});
  header.addEventListener('click',e=>{
    // Don't toggle when clicking inside body (defensive; body isn't inside header)
    if(e.target.closest('.provider-card-body')) return;
    card.classList.toggle('open');
    if(card.classList.contains('open')) setTimeout(()=>input.focus(),0);
  });
  return card;
}

async function _saveProviderKey(providerId){
  const els=_providerCardEls.get(providerId);
  if(!els) return;
  const key=els.input.value.trim();
  if(!key){
    showToast(_providerText('providers_enter_key', 'Please enter an API key'));
    return;
  }
  els.saveBtn.disabled=true;
  els.saveBtn.textContent=_providerText('providers_saving', 'Saving…');
  try{
    const res=await api('/api/providers',{method:'POST',body:JSON.stringify({provider:providerId,api_key:key})});
    if(res.ok){
      showToast(res.provider+' key '+res.action);
      els.input.value='';
      // Invalidate every dropdown surface that caches /api/models so the
      // newly-configured provider's models show up without a server restart
      // or page reload (#1539). Server-side invalidate_models_cache() is
      // already called by api/providers.py:set_provider_key.
      _refreshModelDropdownsAfterProviderChange();
      await loadProvidersPanel(); // refresh list
    }else{
      showToast(res.error||'Failed to save key');
      els.saveBtn.disabled=false;
      els.saveBtn.textContent=_providerText('providers_save', 'Save');
    }
  }catch(e){
    showToast('Error: '+e.message);
    els.saveBtn.disabled=false;
    els.saveBtn.textContent=_providerText('providers_save', 'Save');
  }
}

async function _removeProviderKey(providerId){
  const els=_providerCardEls.get(providerId);
  if(!els) return;
  if(els.saveBtn){els.saveBtn.disabled=true;els.saveBtn.textContent=_providerText('providers_removing', 'Removing…');}
  try{
    const res=await api('/api/providers/delete',{method:'POST',body:JSON.stringify({provider:providerId})});
    if(res.ok){
      showToast(res.provider+' key '+_providerText('providers_key_removed', 'API key removed').toLowerCase());
      // Drop the removed provider from every cached dropdown surface so it
      // disappears immediately — composer picker, /model slash command,
      // Settings → Default Model, configured-model badges (#1539).
      // Without this, a stale list from before the delete keeps offering
      // the now-removed provider's models until the page is reloaded.
      _refreshModelDropdownsAfterProviderChange();
      await loadProvidersPanel(); // refresh list
    }else{
      showToast(res.error||'Failed to remove key');
      if(els.saveBtn){els.saveBtn.disabled=false;els.saveBtn.textContent=_providerText('providers_save', 'Save');}
    }
  }catch(e){
    showToast('Error: '+e.message);
    if(els.saveBtn){els.saveBtn.disabled=false;els.saveBtn.textContent=_providerText('providers_save', 'Save');}
  }
}

// Shared dropdown-cache flush invoked after a provider add/remove. The
// server-side TTL cache is already invalidated by /api/providers and
// /api/providers/delete (via api/providers.py:set_provider_key); this
// flushes the JS-side caches so the next render rebuilds from a fresh
// /api/models response. Wrapped in a try/catch so a UI module that hasn't
// loaded yet (e.g. during early Settings open) cannot break the save flow.
function _refreshModelDropdownsAfterProviderChange(){
  try{
    if(typeof window._invalidateSlashModelCache==='function'){
      window._invalidateSlashModelCache();
    }
    if(typeof populateModelDropdown==='function'){
      // Fire-and-forget: don't block the providers panel refresh on a
      // dropdown rebuild. The composer/Settings dropdowns will catch up
      // on the very next paint frame.
      Promise.resolve(populateModelDropdown()).catch(()=>{});
    }
  }catch(_e){
    // Swallow — dropdown refresh is best-effort, providers panel must still update.
  }
}

async function _refreshProviderModels(providerId, btn){
  btn.disabled=true;
  const orig=btn.innerHTML;
  btn.innerHTML=`<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M3 21v-5h5"/></svg> ${_providerText('providers_refreshing', 'Refreshing...')}`;
  try{
    const res=await api('/api/models/refresh',{method:'POST',body:JSON.stringify({provider:providerId})});
    if(res.ok){
      showToast(_providerText('providers_models_refreshed', 'Models refreshed for ' + res.provider));
    }else{
      showToast(res.error||'Failed to refresh models');
    }
  }catch(e){
    showToast('Error: '+e.message);
  }finally{
    btn.disabled=false;
    btn.innerHTML=orig;
  }
}

function _setSettingsAuthButtonsVisible(active){
  const signOutBtn=$('btnSignOut');
  if(signOutBtn) signOutBtn.style.display=active?'':'none';
  const disableBtn=$('btnDisableAuth');
  if(disableBtn) disableBtn.style.display=active?'':'none';
}

function _applySavedSettingsUi(saved, body, opts){
  const {sendKey,showTokenUsage,showTps,showCliSessions,theme,skin,language,sidebarDensity,fontSize}=opts;
  window._sendKey=sendKey||'enter';
  window._showTokenUsage=showTokenUsage;
  window._showTps=showTps;
  window._showCliSessions=showCliSessions;
  window._soundEnabled=body.sound_enabled;
  window._notificationsEnabled=body.notifications_enabled;
  window._showThinking=body.show_thinking!==false;
  window._simplifiedToolCalling=body.simplified_tool_calling!==false;
  window._gameModeEnabled=!!body.game_mode_enabled;
  syncGameModeButton();
  window._sessionJumpButtonsEnabled=!!body.session_jump_buttons;
  if(typeof _applySessionNavigationPrefs==='function') _applySessionNavigationPrefs();
  window._sidebarDensity=sidebarDensity==='detailed'?'detailed':'compact';
  window._busyInputMode=body.busy_input_mode||'queue';
  window._sessionEndlessScrollEnabled=!!body.session_endless_scroll;
  window._botName=body.bot_name||'Nova';
  if(typeof applyBotName==='function') applyBotName();
  if(typeof setLocale==='function') setLocale(language);
  if(typeof applyLocaleToDOM==='function') applyLocaleToDOM();
  if(typeof startGatewaySSE==='function'){
    if(showCliSessions) startGatewaySSE();
    else if(typeof stopGatewaySSE==='function') stopGatewaySSE();
  }
  _setSettingsAuthButtonsVisible(!!saved.auth_enabled);
  _settingsDirty=false;
  _settingsThemeOnOpen=theme;
  _settingsSkinOnOpen=skin||'default';
  _settingsFontSizeOnOpen=fontSize||localStorage.getItem('sidekick-font-size')||'default';
  const bar=$('settingsUnsavedBar');
  if(bar) bar.style.display='none';
  _settingsSidekickDefaultModelOnOpen=body.default_model||_settingsSidekickDefaultModelOnOpen||'';
  // Sync window._defaultModel so newSession() uses the just-saved default without a reload (#908).
  if(body.default_model) window._defaultModel=body.default_model;
  if(typeof clearMessageRenderCache==='function') clearMessageRenderCache();
  renderMessages();
  if(typeof syncTopbar==='function') syncTopbar();
  if(typeof renderSessionList==='function') renderSessionList();
}

async function checkUpdatesNow(){
  const btn=$('btnCheckUpdatesNow');
  const label=$('checkUpdatesLabel');
  const spinner=$('checkUpdatesSpinner');
  const status=$('checkUpdatesStatus');
  if(!btn||!label) return;
  // Disable button, show spinner
  btn.disabled=true;
  if(spinner) spinner.style.display='';
  if(label) label.textContent=t('settings_checking');
  if(status) status.textContent='';
  try {
    const data=await api('/api/updates/check?force=1');
    if(data.disabled){
      if(status){status.textContent=t('settings_updates_disabled');status.style.color='var(--muted)';}
    } else {
      const parts=[];
      const formatUpdatePart=(typeof _formatUpdateTargetStatus==='function')
        ? _formatUpdateTargetStatus
        : ((label,info)=>info&&info.behind>0?label+': '+info.behind:null);
      const webuiPart=formatUpdatePart('WebUI',data.webui);
      const agentPart=formatUpdatePart('Agent',data.agent);
      if(webuiPart) parts.push(webuiPart);
      if(agentPart) parts.push(agentPart);
      if(parts.length){
        if(status){status.textContent=t('settings_updates_available').replace('{count}',parts.join(', '));status.style.color='var(--accent)';}
        // Also trigger the update banner
        if(typeof _showUpdateBanner==='function') _showUpdateBanner(data);
      } else {
        if(status){status.textContent=t('settings_up_to_date');status.style.color='var(--success)';}
      }
    }
  } catch(e){
    // Never expose raw e.message in UI — log to console for debugging only
    console.warn('[checkUpdatesNow]', e);
    // Show a generic user-facing error; if the API returned a message body use it
    let userMsg=t('settings_update_check_failed');
    if(e&&e.response){
      try{
        const body=JSON.parse(e.response);
        if(body.error) userMsg=String(body.error).substring(0,120);
      }catch(_){}
    }
    if(status){status.textContent=userMsg;status.style.color='var(--error)';}
  } finally {
    btn.disabled=false;
    if(spinner) spinner.style.display='none';
    if(label) label.textContent=t('settings_check_now');
  }
}

async function saveSettings(andClose){
  const model=($('settingsModel')||{}).value;
  const modelChanged=(model||'')!==(_settingsSidekickDefaultModelOnOpen||'');
  const sendKey=($('settingsSendKey')||{}).value;
  const showTokenUsage=!!($('settingsShowTokenUsage')||{}).checked;
  const showTps=!!($('settingsShowTps')||{}).checked;
  const showCliSessions=!!($('settingsShowCliSessions')||{}).checked;
  const pw=($('settingsPassword')||{}).value;
  const theme=($('settingsTheme')||{}).value||'dark';
  const skin=($('settingsSkin')||{}).value||'default';
  const fontSize=($('settingsFontSize')||{}).value||localStorage.getItem('sidekick-font-size')||'default';
  const language=($('settingsLanguage')||{}).value||'en';
  const sidebarDensity=($('settingsSidebarDensity')||{}).value==='detailed'?'detailed':'compact';
  const busyInputMode=($('settingsBusyInputMode')||{}).value||'queue';
  const body={};

  if(sendKey) body.send_key=sendKey;
  body.theme=theme;
  body.skin=skin;
  body.font_size=fontSize;
  body.session_jump_buttons=!!($('settingsSessionJumpButtons')||{}).checked;
  body.session_endless_scroll=!!($('settingsSessionEndlessScroll')||{}).checked;
  body.language=language;
  body.game_mode_enabled=!!($('settingsGameModeEnabled')||{}).checked;
  body.show_token_usage=showTokenUsage;
  body.show_tps=showTps;
  body.simplified_tool_calling=!!($('settingsSimplifiedToolCalling')||{}).checked;
  body.api_redact_enabled=!!($('settingsApiRedact')||{}).checked;
  body.show_cli_sessions=showCliSessions;
  body.show_openrouter_paid=!!($('settingsShowOpenrouterPaid')||{}).checked;
  body.sync_to_insights=!!($('settingsSyncInsights')||{}).checked;
  body.check_for_updates=!!($('settingsCheckUpdates')||{}).checked;
  body.sound_enabled=!!($('settingsSoundEnabled')||{}).checked;
  body.notifications_enabled=!!($('settingsNotificationsEnabled')||{}).checked;
  body.show_thinking=window._showThinking!==false;
  body.sidebar_density=sidebarDensity;
  body.busy_input_mode=busyInputMode;
  body.auto_title_refresh_every=(($('settingsAutoTitleRefresh')||{}).value||'0');
  const botName=(($('settingsBotName')||{}).value||'').trim();
  body.bot_name=botName||'Nova';
  // Password: only act if the field has content; blank = leave auth unchanged
  if(pw && pw.trim()){
    try{
      const saved=await api('/api/settings',{method:'POST',body:JSON.stringify({...body,_set_password:pw.trim()})});
      if(modelChanged && model){
        try{
          await api('/api/default-model',{method:'POST',body:JSON.stringify({model})});
          body.default_model=model;
        }catch(_modelErr){
          if(typeof showToast==='function') showToast('Failed to update default model — settings saved');
        }
      }
      _applySavedSettingsUi(saved, body, {sendKey,showTokenUsage,showTps,showCliSessions,theme,skin,language,sidebarDensity,fontSize});
      showToast(t(saved.auth_just_enabled?'settings_saved_pw':'settings_saved_pw_updated'));
      _settingsDirty=false;
      _resetSettingsPanelState();
      if(!andClose) _pendingSettingsTargetPanel = null;
      if(andClose) _hideSettingsPanel();
      return;
    }catch(e){showToast(t('settings_save_failed')+e.message);return;}
  }
  try{
    const saved=await api('/api/settings',{method:'POST',body:JSON.stringify(body)});
    if(modelChanged && model){
      try{
        await api('/api/default-model',{method:'POST',body:JSON.stringify({model})});
        body.default_model=model;
      }catch(_modelErr){
        if(typeof showToast==='function') showToast('Failed to update default model — settings saved');
      }
    }
    _applySavedSettingsUi(saved, body, {sendKey,showTokenUsage,showTps,showCliSessions,theme,skin,language,sidebarDensity,fontSize});
    showToast(t('settings_saved'));
    _settingsDirty=false;
    _resetSettingsPanelState();
    if(!andClose) _pendingSettingsTargetPanel = null;
    if(andClose) _hideSettingsPanel();
  }catch(e){
    showToast(t('settings_save_failed')+e.message);
  }
}

async function signOut(){
  try{
    await api('/api/auth/logout',{method:'POST',body:'{}'});
    window.location.href='login';
  }catch(e){
    showToast(t('sign_out_failed')+e.message);
  }
}

async function disableAuth(){
  const _disAuth=await showConfirmDialog({title:t('disable_auth_confirm_title'),message:t('disable_auth_confirm_message'),confirmLabel:t('disable'),danger:true,focusCancel:true});
  if(!_disAuth) return;
  try{
    await api('/api/settings',{method:'POST',body:JSON.stringify({_clear_password:true})});
    showToast(t('auth_disabled'));
    // Hide both auth buttons since auth is now off
    const disableBtn=$('btnDisableAuth');
    if(disableBtn) disableBtn.style.display='none';
    const signOutBtn=$('btnSignOut');
    if(signOutBtn) signOutBtn.style.display='none';
  }catch(e){
    showToast(t('disable_auth_failed')+e.message);
  }
}


// ── Cron completion alerts ────────────────────────────────────────────────────

let _cronPollSince=Date.now()/1000;  // track from page load
let _cronPollTimer=null;
let _cronUnreadCount=0;
let _cronPollInFlight=false;  // prevent request pileup
const _cronNewJobIds=new Set();  // track which job IDs had new completions (unread)

// Auto-refresh the cron list when a job is created from chat or any external source.
// The chat path dispatches this event when the agent response mentions cron creation.
window.addEventListener('hermes:cron_created', () => {
  if ($('cronList')) loadCrons();
});

function startCronPolling(){
  if(_cronPollTimer) return;
  _cronPollTimer=setInterval(async()=>{
    if(document.hidden) return;  // don't poll when tab is in background
    try{
      if(_cronPollInFlight) return;
      _cronPollInFlight=true;
      const data=await api(`/api/crons/recent?since=${_cronPollSince}`);
      _cronPollInFlight=false;
      if(data.completions&&data.completions.length>0){
        for(const c of data.completions){
          if(c.toast_notifications !== false){
            showToast(t('cron_completion_status', c.name, c.status==='error' ? t('status_failed') : t('status_completed')),4000);
          }
          _cronPollSince=Math.max(_cronPollSince,c.completed_at);
          if(c.job_id) _cronNewJobIds.add(String(c.job_id));
        }
        // _cronUnreadCount is derived from _cronNewJobIds.size in updateCronBadge.
        updateCronBadge();
      }
    }catch(e){
      _cronPollInFlight=false;
    }
  },30000);
}

function updateCronBadge(){
  const tab=document.querySelector('.nav-tab[data-panel="tasks"]');
  if(!tab) return;
  let badge=tab.querySelector('.cron-badge');
  _cronUnreadCount=_cronNewJobIds.size;  // sync counter to set (source of truth)
  if(_cronUnreadCount>0){
    if(!badge){
      badge=document.createElement('span');
      badge.className='cron-badge';
      tab.style.position='relative';
      tab.appendChild(badge);
    }
    badge.textContent=_cronUnreadCount>9?'9+':_cronUnreadCount;
    badge.style.display='';
  }else if(badge){
    badge.style.display='none';
  }
}

// Clear cron badge only when all unread jobs have been viewed (not on panel open)
function _clearCronUnreadForJob(jobId){
  const id=String(jobId);
  if(_cronNewJobIds.has(id)){
    _cronNewJobIds.delete(id);
    updateCronBadge();  // re-derives _cronUnreadCount from set size
  }
}

const _origSwitchPanel=switchPanel;
switchPanel=async function(name,opts){ return _origSwitchPanel(name,opts); };

// Start polling on page load
startCronPolling();

// ── Background agent error tracking ──────────────────────────────────────────

const _backgroundErrors=[];  // {session_id, title, message, ts}

function trackBackgroundError(sessionId, title, message){
  // Only track if user is NOT currently viewing this session
  if(S.session&&S.session.session_id===sessionId) return;
  _backgroundErrors.push({
    session_id:sessionId,
    title:(title && title !== 'Untitled') ? title : (typeof t === 'function' ? t('new_chat') : 'New chat'),
    message,
    ts:Date.now()
  });
  showErrorBanner();
}

function showErrorBanner(){
  let banner=$('bgErrorBanner');
  if(!banner){
    banner=document.createElement('div');
    banner.id='bgErrorBanner';
    banner.className='bg-error-banner';
    const msgs=document.querySelector('.messages');
    if(msgs) msgs.parentNode.insertBefore(banner,msgs);
    else document.body.appendChild(banner);
  }
  const latest=_backgroundErrors[0];  // FIFO: show oldest (first) error
  if(!latest){banner.style.display='none';return;}
  const count=_backgroundErrors.length;
  const msg=count>1?t('bg_error_multi',count):t('bg_error_single',latest.title);
  banner.innerHTML=`<span>\u26a0 ${esc(msg)}</span><div style="display:flex;gap:6px;flex-shrink:0"><button class="reconnect-btn" onclick="navigateToErrorSession()">${esc(t('view'))}</button><button class="reconnect-btn" onclick="dismissErrorBanner()">${esc(t('dismiss'))}</button></div>`;
  banner.style.display='';
}

function navigateToErrorSession(){
  const latest=_backgroundErrors.shift();  // FIFO: show oldest error first
  if(latest){
    loadSession(latest.session_id);renderSessionList();
  }
  if(_backgroundErrors.length===0) dismissErrorBanner();
  else showErrorBanner();
}

function dismissErrorBanner(){
  _backgroundErrors.length=0;
  const banner=$('bgErrorBanner');
  if(banner) banner.style.display='none';
}

// Event wiring


// ── MCP Server Management ──
function _mcpStatusLabel(status){
  const key={
    active:'mcp_status_active',
    configured:'mcp_status_configured',
    disabled:'mcp_status_disabled',
    invalid_config:'mcp_status_invalid_config',
  }[status]||'mcp_status_unknown';
  return t(key);
}
function loadMcpServers(){
  const list=$('mcpServerList');
  if(!list) return;
  list.innerHTML=`<div style="color:var(--muted);font-size:12px;padding:6px 0">${esc(t('loading'))}</div>`;
  api('/api/mcp/servers').then(r=>{
    if(!r||!Array.isArray(r.servers)) return;
    if(!r.servers.length){
      list.innerHTML=`<div class="mcp-empty-state" style="color:var(--muted);font-size:12px;padding:6px 0">${esc(t('mcp_no_servers'))}</div>`;
      return;
    }
    const toggleSupported=!!r.toggle_supported;
    const toggleNote=toggleSupported?'':'<div class="mcp-readonly-note">'+esc(t('mcp_toggle_followup'))+'</div>';
    list.innerHTML=r.servers.map(s=>{
      const transportLabel=s.transport==='http'?'HTTP':s.transport==='stdio'?'stdio':(''+(s.transport||'unknown'));
      const transportClass=s.transport==='http'?'mcp-http':s.transport==='stdio'?'mcp-stdio':'mcp-unknown';
      const transportBadge=`<span class="mcp-transport-badge ${transportClass}">${esc(transportLabel)}</span>`;
      const status=s.status||'configured';
      const statusBadge=`<span class="mcp-status-badge mcp-status-${esc(status)}">${esc(_mcpStatusLabel(status))}</span>`;
      const toggleButton=toggleSupported
        ? `<button type="button" class="panel-icon-btn" style="width:auto;padding:2px 8px;font-size:11px;display:inline-flex;align-items:center;gap:4px" onclick="toggleMcpServerEnabled(${JSON.stringify(String(s.name||''))}, ${s.enabled===false ? 'true' : 'false'})" aria-label="${esc(s.enabled===false ? 'Enable MCP server' : 'Disable MCP server')}" title="${esc(s.enabled===false ? 'Enable MCP server' : 'Disable MCP server')}">${esc(s.enabled===false ? 'Enable' : 'Disable')}</button>`
        : '';
      const deleteButton=toggleSupported
        ? `<button type="button" class="panel-icon-btn" style="width:auto;padding:2px 8px;font-size:11px;display:inline-flex;align-items:center;gap:4px;color:var(--error,#e94560);border-color:var(--error,#e94560)" onclick="deleteMcpServer(${JSON.stringify(String(s.name||''))})" aria-label="${esc(t('mcp_delete_confirm_title'))}" title="${esc(t('mcp_delete_confirm_title'))}">${esc(t('delete_title'))}</button>`
        : '';
      const toolCount=s.tool_count===null||typeof s.tool_count==='undefined'?'—':String(s.tool_count);
      const detail=s.transport==='http'
        ? (s.url||'')
        : (s.transport==='stdio'?`${s.command||''} ${Array.isArray(s.args)?s.args.join(' '):''}`:t('mcp_status_invalid_config'));
      const envInfo=s.env?Object.entries(s.env).map(([k,v])=>`${k}=${v}`).join(', '):'';
      const headersInfo=s.headers?Object.entries(s.headers).map(([k,v])=>`${k}=${v}`).join(', '):'';
      const secretInfo=[envInfo,headersInfo].filter(Boolean).join(' | ');
      return `<div class="mcp-server-row">
        <div class="mcp-server-row-head" style="justify-content:space-between;gap:8px;align-items:flex-start">
          <span class="mcp-server-name" title="${esc(s.name)}">${esc(s.name)}</span>
          <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end">
            ${transportBadge}
            ${statusBadge}
            ${toggleButton}
            ${deleteButton}
          </div>
        </div>
        <div class="mcp-server-detail">${esc(detail)}${secretInfo?' | '+esc(secretInfo):''}</div>
        <div class="mcp-server-meta"><span class="mcp-tool-count">${esc(t('mcp_tool_count',toolCount))}</span><span>${esc(t(s.enabled===false?'mcp_enabled_no':'mcp_enabled_yes'))}</span></div>
      </div>`;
    }).join('')+toggleNote;
  }).catch(()=>{list.innerHTML=`<div class="mcp-error-state" style="color:#ef4444;font-size:12px;padding:6px 0">${esc(t('mcp_load_failed'))}</div>`});
}
async function toggleMcpServerEnabled(name, enabled){
  const serverName=String(name||'').trim();
  if(!serverName) return;
  const nextEnabled=!!enabled;
  try{
    const saved=await api('/api/mcp/servers/'+encodeURIComponent(serverName),{
      method:'POST',
      body:JSON.stringify({enabled:nextEnabled}),
    });
    if(typeof loadMcpServers==='function') loadMcpServers();
    if(typeof loadMcpTools==='function') loadMcpTools();
    if(typeof showToast==='function'){
      const summary=saved&&saved.server&&saved.server.enabled===false ? 'disabled' : 'enabled';
      showToast('MCP server '+summary+': '+serverName,2200,'info');
    }
  }catch(e){
    if(typeof showToast==='function') showToast('Failed to update MCP server: '+(e&&e.message?e.message:e),2400,'error');
  }
}
window.toggleMcpServerEnabled = toggleMcpServerEnabled;
async function deleteMcpServer(name){
  const serverName=String(name||'').trim();
  if(!serverName) return;
  try{
    const ok=await showConfirmDialog({
      title:t('mcp_delete_confirm_title'),
      message:t('mcp_delete_confirm_message',serverName),
      confirmLabel:t('delete_title'),
      danger:true,
      focusCancel:true,
    });
    if(!ok) return false;
    await api('/api/mcp/servers/'+encodeURIComponent(serverName),{method:'DELETE'});
    if(typeof loadMcpServers==='function') loadMcpServers();
    if(typeof loadMcpTools==='function') loadMcpTools();
    if(typeof showToast==='function') showToast(t('mcp_deleted'),2200,'success');
    return true;
  }catch(e){
    if(typeof showToast==='function') showToast(t('mcp_delete_failed')+((e&&e.message)?(': '+e.message):''),2400,'error');
    return false;
  }
}
window.deleteMcpServer = deleteMcpServer;
let _mcpToolsCache=[];
function _filterMcpToolsForSearch(tools, query){
  const q=(query||'').trim().toLowerCase();
  if(!q) return Array.isArray(tools)?tools:[];
  return (Array.isArray(tools)?tools:[]).filter(tool=>{
    const hay=[tool.name,tool.server,tool.description].map(v=>String(v||'').toLowerCase()).join(' ');
    return hay.includes(q);
  });
}
function _mcpToolSchemaText(schemaSummary){
  if(!Array.isArray(schemaSummary)||!schemaSummary.length) return t('mcp_tools_schema_empty');
  return schemaSummary.map(p=>{
    const req=p.required?'*':'';
    const desc=p.description?` — ${p.description}`:'';
    return `${p.name}${req}: ${p.type||'unknown'}${desc}`;
  }).join('\n');
}
function _renderMcpTools(tools, query){
  const list=$('mcpToolList');
  if(!list) return;
  const filtered=_filterMcpToolsForSearch(tools, query);
  if(!filtered.length){
    const key=query?'mcp_tools_no_matches':'mcp_tools_no_tools';
    list.innerHTML=`<div class="mcp-tool-empty-state" style="color:var(--muted);font-size:12px;padding:6px 0">${esc(t(key))}</div>`;
    return;
  }
  list.innerHTML=filtered.map(tool=>{
    const status=tool.status||'unknown';
    const statusBadge=`<span class="mcp-status-badge mcp-status-${esc(status)}">${esc(_mcpStatusLabel(status))}</span>`;
    const schemaText=_mcpToolSchemaText(tool.schema_summary);
    return `<div class="mcp-tool-row">
      <div class="mcp-server-row-head">
        <span class="mcp-tool-name">${esc(tool.name)}</span>
        <span class="mcp-tool-server">${esc(tool.server||'unknown')}</span>
        ${statusBadge}
      </div>
      <div class="mcp-server-detail">${esc(tool.description||'')}</div>
      <pre class="mcp-tool-schema">${esc(schemaText)}</pre>
    </div>`;
  }).join('');
}
function filterMcpTools(){
  const input=$('mcpToolSearch');
  _renderMcpTools(_mcpToolsCache,input?input.value:'');
}
function loadMcpTools(){
  const list=$('mcpToolList');
  if(!list) return;
  list.innerHTML=`<div style="color:var(--muted);font-size:12px;padding:6px 0">${esc(t('loading'))}</div>`;
  api('/api/mcp/tools').then(r=>{
    _mcpToolsCache=(r&&Array.isArray(r.tools))?r.tools:[];
    filterMcpTools();
  }).catch(()=>{list.innerHTML=`<div class="mcp-tool-error-state" style="color:#ef4444;font-size:12px;padding:6px 0">${esc(t('mcp_tools_load_failed'))}</div>`});
}
function loadGatewayStatus(){
  const card=$('gatewayStatusCard');
  if(!card) return;
  const request=typeof window._workspaceApiWithTimeout==='function'
    ? window._workspaceApiWithTimeout('/api/gateway/status', 6000)
    : api('/api/gateway/status');
  request.then(r=>{
    if(!r) return;
    if(!r.configured){
      card.innerHTML=`<div style="color:var(--muted);font-size:12px;display:flex;align-items:center;gap:6px"><span style="width:8px;height:8px;border-radius:50%;background:#f59e0b;display:inline-block"></span>Gateway not configured</div>`;
      return;
    }
    if(!r.running){
      card.innerHTML=`<div style="color:var(--muted);font-size:12px;display:flex;align-items:center;gap:6px"><span style="width:8px;height:8px;border-radius:50%;background:#ef4444;display:inline-block"></span>Gateway not running</div>`;
      return;
    }
    const platformIcons={telegram:'💬',discord:'🎮',slack:'📝',web:'🌐',api:'🔌'};
    let badges='';
    if(r.platforms&&r.platforms.length){
      badges=r.platforms.map(p=>{
        const icon=platformIcons[p.name]||'📡';
        return `<span style="display:inline-flex;align-items:center;gap:4px;padding:3px 10px;background:var(--code-bg);border:1px solid var(--border2);border-radius:12px;font-size:12px;font-weight:500">${icon} ${esc(p.label)}</span>`;
      }).join(' ');
    }
    const lastActive=r.last_active?`<span style="font-size:11px;color:var(--muted)">Last active: ${esc(new Date(r.last_active).toLocaleString())}</span>`:'';
    const sessionInfo=r.session_count?`<span style="font-size:11px;color:var(--muted)">${r.session_count} session${r.session_count!==1?'s':''}</span>`:'';
    card.innerHTML=`<div style="display:flex;align-items:center;gap:6px;margin-bottom:8px"><span style="width:8px;height:8px;border-radius:50%;background:#22c55e;display:inline-block"></span><span style="font-size:13px;font-weight:500;color:#22c55e">Running</span></div>${badges?`<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px">${badges}</div>`:''}<div style="display:flex;gap:12px">${sessionInfo}${lastActive}</div>`;
  }).catch(()=>{card.innerHTML=`<div style="color:#ef4444;font-size:12px">Failed to load gateway status</div>`});
}
function _subagentStatusLabel(status){
  const normalized=String(status||'').trim().toLowerCase();
  if(!normalized) return 'Unknown';
  if(normalized==='running'||normalized==='active') return 'Running';
  if(normalized==='completed') return 'Completed';
  if(normalized==='failed') return 'Failed';
  if(normalized==='interrupted') return 'Interrupted';
  if(normalized==='paused') return 'Paused';
  return normalized.charAt(0).toUpperCase()+normalized.slice(1);
}
function _renderSubagentStatus(active, paused, targetId='subagentStatusCard'){
  const list=$(targetId);
  if(!list) return;
  const entries=Array.isArray(active)?active:[];
  const pauseLabel=paused?'Resume spawning':'Pause spawning';
  const pauseTitle=paused?'Allow new subagents to spawn again':'Temporarily block new subagent spawns';
  const toggleButton=`<button type="button" class="panel-icon-btn" style="width:auto;padding:2px 8px;font-size:11px;display:inline-flex;align-items:center;gap:4px" onclick="toggleSubagentSpawnPause('${esc(targetId)}')" aria-label="${esc(pauseTitle)}" title="${esc(pauseTitle)}">${paused?'▶':'⏸'} ${esc(pauseLabel)}</button>`;
  if(!entries.length){
    list.innerHTML=`<div class="mcp-server-row">
      <div class="mcp-server-row-head" style="justify-content:space-between;gap:8px">
        <span class="mcp-server-name">No active subagents</span>
        ${toggleButton}
      </div>
      <div class="mcp-server-detail">${paused?'Spawn is paused. No new delegate_task workers will start.':'Spawn is open. New delegate_task workers may start.'}</div>
    </div>`;
    return;
  }
  list.innerHTML=`<div class="mcp-server-row" style="margin-bottom:8px">
    <div class="mcp-server-row-head" style="justify-content:space-between;gap:8px">
      <span class="mcp-server-name">${entries.length} active subagent${entries.length===1?'':'s'}</span>
      ${toggleButton}
    </div>
    <div class="mcp-server-detail">${paused?'Spawn is paused.':'Spawn is open.'}</div>
  </div>` + entries.map(item=>{
    const sid=String(item&&item.subagent_id||'').trim();
    const sessionId=String(item&&item.session_id||'').trim();
    const goal=String(item&&item.goal||'').trim()||(typeof t === 'function' ? t('kanban_new_task') : 'New task');
    const model=String(item&&item.model||'').trim();
    const depth=typeof item?.depth==='number'?item.depth:null;
    const toolCount=typeof item?.tool_count==='number'?item.tool_count:null;
    const status=_subagentStatusLabel(item&&item.status);
    const metaParts=[];
    if(model) metaParts.push(model);
    if(depth!==null) metaParts.push('depth '+depth);
    if(toolCount!==null) metaParts.push(toolCount+' tools');
    const meta=metaParts.join(' · ');
    const interruptButton=sid
      ? `<button type="button" class="panel-icon-btn" style="width:auto;padding:2px 8px;font-size:11px;display:inline-flex;align-items:center;gap:4px" onclick="event.stopPropagation();interruptActiveSubagent('${esc(sid)}','${esc(targetId)}')" aria-label="Interrupt subagent ${esc(sid)}" title="Interrupt this subagent">Stop</button>`
      : '';
    const rowClick=sessionId && typeof loadSession==='function'
      ? `onclick="loadSession('${esc(sessionId)}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();loadSession('${esc(sessionId)}')}" tabindex="0" role="button" aria-label="Open subagent session ${esc(sid||sessionId)}"`
      : '';
    return `<div class="mcp-server-row" ${rowClick} style="${sessionId ? 'cursor:pointer;' : ''}">
      <div class="mcp-server-row-head" style="justify-content:space-between;gap:8px;align-items:flex-start">
        <div style="display:flex;flex-direction:column;gap:2px;min-width:0">
          <span class="mcp-server-name" title="${esc(goal)}">${esc(goal)}</span>
          <span style="font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(sid||'unknown')} · ${esc(status)}</span>
        </div>
        <div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap;justify-content:flex-end">
          ${interruptButton}
        </div>
      </div>
      <div class="mcp-server-detail">${meta?esc(meta):'No additional metadata'}</div>
    </div>`;
  }).join('');
}
function _updateWorkflowSubagentSummary(active, paused){
  const entries=Array.isArray(active)?active:[];
  const first=entries[0]||{};
  window._workflowSubagentSummary={
    count:entries.length,
    paused:!!paused,
    goal:String(first.goal||'').trim(),
    subagent_id:String(first.subagent_id||first.session_id||'').trim(),
    session_id:String(first.session_id||'').trim(),
    preview:String(first.goal||first.session_id||first.subagent_id||'').trim(),
  };
  if(typeof syncWorkflowChip==='function') syncWorkflowChip();
}
function loadSubagentStatus(targetId='subagentStatusCard'){
  const card=$(targetId);
  if(!card) return;
  card.innerHTML=`<div style="color:var(--muted);font-size:12px;padding:6px 0">${esc(t('loading'))}</div>`;
  const request=typeof window._workspaceApiWithTimeout==='function'
    ? window._workspaceApiWithTimeout('/api/subagents', 6000)
    : api('/api/subagents');
  request.then(r=>{
    const active=(r&&r.active)||[];
    const paused=!!(r&&r.spawn_paused);
    _renderSubagentStatus(active, paused, targetId);
    _updateWorkflowSubagentSummary(active, paused);
  }).catch(()=>{card.innerHTML=`<div style="color:#ef4444;font-size:12px;padding:6px 0">Failed to load subagent status</div>`});
}
async function toggleSubagentSpawnPause(targetId='subagentStatusCard'){
  try{
    const current=await api('/api/subagents');
    const nextPaused=!Boolean(current&&current.spawn_paused);
    const saved=await api('/api/subagents',{
      method:'POST',
      body:JSON.stringify({spawn_paused:nextPaused}),
    });
    const active=(saved&&saved.active)||[];
    const paused=!!(saved&&saved.spawn_paused);
    _renderSubagentStatus(active, paused, targetId);
    _updateWorkflowSubagentSummary(active, paused);
    if(typeof showToast==='function') showToast(nextPaused?'Subagent spawning paused':'Subagent spawning resumed',2200,nextPaused?'info':'success');
  }catch(e){
    if(typeof showToast==='function') showToast('Failed to update subagent spawning: '+(e&&e.message?e.message:e),2400,'error');
  }
}
async function interruptActiveSubagent(subagentId, targetId='subagentStatusCard'){
  const sid=String(subagentId||'').trim();
  if(!sid) return;
  try{
    const saved=await api('/api/subagents',{
      method:'POST',
      body:JSON.stringify({subagent_id:sid}),
    });
    const active=(saved&&saved.active)||[];
    const paused=!!(saved&&saved.spawn_paused);
    _renderSubagentStatus(active, paused, targetId);
    _updateWorkflowSubagentSummary(active, paused);
    if(typeof showToast==='function') showToast('Subagent interrupted: '+sid.slice(0,8),2200,'info');
  }catch(e){
    if(typeof showToast==='function') showToast('Failed to interrupt subagent: '+(e&&e.message?e.message:e),2400,'error');
  }
}
function ensureSubagentsPanel(){
  const main=document.querySelector('main.main');
  if(!main) return null;
  let panel=$('panelSubagents');
  if(!panel){
    panel=document.createElement('div');
    panel.className='panel-view';
    panel.id='panelSubagents';
    panel.innerHTML=`
      <div class="panel-head">
        <span>Subagents</span>
        <div class="panel-head-actions">
          <button class="panel-head-btn has-tooltip has-tooltip--bottom" type="button" data-tooltip="Refresh" aria-label="Refresh" onclick="loadSubagentsPanel(true)">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
          </button>
        </div>
      </div>
      <div class="subagents-panel-shell">
        <div class="subagents-panel-note">Active delegate_task workers and the spawn pause switch.</div>
        <div id="subagentStatusCardPanel"></div>
      </div>
    `;
    const ref=$('panelProfiles')||$('panelLogs')||$('panelSettings')||$('panelAppstore')||null;
    if(ref && ref.parentNode===main) main.insertBefore(panel, ref);
    else main.appendChild(panel);
  }
  return panel;
}
function loadSubagentsPanel(force){
  ensureSubagentsPanel();
  loadSubagentStatus('subagentStatusCardPanel');
  return !!force;
}
function openSubagentsPanel(){
  ensureSubagentsPanel();
  if(typeof switchPanel==='function') switchPanel('subagents',{bypassSettingsGuard:true});
  loadSubagentsPanel(true);
}
window.ensureSubagentsPanel=ensureSubagentsPanel;
window.loadSubagentsPanel=loadSubagentsPanel;
window.openSubagentsPanel=openSubagentsPanel;
window.workflowOpenWorkspacePanel=workflowOpenWorkspacePanel;
// Load MCP servers when system settings tab opens
const _origSwitchSettings=switchSettingsSection;
switchSettingsSection=function(name){
  _origSwitchSettings(name);
  if(name==='system'){loadMcpServers();loadMcpTools();loadGatewayStatus();loadSubagentStatus();}
};

// ── Checkpoints / Rollback ──────────────────────────────────────────────────

async function _loadCheckpoints(workspace){
  const container=$('checkpointListContainer');
  if(!container) return;
  try{
    const data=await api(`/api/rollback/list?workspace=${encodeURIComponent(workspace)}`);
    const checkpoints=data.checkpoints||[];
    if(!checkpoints.length){
      container.innerHTML=`<div style="color:var(--muted);font-size:12px;padding:8px 0">${esc(t('checkpoint_empty'))}</div>`;
      return;
    }
    let html='';
    for(const ck of checkpoints){
      const shortId=ck.id||ck.commit||'?';
      const msg=ck.message||'checkpoint';
      const date=ck.date_display||ck.date||'';
      const files=ck.files||0;
      html+=`
        <div class="detail-row" style="align-items:center;padding:6px 0;border-bottom:1px solid var(--border,rgba(255,255,255,0.08))">
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${esc(msg)}">${esc(msg)}</div>
            <div style="font-size:11px;color:var(--muted);margin-top:2px">
              <code style="font-size:10px">${esc(shortId)}</code>
              ${date ? ` · ${esc(date)}` : ''}
              ${files ? ` · ${esc(t('checkpoint_files'))}: ${files}` : ''}
            </div>
          </div>
          <div style="display:flex;gap:4px;flex-shrink:0;margin-left:8px">
            <button class="panel-head-btn" title="${esc(t('checkpoint_view_diff'))}" onclick="event.stopPropagation();_viewCheckpointDiff('${esc(workspace)}','${esc(ck.id)}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><path d="M12 20h9"/><path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4L16.5 3.5z"/></svg>
            </button>
            <button class="panel-head-btn" title="${esc(t('checkpoint_restore'))}" onclick="event.stopPropagation();_restoreCheckpoint('${esc(workspace)}','${esc(ck.id)}','${esc(msg.replace(/'/g,"\\'"))}')">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="16" height="16"><polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/></svg>
            </button>
          </div>
        </div>`;
    }
    container.innerHTML=html;
  }catch(e){
    container.innerHTML=`<div style="color:var(--error,#f87171);font-size:12px;padding:8px 0">${esc(t('checkpoint_error'))}: ${esc(e.message)}</div>`;
  }
}

async function _viewCheckpointDiff(workspace,checkpoint){
  const modal=document.getElementById('checkpointDiffModal');
  if(!modal){
    const m=document.createElement('div');
    m.id='checkpointDiffModal';
    m.style.cssText='position:fixed;inset:0;z-index:9999;display:flex;align-items:center;justify-content:center;background:rgba(0,0,0,0.6)';
    m.innerHTML=`
      <div style="background:var(--bg,${getComputedStyle(document.documentElement).getPropertyValue('--bg')||'#1a1a2e'});border:1px solid var(--border,rgba(255,255,255,0.12));border-radius:12px;width:90vw;max-width:800px;max-height:80vh;display:flex;flex-direction:column;box-shadow:0 8px 32px rgba(0,0,0,0.4)">
        <div style="display:flex;align-items:center;justify-content:space-between;padding:12px 16px;border-bottom:1px solid var(--border,rgba(255,255,255,0.08))">
          <div id="checkpointDiffModalTitle" style="font-weight:600;font-size:14px"></div>
          <button onclick="document.getElementById('checkpointDiffModal').style.display='none'" style="background:none;border:none;color:var(--fg);cursor:pointer;font-size:18px;padding:0 4px">&times;</button>
        </div>
        <div id="checkpointDiffModalBody" style="flex:1;overflow:auto;padding:12px 16px">
          <div style="color:var(--muted);font-size:12px">${esc(t('checkpoint_loading'))}</div>
        </div>
      </div>`;
    m.onclick=(e)=>{if(e.target===m) m.style.display='none';};
    document.body.appendChild(m);
  }
  modal.style.display='flex';
  $('checkpointDiffModalTitle').textContent=t('checkpoint_diff_title');
  $('checkpointDiffModalBody').innerHTML=`<div style="color:var(--muted);font-size:12px">${esc(t('checkpoint_loading'))}</div>`;
  try{
    const data=await api(`/api/rollback/diff?workspace=${encodeURIComponent(workspace)}&checkpoint=${encodeURIComponent(checkpoint)}`);
    const body=$('checkpointDiffModalBody');
    if(!data.total_changes){
      body.innerHTML=`<div style="color:var(--muted);font-size:12px">${esc(t('checkpoint_diff_no_changes'))}</div>`;
      return;
    }
    let html=`<div style="font-size:12px;margin-bottom:8px">${esc(t('checkpoint_diff_files_changed',data.total_changes))}</div>`;
    if(data.files_changed){
      html+='<div style="margin-bottom:8px">';
      for(const f of data.files_changed){
        const icon=f.status==='deleted'?'−':'~';
        const color=f.status==='deleted'?'var(--error,#f87171)':'var(--accent,#60a5fa)';
        html+=`<div style="font-size:12px;padding:2px 0"><span style="color:${color};font-weight:bold;margin-right:6px">${icon}</span><code style="font-size:11px">${esc(f.file)}</code></div>`;
      }
      html+='</div>';
    }
    if(data.diff){
      html+=`<pre style="background:var(--bg-secondary,rgba(0,0,0,0.3));border:1px solid var(--border,rgba(255,255,255,0.08));border-radius:8px;padding:12px;font-size:11px;line-height:1.4;overflow-x:auto;white-space:pre-wrap;word-break:break-all;max-height:50vh;overflow-y:auto;color:var(--fg)">${esc(data.diff)}</pre>`;
    }
    body.innerHTML=html;
  }catch(e){
    $('checkpointDiffModalBody').innerHTML=`<div style="color:var(--error,#f87171);font-size:12px">${esc(e.message)}</div>`;
  }
}

async function _restoreCheckpoint(workspace,checkpoint,message){
  const label=message||checkpoint;
  const ok=await showConfirmDialog({title:t('checkpoint_restore_confirm_title'),message:t('checkpoint_restore_confirm_message',label),confirmLabel:t('checkpoint_restore'),danger:true,focusCancel:true});
  if(!ok) return;
  try{
    const data=await api('/api/rollback/restore',{method:'POST',body:JSON.stringify({workspace,checkpoint})});
    if(data&&data.ok){
      showToast(t('checkpoint_restored')+(data.files_restored_count?` (${data.files_restored_count} ${t('checkpoint_files').toLowerCase()})`:''));
    }else{
      showToast((data&&data.error)||'Restore failed','error');
    }
  }catch(e){
    showToast(t('checkpoint_restore')+': '+e.message,'error');
  }
}

// ── Clickable file paths — open file in workspace panel ─────────────────────
// Called from .clickable-file-path spans in messages. Opens the workspace panel,
// navigates the file tree to the file, and loads the file preview.

async function openFileInWorkspace(filePath) {
  if (!filePath || !S.session) return;
  const sid = S.session.session_id;

  // 1. Ensure workspace panel is open in browse mode (shows file tree)
  if (typeof _setWorkspacePanelMode === 'function') {
    _setWorkspacePanelMode('browse');
  } else if (typeof openWorkspacePanel === 'function') {
    openWorkspacePanel('browse');
  }

  // 2. If a file preview is active, clear it to show the file tree
  if (typeof clearPreview === 'function') {
    clearPreview({ keepPanelOpen: true });
  }

  // 3. Ensure root dir is loaded
  if (!S.entries || !S.entries.length || S.currentDir !== '.') {
    try {
      await loadDir('.');
    } catch (_) { return; }
  }

  // 4. Parse path: split into directory parts + filename
  const parts = filePath.replace(/^\.\//, '').split('/');
  const fileName = parts.pop();
  const dirChain = parts.length ? parts : []; // e.g. ["src", "components"]

  // 5. Expand all ancestor directories progressively
  //    For each directory in the chain, mark expanded and fetch children.
  let accumulated = '';
  for (const segment of dirChain) {
    accumulated += (accumulated ? '/' : '') + segment;

    if (S._expandedDirs) S._expandedDirs.add(accumulated);

    // Fetch children if not yet cached
    if (!S._dirCache || !S._dirCache[accumulated]) {
      try {
        const data = await api(`/api/list?session_id=${encodeURIComponent(sid)}&path=${encodeURIComponent(accumulated)}`);
        if (S._dirCache) S._dirCache[accumulated] = data.entries || [];
      } catch (_) {
        if (S._dirCache) S._dirCache[accumulated] = [];
      }
    }
  }

  // 6. Re-render the file tree (shows expanded directories + children)
  if (typeof renderFileTree === 'function') renderFileTree();

  // 7. Persist expanded dirs
  if (typeof _saveExpandedDirs === 'function') _saveExpandedDirs();

  // 8. Open the file in the preview pane
  let openOk = false;
  try {
    if (typeof openFile === 'function') {
      await openFile(filePath);
      openOk = true;
    }
  } catch (_) { /* open failed — file may not exist */ }

  // 9. Highlight + scroll to the file in the tree (best-effort)
  requestAnimationFrame(() => {
    const fileItems = document.querySelectorAll('.file-item');
    let found = null;
    for (const item of fileItems) {
      const nameEl = item.querySelector('.file-name');
      if (!nameEl || nameEl.textContent !== fileName) continue;
      // Confirm path match: check if this item is at the right depth
      const style = item.getAttribute('style') || '';
      const depthMatch = style.match(/padding-left[^:]*:\s*(\d+)px/);
      const expectedDepth = 8 + dirChain.length * 16;
      if (depthMatch && parseInt(depthMatch[1]) !== expectedDepth) continue;
      found = item;
      break;
    }
    if (found) {
      // Remove highlight from all items
      fileItems.forEach(el => el.classList.remove('current'));
      found.classList.add('current');
      found.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  });
}

// ── EVEY TOOLS PANEL ──────────────────────────────────────────────────

const EVEY_API = '/api/evey';
let eveyCurrentTab = 'health';

/** Switch Evey subtab */
function eveySwitchTab(tab) {
  eveyCurrentTab = tab;
  document.querySelectorAll('.evey-tab').forEach(t => t.classList.toggle('evey-active', t.dataset.eveyTab === tab));
  const el = document.getElementById('eveyContent');
  if (!el) return;
  switch (tab) {
    case 'health': loadEveyHealth(); break;
    case 'telemetry': eveyShowTelemetryPicker(); break;
    case 'learner': eveyShowLearner(); break;
    case 'validate': eveyShowValidate(); break;
    case 'delegation': eveyShowDelegation(); break;
    case 'memory': eveyShowMemory(); break;
    case 'habits': eveyShowHabits(); break;
    case 'cache': loadEveyCache(); break;
    case 'schedule': eveyShowSchedule(); break;
    case 'watchdog': eveyShowWatchdog(); break;
  }
}

function eveyHtml(id, h) { const el = document.getElementById(id) || document.getElementById('eveyContent'); if (el && id === 'eveyContent') el.innerHTML = h; else if (el) el.innerHTML = h; }
function eveyRow(l, v) { return '<div class="evey-row"><span class="evey-row-lbl">' + l + '</span><span>' + v + '</span></div>'; }

/** Load Evey panel when switching to it */
function loadEveyPanel() {
  eveySwitchTab(eveyCurrentTab || 'health');
}

// ── HEALTH ──
async function loadEveyHealth() {
  const el = document.getElementById('eveyContent');
  el.innerHTML = '<div class="evey-loading">Loading system status...</div>';
  try {
    const d = await api(EVEY_API + '/status');
    let h = '<div class="evey-grid evey-mb3">';
    if (d.system) {
      h += '<div class="evey-stat"><div class="evey-stat-val">' + (d.system.memory_pct || '?') + '%</div><div class="evey-stat-lbl">RAM Used</div></div>';
      h += '<div class="evey-stat"><div class="evey-stat-val">' + Math.round((d.system.uptime || 0) / 3600) + 'h</div><div class="evey-stat-lbl">Uptime</div></div>';
      h += '<div class="evey-stat"><div class="evey-stat-val">' + (d.system.cpus || '?') + '</div><div class="evey-stat-lbl">CPUs</div></div>';
    }
    if (d.process) {
      h += '<div class="evey-stat"><div class="evey-stat-val">' + d.process.pid + '</div><div class="evey-stat-lbl">PID</div></div>';
    }
    if (d.evey) {
      h += '<div class="evey-stat"><div class="evey-stat-val">' + d.evey.learnings + '</div><div class="evey-stat-lbl">Learnings</div></div>';
      h += '<div class="evey-stat"><div class="evey-stat-val">' + d.evey.delegation_scores + '</div><div class="evey-stat-lbl">Del. Scores</div></div>';
    }
    if (d.watchdog) {
      const cls = d.watchdog.status === 'alive' ? 'evey-b-green' : 'evey-b-orange';
      h += '<div class="evey-stat"><div class="evey-stat-val"><span class="evey-badge ' + cls + '">' + d.watchdog.status + '</span></div><div class="evey-stat-lbl">Watchdog</div></div>';
    }
    h += '</div><div class="evey-card"><h3>📋 System Details</h3>';
    if (d.system) {    h += eveyRow('Hostname', d.system.hostname) + eveyRow('Platform', d.system.platform) + eveyRow('Load', (d.system.loadavg || []).map(function(l){return l.toFixed(2)}).join(', ')); }
    if (d.process) { h += eveyRow('Python', d.process.version) + eveyRow('Arch', (d.process.arch||'') + ' | RSS: ' + (d.process.memory_rss || '?') + ' MB'); }
    h += '</div>';
    el.innerHTML = h;
  } catch (e) {
    el.innerHTML = '<div class="evey-card"><h3>❌ Error</h3><div class="evey-log">' + e.message + '</div></div>';
  }
}

// ── TELEMETRY ──
function eveyShowTelemetryPicker() {
  const el = document.getElementById('eveyContent');
  el.innerHTML = '<div class="evey-card"><h3>📊 Telemetry</h3><div style="display:flex;gap:4px;flex-wrap:wrap;">' +
    ['session_metrics','delegation_stats','tool_stats','recent_errors'].map(function(t) {
      return '<button class="evey-btn evey-btn-sm" onclick="loadEveyTelemetry(\'' + t + '\')">' + t.replace(/_/g, ' ') + '</button>';
    }).join('') +
    '</div><div id="eveyTelemetryResult" class="evey-mt2"><div class="evey-loading">Select a type above</div></div></div>';
}

async function loadEveyTelemetry(type) {
  const el = document.getElementById('eveyTelemetryResult');
  el.innerHTML = '<div class="evey-loading">Loading...</div>';
  try {
    const d = await api(EVEY_API + '/telemetry?type=' + type);
    let h = '';
    if (type === 'session_metrics' && d.metrics) {
      h = '<div class="evey-grid evey-mt2">';
      for (var k in d.metrics) {
        h += '<div class="evey-stat"><div class="evey-stat-val">' + d.metrics[k] + '</div><div class="evey-stat-lbl">' + k.replace(/_/g, ' ') + '</div></div>';
      }
      h += '</div>';
    } else if (type === 'delegation_stats' && d.models) {
      h = '<div class="evey-card evey-mt2"><h3>Per Model</h3>';
      for (var m in d.models) {
        var s = d.models[m];
        h += '<div class="evey-row"><span>' + m + '</span><span>' + s.calls + ' calls, ' + (s.success_rate || '') + '</span></div>';
      }
      h += '</div>';
      if (d.recommendation) h += '<div style="font-size:0.75rem;color:var(--accent)">🏆 ' + d.recommendation + '</div>';
    } else if (type === 'tool_stats' && d.tools) {
      h = '<div class="evey-card evey-mt2"><h3>Per Tool</h3>';
      for (var m in d.tools) {
        var s = d.tools[m];
        h += '<div class="evey-row"><span>' + m + '</span><span>' + s.calls + ' calls, ' + (s.error_rate || '') + '</span></div>';
      }
      h += '</div>';
    } else if (type === 'recent_errors' && d.errors && d.errors.length) {
      h = '<div class="evey-card evey-mt2"><h3>⚠ Recent Errors (' + d.errors.length + ')</h3>';
      for (var i = 0; i < Math.min(d.errors.length, 10); i++) {
        var e = d.errors[i];
        h += '<div class="evey-log">' + (e.ts || '').slice(0, 19) + ' | ' + (e.error || e.message || '') + '</div>';
      }
      h += '</div>';
    } else {
      h = '<div class="evey-card evey-mt2"><p style="color:var(--muted);font-size:12px">No data available</p></div>';
    }
    el.innerHTML = h;
  } catch (e) {
    el.innerHTML = '<div class="evey-log">❌ ' + e.message + '</div>';
  }
}

// ── LEARNER ──
function eveyShowLearner() {
  var el = document.getElementById('eveyContent');
  el.innerHTML =
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">' +
    '<div class="evey-card">' +
      '<h3>📝 Record Lesson</h3>' +
      '<input class="evey-input evey-mb2" id="eveyLearnTask" placeholder="Task description">' +
      '<input class="evey-input evey-mb2" id="eveyLearnModel" placeholder="Model/Tool used">' +
      '<input class="evey-input evey-mb2" id="eveyLearnScore" type="number" placeholder="Quality (1-10)" min="1" max="10">' +
      '<textarea class="evey-textarea evey-mb2" id="eveyLearnWorked" rows="2" placeholder="What worked?"></textarea>' +
      '<textarea class="evey-textarea evey-mb2" id="eveyLearnFailed" rows="2" placeholder="What went wrong?"></textarea>' +
      '<textarea class="evey-textarea evey-mb2" id="eveyLearnDiff" rows="2" placeholder="Do differently?"></textarea>' +
      '<button class="evey-btn" onclick="eveyRecordLesson()">💾 Save</button><div id="eveyLearnResult" class="evey-mt2"></div>' +
    '</div>' +
    '<div>' +
      '<div class="evey-card">' +
        '<h3>🔍 Find Lessons</h3>' +
        '<input class="evey-input evey-mb2" id="eveyApplyTask" placeholder="Task description">' +
        '<button class="evey-btn" onclick="eveyApplyLearnings()">🔍 Search</button><div id="eveyApplyResult" class="evey-mt2"></div>' +
      '</div>' +
      '<div class="evey-card evey-mt2">' +
        '<div class="evey-flex evey-mb2"><h3 style="margin:0">📚 Recent</h3><button class="evey-btn evey-btn-sm" onclick="loadEveyLearnings()">🔄</button></div>' +
        '<div id="eveyLearningsList"><div class="evey-loading">Loading...</div></div>' +
      '</div>' +
    '</div></div>';
  loadEveyLearnings();
}

async function loadEveyLearnings() {
  try {
    var d = await api(EVEY_API + '/learnings');
    var el = document.getElementById('eveyLearningsList');
    if (!el) return;
    if (!d.learnings || !d.learnings.length) { el.innerHTML = '<span style="color:var(--muted);font-size:11px">No lessons yet</span>'; return; }
    var h = '';
    for (var i = Math.max(0, d.learnings.length - 10); i < d.learnings.length; i++) {
      var l = d.learnings[i];
      var cls = l.quality_score >= 7 ? 'evey-sc-high' : l.quality_score >= 4 ? 'evey-sc-mid' : 'evey-sc-low';
      h += '<div class="evey-log"><span class="' + cls + '">[' + l.quality_score + ']</span> ' + (l.task || '').slice(0, 80) + ' <span style="font-size:0.6rem;color:var(--muted)">' + (l.date || '').slice(0, 10) + '</span></div>';
    }
    el.innerHTML = h || '<span style="color:var(--muted);font-size:11px">No lessons</span>';
  } catch (e) { var el = document.getElementById('eveyLearningsList'); if (el) el.innerHTML = '<span style="color:var(--red);font-size:11px">' + e.message + '</span>'; }
}

async function eveyRecordLesson() {
  var task = document.getElementById('eveyLearnTask');
  if (!task || !task.value) { document.getElementById('eveyLearnResult').innerHTML = '<span style="color:var(--red)">❌ Task required</span>'; return; }
  var body = {
    task: task.value,
    model_or_tool: (document.getElementById('eveyLearnModel') || {}).value || '',
    quality_score: parseInt((document.getElementById('eveyLearnScore') || {}).value) || 5,
    what_worked: (document.getElementById('eveyLearnWorked') || {}).value || '',
    what_failed: (document.getElementById('eveyLearnFailed') || {}).value || '',
    do_differently: (document.getElementById('eveyLearnDiff') || {}).value || '',
    tags: ['general']
  };
  try {
    var r = await api(EVEY_API + '/learn', {method:'POST',body:JSON.stringify(body)});
    document.getElementById('eveyLearnResult').innerHTML = r.status === 'learned' ? '<span style="color:var(--green)">✅ Saved (q=' + r.quality_score + ')</span>' : '<span style="color:var(--red)">❌ ' + (r.error || '') + '</span>';
    loadEveyLearnings();
  } catch (e) { document.getElementById('eveyLearnResult').innerHTML = '<span style="color:var(--red)">❌ ' + e.message + '</span>'; }
}

async function eveyApplyLearnings() {
  var inp = document.getElementById('eveyApplyTask');
  if (!inp || !inp.value) { document.getElementById('eveyApplyResult').innerHTML = '<span style="color:var(--red)">❌ Task required</span>'; return; }
  try {
    var r = await api(EVEY_API + '/learnings/apply', {method:'POST',body:JSON.stringify({task_description:inp.value,model_or_tool:'',max_results:10})});
    var el = document.getElementById('eveyApplyResult');
    if (!el) return;
    var h = '';
    if (r.applicable_lessons && r.applicable_lessons.length) {
      for (var i = 0; i < r.applicable_lessons.length; i++) {
        var l = r.applicable_lessons[i];
        h += '<div class="evey-log" style="border-left:2px solid var(--accent);padding-left:6px;margin-bottom:3px"><b>q=' + l.quality_score + '</b> ' + (l.task || '').slice(0, 60) + '<br><span style="font-size:0.65rem">' + (l.advice || '') + '</span></div>';
      }
    } else {
      h = '<span style="color:var(--muted);font-size:11px">' + (r.message || 'No matches') + '</span>';
    }
    el.innerHTML = h;
  } catch (e) { var el = document.getElementById('eveyApplyResult'); if (el) el.innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }
}

// ── VALIDATE ──
function eveyShowValidate() {
  document.getElementById('eveyContent').innerHTML =
    '<div class="evey-card"><h3>✅ Output Validation</h3>' +
    '<textarea class="evey-textarea evey-mb2" id="eveyValTask" rows="2" placeholder="Original task..."></textarea>' +
    '<textarea class="evey-textarea evey-mb2" id="eveyValResult" rows="4" placeholder="Output to validate..."></textarea>' +
    '<input class="evey-input evey-mb2" id="eveyValModel" placeholder="Model used (optional)">' +
    '<button class="evey-btn" onclick="eveyValidate()">🔍 Validate</button><div id="eveyValResultBox" class="evey-mt2"></div></div>';
}

async function eveyValidate() {
  var task = document.getElementById('eveyValTask'); var result = document.getElementById('eveyValResult');
  if (!task || !task.value || !result || !result.value) { document.getElementById('eveyValResultBox').innerHTML = '<span style="color:var(--red)">❌ Task + Result required</span>'; return; }
  try {
    var d = await api(EVEY_API + '/validate', {method:'POST',body:JSON.stringify({task:task.value,result:result.value,model_used:(document.getElementById('eveyValModel')||{}).value||'unknown'})});
    var cls = d.score >= 7 ? 'evey-sc-high' : d.score >= 4 ? 'evey-sc-mid' : 'evey-sc-low';
    var h = '<div class="evey-card evey-mt2"><h3>Result</h3>' + eveyRow('Score', '<span class="' + cls + '" style="font-size:1.3rem;font-weight:700">' + d.score + '/10</span>');
    h += eveyRow('Recommendation', '<span style="color:var(--accent)">' + d.recommendation + '</span>');
    h += eveyRow('Length', d.length + ' chars');
    if (d.pattern_flags && d.pattern_flags.length) {
      h += '<div class="evey-mt2"><span style="color:var(--orange);font-size:0.75rem">⚠ Flags:</span>';
      for (var i = 0; i < d.pattern_flags.length; i++) h += '<div class="evey-log">' + d.pattern_flags[i] + '</div>';
      h += '</div>';
    }
    h += '</div>';
    document.getElementById('eveyValResultBox').innerHTML = h;
  } catch (e) { document.getElementById('eveyValResultBox').innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }
}

// ── DELEGATION ──
function eveyShowDelegation() {
  document.getElementById('eveyContent').innerHTML =
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">' +
    '<div class="evey-card"><h3>📝 Log Delegation</h3>' +
    '<input class="evey-input evey-mb2" id="eveyDelModel" placeholder="Model name">' +
    '<select class="evey-select evey-mb2" id="eveyDelType"><option value="code">Code</option><option value="research">Research</option><option value="analysis">Analysis</option><option value="creative">Creative</option><option value="summary">Summary</option></select>' +
    '<input class="evey-input evey-mb2" id="eveyDelScore" type="number" placeholder="Score (0-10)" min="0" max="10">' +
    '<input class="evey-input evey-mb2" id="eveyDelTokens" type="number" placeholder="Tokens used">' +
    '<button class="evey-btn" onclick="eveyLogDelegation()">💾 Log</button><div id="eveyDelResult" class="evey-mt2"></div></div>' +
    '<div class="evey-card"><div class="evey-flex evey-mb2"><h3 style="margin:0">📊 Stats</h3><button class="evey-btn evey-btn-sm" onclick="eveyLoadDelegationStats()">🔄</button></div><div id="eveyDelStats"><div class="evey-loading">Load...</div></div></div></div>';
  eveyLoadDelegationStats();
}

async function eveyLogDelegation() {
  var model = document.getElementById('eveyDelModel'); if (!model || !model.value) { document.getElementById('eveyDelResult').innerHTML = '<span style="color:var(--red)">❌ Model required</span>'; return; }
  try {
    var r = await api(EVEY_API + '/delegation/log', {method:'POST',body:JSON.stringify({model:model.value,task_type:document.getElementById('eveyDelType').value,score:parseInt(document.getElementById('eveyDelScore').value)||5,tokens_used:parseInt(document.getElementById('eveyDelTokens').value)||0})});
    document.getElementById('eveyDelResult').innerHTML = r.status === 'logged' ? '<span style="color:var(--green)">✅ Logged</span>' : '<span style="color:var(--red)">❌ ' + (r.error||'') + '</span>';
    eveyLoadDelegationStats();
  } catch (e) { document.getElementById('eveyDelResult').innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }
}

async function eveyLoadDelegationStats() {
  try {
    var d = await api(EVEY_API + '/delegation/stats');
    var el = document.getElementById('eveyDelStats');
    if (!el) return;
    if (!d.models || !Object.keys(d.models).length) { el.innerHTML = '<span style="color:var(--muted);font-size:11px">No data</span>'; return; }
    var h = '';
    var sorted = Object.keys(d.models).sort(function(a,b){return d.models[b].avg_score - d.models[a].avg_score});
    for (var i = 0; i < sorted.length; i++) {
      var m = sorted[i]; var s = d.models[m];
      h += '<div class="evey-row"><span>' + m + '</span><span>' + s.avg_score + ' | ' + s.calls + 'x | ' + s.success_rate + '</span></div>';
    }
    if (d.recommendation) h += '<div class="evey-mt2" style="font-size:0.7rem;color:var(--accent)">🏆 ' + d.recommendation + '</div>';
    el.innerHTML = h;
  } catch (e) { var el = document.getElementById('eveyDelStats'); if (el) el.innerHTML = '<span style="color:var(--red);font-size:11px">' + e.message + '</span>'; }
}

// ── MEMORY ──
function eveyShowMemory() {
  document.getElementById('eveyContent').innerHTML =
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">' +
    '<div class="evey-card"><div class="evey-flex evey-mb2"><h3 style="margin:0">🧠 Scores</h3><button class="evey-btn evey-btn-sm" onclick="eveyLoadMemory()">🔄</button></div><div id="eveyMemRank"><div class="evey-loading">Loading...</div></div></div>' +
    '<div>' +
      '<div class="evey-card"><h3>🔄 Decay</h3><div style="display:flex;gap:4px;align-items:center;margin-bottom:6px;"><input class="evey-input" id="eveyDecayTh" type="number" value="0.1" step="0.05" style="width:80px"><button class="evey-btn evey-btn-sm" onclick="eveyRunDecay()">Scan</button></div><div id="eveyDecayResult"><div class="evey-loading">Load...</div></div></div>' +
      '<div class="evey-card evey-mt2"><h3>⬆️ Boost</h3><div style="display:flex;gap:4px;"><input class="evey-input" id="eveyBoostKey" placeholder="Memory key"><button class="evey-btn evey-btn-sm" onclick="eveyBoostMemory()">Boost</button></div><div id="eveyBoostResult" class="evey-mt2"></div></div>' +
    '</div></div>';
  eveyLoadMemory();
}

async function eveyLoadMemory() {
  try {
    var d = await api(EVEY_API + '/memory/score', {method:'POST',body:JSON.stringify({action:'rank'})});
    var el = document.getElementById('eveyMemRank');
    if (!el) return;
    if (!d.memories || !d.memories.length) { el.innerHTML = '<span style="color:var(--muted);font-size:11px">No memories</span>'; return; }
    var h = '';
    for (var i = 0; i < Math.min(d.memories.length, 15); i++) {
      var m = d.memories[i];
      h += '<div class="evey-row"><span>' + (m.key || '').slice(0, 45) + '</span><span>' + m.decayed_score + '</span></div>';
    }
    el.innerHTML = h;
  } catch (e) { var el = document.getElementById('eveyMemRank'); if (el) el.innerHTML = '<span style="color:var(--red);font-size:11px">' + e.message + '</span>'; }
}

async function eveyRunDecay() {
  var th = parseFloat((document.getElementById('eveyDecayTh') || {}).value) || 0.1;
  try {
    var d = await api(EVEY_API + '/memory/decay', {method:'POST',body:JSON.stringify({threshold:th})});
    var el = document.getElementById('eveyDecayResult');
    if (!el) { document.getElementById('eveyContent').innerHTML += '<div id="eveyDecayResult"></div>'; el = document.getElementById('eveyDecayResult'); }
    var h = eveyRow('Healthy', '<span style="color:var(--green)">' + d.healthy_count + '</span>') + eveyRow('Flagged', '<span style="color:var(--orange)">' + (d.flagged_for_removal ? d.flagged_for_removal.length : 0) + '</span>');
    if (d.flagged_for_removal && d.flagged_for_removal.length) {
      for (var i = 0; i < Math.min(d.flagged_for_removal.length, 8); i++) {
        var f = d.flagged_for_removal[i];
        h += '<div class="evey-log">' + (f.key || '').slice(0, 45) + ' (' + f.score + ')</div>';
      }
    }
    if (d.suggestion) h += '<div style="font-size:0.7rem;color:var(--muted);margin-top:4px">💡 ' + d.suggestion + '</div>';
    el.innerHTML = h;
  } catch (e) { var el = document.getElementById('eveyDecayResult'); if (el) el.innerHTML = '<span style="color:var(--red);font-size:11px">' + e.message + '</span>'; }
}

async function eveyBoostMemory() {
  var key = document.getElementById('eveyBoostKey');
  if (!key || !key.value) { document.getElementById('eveyBoostResult').innerHTML = '<span style="color:var(--red)">❌ Key required</span>'; return; }
  try {
    var d = await api(EVEY_API + '/memory/score', {method:'POST',body:JSON.stringify({action:'boost',memory_key:key.value})});
    document.getElementById('eveyBoostResult').innerHTML = d.status === 'boosted' ? '<span style="color:var(--green)">✅ Boosted → ' + d.new_importance + '</span>' : '<span style="color:var(--red)">❌ ' + (d.error||'') + '</span>';
    eveyLoadMemory();
  } catch (e) { document.getElementById('eveyBoostResult').innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }
}

// ── HABITS ──
function eveyShowHabits() {
  document.getElementById('eveyContent').innerHTML =
    '<div class="evey-card"><div class="evey-flex evey-mb2"><h3 style="margin:0">⏰ Patterns</h3><button class="evey-btn evey-btn-sm" onclick="eveyLoadHabits()">🔄</button></div><div id="eveyHabitsContent"><div class="evey-loading">Analyzing...</div></div></div>' +
    '<div class="evey-card"><h3>📝 Log Interaction</h3><div style="display:flex;gap:4px;"><input class="evey-input" id="eveyHabitTopic" placeholder="Topic" style="flex:1"><input class="evey-input" id="eveyHabitLen" type="number" placeholder="Length" style="width:80px"><button class="evey-btn evey-btn-sm" onclick="eveyLogHabit()">Log</button></div><div id="eveyHabitResult" class="evey-mt2"></div></div>';
  eveyLoadHabits();
}

async function eveyLoadHabits() {
  try {
    var d = await api(EVEY_API + '/habits/insights');
    var el = document.getElementById('eveyHabitsContent');
    if (!el) return;
    if (d.status === 'no_data') { el.innerHTML = '<span style="color:var(--muted);font-size:11px">No interactions yet</span>'; return; }
    var h = '<div class="evey-grid evey-mb2">';
    ['total_interactions','response_success_rate','avg_message_length'].forEach(function(k) {
      h += '<div class="evey-stat"><div class="evey-stat-val">' + (d[k] || 0) + '</div><div class="evey-stat-lbl">' + k.replace(/_/g, ' ') + '</div></div>';
    });
    h += '</div>';
    if (d.peak_hours && d.peak_hours.length) {
      h += '<div style="margin-top:6px"><b style="font-size:0.75rem;color:var(--accent)">🕐 Peak Hours</b>';
      for (var i = 0; i < d.peak_hours.length; i++) h += '<div class="evey-log">' + d.peak_hours[i] + '</div>';
      h += '</div>';
    }
    if (d.recommendations && d.recommendations.length) {
      h += '<div style="margin-top:6px"><b style="font-size:0.75rem;color:var(--accent)">💡 Insights</b>';
      for (var i = 0; i < d.recommendations.length; i++) h += '<div class="evey-log" style="color:var(--accent)">💡 ' + d.recommendations[i] + '</div>';
      h += '</div>';
    }
    el.innerHTML = h;
  } catch (e) { var el = document.getElementById('eveyHabitsContent'); if (el) el.innerHTML = '<span style="color:var(--red);font-size:11px">' + e.message + '</span>'; }
}

async function eveyLogHabit() {
  try {
    var r = await api(EVEY_API + '/habits/log', {method:'POST',body:JSON.stringify({topic:(document.getElementById('eveyHabitTopic')||{}).value||'general',v_message_length:parseInt((document.getElementById('eveyHabitLen')||{}).value)||0})});
    document.getElementById('eveyHabitResult').innerHTML = r.status === 'logged' ? '<span style="color:var(--green)">✅ Logged</span>' : '<span style="color:var(--red)">❌</span>';
    eveyLoadHabits();
  } catch (e) { document.getElementById('eveyHabitResult').innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }
}

// ── CACHE ──
async function loadEveyCache() {
  var el = document.getElementById('eveyContent');
  try {
    var d = await api(EVEY_API + '/cache');
    el.innerHTML = '<div class="evey-grid">' +
      ['valid','total','total_hits'].map(function(k) {
        return '<div class="evey-stat"><div class="evey-stat-val">' + (d[k] || 0) + '</div><div class="evey-stat-lbl">' + k.replace(/_/g, ' ') + '</div></div>';
      }).join('') +
      '</div><div class="evey-card evey-mt2"><h3>💾 Cache</h3>' +
      eveyRow('Max Entries', d.max || 100) + eveyRow('TTL', (d.ttl_hours || 24) + 'h') +
      '</div>';
  } catch (e) { el.innerHTML = '<div class="evey-card"><span style="color:var(--red);font-size:11px">' + e.message + '</span></div>'; }
}

// ── SCHEDULE ──
function eveyShowSchedule() {
  document.getElementById('eveyContent').innerHTML =
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;">' +
    '<div class="evey-card"><h3>📅 Add Event</h3>' +
    '<input class="evey-input evey-mb2" id="eveySchedTitle" placeholder="Title">' +
    '<input class="evey-input evey-mb2" id="eveySchedWhen" placeholder="When (e.g., tomorrow 2pm)">' +
    '<select class="evey-select evey-mb2" id="eveySchedCat"><option value="task">Task</option><option value="meeting">Meeting</option><option value="reminder">Reminder</option><option value="deadline">Deadline</option></select>' +
    '<button class="evey-btn" onclick="eveyAddSchedule()">➕ Add</button><div id="eveySchedResult" class="evey-mt2"></div></div>' +
    '<div class="evey-card"><div class="evey-flex evey-mb2"><h3 style="margin:0">📋 Events</h3><button class="evey-btn evey-btn-sm" onclick="eveyLoadSchedule()">🔄</button></div><div id="eveySchedList"><div class="evey-loading">Loading...</div></div></div></div>';
  eveyLoadSchedule();
}

async function eveyLoadSchedule() {
  try {
    var d = await api(EVEY_API + '/schedule');
    var el = document.getElementById('eveySchedList');
    if (!el) return;
    if (!d.events || !d.events.length) { el.innerHTML = '<span style="color:var(--muted);font-size:11px">No upcoming events</span>'; return; }
    var h = '';
    for (var i = 0; i < d.events.length; i++) {
      var e = d.events[i];
      h += '<div class="evey-log" style="border-left:2px solid var(--accent);padding-left:6px;margin-bottom:3px"><b>' + e.title + '</b><br><span style="font-size:0.65rem">' + e.when + ' | ' + e.category + '</span></div>';
    }
    el.innerHTML = h;
  } catch (e) { var el = document.getElementById('eveySchedList'); if (el) el.innerHTML = '<span style="color:var(--red);font-size:11px">' + e.message + '</span>'; }
}

async function eveyAddSchedule() {
  var title = document.getElementById('eveySchedTitle'); var when = document.getElementById('eveySchedWhen');
  if (!title || !title.value || !when || !when.value) { document.getElementById('eveySchedResult').innerHTML = '<span style="color:var(--red)">❌ Title + When required</span>'; return; }
  try {
    var r = await api(EVEY_API + '/schedule', {method:'POST',body:JSON.stringify({title:title.value,when:when.value,duration_minutes:30,category:(document.getElementById('eveySchedCat')||{}).value||'task',notes:''})});
    document.getElementById('eveySchedResult').innerHTML = r.status === 'added' ? '<span style="color:var(--green)">✅ Added</span>' : '<span style="color:var(--red)">❌ ' + (r.error||'') + '</span>';
    eveyLoadSchedule();
  } catch (e) { document.getElementById('eveySchedResult').innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }
}

// ── WATCHDOG ──
function eveyShowWatchdog() {
  document.getElementById('eveyContent').innerHTML =
    '<div class="evey-card"><div class="evey-flex evey-mb2"><h3 style="margin:0">🛡️ Watchdog</h3><button class="evey-btn evey-btn-sm" onclick="eveyLoadWatchdog()">🔄</button></div><div id="eveyWatchdogContent"><div class="evey-loading">Loading...</div></div></div>' +
    '<div class="evey-card"><h3>💓 Heartbeat</h3><button class="evey-btn" onclick="eveySendHeartbeat()">Send Heartbeat</button><div id="eveyHeartbeatResult" class="evey-mt2"></div></div>';
  eveyLoadWatchdog();
}

async function eveyLoadWatchdog() {
  try {
    var d = await api(EVEY_API + '/watchdog/status');
    var el = document.getElementById('eveyWatchdogContent');
    if (!el) return;
    var cls = d.is_silent_alert ? 'evey-b-orange' : d.silent_minutes >= 0 ? 'evey-b-green' : 'evey-b-red';
    var st = d.is_silent_alert ? '🔴 SILENT' : d.silent_minutes >= 0 ? '🟢 ALIVE' : '⚫ NEVER';
    var h = eveyRow('Status', '<span class="evey-badge ' + cls + '">' + st + '</span>');
    h += eveyRow('Last Activity', (d.last_activity || 'none') + ' @ ' + (d.last_heartbeat || 'never'));
    h += eveyRow('Silent For', d.silent_minutes >= 0 ? d.silent_minutes + ' min' : 'N/A');
    h += eveyRow('Total Heartbeats', d.total_heartbeats || 0);
    h += eveyRow('Alerts Today', d.alerts_sent_today || 0);
    h += eveyRow('Threshold', d.threshold_minutes + ' min');
    el.innerHTML = h;
  } catch (e) { var el = document.getElementById('eveyWatchdogContent'); if (el) el.innerHTML = '<span style="color:var(--red);font-size:11px">' + e.message + '</span>'; }
}

async function eveySendHeartbeat() {
  try {
    var d = await api(EVEY_API + '/watchdog/heartbeat', {method:'POST',body:JSON.stringify({activity:'webui panel'})});
    document.getElementById('eveyHeartbeatResult').innerHTML = d.status === 'alive' ? '<span style="color:var(--green)">✅ Sent (' + d.total_heartbeats + ' total)</span>' : '<span style="color:var(--red)">❌ ' + (d.error||'') + '</span>';
    eveyLoadWatchdog();
  } catch (e) { document.getElementById('eveyHeartbeatResult').innerHTML = '<span style="color:var(--red)">' + e.message + '</span>'; }
}

// ── Memory Cockpit Helpers ──

function _memFormatSize(bytes) {
  if (!bytes || bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB'];
  const i = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1);
  return (bytes / Math.pow(1024, i)).toFixed(i === 0 ? 0 : 1) + ' ' + units[i];
}

function _memTimeAgo(timestamp) {
  if (!timestamp) return '';
  const diff = Date.now() - timestamp * 1000;
  const mins = Math.round(diff / 60000);
  if (mins < 1) return 'just now';
  if (mins < 60) return mins + 'm ago';
  const hours = Math.round(mins / 60);
  if (hours < 24) return hours + 'h ago';
  const days = Math.round(hours / 24);
  return days + 'd ago';
}

function _memInsertMarkdown(before, after) {
  const ta = $('memEditContent');
  if (!ta) return;
  const start = ta.selectionStart;
  const end = ta.selectionEnd;
  const text = ta.value;
  const selected = text.substring(start, end);
  const newText = text.substring(0, start) + before + selected + after + text.substring(end);
  ta.value = newText;
  ta.selectionStart = start + before.length;
  ta.selectionEnd = start + before.length + selected.length;
  ta.focus();
  _memUpdateWordCount();
}

function _memUpdateWordCount() {
  const ta = $('memEditContent');
  const wc = $('memWordCount');
  if (!ta || !wc) return;
  const text = ta.value.trim();
  const words = text ? text.split(/\s+/).length : 0;
  const chars = text.length;
  wc.textContent = words + ' words · ' + chars + ' chars';
}

function _updateMemoryToolsStats() {
  if (!_memoryData) return;
  const notesSize = (_memoryData.memory || '').length;
  const profileSize = (_memoryData.user || '').length;
  const mtime = _memoryData.memory_mtime || _memoryData.user_mtime || 0;
  const notesEl = $('memStatNotes');
  const profileEl = $('memStatProfile');
  const mtimeEl = $('memStatMtime');
  if (notesEl) notesEl.textContent = _memFormatSize(notesSize);
  if (profileEl) profileEl.textContent = _memFormatSize(profileSize);
  if (mtimeEl) mtimeEl.textContent = mtime ? new Date(mtime * 1000).toLocaleString() : '—';
  // Context
  const ctxSpace = $('memCtxSpace');
  if (ctxSpace) {
    const ws = typeof getActiveSpaceQuery === 'function' ? getActiveSpaceQuery().replace('?workspace=','') : 'default';
    ctxSpace.textContent = ws || 'default';
  }
}

function _memorySidebarDots() {
  for (const s of MEMORY_SECTIONS) {
    const items = document.querySelectorAll('#memoryPanel .side-menu-item[data-memory-key="' + s.key + '"]');
    items.forEach(el => {
      const content = _memorySectionContent(s.key);
      if (s.key === 'supermemory' || s.key === 'hybrid') {
        el.dataset.memStatus = 'search';
        return;
      }
      el.dataset.memStatus = content ? 'active' : 'empty';
      // Update subtitle timestamp
      const mtime = _memorySectionMtime(s.key);
      const sub = $('memSubtitle-' + s.key);
      if (sub) sub.textContent = mtime ? _memTimeAgo(mtime) : '';
    });
  }
}

function openMemoryTools() {
  _updateMemoryToolsStats();
  _initMemoryToolsSearch();
}

function closeMemoryTools() {
  // Nothing special needed - CSS handles visibility
}

function copyMemoryContent() {
  if (!_currentMemorySection) { showToast('No section selected', 'error'); return; }
  const text = _memorySectionContent(_currentMemorySection);
  if (!text) { showToast('No content to copy', 'error'); return; }
  navigator.clipboard.writeText(text).then(
    () => showToast('Copied to clipboard'),
    () => showToast('Clipboard write failed', 'error')
  );
}

function exportMemoryMD() {
  if (!_currentMemorySection) { showToast('No section selected', 'error'); return; }
  const text = _memorySectionContent(_currentMemorySection);
  if (!text) { showToast('No content to export', 'error'); return; }
  const meta = _memorySectionMeta(_currentMemorySection);
  const md = '# ' + (t(meta.labelKey) || meta.key) + '\n\n' + text;
  const blob = new Blob([md], { type: 'text/markdown' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'memory-' + _currentMemorySection + '.md';
  a.click();
  URL.revokeObjectURL(a.href);
  showToast('Exported memory-' + _currentMemorySection + '.md');
}

function enrichMemoryWithAI() {
  showToast('AI enrichment - coming soon', 'info');
}

// ── Mail Panel Logic ──

function _mailApi(path, params={}) {
  const url = new URL(path, window.location.origin);
  Object.entries(params).forEach(([k,v])=>url.searchParams.append(k,v));
  return fetch(url).then(r=>{ if(!r.ok) throw new Error(r.statusText); return r.json(); });
}

async function loadMailPanel() {
  const inboxSelector = document.getElementById('mailInboxSelector');
  const mailList = document.getElementById('mailList');
  if (!inboxSelector || !mailList) return;
  inboxSelector.innerHTML = '<option value="" disabled selected>Lade...</option>';
  mailList.innerHTML = '<div class="mail-loading">Lade Mails...</div>';
  try {
    const data = await _mailApi('/api/mail/folders');
    const inboxes = data.inboxes || [];
    inboxSelector.innerHTML = inboxes.map(i=>`<option value="${i.id}">${i.label}</option>`).join('');
    if (inboxes.length>0) {
      _mailSwitchInbox(inboxes[0].id);
    }
  } catch(e) {
    console.warn('Mail load error', e);
    inboxSelector.innerHTML = '<option value="" disabled selected>Fehler beim Laden</option>';
    mailList.innerHTML = `<div class="mail-error">Fehler: ${e.message}</div>`;
  }
}

async function _mailSwitchInbox(inboxId) {
  const mailList = document.getElementById('mailList');
  if (!mailList) return;
  _currentMailInboxId = inboxId;
  mailList.innerHTML = '<div class="mail-loading">Lade Mails...</div>';
  try {
    const data = await _mailApi('/api/mail/inbox', {inbox_id: inboxId, limit: 20});
    const mails = data.mails || [];
    _mailRenderMails(mails);
  } catch(e) {
    console.warn('Mail inbox error', e);
    mailList.innerHTML = '<div class="mail-error">Fehler: ' + escHtml(e.message) + '</div>';
  }
}

function _mailRenderMails(mails) {
  const mailList = document.getElementById('mailList');
  if (!mailList) return;
  if (mails.length===0) {
    mailList.innerHTML = '<div class="mail-empty">Keine Mails.</div>';
    return;
  }
  mailList.innerHTML = mails.map((m,i)=>{
    const unread = !(m.flags || []).includes('\\Seen');
    const cls = unread ? 'mail-card mail-card-unread' : 'mail-card';
    return '<div class="' + cls + '" onclick="_mailViewThread(mails[' + i + '])">' +
      '<div class="mail-card-subject">' + escHtml(m.subject || '(kein Betreff)') + '</div>' +
      '<div class="mail-card-meta"><span>' + escHtml(m.from || '') + '</span><span>' + escHtml(m.date || '') + '</span></div>' +
      '<div class="mail-card-snippet">' + escHtml(m.snippet || '') + '</div>' +
    '</div>';
  }).join('');
}

function _mailOpenConfig() {
  const modal = document.createElement('div');
  modal.className = 'mail-config-modal';
  modal.innerHTML = '<div class="mail-config-modal-content"><h3>Mail-Konfiguration</h3><textarea style="width:100%;height:300px;font-family:monospace;font-size:12px;" class="mail-config-textarea"></textarea><div class="mail-config-actions"><button class="btn btn-primary mail-config-save">Speichern</button><button class="btn btn-secondary mail-config-cancel">Abbrechen</button></div></div>';
  document.body.appendChild(modal);
  const textarea = modal.querySelector('.mail-config-textarea');
  const saveBtn = modal.querySelector('.mail-config-save');
  const cancelBtn = modal.querySelector('.mail-config-cancel');
  const close = () => { document.body.removeChild(modal); };
  const fetchConfig = async () => {
    try {
      const res = await _mailApi('/api/mail/config');
      if (res.success) textarea.value = JSON.stringify(res.config, null, 2);
    } catch(err){
      showToast('Konfiguration laden fehlgeschlagen: '+err.message,'error');
    }
  };
  fetchConfig();
  const sendSave = async () => {
    try {
      const cfg = JSON.parse(textarea.value);
      const res = await fetch('/api/mail/config', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({config: cfg})}).then(r=>r.json());
      if (res.success){
        close();
        loadMailPanel();
      } else throw new Error(res.error||'unknown');
    } catch(err){
      showToast('Speichern fehlgeschlagen: '+err.message,'error');
    }
  };
  saveBtn.onclick = sendSave;
  cancelBtn.onclick = close;
  modal.addEventListener('keydown', e=>{ if(e.key==='Escape') close(); });
  modal.addEventListener('click', e=>{ if(e.target===modal) close(); });
}

let _currentMailInboxId = null;

function _mailCompose() {
  if(!_currentMailInboxId){ showToast('Kein Postfach ausgewählt','error'); return; }
  const modal=document.createElement('div'); modal.className='mail-compose-modal';
  modal.innerHTML='<div class="mail-compose-modal-content"><h3>Neue Mail</h3><label>An: <input type="email" class="mail-compose-to" /></label><label>Betreff: <input type="text" class="mail-compose-subject" /></label><label>Nachricht:<textarea class="mail-compose-body" style="width:100%;height:200px;"></textarea></label><div class="mail-compose-actions"><button class="btn btn-primary mail-compose-send">Senden</button><button class="btn btn-secondary mail-compose-cancel">Abbrechen</button></div></div>';
  document.body.appendChild(modal);
  const to=modal.querySelector('.mail-compose-to');
  const subj=modal.querySelector('.mail-compose-subject');
  const body=modal.querySelector('.mail-compose-body');
  const sendBtn=modal.querySelector('.mail-compose-send');
  const cancelBtn=modal.querySelector('.mail-compose-cancel');
  const close=()=>{document.body.removeChild(modal);};
  sendBtn.onclick=async()=>{ try{ const res=await fetch('/api/mail/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({inbox_id:_currentMailInboxId,to:to.value,subject:subj.value,body:body.value})}).then(r=>r.json()); if(res.success){showToast('Mail gesendet','success');close();loadMailPanel();} else throw new Error(res.error||'unknown'); }catch(err){showToast('Senden fehlgeschlagen: '+err.message,'error');} };
  cancelBtn.onclick=close;
  modal.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
  modal.addEventListener('click',e=>{if(e.target===modal)close();});
}

function _mailReply(mail){
  if(!mail){showToast('Keine Mail zum Antworten','error');return;}
  const modal=document.createElement('div'); modal.className='mail-compose-modal';
  modal.innerHTML='<div class="mail-compose-modal-content"><h3>Antworten</h3><label>An: <input type="email" class="mail-compose-to" value="'+escHtml(mail.from)+'" /></label><label>Betreff: <input type="text" class="mail-compose-subject" value="Re: '+escHtml(mail.subject)+'" /></label><label>Nachricht:<textarea class="mail-compose-body" style="width:100%;height:200px;"></textarea></label><div class="mail-compose-actions"><button class="btn btn-primary mail-compose-send">Senden</button><button class="btn btn-secondary mail-compose-cancel">Abbrechen</button></div></div>';
  document.body.appendChild(modal);
  const to=modal.querySelector('.mail-compose-to');
  const subj=modal.querySelector('.mail-compose-subject');
  const body=modal.querySelector('.mail-compose-body');
  const sendBtn=modal.querySelector('.mail-compose-send');
  const cancelBtn=modal.querySelector('.mail-compose-cancel');
  const close=()=>{document.body.removeChild(modal);};
  sendBtn.onclick=async()=>{ try{ const res=await fetch('/api/mail/send',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({inbox_id:_currentMailInboxId,to:to.value,subject:subj.value,body:body.value})}).then(r=>r.json()); if(res.success){showToast('Mail gesendet','success');close();loadMailPanel();} else throw new Error(res.error||'unknown'); }catch(err){showToast('Senden fehlgeschlagen: '+err.message,'error');} };
  cancelBtn.onclick=close;
  modal.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
  modal.addEventListener('click',e=>{if(e.target===modal)close();});
}

function _mailViewThread(mail){
  if(!mail){showToast('Keine Mail','error');return;}
  const modal=document.createElement('div'); modal.className='mail-thread-modal';
  modal.innerHTML='<div class="mail-thread-modal-content"><h3>'+escHtml(mail.subject||'(kein Betreff)')+'</h3><div class="mail-thread-body" style="white-space:pre-wrap;font-size:13px;padding:12px 0;">'+escHtml(mail.body||'')+'</div><div class="mail-thread-actions"><button class="btn btn-primary mail-thread-reply">Antworten</button><button class="btn btn-secondary mail-thread-close">Schließen</button></div></div>';
  document.body.appendChild(modal);
  const replyBtn=modal.querySelector('.mail-thread-reply');
  const closeBtn=modal.querySelector('.mail-thread-close');
  const close=()=>{document.body.removeChild(modal);};
  replyBtn.onclick=()=>{ close(); _mailReply(mail); };
  closeBtn.onclick=close;
  modal.addEventListener('keydown',e=>{if(e.key==='Escape')close();});
  modal.addEventListener('click',e=>{if(e.target===modal)close();});
}

function openMemorySearch() {
  const input = $('memToolsSearch');
  if (input) { input.focus(); input.select(); }
}

let _memToolsSearchInitialized = false;
function _initMemoryToolsSearch() {
  if (_memToolsSearchInitialized) return;
  const input = $('memToolsSearch');
  if (!input) return;
  _memToolsSearchInitialized = true;
  input.oninput = function() {
    const q = this.value.trim().toLowerCase();
    document.querySelectorAll('#memoryPanel .side-menu-item').forEach(function(el) {
      const key = el.dataset.memoryKey;
      if (!key) return;
      if (!q) {
        el.style.display = '';
        return;
      }
      const haystack = _memorySectionContent(key).toLowerCase();
      el.style.display = haystack.includes(q) ? '' : 'none';
    });
  };
}

// ── Appstore: Space activation toggle ──────────────────────────────────────

async function _appstoreToggleSpace(appKey, active) {
  try {
    const res = await fetch('/api/appstore/space-toggle', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({key: appKey, active: active})
    });
    const data = await res.json();
    if (data.success) {
      showToast(active ? 'App für diesen Space aktiviert' : 'App für diesen Space deaktiviert', 'success');
      // Reload appstore panel to reflect changes
      loadAppstorePanel();
      _appstoreSyncMailButtons();
    } else {
      showToast('Fehler: ' + (data.error || 'Unbekannter Fehler'), 'error');
    }
  } catch (err) {
    showToast('Fehler: ' + err.message, 'error');
  }
}

// ── Mail button visibility (per-space app activation) ─────────────────────

function _appstoreSyncMailButtons() {
  // Check if imap-mail is active for the current space
  const imapApp = _appstoreAppsCache.find(a => a.key === 'imap-mail');
  const isActive = imapApp && imapApp.space_active === true;

  const railBtn = document.getElementById('mailRailBtn');
  const sidebarBtn = document.getElementById('mailSidebarBtn');

  if (railBtn) railBtn.style.display = isActive ? '' : 'none';
  if (sidebarBtn) sidebarBtn.style.display = isActive ? '' : 'none';
}

// Hook into loadAppstorePanel to sync mail buttons after loading
const _origLoadAppstorePanel = loadAppstorePanel;
loadAppstorePanel = async function() {
  await _origLoadAppstorePanel.apply(this, arguments);
  _appstoreSyncMailButtons();
};



