"""Versionable project-local Swarm configuration.

The runtime layout is security-sensitive: a trusted project must never gain
permission to follow a later ``.swarm`` symlink into a different project.  The
pinning layer below therefore uses directory descriptors on POSIX and native
directory handles on Windows.  In particular, it never changes the process
working directory; status reads may run beside unrelated Sidekick requests.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import errno
import os
from pathlib import Path
import secrets
import stat
import threading
import time
from typing import Any, Iterator

import yaml

from .types import SwarmConfig


_DEFAULT_CONFIG = {
    "version": 1,
    "default_provider": "ollama-cloud",
    "default_model": "deepseek-v4-flash",
    "default_autonomy": "reviewed_execution",
}

_AUTONOMY_LEVELS = {
    "observe",
    "suggest",
    "execute_safe",
    "reviewed_execution",
    "autonomous",
}

_CONFIG_INITIALIZATION_LOCK = threading.RLock()
_SWARM_DIRECTORY_PIN_LOCK = threading.RLock()
_REPLACE_RETRY_DELAYS = (0.01, 0.02, 0.05, 0.1, 0.2)
_SQLITE_RUNTIME_FILENAMES = (
    "swarm.sqlite",
    "swarm.sqlite-journal",
    "swarm.sqlite-wal",
    "swarm.sqlite-shm",
)


class SwarmProjectNotInitializedError(FileNotFoundError):
    """Raised when a read-only caller targets a project without Swarm state."""


class SwarmProjectPathError(ValueError):
    """Raised when a Swarm path resolves outside its trusted project root."""


@dataclass(frozen=True)
class PinnedSwarmDatabase:
    """An SQLite target held below a verified, stable runtime directory."""

    database_path: str
    uri: bool
    # On POSIX this deliberately remains a live /proc/self/fd reference.  A
    # late lexical `.swarm` swap then resolves to the original descriptor, not
    # to the replacement name.
    runtime_dir: Path
    database_filename: str = "swarm.sqlite"
    # The surrounding ``pinned_swarm_database`` context keeps this descriptor
    # alive.  POSIX cannot give stock SQLite a pre-opened descriptor while
    # retaining safe journal/WAL sidecars, so Store performs a pre/post
    # identity check around the one SQLite open instead.
    posix_runtime_fd: int | None = field(default=None, repr=False, compare=False)
    database_identity: tuple[int, int] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    def assert_database_identity(self) -> None:
        """Fail closed if the pinned direct database child was replaced.

        On Windows, the held no-delete handle is the native protection.  On
        POSIX this is intentionally a best-effort identity gate around
        SQLite's open: the owner-only runtime directory is the OS boundary,
        while this check catches a stale/in-process same-UID replacement
        before any Swarm SQL is allowed to inspect or mutate it.
        """
        if self.posix_runtime_fd is None or self.database_identity is None:
            return
        try:
            current = os.stat(
                self.database_filename,
                dir_fd=self.posix_runtime_fd,
                follow_symlinks=False,
            )
        except OSError as exc:
            raise SwarmProjectPathError(
                "Pinned Swarm database disappeared before SQLite could open it"
            ) from exc
        if (
            not stat.S_ISREG(current.st_mode)
            or (current.st_dev, current.st_ino) != self.database_identity
        ):
            raise SwarmProjectPathError(
                "Pinned Swarm database changed during SQLite open"
            )


@dataclass
class _DirectoryLease:
    """A platform-native held directory with direct-child operations only."""

    project_root: Path
    path: Path
    posix_fd: int | None = None
    windows_handle: int | None = None

    def close(self) -> None:
        if self.posix_fd is not None:
            os.close(self.posix_fd)
            self.posix_fd = None
        if self.windows_handle is not None:
            _close_windows_handle(self.windows_handle)
            self.windows_handle = None

    def read_text(self, filename: str) -> str:
        _validate_relative_filename(filename)
        descriptor = _open_regular_file(self, filename, read_only=True, create=False)
        try:
            stream = os.fdopen(descriptor, "r", encoding="utf-8")
        except BaseException:
            # Ownership only transfers after fdopen succeeds.
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        with stream:
            return stream.read()

    def write_text(self, filename: str, document: str) -> None:
        """Atomically replace a direct child without a lexical parent lookup."""
        _validate_relative_filename(filename)
        temporary_name = f".{filename}.{secrets.token_hex(16)}.tmp"
        descriptor: int | None = None
        try:
            descriptor = _open_regular_file(
                self,
                temporary_name,
                read_only=False,
                create=True,
            )
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = None
                stream.write(document)
                stream.flush()
                os.fsync(stream.fileno())
            for delay in (*_REPLACE_RETRY_DELAYS, None):
                try:
                    _replace_direct_child(self, temporary_name, filename)
                    break
                except PermissionError:
                    if delay is None:
                        raise
                    time.sleep(delay)
        except BaseException:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            _unlink_direct_child(self, temporary_name)
            raise

    @contextmanager
    def hold_regular_file(
        self,
        filename: str,
        *,
        read_only: bool,
        create: bool,
        owner_only: bool = False,
        repair_owner_only: bool = False,
    ) -> Iterator[int]:
        """Hold a non-reparse regular child through an SQLite connection."""
        _validate_relative_filename(filename)
        descriptor = _open_regular_file(
            self,
            filename,
            read_only=read_only,
            create=create,
        )
        try:
            if owner_only:
                _ensure_posix_owner_only_regular_file(
                    descriptor,
                    filename,
                    repair=repair_owner_only,
                )
            yield descriptor
        finally:
            os.close(descriptor)

    def sqlite_target(
        self,
        filename: str,
        *,
        read_only: bool,
        database_identity: tuple[int, int] | None = None,
    ) -> PinnedSwarmDatabase:
        """Return a URI whose parent remains tied to this held directory."""
        _validate_relative_filename(filename)
        mode = "ro" if read_only else "rw"
        if self.posix_fd is not None:
            reference = _posix_fd_reference(self.posix_fd)
            return PinnedSwarmDatabase(
                database_path=f"file:{reference.as_posix()}/{filename}?mode={mode}",
                uri=True,
                runtime_dir=reference,
                database_filename=filename,
                posix_runtime_fd=self.posix_fd,
                database_identity=database_identity,
            )
        # The native directory hierarchy handles exclude DELETE sharing, so
        # these absolute names cannot be rebound while SQLite opens them.
        database = self.path / filename
        return PinnedSwarmDatabase(
            database_path=f"{database.as_uri()}?mode={mode}",
            uri=True,
            runtime_dir=self.path,
            database_filename=filename,
        )


@dataclass(frozen=True)
class _SwarmLease:
    """The pinned `.swarm` directory and, optionally, its runtime child."""

    swarm: _DirectoryLease
    runtime: _DirectoryLease | None = None


def load_project_config(project_root: Path) -> SwarmConfig:
    """Load an existing config without creating or upgrading anything."""
    project_root = _resolved_project_root(project_root)
    config_path = resolve_swarm_path(project_root, "swarm.yaml")
    try:
        with _pinned_swarm_directory(project_root, create=False) as lease:
            raw_config = yaml.safe_load(lease.swarm.read_text("swarm.yaml")) or {}
    except FileNotFoundError:
        raise SwarmProjectNotInitializedError(
            f"Swarm project is not initialized: {project_root}"
        ) from None
    if not isinstance(raw_config, dict):
        raise ValueError(f"Swarm configuration must be a mapping: {config_path}")
    return _to_config(project_root, config_path, raw_config)


def initialize_project(project_root: Path) -> SwarmConfig:
    """Create (or load) the versionable layout for one project."""
    project_root = _resolved_project_root(project_root)
    with _CONFIG_INITIALIZATION_LOCK:
        # ``sidekick swarm init C:\\new\\project`` has always been allowed to
        # materialize the explicitly named project directory.  Do that before
        # opening the root handle; read-only paths deliberately never call this
        # helper.  All security-sensitive children remain descriptor/handle
        # pinned below the resulting root.
        _ensure_project_root(project_root)
        config_path = resolve_swarm_path(project_root, "swarm.yaml")
        with _pinned_swarm_directory(
            project_root,
            create=True,
            runtime=True,
        ) as lease:
            _ensure_runtime_is_ignored(lease.swarm)
            try:
                raw_config = yaml.safe_load(lease.swarm.read_text("swarm.yaml")) or {}
            except FileNotFoundError:
                raw_config = dict(_DEFAULT_CONFIG)
                _write_project_config(lease.swarm, raw_config)
            if not isinstance(raw_config, dict):
                raise ValueError(
                    f"Swarm configuration must be a mapping: {config_path}"
                )
            if "default_autonomy" not in raw_config:
                raw_config["default_autonomy"] = _DEFAULT_CONFIG["default_autonomy"]
                _write_project_config(lease.swarm, raw_config)
            return _to_config(project_root, config_path, raw_config)


def resolve_swarm_path(project_root: Path, *parts: str) -> Path:
    """Resolve a Swarm path only when it remains in ``project_root``.

    This is the fast lexical/static check.  Every actual file operation also
    goes through :func:`_pinned_swarm_directory`, which closes the swap window.
    """
    root = _resolved_project_root(project_root)
    swarm_dir = _contained_path(root, root / ".swarm", label=".swarm directory")
    if not parts:
        return swarm_dir
    relative = Path(*parts)
    if relative.is_absolute() or ".." in relative.parts:
        raise SwarmProjectPathError("Swarm paths must remain beneath .swarm")
    return _contained_path(
        root,
        swarm_dir / relative,
        label=f".swarm/{relative.as_posix()}",
    )


@contextmanager
def pinned_swarm_database(
    project_root: Path,
    *,
    read_only: bool,
) -> Iterator[PinnedSwarmDatabase]:
    """Pin the runtime directory and database file for one SQLite connection."""
    project_root = _resolved_project_root(project_root)
    with _pinned_swarm_directory(
        project_root,
        create=not read_only,
        runtime=True,
    ) as lease:
        runtime = lease.runtime
        if runtime is None:  # pragma: no cover - protected by runtime=True
            raise RuntimeError("Swarm runtime directory was not pinned")
        _ensure_runtime_sqlite_files_owner_only(runtime, repair=not read_only)
        with runtime.hold_regular_file(
            "swarm.sqlite",
            read_only=read_only,
            create=not read_only,
            owner_only=True,
            repair_owner_only=not read_only,
        ) as database_descriptor:
            identity = _posix_regular_file_identity(database_descriptor)
            try:
                yield runtime.sqlite_target(
                    "swarm.sqlite",
                    read_only=read_only,
                    database_identity=identity,
                )
            finally:
                # SQLite may create a rollback journal, WAL, or SHM file only
                # after the main connection is open.  Repair their modes on
                # the mutating path before releasing the held runtime handle.
                if not read_only:
                    _ensure_runtime_sqlite_files_owner_only(runtime, repair=True)


def read_project_pack_override_texts(project_root: Path) -> tuple[tuple[str, str], ...]:
    """Return project pack YAML through a pinned ``.swarm/packs`` directory.

    Pack metadata is versioned project input, but it must not make a trusted
    project root an ambient permission to follow a later directory link.  The
    static path check rejects an already-escaping link; the held directory
    handles then keep enumeration and each direct-child read anchored to the
    same ``packs`` directory if a swap races afterward.  A missing Swarm
    layout or missing override directory intentionally means no overrides.
    """
    project_root = _resolved_project_root(project_root)
    # This is a fast rejection for an already present escaping link.  Actual
    # enumeration and reads below are descriptor/handle-pinned as well, so it
    # is not relied on as the only race defense.
    resolve_swarm_path(project_root, "packs")
    try:
        with _pinned_swarm_directory(project_root, create=False) as swarm_lease:
            try:
                with _pinned_child_directory(
                    swarm_lease.swarm,
                    "packs",
                    create=False,
                    label=".swarm/packs",
                ) as packs_lease:
                    return tuple(
                        (filename, packs_lease.read_text(filename))
                        for filename in _pinned_yaml_child_names(packs_lease)
                    )
            except FileNotFoundError:
                return ()
    except FileNotFoundError:
        return ()


def _pinned_yaml_child_names(directory: _DirectoryLease) -> tuple[str, ...]:
    """List direct YAML child names while ``directory`` remains pinned."""
    if directory.posix_fd is not None:
        names = os.listdir(directory.posix_fd)
    else:
        # The Windows directory handle omits FILE_SHARE_DELETE, so the
        # canonical path cannot be rebound while this enumeration/read scope
        # is alive.  Each selected child is still reopened as a non-reparse
        # regular file by ``read_text`` below.
        names = [child.name for child in directory.path.iterdir()]
    return tuple(
        sorted(
            name
            for name in names
            if isinstance(name, str)
            and name.endswith(".yaml")
            and Path(name).name == name
        )
    )


@contextmanager
def _pinned_swarm_directory(
    project_root: Path,
    *,
    create: bool,
    runtime: bool = False,
) -> Iterator[_SwarmLease]:
    """Pin root -> `.swarm` -> optional runtime without changing cwd."""
    root = _resolved_project_root(project_root)
    with _SWARM_DIRECTORY_PIN_LOCK:
        with _pinned_root_directory(root) as root_lease:
            with _pinned_child_directory(
                root_lease,
                ".swarm",
                create=create,
                label=".swarm directory",
            ) as swarm_lease:
                _ensure_posix_owner_only_directory(swarm_lease, repair=create)
                if not runtime:
                    yield _SwarmLease(swarm_lease)
                    return
                with _pinned_child_directory(
                    swarm_lease,
                    "runtime",
                    create=create,
                    label=".swarm/runtime",
                ) as runtime_lease:
                    _ensure_posix_owner_only_directory(
                        runtime_lease,
                        repair=create,
                    )
                    yield _SwarmLease(swarm_lease, runtime_lease)


@contextmanager
def _pinned_root_directory(project_root: Path) -> Iterator[_DirectoryLease]:
    if os.name == "nt":
        lease = _open_windows_directory(project_root, project_root, "project root")
    else:
        lease = _open_posix_root_directory(project_root)
    try:
        yield lease
    finally:
        lease.close()


@contextmanager
def _pinned_child_directory(
    parent: _DirectoryLease,
    name: str,
    *,
    create: bool,
    label: str,
) -> Iterator[_DirectoryLease]:
    _validate_relative_filename(name)
    if os.name == "nt":
        child_path = parent.path / name
        if create:
            try:
                child_path.mkdir()
            except FileExistsError:
                pass
        lease = _open_windows_directory(child_path, parent.project_root, label)
    else:
        lease = _open_posix_child_directory(parent, name, create=create, label=label)
    try:
        yield lease
    finally:
        lease.close()


def _open_posix_root_directory(project_root: Path) -> _DirectoryLease:
    flags = _posix_directory_flags()
    descriptor = os.open(project_root, flags)
    try:
        _validate_posix_directory_descriptor(descriptor, project_root, "project root")
        return _DirectoryLease(
            project_root=project_root,
            path=_posix_fd_reference(descriptor),
            posix_fd=descriptor,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _open_posix_child_directory(
    parent: _DirectoryLease,
    name: str,
    *,
    create: bool,
    label: str,
) -> _DirectoryLease:
    if parent.posix_fd is None:  # pragma: no cover - caller dispatches platform
        raise RuntimeError("POSIX directory lease is missing its descriptor")
    if create:
        try:
            os.mkdir(name, dir_fd=parent.posix_fd)
        except FileExistsError:
            pass
    descriptor = os.open(name, _posix_directory_flags(), dir_fd=parent.posix_fd)
    try:
        _validate_posix_directory_descriptor(descriptor, parent.project_root, label)
        return _DirectoryLease(
            project_root=parent.project_root,
            path=_posix_fd_reference(descriptor),
            posix_fd=descriptor,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _posix_directory_flags() -> int:
    directory = getattr(os, "O_DIRECTORY", 0)
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not directory or not no_follow:
        raise SwarmProjectPathError(
            "Secure Swarm directory pinning is unavailable on this platform"
        )
    return os.O_RDONLY | directory | no_follow


def _posix_fd_reference(descriptor: int) -> Path:
    reference = Path(f"/proc/self/fd/{descriptor}")
    try:
        os.readlink(reference)
    except OSError as exc:
        raise SwarmProjectPathError(
            "Secure Swarm descriptor paths are unavailable on this platform"
        ) from exc
    return reference


def _validate_posix_directory_descriptor(
    descriptor: int,
    project_root: Path,
    label: str,
) -> None:
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise SwarmProjectPathError(f"Swarm {label} must be a directory")
        actual = _posix_fd_reference(descriptor).resolve()
        actual.relative_to(project_root)
    except (OSError, RuntimeError, ValueError) as exc:
        if isinstance(exc, SwarmProjectPathError):
            raise
        raise SwarmProjectPathError(
            f"Swarm {label} resolves outside the trusted project root"
        ) from exc


def _ensure_posix_owner_only_directory(
    directory: _DirectoryLease,
    *,
    repair: bool,
) -> None:
    """Require the held Swarm directory to be an owner-only POSIX boundary."""
    if directory.posix_fd is None:
        return
    try:
        metadata = os.fstat(directory.posix_fd)
    except OSError as exc:
        raise SwarmProjectPathError("Unable to inspect pinned Swarm directory") from exc
    expected_uid = os.geteuid()
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_uid != expected_uid:
        raise SwarmProjectPathError(
            "Pinned Swarm directory must be owned by the active OS user"
        )
    if mode == 0o700:
        return
    if not repair:
        raise SwarmProjectPathError(
            "Pinned Swarm directory must be owner-only (mode 0700)"
        )
    try:
        os.fchmod(directory.posix_fd, 0o700)
    except OSError as exc:
        raise SwarmProjectPathError(
            "Unable to repair pinned Swarm directory permissions"
        ) from exc


def _ensure_posix_owner_only_regular_file(
    descriptor: int,
    filename: str,
    *,
    repair: bool,
) -> None:
    """Require a held runtime file to be regular, owned, and mode ``0600``."""
    if os.name == "nt":
        return
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise SwarmProjectPathError(
            f"Unable to inspect pinned Swarm runtime file: {filename}"
        ) from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SwarmProjectPathError(f"Swarm file must be regular: {filename}")
    expected_uid = os.geteuid()
    mode = stat.S_IMODE(metadata.st_mode)
    if metadata.st_uid != expected_uid:
        raise SwarmProjectPathError(
            f"Pinned Swarm runtime file must be owned by the active OS user: {filename}"
        )
    if mode == 0o600:
        return
    if not repair:
        raise SwarmProjectPathError(
            f"Pinned Swarm runtime file must be owner-only (mode 0600): {filename}"
        )
    try:
        os.fchmod(descriptor, 0o600)
    except OSError as exc:
        raise SwarmProjectPathError(
            f"Unable to repair pinned Swarm runtime file permissions: {filename}"
        ) from exc


def _posix_regular_file_identity(descriptor: int) -> tuple[int, int] | None:
    """Return a stable `(device, inode)` pair while the caller holds `descriptor`."""
    if os.name == "nt":
        return None
    try:
        metadata = os.fstat(descriptor)
    except OSError as exc:
        raise SwarmProjectPathError("Unable to inspect pinned Swarm database") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SwarmProjectPathError("Pinned Swarm database must be regular")
    return (metadata.st_dev, metadata.st_ino)


def _ensure_runtime_sqlite_files_owner_only(
    runtime: _DirectoryLease,
    *,
    repair: bool,
) -> None:
    """Validate/repair known SQLite artifacts without following direct links."""
    if runtime.posix_fd is None:
        return
    for filename in _SQLITE_RUNTIME_FILENAMES:
        try:
            with runtime.hold_regular_file(
                filename,
                read_only=not repair,
                create=False,
                owner_only=True,
                repair_owner_only=repair,
            ):
                pass
        except FileNotFoundError:
            continue


def _open_regular_file(
    directory: _DirectoryLease,
    filename: str,
    *,
    read_only: bool,
    create: bool,
) -> int:
    if directory.posix_fd is not None:
        return _open_posix_regular_file(
            directory,
            filename,
            read_only=read_only,
            create=create,
        )
    return _open_windows_regular_file(
        directory,
        filename,
        read_only=read_only,
        create=create,
    )


def _open_posix_regular_file(
    directory: _DirectoryLease,
    filename: str,
    *,
    read_only: bool,
    create: bool,
) -> int:
    if directory.posix_fd is None:  # pragma: no cover - caller dispatches platform
        raise RuntimeError("POSIX directory lease is missing its descriptor")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    if not no_follow:
        raise SwarmProjectPathError("Secure Swarm file pinning is unavailable")
    flags = (os.O_RDONLY if read_only else os.O_RDWR) | no_follow

    def open_direct(open_flags: int, mode: int | None = None) -> int:
        try:
            if mode is None:
                return os.open(filename, open_flags, dir_fd=directory.posix_fd)
            return os.open(
                filename,
                open_flags,
                mode,
                dir_fd=directory.posix_fd,
            )
        except OSError as exc:
            if exc.errno in {errno.ELOOP, errno.ENOTDIR}:
                raise SwarmProjectPathError(
                    f"Swarm file must be a direct non-link regular file: {filename}"
                ) from exc
            raise

    descriptor = -1
    if create:
        try:
            descriptor = open_direct(flags | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
    for _attempt in range(3):
        if descriptor < 0:
            descriptor = open_direct(flags)
        try:
            current = os.stat(
                filename,
                dir_fd=directory.posix_fd,
                follow_symlinks=False,
            )
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(current.st_mode):
                raise SwarmProjectPathError(f"Swarm file must be regular: {filename}")
            if (opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino):
                return descriptor
        except BaseException:
            os.close(descriptor)
            raise
        os.close(descriptor)
        descriptor = -1
    raise SwarmProjectPathError(f"Swarm file changed while being opened: {filename}")


def _replace_direct_child(
    directory: _DirectoryLease, source: str, destination: str
) -> None:
    if directory.posix_fd is not None:
        os.replace(
            source,
            destination,
            src_dir_fd=directory.posix_fd,
            dst_dir_fd=directory.posix_fd,
        )
        return
    os.replace(directory.path / source, directory.path / destination)


def _unlink_direct_child(directory: _DirectoryLease, filename: str) -> None:
    try:
        if directory.posix_fd is not None:
            os.unlink(filename, dir_fd=directory.posix_fd)
        else:
            (directory.path / filename).unlink(missing_ok=True)
    except OSError:
        pass


def _open_windows_directory(
    path: Path,
    project_root: Path,
    label: str,
) -> _DirectoryLease:
    handle, attributes = _open_windows_handle(
        path,
        desired_access=_WIN_GENERIC_READ,
        creation_disposition=_WIN_OPEN_EXISTING,
        directory=True,
    )
    try:
        if (
            attributes
            & (_WIN_FILE_ATTRIBUTE_REPARSE_POINT | _WIN_FILE_ATTRIBUTE_DIRECTORY)
            != _WIN_FILE_ATTRIBUTE_DIRECTORY
        ):
            raise SwarmProjectPathError(
                f"Swarm {label} must be a non-reparse directory"
            )
        actual = _windows_handle_path(handle)
        _ensure_contained(project_root, actual, label=label)
        return _DirectoryLease(
            project_root=project_root,
            path=actual,
            windows_handle=handle,
        )
    except BaseException:
        _close_windows_handle(handle)
        raise


def _open_windows_regular_file(
    directory: _DirectoryLease,
    filename: str,
    *,
    read_only: bool,
    create: bool,
) -> int:
    path = directory.path / filename
    desired_access = (
        _WIN_GENERIC_READ if read_only else _WIN_GENERIC_READ | _WIN_GENERIC_WRITE
    )
    try:
        handle, attributes = _open_windows_handle(
            path,
            desired_access=desired_access,
            creation_disposition=_WIN_CREATE_NEW if create else _WIN_OPEN_EXISTING,
            directory=False,
        )
    except FileExistsError:
        if not create:
            raise
        handle, attributes = _open_windows_handle(
            path,
            desired_access=desired_access,
            creation_disposition=_WIN_OPEN_EXISTING,
            directory=False,
        )
    if attributes & (_WIN_FILE_ATTRIBUTE_REPARSE_POINT | _WIN_FILE_ATTRIBUTE_DIRECTORY):
        _close_windows_handle(handle)
        raise SwarmProjectPathError(f"Swarm file must be regular: {filename}")
    try:
        import msvcrt

        # Ownership transfers to the CRT descriptor; its close preserves the
        # no-delete share that protects the final database/config name.
        return msvcrt.open_osfhandle(handle, os.O_RDONLY if read_only else os.O_RDWR)
    except BaseException:
        _close_windows_handle(handle)
        raise


def _open_windows_handle(
    path: Path,
    *,
    desired_access: int,
    creation_disposition: int,
    directory: bool,
) -> tuple[int, int]:
    """Open a native Windows handle that permits read/write but not delete."""
    if os.name != "nt":  # pragma: no cover - platform dispatch protects this
        raise RuntimeError("Windows handle helper called on a non-Windows platform")
    import ctypes
    from ctypes import wintypes

    flags = _WIN_FILE_FLAG_OPEN_REPARSE_POINT
    if directory:
        flags |= _WIN_FILE_FLAG_BACKUP_SEMANTICS
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    handle = create_file(
        str(path),
        desired_access,
        _WIN_FILE_SHARE_READ | _WIN_FILE_SHARE_WRITE,
        None,
        creation_disposition,
        flags,
        None,
    )
    invalid = wintypes.HANDLE(-1).value
    if handle == invalid:
        error = ctypes.get_last_error()
        if error in {_WIN_ERROR_FILE_EXISTS, _WIN_ERROR_ALREADY_EXISTS}:
            raise FileExistsError(error, "Windows file already exists", str(path))
        if error in {_WIN_ERROR_FILE_NOT_FOUND, _WIN_ERROR_PATH_NOT_FOUND}:
            raise FileNotFoundError(error, "Windows file was not found", str(path))
        raise OSError(error, "CreateFileW failed", str(path))
    try:
        attributes = _windows_handle_attributes(handle)
        return int(handle), attributes
    except BaseException:
        _close_windows_handle(int(handle))
        raise


def _windows_handle_attributes(handle: int) -> int:
    import ctypes
    from ctypes import wintypes

    class _ByHandleFileInformation(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime_dwLowDateTime", wintypes.DWORD),
            ("ftCreationTime_dwHighDateTime", wintypes.DWORD),
            ("ftLastAccessTime_dwLowDateTime", wintypes.DWORD),
            ("ftLastAccessTime_dwHighDateTime", wintypes.DWORD),
            ("ftLastWriteTime_dwLowDateTime", wintypes.DWORD),
            ("ftLastWriteTime_dwHighDateTime", wintypes.DWORD),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_information = kernel32.GetFileInformationByHandle
    get_information.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_ByHandleFileInformation),
    ]
    get_information.restype = wintypes.BOOL
    information = _ByHandleFileInformation()
    if not get_information(handle, information):
        raise OSError(ctypes.get_last_error(), "GetFileInformationByHandle failed")
    return int(information.dwFileAttributes)


def _windows_handle_path(handle: int) -> Path:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    get_final_path = kernel32.GetFinalPathNameByHandleW
    get_final_path.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    get_final_path.restype = wintypes.DWORD
    required = int(get_final_path(handle, None, 0, 0))
    if required <= 0:
        raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
    buffer = ctypes.create_unicode_buffer(required + 1)
    result = int(get_final_path(handle, buffer, len(buffer), 0))
    if result <= 0 or result >= len(buffer):
        raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed")
    value = buffer.value
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return Path(value).resolve()


def _close_windows_handle(handle: int) -> None:
    if not handle:
        return
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    close_handle(handle)


def _write_project_config(lease: _DirectoryLease, raw_config: dict[str, Any]) -> None:
    lease.write_text("swarm.yaml", yaml.safe_dump(raw_config, sort_keys=False))


def _ensure_runtime_is_ignored(lease: _DirectoryLease) -> None:
    entry = "runtime/"
    try:
        lines = lease.read_text(".gitignore").splitlines()
    except FileNotFoundError:
        lease.write_text(".gitignore", f"{entry}\n")
        return
    if entry not in lines:
        suffix = "" if not lines else "\n"
        lease.write_text(".gitignore", "\n".join([*lines, entry]) + suffix)


def _resolved_project_root(project_root: Path) -> Path:
    try:
        return Path(project_root).expanduser().resolve()
    except (OSError, RuntimeError) as exc:
        raise SwarmProjectPathError(
            f"Unable to resolve trusted Swarm project root: {project_root}"
        ) from exc


def _ensure_project_root(project_root: Path) -> None:
    """Create the explicit project root for the mutating initialization path.

    This is intentionally scoped to the caller-provided root, never to a
    derived Swarm child.  A subsequent native root lease validates that the
    resulting object is a directory before any versioned or runtime state is
    opened.  Read-only status paths do not invoke this function.
    """
    try:
        project_root.mkdir(parents=True, exist_ok=True)
    except (OSError, RuntimeError) as exc:
        raise SwarmProjectPathError(
            f"Unable to create trusted Swarm project root: {project_root}"
        ) from exc
    try:
        if not project_root.is_dir():
            raise SwarmProjectPathError(
                f"Trusted Swarm project root is not a directory: {project_root}"
            )
    except (OSError, RuntimeError) as exc:
        raise SwarmProjectPathError(
            f"Unable to inspect trusted Swarm project root: {project_root}"
        ) from exc


def _contained_path(project_root: Path, path: Path, *, label: str) -> Path:
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise SwarmProjectPathError(f"Unable to resolve Swarm {label}: {path}") from exc
    _ensure_contained(project_root, resolved, label=label)
    return resolved


def _ensure_contained(project_root: Path, path: Path, *, label: str) -> None:
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise SwarmProjectPathError(
            f"Swarm {label} resolves outside the trusted project root: {path}"
        ) from exc


def _validate_relative_filename(name: str) -> None:
    path = Path(name)
    if path.is_absolute() or len(path.parts) != 1 or path.name in {"", ".", ".."}:
        raise SwarmProjectPathError(f"Unsafe relative Swarm filename: {name!r}")


def _to_config(
    project_root: Path, config_path: Path, raw_config: dict[str, Any]
) -> SwarmConfig:
    try:
        default_autonomy = str(
            raw_config.get("default_autonomy", _DEFAULT_CONFIG["default_autonomy"])
        )
        if default_autonomy not in _AUTONOMY_LEVELS:
            raise ValueError(f"Unsupported Swarm autonomy level: {default_autonomy}")
        return SwarmConfig(
            project_root=project_root,
            config_path=config_path,
            version=int(raw_config["version"]),
            default_provider=str(raw_config["default_provider"]),
            default_model=str(raw_config["default_model"]),
            default_autonomy=default_autonomy,
        )
    except KeyError as exc:
        raise ValueError(f"Missing Swarm configuration value: {exc.args[0]}") from exc


# Native Windows constants are harmless on POSIX and keep the platform branch
# compact.  Directory/file handles intentionally omit FILE_SHARE_DELETE.
_WIN_GENERIC_READ = 0x80000000
_WIN_GENERIC_WRITE = 0x40000000
_WIN_FILE_SHARE_READ = 0x00000001
_WIN_FILE_SHARE_WRITE = 0x00000002
_WIN_CREATE_NEW = 1
_WIN_OPEN_EXISTING = 3
_WIN_FILE_ATTRIBUTE_DIRECTORY = 0x00000010
_WIN_FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_ERROR_FILE_EXISTS = 80
_WIN_ERROR_ALREADY_EXISTS = 183
_WIN_ERROR_FILE_NOT_FOUND = 2
_WIN_ERROR_PATH_NOT_FOUND = 3
