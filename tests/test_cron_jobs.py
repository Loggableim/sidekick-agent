from datetime import datetime, timezone
import importlib
from pathlib import Path
import subprocess
import sys


def test_save_job_output_does_not_overwrite_same_second_outputs(monkeypatch, tmp_path):
    import runtime.cron.jobs as cron_jobs

    fixed_now = datetime(2026, 6, 22, 12, 34, 56, tzinfo=timezone.utc)

    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(cron_jobs, "_hermes_now", lambda: fixed_now)

    first = cron_jobs.save_job_output("job-1", "first output")
    second = cron_jobs.save_job_output("job-1", "second output")

    assert first != second
    assert Path(first).read_text(encoding="utf-8") == "first output"
    assert Path(second).read_text(encoding="utf-8") == "second output"


def test_importing_cron_jobs_keeps_gateway_forwarders_importable():
    import runtime.cron.jobs  # noqa: F401

    discord = importlib.import_module("gateway.platforms.discord")

    assert hasattr(discord, "DiscordAdapter")


def test_cron_scheduler_script_runs_outside_repo_cwd(tmp_path):
    scheduler = Path(__file__).resolve().parents[1] / "runtime" / "cron" / "scheduler.py"

    result = subprocess.run(
        [sys.executable, str(scheduler)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=20,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
