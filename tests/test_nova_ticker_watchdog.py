from __future__ import annotations

import sqlite3
from pathlib import Path
from uuid import uuid4

from nova.space_supervisor import ManagedSpaceGovernance, ManagedSpaceSupervisor
from nova.ticker_watchdog import inspect_ticker_liveness


def _supervisor(tmp_path: Path) -> ManagedSpaceSupervisor:
    governance = ManagedSpaceGovernance.from_values(
        space_id=str(uuid4()),
        canonical_root=tmp_path / "alpha",
        root_fingerprint="",
        yolo=True,
        enrolled=True,
        revision=1,
        policy_identity="space-governance:1",
    )
    return ManagedSpaceSupervisor(
        ledger_path=tmp_path / "supervisor.sqlite",
        governance_resolver=lambda _target: governance,
    )


def test_missing_ticker_lease_is_reported_without_creating_a_ledger(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)

    result = inspect_ticker_liveness(supervisor=supervisor, now=100.0)

    assert result.status == "missing"
    assert result.alert_code == "ticker_missing"
    assert not (tmp_path / "supervisor.sqlite").exists()


def test_stale_ticker_lease_is_reported_without_mutating_the_lease(tmp_path: Path) -> None:
    supervisor = _supervisor(tmp_path)
    assert supervisor.acquire_ticker_lease("host-a", now=100.0, ttl_seconds=600.0)
    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        before = connection.execute(
            "SELECT state, updated_at, expires_at FROM supervisor_ticker_leases"
        ).fetchone()

    result = inspect_ticker_liveness(supervisor=supervisor, now=281.0)

    with sqlite3.connect(tmp_path / "supervisor.sqlite") as connection:
        after = connection.execute(
            "SELECT state, updated_at, expires_at FROM supervisor_ticker_leases"
        ).fetchone()
    assert result.status == "stale"
    assert result.alert_code == "ticker_stale"
    assert result.age_seconds == 181
    assert after == before



def test_ticker_restart_generations_keep_lease_cleanup_local() -> None:
    """A retiring generation must not release a replacement ticker lease."""
    source = (Path(__file__).parents[1] / "cli" / "web_server.py").read_text(encoding="utf-8")

    assert "def _loop(ticker_lifecycle: dict[str, Any]) -> None:" in source
    assert "ticker_lifecycle: dict[str, Any] = {" in source
    assert "_loop(ticker_lifecycle)" in source
    # There must be no shared outer lifecycle object that an old finally block
    # could overwrite while the restart guard starts the next generation.
    assert '    ticker_lifecycle: dict[str, Any] = {\"lease_stop\": None' not in source
