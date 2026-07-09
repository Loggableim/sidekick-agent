from datetime import datetime, timezone
import importlib
import os
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


def test_load_jobs_removes_stale_tmp_files(monkeypatch, tmp_path):
    import runtime.cron.jobs as cron_jobs

    monkeypatch.setattr(cron_jobs, "HERMES_DIR", tmp_path)
    monkeypatch.setattr(cron_jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")

    cron_jobs.CRON_DIR.mkdir(parents=True, exist_ok=True)
    cron_jobs.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cron_jobs.JOBS_FILE.write_text('{"jobs": []}', encoding="utf-8")

    stale_paths = [
        cron_jobs.JOBS_FILE.with_name("jobs.json.aaaa1111.tmp"),
        cron_jobs.JOBS_FILE.with_name("jobs.json.bbbb2222.tmp"),
    ]
    for path in stale_paths:
        path.write_text("stale", encoding="utf-8")
        old_ts = 1_600_000_000
        os.utime(path, (old_ts, old_ts))

    jobs = cron_jobs.load_jobs()

    assert jobs == []
    assert all(not path.exists() for path in stale_paths)
