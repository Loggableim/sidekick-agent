from __future__ import annotations

import json
import os
import sqlite3
import stat
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
    pinned_swarm_database,
)
from swarm_core import store as store_module
from swarm_core.store import ProjectSwarmStore


def _project_with_external_swarm_link(tmp_path: Path) -> tuple[Path, Path]:
    """Create a trusted project whose .swarm entry resolves outside it."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    try:
        (project / ".swarm").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(
            f"directory symlinks are unavailable in this test environment: {exc}"
        )
    return project, outside


def test_initialize_rejects_external_swarm_link_before_writing(tmp_path: Path):
    """Catches init creating config/runtime files through an escaping .swarm link."""
    project, outside = _project_with_external_swarm_link(tmp_path)

    with pytest.raises(ValueError, match="Swarm.*outside.*project"):
        initialize_project(project)

    assert list(outside.iterdir()) == []


def test_store_entries_reject_external_swarm_link_for_reads_and_writes(tmp_path: Path):
    """Catches the SQLite store following an escaping .swarm link on any entry path."""
    project, outside = _project_with_external_swarm_link(tmp_path)
    initialize_project(outside)
    ProjectSwarmStore(outside)
    before = {
        path.relative_to(outside): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }

    with pytest.raises(ValueError, match="Swarm.*outside.*project"):
        ProjectSwarmStore(project)
    with pytest.raises(ValueError, match="Swarm.*outside.*project"):
        load_project_config(project)
    with pytest.raises(ValueError, match="Swarm.*outside.*project"):
        ProjectSwarmStore.open_read_only(project)

    assert {
        path.relative_to(outside): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    } == before


def test_existing_store_rechecks_swarm_containment_before_each_connection(
    tmp_path: Path,
):
    """Catches a state swap after construction redirecting later SQLite calls."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    store = ProjectSwarmStore(project)
    store.create_run(run_id="inside")
    reader = ProjectSwarmStore.open_read_only(project)
    initialize_project(outside)
    ProjectSwarmStore(outside)
    before = {
        path.relative_to(outside): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    }
    (project / ".swarm").rename(project / "preserved-swarm")
    try:
        (project / ".swarm").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(
            f"directory symlinks are unavailable in this test environment: {exc}"
        )

    with pytest.raises(ValueError, match="Swarm.*outside.*project"):
        store.create_run(run_id="must-not-write-outside")
    with pytest.raises(ValueError, match="Swarm.*outside.*project"):
        reader.list_runs()

    assert {
        path.relative_to(outside): path.read_bytes()
        for path in outside.rglob("*")
        if path.is_file()
    } == before


def test_config_publish_race_cannot_replace_a_file_outside_the_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a `.swarm` swap between validation and the config replacement."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    saved_swarm = project / "saved-swarm"
    original_replace = config_module.os.replace
    swapped = False
    swap_blocked = False

    def swap_swarm_before_replace(source, destination, *args, **kwargs):
        nonlocal swapped, swap_blocked
        if not swapped:
            try:
                (project / ".swarm").rename(saved_swarm)
            except PermissionError:
                swap_blocked = True
                return original_replace(source, destination, *args, **kwargs)
            try:
                (project / ".swarm").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                saved_swarm.rename(project / ".swarm")
                pytest.skip(
                    f"directory symlinks are unavailable in this test environment: {exc}"
                )
            swapped = True
        return original_replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(config_module.os, "replace", swap_swarm_before_replace)
    try:
        config = initialize_project(project)
    finally:
        if (project / ".swarm").is_symlink():
            (project / ".swarm").unlink()
        if saved_swarm.exists():
            saved_swarm.rename(project / ".swarm")

    assert config.project_root == project.resolve()
    assert not (outside / "swarm.yaml").exists()
    assert swapped or swap_blocked
    assert load_project_config(project).version == 1


def test_sqlite_connect_race_cannot_write_a_database_outside_the_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a `.swarm` swap immediately before SQLite opens its database."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    store = ProjectSwarmStore(project)
    external_db = outside / "runtime" / "swarm.sqlite"
    external_db.parent.mkdir()
    with sqlite3.connect(external_db) as connection:
        connection.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )

    saved_swarm = project / "saved-swarm"
    original_connect = store_module.sqlite3.connect
    swapped = False
    swap_blocked = False

    def swap_swarm_before_connect(database, *args, **kwargs):
        nonlocal swapped, swap_blocked
        if not swapped:
            try:
                (project / ".swarm").rename(saved_swarm)
            except PermissionError:
                swap_blocked = True
                return original_connect(database, *args, **kwargs)
            try:
                (project / ".swarm").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                saved_swarm.rename(project / ".swarm")
                pytest.skip(
                    f"directory symlinks are unavailable in this test environment: {exc}"
                )
            swapped = True
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", swap_swarm_before_connect)
    try:
        store.create_run(run_id="must-stay-inside")
    finally:
        if (project / ".swarm").is_symlink():
            (project / ".swarm").unlink()
        if saved_swarm.exists():
            saved_swarm.rename(project / ".swarm")

    with original_connect(external_db) as connection:
        external_rows = connection.execute(
            "SELECT run_id FROM runs WHERE run_id = ?", ("must-stay-inside",)
        ).fetchall()
    assert swapped or swap_blocked
    assert external_rows == []


def test_read_only_sqlite_connect_race_cannot_read_an_external_database(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Catches a late link swap redirecting a status-only SQLite connection."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    store = ProjectSwarmStore(project)
    store.create_run(run_id="inside-only")
    reader = ProjectSwarmStore.open_read_only(project)
    external_db = outside / "runtime" / "swarm.sqlite"
    external_db.parent.mkdir()
    with sqlite3.connect(external_db) as connection:
        connection.execute(
            """
            CREATE TABLE runs (
                run_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            INSERT INTO runs (run_id, status, created_at, updated_at, metadata_json)
            VALUES ('outside-only', 'running', '2026-01-01T00:00:00+00:00',
                    '2026-01-01T00:00:00+00:00', '{}')
            """
        )

    saved_swarm = project / "saved-swarm"
    original_connect = store_module.sqlite3.connect
    swapped = False
    swap_blocked = False

    def swap_swarm_before_connect(database, *args, **kwargs):
        nonlocal swapped, swap_blocked
        if not swapped:
            try:
                (project / ".swarm").rename(saved_swarm)
            except PermissionError:
                swap_blocked = True
                return original_connect(database, *args, **kwargs)
            try:
                (project / ".swarm").symlink_to(outside, target_is_directory=True)
            except OSError as exc:
                saved_swarm.rename(project / ".swarm")
                pytest.skip(
                    f"directory symlinks are unavailable in this test environment: {exc}"
                )
            swapped = True
        return original_connect(database, *args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", swap_swarm_before_connect)
    try:
        observed_run_ids = [run.run_id for run in reader.list_runs()]
    finally:
        if (project / ".swarm").is_symlink():
            (project / ".swarm").unlink()
        if saved_swarm.exists():
            saved_swarm.rename(project / ".swarm")

    assert swapped or swap_blocked
    assert observed_run_ids == ["inside-only"]


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only modes")
def test_posix_mutating_store_repairs_swarm_runtime_and_database_modes(
    tmp_path: Path,
):
    """Existing broad POSIX Swarm state is repaired only on a write-capable path."""
    project = tmp_path / "project"
    store = ProjectSwarmStore(project)
    database = project / ".swarm" / "runtime" / "swarm.sqlite"
    swarm_dir = project / ".swarm"
    runtime_dir = swarm_dir / "runtime"

    os.chmod(swarm_dir, 0o755)
    os.chmod(runtime_dir, 0o755)
    os.chmod(database, 0o644)

    ProjectSwarmStore(project).create_run(run_id="modes-repaired")

    assert stat.S_IMODE(swarm_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(database.stat().st_mode) == 0o600
    assert swarm_dir.stat().st_uid == runtime_dir.stat().st_uid == os.geteuid()
    assert database.stat().st_uid == os.geteuid()
    assert store.get_run("modes-repaired") is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX owner-only modes")
@pytest.mark.parametrize(
    ("relative_path", "broad_mode", "expected_mode"),
    (
        ((".swarm",), 0o755, 0o700),
        ((".swarm", "runtime"), 0o755, 0o700),
        ((".swarm", "runtime", "swarm.sqlite"), 0o644, 0o600),
    ),
)
def test_posix_read_only_open_rejects_broad_swarm_state_without_repair(
    tmp_path: Path,
    relative_path: tuple[str, ...],
    broad_mode: int,
    expected_mode: int,
):
    """A status request must never silently chmod broad Swarm state."""
    project = tmp_path / "project"
    ProjectSwarmStore(project).create_run(run_id="inside-only")
    target = project.joinpath(*relative_path)
    os.chmod(target, broad_mode)

    with pytest.raises(
        ValueError,
        match=rf"owner-only \(mode {expected_mode:04o}\)",
    ):
        ProjectSwarmStore.open_read_only(project)

    assert stat.S_IMODE(target.stat().st_mode) == broad_mode
    ProjectSwarmStore(project).create_run(run_id="modes-repaired")
    assert stat.S_IMODE(target.stat().st_mode) == expected_mode


@pytest.mark.skipif(os.name == "nt", reason="POSIX direct-child identity gate")
def test_posix_database_child_swap_before_sqlite_open_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A swap after the safe fd opens but before SQLite must not call SQLite."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    external_db = outside / "swarm.sqlite"
    external_db.write_bytes(b"outside sentinel")
    store = ProjectSwarmStore(project)
    database = project / ".swarm" / "runtime" / "swarm.sqlite"
    preserved = project / ".swarm" / "runtime" / "preserved.sqlite"
    before = external_db.read_bytes()
    original_assert = config_module.PinnedSwarmDatabase.assert_database_identity
    swapped = False

    def swap_then_assert(pinned):
        nonlocal swapped
        if not swapped:
            database.rename(preserved)
            database.symlink_to(external_db)
            swapped = True
        return original_assert(pinned)

    def sqlite_must_not_open(*_args, **_kwargs):
        raise AssertionError("identity gate must run before sqlite3.connect")

    monkeypatch.setattr(
        config_module.PinnedSwarmDatabase,
        "assert_database_identity",
        swap_then_assert,
    )
    monkeypatch.setattr(store_module.sqlite3, "connect", sqlite_must_not_open)
    try:
        with pytest.raises(ValueError, match="database changed"):
            store.create_run(run_id="must-not-open-outside")
    finally:
        if database.is_symlink():
            database.unlink()
        if preserved.exists():
            preserved.rename(database)

    assert swapped is True
    assert external_db.read_bytes() == before


@pytest.mark.skipif(os.name == "nt", reason="POSIX direct-child identity gate")
@pytest.mark.parametrize("read_only", (False, True))
def test_posix_database_child_swap_during_sqlite_open_fails_before_sql(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    read_only: bool,
):
    """A late same-UID swap is rejected before a reader or writer issues SQL."""
    project = tmp_path / "project"
    outside = tmp_path / "outside"
    outside.mkdir()
    external_db = outside / "swarm.sqlite"
    with sqlite3.connect(external_db) as external:
        external.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        external.execute("INSERT INTO sentinel (value) VALUES ('outside-only')")

    store = ProjectSwarmStore(project)
    store.create_run(run_id="inside-only")
    reader = ProjectSwarmStore.open_read_only(project)
    database = project / ".swarm" / "runtime" / "swarm.sqlite"
    preserved = project / ".swarm" / "runtime" / "preserved.sqlite"
    before = {
        path.name: path.read_bytes()
        for path in outside.iterdir()
        if path.is_file()
    }
    original_connect = store_module.sqlite3.connect
    swapped = False

    def swap_child_during_connect(database_uri, *args, **kwargs):
        nonlocal swapped
        if not swapped:
            database.rename(preserved)
            database.symlink_to(external_db)
            swapped = True
        return original_connect(database_uri, *args, **kwargs)

    monkeypatch.setattr(store_module.sqlite3, "connect", swap_child_during_connect)
    try:
        with pytest.raises(ValueError, match="database changed"):
            if read_only:
                reader.list_runs()
            else:
                store.create_run(run_id="must-not-write-outside")
    finally:
        if database.is_symlink():
            database.unlink()
        if preserved.exists():
            preserved.rename(database)

    assert swapped is True
    assert {
        path.name: path.read_bytes()
        for path in outside.iterdir()
        if path.is_file()
    } == before


def test_concurrent_process_initializers_publish_only_complete_swarm_yaml(
    tmp_path: Path,
    monkeypatch,
):
    """A process racing initialization must see a complete config, never YAML mid-write."""
    publish_started = threading.Event()
    release_first_publish = threading.Event()
    parent_result: dict[str, object] = {}
    source_root = Path(__file__).resolve().parents[1]
    original_replace = config_module.os.replace

    def delayed_config_replace(source, destination, *args, **kwargs):
        if Path(destination).name == "swarm.yaml" and not publish_started.is_set():
            publish_started.set()
            assert release_first_publish.wait(timeout=10)
        return original_replace(source, destination, *args, **kwargs)

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
            # Child import resolution stays explicit while the parent holds
            # its descriptor/handle-based publication lock.
            cwd=source_root,
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
        # Let the writer leave its descriptor/handle publication section
        # before this process performs a separate read-only observation.
        release_first_publish.set()
        parent.join(timeout=10)
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


def test_config_and_read_only_store_never_change_the_process_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """A status read must not briefly redirect unrelated process-relative I/O."""
    project = tmp_path / "project"
    project.mkdir()
    store = ProjectSwarmStore(project)
    store.create_run(run_id="inside-only")
    original_cwd = Path.cwd()

    def forbidden_chdir(*_args, **_kwargs):
        raise AssertionError("Swarm config/store must never call os.chdir")

    # Checking only CWD before/after would permit the old implementation,
    # which temporarily changed a process-global CWD under a Swarm-only lock.
    monkeypatch.setattr(config_module.os, "chdir", forbidden_chdir)

    assert load_project_config(project).project_root == project.resolve()
    reader = ProjectSwarmStore.open_read_only(project)
    assert [run.run_id for run in reader.list_runs()] == ["inside-only"]
    assert Path.cwd() == original_cwd


@pytest.mark.skipif(os.name == "nt", reason="POSIX descriptor paths use /proc/self/fd")
def test_posix_descriptor_runtime_path_is_accepted_by_sqlite_validation(tmp_path: Path):
    """Keep the descriptor-parent comparison portable beyond the Windows host."""
    project = tmp_path / "project"
    project.mkdir()
    store = ProjectSwarmStore(project)
    store.create_run(run_id="inside-only")

    with pinned_swarm_database(project, read_only=True) as pinned:
        assert str(pinned.runtime_dir).startswith("/proc/self/fd/")
        connection = sqlite3.connect(pinned.database_path, uri=pinned.uri)
        try:
            # `runtime_dir` intentionally is an unresolved descriptor path,
            # while SQLite reports the resolved real path.  Comparing the
            # resolved expected parent, not the lexical descriptor parent, is
            # what makes the validation fail closed and work on POSIX.
            store_module._validate_pinned_database_connection(
                connection,
                project.resolve(),
                pinned.runtime_dir,
            )
        finally:
            connection.close()
