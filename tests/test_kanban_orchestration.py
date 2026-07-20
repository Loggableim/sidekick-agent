from types import SimpleNamespace


def test_trigger_accepts_direct_and_reverse_imperatives():
    from web.api.kanban_orchestration import is_kanban_orchestration_request

    assert is_kanban_orchestration_request("kanban orchestriert") is True
    assert is_kanban_orchestration_request("Kanban-Board orchestrieren") is True
    assert is_kanban_orchestration_request("orchestriere das über das Kanban-Board") is True


def test_trigger_rejects_noun_or_failure_report():
    from web.api.kanban_orchestration import is_kanban_orchestration_request

    assert is_kanban_orchestration_request("Die Kanban-Orchestrierung funktioniert nicht") is False
    assert is_kanban_orchestration_request("Was ist ein Kanban-Board?") is False


def test_activate_preserves_existing_tools_and_adds_kanban_once():
    from web.api.kanban_orchestration import activate_kanban_orchestration

    session = SimpleNamespace(enabled_toolsets=["terminal", "file"])

    assert activate_kanban_orchestration(session, "kanban orchestriert") is True
    assert session.enabled_toolsets == ["terminal", "file", "kanban"]


def test_activate_uses_defaults_when_session_has_no_override():
    from web.api.kanban_orchestration import activate_kanban_orchestration

    session = SimpleNamespace(enabled_toolsets=None)

    assert activate_kanban_orchestration(session, "kanban orchestriert", ["terminal"]) is True
    assert session.enabled_toolsets == ["terminal", "kanban"]
