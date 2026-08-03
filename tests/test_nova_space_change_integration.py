"""Cross-system regressions for managed Space governance lifecycle changes."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
import yaml

from nova.space_supervisor import (
    ManagedSpaceGovernance,
    ManagedSpaceSupervisor,
    resolve_managed_space_governance,
)
from swarm_core.store import ProjectSwarmStore


_DASHBOARD_ACTOR = "dashboard:" + ("d" * 64)


def _active_managed_space(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Create one real Space and one active child through the supervisor."""
    from nova import space_supervisor as supervisor_module
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    project_root = tmp_path / "project-a"
    project_root.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy-spaces")
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)

    space = space_engine.Space("alpha", "Alpha")
    space.save_config(
        {"name": "Alpha", "project_dir": str(project_root)},
        mint_space_id=True,
    )
    initial = space.load_config()
    enabled = space_engine.update_nova_management(
        space,
        yolo=True,
        enrolled=True,
        confirmation={
            "space_id": initial["space_id"],
            "root_fingerprint": space_engine.space_root_fingerprint(project_root),
        },
        trusted_project_root=project_root,
        actor=_DASHBOARD_ACTOR,
    )
    records = {
        "alpha": ManagedSpaceGovernance.from_values(
            space_id=initial["space_id"],
            canonical_root=project_root,
            root_fingerprint="",
            yolo=True,
            enrolled=True,
            revision=enabled["revision"],
            policy_identity="space-governance:" + str(enabled["revision"]),
        )
    }
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )
    admission = supervisor.admit("alpha", {"kind": "space-change"})
    assert admission.capability is not None and admission.run_id is not None
    assert supervisor.start_admitted_run(
        admission.capability,
        dispatcher=lambda _root, _run_id: None,
    )
    run = ProjectSwarmStore(project_root).get_run(admission.run_id)
    assert run is not None and run.status == "running"
    monkeypatch.setattr(
        supervisor_module,
        "get_production_managed_space_supervisor",
        lambda: supervisor,
    )
    return space_engine, space, project_root, records, supervisor, admission


def _pause_reasons(supervisor: ManagedSpaceSupervisor, admission_id: str) -> list[str]:
    with supervisor._read_connection() as connection:
        rows = connection.execute(
            "SELECT reason FROM supervisor_audit WHERE admission_id = ? ORDER BY sequence",
            (admission_id,),
        ).fetchall()
    return [str(row["reason"]) for row in rows if row["reason"] is not None]


def _pause_actors(supervisor: ManagedSpaceSupervisor, admission_id: str) -> list[str]:
    """Return the durable actor attached to each lifecycle pause."""
    with supervisor._read_connection() as connection:
        rows = connection.execute(
            """SELECT actor FROM supervisor_audit
               WHERE admission_id = ? AND event_type = 'paused'
               ORDER BY sequence""",
            (admission_id,),
        ).fetchall()
    return [str(row["actor"]) for row in rows if row["actor"] is not None]


def test_confirmed_management_revocation_pauses_the_active_child_before_returning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a governance write leaving an already-running managed child alive."""
    space_engine, space, project_root, _records, supervisor, admission = _active_managed_space(
        monkeypatch, tmp_path
    )

    changed = space_engine.update_nova_management(
        space,
        yolo=False,
        enrolled=False,
        confirmation=None,
        trusted_project_root=project_root,
        actor=_DASHBOARD_ACTOR,
    )

    run = ProjectSwarmStore(project_root).get_run(admission.run_id)
    assert changed == {"yolo": False, "enrolled": False, "revision": 2}
    assert run is not None and run.status == "paused"
    assert "governance_changed" in _pause_reasons(supervisor, admission.admission_id)


def test_project_root_change_pauses_the_active_child_before_persisting_new_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches a generic project-dir write retargeting a live managed run."""
    space_engine, space, project_root, _records, supervisor, admission = _active_managed_space(
        monkeypatch, tmp_path
    )
    replacement_root = tmp_path / "project-b"
    replacement_root.mkdir()

    changed = space_engine.update_space_config(
        space,
        {"project_dir": str(replacement_root)},
    )

    run = ProjectSwarmStore(project_root).get_run(admission.run_id)
    assert changed["project_dir"] == str(replacement_root)
    assert run is not None and run.status == "paused"
    assert "root_changed" in _pause_reasons(supervisor, admission.admission_id)
    assert _pause_actors(supervisor, admission.admission_id) == [
        "system:space-lifecycle"
    ]


def test_root_change_fails_closed_when_the_child_pause_cannot_be_confirmed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A ledger pause alone cannot authorize replacing a running child's root."""
    space_engine, space, project_root, _records, supervisor, admission = _active_managed_space(
        monkeypatch, tmp_path
    )
    replacement_root = tmp_path / "project-b"
    replacement_root.mkdir()

    class FailingPauseStore:
        def set_run_status(self, *_args, **_kwargs):
            raise OSError("durable child pause unavailable")

        def append_event_once(self, *_args, **_kwargs):
            raise AssertionError("event must not be attempted after status failure")

    supervisor._child_store_factory = lambda _root: FailingPauseStore()  # type: ignore[method-assign]

    with pytest.raises(space_engine.SpaceGovernanceError):
        space_engine.update_space_config(space, {"project_dir": str(replacement_root)})

    assert space.load_config()["project_dir"] == str(project_root)
    run = ProjectSwarmStore(project_root).get_run(admission.run_id)
    assert run is not None and run.status == "running"
    assert supervisor.list_active_admissions()[0]["state"] == "paused"
    assert "root_changed" in _pause_reasons(supervisor, admission.admission_id)


def test_deleting_an_enrolled_space_pauses_its_active_child_before_removing_space(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches deletion removing governance evidence while its child still executes."""
    space_engine, space, project_root, _records, supervisor, admission = _active_managed_space(
        monkeypatch, tmp_path
    )

    assert space_engine.delete_space(space.slug) is True

    run = ProjectSwarmStore(project_root).get_run(admission.run_id)
    assert not space.root.exists()
    assert run is not None and run.status == "paused"
    assert "space_deleted" in _pause_reasons(supervisor, admission.admission_id)
    assert _pause_actors(supervisor, admission.admission_id) == [
        "system:space-lifecycle"
    ]


def test_delete_rejects_path_traversal_before_resolving_space_directories(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A raw delete slug must never make ``shutil.rmtree`` leave a Space root."""
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    victim = tmp_path / "victim"
    victim.mkdir()
    marker = victim / "must-survive.txt"
    marker.write_text("outside the Space root", encoding="utf-8")
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy-spaces")

    assert space_engine.delete_space("../victim") is False
    assert marker.read_text(encoding="utf-8") == "outside the Space root"


def test_malformed_space_config_still_pauses_active_child_before_delete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Corrupt YAML cannot bypass the ledger-owned deletion pause gate."""
    space_engine, space, project_root, _records, supervisor, admission = _active_managed_space(
        monkeypatch, tmp_path
    )
    space.config_path.write_text("{not valid", encoding="utf-8")

    assert space_engine.delete_space(space.slug) is True

    run = ProjectSwarmStore(project_root).get_run(admission.run_id)
    assert not space.root.exists()
    assert run is not None and run.status == "paused"
    assert "space_deleted" in _pause_reasons(supervisor, admission.admission_id)


def test_unmanaged_space_config_change_never_resolves_the_supervisor(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Catches generic Space writes gaining a Nova runtime side effect."""
    from nova import space_supervisor as supervisor_module
    from web.api import space_engine

    spaces_root = tmp_path / "spaces"
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy-spaces")
    space = space_engine.Space("ordinary", "Ordinary")
    space.save_config({"name": "Ordinary", "project_dir": str(first_root)})
    monkeypatch.setattr(
        supervisor_module,
        "get_production_managed_space_supervisor",
        lambda: (_ for _ in ()).throw(AssertionError("ordinary Space must stay inert")),
    )

    changed = space_engine.update_space_config(
        space,
        {"project_dir": str(second_root)},
    )

    assert changed["project_dir"] == str(second_root)


@pytest.mark.parametrize("tamper", ("space_id", "root_fingerprint"))
def test_production_governance_resolver_fails_closed_on_tampered_space_evidence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    tamper: str,
) -> None:
    """A raw Space YAML change cannot mint authority or initialize a child."""
    from web.api import space_engine, workspace

    spaces_root = tmp_path / "spaces"
    project_root = tmp_path / "trusted-project"
    project_root.mkdir()
    # The production resolver must obtain this value from the independent
    # enrollment trust boundary.  Keeping that boundary injected avoids a
    # profile-home bootstrap mutating the temporary Space roots in this test.
    monkeypatch.setattr(
        workspace,
        "resolve_enrollment_trusted_workspace_read_only",
        lambda _value: project_root,
    )
    monkeypatch.setattr(space_engine, "SPACES_ROOT", spaces_root)
    monkeypatch.setattr(space_engine, "_OLD_ROOT", tmp_path / "legacy-spaces")
    monkeypatch.setattr(space_engine, "_SPACE_CACHE", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_ROOTS", None)
    monkeypatch.setattr(space_engine, "_SPACE_CACHE_TS", 0.0)

    space = space_engine.Space("alpha", "Alpha")
    space.save_config(
        {"name": "Alpha", "project_dir": str(project_root)},
        mint_space_id=True,
    )
    initial = space.load_config()
    space_engine.update_nova_management(
        space,
        yolo=True,
        enrolled=True,
        confirmation={
            "space_id": initial["space_id"],
            "root_fingerprint": space_engine.space_root_fingerprint(project_root),
        },
        trusted_project_root=project_root,
        actor=_DASHBOARD_ACTOR,
    )
    assert resolve_managed_space_governance("alpha").space_id == initial["space_id"]

    raw = yaml.safe_load(space.config_path.read_text(encoding="utf-8"))
    assert isinstance(raw, dict)
    if tamper == "space_id":
        raw["space_id"] = str(uuid4())
    else:
        raw["nova_management_audit"][-1]["root_fingerprint"] = "0" * 64
    space.config_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=resolve_managed_space_governance,
    )
    result = supervisor.admit("alpha", {"kind": "tampered-evidence"})

    assert result.status == "rejected"
    assert result.reason == "not_yolo_enrolled"
    assert not (project_root / ".swarm").exists()


def test_fake_injected_dispatcher_composes_a_redacted_blocker_without_live_wiring(
    tmp_path: Path,
) -> None:
    """The only host/Telegram composition is explicit, fake, and bounded."""
    from nova.notifications import (
        NovaTelegramNotifications,
        PrivateTelegramTarget,
    )
    from nova.space_supervision_runtime import NovaSpaceSupervisionRuntime

    records = {
        "alpha": ManagedSpaceGovernance.from_values(
            space_id=str(uuid4()),
            canonical_root=tmp_path / "alpha",
            root_fingerprint="",
            yolo=True,
            enrolled=True,
            revision=1,
            policy_identity="space-governance:1",
        )
    }
    supervisor = ManagedSpaceSupervisor(
        ledger_path=tmp_path / "state" / "supervisor.sqlite",
        governance_resolver=lambda target: records[target],
    )
    messages: list[tuple[int, str]] = []

    class FakePrivateSender:
        def send_private(self, chat_id: int, text: str) -> None:
            messages.append((chat_id, text))

    notifier = NovaTelegramNotifications(
        supervisor=supervisor,
        target=PrivateTelegramTarget.from_config(
            {"chat_id": 123456, "chat_type": "private"}
        ),
        sender=FakePrivateSender(),
        allowed_space_ids={records["alpha"].space_id},
    )
    raw_secret = "token=must-not-leave-fake-host"

    def fake_host_dispatch(root: Path, run_id: str) -> None:
        # The callback receives only the supervisor-bound root/run pair; it
        # has no capability, model transport, worker credential, or scheduler.
        assert root == records["alpha"].canonical_root
        assert notifier.send_blocker(
            space_id=records["alpha"].space_id,
            display_name="Alpha " + raw_secret,
            run_id=run_id,
            blocker_code="dispatch_failed",
        ) == "sent"
        raise RuntimeError(raw_secret)

    runtime = NovaSpaceSupervisionRuntime(
        supervisor=supervisor,
        dispatch_run=fake_host_dispatch,
    )
    assert runtime.ingest_signal(
        "alpha", source="git", event_id="fake-host-event", reason_code="git_change"
    )

    outcomes = runtime.pulse(now_epoch=0.0)

    assert [(outcome.target_key, outcome.status) for outcome in outcomes] == [
        ("alpha", "start_failed")
    ]
    run_id = outcomes[0].run_id
    assert run_id is not None
    run = ProjectSwarmStore(records["alpha"].canonical_root).get_run(run_id)
    assert run is not None and run.status == "paused"
    assert len(messages) == 1
    _chat_id, text = messages[0]
    assert raw_secret not in text
    assert run_id not in text
    assert "dispatch failed" in text.lower()
    assert notifier.send_blocker(
        space_id=records["alpha"].space_id,
        display_name="Alpha",
        run_id=run_id,
        blocker_code="dispatch_failed",
    ) == "already_claimed"
