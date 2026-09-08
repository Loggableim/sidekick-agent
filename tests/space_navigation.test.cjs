const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.env.SIDEKICK_TEST_SOURCE_ROOT || path.resolve(__dirname, '..');
const sessions = fs.readFileSync(path.join(root, 'web/static/sessions.js'), 'utf8');
const spaces = fs.readFileSync(path.join(root, 'web/static/spaces.js'), 'utf8');
function section(source, start, end) {
  const a = source.indexOf(start);
  const b = source.indexOf(end, a + start.length);
  assert.ok(a >= 0 && b > a, start);
  return source.slice(a, b);
}
function deferred() {
  let resolve, reject;
  const promise = new Promise((a, b) => { resolve = a; reject = b; });
  return {promise, resolve, reject};
}
function context(extra = {}) {
  const pane = {
    innerHTML: '', textContent: '', style: {}, children: [],
    setAttribute() {}, removeAttribute() {},
    replaceChildren() { this.children = []; },
    append(...children) { this.children.push(...children); },
  };
  const c = vm.createContext({
    window: {__sidekickSessionNavigationEpoch: 0, _sidekickSpaceSwitchRev: 0},
    S: {session: null, messages: [], toolCalls: []},
    _activeSpace: 'a', _spacesCache: [], _allSessions: [],
    _loadingSessionId: null, _activeSessionLoadAbortController: null,
    _SESSION_LOAD_TIMEOUT_MS: 5, _SESSION_MESSAGES_TIMEOUT_MS: 5, _INITIAL_MSG_LIMIT: 12,
    _yoloEnabled: false, _activeProject: null, NO_PROJECT_FILTER: 'none',
    AbortController, URLSearchParams, console: {warn() {}},
    _liveStreamRehydrateTimer: null,
    setTimeout, clearTimeout, Promise,
    $: () => pane,
    document: {getElementById: () => pane, createElement: () => ({})},
    localStorage: {setItem() {}, getItem() {return null;}, removeItem() {}},
    stopApprovalPolling() {}, hideApprovalCard() {}, _updateYoloPill() {},
    _saveComposerDraftNow() {}, _scheduleConversationPaneRecovery() {},
    _setActiveSessionUrl() {}, _conversationPaneShowsLoading() {return false;},
    _clearConversationLoadingState() {}, clearLiveToolCards() {},
    _showConversationLoadingState() {pane.innerHTML = 'Loading conversation...';},
    syncTopbar() {}, renderMessages() {}, _markSpaceSwitchTiming() {},
    _withSpaceTimeout: promise => promise,
    _syncSpaceProjectDirForActiveSession() {},
    _setSessionViewedCount() {}, updateSendBtn() {}, setStatus() {},
    setComposerStatus() {}, updateQueueBadge() {}, loadDir() {},
    ...extra,
  });
  vm.runInContext(
    section(sessions, 'function _markExplicitSessionNavigation(', 'const _SESSION_LOAD_TIMEOUT_MS') +
    section(spaces, 'function _spaceSwitchRev()', 'function _deferSpaceSelect(') +
    section(spaces, 'function _spaceSessionMatchesSlug(', 'function _spaceSlugFromLocation(') +
    section(spaces, 'function _spaceSessionStorageKey(', 'async function _continueSpaceSessionSelection(') +
    section(sessions, 'async function _sessionApi(', 'function _scheduleLiveStreamRehydrate('),
    c,
  );
  c.pane = pane;
  return c;
}
function addLoad(c) {
  vm.runInContext(section(sessions, 'async function loadSession(', '// ── Handoff hint logic'), c);
}
test('space switch cancels metadata and invalidates boot restoration', () => {
  const c = context();
  const controller = new AbortController();
  c._activeSessionLoadAbortController = controller;
  c._loadingSessionId = 'old';
  c._beginSpaceSwitch();
  assert.equal(controller.signal.aborted, true);
  assert.equal(c._loadingSessionId, null);
  assert.equal(c.window.__sidekickSessionNavigationEpoch, 1);
  assert.equal(c.window.__sidekickSkipBootSessionRestore, true);
});
test('cancelled same-ID load cannot restore its previous chat over a newer load', () => {
  const c = context();
  const old = new AbortController();
  const current = new AbortController();
  c._activeSessionLoadAbortController = current;
  c._loadingSessionId = 'a';
  c.S.session = {session_id: 'a'};
  c.S.messages = [{role: 'user', content: 'current'}];
  c._abortStaleSessionLoad('a', {session: {session_id: 'b'}, messages: [{role: 'user', content: 'stale'}]}, old);
  assert.equal(c.S.session.session_id, 'a');
  assert.equal(c.S.messages[0].content, 'current');
  assert.equal(c._loadingSessionId, 'a');
});
test('request deadline is reported as a timeout, not navigation cancellation', async () => {
  const c = context({api: (_url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
  })});
  await assert.rejects(c._sessionApi('/api/session', 5), {name: 'TimeoutError'});
});
test('external cancellation retains AbortError', async () => {
  const c = context({api: (_url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
  })});
  const controller = new AbortController();
  const promise = c._sessionApi('/api/session', 1000, controller.signal);
  controller.abort();
  await assert.rejects(promise, {name: 'AbortError'});
});
test('metadata timeout replaces the loading pane with an error', async () => {
  const c = context({api: (_url, options) => new Promise((resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), {name: 'AbortError'})));
  })});
  addLoad(c);
  await c.loadSession('a', {skipSidebarRender: true});
  assert.match(c.pane.innerHTML, /Failed to load session/);
  assert.equal(c._loadingSessionId, null);
});
test('late failed metadata request cannot replace a newer navigation', async () => {
  const pending = deferred();
  const c = context();
  addLoad(c);
  c._sessionApi = () => pending.promise;
  const old = c.loadSession('a', {skipSidebarRender: true});
  c._markExplicitSessionNavigation(true);
  c.S.session = {session_id: 'b'};
  c.pane.innerHTML = 'new chat';
  pending.reject(new Error('network failure'));
  await old;
  assert.equal(c.pane.innerHTML, 'new chat');
  assert.equal(c.S.session.session_id, 'b');
});
test('A to B to A rejects the first A transcript, even if fetch ignores abort', async () => {
  const pending = deferred();
  let applied = 0;
  const c = context({_applySessionMessagePayload() {applied++;}});
  vm.runInContext(section(sessions, 'async function _ensureMessagesLoaded(', 'function _applySessionMessagePayload('), c);
  c._sessionApi = () => pending.promise;
  c.S.session = {session_id: 'a'};
  const old = c._ensureMessagesLoaded('a');
  c._markExplicitSessionNavigation(true);
  c._markExplicitSessionNavigation(true);
  c.S.session = {session_id: 'a'};
  pending.resolve({session: {session_id: 'a', messages: [{role: 'user', content: 'old'}]}});
  await old;
  assert.equal(applied, 0);
});
test('current transcript is still applied normally', async () => {
  let applied = 0;
  const c = context({_applySessionMessagePayload() {applied++;}});
  vm.runInContext(section(sessions, 'async function _ensureMessagesLoaded(', 'function _applySessionMessagePayload('), c);
  c._sessionApi = async () => ({session: {session_id: 'a', messages: []}});
  c.S.session = {session_id: 'a'};
  await c._ensureMessagesLoaded('a');
  assert.equal(applied, 1);
});
test('late workspace sync never mutates another chat or reloads its directory', async () => {
  const pending = deferred();
  let dirs = 0;
  const c = context({api: () => pending.promise, loadDir() {dirs++;}});
  vm.runInContext(section(spaces, 'async function _syncSpaceProjectDirForActiveSession(', 'async function _loadSpaceConfigForSwitch('), c);
  c._spacesCache = [{slug: 'a', project_dir: '/a'}];
  c.S.session = {session_id: 'a1', workspace_slug: 'a'};
  const old = c._syncSpaceProjectDirForActiveSession('a');
  c.S.session = {session_id: 'a2', workspace_slug: 'a', workspace: '/new'};
  pending.resolve({session: {workspace: '/a'}});
  await old;
  assert.equal(c.S.session.workspace, '/new');
  assert.equal(dirs, 0);
});
test('space auto-selection respects a chat clicked while its list was loading', async () => {
  let loads = 0;
  const c = context({loadSession() {loads++;}});
  vm.runInContext(section(spaces, 'async function _continueSpaceSessionSelection(', 'async function _loadSpaceSessionsForSwitch('), c);
  c._markExplicitSessionNavigation(true);
  await c._continueSpaceSessionSelection('a', 0, [{session_id: 'first', workspace_slug: 'a'}], null, 0);
  assert.equal(loads, 0);
});
test('late new-chat response cannot replace an explicitly selected chat', async () => {
  const pending = deferred();
  const c = context({getActiveSpaceQuery: () => '?workspace=a'});
  vm.runInContext(section(sessions, 'async function newSession(', 'function _currentSessionIsEmptyIdle('), c);
  c._sessionApi = () => pending.promise;
  // Before the fix newSession used api directly.
  c.api = () => pending.promise;
  const old = c.newSession();
  c._markExplicitSessionNavigation(true);
  c.S.session = {session_id: 'selected'};
  pending.resolve({session: {session_id: 'created', messages: []}});
  await old;
  assert.equal(c.S.session.session_id, 'selected');
});
test('space failure offers retry and stale errors leave the new pane alone', () => {
  let retried;
  const c = context({selectSpace: (...args) => {retried = args;}});
  vm.runInContext(section(spaces, 'function _showSpaceSwitchError(', 'async function selectSpace('), c);
  c._showSpaceSwitchError('a', 0, new Error('offline'));
  assert.equal(c.pane.children[1].textContent, 'Retry');
  c.pane.children[1].onclick();
  assert.equal(retried[0], 'a');
  assert.equal(retried[1].retry, true);
  c._activeSpace = 'b';
  c.pane.replaceChildren();
  c._showSpaceSwitchError('a', 0, new Error('late'));
  assert.equal(c.pane.children.length, 0);
});

test('failed initial and fallback lists show retry instead of creating an empty chat', async () => {
  let creations = 0;
  const c = context({
    closeSpaceDropdowns() {}, _startSpaceSwitchTiming() {}, _locationHasSessionRoute: () => false,
    _syncActiveSpaceUrl() {}, _showSpaceSwitchLoading() {},
    _syncSpacesPanelActiveState() {}, _scheduleActiveSpaceScopedPanelRefresh() {},
    renderSpacesPanel() {},
    _loadSpaceConfigForSwitch: async () => null,
    _loadSpaceSessionsForSwitch: async () => {throw Error('offline');},
    renderSessionList: async () => {throw Error('still offline');},
    newSession: async () => {creations++;},
  });
  vm.runInContext(section(spaces, 'function _showSpaceSwitchError(', 'async function createSpace('), c);
  await c.selectSpace('b');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(creations, 0);
  assert.equal(c.pane.children[1].textContent, 'Retry');
});

test('returning to a Space restores its last viewed chat rather than its newest', async () => {
  const storage = new Map();
  let selected;
  const c = context({
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
    loadSession: async sid => {selected = sid;},
  });
  vm.runInContext(section(spaces, 'async function _continueSpaceSessionSelection(', 'async function _loadSpaceSessionsForSwitch('), c);
  c._rememberSpaceSession('a', {session_id: 'older', workspace_slug: 'a'});
  await c._continueSpaceSessionSelection('a', 0, [
    {session_id: 'newest', workspace_slug: 'a'},
    {session_id: 'older', workspace_slug: 'a'},
  ], null, 0);
  assert.equal(selected, 'older');
});
test('Space history is profile scoped and rejects missing or foreign sessions', () => {
  const storage = new Map();
  const c = context({
    localStorage: {getItem: key => storage.get(key), setItem: (key, value) => storage.set(key, value)},
  });
  const first = {session_id: 'first', workspace_slug: 'a'};
  const saved = {session_id: 'saved', workspace_slug: 'a'};
  c.S.activeProfile = 'alice';
  c._rememberSpaceSession('a', saved);
  assert.equal(c._preferredSpaceSession('a', [first, saved]).session_id, 'saved');
  assert.equal(c._preferredSpaceSession('a', [first]).session_id, 'first');
  c.S.activeProfile = 'bob';
  assert.equal(c._preferredSpaceSession('a', [first, saved]).session_id, 'first');
  assert.equal(c._preferredSpaceSession('a', [{session_id: 'foreign', workspace_slug: 'b'}]), null);
});


test('late live-stream metadata cannot attach after A to B to A navigation', async () => {
  const pending = deferred(); let timer, attached = 0;
  const c = context({setTimeout(fn) {timer = fn; return 1;}, attachLiveStream() {attached++;}});
  vm.runInContext(section(sessions, 'function _scheduleLiveStreamRehydrate(', '// ── Composer draft'), c);
  c._sessionApi = () => pending.promise;
  c.S.session = {session_id:'a'};
  c._scheduleLiveStreamRehydrate('a');
  const hydrate = timer();
  c._markExplicitSessionNavigation(true);
  c._markExplicitSessionNavigation(true);
  c.S.session = {session_id:'a'};
  pending.resolve({session:{session_id:'a',active_stream_id:'old'}});
  await hydrate;
  assert.equal(attached, 0);
  assert.equal(c.S.session.active_stream_id, undefined);
});

test('late completion frame cannot clear a newer conversation loading state', () => {
  let frame, cleared = 0;
  const c = context({sid:'a', requestAnimationFrame(fn) {frame=fn;},
    isCurrentNavigationEpoch: () => c.window.__sidekickSessionNavigationEpoch === 0,
    _clearConversationLoadingState() {cleared++;}});
  c.S.session = {session_id:'a'};
  const tail = section(sessions, '  // Defensive re-render:', '  // ── Cross-channel handoff hint');
  vm.runInContext(tail, c);
  c._markExplicitSessionNavigation(true);
  c.S.session = {session_id:'b'};
  frame();
  assert.equal(cleared, 0);
});

test('boot restore owns its load epoch but yields to subsequent user navigation', async () => {
  const boot = fs.readFileSync(path.join(root, 'web/static/boot.js'), 'utf8');
  const c = context();
  c.loadSession = async () => {c._markExplicitSessionNavigation(false); return {missingSession:true};};
  vm.runInContext(section(boot, '  let _bootRestoreEpoch=', '  if (urlSession && saved'), c);
  vm.runInContext(section(boot, '  function _bootLoadSession(', '  let _bootSavedSessionLoadPromise'), c);
  await c._bootLoadSession('missing');
  assert.equal(vm.runInContext('_bootRestoreCanceled()', c), false);
  c._markExplicitSessionNavigation(true);
  assert.equal(vm.runInContext('_bootRestoreCanceled()', c), true);
});

test('missing-session boot fallback respects navigation during Space config wait', async () => {
  const boot = fs.readFileSync(path.join(root, 'web/static/boot.js'), 'utf8');
  const config = deferred(); let created = 0, canceled = false;
  const c = context({_bootMissingSession:true,urlSession:'missing',saved:'missing',
    _spaceConfigReady:config.promise,_bootRestoreCanceled:()=>canceled,newSession:async()=>{created++;}});
  const block = section(boot, '      if (_bootMissingSession && urlSession && saved)', '      // If the restored session');
  const running = vm.runInContext('(async()=>{' + block + '})()', c);
  canceled = true; config.resolve();
  await assert.rejects(running, /restore canceled/);
  assert.equal(created, 0);
});

test('profile switch leaves even empty chats owned by their original profile', async () => {
  const panels = fs.readFileSync(path.join(root, 'web/static/panels.js'), 'utf8');
  const pending = deferred(); let calls = 0, redirected;
  const removed = [];
  const chip = {classList:{add(){},remove(){}},disabled:false,textContent:''};
  const c = context({URL, $:()=>chip,closeProfileDropdown(){},
    document:{baseURI:'http://localhost/sidekick/'},location:{href:'http://localhost/sidekick/session/empty',assign(url){redirected=url;}},
    _clearSessionRoutePath(){return '/sidekick/';},
    localStorage:{removeItem(key){removed.push(key);}},t:x=>x,showToast(){}});
  c.S.activeProfile='alice'; c.S.session={session_id:'empty',profile:'alice'};
  c._sessionApi = (url) => {assert.equal(url,'/api/profile/switch'); calls++; return pending.promise;};
  vm.runInContext(section(panels, 'let _profileSwitchPending', 'function openProfileCreate('), c);
  const switching = c.switchToProfile('bob');
  await c.switchToProfile('charlie');
  pending.resolve({active:'bob'}); await switching;
  assert.equal(calls, 1);
  assert.equal(c.S.session.profile, 'alice');
  assert.equal(redirected, 'http://localhost/sidekick/');
  assert.ok(removed.includes('sidekick-webui-session'));
  assert.equal(chip.disabled, false);
});
