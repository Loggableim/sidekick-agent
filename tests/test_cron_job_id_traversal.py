"""Regression tests for cron job-id path-traversal protection.

Bug (verified live on master 2026-09-14): ``jobs.json`` is hand-editable
and ``/api/cron/delete`` passes ``body["job_id"]`` straight into
``remove_job``, which resolved ``OUTPUT_DIR / job_id`` and called
``shutil.rmtree`` on it - an id like ``../../evil`` deleted an arbitrary
directory. ``save_job_output`` would likewise write outside the cron
directory. The scheduler's script-path guard validated script paths but
job ids were never checked.
"""

import json

import pytest


@pytest.fixture
def isolated_cron_env(monkeypatch, tmp_path):
    import runtime.cron.jobs as jobs_mod

    cron_dir = tmp_path / "cron"
    monkeypatch.setattr(jobs_mod, "CRON_DIR", cron_dir, raising=False)
    monkeypatch.setattr(jobs_mod, "JOBS_FILE", cron_dir / "jobs.json", raising=False)
    monkeypatch.setattr(jobs_mod, "OUTPUT_DIR", cron_dir / "output", raising=False)
    monkeypatch.setattr(jobs_mod, "SIDEKICK_HOME_DIR", tmp_path, raising=False)
    return jobs_mod, cron_dir


def test_is_safe_job_id_accepts_generated_ids():
    from runtime.cron.jobs import _is_safe_job_id

    assert _is_safe_job_id("9a1d470d23cc") is True
    assert _is_safe_job_id("abc123") is True


@pytest.mark.parametrize("bad", ["../../evil", "..", ".", "a/b", "a\\b", ".hidden", "", None])
def test_is_safe_job_id_rejects_traversal(bad):
    from runtime.cron.jobs import _is_safe_job_id

    assert _is_safe_job_id(bad) is False


def test_remove_job_refuses_traversal_id(isolated_cron_env):
    jobs_mod, cron_dir = isolated_cron_env
    # A decoy directory OUTSIDE the cron tree that the traversal would hit.
    outside = cron_dir.parent / "outside-target"
    outside.mkdir()
    (outside / "keep-me.txt").write_text("precious", encoding="utf-8")

    # A job whose id traverses: remove_job must refuse BEFORE rmtree.
    assert jobs_mod.remove_job("../../outside-target") is False
    assert (outside / "keep-me.txt").exists(), "arbitrary directory must not be deleted"


def test_save_job_output_refuses_traversal_id(isolated_cron_env):
    jobs_mod, cron_dir = isolated_cron_env
    outside = cron_dir.parent / "outside-write-target"
    outside.mkdir()

    result = jobs_mod.save_job_output("../../outside-write-target", "poison")

    assert result is None
    assert not (outside / "poison").exists()
    assert list(outside.iterdir()) == [], "no files may be written outside"


def test_remove_job_still_removes_real_jobs(isolated_cron_env):
    jobs_mod, cron_dir = isolated_cron_env
    job_id = "9a1d470d23cc"
    job_dir = cron_dir / "output" / job_id
    job_dir.mkdir(parents=True)
    (job_dir / "2026-09-14_00-00-00.md").write_text("out", encoding="utf-8")
    (cron_dir / "jobs.json").write_text(
        json.dumps({"jobs": [{"id": job_id, "name": "x"}]}), encoding="utf-8"
    )

    assert jobs_mod.remove_job(job_id) is True
    assert not job_dir.exists()
