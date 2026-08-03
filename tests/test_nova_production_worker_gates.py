from nova import production_workers as workers


def test_github_worker_rechecks_typed_requests_before_subprocess(monkeypatch, tmp_path):
    handle = workers.ManagedWorktreeHandle(tmp_path, tmp_path, "run", "identity", "a" * 64)
    calls = []
    monkeypatch.setattr(
        workers,
        "_run_argv",
        lambda *args, **kwargs: calls.append(args) or workers.WorkerResult(True, "ok"),
    )
    worker = workers.ProductionGitHubWorker()
    assert worker.execute(handle, workers.GitHubPushRequest("../main")).code == "operation_hard_denied"
    assert worker.execute(handle, workers.GitHubReleaseRequest("v1", "ok", "token=secret")).code == "operation_hard_denied"
    assert worker.execute(handle, workers.GitHubPullRequestRequest("title", "body", False)).code == "ok"
    assert len(calls) == 1
