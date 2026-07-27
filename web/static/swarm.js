// Project-local Swarm control surface.  This file deliberately has no Nova
// dependency: observations are GET/SSE only and every write starts from an
// explicit operator control.
(function () {
  'use strict';

  const SWARM_CALL_BUDGET = 48;
  const _swarmState = {
    projectPath: '',
    runs: [],
    packs: [],
    catalog: null,
    selectedRunId: '',
    detail: null,
    notice: '',
    cursor: 0,
  };
  let _swarmEventSource = null;
  let _swarmStreamKey = '';
  let _swarmLoadSequence = 0;

  function swarmEsc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function swarmProjectPath() {
    const configured = String(
      window._activeSpaceConfig && window._activeSpaceConfig.project_dir || ''
    ).trim();
    if (configured) return configured;
    const activeSlug = String(window._activeSpace || '').trim().toLowerCase();
    const spaces = Array.isArray(window._spacesCache) ? window._spacesCache : [];
    const active = spaces.find(space => String(space && space.slug || '').trim().toLowerCase() === activeSlug);
    return String(active && active.project_dir || '').trim();
  }

  function _swarmPath(path, projectPath) {
    const separator = path.indexOf('?') >= 0 ? '&' : '?';
    return path + separator + 'project_path=' + encodeURIComponent(projectPath);
  }

  function _swarmRequest(path, options) {
    return api(path, Object.assign({logError: false}, options || {}));
  }

  function _swarmSetNotice(message) {
    _swarmState.notice = String(message || '');
  }

  function _swarmNotify(message, type) {
    _swarmSetNotice(message);
    if (typeof showToast === 'function') showToast(message, 3600, type || '');
  }

  function _swarmResetForProject(projectPath) {
    _swarmState.projectPath = projectPath;
    _swarmState.runs = [];
    _swarmState.packs = [];
    _swarmState.catalog = null;
    _swarmState.selectedRunId = '';
    _swarmState.detail = null;
    _swarmState.notice = '';
    _swarmState.cursor = 0;
  }

  function _swarmParseResponse(result, fallback) {
    return result && result.status === 'fulfilled' ? result.value : fallback;
  }

  function _swarmErrorMessage(result) {
    if (!result || result.status !== 'rejected') return '';
    const error = result.reason;
    return error && error.message ? error.message : String(error || 'Unable to load Swarm state');
  }

  function _swarmSelectedRun() {
    return _swarmState.runs.find(run => run && run.run_id === _swarmState.selectedRunId) || null;
  }

  function _swarmEventList() {
    const events = _swarmState.detail && _swarmState.detail.events;
    return Array.isArray(events) ? events : [];
  }

  function _swarmApprovalList() {
    const approvals = _swarmState.detail && _swarmState.detail.approvals;
    return Array.isArray(approvals) ? approvals : [];
  }

  function _swarmLatestPauseReason(events) {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (!event || event.event_type !== 'run.paused') continue;
      const reason = event.payload && event.payload.reason;
      if (typeof reason === 'string' && reason.trim()) return reason.trim();
    }
    return '';
  }

  function _swarmCallCount(events) {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const payload = events[index] && events[index].payload;
      if (payload && Number.isFinite(Number(payload.call_count))) return Number(payload.call_count);
    }
    return events.filter(event => {
      const type = String(event && event.event_type || '');
      return type === 'model.call' || type === 'model.completed' || type === 'model.response';
    }).length;
  }

  function _swarmRoleActivity(events) {
    return events.filter(event => {
      const payload = event && event.payload || {};
      return (event && (event.event_type === 'work.started' || event.event_type === 'work.completed' || event.event_type === 'model.attempt_failed'))
        && (payload.role || payload.model);
    }).slice(-10).reverse();
  }

  function _swarmEvidenceAndConflicts(events) {
    return events.filter(event => {
      const type = String(event && event.event_type || '');
      const payload = event && event.payload || {};
      return type.indexOf('evidence') >= 0 || type.indexOf('conflict') >= 0 ||
        Object.prototype.hasOwnProperty.call(payload, 'conflict') ||
        Object.prototype.hasOwnProperty.call(payload, 'conflicts');
    }).slice(-10).reverse();
  }

  function _swarmApprovalQueue(events, approvals) {
    const humanDecisions = new Set(
      approvals
        .filter(approval => approval && approval.approval_type === 'human')
        .map(approval => String(approval.proposal_id || ''))
        .filter(Boolean)
    );
    return events
      .filter(event => event && event.event_type === 'swarm.action_proposed')
      .map(event => {
        const payload = event.payload || {};
        return {
          proposalId: String(payload.proposal_id || ''),
          action: payload.requested_action && payload.requested_action.name || payload.action || 'Proposed action',
          decided: humanDecisions.has(String(payload.proposal_id || '')),
        };
      })
      .filter(proposal => proposal.proposalId && !proposal.decided);
  }

  function _swarmEventText(event) {
    const payload = event && event.payload || {};
    const role = payload.role ? String(payload.role) + ': ' : '';
    if (Array.isArray(payload.evidence)) return role + payload.evidence.join(', ');
    if (Array.isArray(payload.conflicts)) return role + payload.conflicts.join(', ');
    if (payload.conflict) return role + String(payload.conflict);
    if (payload.reason) return role + String(payload.reason);
    if (payload.decision) return role + String(payload.decision);
    return role + String(event && event.event_type || 'Recorded event');
  }

  function _swarmFormatTime(value) {
    if (!value) return '—';
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
  }

  function _swarmCatalogSummary() {
    const catalog = _swarmState.catalog;
    if (!catalog) return 'No persisted catalog snapshot. Refresh explicitly when you need current catalog health.';
    const health = catalog.healthy === true ? 'healthy' : catalog.healthy === false ? 'unhealthy' : 'unknown';
    const models = Array.isArray(catalog.models) ? catalog.models.map(String).filter(Boolean) : [];
    const count = models.length;
    return [
      String(catalog.provider || 'ollama-cloud'),
      health,
      count + ' model' + (count === 1 ? '' : 's'),
      models.length ? 'choices: ' + models.join(', ') : 'no model choices recorded',
      catalog.refreshed_at ? 'refreshed ' + _swarmFormatTime(catalog.refreshed_at) : 'not yet refreshed',
    ].join(' · ');
  }

  function _swarmPackOptions() {
    const packs = _swarmState.packs;
    if (!packs.length) return '<option value="coding-team">coding-team</option>';
    return packs.map(pack => {
      const id = String(pack && pack.pack_id || '');
      const description = String(pack && pack.description || '');
      return '<option value="' + swarmEsc(id) + '">' + swarmEsc(id + (description ? ' — ' + description : '')) + '</option>';
    }).join('');
  }

  function _renderSwarmSidebar() {
    const sidebar = document.getElementById('swarmRunList');
    if (!sidebar) return;
    if (!_swarmState.projectPath) {
      sidebar.innerHTML = '<div class="swarm-sidebar-empty">Choose a Space with a configured project directory to inspect its local Swarm runs.</div>';
      return;
    }
    if (!_swarmState.runs.length) {
      sidebar.innerHTML = '<div class="swarm-sidebar-empty">No persisted Swarm runs in this project yet.</div>';
      return;
    }
    sidebar.innerHTML = _swarmState.runs.map(run => {
      const active = run.run_id === _swarmState.selectedRunId ? ' is-active' : '';
      const goal = run.metadata && run.metadata.goal || run.run_id;
      return '<button type="button" class="swarm-run-list-item' + active + '" data-swarm-run-id="' + swarmEsc(run.run_id) + '">' +
        '<span class="swarm-run-list-goal">' + swarmEsc(goal) + '</span>' +
        '<span class="swarm-run-list-meta">' + swarmEsc(run.status || 'unknown') + '</span>' +
        '</button>';
    }).join('');
    sidebar.onclick = function (event) {
      const button = event.target.closest('[data-swarm-run-id]');
      if (!button) return;
      const runId = String(button.dataset.swarmRunId || '');
      if (!runId || runId === _swarmState.selectedRunId) return;
      _swarmState.selectedRunId = runId;
      void _loadSwarmDetail(_swarmState.projectPath, _swarmLoadSequence);
      _renderSwarmSidebar();
    };
  }

  function _renderSwarmMain() {
    const main = document.getElementById('swarmMain');
    if (!main) return;
    if (!_swarmState.projectPath) {
      main.innerHTML = '<section class="swarm-empty-state" aria-live="polite">' +
        '<h2>Connect a project directory</h2>' +
        '<p>This Space has no project directory configured. Set one in Spaces, then return to Swarm. No Swarm request has been made.</p>' +
        '</section>';
      return;
    }

    const selected = _swarmSelectedRun();
    const detailRun = _swarmState.detail && _swarmState.detail.run;
    const run = detailRun && detailRun.run_id === _swarmState.selectedRunId ? detailRun : selected;
    const events = _swarmEventList();
    const approvals = _swarmApprovalList();
    const pauseReason = _swarmLatestPauseReason(events);
    const callCount = _swarmCallCount(events);
    const roles = _swarmRoleActivity(events);
    const evidence = _swarmEvidenceAndConflicts(events);
    const queue = _swarmApprovalQueue(events, approvals);
    const runGoal = run && run.metadata && run.metadata.goal || '';
    const runPack = run && run.metadata && run.metadata.pack || '';
    const autonomy = run && run.metadata && run.metadata.autonomy || 'not recorded';
    const projectionEvent = events.slice().reverse().find(event => event && event.event_type === 'sidekick.kanban_projection_created');
    const projectionFailure = events.slice().reverse().find(event => event && event.event_type === 'sidekick.kanban_projection_failed');
    const projectionTask = projectionEvent && projectionEvent.payload && projectionEvent.payload.kanban_task_id;
    const projectionError = projectionFailure && projectionFailure.payload && projectionFailure.payload.error;
    const projectionStatus = projectionTask ? '<p class="swarm-projection-status"><strong>Kanban triage:</strong> ' + swarmEsc(projectionTask) + '</p>' :
      projectionError ? '<p class="swarm-projection-status swarm-projection-status--failed"><strong>Kanban projection failed:</strong> ' + swarmEsc(projectionError) + '</p>' : '';
    const notice = _swarmState.notice ? '<div class="swarm-notice" role="status">' + swarmEsc(_swarmState.notice) + '</div>' : '';

    const runCard = run ? '<section class="swarm-run-card">' +
      '<div class="swarm-run-card-header"><div><p class="swarm-eyebrow">Run</p><h2>' + swarmEsc(runGoal || run.run_id) + '</h2></div><span class="swarm-status swarm-status--' + swarmEsc(run.status || 'unknown') + '">' + swarmEsc(run.status || 'unknown') + '</span></div>' +
      '<dl class="swarm-metrics"><div><dt>Autonomy</dt><dd>' + swarmEsc(autonomy) + '</dd></div><div><dt>Pack</dt><dd>' + swarmEsc(runPack || 'not recorded') + '</dd></div><div><dt>Calls</dt><dd>' + swarmEsc(String(callCount)) + ' / ' + SWARM_CALL_BUDGET + '</dd></div><div><dt>Updated</dt><dd>' + swarmEsc(_swarmFormatTime(run.updated_at)) + '</dd></div></dl>' +
      (pauseReason ? '<p class="swarm-pause-reason"><strong>Pause reason:</strong> ' + swarmEsc(pauseReason) + '</p>' : '') +
      projectionStatus +
      '<div class="swarm-run-actions">' +
      (run.status === 'running' ? '<button type="button" class="btn secondary" data-swarm-action="pause" data-run-id="' + swarmEsc(run.run_id) + '">Pause run</button>' : '') +
      (run.status === 'paused' ? '<button type="button" class="btn secondary" data-swarm-action="resume" data-run-id="' + swarmEsc(run.run_id) + '">Resume run</button>' : '') +
      '<button type="button" class="btn secondary" data-swarm-action="project-kanban" data-run-id="' + swarmEsc(run.run_id) + '">' + (projectionTask ? 'Kanban task ' + swarmEsc(projectionTask) : 'Project to Kanban triage') + '</button>' +
      '</div></section>' : '<section class="swarm-empty-state"><h2>No run selected</h2><p>Start a project-local run below, or choose one from the run list.</p></section>';

    const roleRows = roles.length ? roles.map(event => {
      const payload = event.payload || {};
      const label = [payload.role, payload.model, event.event_type].filter(Boolean).join(' · ');
      return '<li><strong>' + swarmEsc(label) + '</strong><span>' + swarmEsc(_swarmFormatTime(event.timestamp)) + '</span></li>';
    }).join('') : '<li class="swarm-muted">No recorded role/model activity for this run.</li>';
    const evidenceRows = evidence.length ? evidence.map(event => '<li><strong>' + swarmEsc(event.event_type) + '</strong><span>' + swarmEsc(_swarmEventText(event)) + '</span></li>').join('') : '<li class="swarm-muted">No recorded evidence or conflicts.</li>';
    const approvalRows = queue.length ? queue.map(proposal => '<li><div><strong>' + swarmEsc(proposal.action) + '</strong><span>' + swarmEsc(proposal.proposalId) + '</span></div><div class="swarm-approval-actions"><button type="button" class="btn secondary" data-swarm-action="approve" data-run-id="' + swarmEsc(run && run.run_id || '') + '" data-proposal-id="' + swarmEsc(proposal.proposalId) + '">Approve</button><button type="button" class="btn secondary" data-swarm-action="deny" data-run-id="' + swarmEsc(run && run.run_id || '') + '" data-proposal-id="' + swarmEsc(proposal.proposalId) + '">Deny</button></div></li>').join('') : '<li class="swarm-muted">No persisted human approvals are waiting.</li>';

    main.innerHTML = '<div class="swarm-layout">' +
      '<section class="swarm-create-card"><div><p class="swarm-eyebrow">Project-local control</p><h1>Swarm</h1><p class="swarm-project-path">' + swarmEsc(_swarmState.projectPath) + '</p></div><div class="swarm-create-form"><label for="swarmGoal">Goal</label><input id="swarmGoal" maxlength="500" placeholder="Describe the reviewed work to run"><label for="swarmPack">Pack</label><select id="swarmPack">' + _swarmPackOptions() + '</select><button type="button" class="btn primary" data-swarm-action="start">Start Swarm run</button></div></section>' +
      notice + runCard +
      '<section class="swarm-grid"><article class="swarm-section"><div class="swarm-section-head"><h3>Role and model activity</h3></div><ul class="swarm-event-list">' + roleRows + '</ul></article>' +
      '<article class="swarm-section"><div class="swarm-section-head"><h3>Evidence and conflicts</h3></div><ul class="swarm-event-list">' + evidenceRows + '</ul></article>' +
      '<article class="swarm-section"><div class="swarm-section-head"><h3>Human approval queue</h3></div><ul class="swarm-approval-list">' + approvalRows + '</ul></article>' +
      '<article class="swarm-section"><div class="swarm-section-head"><h3>Catalog and models</h3><button type="button" class="btn secondary" data-swarm-action="refresh-catalog">Refresh catalog</button></div><p class="swarm-catalog-summary">' + swarmEsc(_swarmCatalogSummary()) + '</p></article>' +
      '</section></div>';
    main.onclick = _handleSwarmMainClick;
  }

  async function loadSwarm(options) {
    const projectPath = swarmProjectPath();
    if (!projectPath) {
      stopSwarmStream();
      _swarmResetForProject('');
      _renderSwarmSidebar();
      _renderSwarmMain();
      return;
    }
    const resetSelection = !!(options && options.resetSelection);
    if (_swarmState.projectPath !== projectPath || resetSelection) {
      stopSwarmStream();
      _swarmResetForProject(projectPath);
    }
    const loadSequence = ++_swarmLoadSequence;
    const responses = await Promise.allSettled([
      _swarmRequest(_swarmPath('/api/swarm/runs', projectPath)),
      _swarmRequest(_swarmPath('/api/swarm/packs', projectPath)),
      _swarmRequest(_swarmPath('/api/swarm/models', projectPath)),
    ]);
    if (loadSequence !== _swarmLoadSequence || projectPath !== _swarmState.projectPath) return;
    const runsPayload = _swarmParseResponse(responses[0], {runs: []});
    const packsPayload = _swarmParseResponse(responses[1], {packs: []});
    const modelsPayload = _swarmParseResponse(responses[2], {catalog: null});
    _swarmState.runs = Array.isArray(runsPayload && runsPayload.runs) ? runsPayload.runs : [];
    _swarmState.packs = Array.isArray(packsPayload && packsPayload.packs) ? packsPayload.packs : [];
    _swarmState.catalog = modelsPayload && modelsPayload.catalog || null;
    if (!_swarmState.selectedRunId || !_swarmState.runs.some(run => run && run.run_id === _swarmState.selectedRunId)) {
      _swarmState.selectedRunId = _swarmState.runs[0] && _swarmState.runs[0].run_id || '';
      _swarmState.detail = null;
      _swarmState.cursor = 0;
    }
    const runError = _swarmErrorMessage(responses[0]);
    _swarmSetNotice(runError && !_swarmState.runs.length ? runError : '');
    _renderSwarmSidebar();
    _renderSwarmMain();
    if (_swarmState.selectedRunId) await _loadSwarmDetail(projectPath, loadSequence);
    else stopSwarmStream();
  }

  async function _loadSwarmDetail(projectPath, loadSequence) {
    const runId = _swarmState.selectedRunId;
    if (!projectPath || !runId) return;
    try {
      const detail = await _swarmRequest(_swarmPath('/api/swarm/runs/' + encodeURIComponent(runId), projectPath));
      if (loadSequence !== _swarmLoadSequence || projectPath !== _swarmState.projectPath || runId !== _swarmState.selectedRunId) return;
      _swarmState.detail = detail || null;
      const events = _swarmEventList();
      _swarmState.cursor = events.reduce((cursor, event) => Math.max(cursor, Number(event && event.sequence || 0)), 0);
      _renderSwarmMain();
      startSwarmStream(projectPath, runId, _swarmState.cursor);
    } catch (error) {
      if (loadSequence !== _swarmLoadSequence) return;
      _swarmSetNotice(error && error.message ? error.message : 'Unable to load the selected Swarm run.');
      _renderSwarmMain();
    }
  }

  function startSwarmStream(projectPath, runId, cursor) {
    const key = [projectPath, runId].join('::');
    if (_swarmEventSource && _swarmStreamKey === key) return;
    stopSwarmStream();
    if (!projectPath || !runId || typeof EventSource !== 'function') return;
    const url = '/api/swarm/runs/events/stream?project_path=' + encodeURIComponent(projectPath) +
      '&run_id=' + encodeURIComponent(runId) + '&since=' + encodeURIComponent(String(cursor || 0));
    try {
      const stream = new EventSource(_eventSourceUrl(url));
      _swarmEventSource = stream;
      _swarmStreamKey = key;
      stream.addEventListener('hello', function (event) {
        try {
          const payload = JSON.parse(event.data || '{}');
          _swarmState.cursor = Math.max(_swarmState.cursor, Number(payload.cursor || 0));
        } catch (_) {}
      });
      stream.addEventListener('events', function (event) {
        try {
          const payload = JSON.parse(event.data || '{}');
          _swarmState.cursor = Math.max(_swarmState.cursor, Number(payload.cursor || 0));
        } catch (_) {}
        if (_swarmState.projectPath === projectPath && _swarmState.selectedRunId === runId) void loadSwarm();
      });
      stream.onerror = function () {
        // Native EventSource reconnects GET-only streams.  Do not fall back to
        // a mutating operation or restart a run after a transient outage.
      };
    } catch (_) {
      stopSwarmStream();
    }
  }

  function stopSwarmStream() {
    if (_swarmEventSource) {
      try { _swarmEventSource.close(); } catch (_) {}
    }
    _swarmEventSource = null;
    _swarmStreamKey = '';
  }

  async function swarmStartRun() {
    const projectPath = swarmProjectPath();
    const goalInput = document.getElementById('swarmGoal');
    const packInput = document.getElementById('swarmPack');
    const goal = String(goalInput && goalInput.value || '').trim();
    const pack = String(packInput && packInput.value || 'coding-team').trim();
    if (!projectPath) return _swarmNotify('Configure a project directory before starting Swarm.', 'error');
    if (!goal) return _swarmNotify('Enter a goal before starting Swarm.', 'error');
    if (!confirm('Start this project-local Swarm run? It can use model-call budget.')) return;
    await _swarmWrite('/api/swarm/runs', {project_path: projectPath, goal: goal, pack: pack}, 'Swarm run started.');
  }

  async function swarmPauseRun(runId) {
    const projectPath = swarmProjectPath();
    if (!projectPath || !runId) return;
    if (!confirm('Pause this Swarm run?')) return;
    await _swarmWrite('/api/swarm/runs/' + encodeURIComponent(runId) + '/pause', {project_path: projectPath}, 'Swarm run paused.');
  }

  async function swarmResumeRun(runId) {
    const projectPath = swarmProjectPath();
    if (!projectPath || !runId) return;
    if (!confirm('Resume this Swarm run?')) return;
    await _swarmWrite('/api/swarm/runs/' + encodeURIComponent(runId) + '/resume', {project_path: projectPath}, 'Swarm run resumed.');
  }

  async function swarmDecideApproval(runId, proposalId, deny) {
    const projectPath = swarmProjectPath();
    if (!projectPath || !runId || !proposalId) return;
    const decision = deny ? 'deny' : 'approve';
    if (!confirm('Confirm human decision: ' + decision + ' proposal ' + proposalId + '?')) return;
    await _swarmWrite(
      '/api/swarm/runs/' + encodeURIComponent(runId) + '/approve',
      {project_path: projectPath, proposal_id: proposalId, deny: !!deny},
      'Human decision recorded.'
    );
  }

  async function swarmRefreshCatalog() {
    const projectPath = swarmProjectPath();
    if (!projectPath) return;
    if (!confirm('Refresh the persisted Swarm catalog now? This is an explicit provider operation.')) return;
    await _swarmWrite('/api/swarm/models/refresh', {project_path: projectPath}, 'Catalog refresh requested.');
  }

  async function swarmProjectToKanban(runId) {
    const projectPath = swarmProjectPath();
    if (!projectPath || !runId) return;
    if (!confirm('Create a Sidekick Kanban triage task for this Swarm run? This will not dispatch a worker.')) return;
    await _swarmWrite(
      '/api/swarm/runs/' + encodeURIComponent(runId) + '/kanban-projection',
      {project_path: projectPath},
      'Kanban triage projection recorded.'
    );
  }

  async function _swarmWrite(path, payload, successMessage) {
    try {
      await _swarmRequest(path, {method: 'POST', body: JSON.stringify(payload)});
      _swarmNotify(successMessage, 'success');
      await loadSwarm();
    } catch (error) {
      _swarmNotify(error && error.message ? error.message : 'Swarm control failed.', 'error');
    }
  }

  function _handleSwarmMainClick(event) {
    const button = event.target.closest('[data-swarm-action]');
    if (!button) return;
    const action = button.dataset.swarmAction;
    const runId = String(button.dataset.runId || '');
    const proposalId = String(button.dataset.proposalId || '');
    if (action === 'start') void swarmStartRun();
    if (action === 'pause') void swarmPauseRun(runId);
    if (action === 'resume') void swarmResumeRun(runId);
    if (action === 'approve') void swarmDecideApproval(runId, proposalId, false);
    if (action === 'deny') void swarmDecideApproval(runId, proposalId, true);
    if (action === 'refresh-catalog') void swarmRefreshCatalog();
    if (action === 'project-kanban') void swarmProjectToKanban(runId);
  }

  window.loadSwarm = loadSwarm;
  window.stopSwarmStream = stopSwarmStream;
  window.swarmStartRun = swarmStartRun;
  window.swarmPauseRun = swarmPauseRun;
  window.swarmResumeRun = swarmResumeRun;
  window.swarmDecideApproval = swarmDecideApproval;
  window.swarmRefreshCatalog = swarmRefreshCatalog;
  window.swarmProjectToKanban = swarmProjectToKanban;
})();
