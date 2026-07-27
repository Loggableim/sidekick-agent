from __future__ import annotations

import re
from pathlib import Path


def test_swarm_panel_is_reachable_from_both_navigation_surfaces_and_shell_cache():
    """Catches Swarm becoming an orphaned page that mobile users or offline shells cannot reach."""
    index_html = Path("web/static/index.html").read_text(encoding="utf-8")
    service_worker = Path("web/static/sw.js").read_text(encoding="utf-8")

    sidebar_nav = re.search(
        r'<div class="sidebar-nav">(.*?)<!-- Sidebar Space Selector -->',
        index_html,
        re.S,
    )
    assert sidebar_nav, "mobile sidebar nav block should be present"
    assert 'data-panel="swarm"' in index_html
    assert 'data-panel="swarm"' in sidebar_nav.group(1)
    assert 'id="panelSwarm"' in index_html
    assert 'id="mainSwarm"' in index_html
    assert 'href="/static/swarm.css?v=__WEBUI_VERSION__"' in index_html
    assert 'src="/static/swarm.js?v=__WEBUI_VERSION__"' in index_html
    assert "'./static/swarm.css' + VQ" in service_worker
    assert "'./static/swarm.js' + VQ" in service_worker


def test_swarm_client_uses_explicit_project_paths_and_stops_its_stream_on_panel_exit():
    """Catches a Space slug reaching Swarm as a path or an SSE connection surviving a panel switch."""
    swarm_js = Path("web/static/swarm.js").read_text(encoding="utf-8")
    panels_js = Path("web/static/panels.js").read_text(encoding="utf-8")
    spaces_js = Path("web/static/spaces.js").read_text(encoding="utf-8")
    style_css = Path("web/static/style.css").read_text(encoding="utf-8")

    assert "window._activeSpaceConfig" in swarm_js
    assert "project_dir" in swarm_js
    assert "project_path" in swarm_js
    assert "_eventSourceUrl" in swarm_js
    assert "EventSource" in swarm_js
    assert "function stopSwarmStream" in swarm_js
    assert "prevPanel === 'swarm' && nextPanel !== 'swarm'" in panels_js
    assert "stopSwarmStream" in panels_js
    assert "panel === 'swarm'" in spaces_js
    assert "loadSwarm" in spaces_js
    assert "swarm: 'tab_swarm'" in panels_js
    assert "'swarm'" in panels_js
    assert "main.main.showing-swarm > #mainSwarm" in style_css


def test_swarm_client_keeps_every_observation_read_only_until_an_explicit_control():
    """Catches initial load, polling, or SSE handlers mutating Swarm or refreshing a catalog."""
    swarm_js = Path("web/static/swarm.js").read_text(encoding="utf-8")

    load_start = swarm_js.index("async function loadSwarm")
    controls_start = swarm_js.index("async function swarmStartRun")
    load_body = swarm_js[load_start:controls_start]

    assert "method: 'POST'" not in load_body
    assert "/api/swarm/models/refresh" not in load_body
    assert "async function swarmStartRun" in swarm_js
    assert "async function swarmPauseRun" in swarm_js
    assert "async function swarmResumeRun" in swarm_js
    assert "async function swarmDecideApproval" in swarm_js
    assert "async function swarmRefreshCatalog" in swarm_js
    assert "async function swarmProjectToKanban" in swarm_js
    assert "catalog.models" in swarm_js
    assert "choices: " in swarm_js
    assert "sidekick.kanban_projection_failed" in swarm_js
    assert "project_path: projectPath" in swarm_js
    assert "proposal_id: proposalId" in swarm_js
    assert "deny: !!deny" in swarm_js
    assert "actor_id" not in swarm_js
    assert "model_family" not in swarm_js
    assert "confirm(" in swarm_js
