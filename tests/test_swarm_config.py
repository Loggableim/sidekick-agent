from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from swarm_core import config as config_module
from swarm_core.config import (
    SwarmProjectNotInitializedError,
    initialize_project,
    load_project_config,
)


def test_concurrent_process_initializers_publish_only_complete_swarm_yaml(
    tmp_path: Path,
    monkeypatch,
):
    """A process racing initialization must see a complete config, never YAML mid-write."""
    config_path = tmp_path / ".swarm" / "swarm.yaml"
    publish_started = threading.Event()
    release_first_publish = threading.Event()
    parent_result: dict[str, object] = {}
    original_replace = config_module.os.replace

    def delayed_config_replace(source, destination):
        if Path(destination) == config_path and not publish_started.is_set():
            publish_started.set()
            assert release_first_publish.wait(timeout=10)
        return original_replace(source, destination)

    monkeypatch.setattr(config_module.os, "replace", delayed_config_replace)

    def initialize_in_parent() -> None:
        try:
            parent_result["config"] = initialize_project(tmp_path)
        except BaseException as exc:
            parent_result["error"] = exc

    parent = threading.Thread(target=initialize_in_parent)
    parent.start()
    try:
        assert publish_started.wait(timeout=3), (
            "config initialization must publish through an atomic replacement"
        )
        child = subprocess.run(
            [
                sys.executable,
                "-c",
                (
                    "import json, sys; "
                    "from pathlib import Path; "
                    "from swarm_core.config import initialize_project; "
                    "config = initialize_project(Path(sys.argv[1])); "
                    "print(json.dumps({'version': config.version, "
                    "'provider': config.default_provider, "
                    "'model': config.default_model, "
                    "'autonomy': config.default_autonomy}))"
                ),
                str(tmp_path),
            ],
            cwd=Path.cwd(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
        assert child.returncode == 0, child.stdout + child.stderr
        assert json.loads(child.stdout) == {
            "version": 1,
            "provider": "ollama-cloud",
            "model": "deepseek-v4-flash",
            "autonomy": "reviewed_execution",
        }
        observed = load_project_config(tmp_path)
        assert observed.version == 1
        assert observed.default_provider == "ollama-cloud"
        assert observed.default_model == "deepseek-v4-flash"
        assert observed.default_autonomy == "reviewed_execution"
    finally:
        release_first_publish.set()
        parent.join(timeout=10)

    assert parent.is_alive() is False
    assert "error" not in parent_result
    initialized = parent_result["config"]
    assert initialized.version == 1
    assert initialized.default_provider == "ollama-cloud"
    assert initialized.default_model == "deepseek-v4-flash"
    assert initialized.default_autonomy == "reviewed_execution"
    assert load_project_config(tmp_path).version == 1


def test_read_only_config_load_does_not_initialize_a_missing_project(tmp_path: Path):
    """Status reads must retain their no-create contract during first initialization."""
    with pytest.raises(SwarmProjectNotInitializedError):
        load_project_config(tmp_path)

    assert not (tmp_path / ".swarm").exists()
