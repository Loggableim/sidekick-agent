// â”€â”€ Spaces panel — workspace isolation for sessions, kanban, and config â”€â”€
//
// Provides:
//   _activeSpace     — current space slug (string), stored in localStorage
//   renderSpacesPanel() — renders the spaces management panel
//   selectSpace(slug)   — switch to a different space
//   createSpace(slug, name) — create a new space
//   deleteSpace(slug)      — delete a space
//   getActiveSpaceQuery()  — returns "?workspace=<slug>" or ""

let DEFAULT_SPACE_SLUG = 'nova';
window.DEFAULT_SPACE_SLUG = DEFAULT_SPACE_SLUG;
const LEGACY_DEFAULT_SPACE_SLUG = 'default';
function _isProtectedSpaceSlug(slug) {
  const s = String(slug || '').toLowerCase();
  return s === DEFAULT_SPACE_SLUG || s === LEGACY_DEFAULT_SPACE_SLUG;
}
function _shouldTrustUnscopedSessionsForSpace(slug) {
  const s = String(slug || '').toLowerCase();
  return s === DEFAULT_SPACE_SLUG || s === LEGACY_DEFAULT_SPACE_SLUG;
}
function _spaceSessionMatchesSlug(session, slug) {
  const target = String(slug || '').trim().toLowerCase();
  if (!session || !target) return false;
  const explicit = String(session.workspace_slug || session.space_slug || session.space || '').trim().toLowerCase();
  if (explicit) return explicit === target;
  return _shouldTrustUnscopedSessionsForSpace(target);
}
function _spaceSlugFromLocation() {
  try {
    const slug = new URLSearchParams(window.location.search || '').get('workspace');
    return String(slug || '').trim().toLowerCase();
  } catch (_) {
    return '';
  }
}
const _urlActiveSpace = _spaceSlugFromLocation();
let _activeSpace = _urlActiveSpace || localStorage.getItem('sidekick-active-workspace') || DEFAULT_SPACE_SLUG;
if (_urlActiveSpace) {
  try { localStorage.setItem('sidekick-active-workspace', _urlActiveSpace); } catch (_) {}
}
let _spacesCache = [];
window._hermesSpaceSwitchRev = Number(window._hermesSpaceSwitchRev || 0);
let _spacesPanelRenderRev = 0;

// â”€â”€ Workspace color palette â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
const SPACE_COLORS = [
  '#4FC3F7', '#FFB74D', '#CE93D8', '#81C784', '#4DD0E1',
  '#F06292', '#A1887F', '#AED581', '#FF8A65', '#4DB6AC',
  '#FFD54F', '#E57373', '#7986CB', '#90A4AE', '#BA68C8',
];

function spaceEsc(str) {
  return String(str).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function safeSpaceColor(color) {
  return /^#[0-9a-fA-F]{6}$/.test(String(color || '')) ? color : SPACE_COLORS[0];
}

function getActiveSpaceQuery() {
  return '?workspace=' + encodeURIComponent(_activeSpace);
}

function _withSpaceTimeout(promise, ms, label) {
  let timer = null;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error((label || 'space operation') + ' timed out')), ms);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) clearTimeout(timer);
  });
}

function _clearSessionRoutePath(pathname) {
  const path = String(pathname || '/');
  const marker = '/session/';
  const idx = path.indexOf(marker);
  if (idx < 0) return path || '/';
  const base = path.slice(0, idx) || '/';
  return base.endsWith('/') ? base : base + '/';
}

function _locationHasSessionRoute() {
  try {
    const url = new URL(window.location.href);
    return url.pathname.indexOf('/session/') >= 0 || url.searchParams.has('session');
  } catch (_) {
    return false;
  }
}

function _syncActiveSpaceUrl(slug, options = {}) {
  try {
    const url = new URL(window.location.href);
    url.searchParams.set('workspace', slug);
    url.searchParams.delete('session');
    if (options && options.clearSessionRoute) {
      url.pathname = _clearSessionRoutePath(url.pathname);
    }
    window.history.replaceState(window.history.state || {}, '', url.pathname + url.search + url.hash);
  } catch (_) {}
}

function _publishSpaceGlobals() {
  const descriptors = {
    _activeSpace: {
      configurable: true,
      get: () => _activeSpace,
      set: (value) => {
        const slug = String(value || '').trim().toLowerCase();
        if (slug) _activeSpace = slug;
      },
    },
    _spacesCache: {
      configurable: true,
      get: () => _spacesCache,
    },
  };
  for (const [name, descriptor] of Object.entries(descriptors)) {
    try { Object.defineProperty(window, name, descriptor); } catch (_) {}
  }
  Object.assign(window, {
    getActiveSpaceQuery,
    loadSpaces,
    selectSpace,
    _spaceSessionMatchesSlug,
    createSpace,
    deleteSpace,
    renderSpacesPanel,
    filterSpaces,
    isActiveSpaceLoadKey,
    _activeSpaceLoadKey,
    updateTitlebarSpace,
    updateSidebarSpaceSelector,
    closeSpaceDropdowns,
    toggleTitlebarSpaceDropdown,
    toggleSidebarSpaceDropdown,
    openSidebarSpaceSelector,
    showCreateSpaceDialog,
  });
}

function _spaceSwitchRev() {
  return Number(window._hermesSpaceSwitchRev || 0);
}

function _beginSpaceSwitch() {
  window._hermesSpaceSwitchRev = _spaceSwitchRev() + 1;
  return window._hermesSpaceSwitchRev;
}

function _isCurrentSpaceSwitch(rev, slug) {
  return _spaceSwitchRev() === rev && _activeSpace === slug;
}

function _activeSpaceLoadKey() {
  return `${_activeSpace}:${_spaceSwitchRev()}`;
}

function isActiveSpaceLoadKey(key) {
  return key === _activeSpaceLoadKey();
}

function _startSpaceSwitchTiming(slug, rev) {
  const record = { slug, rev, started_at: Date.now(), marks: [] };
  try { window._lastSpaceSwitchTiming = record; } catch (_) {}
  _markSpaceSwitchTiming(slug, rev, 'start');
  return record;
}

function _markSpaceSwitchTiming(slug, rev, name) {
  try {
    const record = window._lastSpaceSwitchTiming;
    if (!record || record.slug !== slug || record.rev !== rev) return;
    record.marks.push({ name, elapsed_ms: Date.now() - record.started_at });
    const root = document.documentElement;
    if (root) root.setAttribute('data-sidekick-space-switch-timing', JSON.stringify(record));
  } catch (_) {}
}

// â”€â”€ Load spaces from API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function loadSpaces() {
  try {
    const data = await _withSpaceTimeout(api('/api/spaces'), 10000, 'load spaces');
    _spacesCache = data.spaces || [];
    if (data.default_space) {
      DEFAULT_SPACE_SLUG = String(data.default_space || 'nova').toLowerCase() || 'nova';
      window.DEFAULT_SPACE_SLUG = DEFAULT_SPACE_SLUG;
    }
    if (_spacesCache.length && !_spacesCache.some(s => s && s.slug === _activeSpace)) {
      const preferred = _spacesCache.find(s => s && s.slug === DEFAULT_SPACE_SLUG) || _spacesCache[0];
      if (preferred && preferred.slug) {
        _activeSpace = preferred.slug;
        try { localStorage.setItem('sidekick-active-workspace', _activeSpace); } catch (_) {}
      }
    }
    updateTitlebarSpace();
    return _spacesCache;
  } catch (e) {
    console.warn('loadSpaces', e);
    return _spacesCache;
  }
}

// â”€â”€ Switch active space â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function _resetViewsForSpaceSwitch() {
  try { if (typeof _activeProject !== 'undefined') _activeProject = null; } catch (_) {}
  try { if (typeof exitSessionSelectMode === 'function') exitSessionSelectMode(); } catch (_) {}
  try { if (typeof _searchDebounceTimer !== 'undefined' && _searchDebounceTimer) clearTimeout(_searchDebounceTimer); } catch (_) {}
  try { if (typeof _contentSearchResults !== 'undefined') _contentSearchResults = []; } catch (_) {}
  try { if (typeof closeKanbanTaskDetail === 'function') closeKanbanTaskDetail(); } catch (_) {}
  try { if (typeof _kanbanStopPolling === 'function') _kanbanStopPolling(); } catch (_) {}
  try { if (typeof _kanbanLatestEventId !== 'undefined') _kanbanLatestEventId = 0; } catch (_) {}
  try { if (typeof _kanbanCurrentTaskId !== 'undefined') _kanbanCurrentTaskId = null; } catch (_) {}
  try { if (typeof _kanbanBoard !== 'undefined') _kanbanBoard = {columns: []}; } catch (_) {}
  try { if (typeof _memoryData !== 'undefined') _memoryData = null; } catch (_) {}
  try { if (typeof browserPrepareSessionSwitch === 'function') browserPrepareSessionSwitch(); } catch (_) {}
  const todoMain = document.getElementById('todoMainBoard');
  if (todoMain && typeof _renderTodosMainBoard === 'function') _renderTodosMainBoard([]);
}

function _showSessionListSpaceLoading() {
  const list = document.getElementById('sessionList');
  if (!list) return;
  list.innerHTML = '<div style="padding:14px 12px;color:var(--muted);font-size:12px;text-align:center;">Loading conversations...</div>';
  list.dataset.sessionVirtualTotal = '0';
  list.dataset.sessionVirtualFilter = '';
  list.dataset.sessionVirtualStart = '0';
  list.dataset.sessionVirtualEnd = '0';
  delete list.dataset.sessionVirtualActiveAnchor;
}

function _showSpaceSwitchLoading(slug) {
  _resetViewsForSpaceSwitch();
  updateWorkspaceNameBar();
  updateTitlebarSpace();
  _refreshSidebarSelector();
  _showSessionListSpaceLoading();
  try {
    if (typeof _activeSessionLoadAbortController !== 'undefined' && _activeSessionLoadAbortController) {
      _activeSessionLoadAbortController.abort();
    }
  } catch (_) {}
  if (typeof S !== 'undefined' && S) {
    S.session = null;
    S.messages = [];
    S.toolCalls = [];
    S.busy = false;
    S.activeStreamId = null;
    try { if (typeof clearLiveToolCards === 'function') clearLiveToolCards(); } catch (_) {}
    try {
      const inner = document.getElementById('msgInner');
      if (inner) {
        inner.innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:var(--text-muted);font-size:14px;padding:40px;text-align:center;">Loading space conversations...</div>';
      }
    } catch (_) {}
    try { if (typeof syncTopbar === 'function') syncTopbar(); } catch (_) {}
    try { if (typeof updateSendBtn === 'function') updateSendBtn(); } catch (_) {}
  }
}

async function _syncSpaceProjectDirForActiveSession(slug) {
  const spaceCfg = _spacesCache.find(s => s.slug === slug);
  if (!spaceCfg || !spaceCfg.project_dir || typeof loadDir !== 'function') return;
  const pdir = spaceCfg.project_dir.trim();
  if (!pdir || typeof S === 'undefined' || !S.session || !S.session.session_id) return;
  try {
    if (!S.session.workspace || S.session.workspace !== pdir) {
      const upd = await api('/api/session/update', {
        method: 'POST',
        body: JSON.stringify({
          session_id: S.session.session_id,
          workspace: pdir
        })
      });
      if (upd && upd.session) {
        S.session.workspace = upd.session.workspace;
      }
    }
  } catch (e) {
    console.warn('selectSpace: failed to sync workspace to project_dir:', e);
  }
  loadDir('.');
}

async function _loadSpaceConfigForSwitch(slug, switchRev, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => {
    try { controller.abort(); } catch (_) {}
  }, timeoutMs || 1200);
  try {
    _markSpaceSwitchTiming(slug, switchRev, 'config-load-started');
    const configResp = await api(`/api/space/config?slug=${encodeURIComponent(slug)}`, {
      signal: controller.signal,
      logError: false,
    });
    if (!_isCurrentSpaceSwitch(switchRev, slug)) return null;
    const config = configResp ? (configResp.config || null) : null;
    window._activeSpaceConfig = config;
    _markSpaceSwitchTiming(slug, switchRev, 'config-loaded');
    return config;
  } catch (_) {
    if (_isCurrentSpaceSwitch(switchRev, slug)) {
      window._activeSpaceConfig = null;
      _markSpaceSwitchTiming(slug, switchRev, 'config-unavailable');
    }
    return null;
  } finally {
    clearTimeout(timer);
  }
}

async function _continueSpaceSessionSelection(slug, switchRev, sessionsInSpace, configPromise) {
  try {
    if (!_isCurrentSpaceSwitch(switchRev, slug)) return;
    _markSpaceSwitchTiming(slug, switchRev, 'session-selection-start');
    const currentSession = (typeof S !== 'undefined' && S && S.session) ? S.session : null;
    const currentSid = currentSession ? currentSession.session_id : null;
    const activeSessionInTargetSpace = _spaceSessionMatchesSlug(currentSession, slug);
    const hasCurrentInSpace = !!(currentSid && activeSessionInTargetSpace && (sessionsInSpace || []).some(s => s && s.session_id === currentSid));
    if (!currentSid || !hasCurrentInSpace) {
      if ((sessionsInSpace || []).length && typeof loadSession === 'function') {
        await _withSpaceTimeout(Promise.resolve(loadSession(sessionsInSpace[0].session_id, {expectedSpace: slug})), 12000, 'load session');
        if (!_isCurrentSpaceSwitch(switchRev, slug)) return;
        _markSpaceSwitchTiming(slug, switchRev, 'session-loaded');
      } else if (typeof newSession === 'function') {
        if (configPromise) await Promise.resolve(configPromise).catch(() => null);
        if (!_isCurrentSpaceSwitch(switchRev, slug)) return;
        await _withSpaceTimeout(Promise.resolve(newSession()), 12000, 'create session');
        if (!_isCurrentSpaceSwitch(switchRev, slug)) return;
        _markSpaceSwitchTiming(slug, switchRev, 'session-created');
        if (typeof renderSessionList === 'function') await _withSpaceTimeout(Promise.resolve(renderSessionList()), 8000, 'render session list');
        if (!_isCurrentSpaceSwitch(switchRev, slug)) return;
        _markSpaceSwitchTiming(slug, switchRev, 'sessions-rerendered-after-create');
      }
    }
    if (!_isCurrentSpaceSwitch(switchRev, slug)) return;
    void _syncSpaceProjectDirForActiveSession(slug);
    _markSpaceSwitchTiming(slug, switchRev, 'background-finished');
  } catch (e) {
    if (_isCurrentSpaceSwitch(switchRev, slug)) console.warn('continue space session selection:', e);
  }
}

function _refreshActiveSpaceScopedPanel() {
  const panel = (typeof _currentPanel !== 'undefined') ? _currentPanel : 'chat';
  if (panel === 'kanban' && typeof loadKanban === 'function') {
    loadKanban();
  } else if (panel === 'todos' && typeof loadTodos === 'function') {
    loadTodos();
  } else if (panel === 'tasks' && typeof loadCrons === 'function') {
    loadCrons(true);
  } else if (panel === 'memory' && typeof loadMemory === 'function') {
    loadMemory();
  } else if (panel === 'agents' && typeof loadAgentsDashboard === 'function') {
    loadAgentsDashboard();
  } else if (panel === 'gmail' && typeof loadGmailPanel === 'function') {
    loadGmailPanel();
  } else if (panel === 'browser' && typeof browserResearchPanelActivated === 'function') {
    browserResearchPanelActivated();
  } else if (panel === 'workspaces') {
    renderSpacesPanel();
  }
}

function closeSpaceDropdowns() {
  for (const id of ['titlebarSpaceDropdown', 'sidebarSpaceDropdown', 'spaceSelectorDropdown']) {
    const dd = document.getElementById(id);
    if (dd) {
      dd.hidden = true;
      dd.innerHTML = '';
    }
  }
  const titlebarBtn = document.getElementById('titlebarSpaceBtn');
  const sidebarBtn = document.getElementById('sidebarSpaceBtn');
  if (titlebarBtn) titlebarBtn.setAttribute('aria-expanded', 'false');
  if (sidebarBtn) sidebarBtn.setAttribute('aria-expanded', 'false');
}

async function selectSpace(slug) {
  if (!slug) return;
  closeSpaceDropdowns();
  const startedInSpacesPanel = (typeof _currentPanel !== 'undefined' && _currentPanel === 'workspaces');
  const activeSession = (typeof S !== 'undefined' && S && S.session) ? S.session : null;
  const activeSessionInTargetSpace = _spaceSessionMatchesSlug(activeSession, slug);
  const hasActiveSessionForSameSpace = !!(
    slug === _activeSpace &&
    activeSession &&
    activeSession.session_id &&
    activeSessionInTargetSpace
  );
  if (slug === _activeSpace && hasActiveSessionForSameSpace) {
    updateTitlebarSpace();
    renderSpacesPanel();
    return;
  }
  try {
    if (typeof S !== 'undefined' && S.session && S.session.session_id) {
      if (typeof _saveComposerDraftNow === 'function') {
        const ta = document.getElementById('msg');
        _saveComposerDraftNow(S.session.session_id, ta ? ta.value : '', []);
      }
    }
    const previousSpace = _activeSpace;
    _activeSpace = slug;
    const switchRev = _beginSpaceSwitch();
    _startSpaceSwitchTiming(slug, switchRev);
    localStorage.setItem('sidekick-active-workspace', slug);
    const shouldClearSessionRoute = !!(
      !activeSessionInTargetSpace
      && previousSpace !== slug
      && (
        (activeSession && activeSession.session_id)
        || _locationHasSessionRoute()
      )
    );
    if (shouldClearSessionRoute) {
      try { localStorage.removeItem('sidekick-webui-session'); } catch (_) {}
    }
    _syncActiveSpaceUrl(slug, {clearSessionRoute: shouldClearSessionRoute});
    _showSpaceSwitchLoading(slug);
    _syncSpacesPanelActiveState(slug);
    _markSpaceSwitchTiming(slug, switchRev, 'space-visible');
    window._activeSpaceConfig = null;
    const spaceConfigPromise = _loadSpaceConfigForSwitch(slug, switchRev, 1200);
    // Clear stale session cache before reloading — prevents race conditions
    // with polling timers (startStreamingPoll, _gatewayPollTimer) that may
    // fire between the API call and the response, showing old-space sessions.
    if (typeof _allSessions !== 'undefined') {
      try { _allSessions = []; } catch (_) {}
    }
    let sessionsInSpace = [];
    if (typeof renderSessionList === 'function') {
      await _withSpaceTimeout(Promise.resolve(renderSessionList()), 8000, 'render session list');
      if (!_isCurrentSpaceSwitch(switchRev, slug)) return;
      _markSpaceSwitchTiming(slug, switchRev, 'session-list-rendered');
      try {
        if (typeof _allSessions !== 'undefined' && Array.isArray(_allSessions)) {
          // Client-side safety filter: only keep sessions matching the active space.
          // Backend also filters, but this catches edge cases from stale index data.
          sessionsInSpace = _allSessions.filter(s => _spaceSessionMatchesSlug(s, slug));
        }
      } catch (_) {}
    }
    void _continueSpaceSessionSelection(slug, switchRev, sessionsInSpace, spaceConfigPromise);
    _markSpaceSwitchTiming(slug, switchRev, 'background-session-load-started');
    _refreshActiveSpaceScopedPanel();
    // Refresh spaces panel UI if currently visible
    const spacesPanel = document.getElementById('panelWorkspaces');
    if (startedInSpacesPanel || (typeof _currentPanel !== 'undefined' && _currentPanel === 'workspaces') || (spacesPanel && spacesPanel.style.display !== 'none')) {
      renderSpacesPanel();
    }
    // Re-evaluate goal banner for the new space
    if(typeof _renderGoalBanner==='function')_renderGoalBanner();
  } catch (e) {
    console.warn('selectSpace error:', e);
  }
}

// â”€â”€ Create a new space â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function createSpace(slug, name, color, emoji, options = {}) {
  if (!slug) return { error: 'slug is required' };
  if (!/^[a-z0-9][a-z0-9_-]*$/.test(slug)) {
    return { error: 'Invalid slug. Use a-z, 0-9, _, -' };
  }
  try {
    const body = { slug, name: name || slug };
    if (color) body.color = color;
    if (emoji) body.emoji = emoji;
    if (options.novaInstance) body.nova_instance = true;
    if (options.novaCharacter) body.nova_character = options.novaCharacter;
    const result = await api('/api/space/create', {
      method: 'POST',
      body: JSON.stringify(body),
    });
    if (result && result.space) {
      _spacesCache.push(result.space);
      await selectSpace(slug);
      // Refresh the sidebar space selector so the new space appears immediately
      _refreshSidebarSelector();
      return { ok: true };
    }
    return { error: 'Create failed' };
  } catch (e) {
    return { error: e.message || 'Create failed' };
  }
}

// â”€â”€ Delete a space â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function deleteSpace(slug) {
  if (!slug || _isProtectedSpaceSlug(slug)) return { error: 'Cannot delete default space' };
  try {
    const wasActive = _activeSpace === slug;
    if (wasActive) {
      _activeSpace = DEFAULT_SPACE_SLUG;
      try { localStorage.setItem('sidekick-active-workspace', DEFAULT_SPACE_SLUG); } catch (_) {}
    }
    await api('/api/space/delete', {
      method: 'POST',
      body: JSON.stringify({ slug }),
      headers: {'X-Hermes-Workspace': DEFAULT_SPACE_SLUG},
    });
    _spacesCache = _spacesCache.filter(s => s.slug !== slug);
    if (wasActive) {
      await selectSpace(DEFAULT_SPACE_SLUG);
    }
    _refreshSidebarSelector();
    return { ok: true };
  } catch (e) {
    return { error: e.message || 'Delete failed' };
  }
}

// â”€â”€ Space config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

async function loadSpaceConfig(slug) {
  try {
    const data = await api('/api/space/config?slug=' + encodeURIComponent(slug));
    return data.config || {};
  } catch (e) {
    return {};
  }
}

async function saveSpaceConfig(slug, config) {
  try {
    await api('/api/space/config', {
      method: 'POST',
      body: JSON.stringify({ slug, ...config }),
    });
    return { ok: true };
  } catch (e) {
    return { error: e.message };
  }
}

// â”€â”€ Render spaces panel â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function renderSpacesPanel() {
  const container = document.getElementById('workspacesPanel');
  if (!container) return;
  loadSpaces().then(spaces => {
    const query = (document.getElementById('workspaceSearch') || {}).value || '';
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      spaces = spaces.filter(ws => String(ws.name || ws.slug || '').toLowerCase().includes(q) || String(ws.slug || '').toLowerCase().includes(q));
    }
    // Sort: active space first, then alphabetically
    spaces.sort((a, b) => {
      if (a.slug === _activeSpace) return -1;
      if (b.slug === _activeSpace) return 1;
      return a.name.localeCompare(b.name);
    });
    container.innerHTML = '';

    // â”€â”€ Space list â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    const list = document.createElement('div');
    list.className = 'spaces-list';
    for (const ws of spaces) {
      const color = safeSpaceColor(ws.color);
      const item = document.createElement('div');
      item.className = 'space-item' + (ws.slug === _activeSpace ? ' active' : '');
      item.style.setProperty('--space-color', color);
      item.dataset.slug = ws.slug;
      item.dataset.spaceSlug = ws.slug;
      item.onclick = () => selectSpace(ws.slug);

      // Emoji statt/in Ergänzung zum farbigen Punkt
      const icon = document.createElement('span');
      icon.className = 'space-item-icon';
      icon.textContent = ws.emoji || '📁';
      icon.style.cssText = 'font-size:16px;flex-shrink:0;width:20px;text-align:center;';
      item.appendChild(icon);

      // Color strip for active space
      const strip = document.createElement('div');
      strip.className = 'color-strip';
      strip.style.cssText = 'width:3px;flex-shrink:0;border-radius:2px;align-self:stretch;margin:-8px 6px -8px -10px;background:' + color + ';opacity:' + (ws.slug === _activeSpace ? '1' : '0') + ';';
      item.appendChild(strip);

      const info = document.createElement('div');
      info.className = 'space-item-info';

      const nameEl = document.createElement('div');
      nameEl.className = 'space-item-name';
      nameEl.style.color = color;
      nameEl.textContent = ws.name || ws.slug;
      info.appendChild(nameEl);

      const meta = document.createElement('div');
      meta.className = 'space-item-meta';
      const modelStr = (ws.model && ws.model.default) ? ws.model.default : '';
      meta.textContent = modelStr ? modelStr + (ws.model && ws.model.provider ? ' · ' + ws.model.provider : '') : ws.slug;
      info.appendChild(meta);

      item.appendChild(info);

      // Session count badge
      if (typeof ws.session_count === 'number' && ws.session_count > 0) {
        const badge = document.createElement('span');
        badge.className = 'space-item-badge';
        badge.textContent = ws.session_count;
        item.appendChild(badge);
      }

      list.appendChild(item);
    }
    container.appendChild(list);

    // â”€â”€ Action buttons â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    const actions = document.createElement('div');
    actions.className = 'spaces-actions';

    const createBtn = document.createElement('button');
    createBtn.className = 'spaces-btn';
    createBtn.textContent = '+ New Space';
    createBtn.onclick = () => showCreateSpaceDialog();
    actions.appendChild(createBtn);

    container.appendChild(actions);

    // â”€â”€ Active space config section â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    const active = spaces.find(s => s.slug === _activeSpace);
    if (active) {
      const configSection = document.createElement('div');
      configSection.className = 'space-config-section';

      const header = document.createElement('div');
      header.className = 'space-config-header';
      header.style.cssText = 'border-left:3px solid ' + (active.color || '#4FC3F7') + ';padding-left:10px;';
      header.textContent = 'Space: ' + (active.name || active.slug);
      configSection.appendChild(header);

      // Model display
      const modelRow = document.createElement('div');
      modelRow.className = 'space-config-row';
      modelRow.innerHTML = '<span>Model</span><span>' +
        ((active.model && active.model.default) || '—') + '</span>';
      configSection.appendChild(modelRow);

      // Provider display
      const provRow = document.createElement('div');
      provRow.className = 'space-config-row';
      provRow.innerHTML = '<span>Provider</span><span>' +
        ((active.model && active.model.provider) || '—') + '</span>';
      configSection.appendChild(provRow);

      // Description
      if (active.description) {
        const descRow = document.createElement('div');
        descRow.className = 'space-config-row';
        descRow.innerHTML = '<span>Description</span><span>' +
          active.description + '</span>';
        configSection.appendChild(descRow);
      }

      // Project directory
      const projRow = document.createElement('div');
      projRow.className = 'space-config-row';
      projRow.style.flexWrap = 'wrap';
      projRow.innerHTML = '<span>Project Directory</span><span style="font-size:12px;word-break:break-all;max-width:260px;">' +
        (active.project_dir || '—') + '</span>';
      configSection.appendChild(projRow);

      // Edit project_dir inline
      if (active.slug !== LEGACY_DEFAULT_SPACE_SLUG) {
        const editRow = document.createElement('div');
        editRow.className = 'space-config-row';
        editRow.style.cssText = 'display:flex;gap:6px;align-items:center;margin-top:4px;';
        const editInput = document.createElement('input');
        editInput.type = 'text';
        editInput.placeholder = 'Set project directory path...';
        editInput.value = active.project_dir || '';
        editInput.style.cssText = 'flex:1;padding:6px 8px;border:1px solid var(--border,#363636);border-radius:4px;background:var(--bg-input,#2a2a3e);color:var(--text,#e0e0e0);font-size:12px;';
        const saveBtn = document.createElement('button');
        saveBtn.textContent = 'Update';
        saveBtn.className = 'spaces-btn';
        saveBtn.style.cssText = 'padding:4px 10px;font-size:11px;';
        saveBtn.onclick = () => {
          const newDir = editInput.value.trim();
          const slug = active.slug;
          api('/api/space/config', {
            method: 'POST',
            body: JSON.stringify({ slug, project_dir: newDir }),
          }).then(() => {
            renderSpacesPanel();
          }).catch(e => console.warn('Failed to update project_dir:', e));
        };
        editRow.appendChild(editInput);
        editRow.appendChild(saveBtn);
        configSection.appendChild(editRow);
      }

      // Delete button (only for non-default spaces)
      if (!_isProtectedSpaceSlug(active.slug)) {
        const delBtn = document.createElement('button');
        delBtn.className = 'spaces-btn spaces-btn-danger';
        delBtn.textContent = 'Delete Space';
        delBtn.onclick = () => {
          if (confirm('Delete space "' + (active.name || active.slug) + '" and all its data?')) {
            deleteSpace(active.slug);
          }
        };
        configSection.appendChild(delBtn);
      }

      container.appendChild(configSection);
    }
  });
}

// Improved Spaces management UI. This intentionally overrides the legacy
// renderer above: the legacy version only filled the left sidebar and left the
// main view empty.
function _spaceEmoji(ws) {
  const emoji = String((ws && ws.emoji) || '').trim();
  if (!emoji || /ð|Ã|Â|â/.test(emoji)) return '\u{1F4C1}';
  return emoji;
}

function _spaceBySlug(slug) {
  return (_spacesCache || []).find(s => s && s.slug === slug) || null;
}

function _setSpaceDetailActions(space) {
  ['btnActivateWorkspaceDetail', 'btnEditWorkspaceDetail', 'btnDeleteWorkspaceDetail', 'btnCancelWorkspaceDetail', 'btnSaveWorkspaceDetail'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.style.display = 'none';
  });
  const del = document.getElementById('btnDeleteWorkspaceDetail');
  if (del && space && !_isProtectedSpaceSlug(space.slug)) {
    del.style.display = '';
    del.onclick = () => deleteActiveSpaceFromDetail();
  }
}

function renderSpaceDetail(space) {
  const title = document.getElementById('workspaceDetailTitle');
  const body = document.getElementById('workspaceDetailBody');
  const empty = document.getElementById('workspaceDetailEmpty');
  if (!title || !body || !empty) return;
  if (!space) {
    title.textContent = '';
    body.style.display = 'none';
    empty.style.display = '';
    _setSpaceDetailActions(null);
    return;
  }
  const color = safeSpaceColor(space.color);
  const model = (space.model && space.model.default) || 'Not set';
  const provider = (space.model && space.model.provider) || 'Not set';
  const desc = String(space.description || '');
  title.innerHTML = '<span class="space-title-dot" style="background:' + color + '"></span><span style="color:' + color + '">' + spaceEsc(_spaceEmoji(space) + ' ' + (space.name || space.slug)) + '</span>';
  empty.style.display = 'none';
  body.style.display = '';
  body.innerHTML = `
    <div class="space-detail-shell" style="--space-color:${spaceEsc(color)}">
      <section class="space-hero-card">
        <div class="space-hero-mark">${spaceEsc(_spaceEmoji(space))}</div>
        <div class="space-hero-copy">
          <div class="space-hero-kicker">Active isolated space</div>
          <h2>${spaceEsc(space.name || space.slug)}</h2>
          <p>${spaceEsc(desc || 'Own chats, Kanban, memory, agents and settings. Switching spaces reloads isolated data automatically.')}</p>
        </div>
        <button class="space-primary-action" type="button" onclick="switchPanel('chat')">Open chat</button>
      </section>
      <section class="space-stat-grid">
        <div class="space-stat"><span>Chats</span><strong>${Number(space.session_count || 0)}</strong></div>
        <div class="space-stat"><span>Agents</span><strong>${Array.isArray(space.agents) ? space.agents.length : 0}</strong></div>
        <div class="space-stat"><span>Model</span><strong>${spaceEsc(model)}</strong></div>
        <div class="space-stat"><span>Provider</span><strong>${spaceEsc(provider)}</strong></div>
      </section>
      <section class="space-settings-card">
        <div class="space-section-title">Space settings</div>
        <label class="space-field"><span>Emoji</span><input id="spaceDetailEmoji" maxlength="4" value="${spaceEsc(_spaceEmoji(space))}"></label>
        <label class="space-field"><span>Color</span><input id="spaceDetailColor" type="color" value="${spaceEsc(color)}"></label>
        <label class="space-field space-field-wide"><span>Description</span><textarea id="spaceDetailDescription" rows="3" placeholder="What belongs in this space?">${spaceEsc(desc)}</textarea></label>
        <label class="space-field space-field-wide"><span>Project directory</span><input id="spaceDetailProjectDir" value="${spaceEsc(space.project_dir || '')}" placeholder="Optional absolute project path"></label>
        <div class="space-detail-actions">
          <button class="space-secondary-action" type="button" onclick="renderSpacesPanel()">Refresh</button>
          <button class="space-primary-action" type="button" onclick="saveActiveSpaceDetails()">Save changes</button>
          ${!_isProtectedSpaceSlug(space.slug) ? '<button class="space-danger-action" type="button" onclick="deleteActiveSpaceFromDetail()">Delete space</button>' : ''}
        </div>
      </section>
      <section class="space-isolation-card">
        <div class="space-section-title">Isolation contract</div>
        <div class="space-isolation-list">
          <span>Chats reload per space</span>
          <span>Kanban uses this space DB</span>
          <span>Memory reloads per space</span>
          <span>Tasks and agents refresh on switch</span>
        </div>
      </section>
    </div>`;
  _setSpaceDetailActions(space);
}

function _syncSpacesPanelActiveState(slug) {
  const container = document.getElementById('workspacesPanel');
  if (!container) return;
  container.querySelectorAll('.space-item').forEach(item => {
    const isActive = item.dataset.slug === slug;
    item.classList.toggle('active', isActive);
    let activeBadge = item.querySelector('.space-item-active');
    if (isActive && !activeBadge) {
      activeBadge = document.createElement('span');
      activeBadge.className = 'space-item-active';
      activeBadge.textContent = 'Active';
      item.appendChild(activeBadge);
    } else if (!isActive && activeBadge) {
      activeBadge.remove();
    }
  });
  const space = _spaceBySlug(slug);
  if (space) renderSpaceDetail(space);
}

async function saveActiveSpaceDetails() {
  const space = _spaceBySlug(_activeSpace);
  if (!space) return;
  const emoji = (document.getElementById('spaceDetailEmoji') || {}).value || '';
  const color = (document.getElementById('spaceDetailColor') || {}).value || '';
  const description = (document.getElementById('spaceDetailDescription') || {}).value || '';
  const project_dir = (document.getElementById('spaceDetailProjectDir') || {}).value || '';
  try {
    await api('/api/space/config', {
      method: 'POST',
      body: JSON.stringify({ slug: space.slug, emoji: emoji.trim(), color, description: description.trim(), project_dir: project_dir.trim() }),
    });
    await loadSpaces();
    renderSpacesPanel();
    updateTitlebarSpace();
    if (typeof showToast === 'function') showToast('Space saved');
  } catch (e) {
    if (typeof showToast === 'function') showToast('Space save failed: ' + (e.message || e));
  }
}

async function deleteActiveSpaceFromDetail() {
  const space = _spaceBySlug(_activeSpace);
  if (!space || _isProtectedSpaceSlug(space.slug)) return;
  if (!confirm('Delete space "' + (space.name || space.slug) + '" and all isolated data?')) return;
  const result = await deleteSpace(space.slug);
  if (result && result.error) {
    if (typeof showToast === 'function') showToast('Space delete failed: ' + result.error, 3500);
    return;
  }
  if (typeof showToast === 'function') showToast('Space deleted', 1800);
  await loadSpaces();
  renderSpacesPanel();
  if (typeof updateTitlebarSpace === 'function') updateTitlebarSpace();
}

function renderSpacesPanel() {
  const container = document.getElementById('workspacesPanel');
  if (!container) return;
  const renderRev = ++_spacesPanelRenderRev;
  container.classList.add('spaces-sidebar-list');
  container.innerHTML = '<div style="padding:12px;color:var(--muted);font-size:12px">Loading spaces...</div>';
  loadSpaces().then(spaces => {
    if (renderRev !== _spacesPanelRenderRev) return;
    spaces = Array.isArray(spaces) ? spaces.slice() : [];
    const query = (document.getElementById('workspaceSearch') || {}).value || '';
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      spaces = spaces.filter(ws => String(ws.name || ws.slug || '').toLowerCase().includes(q) || String(ws.slug || '').toLowerCase().includes(q));
    }
    spaces.sort((a, b) => {
      if (a.slug === _activeSpace) return -1;
      if (b.slug === _activeSpace) return 1;
      return String(a.name || a.slug).localeCompare(String(b.name || b.slug));
    });
    if (renderRev !== _spacesPanelRenderRev) return;
    container.innerHTML = '';
    container.classList.add('spaces-sidebar-list');

    const list = document.createElement('div');
    list.className = 'spaces-list';
    if (!spaces.length) list.innerHTML = '<div class="spaces-empty-list">No spaces found.</div>';
    for (const ws of spaces) {
      const color = safeSpaceColor(ws.color);
      const item = document.createElement('button');
      item.type = 'button';
      item.className = 'space-item' + (ws.slug === _activeSpace ? ' active' : '');
      item.style.setProperty('--space-color', color);
      item.dataset.slug = ws.slug;
      item.dataset.spaceSlug = ws.slug;
      item.onclick = () => selectSpace(ws.slug);
      const modelStr = (ws.model && ws.model.default) ? ws.model.default : 'No model';
      item.innerHTML = `
        <span class="space-item-swatch" aria-hidden="true"></span>
        <span class="space-item-icon">${spaceEsc(_spaceEmoji(ws))}</span>
        <span class="space-item-info">
          <span class="space-item-name">${spaceEsc(ws.name || ws.slug)}</span>
          <span class="space-item-meta">${spaceEsc(ws.slug)} · ${Number(ws.session_count || 0)} chats · ${spaceEsc(modelStr)}</span>
        </span>
        ${ws.slug === _activeSpace ? '<span class="space-item-active">Active</span>' : ''}`;
      list.appendChild(item);
    }
    if (renderRev !== _spacesPanelRenderRev) return;
    container.appendChild(list);

    const active = _spaceBySlug(_activeSpace) || spaces.find(s => s && s.slug === _activeSpace) || spaces[0] || null;
    if (active && active.slug !== _activeSpace) {
      _activeSpace = active.slug;
      localStorage.setItem('sidekick-active-workspace', active.slug);
    }
    if (renderRev !== _spacesPanelRenderRev) return;
    renderSpaceDetail(active);
  }).catch(e => {
    if (renderRev !== _spacesPanelRenderRev) return;
    container.innerHTML = `<div style="padding:12px;color:var(--danger,#ff6b6b);font-size:12px">Failed to load spaces: ${spaceEsc(e && e.message ? e.message : e)}</div>`;
  });
}

// â”€â”€ Create space dialog â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function _novaCharOption(c) {
  return `<option value="${spaceEsc(c.name)}">${spaceEsc(c.name)}${c.description ? ' — '+spaceEsc(c.description) : ''}</option>`;
}

async function showCreateSpaceDialog() {
  // Fetch available Nova characters from the onboarding status endpoint
  let novaCharacters = [{name:'nova',description:'Canonical Nova consciousness baseline.'}];
  try {
    const status = await api('/api/onboarding/status');
    if (status && status.nova && Array.isArray(status.nova.characters) && status.nova.characters.length) {
      novaCharacters = status.nova.characters;
    }
  } catch (_) {}

  // Modal overlay for creating a new space with name + project directory
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.cssText = 'position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,0.5);z-index:9999;display:flex;align-items:center;justify-content:center;';

  const dialog = document.createElement('div');
  dialog.className = 'modal-content';
  dialog.style.cssText = 'background:var(--bg-panel,#1e1e2e);border:1px solid var(--border,#363636);border-radius:12px;padding:28px;min-width:420px;max-width:520px;box-shadow:0 8px 32px rgba(0,0,0,0.4);';

  const charOptions = novaCharacters.map(c => _novaCharOption(c)).join('');

  dialog.innerHTML = `
    <h3 style="margin:0 0 20px;font-size:16px;font-weight:600;color:var(--text,#e0e0e0);">Create New Space</h3>
    <div style="margin-bottom:14px;">
      <label style="display:block;margin-bottom:4px;font-size:12px;color:var(--text-dim,#888);">Space Name</label>
      <input id="newSpaceName" type="text" placeholder="e.g. My Project" style="width:100%;padding:8px 10px;border:1px solid var(--border,#363636);border-radius:6px;background:var(--bg-input,#2a2a3e);color:var(--text,#e0e0e0);font-size:14px;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:18px;">
      <label style="display:block;margin-bottom:4px;font-size:12px;color:var(--text-dim,#888);">Project Directory (optional)</label>
      <div style="display:flex;gap:6px;">
        <input id="newSpaceProjectDir" type="text" placeholder="e.g. C:\\\\Projects\\\\my-project or ~/code/my-project" style="flex:1;padding:8px 10px;border:1px solid var(--border,#363636);border-radius:6px;background:var(--bg-input,#2a2a3e);color:var(--text,#e0e0e0);font-size:13px;">
      </div>
      <div style="margin-top:4px;font-size:11px;color:var(--text-dim,#888);">The agent will be restricted to this directory when working in this Space.</div>
    </div>
    <div style="margin-bottom:18px;">
      <label style="display:block;margin-bottom:8px;font-size:12px;color:var(--text-dim,#888);">Space Color</label>
      <div id="newSpaceColors" style="display:flex;gap:6px;flex-wrap:wrap;">
        ${SPACE_COLORS.map(c => `<button class="space-color-swatch" data-color="${c}" style="width:28px;height:28px;border-radius:50%;border:2px solid transparent;background:${c};cursor:pointer;transition:transform .12s, border-color .12s;" title="${c}"></button>`).join('')}
      </div>
    </div>
    <div style="margin-bottom:18px;">
      <label style="display:block;margin-bottom:4px;font-size:12px;color:var(--text-dim,#888);">Emoji (optional)</label>
      <input id="newSpaceEmoji" type="text" placeholder="📁" maxlength="4" style="width:60px;padding:8px 10px;border:1px solid var(--border,#363636);border-radius:6px;background:var(--bg-input,#2a2a3e);color:var(--text,#e0e0e0);font-size:18px;text-align:center;box-sizing:border-box;">
    </div>
    <div style="margin-bottom:18px;padding:12px;border:1px solid var(--border,#363636);border-radius:8px;background:var(--bg-soft,#202232);">
      <label style="display:flex;align-items:center;gap:8px;font-size:13px;color:var(--text,#e0e0e0);margin-bottom:10px;">
        <input id="newSpaceNovaInstance" type="checkbox">
        <span>Create dedicated Nova instance for this space</span>
      </label>
      <label style="display:block;margin-bottom:4px;font-size:12px;color:var(--text-dim,#888);">Nova character</label>
      <select id="newSpaceNovaCharacterSelect" style="width:100%;padding:8px 10px;border:1px solid var(--border,#363636);border-radius:6px;background:var(--bg-input,#2a2a3e);color:var(--text,#e0e0e0);font-size:13px;box-sizing:border-box;">${charOptions}</select>
      <div style="margin-top:6px;font-size:11px;color:var(--text-dim,#888);">Spaces with their own Nova instance communicate via the pingpong format.</div>
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end;">
      <button id="newSpaceCancel" style="padding:8px 16px;border:1px solid var(--border,#363636);border-radius:6px;background:transparent;color:var(--text,#e0e0e0);cursor:pointer;font-size:13px;">Cancel</button>
      <button id="newSpaceCreate" style="padding:8px 16px;border:none;border-radius:6px;background:var(--accent,#7c5cfc);color:#fff;cursor:pointer;font-size:13px;font-weight:500;">Create Space</button>
    </div>
  `;

  overlay.appendChild(dialog);
  document.body.appendChild(overlay);

  const nameInput = document.getElementById('newSpaceName');
  const dirInput = document.getElementById('newSpaceProjectDir');
  const cancelBtn = document.getElementById('newSpaceCancel');
  const createBtn = document.getElementById('newSpaceCreate');
  const colorsContainer = document.getElementById('newSpaceColors');

  let selectedColor = SPACE_COLORS[0];

  // Wire up color swatches
  if (colorsContainer) {
    colorsContainer.querySelectorAll('.space-color-swatch').forEach(btn => {
      btn.onclick = () => {
        colorsContainer.querySelectorAll('.space-color-swatch').forEach(b => b.style.borderColor = 'transparent');
        btn.style.borderColor = '#fff';
        btn.style.transform = 'scale(1.2)';
        selectedColor = btn.dataset.color;
      };
    });
    // Select the first one by default
    const first = colorsContainer.querySelector('.space-color-swatch');
    if (first) {
      first.style.borderColor = '#fff';
      first.style.transform = 'scale(1.2)';
    }
  }

  nameInput.focus();

  function close() { overlay.remove(); }

  cancelBtn.onclick = close;

  overlay.onclick = (e) => { if (e.target === overlay) close(); };

  function doCreate() {
    const name = nameInput.value.trim();
    const projectDir = dirInput.value.trim();
    const emoji = document.getElementById('newSpaceEmoji')?.value.trim() || '';
    const novaInstance = !!document.getElementById('newSpaceNovaInstance')?.checked;
    const novaCharacter = document.getElementById('newSpaceNovaCharacterSelect')?.value.trim() || 'nova';
    if (!name) { nameInput.focus(); nameInput.style.borderColor = '#f55'; return; }
    const slug = name.toLowerCase().replace(/[^a-z0-9_-]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '') || 'project';
    nameInput.style.borderColor = '';
    createBtn.disabled = true;
    createBtn.textContent = 'Creating...';
    createSpace(slug, name, selectedColor, emoji, { novaInstance, novaCharacter }).then(result => {
      if (result.error) {
        alert('Error: ' + result.error);
        createBtn.disabled = false;
        createBtn.textContent = 'Create Space';
        return;
      }
      // If project_dir was provided, save it
      if (projectDir) {
        api('/api/space/config', {
          method: 'POST',
          body: JSON.stringify({ slug, project_dir: projectDir }),
        }).catch(e => console.warn('Failed to save project_dir:', e));
      }
      renderSpacesPanel();
      close();
    });
  }

  nameInput.onkeydown = (e) => { if (e.key === 'Enter') doCreate(); };
  createBtn.onclick = doCreate;
}

// â”€â”€ Space selector dropdown (for sidebar header) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function renderSpaceSelector(containerEl) {
  // Called from the sidebar to render a compact space selector
  loadSpaces().then(spaces => {
    const active = spaces.find(s => s.slug === _activeSpace) || spaces[0];
    containerEl.innerHTML = '';
    const selector = document.createElement('div');
    selector.className = 'space-selector';

    const current = document.createElement('div');
    current.className = 'space-selector-current';
    // Show a colored dot + name
    const emojiSpan = document.createElement('span');
    emojiSpan.textContent = (active && active.emoji) || '📁';
    emojiSpan.style.cssText = 'font-size:16px;flex-shrink:0;width:20px;text-align:center;';
    const label = document.createElement('span');
    label.textContent = (active && active.name) || _activeSpace || 'Space';
    current.innerHTML = '';
    current.appendChild(emojiSpan);
    current.appendChild(label);
    current.onclick = (e) => {
      e.stopPropagation();
      toggleSpaceDropdown(selector, spaces);
    };
    selector.appendChild(current);
    containerEl.appendChild(selector);
  });
}

let _spaceDropdownOpen = false;

function toggleSpaceDropdown(selectorEl, spaces) {
  const existing = selectorEl.querySelector('.space-selector-dropdown');
  if (existing) {
    existing.remove();
    _spaceDropdownOpen = false;
    return;
  }
  _spaceDropdownOpen = true;
  const dropdown = document.createElement('div');
  dropdown.className = 'space-selector-dropdown';

  for (const ws of spaces) {
    const item = document.createElement('div');
    item.className = 'space-selector-item' + (ws.slug === _activeSpace ? ' active' : '');
    // Emoji + name
    const wsEmoji = document.createElement('span');
    wsEmoji.textContent = ws.emoji || '📁';
    wsEmoji.style.cssText = 'font-size:16px;flex-shrink:0;width:20px;text-align:center;';
    const wsLabel = document.createElement('span');
    wsLabel.textContent = ws.name || ws.slug;
    item.appendChild(wsEmoji);
    item.appendChild(wsLabel);
    item.onclick = () => {
      selectSpace(ws.slug);
      dropdown.remove();
      _spaceDropdownOpen = false;
    };
    dropdown.appendChild(item);
  }

  // Add a "Manage Spaces" link
  const manage = document.createElement('div');
  manage.className = 'space-selector-item space-selector-manage';
  manage.textContent = '\u2699 Manage Spaces';
  manage.onclick = () => {
    dropdown.remove();
    _spaceDropdownOpen = false;
    if (typeof switchPanel === 'function') {
      switchPanel('workspaces');
    }
  };
  dropdown.appendChild(manage);

  selectorEl.appendChild(dropdown);

  // Close on outside click
  const closeHandler = (e) => {
    if (!selectorEl.contains(e.target)) {
      const dd = selectorEl.querySelector('.space-selector-dropdown');
      if (dd) dd.remove();
      _spaceDropdownOpen = false;
      document.removeEventListener('click', closeHandler);
    }
  };
  setTimeout(() => document.addEventListener('click', closeHandler), 0);
}

// â”€â”€ Hook into session list API calls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

// Patch the api() call for /api/sessions to include workspace query param.
// We do this by wrapping the fetchSessionList-like functions.

// â”€â”€ Update workspace name bar in sidebar â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function updateWorkspaceNameBar() {
  // Find workspace data in cache
  const ws = _spacesCache.find(s => s.slug === _activeSpace);
  const name = ws ? (ws.name || ws.slug) : _activeSpace;
  const color = ws ? (ws.color || '#4FC3F7') : '#4FC3F7';
  const sidebarName = document.getElementById('sidebarSpaceName');
  const sidebarBtn = document.getElementById('sidebarSpaceBtn');
  if (sidebarName) sidebarName.textContent = name;
  if (sidebarBtn) {
    sidebarBtn.style.setProperty('--space-color', color);
    sidebarBtn.dataset.color = color;
    sidebarBtn.setAttribute('title', `Switch space (${name})`);
  }
}

// â”€â”€ Refresh the sidebar space selector â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function _refreshSidebarSelector() {
  updateSidebarSpaceSelector();
}

// â”€â”€ Title bar space dropdown â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function updateTitlebarSpace() {
  const emojiEl = document.getElementById('titlebarSpaceEmoji');
  const nameEl = document.getElementById('titlebarSpaceName');
  if (!emojiEl || !nameEl) return;
  const ws = _spacesCache.find(s => s.slug === _activeSpace);
  emojiEl.textContent = ws ? _spaceEmoji(ws) : '📁';
  nameEl.textContent = ws ? (ws.name || ws.slug) : _activeSpace;
  const color = safeSpaceColor(ws && ws.color);
  const wrap = document.getElementById('titlebarSpace');
  const btn = document.getElementById('titlebarSpaceBtn');
  if (wrap) wrap.style.setProperty('--space-color', color);
  if (btn) btn.style.setProperty('--space-color', color);
  nameEl.style.color = color;
  if (btn) btn.setAttribute('title', `Switch space (${ws ? (ws.name || ws.slug) : _activeSpace})`);
}

function _positionSpaceDropdown(dd, anchor) {
  if (!dd) return;
  const margin = 8;
  const rect = anchor && typeof anchor.getBoundingClientRect === 'function'
    ? anchor.getBoundingClientRect()
    : { left: margin, top: margin, bottom: margin, width: 230 };
  const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1280;
  const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 720;
  const width = Math.min(Math.max(dd.offsetWidth || 230, 220), Math.max(220, viewportWidth - margin * 2));
  let left = rect.left;
  if (left + width > viewportWidth - margin) left = viewportWidth - margin - width;
  left = Math.max(margin, left);
  let top = rect.bottom + 6;
  let maxHeight = viewportHeight - top - margin;
  if (maxHeight < 160) {
    const fallbackHeight = Math.min(320, viewportHeight - margin * 2);
    top = Math.max(margin, rect.top - fallbackHeight - 6);
    maxHeight = viewportHeight - top - margin;
  }
  dd.style.position = 'fixed';
  dd.style.left = left + 'px';
  dd.style.right = 'auto';
  dd.style.top = Math.max(margin, top) + 'px';
  dd.style.width = width + 'px';
  dd.style.maxHeight = Math.max(160, maxHeight) + 'px';
  dd.style.overflowY = 'auto';
}

function _renderSpaceDropdownItems(dd, spaces) {
  dd.innerHTML = '';
  dd.setAttribute('role', 'menu');
  for (const ws of spaces) {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'titlebar-space-dd-item';
    if (ws.slug === _activeSpace) item.classList.add('active');
    const itemColor = safeSpaceColor(ws.color);
    item.style.setProperty('--item-color', itemColor);
    item.dataset.spaceSlug = ws.slug;
    item.setAttribute('role', 'menuitem');
    item.setAttribute('aria-current', ws.slug === _activeSpace ? 'true' : 'false');
    item.innerHTML = `<span class="titlebar-space-dd-swatch"></span><span class="tdd-emoji">${spaceEsc(_spaceEmoji(ws))}</span><span class="tdd-name" style="color:${itemColor}">${spaceEsc(ws.name || ws.slug)}</span>`;
    item.onclick = (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      closeSpaceDropdowns();
      const runSelect = () => selectSpace(ws.slug);
      if (typeof requestAnimationFrame === 'function') requestAnimationFrame(runSelect);
      else setTimeout(runSelect, 0);
    };
    dd.appendChild(item);
  }
  const sep = document.createElement('div');
  sep.className = 'titlebar-space-dd-sep';
  sep.setAttribute('role', 'separator');
  dd.appendChild(sep);
  const newItem = document.createElement('button');
  newItem.type = 'button';
  newItem.className = 'titlebar-space-dd-item titlebar-space-dd-new';
  newItem.dataset.action = 'new-space';
  newItem.setAttribute('role', 'menuitem');
  newItem.innerHTML = '<span style="opacity:0.7">+</span><span>New space...</span>';
  newItem.onclick = (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    closeSpaceDropdowns();
    if (typeof switchPanel === 'function') switchPanel('workspaces');
    setTimeout(() => {
      if (typeof showCreateSpaceDialog === 'function') showCreateSpaceDialog();
    }, 100);
  };
  dd.appendChild(newItem);
}

function _showSpaceDropdownLoading(dd, btn) {
  dd.innerHTML = '<div class="titlebar-space-dd-item" style="opacity:.75;cursor:default">Loading spaces...</div>';
  dd.hidden = false;
  if (btn) btn.setAttribute('aria-expanded', 'true');
  _positionSpaceDropdown(dd, btn);
}

function _showSpaceDropdownError(dd, message) {
  dd.innerHTML = `<div class="titlebar-space-dd-item" style="color:var(--danger,#ff6b6b);cursor:default">${spaceEsc(message || 'Failed to load spaces')}</div>`;
}

function _openSpaceDropdown(dd, btn, className) {
  if (className) dd.className = className;
  _bindSpaceDropdownSelection(dd);
  const cachedSpaces = Array.isArray(_spacesCache) ? _spacesCache.filter(Boolean) : [];
  if (cachedSpaces.length) {
    dd.hidden = false;
    if (btn) btn.setAttribute('aria-expanded', 'true');
    _renderSpaceDropdownItems(dd, cachedSpaces);
    _positionSpaceDropdown(dd, btn);
    _installSpaceDropdownCloser(dd, btn);
  } else {
    _showSpaceDropdownLoading(dd, btn);
  }
  const refresh = () => {
    loadSpaces().then(spaces => {
      if (dd.hidden) return;
      if (className) dd.className = className;
      _renderSpaceDropdownItems(dd, spaces);
      _positionSpaceDropdown(dd, btn);
      if (!cachedSpaces.length) _installSpaceDropdownCloser(dd, btn);
    }).catch(e => {
      if (!cachedSpaces.length) _showSpaceDropdownError(dd, e && e.message);
    });
  };
  if (cachedSpaces.length) setTimeout(refresh, 0);
  else refresh();
}

function _bindSpaceDropdownSelection(dd) {
  if (!dd || dd.dataset.boundSpaceSelection === '1') return;
  dd.dataset.boundSpaceSelection = '1';
  dd.addEventListener('click', (ev) => {
    const item = ev.target && ev.target.closest ? ev.target.closest('[data-space-slug]') : null;
    if (!item || !dd.contains(item)) return;
    const slug = String(item.dataset.spaceSlug || '').trim();
    if (!slug) return;
    ev.preventDefault();
    ev.stopPropagation();
    closeSpaceDropdowns();
    const runSelect = () => selectSpace(slug);
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(runSelect);
    else setTimeout(runSelect, 0);
  });
}

function _installSpaceDropdownCloser(dd, btn) {
  const closer = (e) => {
    if (!dd.parentElement || !dd.parentElement.contains(e.target)) {
      dd.hidden = true;
      dd.innerHTML = '';
      if (btn) btn.setAttribute('aria-expanded', 'false');
      document.removeEventListener('click', closer);
    }
  };
  setTimeout(() => document.addEventListener('click', closer), 0);
}

function toggleTitlebarSpaceDropdown() {
  const dd = document.getElementById('titlebarSpaceDropdown');
  const btn = document.getElementById('titlebarSpaceBtn');
  if (!dd) return;
  if (!dd.hidden) {
    dd.hidden = true;
    dd.innerHTML = '';
    if (btn) btn.setAttribute('aria-expanded', 'false');
    return;
  }
  _openSpaceDropdown(dd, btn);
}

function _bindTitlebarSpaceButton() {
  const btn = document.getElementById('titlebarSpaceBtn');
  const dd = document.getElementById('titlebarSpaceDropdown');
  if (!btn || btn.dataset.boundSpaceDropdown === '1') return;
  btn.dataset.boundSpaceDropdown = '1';
  btn.onclick = null;
  if (dd) dd.hidden = true;
  btn.setAttribute('aria-haspopup', 'menu');
  btn.setAttribute('aria-expanded', 'false');
  btn.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    toggleTitlebarSpaceDropdown();
  });
}

// ─── Sidebar Space Selector ───
function updateSidebarSpaceSelector() {
  const emojiEl = document.getElementById('sidebarSpaceEmoji');
  const nameEl = document.getElementById('sidebarSpaceName');
  if (!emojiEl || !nameEl) return;
  
  const ws = _spacesCache.find(s => s.slug === _activeSpace);
  emojiEl.textContent = ws ? _spaceEmoji(ws) : '📁';
  nameEl.textContent = ws ? (ws.name || ws.slug) : _activeSpace;
  const btn = document.getElementById('sidebarSpaceBtn');
  const wrap = btn?.parentElement;
  const color = safeSpaceColor(ws && ws.color);
  if (wrap) wrap.style.setProperty('--space-color', color);
  if (btn) btn.style.setProperty('--space-color', color);
  nameEl.style.color = color;
  if (btn) btn.setAttribute('title', `Switch space (${ws ? (ws.name || ws.slug) : _activeSpace})`);
}

function toggleSidebarSpaceDropdown() {
  const dd = document.getElementById('sidebarSpaceDropdown');
  const btn = document.getElementById('sidebarSpaceBtn');
  if (!dd) return;
  if (!dd.hidden) {
    dd.hidden = true;
    dd.innerHTML = '';
    if (btn) btn.setAttribute('aria-expanded', 'false');
    return;
  }
  _openSpaceDropdown(dd, btn, 'sidebar-space-dropdown');
}

function openSidebarSpaceSelector() {
  toggleSidebarSpaceDropdown();
}

function _bindSidebarSpaceButton() {
  const btn = document.getElementById('sidebarSpaceBtn');
  const dd = document.getElementById('sidebarSpaceDropdown');
  if (!btn || btn.dataset.boundSideSpaceDropdown === '1') return;
  btn.dataset.boundSideSpaceDropdown = '1';
  btn.onclick = null;
  if (dd) dd.hidden = true;
  btn.setAttribute('aria-haspopup', 'menu');
  btn.setAttribute('aria-expanded', 'false');
  btn.addEventListener('click', (ev) => {
    ev.preventDefault();
    ev.stopPropagation();
    toggleSidebarSpaceDropdown();
  });
}

const _originalToggleTitlebarSpaceDropdown = toggleTitlebarSpaceDropdown;
toggleTitlebarSpaceDropdown = function() {
  _originalToggleTitlebarSpaceDropdown();
  requestAnimationFrame(() => {
    const dd = document.getElementById('titlebarSpaceDropdown');
    const btn = document.getElementById('titlebarSpaceBtn');
    if (dd && !dd.hidden) _positionSpaceDropdown(dd, btn);
  });
};

const _originalToggleSidebarSpaceDropdown = toggleSidebarSpaceDropdown;
toggleSidebarSpaceDropdown = function() {
  _originalToggleSidebarSpaceDropdown();
  requestAnimationFrame(() => {
    const dd = document.getElementById('sidebarSpaceDropdown');
    const btn = document.getElementById('sidebarSpaceBtn');
    if (dd && !dd.hidden) _positionSpaceDropdown(dd, btn);
  });
};

// Update both selectors when space changes
const originalUpdateTitlebarSpace = updateTitlebarSpace;
updateTitlebarSpace = function() {
  originalUpdateTitlebarSpace();
  updateSidebarSpaceSelector();
};
function filterSpaces() {
  renderSpacesPanel();
}
_publishSpaceGlobals();

// â”€â”€ Init on load â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
console.log('[spaces] loaded. Active:', _activeSpace);

// â”€â”€ Sidebar-nav expand/collapse â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
// Default: expanded (labels visible). Collapsed = icons only.

function getSidebarNavState() {
  return localStorage.getItem('sidekick-webui-rail-expanded') === '1' ? 'expanded' : 'collapsed';
}

function setSidebarNavState(state) {
  const expand = state === 'expanded';
  localStorage.setItem('sidekick-webui-rail-expanded', expand ? '1' : '0');
  try { document.documentElement.removeAttribute('data-sidebar-nav'); } catch (_) {}
  const layout = document.querySelector('.layout');
  if (layout) layout.classList.toggle('rail-expanded', expand);
  if (typeof _syncSidebarAria === 'function') {
    try { _syncSidebarAria(); } catch (_) {}
  }
}

function initSidebarNav() {
  try { document.documentElement.removeAttribute('data-sidebar-nav'); } catch (_) {}
}

function toggleSidebarNavLabels() {
  const current = getSidebarNavState();
  const next = current === 'expanded' ? 'collapsed' : 'expanded';
  setSidebarNavState(next);
}

// Override/suppress the mobile-only sidebar-nav hide on desktop
(function() {
  // Run after DOM ready
  function _overrideSidebarCSS() {
    try { document.documentElement.removeAttribute('data-sidebar-nav'); } catch (_) {}
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', _overrideSidebarCSS);
  } else {
    _overrideSidebarCSS();
  }

  // Initialize sidebar-nav state
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initSidebarNav);
  } else {
    initSidebarNav();
  }
})();

// Initialize the space selector in the sidebar once the DOM is ready
async function _initSpaceSelector() {
  _publishSpaceGlobals();
  _bindTitlebarSpaceButton();
  _bindSidebarSpaceButton();
  await loadSpaces();
  updateWorkspaceNameBar();
  updateTitlebarSpace();
  _publishSpaceGlobals();
}

// Defer init — scripts load with 'defer', wait for DOM + dependent functions
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _initSpaceSelector);
} else {
  _initSpaceSelector();
}
