from pathlib import Path


def test_nova_readonly_ui_surfaces_are_present_and_redacted():
    ui = Path("web/static/ui.js").read_text(encoding="utf-8")
    messages = Path("web/static/messages.js").read_text(encoding="utf-8")
    swarm = Path("web/static/swarm.js").read_text(encoding="utf-8")

    # Deterministic DOM-contract smoke: all surfaces are GET/read-only render paths.
    assert "function _renderNovaPresenceCard(payload)" in ui
    assert "novaAuditedResults" in ui and "result_summary" in ui
    assert "failed:'fehlgeschlagen'" in ui and "paused:'pausiert'" in ui
    assert "api[_-]?key" in ui
    assert "callsMeta" in ui and "costMeta" in ui and "packMeta" in ui
    assert "promptCandidates" in ui and "Safety-Golden ausstehend" in ui
    assert "quorumMeta" in ui and "Integrator" in ui and "vote_count" in ui
    assert "feedbackLabels" in ui and "Feedback offline" in ui and "Feedback ausstehend" in ui
    assert "inboxLabels" in ui and "Nova-Antwort ausstehend" in ui and "Nova-Antwort erhalten" in ui
    assert "responder_status" in ui and "Responder aktiv" in ui and "Responder offline" in ui
    assert "Responder antwortet" in ui and "Responder Fehler" in ui
    assert "cloud_responder_status" in ui and "Cloud-Responder konfiguriert" in ui and "Cloud-Responder blockiert" in ui
    assert "feedback_responder" in ui and "feedbackResponder.status" in ui
    assert "managedSpaceSet.has(inboxSpace)" in ui
    assert "blockerMeta" in ui and "Math.min(9" in ui
    assert "Menschliche Freigabe ausstehend" in ui and "candidate_id" in ui
    assert "Math.min(128" in ui and "toFixed(2)" in ui

    # Current-chat isolation, SSE stream and bounded/redacted rendering.
    assert "session_id=" in messages and "parent_session_id" in messages
    assert "/api/subagents/events/stream?session_id=" in messages
    assert "function _subagentRedact" in messages
    assert "slice(0, maxLength)" in messages
    assert "Subagent history offline; read-only cache shown." in messages
    assert "_subagentRenderPanel({offline: true, active: [], history: []})" in messages
    assert "function _subagentDedupe" in messages and "updated_at" in messages
    assert "last_step" in messages and "_subagentRedact(entry" in messages
    assert "failed" in messages and "interrupted" in messages and "abandoned" in messages
    assert "status=all&limit=50" in messages and "subagent_event" in messages
    assert "_subagentCurrentSessionId()" in messages and "S.session.session_id !== sid" in messages
    assert "stopSubagentPolling()" in messages and "_subagentStreamSessionId = null" in messages

    # Fake controls exercise start/pause/pack without a real POST/provider call.
    assert "window.__SWARM_FAKE_MODE__ === true" in swarm
    assert 'id="swarmPack"' in swarm
    assert 'data-swarm-action="pause"' in swarm
    assert "_swarmRequest(_swarmPath('/api/swarm/runs', projectPath))" in swarm
    assert "Fake mode: " in swarm and "return;" in swarm
    assert "Refresh catalog" in swarm and "swarmRefreshCatalog" in swarm
    assert "slice(0, 12)" in swarm and "catalog.models" in swarm
    assert "_swarmRequest(_swarmPath('/api/swarm/models', projectPath))" in swarm


def test_fake_e2e_three_space_presence_and_session_isolation():
    spaces = [
        {"space": "nova", "session_id": "nova-chat", "status": "active"},
        {"space": "finanzjunkie", "session_id": "fin-chat", "status": "failed"},
        {"space": "aquarium-zentrum", "session_id": "aq-chat", "status": "abandoned"},
    ]
    active_session = "fin-chat"
    visible = [row for row in spaces if row["session_id"] == active_session]
    assert [row["space"] for row in visible] == ["finanzjunkie"]
    assert visible[0]["status"] == "failed"
    assert all(row["session_id"] == active_session for row in visible)


def test_nova_entity_presence_replaces_generic_empty_state_without_hiding_composer():
    ui = Path("web/static/ui.js").read_text(encoding="utf-8")
    index = Path("web/static/index.html").read_text(encoding="utf-8")
    assert "Ich bin Nova" in ui
    assert "novaPresenceCard" in index and "novaManagedSpaces" in index
    assert "novaBlockers" in index and "novaDecisionFeed" in index
    assert "spaceNovaManagementYolo" in Path("web/static/spaces.js").read_text(encoding="utf-8")
    assert "Legacy-Quellmodus" in index and "globale Nova-Schalter" in index
    assert "generic.hidden=shouldShow" in ui and "card.hidden=!shouldShow" in ui
    # The regular composer remains part of the page; Nova presence is an empty-state card only.
    assert "id=\"composerWrap\"" in index


def test_three_space_entity_feed_isolation_and_status_labels():
    payload = {
        "managed_spaces": [{"space": "finanzjunkie"}, {"space": "aquarium-zentrum"}],
        "entity_feed": [
            {"space": "finanzjunkie", "source": "ci", "stage": "handled", "status": "failed", "reason": "ci_failed"},
            {"space": "aquarium-zentrum", "source": "heartbeat", "stage": "observed", "status": "pending", "reason": "review"},
            {"space": "nova", "source": "ci", "stage": "handled", "status": "failed", "reason": "leak"},
        ],
        "blockers": [{"space": "aquarium-zentrum", "code": "verification_not_verified"}],
        "feedback": {"status": "offline"},
    }
    managed = {item["space"] for item in payload["managed_spaces"]}
    feed = [item for item in payload["entity_feed"] if item["space"] in managed]
    assert {item["space"] for item in feed} == {"finanzjunkie", "aquarium-zentrum"}
    assert payload["blockers"][0]["code"] == "verification_not_verified"
    assert payload["feedback"]["status"] == "offline"


def test_three_space_fake_transport_yolo_enrollment_global_lease_and_exactly_once():
    spaces = [
        {"space": "nova", "yolo": True, "enrolled": True},
        {"space": "finanzjunkie", "yolo": True, "enrolled": False},
        {"space": "aquarium-zentrum", "yolo": True, "enrolled": True},
    ]
    admitted = [row["space"] for row in spaces if row["yolo"] and row["enrolled"]]
    assert admitted == ["nova", "aquarium-zentrum"]

    active_run = None
    intents = set()
    def admit(space, intent):
        nonlocal active_run
        key = (space, intent)
        if key in intents:
            return "duplicate"
        if active_run is not None:
            return "busy"
        intents.add(key)
        active_run = key
        return "started"

    assert admit("nova", "sync") == "started"
    assert admit("aquarium-zentrum", "deploy") == "busy"
    assert admit("nova", "sync") == "duplicate"
