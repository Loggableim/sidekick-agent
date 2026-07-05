let _browserActiveSessionId = null;
let _browserEventSource = null;
let _browserPollTimer = null;
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
let _browserFullscreen = localStorage.getItem('sidekick-browser-fullscreen') === '1';
let _browserSplitScreen = localStorage.getItem('sidekick-browser-split-open') === '1';
let _browserDrawerHost = null;
let _browserDrawerHostNext = null;
let _browserFrameObjectUrl = '';
let _browserExploreMode = false;
let _browserDiffActive = false;
let _browserDiffTimer = null;
let _browserTraceThumbnails = [];
let _browserChatContextActive = false;
let _browserPrevFrameRev = '';

function _browserEl(id) {
  return document.getElementById(id);
}

function _browserEnsureSplitStyles() {
  if (document.getElementById('browserSplitStyles')) return;
  const style = document.createElement('style');
  style.id = 'browserSplitStyles';
  style.textContent = `
body.browser-split {
  --browser-split-width: min(48vw, 860px);
}
body.browser-split header.app-titlebar,
body.browser-split nav.rail,
body.browser-split aside.sidebar,
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
  display: flex !important;
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
  display: grid !important;
  flex: 0 0 auto;
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

function _browserSetEmptyVisible(visible) {
  const empty = _browserEl('browserEmptyState');
  if (!empty) return;
  empty.classList.toggle('visible', !!visible);
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
  const busy = !!(state && state.busy);
  const buttons = {
    browserPermissionBtn: attached,
    browserAgentStopBtn: attached,
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
  if (next) {
    _browserHoistDrawer();
    browserSetDrawerOpen(true, {force: true, keepViewport: true});
    _browserSetDrawerAccessibility(true);
  } else {
    _browserRestoreDrawerHost();
    _browserSetDrawerAccessibility(_browserDrawerOpen);
  }
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
  if (next) {
    if (!_browserDrawerOpen) {
      browserSetDrawerOpen(true, {force: true, keepViewport: true});
    } else {
      void browserSyncToCurrentSession({allowPending: true});
    }
  } else {
    document.body.classList.remove('browser-split');
  }
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
  }, 600);
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

function browserSendScreenshotToChat() {
  const state = _browserState;
  if (!state || !state.url) {
    if (typeof showToast === 'function') showToast('No browser page loaded', 2000, 'error');
    return;
  }
  const url = state.url;
  const frameUrl = _browserFrameObjectUrl || '';
  const text = '📸 **Browser screenshot**\nURL: ' + url + '\n' + (frameUrl ? '![](' + frameUrl + ')' : '');
  const textarea = document.querySelector('#composerTextarea, #messageInput, textarea');
  if (textarea && typeof insertAtCursor === 'function') {
    insertAtCursor(textarea, text);
  } else if (textarea) {
    textarea.value = (textarea.value ? textarea.value + '\n' : '') + text;
    textarea.dispatchEvent(new Event('input', {bubbles: true}));
  }
  if (typeof showToast === 'function') showToast('Screenshot added to chat', 2000, 'success');
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

function browserRenderPermission(permission) {
  const mode = permission && permission.mode ? String(permission.mode) : 'none';
  _browserPermissionMode = mode;
  const status = _browserEl('browserPermissionStatus');
  const granted = mode === 'control' || mode === 'read';
  if (status) {
    status.textContent = mode === 'control' ? 'Agent control' : (mode === 'read' ? 'Agent watch' : 'Agent locked');
    status.classList.toggle('is-granted', granted);
  }
  const btn = _browserEl('browserPermissionBtn');
  if (btn) {
    const nextAction = mode === 'control' ? 'Pause' : 'Resume';
    const tooltip = mode === 'none'
      ? 'Allow Nova agent browser control'
      : (mode === 'control'
        ? 'Pause Nova agent browser control'
        : 'Resume Nova agent browser control');
    btn.classList.toggle('is-active', mode === 'control');
    btn.setAttribute('aria-pressed', mode === 'control' ? 'true' : 'false');
    btn.setAttribute('aria-label', tooltip);
    btn.dataset.tooltip = tooltip;
    btn.dataset.state = mode;
    btn.dataset.nextAction = nextAction;
  }
  const stopBtn = _browserEl('browserAgentStopBtn');
  if (stopBtn) {
    stopBtn.disabled = mode === 'none';
    stopBtn.classList.toggle('is-active', mode !== 'none');
    stopBtn.setAttribute('aria-pressed', mode !== 'none' ? 'true' : 'false');
    stopBtn.setAttribute('aria-label', mode === 'none' ? 'Stop Nova agent browser handoff' : 'Stop Nova agent browser handoff');
    stopBtn.dataset.tooltip = mode === 'none' ? 'Stop Nova agent browser handoff' : 'Stop Nova agent browser handoff';
  }
}

async function browserRefreshPermission() {
  const sid = _browserCurrentSessionId();
  if (!sid) {
    browserRenderPermission({mode: 'none'});
    return;
  }
  try {
    const data = await api('/api/browser/permission?session_id=' + encodeURIComponent(sid));
    if (_browserCurrentSessionId() !== sid) return;
    browserRenderPermission(data && data.permission ? data.permission : {mode: 'none'});
  } catch (_) {
    if (_browserCurrentSessionId() !== sid) return;
    browserRenderPermission({mode: 'none'});
  }
}

async function browserTogglePermission() {
  const sid = _browserCurrentSessionId();
  if (!sid) {
    if (typeof showToast === 'function') showToast('Open a chat before granting browser control', 2400, 'error');
    return false;
  }
  const nextMode = _browserPermissionMode === 'control' ? 'read' : 'control';
  try {
    const data = await api('/api/browser/permission', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({session_id: sid, mode: nextMode, enabled: true}),
    });
    if (_browserCurrentSessionId() !== sid) return false;
    browserRenderPermission(data && data.permission ? data.permission : {mode: 'none'});
    if (typeof showToast === 'function') {
      showToast(nextMode === 'control' ? 'Nova agent browser control enabled' : 'Nova agent browser control paused', 2200, nextMode === 'control' ? 'success' : 'info');
    }
  } catch (e) {
    if (_browserCurrentSessionId() !== sid) return false;
    browserRenderPermission({mode: 'none'});
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
    browserRenderPermission({mode: 'none'});
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
  const item = {
    step: _browserActionTrace.length + 1,
    status: String((state && state.status) || 'idle'),
    text: actionText,
    meta: String((state && state.target_label) || (state && state.target_selector) || (state && state.active_element_label) || '').trim(),
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
    if (entry.meta) {
      const meta = document.createElement('div');
      meta.className = 'browser-trace-meta';
      meta.textContent = entry.meta;
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
  const w = Math.max(Number(state.viewport_width || 1440) || 1440, 1);
  const h = Math.max(Number(state.viewport_height || 900) || 900, 1);
  const x = Number(state.target_x || 0) || 0;
  const y = Number(state.target_y || 0) || 0;
  const tw = Math.max(Number(state.target_width || 0) || 0, 1);
  const th = Math.max(Number(state.target_height || 0) || 0, 1);
  box.style.left = ((x / w) * 100) + '%';
  box.style.top = ((y / h) * 100) + '%';
  box.style.width = ((tw / w) * 100) + '%';
  box.style.height = ((th / h) * 100) + '%';
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

function _browserSetCursor(state) {
  const cursor = _browserEl('browserCursor');
  if (!cursor || !state) return;
  const w = Number(state.viewport_width || 1440) || 1440;
  const h = Number(state.viewport_height || 900) || 900;
  const x = Number(state.cursor_x || 0) || 0;
  const y = Number(state.cursor_y || 0) || 0;
  const left = (x / Math.max(w, 1)) * 100;
  const top = (y / Math.max(h, 1)) * 100;
  cursor.style.left = left + '%';
  cursor.style.top = top + '%';
  cursor.classList.toggle('visible', !!state && !!state.session_id);
}

function _browserFlashClick(state) {
  const flash = _browserEl('browserClickFlash');
  if (!flash || !state || state.click_x == null || state.click_y == null) return;
  const w = Number(state.viewport_width || 1440) || 1440;
  const h = Number(state.viewport_height || 900) || 900;
  flash.style.left = ((Number(state.click_x || 0) || 0) / Math.max(w, 1)) * 100 + '%';
  flash.style.top = ((Number(state.click_y || 0) || 0) || 0) / Math.max(h, 1) * 100 + '%';
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
        try { console.warn('browser frame image load failed', err); } catch (_) {}
      });
  }
}

function _browserRender(state, opts = {}) {
  if (!state) return;
  _browserState = state;
  _browserActiveSessionId = String(state.session_id || _browserCurrentSessionId() || '');
  _browserRecordActionTrace(state);
  const canGoBack = !!state.can_go_back;
  const canGoForward = !!state.can_go_forward;
  state.can_go_back = canGoBack;
  state.can_go_forward = canGoForward;
  _browserSetStageRatio(state);
  _browserSetImage(state);
  _browserSetCursor(state);
  if (state.click_ts != null) _browserFlashClick(state);
  _browserSetSessionLabel(state);
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
  if (input && document.activeElement !== input && state.url) {
    input.value = state.url;
  }
  _browserSetEmptyVisible(!state.session_id || (!state.frame_rev && !state.url));
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
}

function browserPrepareSessionSwitch() {
  _browserCloseStream();
  _browserRequestRev += 1;
  _browserActiveSessionId = null;
  _browserState = null;
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
  _browserSetEmptyVisible(true);
  _browserSetButtonsDisabled(true, null);
  browserRenderPermission({mode: 'none'});
}

function browserSetDrawerOpen(open, opts = {}) {
  const nextOpen = !!open;
  const prevOpen = _browserDrawerOpen;
  _browserDrawerOpen = nextOpen;
  localStorage.setItem('sidekick-browser-drawer-open', nextOpen ? '1' : '0');
  document.body.classList.toggle('browser-drawer-open', nextOpen);
  _browserSyncDrawerButton(nextOpen);
  _browserSetDrawerAccessibility(nextOpen);
  if (!nextOpen) {
    if (_browserSplitScreen) {
      _browserSetSplitScreen(false);
    }
    if (_browserFullscreen) {
      _browserSetFullscreen(false);
    }
    _browserCloseStream();
    const browserDrawer = _browserEl('browserDrawer');
    if (document.activeElement && browserDrawer && browserDrawer.contains(document.activeElement)) {
      const toggle = _browserEl('btnBrowserDrawerToggle');
      if (toggle && typeof toggle.focus === 'function') toggle.focus();
    }
    if (!opts.keepViewport) {
      _browserSetEmptyVisible(false);
    }
    return;
  }
  if (!prevOpen || opts.force) {
    void browserSyncToCurrentSession({force: true, allowPending: true});
  } else {
    void browserSyncToCurrentSession({allowPending: true});
  }
}

function browserToggleDrawer() {
  browserSetDrawerOpen(!_browserDrawerOpen, {force: true});
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

async function _browserFetchState(sessionId) {
  const sid = String(sessionId || '').trim();
  if (!sid) return null;
  const rev = ++_browserRequestRev;
  try {
    const data = await api('/api/browser/state?session_id=' + encodeURIComponent(sid));
    if (rev !== _browserRequestRev) return null;
    const state = data && (data.state || data);
    if (state && state.session_id === sid) {
      _browserRender(state);
      return state;
    }
  } catch (e) {
    if (rev !== _browserRequestRev) return null;
    const text = e && e.error ? e.error : (e && e.message ? e.message : 'Failed to load browser state');
    _browserSetPill('error', 'Error');
    _browserSetStatusUrl(text);
    _browserSetActionSummary('');
    _browserSetEmptyVisible(true);
  }
  return null;
}

function _browserHandleStreamPayload(payload) {
  if (!payload) return;
  const state = payload.state || payload;
  if (!state || !state.session_id) return;
  if (_browserActiveSessionId && state.session_id !== _browserActiveSessionId) return;
  _browserRender(state);
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
      _browserSetEmptyVisible(true);
      _browserSetButtonsDisabled(true, null);
    }
    return null;
  }
  if (!sessionId) {
    _browserPendingSessionSwitch = false;
    if (visible) {
      browserPrepareSessionSwitch();
      _browserSetStatusUrl('Open a chat session to attach the browser runtime.');
    }
    return null;
  }
  void browserRefreshPermission();
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
    _browserSetEmptyVisible(true);
    _browserSetButtonsDisabled(true, null);
    const state = await _browserFetchState(sessionId);
    if (state && visible) {
      _browserStartStream(sessionId);
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
  return api('/api/browser/control', {
    method: 'POST',
    body: JSON.stringify(Object.assign({session_id: sessionId, action: action}, payload)),
  }).then(data => {
    const state = data && (data.state || data);
    if (state && state.session_id === sessionId) {
      _browserRender(state, {scrollIntoView: true});
    }
    return state;
  }).catch(err => {
    const text = err && err.error ? err.error : (err && err.message ? err.message : 'Browser control failed');
    _browserSetPill('error', 'Error');
    _browserSetStatusUrl(text);
    if (typeof showToast === 'function') showToast(text, 3000, 'error');
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
  const url = input ? String(input.value || '').trim() : '';
  if (!url) return false;
  void _browserSendControl('navigate', {url: url});
  return false;
}

function browserNavigateUrl(url) {
  const next = String(url || '').trim();
  if (!next) return false;
  const input = _browserEl('browserUrlInput');
  if (input) input.value = next;
  void _browserSendControl('navigate', {url: next});
  return true;
}

function _browserCoordsFromEvent(event) {
  const stage = _browserEl('browserStage');
  const state = _browserState;
  if (!stage || !state) return null;
  const rect = stage.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const x = Math.max(0, Math.min(rect.width, event.clientX - rect.left));
  const y = Math.max(0, Math.min(rect.height, event.clientY - rect.top));
  const scaleX = Number(state.viewport_width || 1440) / rect.width;
  const scaleY = Number(state.viewport_height || 900) / rect.height;
  return {
    x: x * scaleX,
    y: y * scaleY,
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

function browserPanelActivated() {
  browserSetDrawerOpen(true, {force: true});
  _browserAttachPointerHandlers();
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
window.browserTogglePermission = browserTogglePermission;
window.browserStopPermission = browserStopPermission;
window.browserRenderPermission = browserRenderPermission;
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
window.browserToggleExploreMode = browserToggleExploreMode;
window.browserSendScreenshotToChat = browserSendScreenshotToChat;

window.addEventListener('load', function() {
  _browserEnsureSplitStyles();
  _browserSyncFullscreenButton(_browserFullscreen);
  _browserSyncSplitButton(_browserSplitScreen);
  if (_browserDrawerOpen) {
    document.body.classList.add('browser-drawer-open');
    _browserSyncDrawerButton(true);
    _browserSetDrawerAccessibility(true);
    if (_browserFullscreen) {
      document.body.classList.add('browser-maximized');
      _browserHoistDrawer();
    }
    if (_browserSplitScreen) {
      document.body.classList.add('browser-split');
    }
    void browserSyncToCurrentSession({force: true, allowPending: true});
  } else {
    _browserSetDrawerAccessibility(false);
    _browserSyncFullscreenButton(false);
    _browserSyncSplitButton(false);
  }
  _browserAttachPointerHandlers();
});

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
