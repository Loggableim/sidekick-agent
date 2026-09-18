"""Contract tests for scripts/restart_dashboard.ps1.

The script restarts the dashboard that serves the live WebUI. Its earlier
version slept a fixed 90 s and then killed every dashboard process, which
murdered any agent turn that ran longer than 90 s (verified 2026-09-18: two
WebUI sessions died mid-turn). These tests pin the replacement contract:

1. The wait is a poll loop on the live worker counters, not a fixed sleep.
2. Ambiguity is treated as busy — the script never kills a turn it cannot see.
3. The kill filter only matches real python dashboard processes for the
   target port, never shells that merely mention the string.
4. A timeout leaves the server alone instead of killing it.
"""

from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "restart_dashboard.ps1"


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_restart_script_exists_and_has_no_fixed_sleep_kill():
    """The fixed 90 s sleep that killed long turns must be gone."""
    script = _script_text()

    assert "Start-Sleep -Seconds $delay" not in script
    assert "$delay = 90" not in script


def test_restart_script_polls_live_worker_counters():
    """Idle detection must use the /health worker counters when available."""
    script = _script_text()

    assert "active_runs" in script
    assert "active_streams" in script
    assert "/health" in script
    # The wait loop must confirm idle across several consecutive polls so a
    # turn that starts between two probes cannot be killed.
    assert "IdleConfirmPolls" in script
    assert "idleStreak" in script


def test_restart_script_treats_ambiguous_health_as_busy():
    """An unanswered probe on a listening port must count as busy."""
    script = _script_text()

    # The fallback path for old servers without counters.
    assert "Test-SessionFilesBusy" in script
    assert "active_stream_id" in script
    # Timeout on a listening port is busy, not idle.
    assert "health probe timed out" in script
    assert "port $Port not listening" in script


def test_restart_script_timeout_does_not_kill():
    """A wait timeout must abort without touching the server."""
    script = _script_text()

    assert "NOT restarting" in script
    # The timeout path exits before the kill block.
    wait_call = script.index("$idle = Wait-ForIdle")
    timeout_exit = script.index("if (-not $idle) { exit 1 }")
    kill_block = script.index("$killed = @()")
    assert wait_call < timeout_exit < kill_block


def test_restart_script_kill_filter_is_port_scoped_and_python_only():
    """Only real python dashboard processes for the target port may match."""
    script = _script_text()

    kill_block = script[
        script.index("$killed = @()") : script.index("Write-RestartLog \"killed PIDs")
    ]

    # Must require a python interpreter, not a shell that mentions the string.
    assert "$_.Name -match '^python(w)?\\.exe$'" in kill_block
    assert "-m\\s+sidekick_app\\s+dashboard" in kill_block
    # Must be scoped to the target port.
    assert "--port[=\\s]+$Port\\b" in kill_block


def test_restart_script_supports_dry_run_and_force():
    """Operators need a safe preview and an explicit override."""
    script = _script_text()

    assert "[switch]$DryRun" in script
    assert "[switch]$Force" in script
    assert "dry run: would restart now" in script


def test_restart_script_is_dot_source_safe():
    """The script must be importable for tests without running the restart."""
    script = _script_text()

    assert "$MyInvocation.InvocationName -ne '.'" in script
