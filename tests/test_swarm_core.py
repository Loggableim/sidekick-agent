from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from swarm_core.config import initialize_project
from swarm_core.events import SwarmEventBus
from swarm_core.store import ProjectSwarmStore


def test_initialize_project_creates_versionable_default_configuration(tmp_path: Path):
    """Catches a missing project-local layout or incorrect provider defaults."""
    config = initialize_project(tmp_path)

    config_path = tmp_path / ".swarm" / "swarm.yaml"
    assert config_path.is_file()
    assert yaml.safe_load(config_path.read_text(encoding="utf-8")) == {
        "version": 1,
        "default_provider": "ollama-cloud",
        "default_model": "deepseek-v4-flash",
        "default_autonomy": "reviewed_execution",
    }
    assert config.project_root == tmp_path
    assert config.default_provider == "ollama-cloud"
    assert config.default_model == "deepseek-v4-flash"
    assert config.default_autonomy == "reviewed_execution"


def test_initialize_project_keeps_runtime_state_ignored_but_creates_its_directory(
    tmp_path: Path,
):
    """Catches runtime state becoming versionable alongside swarm.yaml."""
    initialize_project(tmp_path)

    swarm_dir = tmp_path / ".swarm"
    assert (swarm_dir / "runtime").is_dir()
    assert (swarm_dir / ".gitignore").read_text(encoding="utf-8") == "runtime/\n"
    assert (swarm_dir / "swarm.yaml").is_file()


def test_initialize_project_persists_default_autonomy_into_older_config(
    tmp_path: Path,
):
    """Catches upgraded projects using an implicit, non-versioned autonomy default."""
    swarm_dir = tmp_path / ".swarm"
    swarm_dir.mkdir()
    config_path = swarm_dir / "swarm.yaml"
    config_path.write_text(
        "version: 1\n"
        "default_provider: ollama-cloud\n"
        "default_model: deepseek-v4-flash\n",
        encoding="utf-8",
    )

    config = initialize_project(tmp_path)

    assert config.default_autonomy == "reviewed_execution"
    assert (
        yaml.safe_load(config_path.read_text(encoding="utf-8"))["default_autonomy"]
        == "reviewed_execution"
    )


def test_store_constructor_keeps_direct_runtime_state_ignored(tmp_path: Path):
    """Catches direct store use creating an unignored runtime database."""
    store = ProjectSwarmStore(tmp_path)

    ignore_path = tmp_path / ".swarm" / ".gitignore"
    assert store.db_path.is_file()
    assert ignore_path.is_file()
    assert ignore_path.read_text(encoding="utf-8") == "runtime/\n"


def test_events_are_returned_in_monotonic_sequence_order(tmp_path: Path):
    """Catches event order being based on unstable timestamps or insertion order."""
    initialize_project(tmp_path)
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="event-order")

    first = store.append_event(
        run.run_id,
        "task.created",
        {"task": "design"},
        visibility="project",
    )
    second = store.append_event(
        run.run_id,
        "task.started",
        {"task": "design"},
        visibility="project",
    )

    events = store.list_events(run.run_id)
    assert [event.sequence for event in events] == [1, 2]
    assert [event.event_type for event in events] == ["task.created", "task.started"]
    assert [event.event_id for event in events] == [first.event_id, second.event_id]
    assert events[0].timestamp.tzinfo is not None
    assert events[1].payload == {"task": "design"}
    assert events[1].visibility == "project"


def test_event_bus_publishes_events_through_the_project_store(tmp_path: Path):
    """Catches the event bus bypassing durable project-local event storage."""
    initialize_project(tmp_path)
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="bus-run")

    published = SwarmEventBus(store).publish(
        run.run_id, "run.notified", {"message": "ready"}, visibility="owner"
    )

    assert published.sequence == 1
    assert store.list_events(run.run_id) == [published]


def test_paused_run_can_be_resumed(tmp_path: Path):
    """Catches resume leaving a paused run unavailable to later routing."""
    initialize_project(tmp_path)
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="resume-me")

    paused = store.set_run_status(run.run_id, "paused")
    resumed = store.resume_run(run.run_id)

    assert paused.status == "paused"
    assert resumed.status == "running"
    assert store.get_run(run.run_id).status == "running"


def test_terminal_run_cannot_be_resumed(tmp_path: Path):
    """Catches terminal run state being reopened as if it were a pause."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="terminal")
    store.set_run_status(run.run_id, "completed")

    with pytest.raises(ValueError, match="Only paused"):
        store.resume_run(run.run_id)

    assert store.get_run(run.run_id).status == "completed"


def test_terminal_run_cannot_directly_transition_back_to_running(tmp_path: Path):
    """Catches direct status writes reopening a terminal run behind resume_run."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="direct-terminal")
    store.set_run_status(run.run_id, "completed")

    with pytest.raises(ValueError, match="transition"):
        store.set_run_status(run.run_id, "running")

    assert store.get_run(run.run_id).status == "completed"


def test_set_run_status_rejects_unknown_status(tmp_path: Path):
    """Catches arbitrary status labels entering durable run state."""
    store = ProjectSwarmStore(tmp_path)
    run = store.create_run(run_id="known-states")

    with pytest.raises(ValueError, match="Unsupported"):
        store.set_run_status(run.run_id, "teleporting")

    assert store.get_run(run.run_id).status == "running"


def test_reopening_store_restores_persisted_run_and_events(tmp_path: Path):
    """Catches state being held only in process memory instead of swarm.sqlite."""
    initialize_project(tmp_path)
    first_store = ProjectSwarmStore(tmp_path)
    created = first_store.create_run(run_id="persisted", metadata={"goal": "ship"})
    first_store.append_event(created.run_id, "run.created", {"source": "test"})

    reopened_store = ProjectSwarmStore(tmp_path)
    restored = reopened_store.get_run("persisted")

    assert (tmp_path / ".swarm" / "runtime" / "swarm.sqlite").is_file()
    assert restored is not None
    assert restored.run_id == "persisted"
    assert restored.metadata == {
        "autonomy": "reviewed_execution",
        "goal": "ship",
    }
    assert restored.created_at == created.created_at
    assert [event.event_type for event in reopened_store.list_events("persisted")] == [
        "run.created"
    ]
