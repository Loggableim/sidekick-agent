/**
 * Agents Tab — Multi-Agent System UI for Sidekick
 *
 * Supports:
 * - Splash screen (first-run setup with template selection)
 * - Agent grid view (icons + names)
 * - Agent chat view (per-agent conversation)
 * - Agent info sidebar (session history, settings, memory)
 * - Agent creation and editing
 */

(function() {
  'use strict';

  // ── State ───────────────────────────────────────────────────────────────
  let agents = [];
  let currentAgent = null;        // currently active agent object
  let currentSessionId = null;    // current chat session ID
  let chatMessages = [];          // messages in current view
  let isSplashDone = false;       // whether splash was completed

  // Make currentAgent globally accessible for onclick handlers in HTML
  Object.defineProperty(window, 'currentAgent', {
    get: function() { return currentAgent; },
    set: function(v) { currentAgent = v; },
    configurable: true,
  });

  // Internal setter that also keeps the global proxy happy
  function _setCurrentAgent(a) {
    currentAgent = a;
    // The window proxy handles the rest
  }

  // ── API Helpers ─────────────────────────────────────────────────────────

  function _fetchJson(path, opts) {
    return fetch(path, opts || {}).then(async (r) => {
      const text = await r.text();
      let data = {};
      try {
        data = text ? JSON.parse(text) : {};
      } catch (_) {
        data = { error: text || `HTTP ${r.status}` };
      }
      if (!r.ok) {
        const msg = (data && (data.error || data.message)) || `HTTP ${r.status}`;
        throw new Error(msg);
      }
      return data;
    });
  }

  function _api(path, opts) {
    // Prefer shared api() helper for auth/session handling + workspace scoping.
    if (typeof api === 'function') return api(path, opts || {});
    return _fetchJson(path, opts);
  }

  function apiGet(path) {
    return _api(path);
  }

  function apiPost(path, body) {
    return _api(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
  }

  function apiPatch(path, body) {
    return _api(path, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body || {}),
    });
  }

  function apiDelete(path) {
    return _api(path, { method: 'DELETE' });
  }

  // ── Wizard (Inline-Setup, kein Overlay mehr) ──────────────────────────

  var wizardStep = 1;

  function showWizard() {
    const wiz = document.getElementById('agentsWizard');
    const grid = document.getElementById('agentsGrid');
    if (wiz) wiz.classList.remove('hidden');
    if (grid) grid.classList.add('hidden');
    wizardStep = 1;
    showWizardStep(1);
    // Load templates into step 2
    loadWizardTemplates();
  }

  function hideWizard() {
    const wiz = document.getElementById('agentsWizard');
    const grid = document.getElementById('agentsGrid');
    if (wiz) wiz.classList.add('hidden');
    if (grid) grid.classList.remove('hidden');
  }

  function showWizardStep(n) {
    document.querySelectorAll('.agents-wizard-step').forEach(el => el.classList.add('hidden'));
    var step = document.getElementById('wizardStep' + n);
    if (step) step.classList.remove('hidden');
    wizardStep = n;
  }

  function wizardNext() {
    if (wizardStep === 1) showWizardStep(2);
  }

  function wizardPrev() {
    if (wizardStep === 2) showWizardStep(1);
  }

  function loadWizardTemplates() {
    apiGet('/api/agents/list').then(data => {
      var templates = (data.agents || []).filter(function(a) { return a.is_template; });
      var grid = document.getElementById('agentsWizardGrid');
      if (!grid) return;
      grid.innerHTML = '';

      templates.forEach(function(t) {
        var card = document.createElement('div');
        card.className = 'agents-wizard-card';
        card.dataset.slug = t.slug;
        card.title = t.personality ? t.personality.substring(0, 200) + '...' : (t.description || '');
        card.innerHTML =
          '<div class="agents-wizard-card-color" style="background:' + (t.color || '#6366F1') + '"></div>' +
          '<div class="agents-wizard-card-check">✓</div>' +
          '<div class="agents-wizard-card-avatar">' + (t.avatar_emoji || '🤖') + '</div>' +
          '<div class="agents-wizard-card-name">' + escHtml(t.name) + '</div>' +
          '<div class="agents-wizard-card-desc">' + escHtml(t.description || '') + '</div>' +
          '<div class="agents-wizard-card-tools">🔧 ' + ((t.tools || []).length > 0 ? escHtml(t.tools.join(', ')) : 'Chat & Gespräche') + '</div>' +
          '<div class="agents-wizard-card-hint">Klicken zum Auswählen</div>';
        card.addEventListener('click', function() {
          this.classList.toggle('selected');
          updateWizardButton();
        });
        grid.appendChild(card);
      });

      // Custom agent card
      var customCard = document.createElement('div');
      customCard.className = 'agents-wizard-card custom';
      customCard.title = 'Erstelle einen massgeschneiderten Agenten per KI-Assistent.';
      customCard.innerHTML =
        '<div class="agents-wizard-card-avatar" style="font-size:40px">🎨</div>' +
        '<div class="agents-wizard-card-name">Eigener Agent</div>' +
        '<div class="agents-wizard-card-desc">Beschreib was du brauchst – eine KI designt den perfekten Agenten für dich.</div>' +
        '<div class="agents-wizard-card-custom-btn">🎨 Jetzt erstellen</div>';
      customCard.addEventListener('click', function() {
        openAgentCreator();
      });
      grid.appendChild(customCard);

      updateWizardButton();
    }).catch(function(err) {
      console.warn('[agents] Failed to load templates:', err);
    });
  }

  function updateWizardButton() {
    var btn = document.getElementById('wizardFinishBtn');
    var selected = document.querySelectorAll('#agentsWizardGrid .agents-wizard-card.selected');
    if (btn) {
      btn.disabled = selected.length === 0;
      btn.textContent = selected.length > 0
        ? '✓ ' + selected.length + ' Agent' + (selected.length > 1 ? 'en' : '') + ' einrichten'
        : 'Wähle mindestens einen Agenten';
    }
  }

  function checkSplashStatus() {
    apiGet('/api/agents/splash/status').then(function(data) {
      isSplashDone = data.completed === true;
      if (!isSplashDone) {
        showWizard();
      } else {
        hideWizard();
      }
    }).catch(function() {
      isSplashDone = true;
      hideWizard();
    });
  }

  function completeSplash() {
    const selected = document.querySelectorAll('#agentsWizardGrid .agents-wizard-card.selected');
    const slugs = Array.from(selected).map(el => el.dataset.slug);

    const btn = document.getElementById('wizardFinishBtn');
    if (btn) {
      btn.disabled = true;
      btn.innerHTML = '<span class="agents-wizard-spinner"></span> Wird eingerichtet...';
    }

    apiPost('/api/agents/splash/complete', { activated: slugs }).then(result => {
      isSplashDone = true;
      hideWizard();
      loadAgents();
    }).catch(err => {
      console.warn('[agents] Splash completion failed:', err);
      if (btn) {
        btn.disabled = false;
        btn.textContent = 'Fehlgeschlagen — erneut versuchen';
      }
    });
  }

  function skipSplash() {
    isSplashDone = true;
    apiPost('/api/agents/splash/complete', { activated: [] }).catch(() => {});
    hideWizard();
    loadAgents();
  }

  // ── Agent Creator (Custom Agent via LLM) ────────────────────────────────

var creatorAnswers = [];        // {question, answer} pairs
var creatorLoading = false;     // true while waiting for LLM

function openAgentCreator() {
  creatorAnswers = [];
  creatorLoading = false;
  var modal = document.getElementById('agentsCreatorModal');
  var msgs = document.getElementById('agentsCreatorMessages');
  var input = document.getElementById('agentsCreatorInput');
  var sendBtn = document.getElementById('agentsCreatorSendBtn');
  var startBtn = document.getElementById('agentsCreatorStartBtn');
  if (modal) modal.classList.remove('hidden');
  if (msgs) msgs.innerHTML =
    '<div style="align-self:flex-start;background:var(--surface);border:1px solid var(--border-subtle);border-radius:8px;padding:10px 14px;font-size:13px;line-height:1.5;max-width:85%">' +
    'Klicke auf "Starten", um deinen eigenen Agenten zu entwerfen!</div>';
  if (input) { input.value = ''; input.disabled = true; }
  if (sendBtn) sendBtn.disabled = true;
  if (startBtn) startBtn.style.display = '';
}

function closeAgentCreator() {
  var modal = document.getElementById('agentsCreatorModal');
  if (modal) modal.classList.add('hidden');
  creatorLoading = false;
}

function startAgentCreator() {
  var msgs = document.getElementById('agentsCreatorMessages');
  var input = document.getElementById('agentsCreatorInput');
  var sendBtn = document.getElementById('agentsCreatorSendBtn');
  var startBtn = document.getElementById('agentsCreatorStartBtn');
  if (startBtn) startBtn.style.display = 'none';
  if (input) input.disabled = false;
  if (sendBtn) sendBtn.disabled = false;
  if (input) input.focus();

  creatorAnswers = [];
  creatorLoading = true;

  if (msgs) msgs.innerHTML =
    '<div style="align-self:flex-start;background:var(--surface);border:1px solid var(--border-subtle);border-radius:8px;padding:10px 14px;font-size:13px;line-height:1.5;max-width:85%">' +
    '🧠 Denke nach...</div>';

  // Get first question from LLM
  apiPost('/api/agents/splash/question', { answers: [] }).then(data => {
    creatorLoading = false;
    if (data.question && !data.done) {
      showCreatorQuestion(data.question);
    } else if (data.done && data.agent) {
      showCreatorResult(data.agent);
    }
  }).catch(function() {
    creatorLoading = false;
    showCreatorQuestion('Was soll dein Agent können? Beschreib kurz seinen Zweck.');
  });
}

function showCreatorQuestion(question) {
  var msgs = document.getElementById('agentsCreatorMessages');
  var input = document.getElementById('agentsCreatorInput');
  var sendBtn = document.getElementById('agentsCreatorSendBtn');
  if (!msgs) return;

  // Remove thinking
  msgs.innerHTML = '';

  // Show all previous Q&A
  creatorAnswers.forEach(function(a) {
    if (a.question) {
      msgs.innerHTML += '<div style="align-self:flex-start;background:var(--surface);border:1px solid var(--border-subtle);border-radius:8px;padding:10px 14px;font-size:13px;line-height:1.5;max-width:85%">' + escHtml(a.question) + '</div>';
    }
    if (a.answer) {
      msgs.innerHTML += '<div style="align-self:flex-end;background:var(--accent-bg-strong);border-radius:8px;padding:10px 14px;font-size:13px;line-height:1.5;max-width:85%">' + escHtml(a.answer) + '</div>';
    }
  });

  // Show new question
  msgs.innerHTML += '<div style="align-self:flex-start;background:var(--surface);border:1px solid var(--border-subtle);border-radius:8px;padding:10px 14px;font-size:13px;line-height:1.5;max-width:85%;animation:agentsFadeIn 0.3s ease">' + escHtml(question) + '</div>';

  msgs.scrollTop = msgs.scrollHeight;
  if (input) { input.disabled = false; input.value = ''; input.focus(); }
  if (sendBtn) sendBtn.disabled = false;
}

function sendAgentCreatorAnswer() {
  var input = document.getElementById('agentsCreatorInput');
  var sendBtn = document.getElementById('agentsCreatorSendBtn');
  var answer = (input && input.value.trim()) || '';
  if (!answer || creatorLoading) return;

  // Get last question from messages
  var msgs = document.getElementById('agentsCreatorMessages');
  var lastQuestionEl = msgs ? msgs.querySelector('div:last-child') : null;
  var lastQuestion = lastQuestionEl ? lastQuestionEl.textContent : '';

  creatorAnswers.push({ question: lastQuestion, answer: answer });
  if (input) { input.value = ''; input.disabled = true; }
  if (sendBtn) sendBtn.disabled = true;

  // Show thinking
  if (msgs) {
    msgs.innerHTML += '<div style="align-self:flex-end;background:var(--accent-bg-strong);border-radius:8px;padding:10px 14px;font-size:13px;line-height:1.5;max-width:85%">' + escHtml(answer) + '</div>';
    msgs.innerHTML += '<div style="align-self:flex-start;font-size:24px;padding:4px 10px;opacity:0.5;animation:agentsPulse 1.2s ease-in-out infinite">💭</div>';
    msgs.scrollTop = msgs.scrollHeight;
  }

  creatorLoading = true;
  apiPost('/api/agents/splash/question', { answers: creatorAnswers }).then(data => {
    creatorLoading = false;
    // Remove thinking bubble
    var thinkingEl = msgs ? msgs.querySelector('div:last-child') : null;
    if (thinkingEl && thinkingEl.textContent === '💭') {
      thinkingEl.remove();
    }

    if (data.done && data.agent) {
      showCreatorResult(data.agent);
    } else if (data.question) {
      showCreatorQuestion(data.question);
    } else {
      showCreatorQuestion('Gibt es noch etwas, das dein Agent können soll?');
    }
  }).catch(function() {
    creatorLoading = false;
    var thinkingEl = msgs ? msgs.querySelector('div:last-child') : null;
    if (thinkingEl && thinkingEl.textContent === '💭') {
      thinkingEl.remove();
    }
    showCreatorQuestion('Interessant! Und welche Persönlichkeit soll dein Agent haben?');
  });
}

function showCreatorResult(agent) {
  var msgs = document.getElementById('agentsCreatorMessages');
  var input = document.getElementById('agentsCreatorInput');
  var sendBtn = document.getElementById('agentsCreatorSendBtn');
  var startBtn = document.getElementById('agentsCreatorStartBtn');

  if (input) { input.disabled = true; input.value = ''; }
  if (sendBtn) sendBtn.disabled = true;
  if (startBtn) startBtn.style.display = 'none';

  if (msgs) {
    msgs.innerHTML +=
      '<div style="align-self:flex-start;background:var(--surface);border:1px solid var(--accent);border-radius:8px;padding:14px 16px;font-size:13px;line-height:1.5;max-width:95%;text-align:center;animation:agentsFadeIn 0.4s ease">' +
      '<div style="font-size:48px;margin-bottom:8px">' + (agent.avatar_emoji || '🎉') + '</div>' +
      '<div style="font-size:16px;font-weight:600;margin-bottom:4px">' + escHtml(agent.name) + '</div>' +
      '<div style="color:var(--muted);font-size:12px">' + escHtml(agent.description || 'Dein persönlicher Agent') + '</div>' +
      '<div style="margin-top:12px;padding:8px 20px;border-radius:6px;background:var(--accent);color:var(--bg);font-size:13px;font-weight:600;display:inline-block">✅ Agent erstellt!</div>' +
      '</div>';
    msgs.scrollTop = msgs.scrollHeight;
  }

  // Add to wizard selection
  var wizardGrid = document.getElementById('agentsWizardGrid');
  if (wizardGrid && !document.querySelector('#agentsWizardGrid .agents-wizard-card[data-slug="' + agent.slug + '"]')) {
    var card = document.createElement('div');
    card.className = 'agents-wizard-card selected';
    card.dataset.slug = agent.slug;
    card.innerHTML =
      '<div class="agents-wizard-card-color" style="background:' + (agent.color || '#6366F1') + '"></div>' +
      '<div class="agents-wizard-card-check">✓</div>' +
      '<div class="agents-wizard-card-avatar">' + (agent.avatar_emoji || '🎉') + '</div>' +
      '<div class="agents-wizard-card-name">' + escHtml(agent.name) + '</div>' +
      '<div class="agents-wizard-card-desc">' + escHtml(agent.description || 'Eigener Agent') + '</div>' +
      '<div class="agents-wizard-card-tools">🔧 ' + 'Custom' + '</div>';
    card.addEventListener('click', function() {
      this.classList.toggle('selected');
      updateWizardButton();
    });
    // Insert before custom card (last)
    var customCard = wizardGrid.querySelector('.agents-wizard-card:last-child');
    if (customCard && customCard.classList.contains('custom')) {
      wizardGrid.insertBefore(card, customCard);
    } else {
      wizardGrid.appendChild(card);
    }
    updateWizardButton();
  }
}

  // ── Current Agent Badge (CLI ↔ WebUI Bridge) ──────────────────────────

  function checkCurrentAgent() {
    apiGet('/api/agents/current').then(data => {
      const badge = document.getElementById('agentsCurrentBadge');
      if (!badge) return;
      if (data.active && data.agent) {
        badge.innerHTML =
          '<span style="display:flex;align-items:center;gap:6px;padding:4px 10px;border-radius:12px;background:' +
          (data.agent.color || '#6366F1') + '22;border:1px solid ' + (data.agent.color || '#6366F1') + '44;font-size:11px">' +
          '<span style="font-size:14px">' + (data.agent.avatar_emoji || '🤖') + '</span>' +
          '<span style="font-weight:500">' + escHtml(data.agent.name) + '</span>' +
          '<span style="opacity:0.6;font-size:10px">(CLI aktiv)</span>' +
          '</span>';
        badge.style.display = '';
      } else {
        badge.style.display = 'none';
      }
    }).catch(() => {});
  }

  function setAsCurrentAgent(slug) {
    apiPost('/api/agents/current', { slug: slug }).then(data => {
      if (data.ok) {
        showToast('Agent als aktiv markiert');
        checkCurrentAgent();
        loadAgents();
      }
    }).catch(err => {
      console.warn('[agents] Failed to set current agent:', err);
    });
  }

  // ── Agent Grid ──────────────────────────────────────────────────────────

  function loadAgents() {
    // If dashboard view is active, use dashboard instead
    if (document.getElementById('mainAgents') && document.getElementById('mainAgents').style.display !== 'none') {
      loadAgentsDashboard();
      return;
    }
    const container = document.getElementById('agentsGrid');
    if (!container) return;

    apiGet('/api/agents/activated').then(data => {
      agents = arrayFromApiPayload(data, 'agents');

      if (agents.length === 0) {
        container.innerHTML =
          '<div class="agents-empty-state">' +
          '<div class="agents-empty-state-icon">🤖</div>' +
          '<div class="agents-empty-state-text">Noch keine Agenten aktiviert</div>' +
          '<div class="agents-empty-state-sub">Aktiviere Agenten im Splash-Screen oder erstelle eigene.</div>' +
          '<button class="agents-empty-state-btn" onclick="window.showAgentsSplash()">Agenten einrichten</button>' +
          '</div>';
        return;
      }

      container.innerHTML = '<div class="agents-grid"></div>';
      const grid = container.querySelector('.agents-grid');

      // Fetch current agent for badge
      apiGet('/api/agents/current').then(cdata => {
        const currentSlug = (cdata && cdata.active && cdata.agent && cdata.agent.slug) ? cdata.agent.slug : null;

        agents.forEach(a => {
          const card = document.createElement('div');
          card.className = 'agents-grid-card';
          card.style.borderTopColor = a.color || '#6366F1';
          card.dataset.slug = a.slug;

          const isCurrent = currentSlug === a.slug;

          // Card tooltip
          var tooltipParts = [];
          if (a.description) tooltipParts.push(a.description);
          if (a.tools && JSON.parse(a.tools || '[]').length > 0) {
            var toolsArr = JSON.parse(a.tools || '[]');
            tooltipParts.push('🔧 ' + toolsArr.join(', '));
          }
          if (a.workdir) tooltipParts.push('📁 ' + a.workdir);
          card.title = tooltipParts.join('\n') || a.name;

          card.innerHTML =
            '<div class="agents-grid-card-color" style="background:' + (a.color || '#6366F1') + '"></div>' +
            (isCurrent ? '<div class="agents-grid-card-current-badge">Active in CLI</div>' : '') +
            '<div class="agents-grid-card-avatar">' + (a.avatar_emoji || '🤖') + '</div>' +
            '<div class="agents-grid-card-name">' + escHtml(a.name) + '</div>' +
            '<div class="agents-grid-card-status">' +
            '<span class="agents-grid-card-status-dot ' + (a.status || 'active') + '"></span>' +
            (a.status || 'active') +
            '</div>' +
            '<div class="agents-grid-card-workspace">📁 ' + (a.workdir && a.workdir !== '' ? escHtml(a.workdir.split('\\').pop().split('/').pop()) : escHtml(a.slug) + '-ws') + '</div>' +
            '<div class="agents-grid-card-msg-count">' +
            (a.message_count || 0) + ' Nachricht' + ((a.message_count || 0) !== 1 ? 'en' : '') +
            '</div>';
          card.addEventListener('click', function() {
            openAgentChat(a.slug);
          });
          card.addEventListener('contextmenu', function(e) {
            e.preventDefault();
            setAsCurrentAgent(a.slug);
          });
          grid.appendChild(card);
        });

        // Also check badge after loading grid
        checkCurrentAgent();
      }).catch(() => {});

      // Add create button
      const createCard = document.createElement('div');
      createCard.className = 'agents-grid-card';
      createCard.style.cursor = 'pointer';
      createCard.innerHTML =
        '<div style="font-size:32px;opacity:0.5">+</div>' +
        '<div class="agents-grid-card-name" style="color:var(--muted)">Neuen Agenten erstellen</div>';
      createCard.addEventListener('click', function() {
        showAgentCreateModal();
      });
      grid.appendChild(createCard);
    }).catch(err => {
      console.warn('[agents] Failed to load agents:', err);
      container.innerHTML =
        '<div class="agents-empty-state">' +
        '<div class="agents-empty-state-icon">⚠️</div>' +
        '<div class="agents-empty-state-text">Agenten nicht verfügbar</div>' +
        '<div class="agents-empty-state-sub">Der Agenten-Service konnte nicht geladen werden.</div>' +
        '</div>';
    });
  }

  // ── Agent Chat ──────────────────────────────────────────────────────────

  const _agentChatHome = { parent: null, next: null };

  function _isAgentsMainVisible() {
    const main = document.getElementById('mainAgents');
    return !!(main && main.style.display !== 'none');
  }

  function _rememberAgentChatHome(view) {
    if (!view || _agentChatHome.parent) return;
    _agentChatHome.parent = view.parentElement;
    _agentChatHome.next = view.nextSibling;
  }

  function dockAgentChatInMain() {
    const main = document.getElementById('mainAgents');
    const view = document.getElementById('agentsChatView');
    if (!main || !view || !_isAgentsMainVisible()) return false;
    _rememberAgentChatHome(view);
    main.classList.add('agents-main-chat-open');
    if (view.parentElement !== main) main.appendChild(view);
    if (typeof stopDashboardRefresh === 'function') stopDashboardRefresh();
    return true;
  }

  function restoreAgentChatHome() {
    const view = document.getElementById('agentsChatView');
    const main = document.getElementById('mainAgents');
    if (main) main.classList.remove('agents-main-chat-open');
    if (view && _agentChatHome.parent && view.parentElement !== _agentChatHome.parent) {
      if (_agentChatHome.next && _agentChatHome.next.parentElement === _agentChatHome.parent) {
        _agentChatHome.parent.insertBefore(view, _agentChatHome.next);
      } else {
        _agentChatHome.parent.appendChild(view);
      }
    }
  }

  function openAgentChat(slug) {
    apiGet('/api/agents/' + slug).then(data => {
      _setCurrentAgent(data.agent);
      if (!currentAgent) {
        console.warn('[agents] Agent not found:', slug);
        return;
      }

      const chatDockedInMain = dockAgentChatInMain();

      // Show chat view, hide grid
      document.getElementById('agentsGridView').classList.add('hidden');
      document.getElementById('agentsChatView').classList.remove('hidden');

      // Set header
      document.getElementById('agentsChatAvatar').textContent = currentAgent.avatar_emoji || '🤖';
      document.getElementById('agentsChatName').textContent = currentAgent.name;
      document.getElementById('agentsChatSub').textContent =
        (currentAgent.message_count || 0) + ' Nachrichten · ' + (currentAgent.status || 'active');

      // Load sessions + profile info
      loadAgentSessions(slug);

      // Load the most recent session or start fresh
      chatMessages = [];
      currentSessionId = null;
      renderChatMessages();

      // Try to load latest session
      apiGet('/api/agents/' + slug + '/sessions').then(sdata => {
        const sessions = sdata.sessions || [];
        if (sessions.length > 0) {
          // Highlight latest session
          highlightSession(sessions[0].id);
          loadSessionMessages(slug, sessions[0].id);
        }
      }).catch(() => {});

      // Update panel heading
      const panelHead = document.querySelector('#panelAgents .panel-head-label');
      if (panelHead) panelHead.textContent = currentAgent.name;
      if (chatDockedInMain) {
        const main = document.getElementById('mainAgents');
        if (main) main.setAttribute('aria-label', currentAgent.name + ' agent chat');
      }

      // Initialize workspace for this agent
      initAgentWorkspace(slug);
    }).catch(err => {
      console.warn('[agents] Failed to open agent:', err);
    });
  }

  // ── Agent Workspace (Autonomous Terminal + Actions) ────────────────────

  var workspaceSessionId = null;
  var workspaceEventSource = null;

  function initAgentWorkspace(slug) {
    const container = document.getElementById('agentsWorkspaceView');
    if (!container) return;

    // Show workspace layout
    document.getElementById('agentsChatPane').classList.remove('hidden');
    container.classList.remove('hidden');

    // Update workspace header with agent info
    document.getElementById('agentsWsAvatar').textContent = currentAgent ? (currentAgent.avatar_emoji || '🤖') : '🤖';
    document.getElementById('agentsWsName').textContent = currentAgent ? currentAgent.name : 'Agent';
    document.getElementById('agentsWsStatus').textContent = 'initializing';
    document.getElementById('agentsWsStatus').className = 'ws-status';
    document.getElementById('agentsWsWorkdir').textContent = currentAgent ? (currentAgent.workdir || '~') : '~';

    // Reset workspace terminal
    document.getElementById('agentsWsTerminal').innerHTML =
      '<div class="info">Initializing workspace for ' + (currentAgent ? escHtml(currentAgent.name) : 'agent') + '...</div>';

    // Reset timeline
    document.getElementById('agentsWsTimeline').innerHTML = '';

    // Create workspace session via API
    apiGet('/api/agents/' + slug + '/workspace').then(data => {
      workspaceSessionId = data.session_id;
      document.getElementById('agentsWsStatus').textContent = 'ready';
      document.getElementById('agentsWsStatus').className = 'ws-status';

      // Show ready state
      addWsTerminal('info', 'Workspace ready at: ' + escHtml(data.workdir));
      addWsTerminal('info', 'Waiting for your instructions...');

      // Connect SSE stream
      connectWorkspaceSSE(data.session_id);
    }).catch(err => {
      console.warn('[agents] Workspace init failed:', err);
      addWsTerminal('error', 'Failed to initialize workspace: ' + err);
    });
  }

  function connectWorkspaceSSE(sessionId) {
    // Close any existing connection
    if (workspaceEventSource) {
      workspaceEventSource.close();
    }

    workspaceEventSource = new EventSource(_eventSourceUrl('/api/agents/workspace/stream/' + sessionId));

    workspaceEventSource.onmessage = function(e) {
      try {
        var event = JSON.parse(e.data);

        if (event.type === 'output') {
          addWsTerminal('output', event.data);
        } else if (event.type === 'thinking') {
          addWsTimelineItem('thinking', event.data || '🧠 Thinking...');
          document.getElementById('agentsWsStatus').textContent = 'working';
          document.getElementById('agentsWsStatus').className = 'ws-status busy';
        } else if (event.type === 'thought') {
          addWsTimelineItem('thinking', event.data);
        } else if (event.type === 'action') {
          addWsTimelineItem('action', event.data);
        } else if (event.type === 'command' || event.type === 'command_start') {
          addWsTerminal('prompt', '$ ' + (event.command || event.data || ''));
          addWsTimelineItem('command', 'Running: ' + (event.command || event.data || '').substring(0, 80));
        } else if (event.type === 'result') {
          addWsTimelineItem('result', event.data);
        } else if (event.type === 'complete') {
          addWsTimelineItem('result', '✅ Task completed' + (event.exit_code !== undefined ? ' (exit: ' + event.exit_code + ')' : ''));
          document.getElementById('agentsWsStatus').textContent = 'done';
          document.getElementById('agentsWsStatus').className = 'ws-status done';
        } else if (event.type === 'error') {
          addWsTerminal('error', event.data);
          addWsTimelineItem('error', event.data);
          document.getElementById('agentsWsStatus').textContent = 'error';
          document.getElementById('agentsWsStatus').className = 'ws-status';
        } else if (event.type === 'info') {
          addWsTerminal('info', event.data || '');
        } else if (event.type === 'keepalive') {
          // ping — nichts tun
        }
      } catch(e) {
        // Nicht-JSON Event — event.data direkt als Output
        if (e && e.data) {
          addWsTerminal('output', e.data);
        }
      }
    };

    workspaceEventSource.onerror = function() {
      // SSE connection closed or error — reconnect not needed for task completion
      console.log('[agents] SSE connection closed');
    };
  }

  function addWsTerminal(cls, text) {
    var el = document.getElementById('agentsWsTerminal');
    if (!el) return;
    var line = document.createElement('div');
    line.className = cls || 'output';
    line.textContent = text;
    el.appendChild(line);
    el.scrollTop = el.scrollHeight;
  }

  function addWsTimelineItem(type, text) {
    var el = document.getElementById('agentsWsTimeline');
    if (!el) return;
    var item = document.createElement('div');
    item.className = 'agents-timeline-item ' + (type || 'action');

    var icons = { thinking: '🧠', action: '🔧', command: '💻', result: '✅', error: '❌' };
    item.innerHTML =
      '<span class="agents-timeline-icon">' + (icons[type] || '•') + '</span>' +
      '<span class="agents-timeline-content">' + escHtml(text) + '</span>' +
      '<span class="agents-timeline-time">' + new Date().toLocaleTimeString() + '</span>';
    el.appendChild(item);
    el.scrollTop = el.scrollHeight;
  }

  function sendAgentWorkspaceRequest() {
    var input = document.getElementById('agentsChatInput');
    var message = (input && input.value.trim()) || '';
    if (!message || !workspaceSessionId || !currentAgent) return;

    input.value = '';
    input.disabled = true;

    // Show user message in chat
    chatMessages.push({
      role: 'user',
      content: message,
      timestamp: new Date().toISOString()
    });
    renderChatMessages();

    // Show thinking in workspace
    addWsTimelineItem('thinking', '🧠 Agent analyzes request...');
    document.getElementById('agentsWsStatus').textContent = 'working';
    document.getElementById('agentsWsStatus').className = 'ws-status busy';

    // Send to backend
    apiPost('/api/agents/' + currentAgent.slug + '/workspace/process', {
      message: message,
      session_id: workspaceSessionId
    }).then(data => {
      input.disabled = false;
      input.focus();
      if (data.session_id) {
        // Connected — SSE will handle the rest
      }
    }).catch(function(err) {
      input.disabled = false;
      addWsTerminal('error', 'Request failed: ' + err);
    });
  }

  function sendWsCommand() {
    var input = document.getElementById('agentsWsCmdInput');
    var cmd = (input && input.value.trim()) || '';
    if (!cmd || !workspaceSessionId) return;

    input.value = '';
    addWsTerminal('prompt', '$ ' + cmd);

    apiPost('/api/agents/workspace/' + workspaceSessionId + '/command', {
      command: cmd
    }).then(function(data) {
      if (data && data.error) {
        addWsTerminal('error', data.error);
        addWsTimelineItem('error', data.error);
      }
    }).catch(function(err) {
      addWsTerminal('error', 'Command failed: ' + (err.message || err));
      addWsTimelineItem('error', 'Command failed');
    });
  }

  function stopAgentWorkspace() {
    if (!workspaceSessionId) return;
    apiPost('/api/agents/workspace/' + workspaceSessionId + '/stop', {})
      .then(function() {
        addWsTerminal('info', 'Workspace session stopped.');
        document.getElementById('agentsWsStatus').textContent = 'stopped';
        document.getElementById('agentsWsStatus').className = 'ws-status';
      }).catch(function() {});
  }

  function closeAgentChat() {
    _setCurrentAgent(null);
    currentSessionId = null;
    chatMessages = [];

    // Clean up workspace
    if (workspaceEventSource) {
      workspaceEventSource.close();
      workspaceEventSource = null;
    }
    stopAgentWorkspace();
    workspaceSessionId = null;

    document.getElementById('agentsChatView').classList.add('hidden');
    restoreAgentChatHome();

    // Also hide workspace
    var wsView = document.getElementById('agentsWorkspaceView');
    if (wsView) wsView.classList.add('hidden');
    document.getElementById('agentsChatPane').classList.add('hidden');

    document.getElementById('agentsGridView').classList.remove('hidden');

    const panelHead = document.querySelector('#panelAgents .panel-head-label');
    if (panelHead) panelHead.textContent = 'Agenten';
    loadAgents();
    // Reload dashboard if visible
    const agentsMain = document.getElementById('mainAgents');
    if (agentsMain && agentsMain.style.display !== 'none' && agentsMain.style.display !== '') {
      if (typeof startDashboardRefresh === 'function') startDashboardRefresh();
      setTimeout(loadAgentsDashboard, 300);
    }
  }

  function loadAgentSessions(slug) {
    const list = document.getElementById('agentsSessionList');
    if (!list) return;

    // Also load profile info
    const infoEl = document.getElementById('agentsProfileInfo');
    if (infoEl) {
      apiGet('/api/agents/' + slug).then(data => {
        const a = data.agent;
        if (!a) return;
        var tools = [];
        try { tools = JSON.parse(a.tools || '[]'); } catch(e) {}
        var lines = [];
        if (a.workdir) lines.push('<div style="margin-bottom:6px"><span style="font-weight:600;display:block;font-size:11px;color:var(--text);margin-bottom:2px">Workdir</span><code style="font-size:11px;word-break:break-all;opacity:0.8">' + escHtml(a.workdir) + '</code></div>');
        if (tools.length > 0) lines.push('<div style="margin-bottom:6px"><span style="font-weight:600;display:block;font-size:11px;color:var(--text);margin-bottom:2px">Erlaubte Tools</span><span style="font-size:11px;opacity:0.8">' + escHtml(tools.join(', ')) + '</span></div>');
        if (a.profile) lines.push('<div style="margin-bottom:6px"><span style="font-weight:600;display:block;font-size:11px;color:var(--text);margin-bottom:2px">Nova-Profil</span><code style="font-size:11px">' + escHtml(a.profile) + '</code> <span style="font-size:10px;opacity:0.6">(sidekick -p ' + escHtml(a.profile) + ')</span></div>');
        if (a.agent_type) lines.push('<div style="margin-bottom:6px"><span style="font-weight:600;display:block;font-size:11px;color:var(--text);margin-bottom:2px">Typ</span><span style="font-size:11px;opacity:0.8">' + escHtml(a.agent_type) + '</span></div>');
        if (lines.length === 0) lines.push('<span style="opacity:0.6">Keine zusätzlichen Informationen</span>');
        infoEl.innerHTML = lines.join('');
      }).catch(() => { infoEl.innerHTML = ''; });
    }

    apiGet('/api/agents/' + slug + '/sessions').then(data => {
      const sessions = data.sessions || [];
      if (sessions.length === 0) {
        list.innerHTML = '<div style="padding:8px;font-size:12px;color:var(--muted);text-align:center">Noch keine Chats</div>';
        return;
      }

      list.innerHTML = '';
      sessions.forEach(s => {
        const item = document.createElement('div');
        item.className = 'agents-session-item';
        item.dataset.sessionId = s.id;
        item.innerHTML =
          '<span class="agents-session-item-icon">💬</span>' +
          '<span class="agents-session-item-title">' + escHtml(s.title || 'Chat') + '</span>' +
          '<span class="agents-session-item-time">' + formatTimeAgo(s.last_message_at || s.created_at) + '</span>';
        item.addEventListener('click', function() {
          highlightSession(s.id);
          loadSessionMessages(slug, s.id);
        });
        list.appendChild(item);
      });
    }).catch(() => {});
  }

  function highlightSession(sessionId) {
    currentSessionId = sessionId;
    document.querySelectorAll('#agentsSessionList .agents-session-item').forEach(el => {
      el.classList.toggle('active', el.dataset.sessionId === sessionId);
    });
  }

  function loadSessionMessages(slug, sessionId) {
    apiGet('/api/agents/' + slug + '/sessions/' + sessionId).then(data => {
      const session = data.session;
      if (session && session.messages) {
        chatMessages = session.messages;
        currentSessionId = session.id;
        renderChatMessages();
      }
    }).catch(() => {
      chatMessages = [];
      renderChatMessages();
    });
  }

  function renderChatMessages() {
    const container = document.getElementById('agentsChatMessages');
    if (!container) return;

    if (!chatMessages || chatMessages.length === 0) {
      container.innerHTML =
        '<div class="agents-chat-empty">' +
        '<div class="agents-chat-empty-avatar">' + (currentAgent ? (currentAgent.avatar_emoji || '🤖') : '💬') + '</div>' +
        '<div class="agents-chat-empty-text">Starte eine Unterhaltung mit ' + (currentAgent ? escHtml(currentAgent.name) : 'diesem Agenten') + '</div>' +
        '</div>';
      return;
    }

    container.innerHTML = '';
    chatMessages.forEach(msg => {
      const el = document.createElement('div');
      var isThinking = msg.role === 'assistant' && msg.content === '💭';
      el.className = 'agents-chat-msg ' + (msg.role === 'user' ? 'user' : (isThinking ? 'thinking' : 'agent'));
      el.textContent = msg.content;
      if (msg.timestamp) {
        el.title = new Date(msg.timestamp).toLocaleString();
      }
      container.appendChild(el);
    });

    // Scroll to bottom
    container.scrollTop = container.scrollHeight;
  }

  function sendAgentMessage() {
    const textarea = document.getElementById('agentsChatInput');
    const msg = (textarea && textarea.value.trim()) || '';
    if (!msg || !currentAgent) return;

    textarea.value = '';
    textarea.style.height = 'auto';
    textarea.disabled = true;

    // Add user message to UI immediately
    chatMessages.push({
      role: 'user',
      content: msg,
      timestamp: new Date().toISOString()
    });
    renderChatMessages();

    // Show thinking indicator
    var thinkingIdx = chatMessages.length;
    chatMessages.push({
      role: 'assistant',
      content: '💭',
      timestamp: new Date().toISOString()
    });
    renderChatMessages();

    // Send to backend — get REAL LLM response
    apiPost('/api/agents/' + currentAgent.slug + '/chat', {
      message: msg,
      session_id: currentSessionId || undefined,
    }).then(data => {
      textarea.disabled = false;
      textarea.focus();

      const newSessionId = data.session_id;
      if (newSessionId && newSessionId !== currentSessionId) {
        currentSessionId = newSessionId;
        loadAgentSessions(currentAgent.slug);
      }

      // Auto-open sidebar if sessions exist
      var sidebar = document.getElementById('agentsChatSidebar');
      if (sidebar && sidebar.classList.contains('hidden')) {
        sidebar.classList.remove('hidden');
      }

      // Refresh agent info to update message count
      apiGet('/api/agents/' + currentAgent.slug).then(adata => {
        if (adata.agent) {
          _setCurrentAgent(adata.agent);
          document.getElementById('agentsChatSub').textContent =
            (adata.agent.message_count || 0) + ' Nachrichten · ' + (adata.agent.status || 'active');
        }
      }).catch(() => {});

      // Replace thinking indicator with actual response
      if (data.response) {
        chatMessages[thinkingIdx] = {
          role: 'assistant',
          content: data.response,
          timestamp: new Date().toISOString()
        };
        renderChatMessages();
      }
    }).catch(err => {
      console.warn('[agents] Send failed:', err);
      textarea.disabled = false;
      // Show error in place of thinking indicator
      chatMessages[thinkingIdx] = {
        role: 'assistant',
        content: '⚠️ Fehler bei der Kommunikation mit dem Agenten. Bitte versuch es erneut.',
        timestamp: new Date().toISOString()
      };
      renderChatMessages();
    });
  }

  function getPersonalityResponse(agentName, userMsg) {
    var responses = [
      agentName + ' hat deine Nachricht erhalten. Ich bin für dich da!',
      'Danke für deine Nachricht! ' + agentName + ' hört zu.',
      '👋 ' + agentName + ' hier! Ich habe dich verstanden. In der Vollversion werde ich persönlich und intelligent antworten können.',
      '👍 Nachricht erhalten! ' + agentName + ' merkt sich alles und wird beim nächsten Update noch besser antworten können.',
    ];
    return responses[Math.floor(Math.random() * responses.length)];
  }

  function handleChatKeydown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendAgentMessage();
    }
    // Auto-resize
    var el = e.target;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 120) + 'px';
  }

  // ── Agent Edit/Create Modal ─────────────────────────────────────────────

  function showAgentEditModal(slug) {
    apiGet('/api/agents/' + slug).then(data => {
      const agent = data.agent;
      if (!agent) return;

      const modal = document.getElementById('agentsEditModal');
      if (!modal) return;
      modal.classList.remove('hidden');

      document.getElementById('agentsEditTitle').textContent = agent.name + ' bearbeiten';
      document.getElementById('agentsEditName').value = agent.name;
      document.getElementById('agentsEditEmoji').value = agent.avatar_emoji || '🤖';
      document.getElementById('agentsEditPersonality').value = agent.personality || '';
      document.getElementById('agentsEditWorkdir').value = agent.workdir || '';
      document.getElementById('agentsEditProfile').value = agent.profile || '';
      document.getElementById('agentsEditStatus').value = agent.status || 'active';
      document.getElementById('agentsEditColor').value = agent.color || '#6366F1';

      modal.dataset.editingSlug = slug;
    }).catch(() => {});
  }

  function showAgentCreateModal() {
    const modal = document.getElementById('agentsEditModal');
    if (!modal) return;
    modal.classList.remove('hidden');

    document.getElementById('agentsEditTitle').textContent = 'Neuen Agenten erstellen';
    document.getElementById('agentsEditName').value = '';
    document.getElementById('agentsEditEmoji').value = '🤖';
    document.getElementById('agentsEditPersonality').value = '';
    document.getElementById('agentsEditWorkdir').value = '';
    document.getElementById('agentsEditProfile').value = '';
    document.getElementById('agentsEditStatus').value = 'active';
    document.getElementById('agentsEditColor').value = '#6366F1';

    modal.dataset.editingSlug = '';
  }

  function closeAgentEditModal() {
    const modal = document.getElementById('agentsEditModal');
    if (modal) modal.classList.add('hidden');
  }

  function saveAgentEdit() {
    const modal = document.getElementById('agentsEditModal');
    const slug = modal ? modal.dataset.editingSlug : '';
    const name = document.getElementById('agentsEditName').value.trim();
    if (!name) return;

    const data = {
      name: name,
      avatar_emoji: document.getElementById('agentsEditEmoji').value || '🤖',
      personality: document.getElementById('agentsEditPersonality').value || '',
      workdir: document.getElementById('agentsEditWorkdir').value || '',
      profile: document.getElementById('agentsEditProfile').value || '',
      status: document.getElementById('agentsEditStatus').value || 'active',
      color: document.getElementById('agentsEditColor').value || '#6366F1',
    };

    if (slug) {
      // Update existing
      apiPatch('/api/agents/' + slug, data).then(() => {
        closeAgentEditModal();
        if (currentAgent && currentAgent.slug === slug) {
          openAgentChat(slug);
        } else {
          loadAgents();
        }
      }).catch(err => console.warn('[agents] Update failed:', err));
    } else {
      // Create new
      apiPost('/api/agents/create', data).then(() => {
        closeAgentEditModal();
        loadAgents();
      }).catch(err => console.warn('[agents] Create failed:', err));
    }
  }

  function deleteCurrentAgent() {
    const modal = document.getElementById('agentsEditModal');
    const slug = modal ? modal.dataset.editingSlug : '';
    if (!slug || !confirm('Agent wirklich löschen? Alle Daten (Chats, Erinnerungen) werden unwiderruflich gelöscht.')) return;

    apiDelete('/api/agents/' + slug).then(() => {
      closeAgentEditModal();
      if (currentAgent && currentAgent.slug === slug) {
        closeAgentChat();
      }
      loadAgents();
    }).catch(err => console.warn('[agents] Delete failed:', err));
  }

  // ── Helpers ─────────────────────────────────────────────────────────────

  function escHtml(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(str));
    return div.innerHTML;
  }

  function arrayFromApiPayload(payload, key) {
    if (Array.isArray(payload)) return payload;
    if (payload && Array.isArray(payload[key])) return payload[key];
    if (payload && payload.data && Array.isArray(payload.data)) return payload.data;
    return [];
  }

  function statsMapFromPayload(payload) {
    const rows = arrayFromApiPayload(payload, 'stats');
    if (rows.length) {
      const map = {};
      for (const row of rows) {
        if (row && row.slug) map[row.slug] = row;
      }
      return map;
    }
    if (payload && typeof payload === 'object' && !Array.isArray(payload)) {
      const source = payload.stats && typeof payload.stats === 'object' && !Array.isArray(payload.stats)
        ? payload.stats
        : payload;
      const map = {};
      for (const [slug, value] of Object.entries(source)) {
        if (value && typeof value === 'object') map[slug] = Object.assign({ slug: value.slug || slug }, value);
      }
      return map;
    }
    return {};
  }

  function formatTimeAgo(dateStr) {
    if (!dateStr) return '';
    var date = new Date(dateStr);
    var now = new Date();
    var diff = Math.floor((now - date) / 1000);
    if (diff < 60) return 'jetzt';
    if (diff < 3600) return Math.floor(diff / 60) + 'm';
    if (diff < 86400) return Math.floor(diff / 3600) + 'h';
    return date.toLocaleDateString();
  }

  // ── Agent Dashboard ──────────────────────────────────────────────────────

  async function loadAgentsDashboard() {
    const grid = document.getElementById('agentsDashboardGrid');
    const statusDots = document.getElementById('agentsStatusDots');
    const latestActivity = document.getElementById('agentsLatestActivity');
    const feedList = document.getElementById('agentsFeedList');
    const feedCount = document.getElementById('agentsFeedCount');

    if (!grid) return; // mainAgents not in DOM yet

    try {
      // Fetch agents list + stats + activities in parallel
      const [agentsPayload, statsPayload, activitiesPayload] = await Promise.all([
        apiGet('/api/agents/list'),
        apiGet('/api/agents/stats'),
        apiGet('/api/agents/activities?limit=50'),
      ]);
      const agents = arrayFromApiPayload(agentsPayload, 'agents');
      const activities = arrayFromApiPayload(activitiesPayload, 'activities');

      // Build stats map
      const statsMap = statsMapFromPayload(statsPayload);

      // Status dots
      if (statusDots) {
        if (agents.length === 0) {
          statusDots.innerHTML = '<span style="font-size:11px;color:var(--muted)">Keine Agenten aktiviert</span>';
        } else {
          statusDots.innerHTML = agents.map(a => {
            const st = statsMap[a.slug] || {};
            const status = st.status || a.status || 'idle';
            const dotClass = status === 'active' ? 'active' : status === 'paused' ? 'paused' : 'idle';
            return '<span class="agents-status-dot" onclick="openAgentChat(\'' + a.slug + '\')" title="' + escHtml(a.name) + ' — ' + status + '">' +
              '<span class="dot ' + dotClass + '"></span>' + (a.avatar_emoji || '🤖') + ' ' + escHtml(a.name) +
            '</span>';
          }).join('');
        }
      }

      // Latest activity
      if (latestActivity && activities.length > 0) {
        const last = activities[0];
        const agent = agents.find(a => a.slug === last.agent_slug);
        const emoji = agent ? (agent.avatar_emoji || '🤖') : '🤖';
        const time = last.created_at ? last.created_at.slice(11, 16) : '';
        latestActivity.textContent = emoji + ' ' + escHtml(last.activity) + ' — ' + time;
      } else if (latestActivity) {
        latestActivity.textContent = '';
      }

      // Agent cards grid
      if (grid) {
        if (agents.length === 0) {
          grid.innerHTML = '<div class="agents-dashboard-empty"><div class="empty-icon">🤖</div><div class="empty-title">Keine Agenten</div><div class="empty-desc">Aktiviere Agenten über den "Agenten einrichten"-Dialog oder erstelle neue.</div><button class="agents-wizard-btn primary" onclick="showAgentCreateModal()" style="margin-top:8px;">➕ Agent erstellen</button></div>';
        } else {
          grid.className = 'agents-dashboard-grid-wrap agents-dashboard-grid';
          grid.innerHTML = agents.map(a => {
            const st = statsMap[a.slug] || {};
            const status = st.status || a.status || 'idle';
            const dotClass = status === 'active' ? 'active' : status === 'paused' ? 'paused' : 'idle';
            const color = st.color || a.color || '#6366F1';
            const msgCount = st.message_count || a.message_count || 0;
            const sesCount = st.session_count || 0;
            const lastAct = st.last_activity_text || '';
            return '<div class="agent-dashboard-card" onclick="openAgentChat(\'' + a.slug + '\')">' +
              '<div class="card-accent" style="background:' + color + ';"></div>' +
              '<div class="card-header">' +
                '<div class="card-emoji">' + (a.avatar_emoji || '🤖') + '</div>' +
                '<div class="card-info">' +
                  '<div class="card-name">' + escHtml(a.name) + '</div>' +
                  '<div class="card-status-row">' +
                    '<span class="card-status-dot ' + dotClass + '"></span>' +
                    '<span class="card-status-label">' + status + '</span>' +
                  '</div>' +
                '</div>' +
              '</div>' +
              '<div class="card-stats">' +
                '<span>💬 <strong>' + msgCount + '</strong> Nachrichten</span>' +
                '<span>📋 <strong>' + sesCount + '</strong> Sessions</span>' +
              '</div>' +
              (lastAct ? '<div class="card-last-activity">Letzte: ' + escHtml(lastAct) + '</div>' : '') +
              '<div class="card-actions">' +
                '<button class="card-action-btn primary" onclick="event.stopPropagation();openAgentChat(\'' + a.slug + '\')">💬 Chat</button>' +
                '<button class="card-action-btn" onclick="event.stopPropagation();showAgentEditModal(\'' + a.slug + '\')">⚙️ Bearbeiten</button>' +
              '</div>' +
            '</div>';
          }).join('');
        }
      }

      // Activity feed
      if (feedList) {
        if (activities.length === 0) {
          feedList.innerHTML = '<div style="padding:12px;text-align:center;color:var(--muted);font-size:11px;">Noch keine Aktivitäten — sende eine Nachricht an einen Agenten</div>';
        } else {
          feedList.innerHTML = activities.map(a => {
            const agent = agents.find(ag => ag.slug === a.agent_slug);
            const emoji = agent ? (agent.avatar_emoji || '🤖') : '🤖';
            const time = a.created_at ? a.created_at.slice(11, 16) : '';
            const statusIcon = a.status === 'done' ? '✅' : a.status === 'error' ? '❌' : a.status === 'running' ? '🔄' : '';
            return '<div class="agents-feed-entry" onclick="openAgentChat(\'' + a.agent_slug + '\')">' +
              '<span class="feed-time">' + time + '</span>' +
              '<span class="feed-emoji">' + emoji + '</span>' +
              '<span class="feed-text">' + escHtml(a.activity) + '</span>' +
              '<span class="feed-status">' + statusIcon + '</span>' +
            '</div>';
          }).join('');
        }
      }
      if (feedCount && activities.length > 0) {
        feedCount.textContent = activities.length + ' Einträge';
      }
    } catch (e) {
      console.warn('Dashboard load failed:', e);
      if (grid) grid.innerHTML = '<div class="agents-dashboard-empty"><div class="empty-icon">⚠️</div><div class="empty-title">Fehler beim Laden</div><div class="empty-desc">' + escHtml(e.message || 'Verbindungsfehler') + '</div></div>';
    }
  }

  // ── Dashboard auto-refresh ──
  let _dashboardRefreshTimer = null;

  function startDashboardRefresh() {
    stopDashboardRefresh();
    _dashboardRefreshTimer = setInterval(function() {
      // Only refresh if agents panel is visible
      var agentsMain = document.getElementById('mainAgents');
      if (agentsMain && agentsMain.style.display !== 'none') {
        loadAgentsDashboard();
      }
    }, 15000);
  }

  function stopDashboardRefresh() {
    if (_dashboardRefreshTimer) {
      clearInterval(_dashboardRefreshTimer);
      _dashboardRefreshTimer = null;
    }
  }

  // ── Public API (exposed to global scope for onclick handlers) ───────────

  window.showAgentsSplash = function() {
    showWizard();
    // Gehe direkt zu Schritt 2 wenn der User schon eingeweiht ist
    if (isSplashDone) {
      showWizardStep(2);
    }
  };

  window.completeSplash = completeSplash;
  window.skipSplash = skipSplash;
  window.wizardNext = wizardNext;
  window.wizardPrev = wizardPrev;
  window.openAgentCreator = openAgentCreator;
  window.closeAgentCreator = closeAgentCreator;
  window.startAgentCreator = startAgentCreator;
  window.sendAgentCreatorAnswer = sendAgentCreatorAnswer;
  window.openAgentChat = openAgentChat;
  window.closeAgentChat = closeAgentChat;
  window.sendAgentMessage = sendAgentMessage;
  window.handleChatKeydown = handleChatKeydown;
  window.showAgentEditModal = showAgentEditModal;
  window.showAgentCreateModal = showAgentCreateModal;
  window.closeAgentEditModal = closeAgentEditModal;
  window.saveAgentEdit = saveAgentEdit;
  window.deleteCurrentAgent = deleteCurrentAgent;
  window.loadAgents = loadAgents;
  window.checkCurrentAgent = checkCurrentAgent;
  window.setAsCurrentAgent = setAsCurrentAgent;
  window.sendAgentWorkspaceRequest = sendAgentWorkspaceRequest;
  window.sendWsCommand = sendWsCommand;
  window.stopAgentWorkspace = stopAgentWorkspace;
  window.loadAgentsDashboard = loadAgentsDashboard;
  window.restoreAgentChatHome = restoreAgentChatHome;
  window.startDashboardRefresh = startDashboardRefresh;
  window.stopDashboardRefresh = stopDashboardRefresh;

  // ── Initialize on panel switch ──────────────────────────────────────────

  // Hook into the existing switchPanel mechanism
  var origSwitchPanel = window.switchPanel;
  if (origSwitchPanel) {
    var patched = function(panel, opts) {
      var result = origSwitchPanel.call(this, panel, opts);
      if (panel === 'agents') {
        if (!isSplashDone) {
          checkSplashStatus();
        }
        loadAgents();
      }
      return result;
    };
    window.switchPanel = patched;
  }

  // Initial check on DOM ready
  document.addEventListener('DOMContentLoaded', function() {
    checkSplashStatus();
  });

})();
