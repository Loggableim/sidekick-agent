"""Regression tests for cron job output retention.

Bug (observed 2026-09-14 on a live install): ``save_job_output`` wrote one
timestamped ``.md`` per run and nothing pruned them - 119,857 files
accumulated over three months (one job dir at 21,661 files), degrading
NTFS directory performance.
"""

from __future__ import annotations

import time

import pytest


@pytest.fixture
def isolated_cron_env(monkeypatch, tmp_path):
    import runtime.cron.jobs as jobs_mod

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(jobs_mod, "CRON_DIR", cron_dir, raising=False)
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", cron_dir / "jobs.json", raising=False)
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", cron_dir / "output", raising=False)
    monkeypatch.setattr(jobs_mod, "SIDEKICK_HOME_DIR", tmp_path, raising=False)
    return jobs_mod, cron_dir / "output"


def test_save_job_output_prunes_beyond_retention(isolated_cron_env, monkeypatch):
    jobs_mod, output_dir = isolated_cron_env
    cap = jobs_mod._JOB_OUTPUT_RETENTION

    # Pre-fill the job dir with more files than the retention cap.
    job_dir = output_dir / "probe-job"
    job_dir.mkdir(parents=True)
    for i in range(cap + 50):
        f = job_dir / f"2026-01-01_00-00-{i:02d}.md"
        f.write_text(f"old-{i}", encoding="utf-8")
        # Deterministic mtimes: older files first.
        stamp = time.time() - (cap + 50 - i) * 60
        import os
        os.utime(f, (stamp, stamp))

    written = jobs_mod.save_job_output("probe-job", "fresh output")

    files = sorted(job_dir.iterdir())
    assert len(files) == cap, (
        f"job dir must be pruned to the retention cap (got {len(files)})"
    )
    assert written.exists(), "the fresh output must survive pruning"
    assert written.read_text(encoding="utf-8") == "fresh output"
    # The OLDEST files must be the ones removed.
    assert "old-0000.md" not in {f.name for f in files}
    assert "old-0049.md" not in {f.name for f in files}


def test_save_job_output_no_prune_under_retention(isolated_cron_env):
    jobs_mod, output_dir = isolated_cron_env

    written = jobs_mod.save_job_output("small-job", "only run")

    job_dir = output_dir / "small-job"
    files = list(job_dir.iterdir())
    assert len(files) == 1
    assert files[0] == written
    assert written.read_text(encoding="utf-8") == "only run"