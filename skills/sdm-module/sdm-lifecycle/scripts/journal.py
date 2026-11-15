#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# ///
"""Process-safe CAS locks and recoverable per-file workspace transactions."""

from __future__ import annotations

import argparse
import errno
import fcntl
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from canonical import (
    MAX_DOCUMENT_BYTES, canonical_bytes, digest_bytes, parse_document,
)


_OPERATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_JOURNAL_KEYS = {
    "schema", "operation_id", "workspace_identity", "phase", "writes",
    "created_parents",
}
_WRITE_KEYS = {"target", "stage", "before_digest", "after_digest", "mode", "exclusive"}
_IDENTITY_KEYS = {"device", "inode", "mount_id"}
_DIR_FLAGS = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_DIRECTORY", 0)
)
_FILE_FLAGS = (
    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
)
MAX_READ_TREE_NODES = 100_000
MAX_READ_TREE_BYTES = 134_217_728


class JournalError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise JournalError("unsafe_path", f"path is not a contained portable relative path: {value}")
    return path


def _mount_identity(descriptor: int) -> str:
    try:
        info_fd = os.open(
            f"/proc/self/fdinfo/{descriptor}",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            data = bytearray()
            while len(data) <= 65_536:
                chunk = os.read(info_fd, min(4096, 65_537 - len(data)))
                if not chunk:
                    break
                data.extend(chunk)
        finally:
            os.close(info_fd)
        if len(data) > 65_536:
            raise OSError("fdinfo is oversized")
        for line in bytes(data).splitlines():
            if line.startswith(b"mnt_id:"):
                value = line.split(b":", 1)[1].strip().decode("ascii")
                if value.isdigit():
                    return f"mnt_id:{value}"
    except (OSError, UnicodeError):
        pass
    raise JournalError(
        "mount_identity_unavailable",
        "trustworthy workspace mount identity is unavailable",
    )


def _workspace_identity(descriptor: int) -> dict[str, Any]:
    info = os.fstat(descriptor)
    if not stat.S_ISDIR(info.st_mode):
        raise JournalError("unsafe_workspace", "workspace root is not a directory")
    return {
        "device": info.st_dev,
        "inode": info.st_ino,
        "mount_id": _mount_identity(descriptor),
    }


def _open_absolute_directory(path: Path) -> tuple[Path, int]:
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor = os.open(os.sep, _DIR_FLAGS)
    try:
        for component in absolute.parts[1:]:
            try:
                named = os.stat(component, dir_fd=descriptor,
                                follow_symlinks=False)
                child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            except OSError as exc:
                raise JournalError(
                    "unsafe_workspace",
                    f"workspace component is unavailable or unsafe: {absolute}",
                ) from exc
            opened = os.fstat(child)
            if (
                not stat.S_ISDIR(named.st_mode)
                or stat.S_ISLNK(named.st_mode)
                or (named.st_dev, named.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                os.close(child)
                raise JournalError(
                    "unsafe_workspace",
                    f"workspace component changed while opening: {absolute}",
                )
            os.close(descriptor)
            descriptor = child
        return absolute, descriptor
    except Exception:
        os.close(descriptor)
        raise


def _ensure_root(path: Path) -> Path:
    absolute, descriptor = _open_absolute_directory(path)
    try:
        _workspace_identity(descriptor)
        return absolute
    finally:
        os.close(descriptor)


def contained(root: Path, relative: str, *, allow_missing_leaf: bool = True) -> Path:
    """Return a compatibility pathname after a best-effort safety check.

    This legacy helper is not an I/O authority: a pathname cannot retain the
    directory identities checked here.  Security-sensitive readers must use
    :class:`ReadCapability` and security-sensitive writers must use the
    descriptor-relative transaction helpers below.
    """

    root = _ensure_root(root)
    rel = safe_relative(relative)
    current = root
    for index, part in enumerate(rel.parts):
        candidate = current / part
        leaf = index == len(rel.parts) - 1
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if leaf and allow_missing_leaf:
                return candidate
            raise JournalError("missing_parent", f"transaction parent does not exist: {candidate}")
        if stat.S_ISLNK(info.st_mode):
            raise JournalError("symlink_escape", f"symlink is forbidden in governed paths: {candidate}")
        if not leaf and not stat.S_ISDIR(info.st_mode):
            raise JournalError("unsafe_path", f"transaction parent is not a directory: {candidate}")
        if info.st_dev != root.stat().st_dev:
            raise JournalError("mount_escape", f"cross-device path is forbidden: {candidate}")
        current = candidate
    return current


def _open_relative_directory_at(
    root_fd: int, relative: str, *, create: bool = False, mode: int = 0o700,
) -> int:
    identity = _workspace_identity(root_fd)
    descriptor = os.dup(root_fd)
    traversed: list[str] = []
    try:
        for component in safe_relative(relative).parts:
            traversed.append(component)
            subject = "/".join(traversed)
            try:
                before = os.stat(component, dir_fd=descriptor,
                                 follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(component, mode, dir_fd=descriptor)
                os.fsync(descriptor)
                before = os.stat(component, dir_fd=descriptor,
                                 follow_symlinks=False)
            try:
                child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
            except OSError as exc:
                raise JournalError(
                    "unsafe_path", f"transaction directory is unsafe: {subject}",
                ) from exc
            opened = os.fstat(child)
            if (
                not stat.S_ISDIR(before.st_mode)
                or stat.S_ISLNK(before.st_mode)
                or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
                or opened.st_dev != identity["device"]
                or _mount_identity(child) != identity["mount_id"]
            ):
                os.close(child)
                raise JournalError(
                    "mount_escape",
                    f"transaction directory changed identity or mount: {subject}",
                )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _ensure_directory_at(
    root_fd: int, relative: str, mode: int = 0o700,
) -> None:
    descriptor = _open_relative_directory_at(
        root_fd, relative, create=True, mode=mode,
    )
    os.close(descriptor)


def ensure_directory(root: Path, relative: str, mode: int = 0o700) -> Path:
    root, root_fd = _open_absolute_directory(root)
    try:
        _ensure_directory_at(root_fd, relative, mode)
    finally:
        os.close(root_fd)
    return root.joinpath(*safe_relative(relative).parts)


class WorkspaceLock:
    def __init__(self, root: str | os.PathLike[str], *, exclusive: bool, create: bool = False) -> None:
        self.root = _ensure_root(Path(root))
        self.exclusive = exclusive
        self.create = create
        self.fd: int | None = None
        self.root_fd: int | None = None

    def __enter__(self) -> "WorkspaceLock":
        flags = os.O_RDWR if self.exclusive else os.O_RDONLY
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        if self.create:
            flags |= os.O_CREAT
        try:
            _, self.root_fd = _open_absolute_directory(self.root)
            _workspace_identity(self.root_fd)
            self.fd = os.open("_lock", flags, 0o600, dir_fd=self.root_fd)
            lock_info = os.fstat(self.fd)
            if not stat.S_ISREG(lock_info.st_mode) or lock_info.st_nlink != 1:
                raise JournalError(
                    "lock_failed", "workspace lock is not a single-link regular file",
                )
            mode = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
            fcntl.flock(self.fd, mode | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if self.root_fd is not None:
                os.close(self.root_fd)
                self.root_fd = None
            raise JournalError("lock_conflict", "workspace lock is held by another process") from exc
        except (OSError, JournalError) as exc:
            if self.fd is not None:
                os.close(self.fd)
                self.fd = None
            if self.root_fd is not None:
                os.close(self.root_fd)
                self.root_fd = None
            if isinstance(exc, JournalError):
                raise
            raise JournalError("lock_failed", f"cannot acquire workspace lock: {exc}") from exc
        return self

    def __exit__(self, *_: object) -> None:
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None
        if self.root_fd is not None:
            os.close(self.root_fd)
            self.root_fd = None

    def duplicate_root(
        self, root: Path, *, require_exclusive: bool = True,
    ) -> int:
        """Return the retained, locked root after authenticating its live name."""

        if self.fd is None or self.root_fd is None:
            raise JournalError("lock_required", "workspace lock is not active")
        if require_exclusive and not self.exclusive:
            raise JournalError(
                "lock_required", "workspace mutation requires an exclusive lock",
            )
        requested = Path(os.path.abspath(os.fspath(root)))
        if requested != self.root:
            raise JournalError(
                "lock_root_mismatch", "workspace lock belongs to another root",
            )
        lock_named = os.stat("_lock", dir_fd=self.root_fd, follow_symlinks=False)
        lock_opened = os.fstat(self.fd)
        if (
            not stat.S_ISREG(lock_named.st_mode)
            or lock_named.st_nlink != 1
            or (lock_named.st_dev, lock_named.st_ino)
            != (lock_opened.st_dev, lock_opened.st_ino)
        ):
            raise JournalError(
                "lock_rebound", "workspace lock name changed after acquisition",
            )
        try:
            _, named_fd = _open_absolute_directory(self.root)
        except JournalError as exc:
            raise JournalError(
                "workspace_rebound",
                "workspace root name changed after lock acquisition",
            ) from exc
        try:
            if _workspace_identity(named_fd) != _workspace_identity(self.root_fd):
                raise JournalError(
                    "workspace_rebound",
                    "workspace root name no longer identifies the locked root",
                )
        finally:
            os.close(named_fd)
        return os.dup(self.root_fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _stable_file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        stat.S_IFMT(info.st_mode), info.st_dev, info.st_ino, info.st_nlink,
        info.st_size, info.st_mtime_ns, info.st_ctime_ns,
    )


def _stable_read_identity(info: os.stat_result) -> tuple[int, ...]:
    """Return every mutation-sensitive stat field required by governed reads."""

    return (
        info.st_mode, info.st_dev, info.st_ino, info.st_nlink, info.st_size,
        info.st_mtime_ns, info.st_ctime_ns,
    )


@dataclass(frozen=True)
class ReadEntry:
    """One already-accounted regular file in a retained directory scan."""

    name: str
    relative: str
    identity: tuple[int, ...]


@dataclass
class _ReadRecord:
    kind: str
    identity: tuple[int, ...]
    mount_id: str
    digest: str | None = None


class ReadCapability:
    """Retained, bounded, descriptor-relative authority for governed reads.

    The capability pins the workspace root and its Linux mount identity, never
    follows a path component, accounts nodes/bytes before collection, and
    revalidates every observed name and byte digest before it closes.  A caller
    supplying ``root_fd`` retains ownership of that descriptor; this class
    authenticates and duplicates it.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        *,
        root_fd: int | None = None,
        max_nodes: int = MAX_READ_TREE_NODES,
        max_bytes: int = MAX_READ_TREE_BYTES,
    ) -> None:
        if (
            isinstance(max_nodes, bool) or not isinstance(max_nodes, int)
            or max_nodes < 1
            or isinstance(max_bytes, bool) or not isinstance(max_bytes, int)
            or max_bytes < 0
        ):
            raise JournalError(
                "document_resource_limit", "governed read budgets are invalid",
            )
        self.root_path = Path(os.path.abspath(os.fspath(root)))
        self.max_nodes = max_nodes
        self.max_bytes = max_bytes
        self._records: dict[str, _ReadRecord] = {}
        self._scans: dict[str, tuple[str, ...]] = {}
        self._node_count = 1
        self._total_bytes = 0
        self._closed = False
        descriptor = -1
        try:
            if root_fd is None:
                opened_path, descriptor = _open_absolute_directory(
                    self.root_path,
                )
                self.root_path = opened_path
            else:
                descriptor = os.dup(root_fd)
            root_info = os.fstat(descriptor)
            if not stat.S_ISDIR(root_info.st_mode):
                raise JournalError(
                    "unsafe_workspace", "governed read root is not a directory",
                )
            self.root_fd = descriptor
            descriptor = -1
            self.root_device = root_info.st_dev
            self.root_mount = _mount_identity(self.root_fd)
            self.root_identity = _stable_read_identity(root_info)
            self._verify_live_root_name()
        except BaseException:
            if descriptor >= 0:
                os.close(descriptor)
            if hasattr(self, "root_fd"):
                os.close(self.root_fd)
            self._closed = True
            raise

    def __enter__(self) -> "ReadCapability":
        if self._closed:
            raise JournalError(
                "unsafe_workspace", "governed read capability is closed",
            )
        return self

    def __exit__(self, _type: Any, _value: Any, _traceback: Any) -> None:
        try:
            self.verify()
        finally:
            self.close()

    def close(self) -> None:
        if not self._closed:
            os.close(self.root_fd)
            self._closed = True

    def duplicate_root(self) -> int:
        self._require_open()
        return os.dup(self.root_fd)

    def assert_root(self, root: str | os.PathLike[str]) -> None:
        self._require_open()
        selected = Path(os.path.abspath(os.fspath(root)))
        if selected != self.root_path:
            raise JournalError(
                "unsafe_workspace",
                "governed read capability belongs to another root",
            )

    def _require_open(self) -> None:
        if self._closed:
            raise JournalError(
                "unsafe_workspace", "governed read capability is closed",
            )

    def _verify_live_root_name(self) -> None:
        try:
            _, named_fd = _open_absolute_directory(self.root_path)
        except JournalError as exc:
            raise JournalError(
                "concurrent_mutation", "governed read root name changed",
            ) from exc
        try:
            named = os.fstat(named_fd)
            if (
                _stable_read_identity(named) != self.root_identity
                or _mount_identity(named_fd) != self.root_mount
            ):
                raise JournalError(
                    "concurrent_mutation",
                    "governed read root name changed identity or mount",
                )
        finally:
            os.close(named_fd)

    def _record(
        self,
        relative: str,
        *,
        kind: str,
        info: os.stat_result,
        mount_id: str,
        account: bool,
    ) -> _ReadRecord:
        identity = _stable_read_identity(info)
        prior = self._records.get(relative)
        if prior is not None:
            if (
                prior.kind != kind or prior.identity != identity
                or prior.mount_id != mount_id
            ):
                raise JournalError(
                    "concurrent_mutation",
                    f"governed path changed identity: {relative}",
                )
            return prior
        if not account:
            raise JournalError(
                "concurrent_mutation",
                f"governed path was not present in the retained capture: {relative}",
            )
        if self._node_count >= self.max_nodes:
            raise JournalError(
                "document_resource_limit",
                "governed read exceeds the fixed node limit",
            )
        self._node_count += 1
        if kind == "file":
            if info.st_size > MAX_DOCUMENT_BYTES:
                raise JournalError(
                    "document_resource_limit",
                    f"governed document exceeds the fixed byte limit: {relative}",
                )
            if self._total_bytes > self.max_bytes - info.st_size:
                raise JournalError(
                    "document_resource_limit",
                    "governed read exceeds the fixed aggregate byte limit",
                )
            self._total_bytes += info.st_size
        record = _ReadRecord(kind, identity, mount_id)
        self._records[relative] = record
        return record

    def _open_directory(
        self, relative: str, *, account: bool,
    ) -> int:
        self._require_open()
        if relative in {"", "."}:
            return os.dup(self.root_fd)
        parts = safe_relative(relative).parts
        descriptor = os.dup(self.root_fd)
        traversed: list[str] = []
        try:
            for component in parts:
                traversed.append(component)
                subject = PurePosixPath(*traversed).as_posix()
                try:
                    named = os.stat(
                        component, dir_fd=descriptor,
                        follow_symlinks=False,
                    )
                    if stat.S_ISLNK(named.st_mode) or not stat.S_ISDIR(
                        named.st_mode
                    ):
                        raise JournalError(
                            "unsafe_path",
                            f"governed directory is unsafe: {subject}",
                        )
                    child = os.open(component, _DIR_FLAGS, dir_fd=descriptor)
                except JournalError:
                    raise
                except OSError as exc:
                    raise JournalError(
                        "concurrent_mutation",
                        f"governed directory is unavailable or unsafe: {subject}",
                    ) from exc
                opened = os.fstat(child)
                try:
                    opened_mount = _mount_identity(child)
                    if (
                        _stable_read_identity(named)
                        != _stable_read_identity(opened)
                        or opened.st_dev != self.root_device
                        or opened_mount != self.root_mount
                    ):
                        raise JournalError(
                            "mount_escape",
                            f"governed directory changed identity or mount: {subject}",
                        )
                    self._record(
                        subject, kind="directory", info=opened,
                        mount_id=opened_mount, account=account,
                    )
                except BaseException:
                    os.close(child)
                    raise
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def open_directory(self, relative: str) -> int:
        return self._open_directory(relative, account=True)

    def _parent_and_name(
        self, relative: str, *, account: bool,
    ) -> tuple[int, str]:
        parts = safe_relative(relative).parts
        if len(parts) == 1:
            return os.dup(self.root_fd), parts[0]
        return (
            self._open_directory(
                PurePosixPath(*parts[:-1]).as_posix(), account=account,
            ),
            parts[-1],
        )

    def _read_named(
        self,
        parent_fd: int,
        name: str,
        *,
        relative: str,
        limit: int,
        expected_identity: tuple[int, ...] | None,
        account: bool,
    ) -> bytes:
        if (
            "/" in name or name in {"", ".", ".."}
            or isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise JournalError("unsafe_path", f"unsafe governed file: {relative}")
        try:
            named_before = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False,
            )
        except OSError as exc:
            raise JournalError(
                "concurrent_mutation",
                f"governed file is unavailable: {relative}",
            ) from exc
        if (
            stat.S_ISLNK(named_before.st_mode)
            or not stat.S_ISREG(named_before.st_mode)
            or named_before.st_nlink != 1
        ):
            raise JournalError(
                "unsafe_path",
                f"governed file is not a regular single-link file: {relative}",
            )
        if named_before.st_size > limit:
            raise JournalError(
                "document_resource_limit",
                f"governed document exceeds the fixed byte limit: {relative}",
            )
        if (
            expected_identity is not None
            and _stable_read_identity(named_before) != expected_identity
        ):
            raise JournalError(
                "concurrent_mutation",
                f"governed file changed after inventory: {relative}",
            )
        try:
            descriptor = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
        except OSError as exc:
            raise JournalError(
                "concurrent_mutation",
                f"governed file changed while opening: {relative}",
            ) from exc
        try:
            opened = os.fstat(descriptor)
            opened_mount = _mount_identity(descriptor)
            identity = _stable_read_identity(opened)
            if (
                not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                or opened.st_size > limit
                or opened.st_dev != self.root_device
                or opened_mount != self.root_mount
                or identity != _stable_read_identity(named_before)
                or (expected_identity is not None and identity != expected_identity)
            ):
                raise JournalError(
                    "mount_escape",
                    f"governed file changed identity or mount: {relative}",
                )
            record = self._record(
                relative, kind="file", info=opened,
                mount_id=opened_mount, account=account,
            )
            data = _read_bytes_fd(descriptor, subject=relative, limit=limit)
            named_after = os.stat(
                name, dir_fd=parent_fd, follow_symlinks=False,
            )
            if (
                _stable_read_identity(named_after) != identity
                or _stable_read_identity(os.fstat(descriptor)) != identity
                or _mount_identity(descriptor) != self.root_mount
            ):
                raise JournalError(
                    "concurrent_mutation",
                    f"governed file changed while reading: {relative}",
                )
            captured_digest = digest_bytes(data)
            if record.digest is not None and record.digest != captured_digest:
                raise JournalError(
                    "concurrent_mutation",
                    f"governed file bytes changed: {relative}",
                )
            record.digest = captured_digest
            return data
        finally:
            os.close(descriptor)

    def read_bytes(
        self, relative: str, *, limit: int = MAX_DOCUMENT_BYTES,
    ) -> bytes:
        parent_fd, name = self._parent_and_name(relative, account=True)
        try:
            return self._read_named(
                parent_fd, name, relative=safe_relative(relative).as_posix(),
                limit=limit, expected_identity=None, account=True,
            )
        finally:
            os.close(parent_fd)

    def read_bytes_with_digest(
        self, relative: str, *, limit: int = MAX_DOCUMENT_BYTES,
    ) -> tuple[bytes, str]:
        data = self.read_bytes(relative, limit=limit)
        return data, digest_bytes(data)

    def scan_regular_files(
        self,
        directory_fd: int,
        relative: str,
        *,
        pattern: re.Pattern[str] | None = None,
    ) -> list[ReadEntry]:
        self._require_open()
        directory_relative = safe_relative(relative).as_posix()
        before = os.fstat(directory_fd)
        directory_record = self._records.get(directory_relative)
        if (
            directory_record is None
            or directory_record.kind != "directory"
            or directory_record.identity != _stable_read_identity(before)
            or before.st_dev != self.root_device
            or _mount_identity(directory_fd) != self.root_mount
        ):
            raise JournalError(
                "concurrent_mutation",
                f"governed directory is not retained: {directory_relative}",
            )
        entries: list[ReadEntry] = []
        try:
            with os.scandir(directory_fd) as scanner:
                for entry in scanner:
                    child_relative = f"{directory_relative}/{entry.name}"
                    info = entry.stat(follow_symlinks=False)
                    if (
                        stat.S_ISLNK(info.st_mode)
                        or not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                    ):
                        raise JournalError(
                            "unsafe_path",
                            f"governed inventory contains an unsafe node: {child_relative}",
                        )
                    if pattern is not None and pattern.fullmatch(entry.name) is None:
                        raise JournalError(
                            "unsafe_path",
                            f"governed inventory contains an unexpected file: {child_relative}",
                        )
                    record = self._record(
                        child_relative, kind="file", info=info,
                        mount_id=self.root_mount, account=True,
                    )
                    entries.append(ReadEntry(
                        entry.name, child_relative, record.identity,
                    ))
        except OSError as exc:
            raise JournalError(
                "concurrent_mutation",
                f"governed directory changed while scanning: {directory_relative}",
            ) from exc
        after = os.fstat(directory_fd)
        if (
            _stable_read_identity(before) != _stable_read_identity(after)
            or _mount_identity(directory_fd) != self.root_mount
        ):
            raise JournalError(
                "concurrent_mutation",
                f"governed directory changed while scanning: {directory_relative}",
            )
        names = tuple(sorted(item.name for item in entries))
        prior_scan = self._scans.get(directory_relative)
        if prior_scan is not None and prior_scan != names:
            raise JournalError(
                "concurrent_mutation",
                f"governed directory inventory changed: {directory_relative}",
            )
        self._scans[directory_relative] = names
        entries.sort(key=lambda item: item.name)
        return entries

    def read_entry(
        self,
        directory_fd: int,
        entry: ReadEntry,
        *,
        limit: int = MAX_DOCUMENT_BYTES,
    ) -> bytes:
        return self._read_named(
            directory_fd, entry.name, relative=entry.relative, limit=limit,
            expected_identity=entry.identity, account=True,
        )

    def _verify_scan(self, relative: str, expected: tuple[str, ...]) -> None:
        descriptor = self._open_directory(relative, account=False)
        try:
            before = os.fstat(descriptor)
            expected_set = set(expected)
            observed: list[str] = []
            with os.scandir(descriptor) as scanner:
                for entry in scanner:
                    if entry.name not in expected_set or len(observed) >= len(expected):
                        raise JournalError(
                            "concurrent_mutation",
                            f"governed directory inventory changed: {relative}",
                        )
                    child_relative = f"{relative}/{entry.name}"
                    record = self._records.get(child_relative)
                    current = entry.stat(follow_symlinks=False)
                    if (
                        record is None or record.kind != "file"
                        or record.identity != _stable_read_identity(current)
                    ):
                        raise JournalError(
                            "concurrent_mutation",
                            f"governed directory entry changed: {child_relative}",
                        )
                    observed.append(entry.name)
            after = os.fstat(descriptor)
            if (
                tuple(sorted(observed)) != expected
                or _stable_read_identity(before) != _stable_read_identity(after)
                or _mount_identity(descriptor) != self.root_mount
            ):
                raise JournalError(
                    "concurrent_mutation",
                    f"governed directory inventory changed: {relative}",
                )
        finally:
            os.close(descriptor)

    def _verify_record(self, relative: str, record: _ReadRecord) -> None:
        if record.kind == "directory":
            descriptor = self._open_directory(relative, account=False)
            os.close(descriptor)
            return
        parent_fd, name = self._parent_and_name(relative, account=False)
        try:
            data = self._read_named(
                parent_fd, name, relative=relative,
                limit=MAX_DOCUMENT_BYTES,
                expected_identity=record.identity, account=False,
            )
            if record.digest is None or digest_bytes(data) != record.digest:
                raise JournalError(
                    "concurrent_mutation",
                    f"governed file bytes changed: {relative}",
                )
        finally:
            os.close(parent_fd)

    def verify(self) -> None:
        self._require_open()
        self._verify_live_root_name()
        for relative, record in sorted(
            self._records.items(), key=lambda item: (item[0].count("/"), item[0]),
        ):
            self._verify_record(relative, record)
        for relative, expected in sorted(self._scans.items()):
            self._verify_scan(relative, expected)
        self._verify_live_root_name()
        current = os.fstat(self.root_fd)
        if (
            _stable_read_identity(current) != self.root_identity
            or _mount_identity(self.root_fd) != self.root_mount
        ):
            raise JournalError(
                "concurrent_mutation", "governed read root changed during capture",
            )


def _read_bytes_fd(fd: int, *, subject: str, limit: int) -> bytes:
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
        or before.st_size > limit
    ):
        raise JournalError(
            "document_resource_limit",
            f"regular governed document is unsafe or oversized: {subject}",
        )
    data = bytearray()
    while len(data) < before.st_size:
        part = os.read(fd, min(1024 * 1024, before.st_size - len(data)))
        if not part:
            break
        data.extend(part)
    after = os.fstat(fd)
    if len(data) != before.st_size or _stable_file_identity(before) != _stable_file_identity(after):
        raise JournalError(
            "concurrent_mutation", f"governed document changed while reading: {subject}",
        )
    return bytes(data)


def _read_bytes_at(
    parent_fd: int, name: str, *, subject: str, limit: int = MAX_DOCUMENT_BYTES,
) -> bytes:
    fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    try:
        return _read_bytes_fd(fd, subject=subject, limit=limit)
    finally:
        os.close(fd)


def _open_recovery_journal_at(
    root_fd: int,
) -> tuple[int, bytes, tuple[int, ...]]:
    """Open and authenticate the recovery journal without releasing its inode."""

    try:
        journal_fd = os.open("_journal.json", _FILE_FLAGS, dir_fd=root_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise JournalError(
            "journal_corrupt", "workspace recovery journal cannot be opened safely",
        ) from exc
    try:
        try:
            data = _read_bytes_fd(
                journal_fd, subject="_journal.json", limit=MAX_DOCUMENT_BYTES,
            )
            opened = os.fstat(journal_fd)
            named = os.stat(
                "_journal.json", dir_fd=root_fd, follow_symlinks=False,
            )
            expected = _stable_file_identity(opened)
            if (
                _mount_identity(journal_fd) != _mount_identity(root_fd)
                or _stable_file_identity(named) != expected
            ):
                raise JournalError(
                    "journal_corrupt",
                    "workspace recovery journal changed identity while opening",
                )
            return journal_fd, data, expected
        except FileNotFoundError as exc:
            raise JournalError(
                "journal_corrupt",
                "workspace recovery journal disappeared while opening",
            ) from exc
        except JournalError as exc:
            if exc.code == "journal_corrupt":
                raise
            raise JournalError(
                "journal_corrupt", "workspace recovery journal is unsafe or unstable",
            ) from exc
        except OSError as exc:
            raise JournalError(
                "journal_corrupt", "workspace recovery journal is unsafe or unstable",
            ) from exc
    except BaseException:
        os.close(journal_fd)
        raise


def _read_bytes(path: Path) -> bytes:
    parent, parent_fd = _open_absolute_directory(path.parent)
    try:
        return _read_bytes_at(
            parent_fd, path.name, subject=str(parent / path.name),
        )
    finally:
        os.close(parent_fd)


def _file_digest_at(parent_fd: int, name: str, *, subject: str) -> str | None:
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=parent_fd)
    except FileNotFoundError:
        return None
    try:
        before = os.fstat(fd)
        if (
            not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
            or before.st_size > MAX_DOCUMENT_BYTES
        ):
            raise JournalError(
                "document_resource_limit",
                f"not a bounded regular single-link file: {subject}",
            )
        hasher = __import__("hashlib").sha256()
        captured = 0
        while captured < before.st_size:
            part = os.read(fd, min(1024 * 1024, before.st_size - captured))
            if not part:
                break
            hasher.update(part)
            captured += len(part)
        after = os.fstat(fd)
        if (
            captured != before.st_size
            or _stable_file_identity(before) != _stable_file_identity(after)
        ):
            raise JournalError("concurrent_mutation", f"file changed while hashing: {subject}")
        return "sha256:" + hasher.hexdigest()
    finally:
        os.close(fd)


def file_digest(path: Path) -> str | None:
    try:
        parent, parent_fd = _open_absolute_directory(path.parent)
    except JournalError as exc:
        if not path.parent.exists():
            return None
        raise exc
    try:
        return _file_digest_at(
            parent_fd, path.name, subject=str(parent / path.name),
        )
    finally:
        os.close(parent_fd)


def _atomic_write_at(
    parent_fd: int, name: str, data: bytes, mode: int = 0o600,
    *, exclusive: bool = False,
) -> None:
    if "/" in name or name in {"", ".", ".."}:
        raise JournalError("unsafe_path", f"atomic target is not one file name: {name}")
    if len(data) > MAX_DOCUMENT_BYTES:
        raise JournalError(
            "document_resource_limit", "atomic document exceeds the fixed byte limit",
        )
    temp = f".{name}.tmp-{os.getpid()}-{os.urandom(6).hex()}"
    flags = (
        os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    )
    fd: int | None = os.open(temp, flags, mode, dir_fd=parent_fd)
    source_identity = os.fstat(fd)
    installed = False
    temp_removed = False
    try:
        os.fchmod(fd, mode)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise JournalError("write_failed", "atomic write made no progress")
            view = view[written:]
        os.fsync(fd)
        written_identity = os.fstat(fd)
        named_identity = os.stat(temp, dir_fd=parent_fd, follow_symlinks=False)
        if (
            (source_identity.st_dev, source_identity.st_ino)
            != (written_identity.st_dev, written_identity.st_ino)
            or (written_identity.st_dev, written_identity.st_ino)
            != (named_identity.st_dev, named_identity.st_ino)
        ):
            raise JournalError(
                "concurrent_mutation", "atomic temporary name changed before publish",
            )
        if exclusive:
            try:
                os.link(
                    f"/proc/self/fd/{fd}", name, dst_dir_fd=parent_fd,
                    follow_symlinks=True,
                )
            except FileExistsError as exc:
                raise JournalError(
                    "target_exists", f"append-only target already exists: {name}",
                ) from exc
        else:
            publish = f".{name}.publish-{os.getpid()}-{os.urandom(6).hex()}"
            try:
                os.link(
                    f"/proc/self/fd/{fd}", publish, dst_dir_fd=parent_fd,
                    follow_symlinks=True,
                )
                os.replace(
                    publish, name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd,
                )
            finally:
                try:
                    os.unlink(publish, dir_fd=parent_fd)
                except FileNotFoundError:
                    pass
        installed = True
        published = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        after_publish = os.fstat(fd)
        if (
            (published.st_dev, published.st_ino)
            != (after_publish.st_dev, after_publish.st_ino)
            or published.st_size != len(data)
            or after_publish.st_size != len(data)
        ):
            raise JournalError(
                "concurrent_mutation", "published atomic bytes changed identity",
            )
        current_temp = os.stat(temp, dir_fd=parent_fd, follow_symlinks=False)
        if (current_temp.st_dev, current_temp.st_ino) != (
            after_publish.st_dev, after_publish.st_ino,
        ):
            raise JournalError(
                "concurrent_mutation", "atomic temporary name changed during publish",
            )
        os.unlink(temp, dir_fd=parent_fd)
        temp_removed = True
        os.fsync(parent_fd)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        if not temp_removed:
            try:
                current = os.stat(temp, dir_fd=parent_fd, follow_symlinks=False)
                if (current.st_dev, current.st_ino) == (
                    source_identity.st_dev, source_identity.st_ino,
                ):
                    os.unlink(temp, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def atomic_write(path: Path, data: bytes, mode: int = 0o600, *, exclusive: bool = False) -> None:
    _, parent_fd = _open_absolute_directory(path.parent)
    try:
        _atomic_write_at(parent_fd, path.name, data, mode, exclusive=exclusive)
    finally:
        os.close(parent_fd)


def _prospective_target_at(
    root_fd: int, relative: str,
) -> tuple[str | None, list[str]]:
    """Validate an existing prefix without creating transaction state."""

    rel = safe_relative(relative)
    descriptor = os.dup(root_fd)
    missing: list[str] = []
    try:
        for index, part in enumerate(rel.parts[:-1]):
            prefix = PurePosixPath(*rel.parts[: index + 1]).as_posix()
            if missing:
                missing.append(prefix)
                continue
            try:
                info = os.stat(part, dir_fd=descriptor,
                               follow_symlinks=False)
            except FileNotFoundError:
                missing.append(prefix)
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise JournalError(
                    "unsafe_path", f"transaction parent is unsafe: {prefix}",
                )
            child = _open_relative_directory_at(descriptor, part)
            os.close(descriptor)
            descriptor = child
        before = None
        if not missing:
            before = _file_digest_at(
                descriptor, rel.parts[-1], subject=relative,
            )
        return before, missing
    finally:
        os.close(descriptor)


def _parent_and_name_at(
    root_fd: int, relative: str, *, create: bool = False,
) -> tuple[int, str]:
    parts = safe_relative(relative).parts
    if len(parts) == 1:
        return os.dup(root_fd), parts[0]
    return (
        _open_relative_directory_at(
            root_fd, PurePosixPath(*parts[:-1]).as_posix(), create=create,
        ),
        parts[-1],
    )


@dataclass
class PlannedWrite:
    relative: str
    data: bytes
    mode: int = 0o600
    exclusive: bool = False
    expected_digest: str | None = None


class Transaction:
    """Roll-forward transaction. Caller must hold an exclusive WorkspaceLock."""

    def __init__(self, root: str | os.PathLike[str], operation_id: str) -> None:
        if not _OPERATION.fullmatch(operation_id):
            raise JournalError("invalid_operation_id", "operation ID is not portable")
        self.root = _ensure_root(Path(root))
        self.operation_id = operation_id
        self.writes: list[PlannedWrite] = []

    def add(
        self,
        relative: str,
        data: bytes | dict[str, Any] | list[Any],
        *,
        mode: int = 0o600,
        exclusive: bool = False,
        expected_digest: str | None = None,
    ) -> None:
        safe_relative(relative)
        if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777:
            raise JournalError("invalid_mode", "transaction mode must be an integer permission mask")
        if not isinstance(exclusive, bool):
            raise JournalError("invalid_exclusive", "transaction exclusive flag must be boolean")
        if expected_digest is not None and (not isinstance(expected_digest, str) or not _DIGEST.fullmatch(expected_digest)):
            raise JournalError("invalid_digest", "expected digest must be canonical sha256 or null")
        payload = data if isinstance(data, bytes) else canonical_bytes(data)
        if len(payload) > MAX_DOCUMENT_BYTES:
            raise JournalError(
                "document_resource_limit",
                "transaction document exceeds the fixed byte limit",
            )
        if any(item.relative == relative for item in self.writes):
            raise JournalError("duplicate_write", f"transaction contains duplicate target: {relative}")
        self.writes.append(PlannedWrite(relative, payload, mode, exclusive, expected_digest))

    def commit(self, *, lock: WorkspaceLock) -> None:
        root_fd = lock.duplicate_root(self.root)
        try:
            identity = _workspace_identity(root_fd)
            _recover_at(root_fd, identity)
            transaction_rel = f"_transactions/{self.operation_id}"
            _clean_orphan_operation_at(root_fd, self.operation_id)
            records: list[dict[str, Any]] = []
            for index, item in enumerate(self.writes):
                before, missing = _prospective_target_at(
                    root_fd, item.relative,
                )
                if item.exclusive and before is not None:
                    raise JournalError(
                        "target_exists",
                        f"append-only target already exists: {item.relative}",
                    )
                if item.expected_digest is not None and before != item.expected_digest:
                    raise JournalError(
                        "cas_conflict",
                        f"target digest changed before commit: {item.relative}",
                    )
                staged_rel = f"{transaction_rel}/{index:04d}.stage"
                _prospective_target_at(root_fd, staged_rel)
                records.append({
                    "target": item.relative,
                    "stage": staged_rel,
                    "before_digest": before,
                    "after_digest": digest_bytes(item.data),
                    "mode": item.mode,
                    "exclusive": item.exclusive,
                })
            journal = {
                "schema": "sdm-journal/v1",
                "operation_id": self.operation_id,
                "workspace_identity": identity,
                "phase": "planning",
                "writes": records,
                # Untrusted recovery bytes must never authorize deletion of a
                # non-private target ancestor.  Empty target directories may
                # remain after a planning-phase crash, but no pre-existing
                # directory can be removed by a crafted journal.
                "created_parents": [],
            }
            _atomic_write_at(
                root_fd, "_journal.json", canonical_bytes(journal),
                0o600, exclusive=True,
            )
            try:
                for record in records:
                    target_parts = safe_relative(record["target"]).parts[:-1]
                    if target_parts:
                        _ensure_directory_at(
                            root_fd, PurePosixPath(*target_parts).as_posix(),
                        )
                for item, record in zip(self.writes, records, strict=True):
                    stage_parent, stage_name = _parent_and_name_at(
                        root_fd, record["stage"], create=True,
                    )
                    try:
                        _atomic_write_at(
                            stage_parent, stage_name, item.data, item.mode,
                            exclusive=True,
                        )
                    finally:
                        os.close(stage_parent)
                journal["phase"] = "prepared"
                _atomic_write_at(
                    root_fd, "_journal.json", canonical_bytes(journal), 0o600,
                )
            except BaseException:
                try:
                    _recover_at(root_fd, identity)
                except BaseException:
                    pass
                raise
            _apply_journal_at(root_fd, journal)
            journal["phase"] = "committed"
            _atomic_write_at(
                root_fd, "_journal.json", canonical_bytes(journal), 0o600,
            )
            journal_fd, journal_bytes, journal_identity = (
                _open_recovery_journal_at(root_fd)
            )
            try:
                if journal_bytes != canonical_bytes(journal):
                    raise JournalError(
                        "journal_conflict",
                        "workspace recovery journal changed before cleanup",
                    )
                _cleanup_at(
                    root_fd, journal, journal_fd, journal_identity,
                )
            finally:
                os.close(journal_fd)
        finally:
            os.close(root_fd)


def _clean_orphan_operation_at(root_fd: int, operation_id: str) -> None:
    """Remove only recognizable unpublished stage debris for this operation."""
    try:
        transactions_fd = _open_relative_directory_at(root_fd, "_transactions")
    except FileNotFoundError:
        return
    try:
        try:
            directory_fd = _open_relative_directory_at(
                transactions_fd, operation_id,
            )
        except FileNotFoundError:
            return
        try:
            for name in sorted(os.listdir(directory_fd)):
                if not re.fullmatch(r"[0-9]{4}\.stage", name):
                    raise JournalError(
                        "orphan_transaction",
                        f"unexpected orphan transaction entry: {name}",
                    )
                info = os.stat(name, dir_fd=directory_fd,
                               follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise JournalError(
                        "unsafe_path", f"orphan stage is unsafe: {name}",
                    )
                os.unlink(name, dir_fd=directory_fd)
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.rmdir(operation_id, dir_fd=transactions_fd)
        os.fsync(transactions_fd)
    finally:
        os.close(transactions_fd)
    try:
        os.rmdir("_transactions", dir_fd=root_fd)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY):
            raise
    os.fsync(root_fd)


def _apply_journal_at(root_fd: int, journal: dict[str, Any]) -> None:
    workspace_mount = _mount_identity(root_fd)
    for record in journal["writes"]:
        target_parent, target_name = _parent_and_name_at(root_fd, record["target"])
        try:
            try:
                current = _file_digest_at(
                    target_parent, target_name, subject=record["target"],
                )
            except (OSError, JournalError) as exc:
                raise JournalError(
                    "recovery_conflict",
                    f"target is unsafe or unstable during recovery: {record['target']}",
                ) from exc
            if current == record["after_digest"]:
                continue
            if current != record["before_digest"]:
                raise JournalError(
                    "recovery_conflict",
                    f"target diverged during recovery: {record['target']}",
                )
            stage_parent, stage_name = _parent_and_name_at(
                root_fd, record["stage"],
            )
            try:
                try:
                    stage_fd = os.open(
                        stage_name, _FILE_FLAGS, dir_fd=stage_parent,
                    )
                except OSError as exc:
                    raise JournalError(
                        "recovery_stage_invalid",
                        f"staged bytes cannot be opened safely: {record['stage']}",
                    ) from exc
                try:
                    opened = os.fstat(stage_fd)
                    if (
                        not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                        or opened.st_size > MAX_DOCUMENT_BYTES
                        or _mount_identity(stage_fd) != workspace_mount
                    ):
                        raise JournalError(
                            "recovery_stage_invalid",
                            f"staged bytes are unsafe: {record['stage']}",
                        )

                    # Apply the governed mode before the authenticated read so
                    # every mutation-sensitive field is stable from hashing to
                    # publication.
                    os.fchmod(stage_fd, record["mode"])
                    os.fsync(stage_fd)
                    before_hash = os.fstat(stage_fd)
                    if (
                        not stat.S_ISREG(before_hash.st_mode)
                        or before_hash.st_nlink != 1
                        or before_hash.st_size > MAX_DOCUMENT_BYTES
                        or _mount_identity(stage_fd) != workspace_mount
                    ):
                        raise JournalError(
                            "recovery_stage_invalid",
                            f"staged bytes changed before hashing: {record['stage']}",
                        )
                    hasher = __import__("hashlib").sha256()
                    payload = bytearray()
                    captured = 0
                    while captured < before_hash.st_size:
                        part = os.read(
                            stage_fd,
                            min(1024 * 1024, before_hash.st_size - captured),
                        )
                        if not part:
                            break
                        hasher.update(part)
                        payload.extend(part)
                        captured += len(part)
                    after_hash = os.fstat(stage_fd)
                    if (
                        captured != before_hash.st_size
                        or _stable_file_identity(before_hash)
                        != _stable_file_identity(after_hash)
                        or "sha256:" + hasher.hexdigest()
                        != record["after_digest"]
                    ):
                        raise JournalError(
                            "recovery_stage_invalid",
                            f"staged bytes are missing or corrupt: {record['stage']}",
                        )
                    named = os.stat(
                        stage_name, dir_fd=stage_parent, follow_symlinks=False,
                    )
                    if (
                        _stable_file_identity(named)
                        != _stable_file_identity(after_hash)
                    ):
                        raise JournalError(
                            "recovery_stage_invalid",
                            f"staged name changed during recovery: {record['stage']}",
                        )

                    # Final full-identity check immediately before publishing
                    # from the authenticated open descriptor.
                    before_publish = os.fstat(stage_fd)
                    publish_named = os.stat(
                        stage_name, dir_fd=stage_parent, follow_symlinks=False,
                    )
                    if (
                        _stable_file_identity(before_publish)
                        != _stable_file_identity(after_hash)
                        or _stable_file_identity(publish_named)
                        != _stable_file_identity(after_hash)
                        or _mount_identity(stage_fd) != workspace_mount
                    ):
                        raise JournalError(
                            "recovery_stage_invalid",
                            f"staged bytes changed before publish: {record['stage']}",
                        )
                    # Publish a fresh retained descriptor from the exact bytes
                    # authenticated above.  A writer racing the stage after the
                    # final check cannot change the installed target bytes.
                    try:
                        _atomic_write_at(
                            target_parent,
                            target_name,
                            bytes(payload),
                            record["mode"],
                            exclusive=record["exclusive"],
                        )
                    except JournalError as exc:
                        if not (
                            record["exclusive"] and exc.code == "target_exists"
                        ):
                            raise
                        try:
                            installed = _file_digest_at(
                                target_parent, target_name,
                                subject=record["target"],
                            )
                        except (OSError, JournalError) as digest_exc:
                            raise JournalError(
                                "recovery_conflict",
                                f"exclusive target is unsafe during recovery: {record['target']}",
                            ) from digest_exc
                        if installed != record["after_digest"]:
                            raise JournalError(
                                "recovery_conflict",
                                f"exclusive target appeared during recovery: {record['target']}",
                            )
                    after_publish = os.fstat(stage_fd)
                    if (
                        _stable_file_identity(after_publish)
                        != _stable_file_identity(before_publish)
                        or _mount_identity(stage_fd) != workspace_mount
                    ):
                        raise JournalError(
                            "recovery_stage_invalid",
                            f"staged bytes changed during publish: {record['stage']}",
                        )
                    try:
                        installed = _file_digest_at(
                            target_parent, target_name,
                            subject=record["target"],
                        )
                    except (OSError, JournalError) as exc:
                        raise JournalError(
                            "recovery_conflict",
                            f"installed target is unsafe during recovery: {record['target']}",
                        ) from exc
                    if installed != record["after_digest"]:
                        raise JournalError(
                            "recovery_conflict",
                            f"installed target changed during recovery: {record['target']}",
                        )
                    current_stage = os.stat(
                        stage_name, dir_fd=stage_parent,
                        follow_symlinks=False,
                    )
                    if (current_stage.st_dev, current_stage.st_ino) != (
                        after_publish.st_dev, after_publish.st_ino,
                    ):
                        raise JournalError(
                            "recovery_stage_invalid",
                            f"staged name changed during publish: {record['stage']}",
                        )
                    os.unlink(stage_name, dir_fd=stage_parent)
                finally:
                    os.close(stage_fd)
                os.fsync(target_parent)
                os.fsync(stage_parent)
            finally:
                os.close(stage_parent)
        finally:
            os.close(target_parent)


def _authenticated_journal_identity_at(
    root_fd: int,
    journal_fd: int,
    expected: tuple[int, ...],
    *,
    name: str,
    allow_rename_ctime: bool = False,
) -> None:
    try:
        opened = os.fstat(journal_fd)
        named = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
    except (FileNotFoundError, OSError) as exc:
        raise JournalError(
            "journal_conflict", "workspace recovery journal changed before cleanup",
        ) from exc
    opened_identity = _stable_file_identity(opened)
    named_identity = _stable_file_identity(named)
    retained_matches = (
        opened_identity[:-1] == expected[:-1]
        if allow_rename_ctime else opened_identity == expected
    )
    if (
        not retained_matches
        or named_identity != opened_identity
        or _mount_identity(journal_fd) != _mount_identity(root_fd)
    ):
        raise JournalError(
            "journal_conflict", "workspace recovery journal changed before cleanup",
        )


def _remove_authenticated_journal_at(
    root_fd: int, journal_fd: int, expected: tuple[int, ...],
) -> None:
    """Remove only the journal inode retained and parsed by the caller."""

    _authenticated_journal_identity_at(
        root_fd, journal_fd, expected, name="_journal.json",
    )
    quarantine = (
        f"._journal.json.cleanup-{os.getpid()}-{os.urandom(12).hex()}"
    )
    try:
        os.replace(
            "_journal.json", quarantine,
            src_dir_fd=root_fd, dst_dir_fd=root_fd,
        )
    except OSError as exc:
        raise JournalError(
            "journal_conflict", "workspace recovery journal changed during cleanup",
        ) from exc

    # Renaming first ensures a substitution of the public journal name can at
    # worst be quarantined.  No bytes are unlinked until the quarantined name
    # has been rebound to the descriptor retained since parsing.
    _authenticated_journal_identity_at(
        root_fd, journal_fd, expected, name=quarantine,
        allow_rename_ctime=True,
    )
    try:
        os.unlink(quarantine, dir_fd=root_fd)
    except OSError as exc:
        raise JournalError(
            "journal_conflict", "workspace recovery journal could not be removed safely",
        ) from exc
    os.fsync(root_fd)


def _cleanup_at(
    root_fd: int,
    journal: dict[str, Any],
    journal_fd: int,
    journal_identity: tuple[int, ...],
) -> None:
    for record in journal["writes"]:
        try:
            stage_parent, stage_name = _parent_and_name_at(
                root_fd, record["stage"],
            )
        except (FileNotFoundError, JournalError) as exc:
            if isinstance(exc, FileNotFoundError) or (
                isinstance(exc, JournalError) and exc.code == "missing_parent"
            ):
                continue
            raise
        try:
            try:
                os.unlink(stage_name, dir_fd=stage_parent)
                os.fsync(stage_parent)
            except FileNotFoundError:
                pass
        finally:
            os.close(stage_parent)
    try:
        transactions_fd = _open_relative_directory_at(root_fd, "_transactions")
    except FileNotFoundError:
        transactions_fd = -1
    if transactions_fd >= 0:
        try:
            os.rmdir(journal["operation_id"], dir_fd=transactions_fd)
            os.fsync(transactions_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno != errno.ENOTEMPTY:
                raise
        finally:
            os.close(transactions_fd)
    try:
        os.rmdir("_transactions", dir_fd=root_fd)
    except OSError as exc:
        if exc.errno not in (errno.ENOENT, errno.ENOTEMPTY):
            raise
    _remove_authenticated_journal_at(
        root_fd, journal_fd, journal_identity,
    )


def _validate_journal(value: Any) -> dict[str, Any]:
    def corrupt(message: str) -> JournalError:
        return JournalError("journal_corrupt", message)

    if not isinstance(value, dict) or set(value) != _JOURNAL_KEYS:
        raise corrupt("workspace recovery journal has an invalid outer schema")
    if value.get("schema") != "sdm-journal/v1":
        raise corrupt("workspace recovery journal has an invalid schema identifier")
    operation = value.get("operation_id")
    if not isinstance(operation, str) or not _OPERATION.fullmatch(operation):
        raise corrupt("workspace recovery journal has an invalid operation ID")
    identity = value.get("workspace_identity")
    if (
        not isinstance(identity, dict) or set(identity) != _IDENTITY_KEYS
        or any(
            isinstance(identity.get(field), bool)
            or not isinstance(identity.get(field), int)
            or identity[field] < 0
            for field in ("device", "inode")
        )
        or not isinstance(identity.get("mount_id"), str)
        or re.fullmatch(r"mnt_id:[0-9]+", identity["mount_id"]) is None
    ):
        raise corrupt("workspace recovery journal has an invalid host identity")
    if value.get("phase") not in {"planning", "prepared", "committed"}:
        raise corrupt("workspace recovery journal has an unknown phase")
    writes = value.get("writes")
    parents = value.get("created_parents")
    if not isinstance(writes, list) or not isinstance(parents, list):
        raise corrupt("workspace recovery journal collections are malformed")
    seen_targets: set[str] = set()
    seen_stages: set[str] = set()
    for index, record in enumerate(writes):
        if not isinstance(record, dict) or set(record) != _WRITE_KEYS:
            raise corrupt("workspace recovery journal contains a malformed write")
        target = record.get("target")
        stage = record.get("stage")
        try:
            if not isinstance(target, str):
                raise JournalError("unsafe_path", "target is not text")
            safe_relative(target)
            if not isinstance(stage, str):
                raise JournalError("unsafe_path", "stage is not text")
            safe_relative(stage)
        except JournalError as exc:
            raise corrupt("workspace recovery journal contains an unsafe path") from exc
        expected_stage = f"_transactions/{operation}/{index:04d}.stage"
        if stage != expected_stage or target in seen_targets or stage in seen_stages:
            raise corrupt("workspace recovery journal paths are inconsistent")
        seen_targets.add(target)
        seen_stages.add(stage)
        before = record.get("before_digest")
        after = record.get("after_digest")
        if before is not None and (not isinstance(before, str) or not _DIGEST.fullmatch(before)):
            raise corrupt("workspace recovery journal has an invalid prior digest")
        if not isinstance(after, str) or not _DIGEST.fullmatch(after):
            raise corrupt("workspace recovery journal has an invalid staged digest")
        mode = record.get("mode")
        if isinstance(mode, bool) or not isinstance(mode, int) or not 0 <= mode <= 0o777:
            raise corrupt("workspace recovery journal has an invalid mode")
        if not isinstance(record.get("exclusive"), bool):
            raise corrupt("workspace recovery journal has an invalid exclusive flag")
    if parents:
        raise corrupt(
            "workspace recovery journal cannot authorize parent deletion",
        )
    return value


def _recover_at(root_fd: int, identity: dict[str, Any]) -> bool:
    try:
        journal_fd, journal_bytes, journal_identity = (
            _open_recovery_journal_at(root_fd)
        )
    except FileNotFoundError:
        return False
    try:
        try:
            journal = _validate_journal(parse_document(journal_bytes))
        except JournalError:
            raise
        except (KeyError, TypeError, ValueError, RecursionError) as exc:
            raise JournalError(
                "journal_corrupt", "workspace recovery journal is malformed",
            ) from exc
        if journal["workspace_identity"] != identity:
            raise JournalError(
                "journal_corrupt",
                "workspace recovery journal belongs to another root identity",
            )
        if journal["phase"] == "planning":
            _cleanup_at(
                root_fd, journal, journal_fd, journal_identity,
            )
            return True
        if journal["phase"] == "prepared":
            _apply_journal_at(root_fd, journal)
        _cleanup_at(root_fd, journal, journal_fd, journal_identity)
        return True
    finally:
        os.close(journal_fd)


def recover(
    root: str | os.PathLike[str], *, lock: WorkspaceLock,
) -> bool:
    root_fd = lock.duplicate_root(Path(root))
    try:
        identity = _workspace_identity(root_fd)
        return _recover_at(root_fd, identity)
    finally:
        os.close(root_fd)


def _diagnostic_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect the SDM transaction-journal library")
    parser.parse_args(argv)
    sys.stdout.write(json.dumps({"status": "pass", "command": "journal.help"}) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(_diagnostic_main())
