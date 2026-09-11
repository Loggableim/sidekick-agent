const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const root = process.env.SIDEKICK_TEST_SOURCE_ROOT || path.resolve(__dirname, '..');
const sessions = fs.readFileSync(path.join(root, 'web/static/sessions.js'), 'utf8');
const spaces = fs.readFileSync(path.join(root, 'web/static/spaces.js'), 'utf8');
const commands = fs.readFileSync(path.join(root, 'web/static/commands.js'), 'utf8');
const workspace = fs.readFileSync(path.join(root, 'web/static/workspace.js'), 'utf8');
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

test('profile activation uses the server when this tab has a stale active profile', async () => {
  const panels = fs.readFileSync(path.join(root, 'web/static/panels.js'), 'utf8');
  let calls = 0, redirected;
  const chip = {classList:{add(){},remove(){}},disabled:false,textContent:''};
  const label = {textContent:'default'};
  const c = context({
    URL,
    $: id => id === 'profileChip' ? chip : id === 'profileChipLabel' ? label : null,
    closeProfileDropdown() {},
    document:{baseURI:'http://localhost/sidekick/'},
    location:{href:'http://localhost/sidekick/session/current',assign(url){redirected=url;}},
    _clearSessionRoutePath(){return '/sidekick/';},
    localStorage:{removeItem(){}}, t:x=>x, showToast(){},
  });
  c.S.activeProfile = 'default';
  c.S.session = {session_id:'current',profile:'alice'};
  c._sessionApi = async (url) => {assert.equal(url, '/api/profile/switch'); calls++; return {active:'alice'};};
  vm.runInContext(section(panels, 'let _profileSwitchPending', 'function openProfileCreate('), c);
  await c.switchToProfile('alice');
  assert.equal(calls, 1);
  assert.equal(redirected, 'http://localhost/sidekick/');
  assert.equal(chip.disabled, false);
});

test('failed profile switch recovers a cancelled conversation through the current profile root', async () => {
  const panels = fs.readFileSync(path.join(root, 'web/static/panels.js'), 'utf8');
  let redirected; const toasts = [];
  const chip = {classList:{add(){},remove(){}},disabled:false,textContent:''};
  const label = {textContent:'alice'};
  const c = context({
    URL,
    $: id => id === 'profileChip' ? chip : id === 'profileChipLabel' ? label : null,
    closeProfileDropdown() {},
    document:{baseURI:'http://localhost/sidekick/'},
    location:{href:'http://localhost/sidekick/session/loading',assign(url){redirected=url;}},
    _clearSessionRoutePath(){return '/sidekick/';},
    localStorage:{removeItem(){}},
    t:x=>x,
    showToast(message){toasts.push(message);},
  });
  const controller = new AbortController();
  c._activeSessionLoadAbortController = controller;
  c._loadingSessionId = 'loading';
  c.S.activeProfile = 'alice';
  c.S.session = {session_id:'loading',profile:'alice'};
  c._sessionApi = async () => {throw new Error('offline');};
  vm.runInContext(section(panels, 'let _profileSwitchPending', 'function openProfileCreate('), c);
  await c.switchToProfile('bob');
  assert.equal(controller.signal.aborted, true);
  assert.equal(redirected, 'http://localhost/sidekick/');
  assert.equal(label.textContent, 'alice');
  assert.equal(toasts.length, 1);
  assert.equal(chip.disabled, false);
});

test('malformed session metadata replaces the loading pane with a recoverable error', async () => {
  const c = context();
  addLoad(c);
  c._sessionApi = async () => ({});
  await c.loadSession('broken', {skipSidebarRender: true});
  assert.match(c.pane.innerHTML, /Failed to load session/);
  assert.equal(c._loadingSessionId, null);
  assert.equal(c._activeSessionLoadAbortController, null);
});

test('late boot Space config cannot overwrite a newer Space config', async () => {
  const boot = fs.readFileSync(path.join(root, 'web/static/boot.js'), 'utf8');
  const pending = deferred();
  const c = context({api: () => pending.promise});
  vm.runInContext(section(boot, 'async function _loadActiveSpaceConfig()', 'function _bootTimeout'), c);
  c._activeSpace = 'a';
  const staleLoad = c._loadActiveSpaceConfig();
  c._activeSpace = 'b';
  c.window._sidekickSpaceSwitchRev = 1;
  c.window._activeSpaceConfig = {project_dir: '/b'};
  pending.resolve({config: {project_dir: '/a'}});
  await staleLoad;
  assert.deepEqual(c.window._activeSpaceConfig, {project_dir: '/b'});
});

test('late older-message page cannot append after A to B to A navigation', async () => {
  const pending = deferred();
  const c = context({
    msgContent: message => message.content || '',
    _currentMessageRenderWindowSize: () => 12,
    MESSAGE_RENDER_WINDOW_DEFAULT: 12,
    requestAnimationFrame: fn => fn(),
  });
  vm.runInContext(section(sessions, 'let _loadingOlder = false;', 'let _allSessions = []'), c);
  c._sessionApi = () => pending.promise;
  c.S.session = {session_id: 'a'};
  c.S.messages = [{role: 'assistant', content: 'old tail'}];
  vm.runInContext('_messagesTruncated=true; _oldestIdx=5;', c);
  const stalePage = c._loadOlderMessages();

  c._bumpMessagesGeneration();
  c._invalidateOlderMessagesLoad();
  c.S.session = {session_id: 'b'};
  c.S.messages = [{role: 'assistant', content: 'b tail'}];
  c._bumpMessagesGeneration();
  c._invalidateOlderMessagesLoad();
  c.S.session = {session_id: 'a'};
  c.S.messages = [{role: 'assistant', content: 'fresh a tail'}];
  vm.runInContext('_messagesTruncated=true; _oldestIdx=5;', c);

  pending.resolve({session: {
    messages: [{role: 'user', content: 'stale page'}],
    _messages_truncated: false,
    _messages_offset: 0,
  }});
  await stalePage;
  assert.deepEqual(c.S.messages, [{role: 'assistant', content: 'fresh a tail'}]);
});

test('a stale older-message request cannot unlock a newer request', async () => {
  const first = deferred();
  const second = deferred();
  let request = 0;
  const c = context({
    msgContent: message => message.content || '',
    _currentMessageRenderWindowSize: () => 12,
    MESSAGE_RENDER_WINDOW_DEFAULT: 12,
  });
  vm.runInContext(section(sessions, 'let _loadingOlder = false;', 'let _allSessions = []'), c);
  c._sessionApi = () => (++request === 1 ? first.promise : second.promise);
  c.S.session = {session_id: 'a'};
  c.S.messages = [{role: 'assistant', content: 'a tail'}];
  vm.runInContext('_messagesTruncated=true; _oldestIdx=5;', c);
  const stale = c._loadOlderMessages();

  c._bumpMessagesGeneration();
  c._invalidateOlderMessagesLoad();
  c.S.session = {session_id: 'b'};
  c.S.messages = [{role: 'assistant', content: 'b tail'}];
  vm.runInContext('_messagesTruncated=true; _oldestIdx=5;', c);
  const current = c._loadOlderMessages();
  assert.equal(vm.runInContext('_loadingOlder', c), true);

  first.resolve({session: {messages: []}});
  await stale;
  assert.equal(vm.runInContext('_loadingOlder', c), true);
  second.resolve({session: {messages: []}});
  await current;
  assert.equal(vm.runInContext('_loadingOlder', c), false);
});

test('late workspace switch cannot overwrite an A to B to A navigation', async () => {
  const panels = fs.readFileSync(path.join(root, 'web/static/panels.js'), 'utf8');
  const pending = deferred();
  let dirs = 0;
  const c = context({
    api: () => pending.promise,
    closeWsDropdown() {}, syncTopbar() {}, loadDir() {dirs++;},
    showToast() {}, t: value => value, getWorkspaceFriendlyName: value => value,
  });
  vm.runInContext(section(panels, 'async function switchToWorkspace(path,name){', '// ── Profile panel'), c);
  c.S.session = {session_id: 'a', workspace: '/a', model: 'm'};
  const switching = c.switchToWorkspace('/new', 'New');
  c._markExplicitSessionNavigation(true);
  c.S.session = {session_id: 'b', workspace: '/b', model: 'm'};
  c._markExplicitSessionNavigation(true);
  c.S.session = {session_id: 'a', workspace: '/reloaded-a', model: 'm'};
  pending.resolve({ok: true});
  await switching;
  assert.equal(c.S.session.workspace, '/reloaded-a');
  assert.equal(dirs, 0);
});

test('newer Quick Search result wins when an older request resolves late', async () => {
  const browser = fs.readFileSync(path.join(root, 'web/static/browser.js'), 'utf8');
  const first = deferred();
  const second = deferred();
  const elements = {
    websearchQuery: {value: 'first'},
    websearchQuickMeta: {textContent: '', innerHTML: ''},
    websearchResults: {innerHTML: '', appendChild() {}},
    websearchQuickEmpty: {style: {}},
    websearchSuggestionChips: {style: {}, innerHTML: '', appendChild() {}},
    go: {disabled: false},
  };
  const c = vm.createContext({
    AbortController, Event, Promise, JSON, Date, encodeURIComponent, window: {},
    localStorage: {getItem() {return null;}, setItem() {}},
    document: {
      getElementById(id) {return elements[id] || null;},
      querySelector(selector) {return selector === '.websearch-go-btn' ? elements.go : null;},
      createElement() {return {className: '', innerHTML: '', appendChild() {}};},
    },
    _websearchEscape: value => String(value),
    api: () => elements.websearchQuery.value === 'first' ? first.promise : second.promise,
  });
  vm.runInContext(section(browser, 'let _websearchHistoryOpen = true;', '// ── Parse Quick Response'), c);
  vm.runInContext(section(browser, 'function _websearchParseQuickResponse(', '// ── Simple escape'), c);
  vm.runInContext('_websearchSaveToHistory=()=>{}; _websearchRenderChips=()=>{}; _websearchLastQuery="";', c);
  const oldRequest = c.websearchQuickSearch();
  elements.websearchQuery.value = 'second';
  const currentRequest = c.websearchQuickSearch();
  second.resolve({response: JSON.stringify({answer: 'SECOND', results: []})});
  await currentRequest;
  first.resolve({response: JSON.stringify({answer: 'FIRST', results: []})});
  await oldRequest;
  assert.match(elements.websearchResults.innerHTML, /SECOND/);
  assert.doesNotMatch(elements.websearchResults.innerHTML, /FIRST/);
  assert.equal(elements.go.disabled, false);
});

test('browser state failure stays visible and returns a retryable sentinel', async () => {
  const browser = fs.readFileSync(path.join(root, 'web/static/browser.js'), 'utf8');
  const observed = {pill: null, status: '', summary: '', empty: null, disabled: null};
  const c = vm.createContext({
    window: {}, Promise, String, encodeURIComponent,
    _browserRequestRev: 0,
    _browserCurrentSessionId: () => 's1',
    api: async () => {throw new Error('offline');},
    _browserSetPill: (...args) => {observed.pill = args;},
    _browserSetStatusUrl: value => {observed.status = value;},
    _browserSetActionSummary: value => {observed.summary = value;},
    _browserSetEmptyVisible: (...args) => {observed.empty = args;},
    _browserSetButtonsDisabled: (...args) => {observed.disabled = args;},
    _browserRender() {}, browserRefreshAgentContext() {}, browserRefreshPermission() {},
    console: {warn() {}},
  });
  vm.runInContext(section(browser, 'let _browserStateFetchSid =', 'function _browserHandleStreamPayload'), c);
  const state = await c._browserFetchState('s1');
  assert.equal(state, false);
  assert.deepEqual(observed.pill, ['error', 'Error']);
  assert.equal(observed.status, 'offline');
  assert.match(observed.summary, /Retrying/);
  assert.equal(observed.empty[0], true);
  assert.equal(observed.empty[1].title, 'Browser unavailable');
  assert.equal(observed.empty[1].text, 'Could not load the browser runtime. Retrying…');
  assert.deepEqual(observed.disabled, [true, null]);
  const syncBody = section(browser, 'async function browserSyncToCurrentSession(', 'function _browserSendControl');
  assert.match(syncBody, /state !== false/);
});

test('a stale Space list cannot erase a Space created while it was loading', async () => {
  const staleList = deferred();
  let createBody = null;
  const c = vm.createContext({
    window: {_sidekickSpaceSwitchRev: 0}, Date, Promise, setTimeout, clearTimeout,
    console: {warn() {}}, _activeSpace: 'nova',
    localStorage: {setItem() {}, getItem() {return null;}},
    _withSpaceTimeout: promise => promise,
    updateTitlebarSpace() {}, _refreshSidebarSelector() {},
    api: (path, options) => {
      if (path === '/api/spaces') return staleList.promise;
      if (path === '/api/space/create') {
        createBody = JSON.parse(options.body);
        return Promise.resolve({space: {slug: 'new', name: 'New', project_dir: '/project'}});
      }
      throw new Error('unexpected request: ' + path);
    },
  });
  c.selectSpace = async slug => {
    c._activeSpace = slug;
    c.window._sidekickSpaceSwitchRev++;
  };
  vm.runInContext(section(spaces, 'let _spacesCache = [];', 'function _resetViewsForSpaceSwitch()'), c);
  vm.runInContext(section(spaces, 'function _spaceSwitchRev()', 'function _deferSpaceSelect('), c);
  vm.runInContext(section(spaces, 'async function createSpace(', 'async function deleteSpace'), c);

  const loading = c.loadSpaces();
  const created = await c.createSpace('new', 'New', '', '', {projectDir: '/project'});
  staleList.resolve({spaces: [{slug: 'nova'}]});
  await loading;

  assert.equal(created.ok, true);
  assert.equal(createBody.project_dir, '/project');
  assert.equal(c._activeSpace, 'new');
  assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('_spacesCache', c))), [{slug: 'new', name: 'New', project_dir: '/project'}]);
});

test('the latest forced Space refresh wins when responses arrive out of order', async () => {
  const first = deferred();
  const second = deferred();
  let requests = 0;
  const c = vm.createContext({
    window: {_sidekickSpaceSwitchRev: 0}, Date, Promise, setTimeout, clearTimeout,
    console: {warn() {}}, _activeSpace: 'b',
    localStorage: {setItem() {}, getItem() {return null;}},
    _withSpaceTimeout: promise => promise,
    updateTitlebarSpace() {},
    api: () => (++requests === 1 ? first.promise : second.promise),
  });
  vm.runInContext(section(spaces, 'let _spacesCache = [];', 'function _resetViewsForSpaceSwitch()'), c);
  vm.runInContext(section(spaces, 'function _spaceSwitchRev()', 'function _deferSpaceSelect('), c);

  const oldLoad = c.loadSpaces({force: true});
  const currentLoad = c.loadSpaces({force: true});
  second.resolve({spaces: [{slug: 'b'}]});
  await currentLoad;
  first.resolve({spaces: [{slug: 'a'}]});
  await oldLoad;

  assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('_spacesCache', c))), [{slug: 'b'}]);
});

test('a delayed Space delete does not move a user who already chose another Space', async () => {
  const deleted = deferred();
  const selected = [];
  const c = vm.createContext({
    window: {_sidekickSpaceSwitchRev: 0}, Date, Promise, console: {warn() {}},
    DEFAULT_SPACE_SLUG: 'nova', _activeSpace: 'a',
    _withSpaceTimeout: promise => promise, _isProtectedSpaceSlug: () => false,
    _refreshSidebarSelector() {}, api: () => deleted.promise,
  });
  c.selectSpace = async slug => {selected.push(slug);};
  vm.runInContext(section(spaces, 'let _spacesCache = [];', 'function _resetViewsForSpaceSwitch()'), c);
  vm.runInContext(section(spaces, 'async function deleteSpace(', 'async function loadSpaceConfig'), c);
  vm.runInContext('_spacesCache = [{slug:"a"}, {slug:"c"}]', c);

  const deleting = c.deleteSpace('a');
  c._activeSpace = 'c';
  deleted.resolve({deleted: true});
  await deleting;

  assert.deepEqual(selected, []);
  assert.equal(c._activeSpace, 'c');
});

test('a delayed Space content search cannot populate the next Space sidebar', async () => {
  const pending = deferred();
  let searchTimer = null;
  let currentSpaceKey = 'a:0';
  const input = {value: 'needle'};
  let renders = 0;
  const c = vm.createContext({
    window: {location: {search: ''}}, URLSearchParams, Promise, encodeURIComponent,
    _allSessions: [],
    $: () => input,
    localStorage: {getItem() {return null;}},
    getActiveSpaceQuery: () => '?workspace=a',
    _activeSpaceLoadKey: () => currentSpaceKey,
    isActiveSpaceLoadKey: key => key === currentSpaceKey,
    setTimeout(fn) { searchTimer = fn; return 1; }, clearTimeout() {},
    renderSessionListFromCache() {renders++;},
    api: () => pending.promise,
  });
  vm.runInContext(section(sessions, 'let _searchDebounceTimer = null;', 'function _sessionTimestampMs'), c);

  c.filterSessions();
  const search = searchTimer();
  currentSpaceKey = 'b:1';
  pending.resolve({sessions: [{session_id: 'a1', match_type: 'content', title: 'needle'}]});
  await search;

  assert.deepEqual(JSON.parse(JSON.stringify(vm.runInContext('_contentSearchResults', c))), []);
  assert.equal(renders, 1);
});

test('a stale manual compression preflight cannot restore its former conversation', async () => {
  const preflight = deferred();
  let compressionCalls = 0;
  let currentSpaceKey = 'a:0';
  const c = vm.createContext({
    window: {__sidekickSessionNavigationEpoch: 0, _sidekickSpaceSwitchRev: 0},
    S: {session: {session_id: 'a'}, messages: [{role: 'user', content: 'A'}], toolCalls: []},
    Promise, JSON, encodeURIComponent,
    localStorage: {setItem() {}},
    _activeSpaceLoadKey: () => currentSpaceKey,
    isActiveSpaceLoadKey: key => key === currentSpaceKey,
    api: path => {
      if (path.startsWith('/api/session?')) return preflight.promise;
      if (path === '/api/session/compress') { compressionCalls++; return Promise.resolve({}); }
      throw new Error('unexpected request: ' + path);
    },
    t: value => value, msgContent: message => message.content || '',
    _compressionAnchorMessageKey: () => null,
    clearLiveToolCards() {}, clearCompressionUi() {}, _setCompressionSessionLock() {},
    setBusy() {}, setComposerStatus() {}, renderMessages() {}, showToast() {},
    setCompressionUi() {}, syncTopbar() {}, renderSessionList: async () => {},
    updateQueueBadge() {}, loadSession: async () => {throw new Error('must not load a stale session');},
  });
  const start = commands.lastIndexOf('async function _runManualCompression(');
  const end = commands.indexOf('async function cmdUsage', start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(commands.slice(start, end), c);

  const running = c._runManualCompression();
  c.S.session = {session_id: 'b'};
  c.S.messages = [{role: 'user', content: 'B'}];
  c.window.__sidekickSessionNavigationEpoch++;
  currentSpaceKey = 'b:1';
  preflight.resolve({session: {session_id: 'a', messages: [{role: 'user', content: 'stale A'}]}});
  await running;

  assert.equal(c.S.session.session_id, 'b');
  assert.deepEqual(c.S.messages, [{role: 'user', content: 'B'}]);
  assert.equal(compressionCalls, 0);
});

test('workspace creation cannot fall through and update a newly selected chat', async () => {
  const panels = fs.readFileSync(path.join(root, 'web/static/panels.js'), 'utf8');
  const pending = deferred();
  let updates = 0;
  const c = vm.createContext({
    window: {__sidekickSessionNavigationEpoch: 0}, S: {session: null}, JSON,
    api: path => {
      if (path === '/api/session/new') return pending.promise;
      updates++;
      return Promise.resolve({});
    },
    closeWsDropdown() {}, showToast() {}, t: value => value,
  });
  vm.runInContext(section(panels, 'async function switchToWorkspace(path,name){', '// ── Profile panel'), c);
  const switching = c.switchToWorkspace('/new', 'New');
  c.S.session = {session_id: 'selected', workspace: '/selected'};
  c.window.__sidekickSessionNavigationEpoch++;
  pending.resolve({session: {session_id: 'created', workspace: '/new'}});
  await switching;
  assert.equal(updates, 0);
  assert.equal(c.S.session.workspace, '/selected');
});

for (const fail of [false, true]) {
  test(`manual compression clears busy state on ${fail ? 'failure' : 'success'}`, async () => {
    const busy = [];
    const notices = [];
    const c = vm.createContext({
      window: {}, S: {session: {session_id: 'a'}, messages: [], toolCalls: []},
      encodeURIComponent, JSON,
      api: async path => {
        if (path.startsWith('/api/session?')) return {session: {session_id: 'a', messages: []}};
        if (fail) throw new Error('backend unavailable');
        return {};
      },
      t: value => value, _compressionAnchorMessageKey: () => null,
      setBusy: value => busy.push(value), renderMessages() {},
      showToast: value => notices.push(value),
    });
    const start = commands.lastIndexOf('async function _runManualCompression(');
    vm.runInContext(commands.slice(start, commands.indexOf('async function cmdUsage', start)), c);
    await c._runManualCompression();
    assert.deepEqual(busy, [true, false]);
    assert.equal(notices.length, fail ? 1 : 0);
    if (fail) assert.match(notices[0], /backend unavailable/);
  });
}

test('a late failed directory load cannot blank a newer directory or git badge', async () => {
  const oldLoad = deferred();
  const newLoad = deferred();
  const oldGit = deferred();
  let loads = 0;
  const empty = {textContent: '', style: {display: ''}};
  const tree = {innerHTML: 'current tree', style: {display: ''}};
  const gitBadge = {textContent: 'main', className: '', style: {display: ''}};
  const composerGitBadge = {textContent: 'main', className: '', style: {display: ''}};
  const c = vm.createContext({
    window: {_workspaceApiWithTimeout() {}}, Promise, setTimeout, clearTimeout, AbortController, encodeURIComponent,
    S: {session: {session_id: 'a', workspace: '/a'}, entries: [], _dirCache: {}, _expandedDirs: new Set()},
    localStorage: {getItem() {return null;}, setItem() {}},
    document: {body: {classList: {contains() {return false;}}}},
    getComputedStyle() {return {display: '', visibility: ''};},
    $: id => ({wsEmptyState: empty, fileTree: tree, gitBadge, composerGitBadge}[id] || null),
    renderBreadcrumb() {}, renderFileTree() {tree.innerHTML = c.S.entries[0].name;},
    console: {warn() {}}, t: value => value,
    api: () => oldGit.promise,
  });
  vm.runInContext(section(workspace, 'let _loadDirRev = 0;', 'window.flushPendingWorkspaceTreeRefresh'), c);
  vm.runInContext(section(workspace, 'async function _refreshGitBadge()', 'function navigateUp'), c);
  c._workspaceApiWithTimeout = () => (++loads === 1 ? oldLoad.promise : newLoad.promise);

  const stale = c.loadDir('old');
  const current = c.loadDir('new');
  newLoad.resolve({entries: [{name: 'new'}]});
  await current;
  oldLoad.reject(new Error('old directory failed'));
  await stale;

  assert.equal(tree.innerHTML, 'new');
  assert.equal(empty.style.display, '');

  const badgeRequest = c._refreshGitBadge();
  c.S.session = {session_id: 'b', workspace: '/b'};
  oldGit.resolve({git: {is_git: true, branch: 'stale', dirty: 0, ahead: 0, behind: 0}});
  await badgeRequest;
  assert.equal(gitBadge.textContent, 'main');
  assert.equal(composerGitBadge.textContent, 'main');
});

test('openFileInWorkspace stops after a newer Space navigation', async () => {
  const rootLoad = deferred();
  let rendered = 0;
  let opened = 0;
  let apiCalls = 0;
  const c = vm.createContext({
    window: {__sidekickSessionNavigationEpoch: 0}, Promise, encodeURIComponent,
    S: {session: {session_id: 'a'}, entries: [], _dirCache: {}, _expandedDirs: new Set()},
    _activeSpaceLoadKey: () => 'a:0', isActiveSpaceLoadKey: () => true,
    loadDir: () => rootLoad.promise,
    api: () => {apiCalls++; return Promise.resolve({entries: []});},
    renderFileTree() {rendered++;}, openFile: async () => {opened++;},
    requestAnimationFrame(fn) {fn();},
    document: {querySelectorAll() {return []; }},
  });
  vm.runInContext(section(fs.readFileSync(path.join(root, 'web/static/panels.js'), 'utf8'), 'async function openFileInWorkspace(', '// ── EVEY TOOLS PANEL'), c);

  const opening = c.openFileInWorkspace('src/main.js');
  c.S.session = {session_id: 'b'};
  c.window.__sidekickSessionNavigationEpoch++;
  rootLoad.resolve();
  await opening;

  assert.equal(apiCalls, 0);
  assert.equal(rendered, 0);
  assert.equal(opened, 0);
});
