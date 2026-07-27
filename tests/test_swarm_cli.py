from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from cli.swarm import build_parser, swarm_command


def _parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build_parser(subparsers)
    return parser.parse_args(argv)


def test_cli_init_is_explicit_and_status_on_missing_project_is_read_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Catches a harmless CLI read creating .swarm before the user asks for init."""
    project = tmp_path / "project"
    project.mkdir()

    missing_status = _parse(["swarm", "--project", str(project), "--json", "status"])
    assert swarm_command(missing_status) == 1
    assert not (project / ".swarm").exists()

    initialized = _parse(["swarm", "--project", str(project), "--json", "init"])
    assert swarm_command(initialized) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["project_root"] == str(project.resolve())
    assert (project / ".swarm" / "swarm.yaml").is_file()


def test_cli_run_and_status_never_trigger_model_refresh(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    """Catches a convenient CLI dispatch path silently mutating the catalog."""
    calls: list[tuple[str, object]] = []

    class FakeService:
        def run(self, goal, project_root, *, pack):
            calls.append(("run", (goal, project_root, pack)))
            return {"run_id": "run-1", "status": "paused"}

        def status(self, project_root, run_id=None):
            calls.append(("status", (project_root, run_id)))
            return {"runs": []}

        def refresh_models(self, _project_root):
            raise AssertionError("run/status must not refresh models")

        def list_packs(self, _project_root):
            return []

        def pause(self, *_args):
            raise AssertionError("not expected")

        def resume(self, *_args):
            raise AssertionError("not expected")

        def record_human_approval(self, *_args, **_kwargs):
            raise AssertionError("not expected")

    project = tmp_path / "project"
    project.mkdir()
    service = FakeService()

    run_args = _parse(["swarm", "--project", str(project), "--json", "run", "inspect"])
    status_args = _parse(["swarm", "--project", str(project), "--json", "status"])

    assert swarm_command(run_args, service=service) == 0
    assert swarm_command(status_args, service=service) == 0
    assert [name for name, _value in calls] == ["run", "status"]
    assert '"run_id": "run-1"' in capsys.readouterr().out


def test_cli_approval_accepts_only_a_human_decision(
    tmp_path: Path,
):
    """Catches CLI flags forging a verifier/model quorum or caller identity."""
    project = tmp_path / "project"
    project.mkdir()
    with pytest.raises(SystemExit):
        _parse(
            [
                "swarm",
                "--project",
                str(project),
                "approve",
                "run-1",
                "proposal-1",
                "--approval-type",
                "model",
            ]
        )

    approvals: list[tuple] = []

    class FakeService:
        def record_human_approval(
            self, project_root, run_id, proposal_id, *, actor_id, approved
        ):
            approvals.append((project_root, run_id, proposal_id, actor_id, approved))
            return {"approval_type": "human", "approved": approved}

    args = _parse(
        [
            "swarm",
            "--project",
            str(project),
            "--json",
            "approve",
            "run-1",
            "proposal-1",
            "--deny",
        ]
    )

    assert (
        swarm_command(
            args,
            service=FakeService(),
            actor_factory=lambda: "os:uid:4242",
        )
        == 0
    )
    assert approvals == [
        (project.resolve(), "run-1", "proposal-1", "os:uid:4242", False)
    ]


def test_cli_models_refresh_and_packs_list_are_explicit_commands(
    tmp_path: Path,
):
    """Catches refresh or pack loading being hidden in unrelated command dispatch."""
    project = tmp_path / "project"
    project.mkdir()
    calls: list[str] = []

    class FakeService:
        def refresh_models(self, project_root):
            calls.append(f"refresh:{project_root}")
            return {"healthy": True}

        def list_packs(self, project_root):
            calls.append(f"packs:{project_root}")
            return [{"id": "coding-team"}]

    refresh = _parse(["swarm", "--project", str(project), "models", "refresh"])
    packs = _parse(["swarm", "--project", str(project), "packs", "list"])

    assert swarm_command(refresh, service=FakeService()) == 0
    assert swarm_command(packs, service=FakeService()) == 0
    assert calls == [f"refresh:{project.resolve()}", f"packs:{project.resolve()}"]


def test_cli_shared_flags_work_before_or_after_the_action(tmp_path: Path):
    """Catches conventional `swarm run ... --project` invocations being rejected."""
    project = tmp_path / "project"
    project.mkdir()

    before = _parse(["swarm", "--project", str(project), "--json", "init"])
    after = _parse(["swarm", "init", "--project", str(project), "--json"])

    assert Path(before.project) == project
    assert before.json is True
    assert Path(after.project) == project
    assert after.json is True
