let _browserActiveSessionId = null;
let _browserEventSource = null;
let _browserPollTimer = null;
let _browserSyncRetryTimer = null;
let _browserRequestRev = 0;
let _browserState = null;
let _browserMoveThrottle = null;
let _browserMovePending = null;
let _browserClickFlashTimer = null;
let _browserFrameLoaded = false;
let _browserPendingSessionSwitch = false;
let _browserDrawerOpen = localStorage.getItem('sidekick-browser-drawer-open') === '1';
let _browserActionTrace = [];
let _browserActionTraceSessionId = '';
let _browserActionTraceKey = '';
let _browserResearchSessions = [];
let _browserResearchSessionId = null;
let _browserResearchBusy = false;
let _browserResearchLoadRev = 0;
let _browserResearchCurrentPrompt = '';
let _browserResearchTopicsBySession = {};
let _browserResearchStateBySession = {};
let _browserResearchIntakeBySession = {};
let _browserResearchSelectedDirectionBySession = {};
let _browserResearchQuickAnswerBySession = {};
let _browserResearchQuestionsBySession = {};
let _browserResearchResearchPromptBySession = {};
let _browserResearchModeBySession = {};
let _browserPermissionMode = 'none';
const _BROWSER_PERMISSION_STORAGE_KEY = 'sidekick-browser-permission-mode';
let _browserAgentContext = null;
let _browserAgentContextSid = '';
let _browserAgentContextPromise = null;
let _browserWebBackend = 'auto';
let _browserWebBackendConfigured = '';
let _browserFullscreen = localStorage.getItem('sidekick-browser-fullscreen') === '1';
let _browserSplitScreen = localStorage.getItem('sidekick-browser-split-open') === '1';
let _browserLastSubmittedUrl = '';
let _browserLastSubmittedAt = 0;
let _browserDrawerHost = null;
let _browserDrawerHostNext = null;
let _browserDrawerDragState = null;
const _browserDrawerFloatPositionKey = 'sidekick-browser-drawer-float-position';
let _browserFrameObjectUrl = '';
let _browserExploreMode = false;
let _browserDiffActive = false;
let _browserDiffTimer = null;
let _browserTraceThumbnails = [];
let _browserChatContextActive = false;
let _browserPrevFrameRev = '';
let _browserHeaderMenuOpen = false;
let _browserUrlDraft = '';
let _browserUrlDraftAt = 0;
let _browserRuntimeInitialized = false;

function _browserEl(id) {
  return document.getElementById(id);
}

function _browserRememberUrlDraft(value) {
  _browserUrlDraft = String(value || '').trim();
  _browserUrlDraftAt = Date.now();
}

function _browserClearUrlDraft() {
  _browserUrlDraft = '';
  _browserUrlDraftAt = 0;
}

function _browserNormalizeSubmittedUrl(value) {
  const raw = String(value || '').trim();
  if (!raw) return '';
  const matches = raw.match(/https?:\/\/[^\s]+/gi);
  if (matches && matches.length) return matches[matches.length - 1];
  const cleaned = raw.replace(/^about:blank(?=https?:\/\/)/i, '');
  if (/^(localhost|127(?:\.\d{1,3}){3}|\[?::1\]?)(:\d+)?([/?#]|$)/i.test(cleaned)) {
    return 'http://' + cleaned;
  }
  if (/^[a-z0-9.-]+\.[a-z]{2,}(:\d+)?([/?#]|$)/i.test(cleaned)) {
    return 'https://' + cleaned;
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(cleaned)) return cleaned;
  return cleaned;
}

function _browserAttachUrlDraftHandlers() {
  const input = _browserEl('browserUrlInput');
  if (input && input.dataset.browserDraftBound !== '1') {
    input.dataset.browserDraftBound = '1';
    input.addEventListener('input', function() {
      _browserRememberUrlDraft(input.value);
    });
    input.addEventListener('focus', function() {
      _browserRememberUrlDraft(input.value);
      try { input.select(); } catch (_) {}
    });
  }
  const toolbar = document.querySelector('.browser-toolbar');
  if (toolbar && toolbar.dataset.browserSubmitBound !== '1') {
    toolbar.dataset.browserSubmitBound = '1';
    toolbar.addEventListener('submit', browserSubmitUrl);
  }
  const goBtn = _browserEl('browserGoBtn') || document.querySelector('.browser-toolbar .browser-go-btn');
  if (goBtn && goBtn.dataset.browserClickBound !== '1') {
    goBtn.dataset.browserClickBound = '1';
    goBtn.addEventListener('click', browserSubmitUrl);
  }
}

function _browserEnsureSplitStyles() {
  if (document.getElementById('browserSplitStyles')) return;
  const style = document.createElement('style');
  style.id = 'browserSplitStyles';
  style.textContent = `
body.browser-split {
  --browser-split-width: min(760px, max(420px, 42vw));
}
body.browser-split aside.rightpanel,
body.browser-split .split-resize-handle {
  display: none !important;
}
body.browser-split .app-main,
body.browser-split main.main {
  min-height: 0;
}
body.browser-split main.main {
  padding-right: var(--browser-split-width) !important;
  box-sizing: border-box;
}
body.browser-split main.main > :not(#mainChat):not(#mainBrowser) {
  display: none !important;
}
body.browser-split main.main > #mainBrowser {
  display: none !important;
  flex: 0 0 auto;
  min-width: 0;
  min-height: 0;
}
body.browser-split main.main > #mainChat {
  display: flex !important;
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
body.browser-split #mainChat > :not(#chatSplitLayout) {
  display: none !important;
}
body.browser-split #chatSplitLayout {
  display: flex !important;
  flex: 1 1 auto;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
}
body.browser-split .browser-drawer {
  position: fixed;
  top: 38px !important;
  right: 0 !important;
  bottom: 0 !important;
  left: auto !important;
  width: var(--browser-split-width) !important;
  height: calc(100vh - 38px) !important;
  display: flex;
  flex-direction: column;
  z-index: 260;
  margin: 0;
  max-height: none !important;
  opacity: 1 !important;
  visibility: visible !important;
  pointer-events: auto;
  transform: none !important;
}
body.browser-split.browser-drawer-open:not(.browser-maximized) .browser-drawer {
  height: calc(100vh - 38px) !important;
  max-height: none !important;
}
body.browser-split .browser-drawer-shell {
  width: 100%;
  height: 100%;
  border-radius: 0;
  box-shadow: none;
}
body.browser-split .browser-drawer .browser-stage-wrap {
  flex: 1 1 auto;
  min-height: 0;
}
body.browser-split .browser-drawer .browser-stage {
  height: 100%;
  min-height: 0;
}
.browser-split-btn.is-active {
  color: var(--accent);
  background: var(--accent-bg);
  border-color: var(--accent);
}
@media (max-width: 1100px) {
  .browser-split-btn {
    display: none !important;
  }
  body.browser-split {
    --browser-split-width: min(760px, calc(100vw - 32px));
  }
  body.browser-split main.main {
    padding-right: 0;
  }
  body.browser-split .browser-drawer {
    left: 50% !important;
    right: auto !important;
    width: min(760px, calc(100vw - 32px)) !important;
    transform: translateX(-50%) !important;
    border-radius: 12px;
    top: auto !important;
    bottom: calc(12px + env(safe-area-inset-bottom,0px)) !important;
    height: min(52vh, 560px) !important;
  }
  body.browser-split .browser-drawer-shell {
    border-radius: 12px;
    box-shadow: 0 10px 30px rgba(0,0,0,.16);
  }
}
  `;
  document.head.appendChild(style);
}

function _browserCurrentSessionId() {
  if (typeof S !== 'undefined' && S && S.session && S.session.session_id) return String(S.session.session_id);
  const match = String(location.pathname || '').match(/^\/session\/([^/?#]+)/);
  if (match) return decodeURIComponent(match[1]);
  try {
    const activeRow = document.querySelector('#sessionList .session-item.active[data-sid]');
    if (activeRow && activeRow.dataset && activeRow.dataset.sid) return String(activeRow.dataset.sid);
  } catch (_) {}
  try {
    const saved = localStorage.getItem('sidekick-webui-session');
    if (saved) return String(saved);
  } catch (_) {}
  return '';
}

function _browserRememberedPermissionMode() {
  const liveMode = String(_browserPermissionMode || '').trim().toLowerCase();
  if (liveMode === 'read' || liveMode === 'control') return liveMode;
  try {
    const storedMode = String(localStorage.getItem(_BROWSER_PERMISSION_STORAGE_KEY) || '').trim().toLowerCase();
    if (storedMode === 'read' || storedMode === 'control') return storedMode;
  } catch (_) {}
  return 'none';
}

function _browserPersistPermissionMode(mode) {
  const nextMode = String(mode || '').trim().toLowerCase();
  try {
    if (nextMode === 'read' || nextMode === 'control') {
      localStorage.setItem(_BROWSER_PERMISSION_STORAGE_KEY, nextMode);
    } else {
      localStorage.removeItem(_BROWSER_PERMISSION_STORAGE_KEY);
    }
  } catch (_) {}
  return nextMode;
}

function _browserPanelVisible() {
  return document.body.classList.contains('browser-drawer-open');
}

function _browserSyncDrawerButton(open) {
  const btn = _browserEl('btnBrowserDrawerToggle');
  if (!btn) return;
  btn.classList.toggle('is-active', !!open);
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

function _browserSessionLabel(state) {
  const sid = String((state && state.session_id) || _browserActiveSessionId || _browserCurrentSessionId() || '');
  if (!sid) return '';
  return 'session ' + sid.slice(0, 8);
}

function _browserSetEmptyVisible(visible, opts = {}) {
  const empty = _browserEl('browserEmptyState');
  const isVisible = !!visible;
  if (empty) {
    const title = empty.querySelector ? empty.querySelector('.browser-empty-title') : null;
    const text = empty.querySelector ? empty.querySelector('.browser-empty-text') : null;
    if (isVisible && title) title.textContent = opts.title || 'Browser not attached';
    if (isVisible && text) text.textContent = opts.text || 'Open a chat session to attach the browser runtime.';
    empty.classList.toggle('visible', isVisible);
    empty.setAttribute('aria-hidden', isVisible ? 'false' : 'true');
  }
  const stage = _browserEl('browserStage');
  if (stage) stage.classList.toggle('has-empty-state', isVisible);
  const wrap = _browserEl('browserStageWrap');
  if (wrap) wrap.classList.toggle('has-empty-state', isVisible);
}

function _browserClearViewport() {
  const img = _browserEl('browserFrameImage');
  if (img) {
    if (_browserFrameObjectUrl) {
      try { URL.revokeObjectURL(_browserFrameObjectUrl); } catch (_) {}
      _browserFrameObjectUrl = '';
    }
    img.removeAttribute('src');
    img.dataset.rev = '';
    img.dataset.frameSrc = '';
    img.style.visibility = 'hidden';
  }
  const target = _browserEl('browserTargetBox');
  if (target) target.classList.remove('visible');
  const targetLabel = _browserEl('browserTargetLabel');
  if (targetLabel) targetLabel.textContent = '';
  const cursor = _browserEl('browserCursor');
  if (cursor) cursor.classList.remove('visible');
  const flash = _browserEl('browserClickFlash');
  if (flash) flash.classList.remove('visible');
  _browserApplyFrameHitBounds(null);
}

function _browserSetPill(kind, text) {
  const pill = _browserEl('browserStatusPill');
  if (!pill) return;
  pill.className = 'browser-status-pill';
  if (kind) pill.classList.add('is-' + kind);
  pill.textContent = text || 'Idle';
}

function _browserSetDrawerAccessibility(open) {
  const browserDrawer = _browserEl('browserDrawer');
  if (!browserDrawer) return;
  if (open) {
    browserDrawer.setAttribute("aria-hidden", "false");
    browserDrawer.removeAttribute("inert");
  } else {
    browserDrawer.setAttribute("aria-hidden", "true");
    browserDrawer.setAttribute("inert", "");
  }
}

function _browserResearchStateKey() {
  return _browserCurrentSessionId() || '__no_session__';
}

function _browserResearchGetSessionState() {
  const key = _browserResearchStateKey();
  if (!_browserResearchStateBySession[key]) {
    _browserResearchStateBySession[key] = {
      sessionId: null,
      prompt: '',
      intake: null,
      selectedDirection: '',
      quickAnswer: '',
      questions: [],
      researchPrompt: '',
      mode: 'idle',
    };
  }
  return _browserResearchStateBySession[key];
}

function _browserResearchSaveSessionState() {
  const state = _browserResearchGetSessionState();
  state.sessionId = _browserResearchSessionId || null;
  state.prompt = _browserResearchCurrentPrompt || '';
  const key = _browserResearchStateKey();
  state.intake = _browserResearchIntakeBySession[key] || null;
  state.selectedDirection = _browserResearchSelectedDirectionBySession[key] || '';
  state.quickAnswer = _browserResearchQuickAnswerBySession[key] || '';
  state.questions = Array.isArray(_browserResearchQuestionsBySession[key]) ? _browserResearchQuestionsBySession[key].slice() : [];
  state.researchPrompt = _browserResearchResearchPromptBySession[key] || '';
  state.mode = _browserResearchModeBySession[key] || 'idle';
}

function _browserResearchApplySessionState() {
  const state = _browserResearchGetSessionState();
  _browserResearchSessionId = state.sessionId || null;
  _browserResearchCurrentPrompt = state.prompt || '';
  const key = _browserResearchStateKey();
  _browserResearchIntakeBySession[key] = state.intake || null;
  _browserResearchSelectedDirectionBySession[key] = state.selectedDirection || '';
  _browserResearchQuickAnswerBySession[key] = state.quickAnswer || '';
  _browserResearchQuestionsBySession[key] = Array.isArray(state.questions) ? state.questions.slice() : [];
  _browserResearchResearchPromptBySession[key] = state.researchPrompt || '';
  _browserResearchModeBySession[key] = state.mode || 'idle';
  const topic = _browserEl('browserResearchTopic');
  if (topic && document.activeElement !== topic) topic.value = _browserResearchCurrentPrompt;
  _browserResearchRenderQuickAnswer(_browserResearchQuickAnswerBySession[key] || '', {sessionId: _browserResearchSessionId});
  _browserResearchRenderQuestions(_browserResearchQuestionsBySession[key] || [], {sessionId: _browserResearchSessionId});
  _browserResearchSetContinueState();
}

function _browserSetButtonsDisabled(disabled, state) {
  state = state || {};
  const attached = !!(state && state.session_id) && !disabled;
  const hasSession = !!((state && state.session_id) || _browserCurrentSessionId());
  const busy = !!(state && state.busy);
  const buttons = {
    browserPermissionBtn: hasSession,
    browserAgentStopBtn: hasSession && _browserPermissionMode !== 'none',
    browserBtnBack: attached && !busy && !!state.can_go_back,
    browserBtnForward: attached && !busy && !!state.can_go_forward,
    browserBtnReload: attached && !busy,
    browserBtnStop: attached && busy,
    browserBtnOpenTab: attached && !!state.url,
  };
  Object.entries(buttons).forEach(([id, enabled]) => {
    const btn = _browserEl(id);
    if (btn) btn.disabled = !enabled;
  });
  document.querySelectorAll('.browser-toolbar .browser-nav-btn').forEach((btn, index) => {
    if (index === 0) btn.disabled = !(attached && !busy && !!state.can_go_back);
    else if (index === 1) btn.disabled = !(attached && !busy && !!state.can_go_forward);
    else btn.disabled = !(attached && !busy);
  });
  const goBtn = document.querySelector('.browser-toolbar .browser-go-btn');
  if (goBtn) goBtn.disabled = !(attached && !busy);
  const input = _browserEl('browserUrlInput');
  if (input) input.disabled = !attached;
}

function _browserSetSessionControlsReady(sessionId, statusText) {
  const sid = String(sessionId || '').trim();
  if (!sid) return;
  _browserActiveSessionId = sid;
  _browserPendingSessionSwitch = false;
  _browserSetSessionLabel({session_id: sid});
  _browserSetPill('idle', 'Idle');
  _browserSetStatusUrl(statusText || 'about:blank');
  _browserSetButtonsDisabled(false, {
    session_id: sid,
    busy: false,
    url: statusText || ((_browserEl('browserUrlInput') || {}).value || 'about:blank'),
    can_go_back: false,
    can_go_forward: false,
  });
  _browserSetEmptyVisible(false);
  const stage = _browserEl('browserStage');
  if (stage) stage.style.opacity = '1';
  _browserUpdateHeaderBadge();
}

function _browserResearchDefaultQuestions(topic) {
  const t = String(topic || '').toLowerCase();
  if (/(preis|kosten|budget|budget|pricing|cost)/.test(t)) {
    return ['Preisrahmen oder Budget?', 'Welche Region oder welcher Markt?', 'Kurzvergleich oder tiefe Analyse?'];
  }
  if (/(vergleich|vs\.?|alternativ|besten|beste)/.test(t)) {
    return ['Welche Optionen sollen verglichen werden?', 'Welche Kriterien sind entscheidend?', 'Soll ich aktuelle Quellen priorisieren?'];
  }
  if (/(how|wie|anleitung|guide|tutorial|setup|einrichten)/.test(t)) {
    return ['Für Anfänger oder Fortgeschrittene?', 'Welche Plattform oder Umgebung?', 'Soll ich die Schritt-für-Schritt-Version liefern?'];
  }
  if (/(recht|legal|steuer|medizin|gesundheit|sicherheit)/.test(t)) {
    return ['Geht es um allgemeine Orientierung oder konkrete Fälle?', 'Welches Land oder welcher Markt?', 'Soll ich Risiken, Grenzen und Quellenqualität extra hervorheben?'];
  }
  return ['Breit beginnen oder fokussiert?', 'Aktuellste Quellen oder Hintergrundwissen?', 'Für wen soll das Ergebnis aufbereitet sein?'];
}

function _browserResearchNormalizeQuestions(value, topic, allowEmpty) {
  let list = [];
  if (Array.isArray(value)) {
    list = value.map(item => String(item || '').trim()).filter(Boolean);
  } else if (typeof value === 'string') {
    list = value.split(/\r?\n|[•\u2022]/).map(item => item.replace(/^\s*[-*]\s*/, '').trim()).filter(Boolean);
  }
  list = list.slice(0, 3);
  if (!list.length && !allowEmpty) list = _browserResearchDefaultQuestions(topic).slice(0, 3);
  if (allowEmpty && !list.length) return [];
  while (list.length < 2) {
    list.push(_browserResearchDefaultQuestions(topic)[list.length % _browserResearchDefaultQuestions(topic).length]);
  }
  return list.slice(0, 3);
}

function _browserResearchBuildIntakePrompt(topic) {
  const clean = String(topic || '').trim();
  return [
    'You are Nova, the Sidekick browser tab intake assistant.',
    'Return ONLY valid JSON. No markdown, no code fences, no commentary.',
    'Schema:',
    '{',
    '  "quick_answer": "2-4 concise sentences that answer the topic at a glance",',
    '  "follow_up_questions": ["question 1", "question 2", "question 3"],',
    '  "research_prompt": "A single prompt that instructs a deeper research pass after the user chooses a direction",',
    '  "title": "Short title for this research run",',
    '  "focus_hint": "One short phrase describing the most useful next angle"',
    '}',
    'Rules:',
    '- Keep follow_up_questions highly relevant, narrow, and answerable.',
    '- Prefer 2-3 questions that split the topic by audience, region, timeframe, scope, or evaluation criteria.',
    '- Keep quick_answer readable, direct, and honest about uncertainty.',
    '- Mention source quality when relevant. Prefer primary sources, official docs, standards, papers, or current vendor docs over blogs.',
    '- Prefer newer or canonical sources when the topic is time-sensitive.',
    '- research_prompt should mention that this is the second pass after user chooses one direction.',
    '- research_prompt should instruct the model to summarize key claims, caveats, and the best sources to trust.',
    '- If the topic is ambiguous, make the quick_answer mention the ambiguity briefly and suggest the best next split.',
    'Topic: ' + clean,
  ].join('\n');
}

function _browserResearchBuildResearchPrompt(topic, direction, intake) {
  const cleanTopic = String(topic || '').trim();
  const cleanDirection = String(direction || '').trim();
  const quick = intake && intake.quick_answer ? String(intake.quick_answer).trim() : '';
  return [
    'You are Nova, the Sidekick browser research agent.',
    'The user has already seen a quick intake answer and chose a direction.',
    'Now produce a curated research result. Focus on clarity, structure, and sources.',
    'Output a concise but useful answer with headings, key takeaways, caveats, and next steps where relevant.',
    'Prioritize source quality: primary sources, official docs, papers, standards, and current product docs first.',
    'Avoid shallow summary. Compare conflicting claims, note the strongest evidence, and call out what is uncertain.',
    'When time-sensitive, prefer the latest canonical source and mention dates or version numbers if they matter.',
    quick ? ('Previously shown quick answer: ' + quick) : '',
    cleanDirection ? ('Chosen direction: ' + cleanDirection) : '',
    'Topic: ' + cleanTopic,
    'If the selected direction is too broad, narrow it to the most useful interpretation and say so briefly.',
  ].filter(Boolean).join('\n');
}

function _browserResearchParseIntakeResponse(text) {
  const raw = String(text == null ? '' : text).trim();
  if (!raw) {
    return {
      quick_answer: '',
      follow_up_questions: [],
      research_prompt: '',
      title: '',
      focus_hint: '',
    };
  }
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  const candidates = [];
  if (fenced && fenced[1]) candidates.push(fenced[1].trim());
  candidates.push(raw);
  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === 'object') {
        return {
          quick_answer: String(parsed.quick_answer || parsed.quickAnswer || parsed.answer || parsed.summary || '').trim(),
          follow_up_questions: _browserResearchNormalizeQuestions(parsed.follow_up_questions || parsed.followUpQuestions || parsed.questions || [], ''),
          research_prompt: String(parsed.research_prompt || parsed.researchPrompt || parsed.prompt || '').trim(),
          title: String(parsed.title || parsed.heading || '').trim(),
          focus_hint: String(parsed.focus_hint || parsed.focusHint || parsed.direction || '').trim(),
        };
      }
    } catch (_) {}
  }
  const cleaned = raw
    .replace(/^```[\w-]*\s*/i, '')
    .replace(/```$/i, '')
    .trim();
  const lines = cleaned.split(/\r?\n/).map(line => line.trim()).filter(Boolean);
  const questionLines = lines.filter(line => /[?？]$/.test(line)).slice(0, 3);
  return {
    quick_answer: cleaned,
    follow_up_questions: _browserResearchNormalizeQuestions(questionLines, ''),
    research_prompt: cleaned,
    title: '',
    focus_hint: '',
  };
}

function _browserResearchSetQuickAnswer(text, meta = {}) {
  const el = _browserEl('browserResearchQuickAnswer');
  if (!el) return;
  const clean = String(text == null ? '' : text).trim();
  el.classList.toggle('is-empty', !clean);
  el.innerHTML = clean ? _browserResearchEscape(clean) : 'Enter a topic and I will give you a quick answer first.';
  const state = _browserResearchGetSessionState();
  state.quickAnswer = clean;
  const key = _browserResearchStateKey();
  _browserResearchQuickAnswerBySession[key] = clean;
  if (meta && meta.researchPrompt) {
    state.researchPrompt = String(meta.researchPrompt || '').trim();
    _browserResearchResearchPromptBySession[key] = state.researchPrompt;
  }
  if (meta && meta.mode) {
    state.mode = String(meta.mode || 'idle');
    _browserResearchModeBySession[key] = state.mode;
  }
}

function _browserRememberDrawerHost() {
  const browserDrawer = _browserEl('browserDrawer');
  if (!browserDrawer || _browserDrawerHost) return;
  _browserDrawerHost = browserDrawer.parentNode || null;
  _browserDrawerHostNext = browserDrawer.nextSibling || null;
}

function _browserHoistDrawer() {
  const browserDrawer = _browserEl('browserDrawer');
  if (!browserDrawer || browserDrawer.parentNode === document.body) return;
  _browserRememberDrawerHost();
  document.body.appendChild(browserDrawer);
}

function _browserRestoreDrawerHost() {
  const browserDrawer = _browserEl('browserDrawer');
  if (!browserDrawer || !_browserDrawerHost) return;
  if (browserDrawer.parentNode === _browserDrawerHost) return;
  if (_browserDrawerHostNext && _browserDrawerHostNext.parentNode === _browserDrawerHost) {
    _browserDrawerHost.insertBefore(browserDrawer, _browserDrawerHostNext);
  } else {
    _browserDrawerHost.appendChild(browserDrawer);
  }
}

function _browserClearDrawerFloatPositionStyles() {
  const browserDrawer = _browserEl('browserDrawer');
  if (!browserDrawer) return;
  browserDrawer.classList.remove('is-dragging');
  browserDrawer.style.removeProperty('left');
  browserDrawer.style.removeProperty('top');
  browserDrawer.style.removeProperty('right');
  browserDrawer.style.removeProperty('bottom');
  browserDrawer.style.removeProperty('transform');
}

function _browserReadDrawerFloatPosition() {
  try {
    const raw = localStorage.getItem(_browserDrawerFloatPositionKey);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    const left = Number(parsed && parsed.left);
    const top = Number(parsed && parsed.top);
    if (!Number.isFinite(left) || !Number.isFinite(top)) return null;
    return {left, top};
  } catch (_) {
    return null;
  }
}

function _browserWriteDrawerFloatPosition(left, top) {
  try {
    localStorage.setItem(_browserDrawerFloatPositionKey, JSON.stringify({
      left: Math.round(left),
      top: Math.round(top),
    }));
  } catch (_) {}
}

function _browserClampDrawerFloatPosition(left, top, width, height) {
  const margin = 16;
  const titlebar = 38;
  const maxLeft = Math.max(margin, window.innerWidth - width - margin);
  const maxTop = Math.max(titlebar + margin, window.innerHeight - height - margin);
  return {
    left: Math.max(margin, Math.min(left, maxLeft)),
    top: Math.max(titlebar + margin, Math.min(top, maxTop)),
  };
}

function _browserDefaultDrawerFloatPosition(width, height) {
  const margin = 16;
  const titlebar = 38;
  const maxLeft = Math.max(margin, window.innerWidth - width - margin);
  const maxTop = Math.max(titlebar + margin, window.innerHeight - height - margin);
  return {
    left: maxLeft,
    top: maxTop,
  };
}

function _browserSyncDrawerFloatPosition() {
  const browserDrawer = _browserEl('browserDrawer');
  if (!browserDrawer) return;
  if (!_browserDrawerOpen || _browserSplitScreen || _browserFullscreen) {
    _browserClearDrawerFloatPositionStyles();
    _browserDrawerDragState = null;
    return;
  }
  const rect = browserDrawer.getBoundingClientRect();
  const width = rect && rect.width > 0 ? rect.width : Math.min(760, Math.max(320, window.innerWidth - 32));
  const height = rect && rect.height > 0 ? rect.height : Math.min(Math.round(window.innerHeight * 0.52), 560);
  const saved = _browserReadDrawerFloatPosition();
  const fallback = _browserDefaultDrawerFloatPosition(width, height);
  const next = _browserClampDrawerFloatPosition(
    saved && Number.isFinite(saved.left) ? saved.left : fallback.left,
    saved && Number.isFinite(saved.top) ? saved.top : fallback.top,
    width,
    height,
  );
  browserDrawer.style.left = next.left + 'px';
  browserDrawer.style.top = next.top + 'px';
  browserDrawer.style.right = 'auto';
  browserDrawer.style.bottom = 'auto';
  browserDrawer.style.transform = 'none';
}

function _browserSyncFullscreenButton(active) {
  const btn = _browserEl('browserBtnOpenTab');
  if (!btn) return;
  btn.classList.toggle('is-active', !!active);
  btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  btn.setAttribute('data-tooltip', active ? 'Restore browser' : 'Maximize browser');
  btn.setAttribute('aria-label', active ? 'Restore browser' : 'Maximize browser');
}

function _browserSyncSplitButton(active) {
  const btn = _browserEl('browserBtnSplit');
  if (!btn) return;
  btn.classList.toggle('is-active', !!active);
  btn.setAttribute('aria-pressed', active ? 'true' : 'false');
  btn.setAttribute('data-tooltip', active ? 'Exit split screen' : 'Split browser and chat');
  btn.setAttribute('aria-label', active ? 'Exit split screen' : 'Split browser and chat');
}

function _browserSetFullscreen(open) {
  const next = !!open;
  _browserFullscreen = next;
  try { localStorage.setItem('sidekick-browser-fullscreen', next ? '1' : '0'); } catch (_) {}
  if (next && _browserSplitScreen) {
    _browserSetSplitScreen(false);
  }
  document.body.classList.toggle('browser-maximized', next);
  _browserSyncFullscreenButton(next);
  _browserUpdateHeaderBadge();
  if (next) {
    _browserHoistDrawer();
    browserSetDrawerOpen(true, {force: true, keepViewport: true});
    _browserSetDrawerAccessibility(true);
  } else {
    _browserRestoreDrawerHost();
    _browserSetDrawerAccessibility(_browserDrawerOpen);
  }
  _browserSyncDrawerFloatPosition();
}

function browserToggleFullscreen() {
  _browserSetFullscreen(!_browserFullscreen);
}

function _browserSetSplitScreen(open) {
  const next = !!open;
  _browserSplitScreen = next;
  try { localStorage.setItem('sidekick-browser-split-open', next ? '1' : '0'); } catch (_) {}
  if (next && _browserFullscreen) {
    _browserSetFullscreen(false);
  }
  document.body.classList.toggle('browser-split', next);
  _browserSyncSplitButton(next);
  _browserUpdateHeaderBadge();
  if (next) {
    if (!_browserDrawerOpen) {
      browserSetDrawerOpen(true, {force: true, keepViewport: true});
    } else {
      void browserSyncToCurrentSession({allowPending: true});
    }
  } else {
    document.body.classList.remove('browser-split');
  }
  _browserSyncDrawerFloatPosition();
}

function browserToggleExploreMode() {
  _browserExploreMode = !_browserExploreMode;
  const btn = _browserEl('browserExploreBtn');
  if (btn) {
    btn.classList.toggle('is-active', _browserExploreMode);
    btn.setAttribute('aria-pressed', _browserExploreMode ? 'true' : 'false');
    btn.setAttribute('data-tooltip', _browserExploreMode ? 'Switch to Follow mode' : 'Switch to Explore mode');
    btn.setAttribute('aria-label', _browserExploreMode ? 'Switch to Follow mode' : 'Switch to Explore mode');
  }
  const stage = _browserEl('browserStage');
  if (stage) {
    stage.style.cursor = _browserExploreMode ? 'default' : 'none';
  }
  const hitLayer = _browserEl('browserHitLayer');
  if (hitLayer) {
    hitLayer.style.cursor = _browserExploreMode ? 'pointer' : 'default';
  }
  if (typeof showToast === 'function') {
    showToast(_browserExploreMode ? 'Explore mode: you can click the viewport' : 'Follow mode: agent controls the browser', 2000, 'info');
  }
  _browserUpdateHeaderBadge();
}

function _browserTriggerDiffOverlay() {
  const overlay = _browserEl('browserDiffOverlay');
  if (!overlay || _browserDiffActive) return;
  _browserDiffActive = true;
  overlay.classList.add('visible');
  clearTimeout(_browserDiffTimer);
  _browserDiffTimer = setTimeout(function() {
    overlay.classList.remove('visible');
    _browserDiffActive = false;
  }, 260);
}

function _browserUpdateChatContext(state) {
  const bar = _browserEl('browserChatContextBar');
  if (!bar) return;
  const hasSession = !!(state && state.session_id);
  _browserChatContextActive = hasSession;
  bar.classList.toggle('visible', hasSession);
  if (!hasSession) return;
  const url = String(state.url || '').trim();
  var domain = url;
  try { domain = new URL(url).hostname; } catch (_) { if (!url) domain = 'about:blank'; }
  const actionCount = _browserActionTrace.length;
  bar.innerHTML = '🔍 <span class="browser-ctx-domain">' + _browserResearchEscape(domain) + '</span>' +
    (actionCount > 0 ? ' — <span class="browser-ctx-actions">' + actionCount + ' action' + (actionCount !== 1 ? 's' : '') + '</span>' : '');
}

async function _browserEnsureCurrentState() {
  const sessionId = _browserCurrentSessionId();
  if (!sessionId) return null;
  const state = _browserState;
  if (state && state.session_id === sessionId && (state.url || state.frame_rev)) {
    return state;
  }
  try {
    const synced = await browserSyncToCurrentSession({force: true, allowPending: true});
    if (synced && synced.session_id === sessionId) return synced;
  } catch (_) {}
  return (_browserState && _browserState.session_id === sessionId) ? _browserState : null;
}

function _browserComposerTextarea() {
  return _browserEl('msg')
    || _browserEl('composerTextarea')
    || _browserEl('messageInput')
    || document.querySelector('textarea');
}

function _browserIsEditableTarget(target) {
  if (!target) return false;
  if (target.isContentEditable) return true;
  const tagName = String(target.tagName || '').toUpperCase();
  if (tagName === 'INPUT' || tagName === 'TEXTAREA' || tagName === 'SELECT') return true;
  return typeof target.closest === 'function' && !!target.closest('[contenteditable="true"]');
}

function _browserInsertIntoComposer(text) {
  const textarea = _browserComposerTextarea();
  if (!textarea) return false;
  if (typeof insertAtCursor === 'function') {
    insertAtCursor(textarea, text);
  } else {
    textarea.value = (textarea.value ? textarea.value + '\n' : '') + text;
    textarea.dispatchEvent(new Event('input', {bubbles: true}));
  }
  if (typeof textarea.focus === 'function') textarea.focus();
  if (typeof textarea.setSelectionRange === 'function') {
    const pos = String(textarea.value || '').length;
    try { textarea.setSelectionRange(pos, pos); } catch (_) {}
  }
  return true;
}

function _browserVisibleUrl() {
  const stateUrl = String((_browserState && _browserState.url) || '').trim();
  if (stateUrl) return stateUrl;
  const input = _browserEl('browserUrlInput');
  const inputUrl = String(input && input.value || '').trim();
  if (inputUrl) return inputUrl;
  const drawer = _browserEl('browserDrawer');
  const text = String(drawer && drawer.innerText ? drawer.innerText : '').trim();
  if (!text) return '';
  const match = text.match(/\bhttps?:\/\/[^\s)]+|\babout:[^\s)]+/i);
  return match ? String(match[0] || '').trim() : '';
}

function browserGetState() {
  const state = _browserState ? Object.assign({}, _browserState) : {};
  const img = _browserEl('browserFrameImage');
  return {
    session_id: String(state.session_id || _browserCurrentSessionId() || ''),
    url: String(state.url || _browserVisibleUrl() || ''),
    status: String(state.status || ''),
    error: String(state.error || ''),
    busy: !!state.busy,
    can_go_back: !!state.can_go_back,
    can_go_forward: !!state.can_go_forward,
    drawer_open: !!_browserDrawerOpen,
    split: !!_browserSplitScreen,
    fullscreen: !!_browserFullscreen,
    permission_mode: String(_browserPermissionMode || 'none'),
    web_backend: String(_browserWebBackend || 'auto'),
    frame_rev: state.frame_rev == null ? null : state.frame_rev,
    frame_url: String(state.frame_url || (img && img.src) || ''),
    frame_complete: !!(img && img.complete),
    frame_width: img ? (img.naturalWidth || 0) : 0,
    frame_height: img ? (img.naturalHeight || 0) : 0,
  };
}

function _browserShouldAcceptState(nextState) {
  if (!nextState || !nextState.session_id) return false;
  const current = _browserState;
  if (!current || current.session_id !== nextState.session_id) return true;
  const currentUpdated = Number(current.updated_at || 0);
  const nextUpdated = Number(nextState.updated_at || 0);
  if (Number.isFinite(currentUpdated) && Number.isFinite(nextUpdated)) {
    if (nextUpdated + 0.000001 < currentUpdated) return false;
    if (nextUpdated > currentUpdated + 0.000001) return true;
  }
  const currentRev = Number(current.frame_rev || 0);
  const nextRev = Number(nextState.frame_rev || 0);
  if (Number.isFinite(currentRev) && Number.isFinite(nextRev)) {
    if (nextRev < currentRev) return false;
    if (nextRev > currentRev) return true;
  }
  const currentRunning = String(current.status || '').toLowerCase() === 'running' || !!current.busy;
  const nextRunning = String(nextState.status || '').toLowerCase() === 'running' || !!nextState.busy;
  if (!currentRunning && nextRunning) return false;
  return true;
}

function browserGetQaState() {
  const remembered = _browserLoadRememberedTestReport();
  const report = remembered && remembered.report ? remembered.report : null;
  const reportText = String((remembered && remembered.text) || '').trim();
  const state = browserGetState();
  const fix = _browserQaActionUi(reportText, report, state, {kind: 'fix'});
  const repro = _browserQaActionUi(reportText, report, state, {kind: 'repro'});
  const scopeRisk = _browserQaScopeRisk(report, state);
  const ageMinutes = report ? _browserQaEvidenceAgeMinutes(report) : null;
  const findings = report ? _browserReportFindings(report) : [];
  const actionableLabels = report ? _browserReportActionableLabels(report) : [];
  return {
    session_id: state.session_id,
    browser_url: state.url,
    has_report: !!(report && reportText),
    report,
    report_text: reportText,
    report_url: report ? String(report.url || '') : '',
    report_status: report ? String(report.status || 'unknown') : 'missing',
    clean_pass: _browserReportIsCleanPass(report),
    busy: !!_browserQaBusy,
    stale: !!(scopeRisk && scopeRisk.risk),
    scope_risk: scopeRisk,
    age_minutes: ageMinutes,
    old_evidence: !!(report && report.qa_recorded_at_inferred) || (typeof ageMinutes === 'number' && ageMinutes >= 30),
    findings,
    actionable_labels: actionableLabels,
    actions: {
      fix: {
        enabled: !fix.disabled,
        disabled: !!fix.disabled,
        reason: fix.reason,
        label: fix.text,
        menu_label: fix.menuText,
        title: fix.title,
      },
      repro: {
        enabled: !repro.disabled,
        disabled: !!repro.disabled,
        reason: repro.reason,
        label: repro.text,
        menu_label: repro.menuText,
        title: repro.title,
      },
    },
    last_action_result: (typeof window !== 'undefined' && window.browserLastQaActionResult) ? window.browserLastQaActionResult : null,
  };
}

function browserGetAgentContext() {
  const state = browserGetState();
  const qa = browserGetQaState();
  const backendContext = _browserAgentContext && _browserAgentContext.session_id === state.session_id
    ? _browserAgentContext
    : null;
  const textOf = function(id, fallback) {
    const el = typeof document !== 'undefined' ? document.getElementById(id) : null;
    const text = String(el && el.textContent || '').trim();
    return text || fallback || '';
  };
  const approvalMode = String((typeof window !== 'undefined' && window._approvalMode) || textOf('approvalModeValue', 'manual') || 'manual').trim().toLowerCase();
  const workflowMode = textOf('workflowStatusValue', '');
  const model = textOf('modelStatusValue', '');
  const reasoningMode = textOf('reasoningModeValue', '');
  const goalState = (typeof window !== 'undefined' && window._goalState && typeof window._goalState === 'object') ? window._goalState : null;
  const goalSession = String((goalState && goalState.session_id) || '');
  const goalMatchesSession = !!goalState && (!goalSession || !state.session_id || goalSession === state.session_id);
  const activeGoal = goalMatchesSession ? {
    available: true,
    present: !!String(goalState.goal || '').trim(),
    active: String(goalState.status || 'active').toLowerCase() === 'active',
    goal: String(goalState.goal || ''),
    status: String(goalState.status || ''),
    turns_used: Number(goalState.turns_used || 0),
    max_turns: Number(goalState.max_turns || 0),
    session_id: goalSession || state.session_id || '',
  } : {
    available: true,
    present: false,
    active: false,
    session_id: state.session_id || '',
    stale_session_id: goalSession || '',
  };
  const permissionMode = String(state.permission_mode || 'none');
  const canControl = permissionMode === 'control';
  const canWatch = permissionMode === 'control' || permissionMode === 'read';
  const frameReady = !!(state.frame_complete && state.frame_width > 0 && state.frame_height > 0);
  const nextActions = [];
  if (!state.drawer_open) nextActions.push('open_browser_drawer');
  if (!canWatch) nextActions.push('request_browser_permission');
  if (!state.url || state.url === 'about:blank') nextActions.push('navigate_to_url');
  if (state.busy) nextActions.push('wait_for_browser_idle');
  if (!frameReady) nextActions.push('wait_for_rendered_frame');
  if (!qa.has_report && frameReady && !state.busy) nextActions.push('run_browser_qa');
  if (qa.has_report && qa.stale) nextActions.push('retest_current_page');
  if (qa.actions && qa.actions.fix && qa.actions.fix.enabled) nextActions.push('fix_browser_findings');
  if (qa.actions && qa.actions.repro && qa.actions.repro.enabled) nextActions.push('create_browser_repro');
  if (qa.clean_pass && qa.has_report && !qa.stale) nextActions.push('use_current_qa_as_evidence');
  const localContext = {
    session_id: state.session_id,
    browser: state,
    qa,
    permission: {
      mode: permissionMode,
      can_watch: canWatch,
      can_control: canControl,
      needs_user_approval: !canWatch,
    },
    controls: {
      approval_mode: approvalMode,
      workflow_mode: workflowMode,
      model,
      reasoning_mode: reasoningMode,
    },
    approval_mode: approvalMode,
    active_goal: activeGoal,
    expected_frame_rev: state.frame_rev == null ? null : state.frame_rev,
    rendered_frame_ready: frameReady,
    agent_can_operate: canControl && frameReady && !state.busy,
    agent_can_assess: canWatch && frameReady && !state.busy,
    next_actions: nextActions,
    generated_at: Date.now(),
  };
  if (!backendContext || typeof backendContext !== 'object') return localContext;
  return Object.assign({}, localContext, {
    backend_context: backendContext,
    browser: backendContext.browser || localContext.browser,
    permission: backendContext.permission || localContext.permission,
    approval_mode: backendContext.approval_mode || localContext.approval_mode,
    active_goal: backendContext.active_goal || localContext.active_goal,
    expected_frame_rev: backendContext.expected_frame_rev != null ? backendContext.expected_frame_rev : localContext.expected_frame_rev,
    rendered_frame_ready: backendContext.rendered_frame_ready != null ? !!backendContext.rendered_frame_ready : localContext.rendered_frame_ready,
    agent_can_operate: backendContext.agent_can_operate != null ? !!backendContext.agent_can_operate : localContext.agent_can_operate,
    agent_can_assess: backendContext.agent_can_assess != null ? !!backendContext.agent_can_assess : localContext.agent_can_assess,
    next_actions: Array.isArray(backendContext.next_actions) ? backendContext.next_actions : localContext.next_actions,
    available_actions: backendContext.available_actions || {},
    recommended_action: backendContext.recommended_action || '',
    blocked_reasons: Array.isArray(backendContext.blocked_reasons) ? backendContext.blocked_reasons : [],
    visual_findings: Array.isArray(backendContext.visual_findings) ? backendContext.visual_findings : [],
    technical_findings: Array.isArray(backendContext.technical_findings) ? backendContext.technical_findings : [],
    generated_at: backendContext.generated_at || localContext.generated_at,
  });
}

function _browserQaObservedCurrentUrl(report) {
  const reportObj = report && typeof report === 'object' ? report : {};
  const direct = String(
    reportObj.current_browser_url ||
    reportObj.currentBrowserUrl ||
    reportObj.current_url ||
    reportObj.currentUrl ||
    reportObj.visible_url ||
    reportObj.visibleUrl ||
    reportObj.browser_url ||
    reportObj.browserUrl ||
    ''
  ).trim();
  if (direct) return direct;
  return String(_browserVisibleUrl() || '').trim();
}

function _browserQaScopeRisk(report, state) {
  const reportObj = report && typeof report === 'object' ? report : {};
  const reportUrl = String(reportObj.url || '').trim();
  const currentUrl = String((state && state.url) || _browserQaObservedCurrentUrl(reportObj) || '').trim();
  if (reportUrl && currentUrl && _browserComparableQaUrl(reportUrl) !== _browserComparableQaUrl(currentUrl)) {
    return {risk: true, stale: true, unknown: false, reportUrl: reportUrl, currentUrl: currentUrl};
  }
  if (reportUrl && !currentUrl) {
    return {risk: true, stale: false, unknown: true, reportUrl: reportUrl, currentUrl: ''};
  }
  return {risk: false, stale: false, unknown: false, reportUrl: reportUrl, currentUrl: currentUrl};
}

function _browserQaRetestUi(scopeRisk, oldEvidence) {
  const risk = scopeRisk && scopeRisk.risk;
  return {
    text: risk ? 'Retest URL' : (oldEvidence ? 'Refresh' : 'Retest'),
    title: risk ? 'Retest the last QA report URL and update the QA scope before Fix/Repro.' : (oldEvidence ? 'Refresh old QA evidence for the current URL.' : 'Retest the latest QA report URL.'),
    aria: risk ? 'Retest the last QA report URL and update QA scope' : (oldEvidence ? 'Refresh old QA evidence' : 'Retest the latest QA report URL'),
    menuText: risk ? 'Retest QA URL' : (oldEvidence ? 'Refresh browser QA' : 'Retest current page'),
  };
}

function _browserApplySharedRetestUi(retestUi) {
  if (!retestUi) return;
  window.browserQaRetestMenuLabel = retestUi.menuText;
  window.browserQaRetestTitle = retestUi.title;
  window.browserQaRetestAria = retestUi.aria;
  const workflowRetestBtn = document.getElementById('workflowHeaderBrowserRetestPageAction');
  if (workflowRetestBtn) {
    workflowRetestBtn.textContent = retestUi.menuText;
    workflowRetestBtn.setAttribute('title', retestUi.title);
    workflowRetestBtn.setAttribute('aria-label', retestUi.aria);
  }
}

function _browserQaActionUi(reportText, report, state, opts) {
  opts = opts || {};
  const kind = opts.kind === 'repro' ? 'repro' : 'fix';
  const reportObj = report && typeof report === 'object' ? report : null;
  const hasReportText = !!String(reportText || '').trim();
  const hasReport = !!reportObj;
  const cleanPass = _browserReportIsCleanPass(reportObj);
  const scopeRisk = _browserQaScopeRisk(reportObj, state || _browserState);
  const age = reportObj ? _browserQaEvidenceAgeMinutes(reportObj) : null;
  const oldEvidence = !!(reportObj && reportObj.qa_recorded_at_inferred) || (typeof age === 'number' && age >= 30);
  const actionable = _browserReportActionableLabels(reportObj).length;
  const baseText = kind === 'repro' ? 'Repro' : 'Fix';
  const menuText = kind === 'repro' ? 'Create browser repro' : 'Fix browser findings';
  const noReportTitle = kind === 'repro' ? 'Run Browser QA before creating a repro brief.' : 'Run Browser QA before fixing browser findings.';
  const cleanTitle = kind === 'repro' ? 'No browser findings to reproduce. Retest if the page changed.' : 'No browser findings to fix. Retest if the page changed.';
  const staleTitle = kind === 'repro' ? 'Retest before creating a repro. QA scope is stale or the current browser URL was not observed.' : 'Retest before fixing. QA scope is stale or the current browser URL was not observed.';
  const oldTitle = kind === 'repro' ? 'Create a repro brief from old QA evidence; retest before final proof.' : 'Fix findings from old QA evidence; retest before using this as final proof.';
  const readyTitle = kind === 'repro' ? 'Create a repro brief from the latest QA report.' : 'Fix findings from the latest QA report.';
  let disabled = false;
  let text = baseText;
  let title = oldEvidence ? oldTitle : readyTitle;
  let aria = kind === 'repro' ? 'Create repro brief from the latest QA report' : 'Fix findings from the latest QA report';
  let reason = '';
  if (_browserQaBusy) {
    disabled = true;
    text = 'QA running';
    title = 'Browser QA is running. Wait for the current QA run to finish.';
    aria = title;
    reason = 'busy';
  } else if (!hasReportText || !hasReport) {
    disabled = true;
    text = baseText;
    title = noReportTitle;
    aria = noReportTitle;
    reason = 'missing';
  } else if (cleanPass) {
    disabled = true;
    text = kind === 'repro' ? 'No repro' : 'No fix';
    title = cleanTitle;
    aria = cleanTitle;
    reason = 'clean-pass';
  } else if (scopeRisk.risk) {
    disabled = true;
    text = 'Retest first';
    title = staleTitle;
    aria = staleTitle;
    reason = scopeRisk.stale ? 'stale' : 'unknown-scope';
  } else {
    text = oldEvidence ? (kind === 'repro' ? 'Repro old' : 'Fix old') : baseText;
    aria = oldEvidence
      ? (kind === 'repro' ? 'Create repro brief from old QA evidence' : 'Fix findings from old QA evidence')
      : (kind === 'repro' ? 'Create repro brief from the latest QA report' : 'Fix findings from the latest QA report');
    reason = oldEvidence ? 'old' : (actionable ? 'ready' : 'needs-review');
  }
  return {
    kind,
    disabled,
    reason,
    text,
    menuText: disabled && reason === 'clean-pass'
      ? (kind === 'repro' ? 'No browser repro needed' : 'No browser fix needed')
      : menuText,
    title,
    aria,
    cleanPass,
    scopeRisk,
    oldEvidence,
  };
}

function _browserSetActionButtonUi(button, actionUi, opts) {
  if (!button || !actionUi) return;
  opts = opts || {};
  button.disabled = !!actionUi.disabled;
  button.setAttribute('aria-disabled', actionUi.disabled ? 'true' : 'false');
  button.setAttribute('title', actionUi.title);
  button.setAttribute('aria-label', actionUi.aria);
  if (opts.tooltip) button.dataset.tooltip = actionUi.title;
  if (opts.text === 'card') button.textContent = actionUi.text;
  else if (opts.text === 'menu') button.textContent = actionUi.menuText;
}

function _browserApplySharedFixReproUi(fixUi, reproUi) {
  if (!fixUi || !reproUi) return;
  window.browserQaFixFindingsDisabled = !!fixUi.disabled;
  window.browserQaFixFindingsMenuLabel = fixUi.menuText;
  window.browserQaFixFindingsTitle = fixUi.title;
  window.browserQaFixFindingsAria = fixUi.aria;
  window.browserQaReproDisabled = !!reproUi.disabled;
  window.browserQaReproMenuLabel = reproUi.menuText;
  window.browserQaReproTitle = reproUi.title;
  window.browserQaReproAria = reproUi.aria;
  _browserSetActionButtonUi(document.getElementById('browserQaFixBtn'), fixUi, {text: 'card'});
  _browserSetActionButtonUi(document.getElementById('browserQaReproBtn'), reproUi, {text: 'card'});
  _browserSetActionButtonUi(document.getElementById('browserFixFindingsBtn'), fixUi, {tooltip: true});
  _browserSetActionButtonUi(document.getElementById('browserHeaderFixFindingsAction'), fixUi, {text: 'menu'});
  _browserSetActionButtonUi(document.getElementById('browserHeaderReproAction'), reproUi, {text: 'menu'});
  _browserSetActionButtonUi(document.getElementById('workflowHeaderBrowserFixFindingsAction'), fixUi, {text: 'menu'});
  _browserSetActionButtonUi(document.getElementById('workflowHeaderBrowserReproAction'), reproUi, {text: 'menu'});
}

function _browserCurrentQaActionUi(kind, stateOverride) {
  const remembered = _browserLoadRememberedTestReport();
  const reportText = String((remembered && remembered.text) || '').trim();
  const report = remembered && remembered.report ? remembered.report : null;
  return _browserQaActionUi(reportText, report, stateOverride || _browserState, {kind});
}

function _browserFallbackReadableText() {
  const drawer = _browserEl('browserDrawer');
  const raw = String(drawer && drawer.innerText ? drawer.innerText : '').trim();
  if (!raw) return '';
  const lines = raw
    .split('\n')
    .map(function(line) { return String(line || '').trim(); })
    .filter(Boolean);
  if (!lines.length) return '';
  const startIndex = lines.findIndex(function(line) {
    return /^https?:\/\//i.test(line) || /^about:/i.test(line);
  });
  const relevant = (startIndex >= 0 ? lines.slice(startIndex) : lines.slice()).filter(function(line) {
    if (!line) return false;
    if (/^session [A-Za-z0-9]+$/i.test(line)) return false;
    if (/^#\d+$/.test(line)) return false;
    if (line === 'WEBSEARCH' || line === 'Web auto' || line === 'IDLE' || line === 'LOADING') return false;
    if (line === 'AGENT CONTROL' || line === 'AGENT LOCKED') return false;
    if (line === 'Go' || line === 'Change detected' || line === 'snapshot') return false;
    return true;
  });
  return relevant.slice(0, 40).join('\n').trim();
}

async function browserSendScreenshotToChat() {
  const state = await _browserEnsureCurrentState();
  const url = String((state && state.url) || _browserVisibleUrl() || '').trim();
  if (!url) {
    if (typeof showToast === 'function') showToast('No browser page loaded', 2000, 'error');
    return;
  }
  if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard: true});
  const frameUrl = _browserFrameObjectUrl || '';
  const text = '📸 **Browser screenshot**\nURL: ' + url + '\n' + (frameUrl ? '![](' + frameUrl + ')' : '');
  if (!_browserInsertIntoComposer(text)) {
    if (typeof showToast === 'function') showToast('Chat composer unavailable', 2200, 'error');
    return;
  }
  if (typeof showToast === 'function') showToast('Screenshot added to chat', 2000, 'success');
}

async function browserCopyCurrentUrl() {
  const state = await _browserEnsureCurrentState();
  const url = String((state && state.url) || _browserVisibleUrl() || '').trim();
  if (!url) {
    if (typeof showToast === 'function') showToast('No browser page loaded', 2000, 'error');
    return;
  }
  const clipboard = (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) ? navigator.clipboard.writeText(url) : null;
  if (!clipboard) {
    if (typeof showToast === 'function') showToast('Copy failed', 2200, 'error');
    return;
  }
  clipboard.then(function() {
    if (typeof showToast === 'function') showToast('URL copied to clipboard', 1800, 'success');
  }).catch(function() {
    if (typeof showToast === 'function') showToast('Copy failed', 2200, 'error');
  });
}

async function browserSendPageContextToChat(opts = {}) {
  const full = !!(opts && opts.full);
  const state = await _browserEnsureCurrentState();
  const sid = _browserCurrentSessionId();
  if (!sid) {
    if (typeof showToast === 'function') showToast('No chat session selected', 2000, 'error');
    return;
  }
  const visibleUrl = _browserVisibleUrl();
  if (!visibleUrl && !(state && state.url)) {
    if (typeof showToast === 'function') showToast('No browser page loaded', 2000, 'error');
    return;
  }
  if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard: true});
  const frameUrl = _browserFrameObjectUrl || '';
  api('/api/browser/action', {
    method: 'POST',
    body: JSON.stringify({
      session_id: sid,
      action: 'snapshot',
      full,
    }),
  }).then(function(data) {
    const snapshotText = String(data && data.text || '').trim() || _browserFallbackReadableText();
    const snapshotState = data && data.state ? data.state : state;
    const pageTitle = String((snapshotState && snapshotState.title) || '').trim();
    const pageUrl = String((snapshotState && snapshotState.url) || visibleUrl || state && state.url || '').trim();
    const heading = pageTitle || pageUrl || 'Browser page';
    const lines = [
      (full ? '📘 **Full browser page context**' : '🌐 **Browser page context**'),
      'URL: ' + (pageUrl || heading),
      '',
      snapshotText || 'No readable text returned by the browser snapshot.',
    ];
    if (frameUrl) lines.push('', `![Browser screenshot](${frameUrl})`);
    const text = lines.join('\n');
    if (!_browserInsertIntoComposer(text)) {
      if (typeof showToast === 'function') showToast('Chat composer unavailable', 2200, 'error');
      return;
    }
    if (typeof showToast === 'function') showToast(full ? 'Full page context added to chat' : 'Readable page text added to chat', 2000, 'success');
  }).catch(function() {
    if (typeof showToast === 'function') showToast('Page text export failed', 2200, 'error');
  });
}

async function browserSendFullPageContextToChat() {
  return browserSendPageContextToChat({full: true});
}

let _browserLastTestReportText = '';
let _browserLastTestReport = null;
let _browserPreviousTestReportText = '';
let _browserPreviousTestReport = null;
let _browserQaBusy = false;
let _browserQaDetailsUserToggled = false;
let _browserQaFreshnessTimer = null;

function _browserQaHistoryItem(text, report) {
  const reportObj = report && typeof report === 'object' ? report : {};
  return {
    ts: Date.now(),
    url: String(reportObj.url || _browserVisibleUrl() || 'about:blank'),
    title: String(reportObj.title || ''),
    status: String(reportObj.status || 'unknown'),
    findings: _browserReportActionableLabels(reportObj).length,
    visual: _browserQaCountReportArrays(reportObj, ['visual_findings', 'visualFindings']),
    layout: _browserQaCountReportArrays(reportObj, ['layout_findings', 'layoutFindings']),
    a11y: _browserQaCountReportArrays(reportObj, ['accessibility_findings', 'accessibilityFindings']),
    console: _browserQaCountReportArrays(reportObj, ['console_events', 'consoleEvents']),
    network: _browserQaCountReportArrays(reportObj, ['network_events', 'networkEvents']),
    permission: String((reportObj.permission && reportObj.permission.mode) || 'none'),
    hasText: !!String(text || '').trim(),
  };
}

function _browserRememberQaHistory(text, report) {
  try {
    const item = _browserQaHistoryItem(text, report);
    const raw = localStorage.getItem('sidekick-browser-test-history') || '[]';
    const existing = JSON.parse(raw);
    const history = (Array.isArray(existing) ? existing : []).filter(function(entry) {
      return entry && typeof entry === 'object' && entry.url;
    });
    history.unshift(item);
    localStorage.setItem('sidekick-browser-test-history', JSON.stringify(history.slice(0, 8)));
  } catch (_) {}
}

function _browserLoadQaHistory() {
  try {
    const raw = localStorage.getItem('sidekick-browser-test-history') || '[]';
    const history = JSON.parse(raw);
    return Array.isArray(history) ? history.filter(function(entry) {
      return entry && typeof entry === 'object' && entry.url;
    }).slice(0, 8) : [];
  } catch (_) {
    return [];
  }
}

function browserClearQaReports() {
  _browserLastTestReportText = '';
  _browserLastTestReport = null;
  _browserPreviousTestReportText = '';
  _browserPreviousTestReport = null;
  _browserQaDetailsUserToggled = false;
  if (_browserQaFreshnessTimer && typeof window !== 'undefined' && typeof window.clearInterval === 'function') {
    window.clearInterval(_browserQaFreshnessTimer);
    _browserQaFreshnessTimer = null;
  }
  try {
    localStorage.removeItem('sidekick-browser-last-test-report-text');
    localStorage.removeItem('sidekick-browser-last-test-report');
    localStorage.removeItem('sidekick-browser-previous-test-report-text');
    localStorage.removeItem('sidekick-browser-previous-test-report');
    localStorage.removeItem('sidekick-browser-test-history');
  } catch (_) {}
  const card = document.getElementById('browserQaCard');
  const details = document.getElementById('browserQaDetails');
  const button = document.getElementById('browserQaDetailsBtn');
  if (details) details.hidden = true;
  if (button) button.setAttribute('aria-expanded', 'false');
  if (card) card.hidden = true;
  _browserApplySharedFixReproUi(
    _browserQaActionUi('', null, _browserState, {kind: 'fix'}),
    _browserQaActionUi('', null, _browserState, {kind: 'repro'})
  );
  _browserRefreshHeaderMenu();
  if (typeof syncWorkflowChip === 'function') syncWorkflowChip();
  if (typeof showToast === 'function') showToast('Browser QA reports cleared', 1800, 'success');
}

function _browserSetComposerText(text) {
  const textarea = _browserComposerTextarea();
  if (!textarea) return false;
  textarea.value = String(text || '');
  textarea.dispatchEvent(new Event('input', {bubbles: true}));
  if (typeof textarea.focus === 'function') textarea.focus();
  if (typeof textarea.setSelectionRange === 'function') {
    const pos = String(textarea.value || '').length;
    try { textarea.setSelectionRange(pos, pos); } catch (_) {}
  }
  return true;
}

function _browserRememberTestReport(text, report) {
  const previousText = _browserLastTestReportText;
  const previousReport = _browserLastTestReport;
  const reportForStorage = report && typeof report === 'object' ? Object.assign({}, report) : null;
  if (reportForStorage && !reportForStorage.qa_recorded_at) {
    reportForStorage.qa_recorded_at = new Date().toISOString();
  }
  if (previousText) {
    _browserPreviousTestReportText = previousText;
    _browserPreviousTestReport = previousReport || null;
  }
  _browserLastTestReport = reportForStorage || null;
  _browserLastTestReportText = _browserNormalizeStoredQaText(text, _browserLastTestReport);
  try {
    if (previousText) {
      localStorage.setItem('sidekick-browser-previous-test-report-text', previousText);
      localStorage.setItem('sidekick-browser-previous-test-report', JSON.stringify(previousReport || {}));
    }
    localStorage.setItem('sidekick-browser-last-test-report-text', _browserLastTestReportText);
    localStorage.setItem('sidekick-browser-last-test-report', JSON.stringify(_browserLastTestReport || {}));
  } catch (_) {}
  _browserRememberQaHistory(_browserLastTestReportText, _browserLastTestReport);
  _browserQaDetailsUserToggled = false;
  _browserStartQaFreshnessTimer();
  _browserRenderQaCard(_browserLastTestReportText, _browserLastTestReport);
}

function _browserLoadRememberedTestReport() {
  if (_browserLastTestReportText) {
    _browserLastTestReportText = _browserNormalizeStoredQaText(_browserLastTestReportText, _browserLastTestReport);
    return {text: _browserLastTestReportText, report: _browserLastTestReport};
  }
  try {
    const text = String(localStorage.getItem('sidekick-browser-last-test-report-text') || '').trim();
    const raw = localStorage.getItem('sidekick-browser-last-test-report') || '{}';
    const report = JSON.parse(raw);
    if (text) {
      _browserLastTestReport = report || null;
      if (_browserLastTestReport && typeof _browserLastTestReport === 'object' && !_browserLastTestReport.qa_recorded_at) {
        _browserLastTestReport.qa_recorded_at = new Date().toISOString();
        _browserLastTestReport.qa_recorded_at_inferred = true;
        try { localStorage.setItem('sidekick-browser-last-test-report', JSON.stringify(_browserLastTestReport)); } catch (_) {}
      }
      _browserLastTestReportText = _browserNormalizeStoredQaText(text, _browserLastTestReport);
      if (_browserLastTestReportText !== text) {
        try { localStorage.setItem('sidekick-browser-last-test-report-text', _browserLastTestReportText); } catch (_) {}
      }
      return {text: _browserLastTestReportText, report: _browserLastTestReport};
    }
  } catch (_) {}
  return {text: '', report: null};
}

function _browserLoadPreviousTestReport() {
  if (_browserPreviousTestReportText) {
    _browserPreviousTestReportText = _browserNormalizeStoredQaText(_browserPreviousTestReportText, _browserPreviousTestReport);
    return {text: _browserPreviousTestReportText, report: _browserPreviousTestReport};
  }
  try {
    const text = String(localStorage.getItem('sidekick-browser-previous-test-report-text') || '').trim();
    const raw = localStorage.getItem('sidekick-browser-previous-test-report') || '{}';
    const report = JSON.parse(raw);
    if (text) {
      _browserPreviousTestReport = report || null;
      _browserPreviousTestReportText = _browserNormalizeStoredQaText(text, _browserPreviousTestReport);
      if (_browserPreviousTestReportText !== text) {
        try { localStorage.setItem('sidekick-browser-previous-test-report-text', _browserPreviousTestReportText); } catch (_) {}
      }
      return {text: _browserPreviousTestReportText, report: _browserPreviousTestReport};
    }
  } catch (_) {}
  return {text: '', report: null};
}

function _browserQaStatusKey(status) {
  const raw = String(status || '').trim().toLowerCase().replace(/\s+/g, '_');
  if (raw === 'running' || raw === 'testing' || raw === 'busy') return 'running';
  if (raw === 'pass' || raw === 'passed' || raw === 'ok' || raw === 'success') return 'pass';
  if (raw === 'failed' || raw === 'fail' || raw === 'error' || raw === 'blocked') return 'failed';
  return 'needs_review';
}

function _browserQaStatusLabel(status) {
  const key = _browserQaStatusKey(status);
  if (key === 'running') return 'RUNNING';
  if (key === 'pass') return 'PASS';
  if (key === 'failed') return 'FAILED';
  return 'NEEDS REVIEW';
}

function _browserSetQaBusy(isBusy, label) {
  _browserQaBusy = !!isBusy;
  const card = document.getElementById('browserQaCard');
  if (!card) return;
  const statusEl = document.getElementById('browserQaCardStatus');
  const urlEl = document.getElementById('browserQaCardUrl');
  const buttons = [
    document.getElementById('browserQaFixBtn'),
    document.getElementById('browserQaRetestBtn'),
    document.getElementById('browserQaReproBtn'),
    document.getElementById('browserQaCopyBtn'),
    document.getElementById('browserQaDetailsBtn'),
    document.getElementById('browserQaClearBtn'),
    document.getElementById('browserTestPageBtn'),
    document.getElementById('browserRetestPageBtn'),
    document.getElementById('browserQaBtn'),
    document.getElementById('browserWebuiSmokeBtn'),
    document.getElementById('browserFixFindingsBtn'),
    document.getElementById('browserHeaderFixFindingsAction'),
    document.getElementById('browserHeaderReproAction'),
    document.getElementById('workflowHeaderBrowserFixFindingsAction'),
    document.getElementById('workflowHeaderBrowserReproAction'),
  ];
  card.hidden = false;
  card.dataset.busy = isBusy ? '1' : '0';
  if (isBusy) {
    card.dataset.status = 'running';
    if (statusEl) statusEl.textContent = 'RUNNING';
    if (urlEl) {
      urlEl.textContent = label || 'Browser QA is running...';
      urlEl.title = label || 'Browser QA is running...';
    }
  }
  buttons.forEach(function(button) {
    if (button) button.disabled = !!isBusy;
  });
}

function _browserBeginQaRun(label) {
  if (_browserQaBusy) {
    if (typeof showToast === 'function') showToast('Browser QA is already running', 1800, 'info');
    return false;
  }
  _browserSetQaBusy(true, label);
  return true;
}

function _browserFinishQaRun() {
  _browserSetQaBusy(false);
  _browserRenderRememberedQaCard();
}

function _browserQaCountReportArrays(report, keys) {
  if (!report || typeof report !== 'object') return 0;
  return keys.reduce(function(total, key) {
    const value = report[key];
    if (Array.isArray(value)) return total + value.length;
    if (value && typeof value === 'object') return total + Object.keys(value).length;
    if (typeof value === 'number' && Number.isFinite(value)) return total + Math.max(0, value);
    return total;
  }, 0);
}

function _browserHtmlEscape(value) {
  return String(value == null ? '' : value).replace(/[&<>"']/g, function(ch) {
    return ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]);
  });
}

function _browserQaMetric(label, value) {
  return '<span class="browser-qa-metric"><span>' + label + '</span><strong>' + String(value) + '</strong></span>';
}

function _browserQaList(title, items, emptyText) {
  const list = Array.isArray(items) ? items.map(function(item) {
    return String(item || '').trim();
  }).filter(Boolean).slice(0, 6) : [];
  const body = list.length
    ? '<ul>' + list.map(function(item) { return '<li>' + _browserHtmlEscape(item).slice(0, 320) + '</li>'; }).join('') + '</ul>'
    : '<div class="browser-qa-empty">' + _browserHtmlEscape(emptyText || 'None') + '</div>';
  return '<section class="browser-qa-detail-section"><h4>' + _browserHtmlEscape(title) + '</h4>' + body + '</section>';
}

function _browserQaRetestSummary(comparison) {
  if (!comparison) return '';
  const rows = [
    ['Previous', comparison.previousStatus],
    ['Current', comparison.currentStatus],
    ['Change', comparison.improved ? 'improved' : (comparison.regressed ? 'regressed' : 'unchanged')],
    ['Fixed', comparison.fixed.length],
    ['Still', comparison.stillFailing.length],
    ['New', comparison.newRisks.length],
  ];
  return '<section class="browser-qa-detail-section browser-qa-retest-section"><h4>Retest</h4>' +
    '<div class="browser-qa-retest-pills">' +
    rows.map(function(row) {
      return '<span class="browser-qa-retest-pill"><span>' + _browserHtmlEscape(row[0]) + '</span><strong>' + _browserHtmlEscape(row[1]) + '</strong></span>';
    }).join('') +
    '</div>' +
    _browserQaList('Fixed findings', comparison.fixed, 'No previous finding disappeared.').replace('browser-qa-detail-section', 'browser-qa-detail-section browser-qa-subsection') +
    _browserQaList('Still failing', comparison.stillFailing, 'No previous finding is still present.').replace('browser-qa-detail-section', 'browser-qa-detail-section browser-qa-subsection') +
    _browserQaList('New risks', comparison.newRisks, 'No new risks detected.').replace('browser-qa-detail-section', 'browser-qa-detail-section browser-qa-subsection') +
    '</section>';
}

function _browserQaNextActions(report) {
  const reportObj = report && typeof report === 'object' ? report : {};
  const status = _browserQaStatusKey(reportObj.status || '');
  const findings = _browserReportFindings(reportObj);
  const actionableCount = _browserReportActionableLabels(reportObj).length;
  const permissionMode = String((reportObj.permission && reportObj.permission.mode) || 'none');
  const visualCount = _browserQaCountReportArrays(reportObj, ['visual_findings', 'visualFindings']);
  const layoutCount = _browserQaCountReportArrays(reportObj, ['layout_findings', 'layoutFindings']);
  const a11yCount = _browserQaCountReportArrays(reportObj, ['accessibility_findings', 'accessibilityFindings']);
  const consoleCount = _browserQaCountReportArrays(reportObj, ['console_events', 'consoleEvents']);
  const networkCount = _browserQaCountReportArrays(reportObj, ['network_events', 'networkEvents']);
  const reportUrl = String(reportObj.url || '').trim();
  const currentUrl = _browserQaObservedCurrentUrl(reportObj);
  const stale = !!(reportUrl && currentUrl && _browserComparableQaUrl(reportUrl) !== _browserComparableQaUrl(currentUrl));
  const evidenceAgeMinutes = _browserQaEvidenceAgeMinutes(reportObj);
  const oldEvidence = !!reportObj.qa_recorded_at_inferred || (typeof evidenceAgeMinutes === 'number' && evidenceAgeMinutes >= 30);
  const actions = [];
  if (stale) {
    actions.push('QA scope is stale; use Retest URL or navigate back to the report URL before fixing current-page-specific issues.');
  } else if (reportUrl && !currentUrl) {
    actions.push('Current browser URL was not observed; retest before treating this report as current-page proof.');
  } else if (oldEvidence) {
    actions.push(reportObj.qa_recorded_at_inferred ? 'QA evidence uses an inferred legacy timestamp; retest before using it as final fix evidence.' : 'QA evidence is older than 30 minutes; retest before using it as final fix evidence.');
  }
  if (status === 'pass' && !actionableCount) {
    actions.push('Keep this report as passing evidence; no blocking browser issue is currently detected.');
    actions.push('If this followed a patch, use Retest evidence and Delta before claiming the fix is done.');
  } else if (status === 'pass' && actionableCount) {
    actions.push('PASS includes technical QA signals; inspect the listed console/network/layout evidence before treating it as clean.');
    actions.push('Use Repro or Fix only after confirming the signal is app-owned and reproducible.');
  } else {
    actions.push('Use Repro to create a focused bug brief before editing files.');
    actions.push('Use Fix only after identifying the likely affected files and root cause.');
  }
  if (permissionMode === 'none') {
    actions.push('Browser permission is locked; enable read/control only if the next action requires agent interaction.');
  }
  if (visualCount || layoutCount) {
    actions.push('Prioritize visual/layout evidence first; compare the live frame against the reported viewport risks.');
  }
  if (a11yCount) {
    actions.push('Review accessibility heuristics manually; fix labels, alt text, or heading structure only where semantically correct.');
  }
  if (consoleCount || networkCount) {
    actions.push('Inspect console/network entries before changing UI code; distinguish app errors from third-party noise.');
  }
  return actions.slice(0, 6);
}

function _browserQaHistoryHtml() {
  const history = _browserLoadQaHistory();
  if (!history.length) return '';
  const rows = history.map(function(entry) {
    const ts = Number(entry.ts || 0);
    const when = ts ? new Date(ts).toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) : 'unknown';
    const status = _browserQaStatusLabel(entry.status || 'unknown');
    const issues = [
      'F ' + String(entry.findings || 0),
      'V ' + String(entry.visual || 0),
      'L ' + String(entry.layout || 0),
      'A ' + String(entry.a11y || 0),
      'C ' + String(entry.console || 0),
      'N ' + String(entry.network || 0),
      'M ' + String(entry.permission || 'none'),
    ].join(' · ');
    return '<div class="browser-qa-history-row">' +
      '<span class="browser-qa-history-status" data-status="' + _browserHtmlEscape(_browserQaStatusKey(entry.status || '')) + '">' + _browserHtmlEscape(status) + '</span>' +
      '<span class="browser-qa-history-url" title="' + _browserHtmlEscape(entry.url || '') + '">' + _browserHtmlEscape(entry.url || 'about:blank') + '</span>' +
      '<span class="browser-qa-history-issues">' + _browserHtmlEscape(issues) + '</span>' +
      '<span class="browser-qa-history-time">' + _browserHtmlEscape(when) + '</span>' +
      '<button type="button" class="browser-qa-history-open" onclick="browserOpenQaHistoryUrl(\'' + _browserHtmlEscape(encodeURIComponent(entry.url || '')) + '\')">Open</button>' +
      '</div>';
  }).join('');
  return '<section class="browser-qa-detail-section browser-qa-history-section"><h4>Recent QA</h4>' + rows + '</section>';
}

function browserOpenQaHistoryUrl(encodedUrl) {
  let url = '';
  try {
    url = decodeURIComponent(String(encodedUrl || ''));
  } catch (_) {
    url = String(encodedUrl || '');
  }
  url = String(url || '').trim();
  if (!url || url === 'about:blank') {
    if (typeof showToast === 'function') showToast('No QA history URL available', 1800, 'error');
    return;
  }
  if (typeof browserNavigateUrl === 'function') {
    browserNavigateUrl(url);
    if (typeof showToast === 'function') showToast('Opening QA history URL', 1600, 'info');
  }
}

function _browserBuildQaDetailsHtml(report, reportText) {
  const reportObj = report && typeof report === 'object' ? report : {};
  const previous = _browserLoadPreviousTestReport();
  const comparison = previous && previous.report ? _browserBuildRetestComparison(previous, {text: reportText, report: reportObj}) : null;
  const screenshot = reportObj.screenshot && typeof reportObj.screenshot === 'object' ? reportObj.screenshot : {};
  const analysis = screenshot.analysis && typeof screenshot.analysis === 'object' ? screenshot.analysis : {};
  const page = reportObj.page && typeof reportObj.page === 'object' ? reportObj.page : {};
  const permission = reportObj.permission && typeof reportObj.permission === 'object' ? reportObj.permission : {};
  const findings = _browserReportFindings(reportObj);
  const visualFindings = Array.isArray(reportObj.visual_findings) ? reportObj.visual_findings : [];
  const layoutFindings = Array.isArray(reportObj.layout_findings) ? reportObj.layout_findings : [];
  const a11yFindings = Array.isArray(reportObj.accessibility_findings) ? reportObj.accessibility_findings : [];
  const consoleEvents = _browserReportItemsForKeys(reportObj, ['console_events', 'consoleEvents', 'console_errors', 'consoleErrors', 'page_errors', 'pageErrors']);
  const networkEvents = _browserReportItemsForKeys(reportObj, ['network_events', 'networkEvents', 'network_errors', 'networkErrors', 'failed_requests', 'failedRequests']);
  const layout = page.layout && typeof page.layout === 'object' ? page.layout : {};
  const accessibility = page.accessibility && typeof page.accessibility === 'object' ? page.accessibility : {};
  const consoleFindings = consoleEvents.map(function(ev) { return String(ev).slice(0, 260); });
  const networkFindings = networkEvents.map(function(ev) { return String(ev).slice(0, 260); });
  const evidence = [
    'Screenshot: ' + (screenshot.available ? 'available' : 'missing') + ' rev ' + String(screenshot.frame_rev || 0),
    'Browser permission: ' + String(permission.mode || 'none') + (permission.granted ? ' granted' : ' locked'),
    'Visual luma: ' + String(analysis.avg_luma == null ? 'unknown' : analysis.avg_luma) + ' bright=' + String(analysis.bright_ratio == null ? 'unknown' : analysis.bright_ratio) + ' dark=' + String(analysis.dark_ratio == null ? 'unknown' : analysis.dark_ratio),
    'Horizontal overflow: ' + String(layout.horizontalOverflowPx || 0) + 'px',
    'Fixed overlays: ' + String(Array.isArray(layout.fixedOverlays) ? layout.fixedOverlays.length : 0),
    'Offscreen interactive controls: ' + String(Array.isArray(layout.offscreenInteractive) ? layout.offscreenInteractive.length : 0),
    'Unlabeled controls: ' + String(Array.isArray(accessibility.unlabeledInteractive) ? accessibility.unlabeledInteractive.length : 0),
    'Images missing alt: ' + String(Array.isArray(accessibility.imagesMissingAlt) ? accessibility.imagesMissingAlt.length : 0),
    'Visible H1 count: ' + String(accessibility.h1Count || 0),
    'Text length: ' + String(page.text_length || 0),
    'Interactive controls: ' + String(page.interactive_count || 0),
  ];
  return [
    '<div class="browser-qa-detail-grid">',
    comparison ? _browserQaRetestSummary(comparison) : '',
    _browserQaList('Next action', _browserQaNextActions(reportObj), 'No next action available.'),
    _browserQaList('Findings', findings, 'No findings in latest report.'),
    _browserQaList('Visual', visualFindings, 'No visual screenshot risks detected.'),
    _browserQaList('Layout', layoutFindings, 'No layout overflow or overlay risks detected.'),
    _browserQaList('A11y', a11yFindings, 'No accessibility heuristic risks detected.'),
    _browserQaList('Console', consoleFindings, 'No console warnings/errors in latest report.'),
    _browserQaList('Network', networkFindings, 'No failed network events in latest report.'),
    _browserQaList('Scope', _browserQaScopeDetails(reportObj), 'Scope matches the current browser page.'),
    _browserQaList('Evidence', evidence, 'No evidence available.'),
    _browserQaHistoryHtml(),
    '</div>',
    reportText ? '<div class="browser-qa-detail-foot">Full report is stored for Copy/Fix/Retest.</div>' : '',
  ].join('');
}

function _browserComparableQaUrl(url) {
  const raw = String(url || '').trim();
  if (!raw) return '';
  try {
    const parsed = new URL(raw, window.location.origin);
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      ['cb', '_', 't', 'ts', 'cache', 'cachebuster', 'cache_buster'].forEach(function(param) {
        parsed.searchParams.delete(param);
      });
      let path = parsed.pathname || '/';
      if (path.length > 1) path = path.replace(/\/+$/, '');
      return (parsed.protocol + '//' + parsed.host + path + parsed.search).toLowerCase();
    }
    return raw.toLowerCase();
  } catch (_) {
    return raw.replace(/\/+$/, '').toLowerCase();
  }
}

function _browserQaScopeDetails(report) {
  const reportObj = report && typeof report === 'object' ? report : {};
  const reportUrl = String(reportObj.url || '').trim();
  const currentUrl = _browserQaObservedCurrentUrl(reportObj);
  const recordedAt = String(reportObj.qa_recorded_at || reportObj.timestamp || '').trim();
  const evidenceAgeMinutes = _browserQaEvidenceAgeMinutes(reportObj);
  const lines = [];
  if (recordedAt) lines.push('Recorded: ' + recordedAt + (reportObj.qa_recorded_at_inferred ? ' (inferred from load time)' : ''));
  if (typeof evidenceAgeMinutes === 'number') {
    lines.push('Evidence freshness: ' + (reportObj.qa_recorded_at_inferred ? 'unknown legacy timestamp - retest before final proof' : (evidenceAgeMinutes >= 30 ? 'old - retest before final proof' : 'current')));
  }
  if (reportUrl) lines.push('Report URL: ' + reportUrl);
  else lines.push('Report URL: not stored');
  if (currentUrl) lines.push('Current browser URL: ' + currentUrl);
  else lines.push('Current browser URL: not observed');
  if (reportUrl && currentUrl && _browserComparableQaUrl(reportUrl) !== _browserComparableQaUrl(currentUrl)) {
    lines.push('Scope status: STALE - retest or navigate to the report URL before claiming current-page fixes.');
  } else if (reportUrl && !currentUrl) {
    lines.push('Scope status: UNKNOWN - retest before claiming current-page fixes.');
  } else {
    lines.push('Scope status: current');
  }
  return lines;
}

function _browserQaRecordedLabel(report) {
  const reportObj = report && typeof report === 'object' ? report : {};
  const raw = String(reportObj.qa_recorded_at || reportObj.timestamp || '').trim();
  if (!raw) return '';
  const date = new Date(raw);
  const suffix = reportObj.qa_recorded_at_inferred ? '*' : '';
  if (!Number.isNaN(date.getTime())) {
    return date.toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'}) + suffix;
  }
  return (raw.length > 16 ? raw.slice(0, 16) : raw) + suffix;
}

function _browserQaEvidenceAgeMinutes(report) {
  const reportObj = report && typeof report === 'object' ? report : {};
  const raw = String(reportObj.qa_recorded_at || reportObj.timestamp || '').trim();
  if (!raw) return null;
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return null;
  const ageMs = Date.now() - date.getTime();
  if (ageMs < 0) return 0;
  return Math.round(ageMs / 60000);
}

function _browserRenderQaCard(text, report) {
  const card = document.getElementById('browserQaCard');
  if (!card) return;
  const reportObj = report && typeof report === 'object' ? report : {};
  const reportText = String(text || '').trim();
  if (!reportText && !Object.keys(reportObj).length) {
    _browserApplySharedFixReproUi(
      _browserQaActionUi('', null, _browserState, {kind: 'fix'}),
      _browserQaActionUi('', null, _browserState, {kind: 'repro'})
    );
    card.hidden = true;
    return;
  }
  const status = _browserQaStatusKey(reportObj.status || '');
  const reportUrl = String(reportObj.url || '').trim();
  const visibleUrl = _browserQaObservedCurrentUrl(reportObj);
  const scopeRisk = _browserQaScopeRisk(reportObj);
  const url = String(reportUrl || visibleUrl || 'about:blank').trim();
  const stale = scopeRisk.stale;
  const scopeUnknown = scopeRisk.unknown;
  const findings = _browserReportFindings(reportObj).length;
  const cleanPass = _browserReportIsCleanPass(reportObj);
  const actionableCount = _browserReportActionableLabels(reportObj).length;
  const technicalPass = status === 'pass' && !cleanPass && actionableCount > 0;
  const consoleCount = _browserQaCountReportArrays(reportObj, [
    'console_events',
    'consoleEvents',
    'console_errors',
    'consoleErrors',
    'page_errors',
    'pageErrors',
  ]);
  const networkCount = _browserQaCountReportArrays(reportObj, [
    'network_events',
    'networkEvents',
    'network_errors',
    'networkErrors',
    'failed_requests',
    'failedRequests',
  ]);
  const visualCount = _browserQaCountReportArrays(reportObj, [
    'visual_findings',
    'visualFindings',
    'visual_errors',
    'visualErrors',
  ]);
  const permissionMode = String((reportObj.permission && reportObj.permission.mode) || 'none');
  const recordedLabel = _browserQaRecordedLabel(reportObj);
  const evidenceAgeMinutes = _browserQaEvidenceAgeMinutes(reportObj);
  const oldEvidence = !!reportObj.qa_recorded_at_inferred || (typeof evidenceAgeMinutes === 'number' && evidenceAgeMinutes >= 30);
  const layoutCount = _browserQaCountReportArrays(reportObj, [
    'layout_findings',
    'layoutFindings',
    'layout_errors',
    'layoutErrors',
  ]);
  const a11yCount = _browserQaCountReportArrays(reportObj, [
    'accessibility_findings',
    'accessibilityFindings',
    'a11y_findings',
    'a11yFindings',
  ]);
  const previous = _browserLoadPreviousTestReport();
  const comparison = previous && previous.report ? _browserBuildRetestComparison(previous, {text: reportText, report: reportObj}) : null;
  const deltaLabel = comparison
    ? (comparison.improved ? 'improved' : (comparison.regressed ? 'regressed' : 'same'))
    : '';
  const statusEl = document.getElementById('browserQaCardStatus');
  const urlEl = document.getElementById('browserQaCardUrl');
  const metricsEl = document.getElementById('browserQaCardMetrics');
  const retestBtn = document.getElementById('browserQaRetestBtn');
  const toolbarRetestBtn = document.getElementById('browserRetestPageBtn');
  const fixBtn = document.getElementById('browserQaFixBtn');
  const reproBtn = document.getElementById('browserQaReproBtn');
  const copyBtn = document.getElementById('browserQaCopyBtn');
  const detailsBtn = document.getElementById('browserQaDetailsBtn');
  const clearBtn = document.getElementById('browserQaClearBtn');
  const detailsEl = document.getElementById('browserQaDetails');
  const retestUi = _browserQaRetestUi(scopeRisk, oldEvidence);
  const fixUi = _browserQaActionUi(reportText, reportObj, _browserState, {kind: 'fix'});
  const reproUi = _browserQaActionUi(reportText, reportObj, _browserState, {kind: 'repro'});
  if (stale || scopeUnknown) {
    const staleReason = stale ? 'stale' : 'unknown-scope';
    const staleFixTitle = 'Retest before fixing. QA scope is stale or the current browser URL was not observed.';
    const staleReproTitle = 'Retest before creating a repro. QA scope is stale or the current browser URL was not observed.';
    Object.assign(fixUi, {
      disabled: true,
      reason: staleReason,
      text: 'Retest first',
      title: staleFixTitle,
      aria: staleFixTitle,
      scopeRisk,
    });
    Object.assign(reproUi, {
      disabled: true,
      reason: staleReason,
      text: 'Retest first',
      title: staleReproTitle,
      aria: staleReproTitle,
      scopeRisk,
    });
  }
  _browserApplySharedRetestUi(retestUi);
  _browserApplySharedFixReproUi(fixUi, reproUi);
  card.hidden = false;
  card.dataset.status = status;
  card.dataset.stale = stale ? '1' : '0';
  card.dataset.scopeUnknown = scopeUnknown ? '1' : '0';
  card.dataset.oldEvidence = oldEvidence ? '1' : '0';
  card.dataset.technicalPass = technicalPass ? '1' : '0';
  if (statusEl) {
  const statusLabel = _browserQaStatusLabel(reportObj.status || status) + (technicalPass ? ' · SIGNALS' : '');
  statusEl.textContent = stale ? ('STALE · ' + statusLabel) : (scopeUnknown ? ('UNKNOWN SCOPE · ' + statusLabel) : (oldEvidence ? ('OLD · ' + statusLabel) : statusLabel));
  }
  if (urlEl) {
    urlEl.textContent = (stale || scopeUnknown) ? ('Last QA: ' + (url || 'about:blank')) : (url || 'about:blank');
    if (stale) {
      urlEl.title = 'Last QA URL: ' + (url || 'about:blank') + '\nCurrent browser URL: ' + visibleUrl;
    } else if (scopeUnknown) {
      urlEl.title = 'Last QA URL: ' + (url || 'about:blank') + '\nCurrent browser URL: not observed. Retest before fixing current-page issues.';
    } else {
      urlEl.title = (url || 'about:blank') + (reportObj.qa_recorded_at_inferred ? '\nRecorded timestamp inferred from load time.' : '');
    }
  }
  if (metricsEl) {
    metricsEl.innerHTML = [
      stale ? _browserQaMetric('Scope', 'stale') : '',
      scopeUnknown ? _browserQaMetric('Scope', 'unknown') : '',
      _browserQaMetric('Findings', findings),
      _browserQaMetric('Visual', visualCount),
      _browserQaMetric('Layout', layoutCount),
      _browserQaMetric('A11y', a11yCount),
      _browserQaMetric('Console', consoleCount),
      _browserQaMetric('Network', networkCount),
      _browserQaMetric('Mode', permissionMode),
      recordedLabel ? _browserQaMetric('Recorded', recordedLabel) : '',
      evidenceAgeMinutes != null ? _browserQaMetric('Age', evidenceAgeMinutes + 'm') : '',
      deltaLabel ? _browserQaMetric('Delta', deltaLabel) : '',
    ].join('');
  }
  if (retestBtn) retestBtn.disabled = !url;
  _browserSetActionButtonUi(fixBtn, fixUi, {text: 'card'});
  _browserSetActionButtonUi(reproBtn, reproUi, {text: 'card'});
  if (copyBtn) copyBtn.disabled = !reportText;
  if (detailsBtn) detailsBtn.disabled = !reportText && !Object.keys(reportObj).length;
  if (clearBtn) clearBtn.disabled = !reportText && !Object.keys(reportObj).length && !_browserLoadQaHistory().length;
  if (retestBtn) retestBtn.title = retestUi.title;
  if (toolbarRetestBtn) {
    toolbarRetestBtn.setAttribute('title', retestUi.title);
    toolbarRetestBtn.setAttribute('aria-label', retestUi.aria);
    toolbarRetestBtn.dataset.tooltip = retestUi.menuText;
  }
  if (copyBtn) copyBtn.title = stale ? 'Copy the last QA report. Report URL differs from the current browser URL.' : (oldEvidence ? 'Copy old QA evidence with scope and timestamp.' : 'Copy the latest QA report.');
  if (detailsBtn) detailsBtn.title = stale ? 'Show details for the last QA report. Report URL differs from the current browser URL.' : (oldEvidence ? 'Show old QA details with scope and timestamp.' : 'Show latest QA details.');
  if (retestBtn) retestBtn.textContent = retestUi.text;
  if (copyBtn) copyBtn.textContent = stale ? 'Copy last' : (oldEvidence ? 'Copy old' : 'Copy');
  if (detailsBtn) detailsBtn.textContent = stale ? 'Details' : (oldEvidence ? 'Details' : 'Details');
  if (retestBtn) retestBtn.setAttribute('aria-label', retestUi.aria);
  if (copyBtn) copyBtn.setAttribute('aria-label', stale ? 'Copy the last QA report' : (oldEvidence ? 'Copy old QA evidence' : 'Copy the latest QA report'));
  if (detailsBtn) detailsBtn.setAttribute('aria-label', stale ? 'Show details for the last QA report' : (oldEvidence ? 'Show old QA details' : 'Show latest QA details'));
  if (detailsEl) detailsEl.innerHTML = _browserBuildQaDetailsHtml(reportObj, reportText);
  if (detailsEl && detailsBtn && !_browserQaDetailsUserToggled && !_browserQaBusy) {
    const shouldOpen = !!reportText && status !== 'pass';
    detailsEl.hidden = !shouldOpen;
    detailsBtn.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
  }
  _browserRefreshHeaderMenu();
  if (_browserQaBusy) _browserSetQaBusy(true);
}

function _browserRenderRememberedQaCard() {
  const remembered = _browserLoadRememberedTestReport();
  if (remembered.text || remembered.report) _browserStartQaFreshnessTimer();
  _browserRenderQaCard(remembered.text, remembered.report);
}

function _browserStartQaFreshnessTimer() {
  if (_browserQaFreshnessTimer || typeof window === 'undefined' || typeof window.setInterval !== 'function') return;
  _browserQaFreshnessTimer = window.setInterval(function() {
    if (_browserLastTestReportText || _browserLastTestReport) {
      _browserRenderQaCard(_browserLastTestReportText, _browserLastTestReport);
    }
  }, 60000);
}

function browserToggleQaDetails() {
  const details = document.getElementById('browserQaDetails');
  const button = document.getElementById('browserQaDetailsBtn');
  if (!details) return;
  _browserQaDetailsUserToggled = true;
  details.hidden = !details.hidden;
  if (button) button.setAttribute('aria-expanded', details.hidden ? 'false' : 'true');
}

function _browserReportFindings(report) {
  return Array.isArray(report && report.findings) ? report.findings.map(function(item) {
    return String(item || '').trim();
  }).filter(Boolean) : [];
}

function _browserReportItemsForKeys(report, keys) {
  if (!report || typeof report !== 'object') return [];
  const items = [];
  keys.forEach(function(key) {
    if (!Array.isArray(report[key])) return;
    report[key].forEach(function(item) {
      if (item && typeof item === 'object') {
        const kind = String(item.type || item.kind || item.status || item.error || '').trim();
        const body = String(item.text || item.message || item.method || item.url || '').trim();
        const extra = item.url && body !== String(item.url).trim() ? (' ' + String(item.url).trim()) : '';
        const text = (kind ? (kind + ': ') : '') + (body || JSON.stringify(item)) + extra;
        if (text.trim()) items.push(text.trim());
        return;
      }
      const text = String(item || '').trim();
      if (text) items.push(text);
    });
  });
  return items;
}

function _browserReportActionableLabels(report) {
  if (!report || typeof report !== 'object') return [];
  const groups = [
    ['Finding', ['findings']],
    ['Visual', ['visual_findings', 'visualFindings', 'visual_errors', 'visualErrors']],
    ['Layout', ['layout_findings', 'layoutFindings', 'layout_errors', 'layoutErrors']],
    ['A11y', ['accessibility_findings', 'accessibilityFindings', 'a11y_findings', 'a11yFindings']],
    ['Console', ['console_events', 'consoleEvents', 'console_errors', 'consoleErrors', 'page_errors', 'pageErrors']],
    ['Network', ['network_events', 'networkEvents', 'network_errors', 'networkErrors', 'failed_requests', 'failedRequests']],
  ];
  const labels = [];
  groups.forEach(function(group) {
    const prefix = group[0];
    _browserReportItemsForKeys(report, group[1]).forEach(function(text) {
      if (text) labels.push(prefix + ': ' + text);
    });
  });
  return labels;
}

function _browserReportHasActionableFindings(report) {
  return _browserReportActionableLabels(report).length > 0;
}

function _browserReportIsCleanPass(report) {
  return !!(report && _browserQaStatusKey(report.status || '') === 'pass' && !_browserReportHasActionableFindings(report));
}

function _browserBuildRetestComparison(previous, current) {
  const prevReport = previous && previous.report ? previous.report : null;
  const currentReport = current && current.report ? current.report : null;
  const prevFindings = _browserReportActionableLabels(prevReport);
  const currentFindings = _browserReportActionableLabels(currentReport);
  const fixed = prevFindings.filter(function(item) { return currentFindings.indexOf(item) < 0; });
  const stillFailing = prevFindings.filter(function(item) { return currentFindings.indexOf(item) >= 0; });
  const newRisks = currentFindings.filter(function(item) { return prevFindings.indexOf(item) < 0; });
  const prevStatus = String((prevReport && prevReport.status) || 'unknown');
  const currentStatus = String((currentReport && currentReport.status) || 'unknown');
  const improved = prevStatus !== 'pass' && currentStatus === 'pass';
  const regressed = prevStatus === 'pass' && currentStatus !== 'pass';
  return {
    previousStatus: prevStatus,
    currentStatus,
    fixed,
    stillFailing,
    newRisks,
    improved,
    regressed,
  };
}

function _browserRetestComparisonText(comparison) {
  const lines = [
    'Retest result:',
    '- Previous status: ' + comparison.previousStatus,
    '- Current status: ' + comparison.currentStatus,
    '- Change: ' + (comparison.improved ? 'improved' : (comparison.regressed ? 'regressed' : 'unchanged')),
    '',
    'Fixed:',
  ];
  if (comparison.fixed.length) comparison.fixed.forEach(function(item) { lines.push('- ' + item); });
  else lines.push('- None detected by exact finding comparison.');
  lines.push('', 'Still failing:');
  if (comparison.stillFailing.length) comparison.stillFailing.forEach(function(item) { lines.push('- ' + item); });
  else lines.push('- None from previous findings.');
  lines.push('', 'New risks:');
  if (comparison.newRisks.length) comparison.newRisks.forEach(function(item) { lines.push('- ' + item); });
  else lines.push('- None detected.');
  return lines.join('\n');
}

function _browserCurrentComposerReportText() {
  const textarea = _browserComposerTextarea();
  const text = String(textarea && textarea.value || '').trim();
  if (text && text.indexOf('Browser Test Report') >= 0) return text;
  return '';
}

function _browserBuildAgentQaProtocol() {
  return [
    'Agent QA protocol:',
    '- Work evidence-first: separate observed facts, inferences, and assumptions.',
    '- Be precise and akribisch: identify the affected files before editing and explain why they are relevant.',
    '- Fix the root cause, not only the visible symptom.',
    '- If QA scope is STALE, navigate back to the report URL or run Retest before editing current-page-specific issues.',
    '- If QA scope is UNKNOWN, run Retest URL before editing current-page-specific issues.',
    '- If QA evidence is old, refresh it with Retest before using it as final proof.',
    '- Keep user control: do not perform destructive, external, or risky actions without approval.',
    '- After changes, rerun Browser QA / Retest current page and report the exact status, delta, remaining risks, and evidence.',
    '- Deepseek/GLM note: use explicit short checklists and avoid claiming success without concrete test evidence.',
  ].join('\n');
}

function _browserBuildQaScopeLines(report, state) {
  const reportUrl = String((report && report.url) || '').trim();
  const scopeRisk = _browserQaScopeRisk(report, state);
  const currentUrl = scopeRisk.currentUrl;
  const stale = scopeRisk.stale;
  const permission = report && report.permission && typeof report.permission === 'object' ? report.permission : {};
  const permissionMode = String(permission.mode || 'none');
  const permissionState = permission.granted ? 'granted' : 'locked';
  const activeGoal = report && report.active_goal && typeof report.active_goal === 'object' ? report.active_goal : null;
  const fallbackGoal = _browserActiveGoalForCurrentSession(state && state.session_id);
  const activeGoalText = String((activeGoal && activeGoal.goal) || (fallbackGoal && fallbackGoal.goal) || '').trim();
  const recordedAt = String((report && (report.qa_recorded_at || report.timestamp)) || '').trim();
  const evidenceAgeMinutes = _browserQaEvidenceAgeMinutes(report);
  const freshness = report && report.qa_recorded_at_inferred
    ? 'unknown legacy timestamp - retest before using this as final fix evidence'
    : (typeof evidenceAgeMinutes === 'number' && evidenceAgeMinutes >= 30
      ? 'old - retest before using this as final fix evidence'
      : 'current');
  const lines = [
    'QA scope:',
  ];
  if (recordedAt) lines.push('- Recorded: ' + recordedAt + (report && report.qa_recorded_at_inferred ? ' (inferred from load time)' : ''));
  lines.push(
    '- Report URL: ' + (reportUrl || 'unknown'),
    '- Current browser URL: ' + (currentUrl || 'not observed'),
    '- Scope status: ' + (stale ? 'STALE - report belongs to a different URL; navigate/retest before claiming the current page is fixed.' : (scopeRisk.unknown ? 'UNKNOWN - current browser URL was not observed; run Retest URL before current-page fixes.' : 'current')),
    '- Evidence freshness: ' + freshness,
    '- Browser approval/control: ' + permissionMode + ' (' + permissionState + ')',
    '- Active goal: ' + (activeGoalText || 'none')
  );
  return lines;
}

function _browserBuildFixFindingsPrompt(reportText, report, state) {
  const url = String((report && report.url) || (state && state.url) || _browserVisibleUrl() || '').trim();
  const status = String((report && report.status) || '').trim();
  const findings = Array.isArray(report && report.findings) ? report.findings : [];
  const intro = [
    'Nutze den letzten Browser Test Report als Evidence und schliesse den Browser-Fix-Loop.',
    '',
    'Auftrag:',
    '- Analysiere die Findings und die Evidence.',
    '- Finde die betroffenen Dateien im aktuellen Workspace.',
    '- Fixe die Root Cause, nicht nur das Symptom.',
    '- Teste dieselbe URL danach erneut mit "Test current page".',
    '- Wenn die Evidence als old markiert ist, nutze Retest/Refresh QA vor dem finalen Fix-Nachweis.',
    '- Berichte am Ende kurz mit Evidence: geaenderte Dateien, Retest-Status, verbleibende Risiken.',
    '',
    'Constraints:',
    '- User-Kontrolle behalten: nutze den Approval mode aus dem Browser Test Report als Arbeitsbedingung und keine riskanten Aktionen ohne Approval.',
    '- Persistent Goal beachten: nutze die "Active goal:" Zeile im QA scope als Zielbild und reduziere Erfolg nicht auf kleinere Checks.',
    '- Wenn der Report PASS ist, pruefe trotzdem kurz, ob es UX- oder Stabilitaetsrisiken gibt, bevor du etwas aenderst.',
    '- Wenn kein Code-Fix noetig ist, sage das klar und begruende es mit Evidence.',
    '',
    _browserBuildAgentQaProtocol(),
    '',
    _browserBuildQaScopeLines(report, state).join('\n'),
    '',
    'Target URL for this report: ' + (url || 'unknown'),
    'Report status: ' + (status || 'unknown'),
  ];
  if (findings.length) {
    intro.push('', 'Findings summary:');
    findings.slice(0, 8).forEach(function(item) {
      intro.push('- ' + String(item || '').slice(0, 240));
    });
  }
  intro.push('', 'Browser Test Report:', reportText || '(no report text available)');
  return intro.join('\n');
}

async function browserTestCurrentPageToChat() {
  if (!_browserBeginQaRun('Testing current browser page...')) return;
  let state = null;
  try {
    state = await _browserEnsureCurrentState();
  } catch (err) {
    _browserFinishQaRun();
    const message = err && (err.error || err.message) ? String(err.error || err.message) : 'Browser state unavailable';
    if (typeof showToast === 'function') showToast(message, 2400, 'error');
    return;
  }
  const sid = _browserCurrentSessionId();
  if (!sid) {
    _browserFinishQaRun();
    if (typeof showToast === 'function') showToast('No chat session selected', 2000, 'error');
    return;
  }
  const visibleUrl = _browserVisibleUrl();
  if (!visibleUrl && !(state && state.url)) {
    _browserFinishQaRun();
    if (typeof showToast === 'function') showToast('No browser page loaded', 2000, 'error');
    return;
  }
  if (typeof showToast === 'function') showToast('Testing current browser page...', 1800, 'info');
  try {
    if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard: true});
  } catch (err) {
    _browserFinishQaRun();
    const message = err && (err.error || err.message) ? String(err.error || err.message) : 'Chat panel unavailable';
    if (typeof showToast === 'function') showToast(message, 2400, 'error');
    return;
  }
  const frameUrl = _browserFrameObjectUrl || '';
  try {
    const data = await api('/api/browser/action', {
      method: 'POST',
      body: JSON.stringify({
        session_id: sid,
        action: 'test_current_page',
      }),
    });
    const text = String(data && data.text || '').trim();
    const lines = [text || '🧪 **Browser Test Report**\nStatus: NEEDS REVIEW\nNo report text returned.'];
    if (frameUrl) lines.push('', `![Browser screenshot](${frameUrl})`);
    _browserRememberTestReport(lines.join('\n'), data && data.report ? data.report : null);
    if (!_browserInsertIntoComposer(lines.join('\n'))) {
      _browserFinishQaRun();
      if (typeof showToast === 'function') showToast('Chat composer unavailable', 2200, 'error');
      return;
    }
    _browserFinishQaRun();
    const status = data && data.report && data.report.status === 'pass' ? 'success' : 'info';
    if (typeof showToast === 'function') showToast('Browser test report added to chat', 2200, status);
    return data || null;
  } catch (err) {
    const message = err && (err.error || err.message) ? String(err.error || err.message) : 'Browser page test failed';
    const fallback = [
      '🧪 **Browser Test Report**',
      'Status: FAILED',
      'URL: ' + (visibleUrl || (state && state.url) || 'about:blank'),
      '',
      'Findings:',
      '- ' + message,
      '',
      'Suggested next steps:',
      '- Check browser permission mode and retry.',
    ].join('\n');
    _browserRememberTestReport(fallback, {status: 'failed', url: visibleUrl || (state && state.url) || 'about:blank', findings: [message]});
    _browserFinishQaRun();
    if (!_browserInsertIntoComposer(fallback) && typeof showToast === 'function') showToast(message, 2400, 'error');
    else if (typeof showToast === 'function') showToast('Browser test failed; report added to chat', 2400, 'error');
    return {text: fallback, report: {status: 'failed', url: visibleUrl || (state && state.url) || 'about:blank', findings: [message]}};
  }
}

function _browserStripCleanPassFixPrompt(text) {
  let clean = String(text || '');
  clean = clean.replace(
    /\n+Fix findings prompt:\n[\s\S]*?(?=\n\n!\[Browser screenshot\]|\s*$)/,
    ''
  );
  clean = clean.replace(
    /\n+Suggested next steps:\n- If findings exist[^\n]*\n- Re-run Test current page after changes to verify the fix\./,
    '\n\nSuggested next steps:\n- Keep this PASS report as current evidence while the URL and browser state remain unchanged.'
  );
  return clean.trim();
}

function _browserNormalizeStoredQaText(text, report) {
  const raw = String(text || '').trim();
  if (!raw) return '';
  return _browserReportIsCleanPass(report) ? _browserStripCleanPassFixPrompt(raw) : raw;
}

async function _browserRunPageTest() {
  const state = await _browserEnsureCurrentState();
  const sid = _browserCurrentSessionId();
  if (!sid) throw new Error('No chat session selected');
  const visibleUrl = _browserVisibleUrl();
  if (!visibleUrl && !(state && state.url)) throw new Error('No browser page loaded');
  const frameUrl = _browserFrameObjectUrl || '';
  const data = await api('/api/browser/action', {
    method: 'POST',
    body: JSON.stringify({
      session_id: sid,
      action: 'test_current_page',
    }),
  });
  const report = data && data.report ? data.report : null;
  const text = String(data && data.text || '').trim();
  const isCleanPass = _browserReportIsCleanPass(report);
  const reportText = isCleanPass ? _browserStripCleanPassFixPrompt(text) : text;
  const lines = [reportText || '🧪 **Browser Test Report**\nStatus: NEEDS REVIEW\nNo report text returned.'];
  if (frameUrl) lines.push('', `![Browser screenshot](${frameUrl})`);
  const fullText = lines.join('\n');
  _browserRememberTestReport(fullText, report);
  return {text: fullText, report};
}

async function browserRetestCurrentPageToChat() {
  if (!_browserBeginQaRun('Retesting last browser page...')) return;
  const remembered = _browserLoadRememberedTestReport();
  const targetUrl = String((remembered.report && remembered.report.url) || '').trim();
  if (!targetUrl) {
    _browserFinishQaRun();
    if (typeof showToast === 'function') showToast('Run Test current page first', 2200, 'error');
    return;
  }
  if (typeof showToast === 'function') showToast('Retesting last browser page...', 1800, 'info');
  try {
    const currentState = await _browserEnsureCurrentState();
    const currentUrl = String((currentState && currentState.url) || _browserVisibleUrl() || '').trim();
    if (_browserComparableQaUrl(currentUrl) !== _browserComparableQaUrl(targetUrl)) {
      _browserSetStatusUrl('Retest navigating to report URL: ' + targetUrl);
      if (typeof showToast === 'function') showToast('Retest: navigating to last QA URL', 1800, 'info');
      const navigatedState = await browserNavigateUrl(targetUrl);
      const navigatedUrl = String((navigatedState && navigatedState.url) || _browserVisibleUrl() || '').trim();
      if (_browserComparableQaUrl(navigatedUrl) !== _browserComparableQaUrl(targetUrl)) {
        throw new Error('Retest did not reach the QA URL. Current: ' + (navigatedUrl || 'unknown'));
      }
    } else {
      _browserSetStatusUrl('Retest using current URL: ' + (targetUrl || currentUrl));
    }
    if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard: true});
    const previous = _browserLoadRememberedTestReport();
    const current = await _browserRunPageTest();
    const comparison = _browserBuildRetestComparison(previous, current);
    const previousScope = _browserBuildQaScopeLines(previous && previous.report ? previous.report : remembered.report, currentState);
    const text = [
      '🔁 **Browser Retest Report**',
      'URL: ' + targetUrl,
      '',
      'Scope before retest:',
      previousScope.join('\n'),
      '',
      _browserRetestComparisonText(comparison),
      '',
      'Current Browser Test Report:',
      current.text,
    ].join('\n');
    if (!_browserSetComposerText(text)) {
      _browserFinishQaRun();
      if (typeof showToast === 'function') showToast('Chat composer unavailable', 2200, 'error');
      return;
    }
    _browserFinishQaRun();
    if (typeof showToast === 'function') showToast('Retest report ready', 2200, comparison.currentStatus === 'pass' ? 'success' : 'info');
    return {text, report: current.report || null, comparison};
  } catch (err) {
    _browserFinishQaRun();
    const message = err && (err.error || err.message) ? String(err.error || err.message) : 'Retest failed';
    if (typeof showToast === 'function') showToast(message, 2400, 'error');
    return {text: '', report: {status: 'failed', url: targetUrl || 'about:blank', findings: [message]}, error: message};
  }
}

function _browserQaActionResult(ok, details) {
  const result = Object.assign({ok: !!ok, at: Date.now()}, details || {});
  try { window.browserLastQaActionResult = result; } catch (_) {}
  return result;
}

async function browserRunBrowserQaToChat() {
  if (!_browserBeginQaRun('Running browser QA...')) return _browserQaActionResult(false, {reason: 'busy'});
  const previous = _browserLoadRememberedTestReport();
  if (typeof showToast === 'function') showToast('Running browser QA...', 1800, 'info');
  try {
    if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard: true});
    const currentState = await _browserEnsureCurrentState();
    const current = await _browserRunPageTest();
    const currentReport = current && current.report ? current.report : null;
    const currentText = String(current && current.text || '').trim();
    const hasPrevious = !!(previous && previous.text && previous.report);
    const comparison = hasPrevious ? _browserBuildRetestComparison(previous, current) : null;
    const isCleanPass = _browserReportIsCleanPass(currentReport);
    const hasTechnicalPassSignals = !!(currentReport && currentReport.status === 'pass' && !isCleanPass && _browserReportActionableLabels(currentReport).length);
    const fixPrompt = isCleanPass ? '' : _browserBuildFixFindingsPrompt(currentText, currentReport, currentState);
    const nextAction = isCleanPass
      ? 'No blocking findings detected. Keep this as current evidence only while the Browser QA card is not stale or old.'
      : (hasTechnicalPassSignals
        ? 'PASS includes technical QA signals. Inspect the evidence, confirm ownership/repro, then use Fix/Repro only if it is app-owned.'
        : 'Send the Fix prompt below, then run Retest current page after the patch.');
    const lines = [
      '🧭 **Browser QA Report**',
      'URL: ' + String((currentReport && currentReport.url) || _browserVisibleUrl() || 'about:blank'),
      'Current status: ' + String((currentReport && currentReport.status) || 'unknown'),
      '',
      'Current test:',
      currentText || '(no current test report text)',
      '',
      _browserBuildQaScopeLines(currentReport, currentState).join('\n'),
    ];
    if (comparison) {
      lines.push('', 'Retest comparison:', _browserRetestComparisonText(comparison));
    } else {
      lines.push('', 'Retest comparison:', '- No previous Browser Test Report available for comparison.');
    }
    if (isCleanPass) {
      lines.push(
        '',
        'Evidence note:',
        '- This Browser QA run found no blocking findings for the scoped URL/state.',
        '- Do not treat this as final proof if the Browser QA card later becomes stale or old.',
        '',
        'Next action:',
        '- ' + nextAction
      );
    } else {
      lines.push('', _browserBuildAgentQaProtocol(), '', 'Fix prompt:', fixPrompt, '', 'Next action:', '- ' + nextAction);
    }
    const text = lines.join('\n');
    if (!_browserSetComposerText(text)) {
      _browserFinishQaRun();
      if (typeof showToast === 'function') showToast('Chat composer unavailable', 2200, 'error');
      return _browserQaActionResult(false, {
        reason: 'composer_unavailable',
        text,
        report: currentReport,
        comparison,
        clean_pass: isCleanPass,
        fix_prompt: fixPrompt,
        next_action: nextAction,
      });
    }
    _browserFinishQaRun();
    if (typeof showToast === 'function') showToast('Browser QA report ready', 2200, currentReport && currentReport.status === 'pass' ? 'success' : 'info');
    return _browserQaActionResult(true, {
      action: 'browser_qa',
      text,
      report: currentReport,
      comparison,
      clean_pass: isCleanPass,
      fix_prompt: fixPrompt,
      next_action: nextAction,
    });
  } catch (err) {
    _browserFinishQaRun();
    const message = err && (err.error || err.message) ? String(err.error || err.message) : 'Browser QA failed';
    if (typeof showToast === 'function') showToast(message, 2400, 'error');
    return _browserQaActionResult(false, {reason: 'error', error: message, report: {status: 'failed', findings: [message]}});
  }
}

function _browserCurrentWorkspaceSlug() {
  try {
    const params = new URLSearchParams(window.location.search || '');
    const workspace = String(params.get('workspace') || '').trim();
    if (workspace) return workspace;
  } catch (_) {}
  const btn = _browserEl('titlebarSpaceBtn');
  const text = btn ? String(btn.textContent || '').trim().replace(/\s+/g, ' ') : '';
  if (text) return text.replace(/^[^\w-]+/, '').trim().toLowerCase() || 'nova';
  return 'nova';
}

function _browserDefaultSwitchWorkspace(workspace) {
  const current = String(workspace || '').trim().toLowerCase();
  return current === 'sidekick' ? 'nova' : 'sidekick';
}

function _browserBuildWebuiSmokeReport(result) {
  const checks = Array.isArray(result && result.checks) ? result.checks : [];
  const failed = checks.filter(check => !(check && check.ok));
  const timings = result && result.timings ? result.timings : {};
  const artifacts = result && result.artifacts ? result.artifacts : {};
  const approvalMode = String((result && result.approval_mode) || (typeof window !== 'undefined' && window._approvalMode) || 'manual').trim().toLowerCase();
  const approvalLabel = ['manual', 'smart', 'off'].includes(approvalMode) ? approvalMode : 'manual';
  const fallbackGoal = _browserActiveGoalForCurrentSession(result && result.session_id);
  const activeGoalText = String((result && result.active_goal && result.active_goal.goal) || (fallbackGoal && fallbackGoal.goal) || '').trim();
  const lines = [
    '🧪 **WebUI Browser Smoke**',
    'Status: ' + (result && result.ok ? 'PASS' : 'FAIL'),
    'URL: ' + String((result && result.url) || window.location.href || ''),
    'Approval mode: ' + approvalLabel,
    'Active goal: ' + (activeGoalText || 'none'),
    'Return code: ' + String((result && result.returncode) ?? 'n/a'),
    'Elapsed: ' + String((result && result.elapsed_ms) ?? 'n/a') + 'ms',
    '',
    'Timings:',
    '- Load: ' + String(timings.load_ms ?? 'n/a') + 'ms',
    '- Switch workspace: ' + String(timings.switch_workspace_ms ?? 'n/a') + 'ms',
    '- Restore workspace: ' + String(timings.restore_workspace_ms ?? 'n/a') + 'ms',
    '',
    'Checks:',
  ];
  if (!checks.length) {
    lines.push('- No checks returned.');
  } else {
    checks.forEach(check => {
      const name = String((check && check.name) || 'unknown');
      lines.push('- ' + (check && check.ok ? 'PASS ' : 'FAIL ') + name);
    });
  }
  if (failed.length) {
    lines.push('', 'Failed details:');
    failed.slice(0, 8).forEach(check => {
      lines.push('- ' + String(check.name || 'unknown') + ': ' + JSON.stringify(check.detail || {}));
    });
  }
  const screenshot = artifacts.screenshot || artifacts.failure_screenshot || '';
  if (screenshot) {
    lines.push('', artifacts.failure_screenshot ? 'Failure screenshot:' : 'Visual screenshot:', String(screenshot));
  }
  if (result && result.stderr) {
    lines.push('', 'stderr:', '```text', String(result.stderr).slice(0, 2000), '```');
  }
  return lines.join('\n');
}

function _browserRenderWebuiSmokeCard(result, text) {
  const card = document.getElementById('browserQaCard');
  if (!card) return;
  const data = result && typeof result === 'object' ? result : {};
  const checks = Array.isArray(data.checks) ? data.checks : [];
  const failed = checks.filter(check => !(check && check.ok));
  const timings = data.timings && typeof data.timings === 'object' ? data.timings : {};
  const artifacts = data.artifacts && typeof data.artifacts === 'object' ? data.artifacts : {};
  const screenshot = artifacts.screenshot || artifacts.failure_screenshot || '';
  const hasEvidence = !!screenshot || !!String(text || '').trim();
  const running = !!data.running;
  const ok = !!data.ok;
  const statusEl = document.getElementById('browserQaCardStatus');
  const urlEl = document.getElementById('browserQaCardUrl');
  const metricsEl = document.getElementById('browserQaCardMetrics');
  const retestBtn = document.getElementById('browserQaRetestBtn');
  const fixBtn = document.getElementById('browserQaFixBtn');
  const reproBtn = document.getElementById('browserQaReproBtn');
  const copyBtn = document.getElementById('browserQaCopyBtn');
  const detailsBtn = document.getElementById('browserQaDetailsBtn');
  const clearBtn = document.getElementById('browserQaClearBtn');
  const detailsEl = document.getElementById('browserQaDetails');
  card.hidden = false;
  card.dataset.status = running ? 'running' : (ok ? 'pass' : 'failed');
  card.dataset.webuiSmoke = '1';
  card.dataset.stale = '0';
  card.dataset.scopeUnknown = '0';
  if (statusEl) statusEl.textContent = running ? 'WEBUI RUNNING' : (ok ? 'WEBUI PASS' : 'WEBUI FAIL');
  if (urlEl) {
    const label = 'WebUI smoke · ' + String(data.url || window.location.href || '');
    urlEl.textContent = label;
    urlEl.title = label;
  }
  if (metricsEl) {
    metricsEl.innerHTML = [
      _browserQaMetric('Checks', checks.length || 0),
      _browserQaMetric('Failed', failed.length || 0),
      _browserQaMetric('Load', (timings.load_ms ?? 'n/a') + 'ms'),
      _browserQaMetric('Switch', (timings.switch_workspace_ms ?? 'n/a') + 'ms'),
      _browserQaMetric('Restore', (timings.restore_workspace_ms ?? 'n/a') + 'ms'),
    ].join('');
  }
  if (retestBtn) {
    retestBtn.disabled = running;
    retestBtn.textContent = running ? 'Running' : 'Run smoke';
    retestBtn.title = running ? 'WebUI smoke is running' : 'Run WebUI smoke again';
    retestBtn.setAttribute('aria-label', running ? 'WebUI smoke is running' : 'Run WebUI smoke again');
    retestBtn.onclick = browserRunWebuiSmokeToChat;
  }
  if (fixBtn) {
    fixBtn.disabled = true;
    fixBtn.textContent = 'Fix';
    fixBtn.title = 'Use the generated WebUI smoke report before fixing.';
  }
  if (reproBtn) {
    reproBtn.disabled = true;
    reproBtn.textContent = 'Repro';
    reproBtn.title = 'Use the generated WebUI smoke report before creating a repro.';
  }
  if (copyBtn) {
    copyBtn.disabled = true;
    copyBtn.textContent = 'Copy';
    copyBtn.title = 'WebUI smoke report is written to the composer.';
  }
  if (clearBtn) clearBtn.disabled = false;
  if (detailsBtn) {
    detailsBtn.disabled = false;
    detailsBtn.textContent = 'Details';
    detailsBtn.setAttribute('aria-expanded', (!running && (failed.length || hasEvidence)) ? 'true' : 'false');
    detailsBtn.title = screenshot ? 'Show WebUI smoke evidence and report' : 'Show WebUI smoke report';
  }
  if (detailsEl) {
    const failedHtml = failed.length
      ? '<h4>Failed checks</h4><ul>' + failed.slice(0, 12).map(check => (
        '<li><strong>' + _browserHtmlEscape(check.name || 'unknown') + '</strong>: ' + _browserHtmlEscape(JSON.stringify(check.detail || {})) + '</li>'
      )).join('') + '</ul>'
      : '<h4>Failed checks</h4><div class="browser-qa-empty">None</div>';
    const artifactHtml = screenshot
      ? '<h4>' + (artifacts.failure_screenshot ? 'Failure screenshot' : 'Visual screenshot') + '</h4><div class="browser-qa-empty">' + _browserHtmlEscape(String(screenshot)) + '</div>'
      : '';
    detailsEl.innerHTML = failedHtml + artifactHtml + '<h4>Report</h4><pre>' + _browserHtmlEscape(String(text || '')) + '</pre>';
    detailsEl.hidden = running || !(failed.length || hasEvidence);
  }
  try {
    window.browserLastWebuiSmokeResult = data;
    window.browserLastWebuiSmokeText = String(text || '');
  } catch (_) {}
  _browserRefreshHeaderMenu();
}

async function browserRunWebuiSmokeToChat() {
  if (typeof showToast === 'function') showToast('Running WebUI smoke...', 1800, 'info');
  try {
    if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard: true});
    if (typeof browserSetDrawerOpen === 'function') browserSetDrawerOpen(true);
    const workspace = _browserCurrentWorkspaceSlug();
    const sid = _browserCurrentSessionId() || (typeof S !== 'undefined' && S.session && S.session.session_id) || '';
    _browserRenderWebuiSmokeCard({
      running: true,
      url: window.location.href || '',
      checks: [],
      timings: {},
    }, 'WebUI smoke is running...');
    const result = await api('/api/browser/webui-smoke', {
      method: 'POST',
      body: JSON.stringify({
        session_id: sid || undefined,
        workspace,
        switch_workspace: _browserDefaultSwitchWorkspace(workspace),
      }),
    });
    const text = _browserBuildWebuiSmokeReport(result);
    _browserRenderWebuiSmokeCard(result, text);
    if (!_browserSetComposerText(text)) {
      if (typeof showToast === 'function') showToast('WebUI smoke finished; composer unavailable', 2600, result && result.ok ? 'info' : 'warning');
      return _browserQaActionResult(!!(result && result.ok), {action: 'browser_webui_smoke', result, text, reason: 'composer_unavailable'});
    }
    if (typeof showToast === 'function') {
      showToast(result && result.ok ? 'WebUI smoke passed' : 'WebUI smoke failed; report ready', 2400, result && result.ok ? 'success' : 'warning');
    }
    return _browserQaActionResult(!!(result && result.ok), {action: 'browser_webui_smoke', result, text});
  } catch (err) {
    const message = err && (err.error || err.message) ? String(err.error || err.message) : 'WebUI smoke failed';
    const errorPayload = err && err.data && typeof err.data === 'object' ? err.data : null;
    if (errorPayload && (Array.isArray(errorPayload.checks) || errorPayload.returncode !== undefined || errorPayload.ok === false)) {
      const text = _browserBuildWebuiSmokeReport(errorPayload);
      _browserRenderWebuiSmokeCard(errorPayload, text);
      _browserSetComposerText(text);
      if (typeof showToast === 'function') showToast('WebUI smoke failed; report ready', 2600, 'warning');
      return _browserQaActionResult(false, {action: 'browser_webui_smoke', result: errorPayload, text, reason: 'smoke_failed'});
    }
    const fallback = {
      ok: false,
      url: window.location.href || '',
      timings: {},
      artifacts: {},
      checks: [{name: 'webui_smoke_request', ok: false, detail: {message}}],
    };
    const text = _browserBuildWebuiSmokeReport(fallback);
    _browserRenderWebuiSmokeCard(fallback, text);
    _browserSetComposerText(text);
    if (typeof showToast === 'function') showToast(message, 2600, 'error');
    return _browserQaActionResult(false, {action: 'browser_webui_smoke', reason: 'error', error: message});
  }
}

async function browserFixFindingsToChat() {
  const composerReport = _browserCurrentComposerReportText();
  const remembered = _browserLoadRememberedTestReport();
  const reportText = composerReport || remembered.text;
  const preActionUi = _browserQaActionUi(reportText, remembered.report, _browserState, {kind: 'fix'});
  if (preActionUi.disabled && (preActionUi.reason === 'missing' || preActionUi.reason === 'clean-pass' || preActionUi.reason === 'busy')) {
    if (typeof showToast === 'function') showToast(preActionUi.title, 2600, preActionUi.reason === 'clean-pass' ? 'info' : 'warning');
    return _browserQaActionResult(false, {action: 'fix_findings', reason: preActionUi.reason || 'disabled', title: preActionUi.title, report: remembered.report || null});
  }
  const state = await _browserEnsureCurrentState();
  const actionUi = _browserQaActionUi(reportText, remembered.report, state, {kind: 'fix'});
  if (actionUi.disabled) {
    if (typeof showToast === 'function') showToast(actionUi.title, 2600, actionUi.reason === 'clean-pass' ? 'info' : 'warning');
    return _browserQaActionResult(false, {action: 'fix_findings', reason: actionUi.reason || 'disabled', title: actionUi.title, report: remembered.report || null, state});
  }
  if (!reportText) {
    if (typeof showToast === 'function') showToast('Run Test current page first', 2200, 'error');
    return _browserQaActionResult(false, {action: 'fix_findings', reason: 'missing_report', report: null, state});
  }
  if (_browserReportIsCleanPass(remembered.report)) {
    if (typeof showToast === 'function') showToast('No browser findings to fix; retest if the page changed', 2400, 'info');
    return _browserQaActionResult(false, {action: 'fix_findings', reason: 'clean_pass', report: remembered.report || null, state});
  }
  const scopeRisk = _browserQaScopeRisk(remembered.report, state);
  if (scopeRisk.risk) {
    const message = scopeRisk.stale
      ? 'Retest first: QA report URL differs from the current browser URL'
      : 'Retest first: current browser URL was not observed';
    if (typeof showToast === 'function') showToast(message, 2600, 'warning');
    return _browserQaActionResult(false, {action: 'fix_findings', reason: scopeRisk.stale ? 'stale_report' : 'unobserved_url', message, report: remembered.report || null, state, scope_risk: scopeRisk});
  }
  if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard: true});
  const prompt = _browserBuildFixFindingsPrompt(reportText, remembered.report, state);
  if (!_browserSetComposerText(prompt)) {
    if (typeof showToast === 'function') showToast('Chat composer unavailable', 2200, 'error');
    return _browserQaActionResult(false, {action: 'fix_findings', reason: 'composer_unavailable', prompt, report: remembered.report || null, state, scope_risk: scopeRisk});
  }
  if (typeof showToast === 'function') showToast('Fix findings prompt ready', 2000, 'success');
  return _browserQaActionResult(true, {action: 'fix_findings', prompt, report: remembered.report || null, state, scope_risk: scopeRisk});
}

async function browserQaReproToChat() {
  const remembered = _browserLoadRememberedTestReport();
  const report = remembered && remembered.report ? remembered.report : null;
  const reportText = String((remembered && remembered.text) || '').trim();
  const actionUi = _browserQaActionUi(reportText, report, _browserState, {kind: 'repro'});
  if (actionUi.disabled) {
    if (typeof showToast === 'function') showToast(actionUi.title, 2600, actionUi.reason === 'clean-pass' ? 'info' : 'warning');
    return _browserQaActionResult(false, {action: 'qa_repro', reason: actionUi.reason || 'disabled', title: actionUi.title, report});
  }
  if (!reportText || !report) {
    if (typeof showToast === 'function') showToast('Run Browser QA first', 2200, 'error');
    return _browserQaActionResult(false, {action: 'qa_repro', reason: 'missing_report', report});
  }
  if (_browserReportIsCleanPass(report)) {
    if (typeof showToast === 'function') showToast('No browser findings to reproduce; retest if the page changed', 2400, 'info');
    return _browserQaActionResult(false, {action: 'qa_repro', reason: 'clean_pass', report});
  }
  const scopeRisk = _browserQaScopeRisk(report, _browserState);
  if (scopeRisk.risk) {
    const message = scopeRisk.stale
      ? 'Retest first: QA report URL differs from the current browser URL'
      : 'Retest first: current browser URL was not observed';
    if (typeof showToast === 'function') showToast(message, 2600, 'warning');
    return _browserQaActionResult(false, {action: 'qa_repro', reason: scopeRisk.stale ? 'stale_report' : 'unobserved_url', message, report, scope_risk: scopeRisk});
  }
  const findings = _browserReportFindings(report);
  const visual = Array.isArray(report.visual_findings) ? report.visual_findings : [];
  const layout = Array.isArray(report.layout_findings) ? report.layout_findings : [];
  const page = report.page && typeof report.page === 'object' ? report.page : {};
  const permission = report.permission && typeof report.permission === 'object' ? report.permission : {};
  const scopeLines = _browserBuildQaScopeLines(report, _browserState);
  const lines = [
    'Browser Repro Brief',
    '',
    'Target:',
    '- URL: ' + String(report.url || _browserVisibleUrl() || 'about:blank'),
    '- Status: ' + String(report.status || 'unknown'),
    '- Browser permission: ' + String(permission.mode || 'none') + (permission.granted ? ' granted' : ' locked'),
    '',
    scopeLines.join('\n'),
    '',
    'Repro steps:',
    '1. Open the URL in the Sidekick browser drawer.',
    '2. Run Browser QA / Test current page.',
    '3. Compare the visible browser frame, QA card, console/network evidence, and layout findings below.',
    '',
    'Observed findings:',
  ];
  if (findings.length) findings.slice(0, 10).forEach(function(item) { lines.push('- ' + String(item).slice(0, 260)); });
  else lines.push('- No findings in latest report.');
  lines.push('', 'Visual/Layout evidence:');
  if (visual.length) visual.slice(0, 6).forEach(function(item) { lines.push('- Visual: ' + String(item).slice(0, 220)); });
  if (layout.length) layout.slice(0, 6).forEach(function(item) { lines.push('- Layout: ' + String(item).slice(0, 220)); });
  if (!visual.length && !layout.length) lines.push('- No visual/layout risks reported.');
  lines.push(
    '',
    'Page evidence:',
    '- Text length: ' + String(page.text_length || 0),
    '- Interactive controls: ' + String(page.interactive_count || 0),
    '',
    'Expected fix loop:',
    '- Patch the root cause.',
    '- Run Retest current page.',
    '- If Evidence freshness is old, refresh QA before treating this brief as final proof.',
    '- Confirm Delta/Retest details show fixed findings and no new risks.',
    '',
    _browserBuildAgentQaProtocol()
  );
  if (typeof switchPanel === 'function') await switchPanel('chat', {bypassSettingsGuard: true});
  const text = lines.join('\n');
  if (!_browserSetComposerText(text)) {
    if (typeof showToast === 'function') showToast('Chat composer unavailable', 2200, 'error');
    return _browserQaActionResult(false, {action: 'qa_repro', reason: 'composer_unavailable', text, report, scope_risk: scopeRisk, findings});
  }
  if (typeof showToast === 'function') showToast('Browser repro brief ready', 2000, 'success');
  return _browserQaActionResult(true, {action: 'qa_repro', text, report, scope_risk: scopeRisk, findings});
}

async function browserCopyQaReport() {
  const remembered = _browserLoadRememberedTestReport();
  const report = remembered && remembered.report ? remembered.report : null;
  const rawText = String((remembered && remembered.text) || _browserCurrentComposerReportText() || '').trim();
  const text = _browserReportIsCleanPass(report) ? _browserStripCleanPassFixPrompt(rawText) : rawText;
  if (!text) {
    if (typeof showToast === 'function') showToast('Run Browser QA first', 2200, 'error');
    return;
  }
  const scopedText = [
    _browserBuildQaScopeLines(report, _browserState).join('\n'),
    '',
    text,
  ].join('\n');
  let copied = false;
  try {
    if (navigator.clipboard && typeof navigator.clipboard.writeText === 'function') {
      await navigator.clipboard.writeText(scopedText);
      copied = true;
    }
  } catch (_) {}
  try {
    const helper = document.createElement('textarea');
    helper.value = scopedText;
    helper.setAttribute('readonly', 'readonly');
    helper.style.position = 'fixed';
    helper.style.left = '-9999px';
    helper.style.top = '0';
    document.body.appendChild(helper);
    helper.focus();
    helper.select();
    helper.setSelectionRange(0, helper.value.length);
    copied = document.execCommand('copy') || copied;
    helper.remove();
  } catch (_) {}
  if (copied) {
    if (typeof showToast === 'function') showToast('Browser QA report copied', 1800, 'success');
    return;
  }
  if (_browserSetComposerText(scopedText) && typeof showToast === 'function') showToast('Clipboard blocked; report placed in composer', 2400, 'info');
  else if (typeof showToast === 'function') showToast('Could not copy Browser QA report', 2200, 'error');
}

function _browserResearchRenderQuickAnswer(text, meta = {}) {
  return _browserResearchSetQuickAnswer(text, meta);
}

function _browserResearchSetQuestions(questions, meta = {}) {
  const el = _browserEl('browserResearchQuestions');
  const key = _browserResearchStateKey();
  const list = _browserResearchNormalizeQuestions(questions, _browserResearchCurrentPrompt, !!meta.allowEmpty);
  _browserResearchQuestionsBySession[key] = list.slice();
  const state = _browserResearchGetSessionState();
  state.questions = list.slice();
  if (meta && meta.selectedDirection != null) {
    state.selectedDirection = String(meta.selectedDirection || '').trim();
    _browserResearchSelectedDirectionBySession[key] = state.selectedDirection;
  }
  if (!el) return;
  el.innerHTML = '';
  if (!list.length) {
    el.innerHTML = '<div class="browser-research-empty">No follow-up questions available.</div>';
    return;
  }
  const selected = String(_browserResearchSelectedDirectionBySession[key] || '').trim();
  list.forEach(question => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'browser-research-question-chip' + (selected && selected === question ? ' is-active' : '');
    chip.textContent = question;
    chip.addEventListener('click', function() {
      _browserResearchSelectedDirectionBySession[key] = question;
      state.selectedDirection = question;
      _browserResearchSaveSessionState();
      _browserResearchRenderQuestions(list, {sessionId: _browserResearchSessionId});
      _browserResearchSetContinueState();
    });
    el.appendChild(chip);
  });
}

function _browserResearchRenderQuestions(questions, meta = {}) {
  return _browserResearchSetQuestions(questions, meta);
}

function _browserResearchSetContinueState() {
  const btn = _browserEl('browserResearchContinueBtn');
  const key = _browserResearchStateKey();
  const hasTopic = !!String(_browserResearchCurrentPrompt || '').trim();
  const direction = String(_browserResearchSelectedDirectionBySession[key] || '').trim();
  const intakeReady = !!String(_browserResearchQuickAnswerBySession[key] || '').trim();
  if (btn) {
    btn.disabled = _browserResearchBusy || !hasTopic || !intakeReady;
    btn.textContent = _browserResearchBusy ? 'Researching…' : (direction ? 'Continue with selected direction' : 'Continue research');
  }
}

function _browserResearchRenderIntakeCard(title, copy, metaParts = []) {
  const body = _browserEl('browserResearchBody');
  if (!body) return null;
  const card = document.createElement('section');
  card.className = 'browser-research-intake-result';
  const label = document.createElement('div');
  label.className = 'browser-research-result-label';
  label.textContent = 'Quick answer';
  card.appendChild(label);
  const titleEl = document.createElement('div');
  titleEl.className = 'browser-research-result-title';
  titleEl.textContent = title || _browserResearchCurrentPrompt || 'Research intake';
  card.appendChild(titleEl);
  const copyEl = document.createElement('div');
  copyEl.className = 'browser-research-result-copy';
  copyEl.textContent = copy || 'Choose a direction to continue the research.';
  card.appendChild(copyEl);
  if (metaParts.length) {
    const meta = document.createElement('div');
    meta.className = 'browser-research-result-meta';
    metaParts.forEach(part => {
      const span = document.createElement('span');
      span.textContent = part;
      meta.appendChild(span);
    });
    card.appendChild(meta);
  }
  body.appendChild(card);
  return card;
}

function _browserResearchRenderResearchCard(title, copy, metaParts = []) {
  const body = _browserEl('browserResearchBody');
  if (!body) return null;
  const card = document.createElement('section');
  card.className = 'browser-research-research-result';
  const label = document.createElement('div');
  label.className = 'browser-research-result-label';
  label.textContent = 'Deep research';
  card.appendChild(label);
  if (title) {
    const titleEl = document.createElement('div');
    titleEl.className = 'browser-research-result-title';
    titleEl.textContent = title;
    card.appendChild(titleEl);
  }
  const copyEl = document.createElement('div');
  copyEl.className = 'browser-research-result-copy';
  if (typeof renderMd === 'function') copyEl.innerHTML = renderMd(String(copy || ''));
  else copyEl.textContent = String(copy || '');
  card.appendChild(copyEl);
  if (metaParts.length) {
    const meta = document.createElement('div');
    meta.className = 'browser-research-result-meta';
    metaParts.forEach(part => {
      const span = document.createElement('span');
      span.textContent = part;
      meta.appendChild(span);
    });
    card.appendChild(meta);
  }
  body.appendChild(card);
  return card;
}

function _browserResearchDisplayContent(msg) {
  const content = String((msg && msg.content) || '');
  if (msg && msg.display_content) return String(msg.display_content);
  if (msg && msg.research_topic) return String(msg.research_topic);
  const marker = 'Führe eine Deep-Research zu folgendem Thema durch:';
  const altMarker = 'F' + String.fromCharCode(0x00c3, 0x00bc) + 'hre eine Deep-Research zu folgendem Thema durch:';
  const matchedMarker = content.includes(marker) ? marker : (content.includes(altMarker) ? altMarker : '');
  if (matchedMarker) {
    const rest = content.slice(content.indexOf(matchedMarker) + matchedMarker.length).trim();
    const firstLine = rest.split(/\r?\n/).map(line => line.trim()).find(Boolean);
    if (firstLine) return firstLine;
  }
  return content;
}

function _browserSetSessionLabel(state) {
  const el = _browserEl('browserSessionLabel');
  if (!el) return;
  el.textContent = _browserSessionLabel(state);
}

function _browserActiveGoalForCurrentSession(sessionId) {
  const sid = String(sessionId || _browserCurrentSessionId() || '');
  const context = _browserAgentContext && (!sid || _browserAgentContext.session_id === sid)
    ? _browserAgentContext
    : null;
  const contextGoal = context && context.active_goal && typeof context.active_goal === 'object' ? context.active_goal : null;
  if (contextGoal && String(contextGoal.goal || '').trim()) return contextGoal;
  const localGoalState = (typeof window !== 'undefined' && window._goalState && typeof window._goalState === 'object') ? window._goalState : null;
  const localGoalSession = String((localGoalState && localGoalState.session_id) || '');
  const localGoalMatchesSession = !!localGoalState && (!localGoalSession || !sid || localGoalSession === sid);
  return localGoalMatchesSession ? localGoalState : null;
}

function _browserUpdateHeaderBadge() {
  const badge = _browserEl('browserStatusBadge');
  const value = _browserEl('browserStatusValue');
  if (!badge || !value) return;

  const open = !!_browserDrawerOpen;
  const mode = String(_browserPermissionMode || 'none');
  const openState = open ? 'open' : 'closed';
  const modeState = mode === 'control' ? 'control' : (mode === 'read' ? 'read' : 'locked');
  const extraStates = [];
  if (_browserExploreMode) extraStates.push('explore');
  if (_browserSplitScreen) extraStates.push('split');
  if (_browserFullscreen) extraStates.push('fullscreen');
  const agentContext = _browserAgentContext && _browserAgentContext.session_id === _browserCurrentSessionId()
    ? _browserAgentContext
    : null;
  const recommended = String((agentContext && agentContext.recommended_action) || '').trim();
  const approvalMode = String((typeof window !== 'undefined' && window._approvalMode) || (agentContext && agentContext.approval_mode) || '').trim().toLowerCase();
  const approvalLabel = ['manual', 'smart', 'off'].includes(approvalMode) ? approvalMode : '';
  const activeGoal = _browserActiveGoalForCurrentSession();
  const activeGoalText = String((activeGoal && activeGoal.goal) || '').trim();
  const activeGoalLabel = activeGoalText ? 'goal active' : '';
  const recommendedLabelMap = {
    request_read_permission: 'allow read',
    request_control_permission: 'allow control',
    navigate: 'navigate',
    wait_for_idle: 'wait',
    qa: 'run QA',
    none: 'idle',
  };
  const recommendedLabel = recommended ? (recommendedLabelMap[recommended] || recommended.replace(/_/g, ' ')) : '';
  const blockedReasons = Array.isArray(agentContext && agentContext.blocked_reasons)
    ? agentContext.blocked_reasons.map(item => String(item || '').trim()).filter(Boolean)
    : [];

  badge.classList.remove('browser-state-open', 'browser-state-closed', 'browser-state-control', 'browser-state-read', 'browser-state-locked', 'browser-state-explore', 'browser-state-split', 'browser-state-fullscreen');
  badge.classList.add('browser-state-' + openState);
  badge.classList.add('browser-state-' + modeState);
  extraStates.forEach(stateName => badge.classList.add('browser-state-' + stateName));

  value.textContent = 'browser ' + openState + ' · ' + modeState + (approvalLabel ? ' · approval ' + approvalLabel : '') + (activeGoalLabel ? ' · ' + activeGoalLabel : '') + (recommendedLabel ? ' · next: ' + recommendedLabel : '');

  const parts = [
    'Browser drawer ' + openState,
    modeState === 'control' ? 'agent control enabled' : (modeState === 'read' ? 'agent watch mode' : 'agent locked')
  ];
  if (_browserState && _browserState.status) parts.push(String(_browserState.status));
  if (approvalLabel) parts.push('approval mode ' + approvalLabel);
  if (activeGoalText) parts.push('active goal: ' + activeGoalText);
  if (recommendedLabel) parts.push('recommended next action: ' + recommendedLabel);
  if (blockedReasons.length) parts.push('blocked: ' + blockedReasons.slice(0, 4).join(', '));
  if (_browserExploreMode) parts.push('explore mode');
  if (_browserSplitScreen) parts.push('split view');
  if (_browserFullscreen) parts.push('fullscreen');
  const label = parts.join(' · ') + '. Click to toggle the drawer.';
  badge.setAttribute('title', label);
  badge.setAttribute('aria-label', label);
  _browserRefreshHeaderMenu();
  if (typeof syncWorkflowChip === 'function') syncWorkflowChip();
}

function _browserAgentAction(actionId) {
  const ctx = _browserAgentContext && _browserAgentContext.session_id === _browserCurrentSessionId()
    ? _browserAgentContext
    : null;
  const actions = ctx && ctx.available_actions && typeof ctx.available_actions === 'object'
    ? ctx.available_actions
    : {};
  return actions[actionId] && typeof actions[actionId] === 'object' ? actions[actionId] : null;
}

function _browserApplyAgentActionButton(button, actionId, fallbackText) {
  if (!button) return;
  const action = _browserAgentAction(actionId);
  const text = fallbackText || (button.textContent || '');
  const blocked = action && Array.isArray(action.blocked_reasons)
    ? action.blocked_reasons.map(item => String(item || '').trim()).filter(Boolean)
    : [];
  const permissionSteps = action && Array.isArray(action.permission_steps)
    ? action.permission_steps.map(item => String(item || '').trim()).filter(Boolean)
    : [];
  const permissionStepLabels = action && Array.isArray(action.permission_step_labels)
    ? action.permission_step_labels.map(item => String(item || '').trim()).filter(Boolean)
    : [];
  const approvalMode = String((action && action.approval_mode) || (_browserAgentContext && _browserAgentContext.approval_mode) || (typeof window !== 'undefined' && window._approvalMode) || '').trim().toLowerCase();
  const approvalLabel = ['manual', 'smart', 'off'].includes(approvalMode) ? approvalMode : '';
  const activeGoal = _browserActiveGoalForCurrentSession();
  const activeGoalText = String((activeGoal && activeGoal.goal) || '').trim();
  const available = action ? !!action.available : true;
  button.textContent = !available && blocked.length ? text + ' (blocked)' : text;
  button.dataset.agentActionAvailable = available ? '1' : '0';
  button.setAttribute('aria-disabled', available ? 'false' : 'true');
  const titleParts = [];
  if (action && action.label) titleParts.push(String(action.label));
  if (action && action.required_permission) titleParts.push('permission: ' + String(action.required_permission));
  if (approvalLabel) titleParts.push('approval: ' + approvalLabel);
  if (activeGoalText) titleParts.push('goal: ' + activeGoalText);
  const readableSteps = permissionStepLabels.length ? permissionStepLabels : permissionSteps.map(_browserPermissionStepLabel);
  if (readableSteps.length) titleParts.push('steps: ' + readableSteps.slice(0, 4).join(' -> '));
  if (blocked.length) titleParts.push('blocked: ' + blocked.slice(0, 4).join(', '));
  if (!titleParts.length) titleParts.push(text);
  button.title = titleParts.join(' · ');
  button.setAttribute('aria-label', button.title);
}

function _browserPermissionStepLabel(step) {
  const value = String(step || '').trim();
  const labels = {
    enable_browser_watch: 'Enable browser watch',
    enable_browser_control: 'Enable browser control',
    request_read_permission: 'Enable browser watch',
    request_control_permission: 'Enable browser control',
  };
  return labels[value] || value.replace(/_/g, ' ');
}

function _browserErrorData(err) {
  const data = err && err.data && typeof err.data === 'object' ? err.data : {};
  const nested = data && data.error && typeof data.error === 'object' ? data.error : {};
  return Object.assign({}, nested, data, err || {});
}

function _browserIsStaleFrameError(err) {
  const data = _browserErrorData(err);
  const code = String((data && data.code) || '').trim();
  const text = String((data && (data.error || data.message)) || '').trim();
  return code === 'browser_frame_stale' || /browser frame changed since inspection/i.test(text);
}

function _browserStaleFrameMessage(err) {
  const data = _browserErrorData(err);
  const expected = data && data.expected_frame_rev != null ? String(data.expected_frame_rev) : '';
  const current = data && data.current_frame_rev != null ? String(data.current_frame_rev) : '';
  const revs = expected || current ? ' (expected rev ' + (expected || '?') + ', current rev ' + (current || '?') + ')' : '';
  return 'Browser frame changed since inspection' + revs + '. Refresh snapshot and retry.';
}

function _browserFrameBoundControlPayload(action, payload) {
  const next = Object.assign({}, payload || {});
  const act = String(action || '').trim().toLowerCase();
  const state = _browserState || {};
  if ((act === 'click' || act === 'scroll' || act === 'move') && next.expected_frame_rev == null && state.frame_rev != null) {
    next.expected_frame_rev = state.frame_rev;
  }
  return next;
}

function _browserWarnAgentActionBlocked(actionId) {
  const action = _browserAgentAction(actionId);
  if (!action || action.available) return false;
  const blocked = Array.isArray(action.blocked_reasons)
    ? action.blocked_reasons.map(item => String(item || '').trim()).filter(Boolean)
    : [];
  const permissionSteps = Array.isArray(action.permission_steps)
    ? action.permission_steps.map(item => String(item || '').trim()).filter(Boolean)
    : [];
  const permissionStepLabels = Array.isArray(action.permission_step_labels)
    ? action.permission_step_labels.map(item => String(item || '').trim()).filter(Boolean)
    : [];
  const approvalMode = String(action.approval_mode || (_browserAgentContext && _browserAgentContext.approval_mode) || (typeof window !== 'undefined' && window._approvalMode) || '').trim().toLowerCase();
  const approvalLabel = ['manual', 'smart', 'off'].includes(approvalMode) ? approvalMode : '';
  const activeGoal = _browserActiveGoalForCurrentSession();
  const activeGoalText = String((activeGoal && activeGoal.goal) || '').trim();
  if (!blocked.length) return false;
  const label = String(action.label || actionId || 'Browser action').trim();
  const readableSteps = permissionStepLabels.length ? permissionStepLabels : permissionSteps.map(_browserPermissionStepLabel);
  const message = label + ' blocked: ' + blocked.slice(0, 4).join(', ')
    + (approvalLabel ? ' · approval: ' + approvalLabel : '')
    + (activeGoalText ? ' · goal: ' + activeGoalText : '')
    + (readableSteps.length ? ' · steps: ' + readableSteps.slice(0, 4).join(' -> ') : '');
  if (typeof showToast === 'function') showToast(message, 3200, 'warning');
  return true;
}

function _browserRefreshHeaderMenu() {
  const drawerBtn = _browserEl('browserHeaderDrawerAction');
  const permissionBtn = _browserEl('browserHeaderPermissionAction');
  const exploreBtn = _browserEl('browserHeaderExploreAction');
  const splitBtn = _browserEl('browserHeaderSplitAction');
  const fullscreenBtn = _browserEl('browserHeaderFullscreenAction');
  const backBtn = _browserEl('browserHeaderBackAction');
  const forwardBtn = _browserEl('browserHeaderForwardAction');
  const reloadBtn = _browserEl('browserHeaderReloadAction');
  const stopBtn = _browserEl('browserHeaderStopAction');
  const navigateBtn = _browserEl('browserHeaderNavigateAction');
  const newTabBtn = _browserEl('browserHeaderNewTabAction');
  const copyUrlBtn = _browserEl('browserHeaderCopyUrlAction');
  const pageContextBtn = _browserEl('browserHeaderPageContextAction');
  const fullPageContextBtn = _browserEl('browserHeaderFullPageContextAction');
  const screenshotBtn = _browserEl('browserHeaderScreenshotAction');
  const qaBtn = _browserEl('browserHeaderQaAction');
  const webuiSmokeBtn = _browserEl('browserHeaderWebuiSmokeAction');
  const testBtn = _browserEl('browserHeaderTestPageAction');
  const retestBtn = _browserEl('browserHeaderRetestPageAction');
  const fixBtn = _browserEl('browserHeaderFixFindingsAction');
  const reproBtn = _browserEl('browserHeaderReproAction');
  const menuBtn = _browserEl('browserStatusMenuBtn');
  const rememberedQa = _browserLoadRememberedTestReport();
  const qaCard = _browserEl('browserQaCard');
  const hasRenderedQaCard = !!(qaCard && !qaCard.hidden);
  const renderedScopeRisk = {
    risk: hasRenderedQaCard && (qaCard.dataset.stale === '1' || qaCard.dataset.scopeUnknown === '1'),
    stale: hasRenderedQaCard && qaCard.dataset.stale === '1',
    unknown: hasRenderedQaCard && qaCard.dataset.scopeUnknown === '1',
  };
  const rememberedScopeRisk = hasRenderedQaCard ? renderedScopeRisk : _browserQaScopeRisk(rememberedQa && rememberedQa.report ? rememberedQa.report : null, _browserState);
  const rememberedAge = rememberedQa && rememberedQa.report ? _browserQaEvidenceAgeMinutes(rememberedQa.report) : null;
  const rememberedOld = !!(rememberedQa && rememberedQa.report && rememberedQa.report.qa_recorded_at_inferred) || (typeof rememberedAge === 'number' && rememberedAge >= 30);
  const retestUi = _browserQaRetestUi(rememberedScopeRisk, rememberedOld);
  const rememberedText = String((rememberedQa && rememberedQa.text) || '').trim();
  const rememberedReport = rememberedQa && rememberedQa.report ? rememberedQa.report : null;
  const fixUi = _browserQaActionUi(rememberedText, rememberedReport, _browserState, {kind: 'fix'});
  const reproUi = _browserQaActionUi(rememberedText, rememberedReport, _browserState, {kind: 'repro'});
  if (rememberedScopeRisk.risk) {
    const staleReason = rememberedScopeRisk.stale ? 'stale' : 'unknown-scope';
    const staleFixTitle = 'Retest before fixing. QA scope is stale or the current browser URL was not observed.';
    const staleReproTitle = 'Retest before creating a repro. QA scope is stale or the current browser URL was not observed.';
    Object.assign(fixUi, {
      disabled: true,
      reason: staleReason,
      text: 'Retest first',
      title: staleFixTitle,
      aria: staleFixTitle,
      scopeRisk: rememberedScopeRisk,
    });
    Object.assign(reproUi, {
      disabled: true,
      reason: staleReason,
      text: 'Retest first',
      title: staleReproTitle,
      aria: staleReproTitle,
      scopeRisk: rememberedScopeRisk,
    });
  }
  _browserApplySharedRetestUi(retestUi);
  _browserApplySharedFixReproUi(fixUi, reproUi);

  if (drawerBtn) drawerBtn.textContent = _browserDrawerOpen ? 'Close drawer' : 'Open drawer';
  if (permissionBtn) {
    permissionBtn.textContent = _browserPermissionMode === 'control'
      ? 'Pause to watch-only'
      : (_browserPermissionMode === 'read' ? 'Enable agent control' : 'Enable browser watch');
  }
  if (exploreBtn) {
    exploreBtn.textContent = _browserExploreMode ? 'Switch to Follow mode' : 'Switch to Explore mode';
  }
  if (splitBtn) {
    splitBtn.textContent = _browserSplitScreen ? 'Exit split view' : 'Split browser and chat';
  }
  if (fullscreenBtn) {
    fullscreenBtn.textContent = _browserFullscreen ? 'Exit fullscreen' : 'Maximize browser';
  }
  if (backBtn) backBtn.textContent = 'Go back';
  if (forwardBtn) forwardBtn.textContent = 'Go forward';
  if (reloadBtn) reloadBtn.textContent = 'Reload page';
  if (stopBtn) stopBtn.textContent = 'Stop loading';
  _browserApplyAgentActionButton(navigateBtn, 'navigate', 'Navigate to URL...');
  if (newTabBtn) newTabBtn.textContent = 'Open current in new tab';
  if (copyUrlBtn) copyUrlBtn.textContent = 'Copy current URL';
  _browserApplyAgentActionButton(pageContextBtn, 'snapshot', 'Send readable page text to chat');
  _browserApplyAgentActionButton(fullPageContextBtn, 'snapshot', 'Send full page context to chat');
  _browserApplyAgentActionButton(screenshotBtn, 'snapshot', 'Send screenshot to chat');
  _browserApplyAgentActionButton(qaBtn, 'qa', 'Run browser QA');
  if (webuiSmokeBtn) webuiSmokeBtn.textContent = 'Run WebUI smoke';
  _browserApplyAgentActionButton(testBtn, 'qa', 'Test current page');
  if (retestBtn) retestBtn.textContent = retestUi.menuText;
  _browserSetActionButtonUi(fixBtn, fixUi, {text: 'menu'});
  _browserSetActionButtonUi(reproBtn, reproUi, {text: 'menu'});
  if (menuBtn) {
    menuBtn.setAttribute('aria-expanded', _browserHeaderMenuOpen ? 'true' : 'false');
  }
}

function _browserCloseHeaderMenu() {
  const menu = _browserEl('browserStatusMenu');
  const menuBtn = _browserEl('browserStatusMenuBtn');
  if (menu) menu.hidden = true;
  if (menuBtn) menuBtn.setAttribute('aria-expanded', 'false');
  if (_browserHeaderMenuOpen) {
    document.removeEventListener('click', _browserHeaderMenuOutsideClick, true);
    document.removeEventListener('keydown', _browserHeaderMenuKeydown, true);
  }
  _browserHeaderMenuOpen = false;
}

function _browserHeaderMenuOutsideClick(event) {
  if (!_browserHeaderMenuOpen) return;
  const menu = _browserEl('browserStatusMenu');
  const menuBtn = _browserEl('browserStatusMenuBtn');
  const target = event && event.target;
  if (menu && target && menu.contains(target)) return;
  if (menuBtn && target && menuBtn.contains(target)) return;
  _browserCloseHeaderMenu();
}

function _browserHeaderMenuKeydown(event) {
  if (!event) return;
  if (event.key === 'Escape') {
    event.preventDefault();
    _browserCloseHeaderMenu();
  }
}

function _browserHandleExportHotkeys(event) {
  if (!event || event.defaultPrevented) return;
  if (!_browserPanelVisible()) return;
  if (_browserHeaderMenuOpen) return;
  if (_browserIsEditableTarget(event.target)) return;
  const key = String(event.key || '').toLowerCase();
  if (key !== 'e') return;
  if (!(event.ctrlKey || event.metaKey) || !event.shiftKey) return;
  event.preventDefault();
  event.stopPropagation();
  if (event.altKey) {
    void browserSendFullPageContextToChat();
  } else {
    void browserSendPageContextToChat();
  }
}

function browserToggleHeaderMenu(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  if (event && typeof event.stopPropagation === 'function') event.stopPropagation();
  const menu = _browserEl('browserStatusMenu');
  if (!menu) return false;
  if (_browserHeaderMenuOpen) {
    _browserCloseHeaderMenu();
    return false;
  }
  if (typeof workflowCloseHeaderMenu === 'function') workflowCloseHeaderMenu();
  if (typeof closeModelDropdown === 'function') closeModelDropdown();
  if (typeof closeReasoningDropdown === 'function') closeReasoningDropdown();
  _browserRefreshHeaderMenu();
  menu.hidden = false;
  _browserHeaderMenuOpen = true;
  const menuBtn = _browserEl('browserStatusMenuBtn');
  if (menuBtn) menuBtn.setAttribute('aria-expanded', 'true');
  document.addEventListener('click', _browserHeaderMenuOutsideClick, true);
  document.addEventListener('keydown', _browserHeaderMenuKeydown, true);
  return false;
}
if (typeof window !== 'undefined') {
  window.browserToggleDrawer = browserToggleDrawer;
  window.browserToggleHeaderMenu = browserToggleHeaderMenu;
  window.browserRunHeaderAction = browserRunHeaderAction;
  window.browserTogglePermission = browserTogglePermission;
  window.browserSetDrawerOpen = browserSetDrawerOpen;
}

function browserRunHeaderAction(action) {
  _browserCloseHeaderMenu();
  switch (String(action || '')) {
    case 'drawer-toggle':
      browserToggleDrawer();
      break;
    case 'permission':
      void browserTogglePermission();
      break;
    case 'explore':
      browserToggleExploreMode();
      break;
    case 'split':
      browserToggleSplit();
      break;
    case 'fullscreen':
      browserToggleFullscreen();
      break;
    case 'screenshot':
      _browserWarnAgentActionBlocked('snapshot');
      browserSendScreenshotToChat();
      break;
    case 'qa':
    case 'browser-qa':
      _browserWarnAgentActionBlocked('qa');
      return browserRunBrowserQaToChat();
    case 'webui-smoke':
    case 'browser-webui-smoke':
      return browserRunWebuiSmokeToChat();
    case 'test-page':
    case 'test':
      _browserWarnAgentActionBlocked('qa');
      return browserTestCurrentPageToChat();
    case 'retest-page':
    case 'retest':
      return browserRetestCurrentPageToChat();
    case 'fix-findings':
    case 'fix': {
      const actionUi = _browserCurrentQaActionUi('fix');
      if (actionUi.disabled) {
        if (typeof showToast === 'function') showToast(actionUi.title, 2600, actionUi.reason === 'clean-pass' ? 'info' : 'warning');
        return _browserQaActionResult(false, {action: 'fix_findings', reason: actionUi.reason || 'disabled', title: actionUi.title});
      }
      return browserFixFindingsToChat();
    }
    case 'repro':
    case 'qa-repro': {
      const actionUi = _browserCurrentQaActionUi('repro');
      if (actionUi.disabled) {
        if (typeof showToast === 'function') showToast(actionUi.title, 2600, actionUi.reason === 'clean-pass' ? 'info' : 'warning');
        return _browserQaActionResult(false, {action: 'qa_repro', reason: actionUi.reason || 'disabled', title: actionUi.title});
      }
      return browserQaReproToChat();
    }
    case 'copyurl':
      browserCopyCurrentUrl();
      break;
    case 'pagecontext':
      _browserWarnAgentActionBlocked('snapshot');
      browserSendPageContextToChat();
      break;
    case 'fullpagecontext':
    case 'pagecontext-full':
    case 'extract':
      _browserWarnAgentActionBlocked('snapshot');
      browserSendFullPageContextToChat();
      break;
    case 'back':
      browserGoBack();
      break;
    case 'forward':
      browserGoForward();
      break;
    case 'reload':
      browserReload();
      break;
    case 'stop':
      browserStop();
      break;
    case 'navigate': {
      _browserWarnAgentActionBlocked('navigate');
      const current = (_browserState && _browserState.url) ? String(_browserState.url) : String((_browserEl('browserUrlInput') || {}).value || '');
      const next = typeof window !== 'undefined' && typeof window.prompt === 'function'
        ? window.prompt('Navigate browser to URL', current || 'https://')
        : current;
      if (!next) break;
      browserNavigateUrl(next);
      break;
    }
    case 'newtab':
      browserOpenInNewTab();
      break;
  }
  return false;
}

function browserRenderPermission(permission) {
  const mode = permission && permission.mode ? String(permission.mode) : 'none';
  _browserPermissionMode = mode;
  if (!permission || permission.persist !== false) {
    _browserPersistPermissionMode(mode);
  }
  const status = _browserEl('browserPermissionStatus');
  const granted = mode === 'control' || mode === 'read';
  if (status) {
    status.textContent = mode === 'control' ? 'Agent control' : (mode === 'read' ? 'Agent watch' : 'Agent locked');
    status.classList.toggle('is-granted', granted);
  }
  const btn = _browserEl('browserPermissionBtn');
  if (btn) {
    const nextAction = mode === 'none' ? 'Allow read' : (mode === 'read' ? 'Allow control' : 'Pause control');
    const tooltip = mode === 'none'
      ? 'Allow Nova agent to watch and run browser QA'
      : (mode === 'control'
        ? 'Pause Nova agent browser control back to watch-only'
        : 'Allow Nova agent browser control');
    btn.classList.toggle('is-active', mode === 'control');
    btn.setAttribute('aria-pressed', mode === 'control' ? 'true' : 'false');
    btn.setAttribute('aria-label', tooltip);
    btn.dataset.tooltip = tooltip;
    btn.dataset.state = mode;
    btn.dataset.nextAction = nextAction;
  }
  const stopBtn = _browserEl('browserAgentStopBtn');
  if (stopBtn) {
    const canStop = mode !== 'none';
    const stopTooltip = canStop ? 'Stop Nova agent browser handoff' : 'Agent browser control is locked';
    stopBtn.disabled = !canStop;
    stopBtn.classList.toggle('is-active', canStop);
    stopBtn.setAttribute('aria-pressed', canStop ? 'true' : 'false');
    stopBtn.setAttribute('aria-disabled', canStop ? 'false' : 'true');
    stopBtn.setAttribute('tabindex', canStop ? '0' : '-1');
    stopBtn.setAttribute('aria-label', stopTooltip);
    stopBtn.dataset.tooltip = stopTooltip;
  }
  _browserUpdateHeaderBadge();
}

function browserRenderWebBackend(status) {
  const backend = status && status.backend ? String(status.backend).trim().toLowerCase() : 'auto';
  const configuredBackend = status && status.configured_backend ? String(status.configured_backend).trim().toLowerCase() : '';
  _browserWebBackend = backend || 'auto';
  _browserWebBackendConfigured = configuredBackend;
  const el = _browserEl('browserBackendStatus');
  if (!el) return;
  const nextMode = configuredBackend === 'firecrawl' ? 'auto' : 'firecrawl';
  const tooltip = configuredBackend === 'firecrawl'
    ? 'Return web backend to auto-detect'
    : 'Pin web backend to Firecrawl';
  el.textContent = 'Web ' + _browserWebBackend;
  el.classList.toggle('is-active', _browserWebBackend === 'firecrawl');
  el.classList.toggle('is-auto', !configuredBackend);
  el.setAttribute('aria-pressed', configuredBackend === 'firecrawl' ? 'true' : 'false');
  el.setAttribute('aria-label', tooltip);
  el.dataset.tooltip = tooltip;
  el.dataset.backend = _browserWebBackend;
  el.dataset.configuredBackend = configuredBackend || 'auto';
  el.dataset.nextMode = nextMode;
  _browserUpdateHeaderBadge();
}

async function browserRefreshWebBackend() {
  try {
    const data = await api('/api/web/backend');
    browserRenderWebBackend(data || {backend: 'auto', configured_backend: ''});
  } catch (_) {
    browserRenderWebBackend({backend: 'auto', configured_backend: ''});
  }
}

async function browserToggleWebBackend() {
  const nextBackend = _browserWebBackendConfigured === 'firecrawl' ? 'auto' : 'firecrawl';
  try {
    const data = await api('/api/web/backend', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({backend: nextBackend}),
    });
    browserRenderWebBackend(data || {backend: nextBackend, configured_backend: nextBackend === 'auto' ? '' : nextBackend});
    if (typeof showToast === 'function') {
      showToast(nextBackend === 'firecrawl' ? 'Web backend pinned to Firecrawl' : 'Web backend returned to auto', 2200, nextBackend === 'firecrawl' ? 'success' : 'info');
    }
  } catch (e) {
    browserRenderWebBackend({backend: _browserWebBackend, configured_backend: _browserWebBackendConfigured});
    if (typeof showToast === 'function') showToast('Web backend update failed', 2400, 'error');
  }
  return false;
}

let _browserPermissionRefreshSid = '';
let _browserPermissionRefreshPromise = null;

async function browserRefreshPermission() {
  const sid = _browserCurrentSessionId();
  if (!sid) {
    browserRenderPermission({mode: _browserRememberedPermissionMode(), persist: false});
    return;
  }
  if (_browserPermissionRefreshPromise && _browserPermissionRefreshSid === sid) {
    return _browserPermissionRefreshPromise;
  }
  _browserPermissionRefreshSid = sid;
  _browserPermissionRefreshPromise = (async () => {
  try {
    const data = await api('/api/browser/permission?session_id=' + encodeURIComponent(sid));
    if (_browserCurrentSessionId() !== sid) return;
    browserRenderPermission(data && data.permission ? data.permission : {mode: 'none'});
  } catch (_) {
    if (_browserCurrentSessionId() !== sid) return;
    browserRenderPermission({mode: _browserRememberedPermissionMode(), persist: false});
  } finally {
    if (_browserPermissionRefreshSid === sid) {
      _browserPermissionRefreshSid = '';
      _browserPermissionRefreshPromise = null;
    }
  }
  })();
  return _browserPermissionRefreshPromise;
}

async function browserTogglePermission() {
  const sid = _browserCurrentSessionId();
  if (!sid) {
    if (typeof showToast === 'function') showToast('Open a chat before granting browser permission', 2400, 'error');
    return false;
  }
  const previousMode = _browserRememberedPermissionMode();
  const nextMode = _browserPermissionMode === 'none' ? 'read' : (_browserPermissionMode === 'read' ? 'control' : 'read');
  try {
    const data = await api('/api/browser/permission', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sid, mode: nextMode, enabled: true}),
    });
    if (_browserCurrentSessionId() !== sid) return false;
    browserRenderPermission(data && data.permission ? data.permission : {mode: 'none'});
    if (typeof showToast === 'function') {
      showToast(
        nextMode === 'control' ? 'Nova agent browser control enabled' : 'Nova agent browser watch mode enabled',
        2200,
        nextMode === 'control' ? 'success' : 'info'
      );
    }
  } catch (e) {
    if (_browserCurrentSessionId() !== sid) return false;
    browserRenderPermission({mode: previousMode, persist: false});
    if (typeof showToast === 'function') showToast('Browser permission update failed', 2400, 'error');
  }
  return false;
}

async function browserStopPermission() {
  const sid = _browserCurrentSessionId();
  if (!sid) return false;
  try {
    const data = await api('/api/browser/permission', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sid, mode: 'none', action: 'revoke'}),
    });
    if (_browserCurrentSessionId() !== sid) return false;
    browserRenderPermission(data && data.permission ? data.permission : {mode: 'none'});
    if (typeof showToast === 'function') showToast('Nova agent browser handoff stopped', 2200, 'info');
  } catch (e) {
    if (_browserCurrentSessionId() !== sid) return false;
    browserRenderPermission({mode: _browserRememberedPermissionMode(), persist: false});
    if (typeof showToast === 'function') showToast('Browser handoff stop failed', 2400, 'error');
  }
  return false;
}

function _browserSetStatusUrl(text) {
  const el = _browserEl('browserStatusUrl');
  if (el) el.textContent = text || '';
}

function _browserSetActionSummary(text) {
  const el = _browserEl('browserActionSummary');
  if (el) el.textContent = text || '';
}

function _browserResetActionTrace(sessionId = '') {
  _browserActionTrace = [];
  _browserActionTraceSessionId = String(sessionId || '');
  _browserActionTraceKey = '';
  const el = _browserEl('browserActionTrace');
  if (el) el.innerHTML = '';
}

function _browserRecordActionTrace(state) {
  const el = _browserEl('browserActionTrace');
  if (!el) return;
  const sid = String((state && state.session_id) || _browserCurrentSessionId() || '');
  if (sid && sid !== _browserActionTraceSessionId) {
    _browserResetActionTrace(sid);
  }
  const actionText = String((state && state.last_action_detail) || (state && state.last_action) || '').trim();
  const key = [sid, actionText, state && state.target_selector, state && state.target_label].join('|');
  if (!actionText || key === _browserActionTraceKey) return;
  const first = _browserActionTrace[0];
  if (first && first.text === actionText && first.meta === String((state && state.target_label) || (state && state.target_selector) || (state && state.active_element_label) || '').trim()) {
    return;
  }
  _browserActionTraceKey = key;
  const approvalMode = String((typeof window !== 'undefined' && window._approvalMode) || '').trim().toLowerCase();
  const approvalLabel = ['manual', 'smart', 'off'].includes(approvalMode) ? approvalMode : '';
  const activeGoal = _browserActiveGoalForCurrentSession(sid);
  const goalText = String((activeGoal && activeGoal.goal) || '').trim();
  const item = {
    step: _browserActionTrace.length + 1,
    status: String((state && state.status) || 'idle'),
    text: actionText,
    meta: String((state && state.target_label) || (state && state.target_selector) || (state && state.active_element_label) || '').trim(),
    frameRev: state && state.frame_rev != null ? String(state.frame_rev) : '',
    approvalMode: approvalLabel,
    activeGoal: goalText,
    frameUrl: _browserFrameObjectUrl || '',
  };
  _browserActionTrace.unshift(item);
  _browserActionTrace = _browserActionTrace.slice(0, 5);
  el.innerHTML = '';
  _browserActionTrace.forEach(entry => {
    const row = document.createElement('div');
    row.className = 'browser-trace-item' + (entry.status ? ' is-' + entry.status : '');
    if (entry.frameUrl) {
      const thumb = document.createElement('img');
      thumb.className = 'browser-trace-thumb';
      thumb.src = entry.frameUrl;
      thumb.alt = '';
      thumb.width = 40;
      thumb.height = 30;
      row.appendChild(thumb);
    }
    const step = document.createElement('div');
    step.className = 'browser-trace-step';
    step.textContent = entry.step ? ('#' + entry.step) : '·';
    const main = document.createElement('div');
    main.className = 'browser-trace-main';
    main.textContent = entry.text;
    row.appendChild(step);
    row.appendChild(main);
    const metaParts = [
      entry.meta,
      entry.frameRev ? ('rev ' + entry.frameRev) : '',
      entry.approvalMode ? ('approval ' + entry.approvalMode) : '',
      entry.activeGoal ? ('goal ' + entry.activeGoal) : '',
    ].filter(Boolean);
    if (metaParts.length) {
    const meta = document.createElement('div');
    meta.className = 'browser-trace-meta';
    meta.textContent = metaParts.join(' · ');
      row.appendChild(meta);
    }
    el.appendChild(row);
  });
}

function _browserSetTarget(state) {
  const box = _browserEl('browserTargetBox');
  const label = _browserEl('browserTargetLabel');
  if (!box) return;
  const hasTarget = !!(state && state.target_visible && state.target_width != null && state.target_height != null && state.target_x != null && state.target_y != null);
  if (!hasTarget) {
    box.classList.remove('visible');
    if (label) label.textContent = '';
    return;
  }
  const stage = _browserEl('browserStage');
  if (!stage) {
    box.classList.remove('visible');
    return;
  }
  const x = Number(state.target_x || 0) || 0;
  const y = Number(state.target_y || 0) || 0;
  const tw = Math.max(Number(state.target_width || 0) || 0, 1);
  const th = Math.max(Number(state.target_height || 0) || 0, 1);
  const point = _browserFramePointToStagePercent(state, x, y);
  if (!point || !point.frame) {
    box.classList.remove('visible');
    return;
  }
  box.style.left = point.left + '%';
  box.style.top = point.top + '%';
  box.style.width = ((tw / point.frame.viewportWidth) * point.frame.width / point.frame.stageWidth * 100) + '%';
  box.style.height = ((th / point.frame.viewportHeight) * point.frame.height / point.frame.stageHeight * 100) + '%';
  if (label) {
    label.textContent = String(state.target_label || state.target_selector || state.target_kind || '').trim();
  }
  box.classList.add('visible');
}

function _browserSetStageRatio(state) {
  const stage = _browserEl('browserStage');
  if (!stage || !state) return;
  const w = Number(state.viewport_width || 1440) || 1440;
  const h = Number(state.viewport_height || 900) || 900;
  if (w > 0 && h > 0) stage.style.aspectRatio = String(w) + ' / ' + String(h);
}

function _browserVisibleFrameRect(state) {
  const stage = _browserEl('browserStage');
  if (!stage || !state || typeof stage.getBoundingClientRect !== 'function') return null;
  const overlay = _browserEl('browserOverlay') || stage;
  const stageRect = stage.getBoundingClientRect();
  const containerRect = overlay.getBoundingClientRect();
  const containerW = Number(containerRect.width || 0);
  const containerH = Number(containerRect.height || 0);
  if (!containerW || !containerH) return null;
  const viewportW = Math.max(Number(state.viewport_width || 1440) || 1440, 1);
  const viewportH = Math.max(Number(state.viewport_height || 900) || 900, 1);
  const img = _browserEl('browserFrameImage');
  const objectFit = img && window.getComputedStyle ? String(window.getComputedStyle(img).objectFit || '') : '';
  const imgRect = img && typeof img.getBoundingClientRect === 'function' ? img.getBoundingClientRect() : stageRect;
  const imgW = Number(imgRect.width || stageRect.width || 0);
  const imgH = Number(imgRect.height || stageRect.height || 0);
  const imgLeft = Number(imgRect.left || stageRect.left || 0) - Number(containerRect.left || 0);
  const imgTop = Number(imgRect.top || stageRect.top || 0) - Number(containerRect.top || 0);
  if (objectFit !== 'contain') {
    return {
      left: imgLeft,
      top: imgTop,
      width: imgW,
      height: imgH,
      stageWidth: containerW,
      stageHeight: containerH,
      containerLeft: Number(containerRect.left || 0),
      containerTop: Number(containerRect.top || 0),
      viewportWidth: viewportW,
      viewportHeight: viewportH,
    };
  }
  const scale = Math.min(imgW / viewportW, imgH / viewportH);
  const frameW = viewportW * scale;
  const frameH = viewportH * scale;
  return {
    left: imgLeft + (imgW - frameW) / 2,
    top: imgTop + (imgH - frameH) / 2,
    width: frameW,
    height: frameH,
    stageWidth: containerW,
    stageHeight: containerH,
    containerLeft: Number(containerRect.left || 0),
    containerTop: Number(containerRect.top || 0),
    viewportWidth: viewportW,
    viewportHeight: viewportH,
  };
}

function _browserFramePointToStagePercent(state, x, y) {
  const frame = _browserVisibleFrameRect(state);
  if (!frame) return null;
  const px = frame.left + (Number(x || 0) / frame.viewportWidth) * frame.width;
  const py = frame.top + (Number(y || 0) / frame.viewportHeight) * frame.height;
  return {
    left: (px / frame.stageWidth) * 100,
    top: (py / frame.stageHeight) * 100,
    frame: frame,
  };
}

function _browserApplyFrameHitBounds(state) {
  const hitLayer = _browserEl('browserHitLayer');
  if (!hitLayer) return;
  const frame = state ? _browserVisibleFrameRect(state) : null;
  if (!frame) {
    hitLayer.style.left = '0';
    hitLayer.style.top = '0';
    hitLayer.style.width = '100%';
    hitLayer.style.height = '100%';
    hitLayer.dataset.frameBounds = 'stage';
    return;
  }
  hitLayer.style.left = frame.left + 'px';
  hitLayer.style.top = frame.top + 'px';
  hitLayer.style.width = frame.width + 'px';
  hitLayer.style.height = frame.height + 'px';
  hitLayer.dataset.frameBounds = 'frame';
}

function _browserSetCursor(state) {
  const cursor = _browserEl('browserCursor');
  if (!cursor || !state) return;
  const x = Number(state.cursor_x || 0) || 0;
  const y = Number(state.cursor_y || 0) || 0;
  const point = _browserFramePointToStagePercent(state, x, y);
  if (!point) {
    cursor.classList.remove('visible');
    return;
  }
  cursor.style.left = point.left + '%';
  cursor.style.top = point.top + '%';
  cursor.classList.toggle('visible', !!state && !!state.session_id);
}

function _browserFlashClick(state) {
  const flash = _browserEl('browserClickFlash');
  if (!flash || !state || state.click_x == null || state.click_y == null) return;
  const point = _browserFramePointToStagePercent(state, Number(state.click_x || 0) || 0, Number(state.click_y || 0) || 0);
  if (!point) return;
  flash.style.left = point.left + '%';
  flash.style.top = point.top + '%';
  flash.classList.remove('visible');
  void flash.offsetWidth;
  flash.classList.add('visible');
  clearTimeout(_browserClickFlashTimer);
  _browserClickFlashTimer = setTimeout(() => flash.classList.remove('visible'), 450);
}

function _browserSetImage(state) {
  const img = _browserEl('browserFrameImage');
  if (!img || !state) return;
  const rev = String(state.frame_rev || 0);
  const nextSrc = state.frame_url || ('/api/browser/frame?session_id=' + encodeURIComponent(state.session_id || _browserCurrentSessionId()) + '&rev=' + encodeURIComponent(rev));
  const frameRequestUrl = nextSrc + (nextSrc.includes('?') ? '&' : '?') + 'cache=' + encodeURIComponent(rev);
  if (img.dataset.rev !== rev || img.dataset.frameSrc !== frameRequestUrl) {
    img.dataset.rev = rev;
    img.dataset.frameSrc = frameRequestUrl;
    if (!img.getAttribute('src')) img.style.visibility = 'hidden';
    fetch(frameRequestUrl, {credentials:'same-origin'})
      .then(res => {
        if (!res.ok) throw new Error('browser frame request failed: ' + res.status);
        return res.blob();
      })
      .then(blob => {
        if (img.dataset.rev !== rev || img.dataset.frameSrc !== frameRequestUrl) return;
        const objectUrl = URL.createObjectURL(blob);
        if (_browserFrameObjectUrl) {
          try { URL.revokeObjectURL(_browserFrameObjectUrl); } catch (_) {}
        }
        _browserFrameObjectUrl = objectUrl;
        img.src = objectUrl;
        img.style.visibility = 'visible';
        _browserApplyFrameHitBounds(_browserState || state);
        // Trigger diff overlay if frame changed
        if (_browserPrevFrameRev && _browserPrevFrameRev !== rev) {
          _browserTriggerDiffOverlay();
        }
        _browserPrevFrameRev = rev;
      })
      .catch(err => {
        if (img.dataset.rev !== rev || img.dataset.frameSrc !== frameRequestUrl) return;
        if (!_browserFrameObjectUrl) {
          img.removeAttribute('src');
          img.style.visibility = 'hidden';
        }
        // Frame image fetches can race against session/frame refreshes. Retry
        // quietly instead of surfacing a noisy console warning for an expected
        // transient failure.
        if (!_browserSyncRetryTimer) _browserScheduleSyncRetry(1200);
      });
  }
}

function _browserRender(state, opts = {}) {
  if (!state) return;
  if (!_browserShouldAcceptState(state)) return;
  if (_browserSyncRetryTimer) {
    clearTimeout(_browserSyncRetryTimer);
    _browserSyncRetryTimer = null;
  }
  _browserState = state;
  _browserActiveSessionId = String(state.session_id || _browserCurrentSessionId() || '');
  _browserRecordActionTrace(state);
  const canGoBack = !!state.can_go_back;
  const canGoForward = !!state.can_go_forward;
  state.can_go_back = canGoBack;
  state.can_go_forward = canGoForward;
  _browserSetStageRatio(state);
  _browserApplyFrameHitBounds(state);
  _browserSetImage(state);
  _browserSetCursor(state);
  if (state.click_ts != null) _browserFlashClick(state);
  _browserSetSessionLabel(state);
  _browserUpdateHeaderBadge();
  const isBlocked = state.status === 'blocked';
  const isError = state.status === 'error';
  const isRunning = state.status === 'running' || state.busy;
  if (isBlocked) _browserSetPill('blocked', 'Blocked');
  else if (isError) _browserSetPill('error', 'Error');
  else if (isRunning) _browserSetPill('running', 'Running');
  else _browserSetPill('idle', 'Idle');
  _browserSetStatusUrl(state.error ? state.error : (state.url || 'about:blank'));
  const actionSummaryParts = [];
  if (state.last_action_detail) actionSummaryParts.push(state.last_action_detail);
  else if (state.last_action) actionSummaryParts.push('Last action: ' + state.last_action);
  if (state.ready_state) actionSummaryParts.push('ready: ' + state.ready_state);
  if (state.active_element_label) actionSummaryParts.push('focus: ' + state.active_element_label);
  if (state.scroll_x != null || state.scroll_y != null) actionSummaryParts.push('scroll: ' + Math.round(Number(state.scroll_x || 0)) + 'x' + Math.round(Number(state.scroll_y || 0)));
  _browserSetActionSummary(actionSummaryParts.join(' · '));
  _browserUpdateChatContext(state);
  _browserSetTarget(state);
  _browserSetButtonsDisabled(!state.session_id, state);
  const input = _browserEl('browserUrlInput');
  const draftFresh = _browserUrlDraft && (Date.now() - _browserUrlDraftAt) < 4000;
  if (input && document.activeElement !== input && !draftFresh && state.url) {
    input.value = state.url;
  }
  if (_browserLastTestReportText || _browserLastTestReport) {
    _browserRenderQaCard(_browserLastTestReportText, _browserLastTestReport);
  }
  const showEmpty = !state.session_id || (!state.frame_rev && !state.url);
  _browserSetEmptyVisible(showEmpty, !state.session_id ? {
    title: 'Browser not attached',
    text: 'Open a chat session to attach the browser runtime.',
  } : {
    title: 'Loading browser',
    text: 'Waiting for a browser frame from this session.',
  });
  const stage = _browserEl('browserStage');
  if (stage) {
    stage.style.opacity = state.session_id ? '1' : '.65';
  }
  if (opts.scrollIntoView && stage && typeof stage.scrollIntoView === 'function') {
    try {
      const wrap = _browserEl('browserStageWrap');
      if (wrap) {
        if (typeof wrap.scrollTo === 'function') wrap.scrollTo(0, 0);
        else {
          wrap.scrollTop = 0;
          wrap.scrollLeft = 0;
        }
      }
    } catch (_) {}
  }
}

function _browserCloseStream() {
  if (_browserEventSource) {
    try { _browserEventSource.close(); } catch (_) {}
    _browserEventSource = null;
  }
  if (_browserPollTimer) {
    clearTimeout(_browserPollTimer);
    _browserPollTimer = null;
  }
  if (_browserSyncRetryTimer) {
    clearTimeout(_browserSyncRetryTimer);
    _browserSyncRetryTimer = null;
  }
}

function _browserScheduleSyncRetry(delayMs = 1200) {
  if (_browserSyncRetryTimer) return;
  if (!_browserPanelVisible()) return;
  const sessionId = _browserCurrentSessionId();
  if (!sessionId) return;
  _browserSyncRetryTimer = setTimeout(async function retry() {
    _browserSyncRetryTimer = null;
    if (!_browserPanelVisible()) return;
    const sid = _browserCurrentSessionId();
    if (!sid) return;
    const state = _browserState;
    if (state && state.session_id === sid && (state.url || state.frame_rev)) return;
    try {
      await browserSyncToCurrentSession({force: true, allowPending: true});
    } catch (_) {}
    const nextState = _browserState;
    if (_browserPanelVisible() && !(nextState && nextState.session_id === sid && (nextState.url || nextState.frame_rev))) {
      _browserScheduleSyncRetry(Math.min(delayMs + 1000, 5000));
    }
  }, delayMs);
}

function browserPrepareSessionSwitch() {
  _browserCloseHeaderMenu();
  _browserCloseStream();
  _browserRequestRev += 1;
  _browserActiveSessionId = null;
  _browserState = null;
  _browserAgentContext = null;
  _browserAgentContextSid = '';
  _browserAgentContextPromise = null;
  _browserFrameLoaded = false;
  _browserPendingSessionSwitch = true;
  _browserResetActionTrace('');
  _browserClearViewport();
  const input = _browserEl('browserUrlInput');
  if (input) input.value = '';
  const sessionLabel = _browserEl('browserSessionLabel');
  if (sessionLabel) sessionLabel.textContent = '';
  _browserSetPill('idle', 'Loading');
  _browserSetStatusUrl('Switching session...');
  _browserSetActionSummary('');
  _browserSetEmptyVisible(true, {
    title: 'Switching browser session',
    text: 'Clearing the previous page while the new session attaches.',
  });
  _browserSetButtonsDisabled(true, null);
  browserRenderPermission({mode: _browserRememberedPermissionMode(), persist: false});
  _browserUpdateHeaderBadge();
  _browserScheduleSyncRetry(1200);
}

function browserSetDrawerOpen(open, opts = {}) {
  const nextOpen = !!open;
  const prevOpen = _browserDrawerOpen;
  _browserCloseHeaderMenu();
  _browserDrawerOpen = nextOpen;
  localStorage.setItem('sidekick-browser-drawer-open', nextOpen ? '1' : '0');
  document.body.classList.toggle('browser-drawer-open', nextOpen);
  if (!nextOpen && typeof flushPendingWorkspaceTreeRefresh === 'function') {
    setTimeout(flushPendingWorkspaceTreeRefresh, 0);
  }
  _browserSyncDrawerButton(nextOpen);
  _browserSetDrawerAccessibility(nextOpen);
  _browserUpdateHeaderBadge();
  if (nextOpen) {
    void browserRefreshWebBackend();
  }
  if (!nextOpen) {
    if (_browserSplitScreen) {
      _browserSetSplitScreen(false);
    }
    if (_browserFullscreen) {
      _browserSetFullscreen(false);
    }
    _browserCloseStream();
    const browserDrawer = _browserEl('browserDrawer');
    if (browserDrawer) {
      browserDrawer.style.left = '';
      browserDrawer.style.top = '';
      browserDrawer.style.right = '';
      browserDrawer.style.bottom = '';
      browserDrawer.style.transform = '';
      browserDrawer.classList.remove('is-dragging');
    }
    _browserDrawerDragState = null;
    if (document.activeElement && browserDrawer && browserDrawer.contains(document.activeElement)) {
      const toggle = _browserEl('btnBrowserDrawerToggle');
      if (toggle && typeof toggle.focus === 'function') toggle.focus();
    }
    if (!opts.keepViewport) {
      _browserSetEmptyVisible(false);
    }
    return;
  }
  _browserSyncDrawerFloatPosition();
  _browserScheduleSyncRetry(1200);
  if (!prevOpen || opts.force) {
    void browserSyncToCurrentSession({force: true, allowPending: true});
  } else {
    void browserSyncToCurrentSession({allowPending: true});
  }
}

function browserToggleDrawer(open) {
  _browserCloseHeaderMenu();
  const nextOpen = typeof open === 'boolean' ? open : !_browserDrawerOpen;
  browserSetDrawerOpen(nextOpen, {force: true});
}

function browserToggleSplit() {
  if (!_browserSplitScreen) {
    if (_browserFullscreen) _browserSetFullscreen(false);
    _browserSetSplitScreen(true);
    return;
  }
  _browserSetSplitScreen(false);
}

if (typeof window !== 'undefined') {
  window.browserToggleFullscreen = browserToggleFullscreen;
  window.browserToggleSplit = browserToggleSplit;
}

let _browserStateFetchSid = '';
let _browserStateFetchPromise = null;

async function browserRefreshAgentContext(sessionId) {
  const sid = String(sessionId || _browserCurrentSessionId() || '').trim();
  if (!sid) {
    _browserAgentContext = null;
    _browserAgentContextSid = '';
    return null;
  }
  if (_browserAgentContextPromise && _browserAgentContextSid === sid) {
    return _browserAgentContextPromise;
  }
  _browserAgentContextSid = sid;
  _browserAgentContextPromise = (async () => {
    try {
      const data = await api('/api/browser/agent-context?session_id=' + encodeURIComponent(sid));
      const context = data && data.context ? data.context : null;
      if (context && String(context.session_id || '') === sid) {
        _browserAgentContext = context;
        _browserUpdateHeaderBadge();
        return context;
      }
    } catch (e) {
      try { console.warn('browser agent context refresh failed', e); } catch (_) {}
    }
    return null;
  })();
  try {
    return await _browserAgentContextPromise;
  } finally {
    if (_browserAgentContextSid === sid) {
      _browserAgentContextPromise = null;
    }
  }
}

async function _browserFetchState(sessionId) {
  const sid = String(sessionId || '').trim();
  if (!sid) return null;
  if (_browserStateFetchPromise && _browserStateFetchSid === sid) {
    return _browserStateFetchPromise;
  }
  _browserStateFetchSid = sid;
  _browserStateFetchPromise = (async () => {
  const rev = ++_browserRequestRev;
  try {
    const data = await api('/api/browser/state?session_id=' + encodeURIComponent(sid));
    if (_browserCurrentSessionId() !== sid) return null;
    const state = data && (data.state || data);
    if (state && state.session_id === sid) {
      _browserRender(state);
      void browserRefreshAgentContext(sid);
      void browserRefreshPermission();
      return state;
    }
  } catch (e) {
    if (_browserCurrentSessionId() !== sid) return null;
    const text = e && e.error ? e.error : (e && e.message ? e.message : 'Failed to load browser state');
    _browserSetPill('error', 'Error');
    _browserSetStatusUrl(text);
    _browserSetActionSummary('');
    _browserSetSessionControlsReady(sid, text);
  }
  return null;
  })();
  try {
    return await _browserStateFetchPromise;
  } finally {
    if (_browserStateFetchSid === sid) {
      _browserStateFetchSid = '';
      _browserStateFetchPromise = null;
    }
  }
}

function _browserHandleStreamPayload(payload) {
  if (!payload) return;
  const state = payload.state || payload;
  if (!state || !state.session_id) return;
  if (_browserActiveSessionId && state.session_id !== _browserActiveSessionId) return;
  _browserRender(state);
  void browserRefreshAgentContext(state.session_id);
}

function _browserStartStream(sessionId) {
  const sid = String(sessionId || '').trim();
  if (!sid) return;
  _browserCloseStream();
  try {
    const es = new EventSource(_eventSourceUrl('/api/browser/events?session_id=' + encodeURIComponent(sid)));
    _browserEventSource = es;
    es.addEventListener('initial', function(ev) {
      try {
        const payload = JSON.parse(ev.data || '{}');
        _browserHandleStreamPayload(payload);
      } catch (_) {}
    });
    es.addEventListener('snapshot', function(ev) {
      try {
        const payload = JSON.parse(ev.data || '{}');
        _browserHandleStreamPayload(payload);
      } catch (_) {}
    });
    es.onerror = function() {
      if (_browserEventSource !== es) return;
      try { es.close(); } catch (_) {}
      _browserEventSource = null;
      if (_browserPollTimer) return;
      _browserPollTimer = setTimeout(async function poll() {
        _browserPollTimer = null;
        if (_browserActiveSessionId !== sid) return;
        await _browserFetchState(sid);
        if (_browserActiveSessionId === sid && _browserPanelVisible()) {
          _browserPollTimer = setTimeout(poll, 3000);
        }
      }, 1200);
    };
  } catch (_) {
    _browserEventSource = null;
  }
}

async function browserSyncToCurrentSession(opts = {}) {
  const sessionId = _browserCurrentSessionId();
  const visible = _browserPanelVisible();
  const allowPending = !!opts.allowPending;
  if (_browserPendingSessionSwitch && !allowPending) {
    if (visible) {
      _browserSetPill('idle', 'Loading');
      _browserSetStatusUrl('Switching session...');
      _browserSetEmptyVisible(true, {
        title: 'Switching browser session',
        text: 'Waiting for the active chat session to attach.',
      });
      _browserSetButtonsDisabled(true, null);
    }
    _browserScheduleSyncRetry();
    return null;
  }
  if (!sessionId) {
    _browserPendingSessionSwitch = false;
    if (visible) {
      browserPrepareSessionSwitch();
      _browserSetStatusUrl('Open a chat session to attach the browser runtime.');
      _browserSetEmptyVisible(true, {
        title: 'Browser not attached',
        text: 'Open a chat session to attach the browser runtime.',
      });
    }
    return null;
  }
  void browserRefreshPermission();
  void browserRefreshAgentContext(sessionId);
  const changed = sessionId !== _browserActiveSessionId;
  if (changed || opts.force) {
    _browserActiveSessionId = sessionId;
    _browserCloseStream();
    _browserPendingSessionSwitch = false;
    if (!visible) {
      return _browserState;
    }
    _browserSetPill('idle', 'Loading');
    _browserSetStatusUrl('Loading browser state...');
      _browserSetEmptyVisible(true, {
        title: 'Loading browser state',
        text: 'Fetching the current page and controls for this session.',
      });
    _browserSetButtonsDisabled(true, null);
    const state = await _browserFetchState(sessionId);
    if (state && visible) {
      _browserStartStream(sessionId);
    } else if (visible) {
      if (_browserCurrentSessionId() === sessionId) {
        _browserSetSessionControlsReady(sessionId, ((_browserEl('browserUrlInput') || {}).value || 'about:blank'));
      }
      _browserScheduleSyncRetry();
    }
    return state;
  }
  if (visible && !_browserEventSource) {
    _browserStartStream(sessionId);
  }
  return _browserState;
}

function _browserSendControl(action, payload = {}) {
  const sessionId = _browserCurrentSessionId();
  if (!sessionId) {
    if (typeof showToast === 'function') showToast('No chat session selected', 2000, 'error');
    return Promise.resolve(null);
  }
  const controlPayload = _browserFrameBoundControlPayload(action, payload);
  return api('/api/browser/control', {
    method: 'POST',
    body: JSON.stringify(Object.assign({session_id: sessionId, action: action}, controlPayload)),
  }).then(data => {
    const state = data && (data.state || data);
    if (state && state.session_id === sessionId) {
      _browserRender(state, {scrollIntoView: true});
    }
    return state;
  }).catch(err => {
    const staleFrame = _browserIsStaleFrameError(err);
    const text = staleFrame
      ? _browserStaleFrameMessage(err)
      : (err && err.error ? err.error : (err && err.message ? err.message : 'Browser control failed'));
    _browserSetPill('error', 'Error');
    _browserSetStatusUrl(text);
    if (staleFrame) _browserSetActionSummary('Blocked: ' + text);
    if (typeof showToast === 'function') showToast(text, 3000, 'error');
    if (staleFrame && typeof browserSyncToCurrentSession === 'function') {
      void browserSyncToCurrentSession({force: true, allowPending: true});
    }
    return null;
  });
}

function browserGoBack() {
  return _browserSendControl('back');
}

function browserGoForward() {
  return _browserSendControl('forward');
}

function browserReload() {
  return _browserSendControl('reload');
}

function browserStop() {
  return _browserSendControl('stop');
}

function browserOpenInNewTab() {
  const url = (_browserState && _browserState.url) ? _browserState.url : ((_browserEl('browserUrlInput') || {}).value || '');
  if (!url) return;
  window.open(url, '_blank', 'noopener,noreferrer');
}

function browserSubmitUrl(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  const input = _browserEl('browserUrlInput');
  const draftFresh = _browserUrlDraft && (Date.now() - _browserUrlDraftAt) < 6000;
  const url = _browserNormalizeSubmittedUrl(draftFresh ? _browserUrlDraft : (input ? String(input.value || '').trim() : ''));
  if (!url) return false;
  const now = Date.now();
  if (url === _browserLastSubmittedUrl && (now - _browserLastSubmittedAt) < 700) {
    return false;
  }
  _browserLastSubmittedUrl = url;
  _browserLastSubmittedAt = now;
  if (input) input.value = url;
  _browserClearUrlDraft();
  void _browserSendControl('navigate', {url: url});
  return false;
}

function _browserDelay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function browserWaitForStableState(previousState, opts = {}) {
  const timeoutMs = Number(opts.timeoutMs || 3500);
  const started = Date.now();
  const previousUrl = String(previousState && previousState.url || '');
  const previousRev = previousState && previousState.frame_rev;
  while (Date.now() - started < timeoutMs) {
    const live = browserGetState();
    const liveUrl = String(live.url || '');
    const revChanged = previousRev == null || live.frame_rev == null ? false : live.frame_rev !== previousRev;
    const usableUrl = liveUrl && liveUrl !== 'about:blank';
    const recovered = usableUrl && (!previousUrl || liveUrl !== previousUrl || revChanged || live.status !== 'error');
    if (recovered && live.frame_complete && live.frame_width > 0) return live;
    await _browserDelay(200);
  }
  return browserGetState();
}

async function browserNavigateUrl(url) {
  const next = _browserNormalizeSubmittedUrl(url);
  if (!next) return null;
  const input = _browserEl('browserUrlInput');
  if (input) input.value = next;
  _browserClearUrlDraft();
  const state = await _browserSendControl('navigate', {url: next});
  if (state && (state.status === 'error' || state.url === 'about:blank')) {
    return browserWaitForStableState(state, {timeoutMs: 4000});
  }
  return state;
}

function _browserCoordsFromEvent(event) {
  const stage = _browserEl('browserStage');
  const state = _browserState;
  if (!stage || !state) return null;
  const rect = stage.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const frame = _browserVisibleFrameRect(state);
  if (!frame || !frame.width || !frame.height) return null;
  const stageX = event.clientX - frame.containerLeft;
  const stageY = event.clientY - frame.containerTop;
  if (stageX < frame.left || stageY < frame.top || stageX > frame.left + frame.width || stageY > frame.top + frame.height) {
    return null;
  }
  const x = Math.max(0, Math.min(frame.width, stageX - frame.left));
  const y = Math.max(0, Math.min(frame.height, stageY - frame.top));
  const scaleX = Number(state.viewport_width || 1440) / frame.width;
  const scaleY = Number(state.viewport_height || 900) / frame.height;
  return {
    x: x * scaleX,
    y: y * scaleY,
    expected_frame_rev: state.frame_rev != null ? state.frame_rev : undefined,
  };
}

function _browserSendMove(payload) {
  _browserMovePending = payload;
  if (_browserMoveThrottle) return;
  _browserMoveThrottle = setTimeout(function() {
    _browserMoveThrottle = null;
    const next = _browserMovePending;
    _browserMovePending = null;
    if (!next) return;
    void _browserSendControl('move', next);
  }, 50);
}

function _browserAttachPointerHandlers() {
  const layer = _browserEl('browserHitLayer');
  if (!layer || layer.dataset.bound === '1') return;
  layer.dataset.bound = '1';
  layer.addEventListener('pointermove', function(event) {
    const coords = _browserCoordsFromEvent(event);
    if (!coords) return;
    _browserSendMove(coords);
  });
  layer.addEventListener('pointerdown', function(event) {
    if (!_browserExploreMode) return;
    const coords = _browserCoordsFromEvent(event);
    if (!coords) return;
    event.preventDefault();
    void _browserSendControl('click', coords);
  });
  layer.addEventListener('pointerenter', function() {
    const cursor = _browserEl('browserCursor');
    if (cursor && _browserState && _browserState.session_id) cursor.classList.add('visible');
  });
}

function _browserAttachDrawerDragHandlers() {
  const browserDrawer = _browserEl('browserDrawer');
  const header = browserDrawer ? browserDrawer.querySelector('.browser-drawer-header') : null;
  if (!browserDrawer || !header || header.dataset.dragBound === '1') return;
  header.dataset.dragBound = '1';
  header.style.cursor = 'grab';
  header.style.userSelect = 'none';
  if (!header.title) header.title = 'Drag to move the browser drawer';

  const dragMove = function(event) {
    if (!_browserDrawerDragState) return;
    if (_browserDrawerDragState.pointerId != null && event.pointerId != null && event.pointerId !== _browserDrawerDragState.pointerId) return;
    const nextLeft = _browserDrawerDragState.startLeft + (event.clientX - _browserDrawerDragState.startX);
    const nextTop = _browserDrawerDragState.startTop + (event.clientY - _browserDrawerDragState.startY);
    const next = _browserClampDrawerFloatPosition(nextLeft, nextTop, _browserDrawerDragState.width, _browserDrawerDragState.height);
    browserDrawer.style.left = next.left + 'px';
    browserDrawer.style.top = next.top + 'px';
    browserDrawer.style.right = 'auto';
    browserDrawer.style.bottom = 'auto';
    browserDrawer.style.transform = 'none';
    if (event.cancelable) event.preventDefault();
  };

  const endDrag = function(event) {
    if (!_browserDrawerDragState) return;
    if (_browserDrawerDragState.pointerId != null && event.pointerId != null && event.pointerId !== _browserDrawerDragState.pointerId) return;
    document.removeEventListener('pointermove', dragMove, true);
    document.removeEventListener('pointerup', endDrag, true);
    document.removeEventListener('pointercancel', endDrag, true);
    document.removeEventListener('mousemove', dragMove, true);
    document.removeEventListener('mouseup', endDrag, true);
    const rect = browserDrawer.getBoundingClientRect();
    const next = _browserClampDrawerFloatPosition(rect.left, rect.top, rect.width || _browserDrawerDragState.width, rect.height || _browserDrawerDragState.height);
    _browserWriteDrawerFloatPosition(next.left, next.top);
    _browserDrawerDragState = null;
    browserDrawer.classList.remove('is-dragging');
  };

  const startDrag = function(event) {
    if (_browserDrawerDragState) return;
    if (event.button != null && event.button !== 0) return;
    if (_browserFullscreen || _browserSplitScreen || !_browserDrawerOpen) return;
    if (event.target && event.target.closest && event.target.closest('button, a, input, textarea, select, option, [role="button"], [data-no-drag]')) return;
    const rect = browserDrawer.getBoundingClientRect();
    if (!rect || rect.width <= 0 || rect.height <= 0) return;
    _browserDrawerDragState = {
      pointerId: event.pointerId == null ? null : event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      startLeft: rect.left,
      startTop: rect.top,
      width: rect.width,
      height: rect.height,
    };
    browserDrawer.classList.add('is-dragging');
    if (typeof header.setPointerCapture === 'function') {
      try { header.setPointerCapture(event.pointerId); } catch (_) {}
    }
    document.addEventListener('pointermove', dragMove, true);
    document.addEventListener('pointerup', endDrag, true);
    document.addEventListener('pointercancel', endDrag, true);
    document.addEventListener('mousemove', dragMove, true);
    document.addEventListener('mouseup', endDrag, true);
    event.preventDefault();
  };

  header.addEventListener('pointerdown', startDrag);
  header.addEventListener('mousedown', startDrag);
}

function browserPanelActivated() {
  browserSetDrawerOpen(true, {force: true});
  _browserAttachPointerHandlers();
  _browserAttachDrawerDragHandlers();
}

function browserPanelDeactivated() {
  if (_browserFullscreen) _browserSetFullscreen(false);
  browserSetDrawerOpen(false);
}

function _browserResearchRenderEmpty(message) {
  const body = _browserEl('browserResearchBody');
  if (!body) return;
  body.dataset.initialized = '1';
  body.innerHTML = '<div class="browser-research-empty">' + (message ? _browserResearchEscape(message) : 'No research session selected.') + '</div>';
}

function _browserResearchEscape(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function _browserResearchSetBusy(busy, statusText) {
  _browserResearchBusy = !!busy;
  const btn = _browserEl('browserResearchStartBtn');
  if (btn) btn.disabled = !!busy;
  const pill = _browserEl('browserResearchStatusPill');
  if (pill) {
    pill.className = 'browser-status-pill';
    pill.classList.add(busy ? 'is-running' : 'is-idle');
    pill.textContent = busy ? 'Running' : 'Idle';
  }
  const status = _browserEl('browserResearchStatusUrl');
  if (status) status.textContent = statusText || (busy ? 'Running deep research…' : 'Enter a topic to begin.');
  _browserResearchSetContinueState();
}

function _browserResearchRenderSessions() {
  const list = _browserEl('browserResearchSessions');
  if (!list) return;
  if (!_browserResearchSessions.length) {
    list.innerHTML = '<div class="browser-research-empty">No prior research runs yet.</div>';
    return;
  }
  list.innerHTML = '';
  _browserResearchSessions.forEach(sess => {
    const item = document.createElement('button');
    item.type = 'button';
    item.className = 'browser-research-session' + (sess.id === _browserResearchSessionId ? ' is-active' : '');
    item.innerHTML =
      '<span class="browser-research-session-title">' + _browserResearchEscape(sess.title || 'Research') + '</span>' +
      '<span class="browser-research-session-meta">' + _browserResearchEscape((sess.last_message_at || sess.created_at || '').slice(0, 19).replace('T', ' ')) + '</span>';
    item.addEventListener('click', function() {
      browserResearchLoadSession(sess.id);
    });
    list.appendChild(item);
  });
}

function _browserResearchRenderMessage(role, content, timestamp) {
  const body = _browserEl('browserResearchBody');
  if (!body) return;
  if (!body.dataset.initialized) {
    body.dataset.initialized = '1';
    body.innerHTML = '';
  }
  const row = document.createElement('div');
  row.className = 'browser-research-msg ' + (role === 'assistant' ? 'is-assistant' : 'is-user');
  const bubble = document.createElement('div');
  bubble.className = 'browser-research-msg-bubble';
  if (role === 'assistant' && typeof renderMd === 'function') {
    bubble.innerHTML = renderMd(String(content || ''));
  } else {
    bubble.textContent = String(content || '');
  }
  row.appendChild(bubble);
  if (timestamp) {
    const meta = document.createElement('div');
    meta.className = 'browser-research-msg-meta';
    meta.textContent = new Date(timestamp).toLocaleString();
    row.appendChild(meta);
  }
  body.appendChild(row);
  body.scrollTop = body.scrollHeight;
}

function browserResearchRenderSession(session) {
  const body = _browserEl('browserResearchBody');
  if (!body) return;
  body.dataset.initialized = '1';
  body.innerHTML = '';
  const titleEl = _browserEl('browserResearchSessionLabel');
  if (titleEl) titleEl.textContent = session && session.title ? session.title : '';
  const topic = _browserEl('browserResearchTopic');
  const messages = (session && Array.isArray(session.messages)) ? session.messages : [];
  const sessionTopic = session && session.id ? _browserResearchTopicsBySession[session.id] : '';
  const firstUser = messages.find(msg => msg && msg.role === 'user');
  const display_content = firstUser ? _browserResearchDisplayContent(firstUser) : '';
  if (topic && document.activeElement !== topic) topic.value = sessionTopic || display_content || '';

  const firstAssistantIndex = messages.findIndex(msg => msg && msg.role === 'assistant');
  const intakeMessage = firstAssistantIndex >= 0 ? messages[firstAssistantIndex] : null;
  const intake = intakeMessage ? _browserResearchParseIntakeResponse(intakeMessage.content) : null;
  const quickAnswer = intake && intake.quick_answer ? intake.quick_answer : (intakeMessage ? _browserResearchDisplayContent(intakeMessage) : '');
  const questions = intake ? _browserResearchNormalizeQuestions(intake.follow_up_questions, display_content || sessionTopic || _browserResearchCurrentPrompt) : _browserResearchDefaultQuestions(display_content || sessionTopic || _browserResearchCurrentPrompt);
  const selectedDirection = String((_browserResearchSelectedDirectionBySession[_browserResearchStateKey()] || (questions[0] || '')) || '').trim();
  const researchPrompt = intake && intake.research_prompt ? intake.research_prompt : _browserResearchBuildResearchPrompt(display_content || sessionTopic || _browserResearchCurrentPrompt, selectedDirection, intake || {});
  const key = _browserResearchStateKey();
  _browserResearchIntakeBySession[key] = intake || null;
  _browserResearchQuickAnswerBySession[key] = quickAnswer || '';
  _browserResearchQuestionsBySession[key] = questions.slice();
  _browserResearchSelectedDirectionBySession[key] = selectedDirection;
  _browserResearchResearchPromptBySession[key] = researchPrompt;
  _browserResearchModeBySession[key] = messages.length > 1 ? 'research' : (intakeMessage ? 'intake' : 'idle');
  const state = _browserResearchGetSessionState();
  state.intake = intake || null;
  state.quickAnswer = quickAnswer || '';
  state.questions = questions.slice();
  state.selectedDirection = selectedDirection;
  state.researchPrompt = researchPrompt;
  state.mode = _browserResearchModeBySession[key];

  if (!messages.length) {
    _browserResearchSetQuickAnswer('', {mode: 'idle'});
    _browserResearchSetQuestions(questions, {selectedDirection: selectedDirection});
    _browserResearchRenderEmpty('Enter a topic to start a slim research run.');
    _browserResearchSetContinueState();
    return;
  }

  _browserResearchSetQuickAnswer(quickAnswer || '', {researchPrompt: researchPrompt, mode: state.mode});
  _browserResearchSetQuestions(questions, {selectedDirection: selectedDirection});
  const intro = document.createElement('div');
  intro.className = 'browser-research-empty';
  intro.textContent = messages.length > 1 ? 'Research summary loaded. Choose a direction or continue the current thread.' : 'Quick answer loaded. Choose a direction to continue.';
  body.appendChild(intro);
  _browserResearchRenderIntakeCard(session && session.title ? session.title : (display_content || 'Research intake'), quickAnswer || 'Choose a direction to continue.', [
    selectedDirection ? ('Direction: ' + selectedDirection) : 'Direction pending',
    questions.length ? ('Questions: ' + questions.length) : 'No follow-up questions',
  ]);
  messages.slice(firstAssistantIndex + 1).forEach(msg => {
    if (!msg || !msg.role) return;
    const text = msg.role === 'user' ? _browserResearchDisplayContent(msg) : msg.content;
    if (msg.role === 'assistant') {
      _browserResearchRenderResearchCard(session && session.title ? session.title : 'Deep research', text, [msg.timestamp ? new Date(msg.timestamp).toLocaleString() : '']);
    } else {
      const note = document.createElement('div');
      note.className = 'browser-research-empty';
      note.textContent = 'Direction: ' + (selectedDirection || text || 'Research');
      body.appendChild(note);
    }
  });
  const status = _browserEl('browserResearchStatusUrl');
  if (status) status.textContent = messages.length > 1 ? 'Loaded research summary. Continue or refine the direction.' : 'Loaded quick answer. Pick a direction to continue.';
  _browserResearchSetContinueState();
}

async function browserResearchLoadSessions(selectSessionId) {
  const rev = ++_browserResearchLoadRev;
  try {
    const data = await api('/api/agents/research/sessions');
    if (rev !== _browserResearchLoadRev) return;
    _browserResearchSessions = (data && data.sessions) ? data.sessions : [];
    if (selectSessionId) {
      _browserResearchSessionId = selectSessionId;
    }
    _browserResearchRenderSessions();
  } catch (e) {
    if (rev !== _browserResearchLoadRev) return;
    _browserResearchSessions = [];
    _browserResearchRenderSessions();
  }
}

async function browserResearchLoadSession(sessionId) {
  const sid = String(sessionId || '').trim();
  if (!sid) return null;
  _browserResearchSessionId = sid;
  _browserResearchSaveSessionState();
  _browserResearchRenderSessions();
  const rev = ++_browserResearchLoadRev;
  const body = _browserEl('browserResearchBody');
  if (body) {
    body.dataset.initialized = '1';
    body.innerHTML = '<div class="browser-research-empty">Loading research session…</div>';
  }
  try {
    const data = await api('/api/agents/research/sessions/' + encodeURIComponent(sid));
    if (rev !== _browserResearchLoadRev) return null;
    browserResearchRenderSession(data && data.session ? data.session : null);
    return data && data.session ? data.session : null;
  } catch (e) {
    if (rev !== _browserResearchLoadRev) return null;
    _browserResearchRenderEmpty('Failed to load research session.');
    return null;
  }
}

async function browserResearchPanelActivated() {
  _browserResearchSetBusy(false);
  _browserResearchApplySessionState();
  await browserResearchLoadSessions();
  if (_browserResearchSessionId) {
    await browserResearchLoadSession(_browserResearchSessionId);
  } else {
    _browserResearchRenderEmpty('Enter a topic to start a slim research run.');
    _browserResearchSetContinueState();
  }
}

function browserResearchPanelDeactivated() {}

function browserResearchReset() {
  const key = _browserResearchStateKey();
  _browserResearchCurrentPrompt = '';
  _browserResearchSessionId = null;
  _browserResearchIntakeBySession[key] = null;
  _browserResearchSelectedDirectionBySession[key] = '';
  _browserResearchQuickAnswerBySession[key] = '';
  _browserResearchQuestionsBySession[key] = [];
  _browserResearchResearchPromptBySession[key] = '';
  _browserResearchModeBySession[key] = 'idle';
  const state = _browserResearchGetSessionState();
  state.sessionId = null;
  state.prompt = '';
  state.intake = null;
  state.selectedDirection = '';
  state.quickAnswer = '';
  state.questions = [];
  state.researchPrompt = '';
  state.mode = 'idle';
  const input = _browserEl('browserResearchTopic');
  if (input) input.value = '';
  _browserResearchSetQuickAnswer('', {mode: 'idle'});
  _browserResearchSetQuestions([], {selectedDirection: '', allowEmpty: true});
  _browserResearchRenderEmpty('Enter a topic to start a slim research run.');
  _browserResearchSetBusy(false, 'Enter a topic to begin.');
  _browserResearchSaveSessionState();
  return false;
}

async function browserResearchContinue() {
  const topic = String(_browserResearchCurrentPrompt || ((_browserEl('browserResearchTopic') || {}).value || '')).trim();
  const key = _browserResearchStateKey();
  const selectedDirection = String(_browserResearchSelectedDirectionBySession[key] || '').trim();
  const quickAnswer = String(_browserResearchQuickAnswerBySession[key] || '').trim();
  if (!topic || _browserResearchBusy) return false;
  const researchPrompt = String(_browserResearchResearchPromptBySession[key] || '').trim() || _browserResearchBuildResearchPrompt(topic, selectedDirection || (_browserResearchQuestionsBySession[key] || [])[0] || '', {quick_answer: quickAnswer});
  const body = _browserEl('browserResearchBody');
  _browserResearchSetBusy(true, 'Running curated research…');
  if (body) {
    body.dataset.initialized = '1';
    const note = document.createElement('div');
    note.className = 'browser-research-empty';
    note.textContent = 'Running curated research for: ' + (selectedDirection || topic);
    body.appendChild(note);
  }
  try {
    const data = await api('/api/agents/research/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        message: researchPrompt,
        research_topic: topic,
        display_content: topic,
        session_title: topic,
        session_id: _browserResearchSessionId || undefined,
      }),
    });
    _browserResearchSessionId = data && data.session_id ? data.session_id : _browserResearchSessionId;
    _browserResearchModeBySession[key] = 'research';
    _browserResearchSaveSessionState();
    await browserResearchLoadSessions(_browserResearchSessionId);
    const session = await browserResearchLoadSession(_browserResearchSessionId);
    if (!session && data && data.response) {
      _browserResearchRenderResearchCard(topic, data.response, [selectedDirection || 'Curated research']);
    }
    _browserResearchSetBusy(false, 'Research complete.');
  } catch (e) {
    const errText = e && (e.error || e.message) ? (e.error || e.message) : 'Deep research failed';
    const setupBlocked = /LLM provider|provider not configured|onboarding/i.test(String(errText || ''));
    if (setupBlocked) {
      _browserResearchSetBusy(false, 'Choose an LLM provider in onboarding to continue.');
      _browserResearchRenderEmpty('Choose an LLM provider in onboarding to continue.');
    } else {
      _browserResearchSetBusy(false, errText);
      if (body) {
        _browserResearchRenderResearchCard(topic || 'Research', errText, ['Research failed']);
      }
    }
  }
  return false;
}

async function browserResearchSubmit(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  const input = _browserEl('browserResearchTopic');
  const topic = input ? String(input.value || '').trim() : '';
  if (!topic || _browserResearchBusy) return false;
  _browserResearchCurrentPrompt = topic;
  _browserResearchTopicsBySession[_browserResearchStateKey()] = topic;
  const key = _browserResearchStateKey();
  _browserResearchSelectedDirectionBySession[key] = '';
  _browserResearchResearchPromptBySession[key] = '';
  _browserResearchIntakeBySession[key] = null;
  _browserResearchQuickAnswerBySession[key] = '';
  _browserResearchQuestionsBySession[key] = [];
  _browserResearchModeBySession[key] = 'intake';
  _browserResearchSessionId = null;
  _browserResearchSaveSessionState();
  _browserResearchSetBusy(true, 'Drafting quick answer…');
  const body = _browserEl('browserResearchBody');
  if (body) {
    body.dataset.initialized = '1';
    body.innerHTML = '';
    _browserResearchRenderEmpty('Drafting quick answer for "' + topic + '"…');
  }
  try {
    const intakePrompt = _browserResearchBuildIntakePrompt(topic);
    const data = await api('/api/agents/research/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        message: intakePrompt,
        research_topic: topic,
        display_content: topic,
        session_title: topic,
      }),
    });
    _browserResearchSessionId = data && data.session_id ? data.session_id : null;
    if (_browserResearchSessionId) _browserResearchTopicsBySession[_browserResearchSessionId] = topic;
    _browserResearchSaveSessionState();
    await browserResearchLoadSessions(_browserResearchSessionId);
    const session = await browserResearchLoadSession(_browserResearchSessionId);
    if (session) {
      browserResearchRenderSession(session);
    } else if (data && data.response) {
      const parsed = _browserResearchParseIntakeResponse(data.response);
      _browserResearchSetQuickAnswer(parsed.quick_answer || data.response, {researchPrompt: parsed.research_prompt || '', mode: 'intake'});
      _browserResearchSetQuestions(parsed.follow_up_questions || _browserResearchDefaultQuestions(topic), {selectedDirection: parsed.focus_hint || ''});
      _browserResearchRenderIntakeCard(parsed.title || topic, parsed.quick_answer || data.response, [parsed.focus_hint || 'Choose a direction to continue']);
      _browserResearchSetBusy(false, 'Quick answer ready. Choose a direction to continue.');
    }
    if (input) input.value = topic;
    _browserResearchSetBusy(false, 'Quick answer ready. Choose a direction to continue.');
  } catch (e) {
    const errText = e && (e.error || e.message) ? (e.error || e.message) : 'Deep research failed';
    const setupBlocked = /LLM provider|provider not configured|onboarding/i.test(String(errText || ''));
    if (setupBlocked) {
      _browserResearchSetBusy(false, 'Choose an LLM provider in onboarding to start.');
      _browserResearchRenderEmpty('Choose an LLM provider in onboarding to start.');
    } else {
      _browserResearchSetBusy(false, errText);
      if (body) {
        _browserResearchRenderResearchCard(topic || 'Research', errText, ['Research failed']);
      }
    }
  }
  return false;
}

function browserSessionChanged() {
  _browserResearchSaveSessionState();
  browserPrepareSessionSwitch();
  _browserResearchApplySessionState();
  if (_browserPanelVisible()) {
    void browserSyncToCurrentSession({force: true, allowPending: true});
  }
}

window.browserPrepareSessionSwitch = browserPrepareSessionSwitch;
window.browserSyncToCurrentSession = browserSyncToCurrentSession;
window.browserPanelActivated = browserPanelActivated;
window.browserPanelDeactivated = browserPanelDeactivated;
window.browserSetDrawerOpen = browserSetDrawerOpen;
window.browserToggleDrawer = browserToggleDrawer;
window.browserToggleHeaderMenu = browserToggleHeaderMenu;
window.browserRunHeaderAction = browserRunHeaderAction;
window.browserTogglePermission = browserTogglePermission;
window.browserStopPermission = browserStopPermission;
window.browserRenderPermission = browserRenderPermission;
window.browserRenderWebBackend = browserRenderWebBackend;
window.browserRefreshWebBackend = browserRefreshWebBackend;
window.browserToggleWebBackend = browserToggleWebBackend;
window.browserRefreshPermission = browserRefreshPermission;
window.browserResearchPanelActivated = browserResearchPanelActivated;
window.browserResearchPanelDeactivated = browserResearchPanelDeactivated;
window.browserResearchSubmit = browserResearchSubmit;
window.browserResearchContinue = browserResearchContinue;
window.browserResearchReset = browserResearchReset;
window.browserSessionChanged = browserSessionChanged;
window.browserGoBack = browserGoBack;
window.browserGoForward = browserGoForward;
window.browserReload = browserReload;
window.browserStop = browserStop;
window.browserSubmitUrl = browserSubmitUrl;
window.browserNavigateUrl = browserNavigateUrl;
window.browserGetState = browserGetState;
window.browserGetQaState = browserGetQaState;
window.browserGetAgentContext = browserGetAgentContext;
window.browserWaitForStableState = browserWaitForStableState;
window.browserToggleExploreMode = browserToggleExploreMode;
window.browserSendScreenshotToChat = browserSendScreenshotToChat;
window.browserSendPageContextToChat = browserSendPageContextToChat;
window.browserSendFullPageContextToChat = browserSendFullPageContextToChat;
window.browserRunBrowserQaToChat = browserRunBrowserQaToChat;
window.browserTestCurrentPageToChat = browserTestCurrentPageToChat;
window.browserRetestCurrentPageToChat = browserRetestCurrentPageToChat;
window.browserFixFindingsToChat = browserFixFindingsToChat;
window.browserQaReproToChat = browserQaReproToChat;
window.browserCopyQaReport = browserCopyQaReport;
window.browserToggleQaDetails = browserToggleQaDetails;
window.browserClearQaReports = browserClearQaReports;
window.browserOpenQaHistoryUrl = browserOpenQaHistoryUrl;

function _browserInitializeRuntime() {
  if (_browserRuntimeInitialized) return;
  _browserRuntimeInitialized = true;
  _browserEnsureSplitStyles();
  _browserAttachUrlDraftHandlers();
  _browserRenderRememberedQaCard();
  browserRenderPermission({mode: _browserRememberedPermissionMode(), persist: false});
  _browserSyncFullscreenButton(_browserFullscreen);
  _browserSyncSplitButton(_browserSplitScreen);
  if (_browserDrawerOpen) {
    document.body.classList.add('browser-drawer-open');
    _browserSyncDrawerButton(true);
    _browserSetDrawerAccessibility(true);
    void browserRefreshWebBackend();
    if (_browserFullscreen) {
      document.body.classList.add('browser-maximized');
      _browserHoistDrawer();
    }
    if (_browserSplitScreen) {
      document.body.classList.add('browser-split');
    }
    _browserSyncDrawerFloatPosition();
    void browserSyncToCurrentSession({force: true, allowPending: true});
    _browserScheduleSyncRetry(1200);
  } else {
    _browserSetDrawerAccessibility(false);
    _browserSyncFullscreenButton(false);
    _browserSyncSplitButton(false);
  }
  document.addEventListener('keydown', _browserHandleExportHotkeys, true);
  _browserUpdateHeaderBadge();
  _browserAttachPointerHandlers();
  _browserAttachDrawerDragHandlers();
  window.addEventListener('resize', function() {
    if (_browserDrawerOpen && !_browserSplitScreen && !_browserFullscreen) {
      _browserSyncDrawerFloatPosition();
    }
  });
}

if (document.readyState === 'complete' || document.readyState === 'interactive') {
  setTimeout(_browserInitializeRuntime, 0);
} else {
  window.addEventListener('load', _browserInitializeRuntime);
}

/* ═══════════════════════════════════════════════════════════════
   WE BSEARCH PANEL — Quick Search, Mode Toggle, History, Split
   ═══════════════════════════════════════════════════════════════ */

// ── State ──────────────────────────────────────
let _websearchHistoryOpen = true;
let _websearchSplitOpen = false;

function websearchIsMobileWidth() {
  return window.matchMedia && window.matchMedia('(max-width: 640px)').matches;
}

function websearchSetHistoryOpen(open) {
  _websearchHistoryOpen = !!open;
  const el = document.getElementById('websearchHistory');
  if (el) el.classList.toggle('is-collapsed', !_websearchHistoryOpen);
  const btn = document.getElementById('websearchToggleHistoryBtn');
  if (btn) btn.setAttribute('aria-expanded', String(_websearchHistoryOpen));
}

// ── Mode Toggle ────────────────────────────────
function websearchToggleMode(mode) {
  document.querySelectorAll('.websearch-mode-btn').forEach(b => {
    const active = b.dataset.mode === mode;
    b.classList.toggle('is-active', active);
    b.setAttribute('aria-pressed', active ? 'true' : 'false');
  });
  const quickPane = document.getElementById('websearchQuickPane');
  const deepPane = document.getElementById('websearchDeepPane');
  if (mode === 'quick') {
    quickPane.style.display = '';
    deepPane.style.display = 'none';
  } else {
    quickPane.style.display = 'none';
    deepPane.style.display = '';
    // Refresh deep research list when switching to deep
    browserResearchPanelActivated();
  }
  if (typeof syncWorkflowChip === 'function') syncWorkflowChip();
}

// ── History Sidebar Toggle ─────────────────────
function websearchToggleHistory() {
  websearchSetHistoryOpen(!_websearchHistoryOpen);
}

// ── Split View Toggle ──────────────────────────
function websearchToggleSplit() {
  _websearchSplitOpen = !_websearchSplitOpen;
  const btn = document.getElementById('websearchSplitBtn');
  if (btn) {
    btn.classList.toggle('is-active', _websearchSplitOpen);
    btn.setAttribute('aria-pressed', _websearchSplitOpen ? 'true' : 'false');
  }
  // Toggle browser drawer as the preview pane
  if (_websearchSplitOpen) {
    browserSetDrawerOpen(true, {force: true});
  } else if (!_browserFullscreen) {
    browserSetDrawerOpen(false);
  }
}

// ── Quick Search ───────────────────────────────

// ── Websearch Query Chips ──────────────────────────
function _websearchChipContainer() {
  return document.getElementById('websearchSuggestionChips');
}

function _websearchRenderChips() {
  var chips = _websearchChipContainer();
  if (!chips) return;
  var history = _websearchGetHistory();
  var recent = history.slice(0, 3);
  if (!recent.length) { chips.style.display = 'none'; return; }
  chips.style.display = '';
  chips.innerHTML = '';
  recent.forEach(function(item) {
    var btn = document.createElement('button');
    btn.className = 'websearch-chip';
    btn.textContent = item.query;
    btn.addEventListener('click', function() {
      window.websearchQuickSearchFromChip(item.query);
    });
    chips.appendChild(btn);
  });
}

function _websearchRenderResultsSummary(results, elapsed) {
  var meta = document.getElementById('websearchQuickMeta');
  if (!meta) return;
  var resultCount = (results && results.length) || 0;
  var sources = results ? results.map(function(r) { return _websearchExtractDomain(r.url || ''); }).filter(Boolean) : [];
  var uniqueSources = sources.filter(function(v,i,a){return a.indexOf(v)===i;});
  var parts = [];
  if (resultCount > 0) parts.push(resultCount + ' result' + (resultCount !== 1 ? 's' : ''));
  if (uniqueSources.length > 0) parts.push(uniqueSources.length + ' source' + (uniqueSources.length !== 1 ? 's' : ''));
  parts.push(elapsed + 's');
  meta.textContent = parts.join(' · ');
}

function _websearchRenderEmptyState() {
  var empty = document.getElementById('websearchQuickEmpty');
  if (!empty) return;
  var examples = [
    'Latest AI research papers',
    'Python vs JavaScript 2025',
    'Climate change solutions',
    'Best IDE for web development',
    'Rust vs Go performance',
  ];
  empty.innerHTML = '<div class="websearch-empty-text">Try searching for something:</div><div class="websearch-chips" style="justify-content:center;margin-top:8px">' +
    examples.map(function(q) {
      return '<button class="websearch-chip" onclick="window.websearchQuickSearchFromChip(\'' + _websearchEscape(q.replace(/'/g, "\\'")) + '\')">' + _websearchEscape(q) + '</button>';
    }).join('') +
  '</div>';
  empty.style.display = '';
}

window.websearchQuickSearchFromChip = function(q) {
  var input = document.getElementById('websearchQuery');
  if (!input) return;
  input.value = q;
  websearchQuickSearch(new Event('submit'));
};

async function websearchQuickSearch(event) {
  if (event && typeof event.preventDefault === 'function') event.preventDefault();
  const input = document.getElementById('websearchQuery');
  const query = input ? String(input.value || '').trim() : '';
  if (!query) return false;

  // Hide chips when searching
  const chips = _websearchChipContainer();
  if (chips) chips.style.display = 'none';

  const meta = document.getElementById('websearchQuickMeta');
  const results = document.getElementById('websearchResults');
  const empty = document.getElementById('websearchQuickEmpty');
  if (empty) empty.style.display = 'none';
  if (meta) meta.textContent = 'Searching\u2026';
  if (results) results.innerHTML = '<div class="websearch-empty-text">Searching for <b>' + _websearchEscape(query) + '</b>\u2026</div>';

  const goBtn = document.querySelector('.websearch-go-btn');
  if (goBtn) goBtn.disabled = true;

  const startTime = Date.now();

  try {
    const data = await api('/api/agents/research/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        message: [
          'You are a web search assistant. Return ONLY valid JSON. No markdown, no code fences, no commentary.',
          'Schema:',
          '{',
          '  "answer": "A concise 2-4 sentence answer to the query",',
          '  "results": [',
          '    {',
          '      "title": "Result title",',
          '      "url": "https://example.com/page",',
          '      "source": "Example.com",',
          '      "snippet": "A short excerpt or summary of what this source says",',
          '      "tags": ["relevant", "tag"]',
          '    }',
          '  ]',
          '}',
          'Rules:',
          '- Provide 3-6 results when possible.',
          '- Titles should be descriptive like actual web search results.',
          '- URLs should look realistic and be on-topic (even if you generate them from knowledge).',
          '- Source is the domain name (e.g. "docs.python.org").',
          '- Snippets are 1-3 sentences summarizing the key info from that source.',
          '- Tags are 1-3 short keywords (e.g. ["tutorial", "python", "2024"]).',
          '- The answer field is your direct, helpful reply.',
          '- Be honest: if you don\'t know, say so and suggest how the user might find the answer.',
          'Query: ' + query,
        ].join('\n'),
        research_topic: query,
        session_title: 'Quick Search: ' + query,
      }),
    });

    const elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

    const responseText = data && data.response ? data.response : '';
    const parsed = _websearchParseQuickResponse(responseText);

    // Show result summary
    if (meta) {
      const resultCount = (parsed.results && parsed.results.length) || 0;
      if (resultCount > 0) {
        const sources = parsed.results ? parsed.results.map(function(r) { return _websearchExtractDomain(r.url || ''); }).filter(Boolean) : [];
        const uniqueSources = sources.filter(function(v,i,a){return a.indexOf(v)===i;});
        meta.innerHTML = '<div class="websearch-meta-summary">' + resultCount + ' result' + (resultCount !== 1 ? 's' : '') + ' from ' + uniqueSources.length + ' source' + (uniqueSources.length !== 1 ? 's' : '') + ' · ' + elapsed + 's</div>';
      } else {
        meta.innerHTML = '<div class="websearch-meta-summary">No results</div>';
      }
    }

    if (results) {
      if (parsed.answer) {
        results.innerHTML = '<div class="websearch-result-card websearch-result-card-answer">' + _websearchEscape(parsed.answer) + '</div>';
      }
      if (parsed.results && parsed.results.length) {
        parsed.results.forEach(function(r) {
          const tagsHtml = (r.tags && r.tags.length) ? '<div class="websearch-result-badges">' + r.tags.map(function(t) { return '<span class="websearch-result-badge">' + _websearchEscape(t) + '</span>'; }).join('') + '</div>' : '';
          const domain = _websearchExtractDomain(r.url || '');
          const card = document.createElement('div');
          card.className = 'websearch-result-card';
          card.innerHTML =
            '<div class="websearch-result-header">' + (domain ? '<img class="websearch-result-favicon" src="https://www.google.com/s2/favicons?domain=' + encodeURIComponent(domain) + '&sz=16" alt="" onerror="this.style.display=\'none\'"> ' : '') + '<a class="websearch-result-title" href="' + _websearchEscape(r.url || '#') + '" target="_blank" rel="noopener">' + _websearchEscape(r.title || '') + '</a></div>' +
            '<span class="websearch-result-source">' + _websearchEscape(r.source || domain || r.url || '') + '</span>' +
            '<p class="websearch-result-snippet">' + _websearchEscape(r.snippet || '') + '</p>' +
            tagsHtml;
          results.appendChild(card);
        });
      } else if (!parsed.answer) {
        results.innerHTML = '<div class="websearch-empty-text">No results found for "' + _websearchEscape(query) + '". Try rephrasing your query.</div>';
      }
    }

    // Save to history
    _websearchSaveToHistory(query, parsed.answer || '', parsed.results || []);
    _websearchLastQuery = query;
    _websearchRenderChips();

    // If we got a session_id, also update the deep research history filter
    if (data && data.session_id) {
      browserResearchLoadSessions();
    }

  } catch (e) {
    const errText = e && (e.error || e.message) ? (e.error || e.message) : 'Search failed';
    if (meta) meta.textContent = 'Error';
    if (results) results.innerHTML = '<div class="websearch-empty-text is-error">⚠️ ' + _websearchEscape(errText) + '</div>';
  }

  if (goBtn) goBtn.disabled = false;
  return false;
}

// ── Parse Quick Response ───────────────────────
function _websearchParseQuickResponse(text) {
  const raw = String(text == null ? '' : text).trim();
  if (!raw) return { answer: '', results: [] };

  // Try extracting JSON from code fences first
  const fenced = raw.match(/```(?:json)?\s*([\s\S]*?)\s*```/i);
  const candidates = [];
  if (fenced && fenced[1]) candidates.push(fenced[1].trim());
  candidates.push(raw);

  for (const candidate of candidates) {
    try {
      const parsed = JSON.parse(candidate);
      if (parsed && typeof parsed === 'object') {
        const results = Array.isArray(parsed.results) ? parsed.results.map(function(r) {
          return {
            title: String(r.title || r.name || '').trim(),
            url: String(r.url || r.link || '').trim(),
            source: String(r.source || r.site || '').trim(),
            snippet: String(r.snippet || r.description || r.summary || '').trim(),
            tags: Array.isArray(r.tags || r.keywords) ? (r.tags || r.keywords).map(String) : [],
          };
        }).filter(function(r) { return r.title || r.url; }) : [];
        return {
          answer: String(parsed.answer || parsed.summary || '').trim(),
          results: results,
        };
      }
    } catch (_) {}
  }

  // Fallback: treat the whole response as a text answer
  return { answer: raw.replace(/```[\s\S]*?```/g, '').trim(), results: [] };
}

// ── Simple escape ──────────────────────────────
function _websearchEscape(text) {
  return String(text == null ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

function _websearchExtractDomain(url) {
  try {
    var u = new URL(url);
    return u.hostname || '';
  } catch (_) { return ''; }
}

// ── History (localStorage) ─────────────────────
function _websearchGetHistory() {
  try {
    const raw = localStorage.getItem('sidekick-websearch-history');
    return raw ? JSON.parse(raw) : [];
  } catch (_) { return []; }
}

function _websearchClearHistory() {
  try {
    localStorage.removeItem('sidekick-websearch-history');
  } catch (_) {}
  _websearchRenderHistory();
}

function _websearchSaveToHistory(query, answer, results) {
  const history = _websearchGetHistory();
  history.unshift({
    query: query,
    answer: answer || '',
    resultCount: (results && results.length) || 0,
    timestamp: new Date().toISOString(),
  });
  // Keep last 50
  if (history.length > 50) history.length = 50;
  try {
    localStorage.setItem('sidekick-websearch-history', JSON.stringify(history));
  } catch (_) {}
  _websearchRenderHistory();
}

function _websearchTimeAgo(isoStr) {
  if (!isoStr) return '';
  const now = Date.now();
  const then = new Date(isoStr).getTime();
  const diffSec = Math.floor((now - then) / 1000);
  if (diffSec < 0) return 'just now';
  if (diffSec < 60) return diffSec + 's ago';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return diffMin + 'm ago';
  const diffHr = Math.floor(diffMin / 60);
  if (diffHr < 24) return diffHr + 'h ago';
  const diffDay = Math.floor(diffHr / 24);
  if (diffDay < 30) return diffDay + 'd ago';
  const diffMonth = Math.floor(diffDay / 30);
  if (diffMonth < 12) return diffMonth + 'mo ago';
  return Math.floor(diffMonth / 12) + 'y ago';
}

function _websearchRenderHistory() {
  const list = document.getElementById('websearchHistoryList');
  if (!list) return;
  const history = _websearchGetHistory();
  if (!history.length) {
    list.innerHTML =
      '<div style="padding:16px 8px;text-align:center">' +
        '<div style="font-size:13px;color:var(--muted);margin-bottom:4px">No searches yet.</div>' +
        '<div style="font-size:11px;color:var(--muted);opacity:.6">Try typing a query in the search box above.</div>' +
      '</div>';
    return;
  }

  // Group by date
  const now = new Date();
  const todayStr = now.toDateString();
  const yesterday = new Date(now);
  yesterday.setDate(yesterday.getDate() - 1);
  const yesterdayStr = yesterday.toDateString();

  var groups = { today: [], yesterday: [], older: [] };
  history.forEach(function(item) {
    var d = new Date(item.timestamp);
    var ds = d.toDateString();
    if (ds === todayStr) groups.today.push(item);
    else if (ds === yesterdayStr) groups.yesterday.push(item);
    else groups.older.push(item);
  });

  list.innerHTML = '';
  var labels = [
    { key: 'today', label: 'Today' },
    { key: 'yesterday', label: 'Yesterday' },
    { key: 'older', label: 'Older' },
  ];
  labels.forEach(function(groupInfo) {
    var items = groups[groupInfo.key];
    if (!items || !items.length) return;
    var header = document.createElement('div');
    header.className = 'websearch-history-group';
    header.textContent = groupInfo.label;
    list.appendChild(header);
    items.forEach(function(item) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'websearch-history-item' + (item.answer && item.answer.trim() ? ' websearch-history-item-has-answer' : '');
      var timeStr = _websearchTimeAgo(item.timestamp);
      var badge = item.resultCount ? '<span class="websearch-history-badge">' + item.resultCount + '</span>' : '';
      btn.innerHTML =
        '<div style="display:flex;align-items:flex-start;gap:4px">' +
          '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + _websearchEscape(item.query || '') + '</span>' +
          badge +
        '</div>' +
        '<span class="websearch-history-meta">' + timeStr + '</span>';
      btn.addEventListener('click', function() {
        var input = document.getElementById('websearchQuery');
        if (input) input.value = item.query || '';
        websearchToggleMode('quick');
        websearchQuickSearch(new Event('submit'));
      });
      list.appendChild(btn);
    });
  });
}

// ── Init on panel show ─────────────────────────
// Override existing browserResearchPanelActivated to also render websearch history
const _origWebsearchPanelActivated = window.browserResearchPanelActivated;
window.browserResearchPanelActivated = function() {
  if (typeof _origWebsearchPanelActivated === 'function') _origWebsearchPanelActivated();
  websearchSetHistoryOpen(!websearchIsMobileWidth());
  _websearchRenderHistory();
  _websearchRenderChips();
};

// Export
window.websearchToggleMode = websearchToggleMode;
window.websearchToggleSplit = websearchToggleSplit;
window.websearchToggleHistory = websearchToggleHistory;
window.websearchQuickSearch = websearchQuickSearch;

// Final public bindings for header buttons, workflow actions, and inline HTML
// handlers. The page ships early fallbacks so controls work during deferred
// script loading; rebind here so split/drawer/fullscreen actions always use
// the complete browser implementation after this bundle has finished.
if (typeof window !== 'undefined') {
  if (typeof browserToggleDrawer === 'function') window.browserToggleDrawer = browserToggleDrawer;
  if (typeof browserSetDrawerOpen === 'function') window.browserSetDrawerOpen = browserSetDrawerOpen;
  if (typeof browserToggleHeaderMenu === 'function') window.browserToggleHeaderMenu = browserToggleHeaderMenu;
  if (typeof browserRunHeaderAction === 'function') window.browserRunHeaderAction = browserRunHeaderAction;
  if (typeof browserToggleSplit === 'function') window.browserToggleSplit = browserToggleSplit;
  if (typeof browserToggleFullscreen === 'function') window.browserToggleFullscreen = browserToggleFullscreen;
  if (typeof browserTogglePermission === 'function') window.browserTogglePermission = browserTogglePermission;
  if (typeof browserToggleExploreMode === 'function') window.browserToggleExploreMode = browserToggleExploreMode;
  if (typeof browserSendScreenshotToChat === 'function') window.browserSendScreenshotToChat = browserSendScreenshotToChat;
  if (typeof browserSendPageContextToChat === 'function') window.browserSendPageContextToChat = browserSendPageContextToChat;
  if (typeof browserSendFullPageContextToChat === 'function') window.browserSendFullPageContextToChat = browserSendFullPageContextToChat;
  if (typeof browserRunBrowserQaToChat === 'function') window.browserRunBrowserQaToChat = browserRunBrowserQaToChat;
  if (typeof browserTestCurrentPageToChat === 'function') window.browserTestCurrentPageToChat = browserTestCurrentPageToChat;
  if (typeof browserRetestCurrentPageToChat === 'function') window.browserRetestCurrentPageToChat = browserRetestCurrentPageToChat;
  if (typeof browserFixFindingsToChat === 'function') window.browserFixFindingsToChat = browserFixFindingsToChat;
  if (typeof browserQaReproToChat === 'function') window.browserQaReproToChat = browserQaReproToChat;
  if (typeof browserCopyQaReport === 'function') window.browserCopyQaReport = browserCopyQaReport;
  if (typeof browserToggleQaDetails === 'function') window.browserToggleQaDetails = browserToggleQaDetails;
  if (typeof browserClearQaReports === 'function') window.browserClearQaReports = browserClearQaReports;
  if (typeof browserOpenQaHistoryUrl === 'function') window.browserOpenQaHistoryUrl = browserOpenQaHistoryUrl;
}
