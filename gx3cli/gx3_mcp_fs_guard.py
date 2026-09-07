from __future__ import annotations

"""Filesystem write guard used only by MCP-launched CLI subprocesses.

The MCP server is intentionally allowed to read a user-selected GX3 project,
but generated artifacts must stay inside ``GX3_MCP_OUTPUT_DIR``.  This module
is loaded from a temporary ``sitecustomize.py`` inserted by the MCP server, so
it also applies to nested Python subprocesses started by CLI commands.

This is a confused-deputy boundary, not an OS privilege boundary.  It protects
against an MCP caller choosing an arbitrary writable path through CLI arguments.
It does not attempt to defend against a hostile local process racing path
resolution or against arbitrary native code loaded into the Python process.
"""

import builtins
import io
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname


SANDBOX_ENV = "GX3_MCP_SANDBOX_ROOT"
_INSTALLED = False
_ROOT: Path | None = None


def _canonical(path: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> Path:
    value = os.fsdecode(os.fspath(path))
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    return candidate.resolve(strict=False)


def _inside(path: str | bytes | os.PathLike[str] | os.PathLike[bytes]) -> bool:
    assert _ROOT is not None
    try:
        _canonical(path).relative_to(_ROOT)
        return True
    except (ValueError, OSError):
        return False


def _blocked(operation: str, path: object) -> PermissionError:
    return PermissionError(
        f"MCP filesystem sandbox blocked {operation} outside {SANDBOX_ENV}: {path}"
    )


def _require_inside(path: object, operation: str, *, dir_fd: int | None = None) -> None:
    if isinstance(path, int):
        return
    if dir_fd is not None:
        # Resolving a relative path against an arbitrary directory fd is not
        # portable. Fail closed rather than accidentally treating it as cwd.
        raise _blocked(f"{operation} with dir_fd", path)
    if not isinstance(path, (str, bytes, os.PathLike)) or not _inside(path):
        raise _blocked(operation, path)


def _mode_writes(mode: object) -> bool:
    if mode is None:
        return False
    text = str(mode)
    return any(flag in text for flag in ("w", "a", "x", "+"))


def _flags_write(flags: int) -> bool:
    write_mask = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    return bool(flags & write_mask)


def _install_open_guards() -> None:
    original_open = builtins.open
    original_io_open = io.open
    original_os_open = os.open
    original_path_open = Path.open

    def guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        if _mode_writes(mode) and not isinstance(file, int):
            _require_inside(file, "open for write")
        return original_open(file, mode, *args, **kwargs)

    def guarded_io_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):
        if _mode_writes(mode) and not isinstance(file, int):
            _require_inside(file, "io.open for write")
        return original_io_open(file, mode, *args, **kwargs)

    def guarded_os_open(path: Any, flags: int, mode: int = 0o777, *, dir_fd: int | None = None):
        if _flags_write(flags):
            _require_inside(path, "os.open for write", dir_fd=dir_fd)
        return original_os_open(path, flags, mode, dir_fd=dir_fd)

    def guarded_path_open(
        self: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ):
        # pathlib implementations have changed across supported Python
        # versions. Some versions retain an opener captured before
        # sitecustomize patches io.open, so guard Path.open explicitly too.
        if _mode_writes(mode):
            _require_inside(self, "Path.open for write")
        return original_path_open(
            self,
            mode=mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
        )

    builtins.open = guarded_open
    io.open = guarded_io_open
    os.open = guarded_os_open
    Path.open = guarded_path_open


def _guard_tempfile_event(event: str, args: tuple) -> None:
    # tempfile retries PermissionError from os.open/os.mkdir on Windows as a
    # possible random-name collision. Reject before entering that retry loop,
    # including for nested CLI processes constructing staged SQLite indexes.
    if event in {"tempfile.mkstemp", "tempfile.mkdtemp"}:
        _require_inside(args[0], event)


def _install_mutation_guards() -> None:
    original_mkdir = os.mkdir
    original_remove = os.remove
    original_unlink = os.unlink
    original_rmdir = os.rmdir
    original_rename = os.rename
    original_replace = os.replace
    original_link = os.link
    original_symlink = os.symlink
    original_chmod = os.chmod
    original_utime = os.utime
    original_truncate = os.truncate

    def mkdir(path: Any, mode: int = 0o777, *, dir_fd: int | None = None):
        _require_inside(path, "mkdir", dir_fd=dir_fd)
        return original_mkdir(path, mode, dir_fd=dir_fd)

    def remove(path: Any, *, dir_fd: int | None = None):
        _require_inside(path, "remove", dir_fd=dir_fd)
        return original_remove(path, dir_fd=dir_fd)

    def unlink(path: Any, *, dir_fd: int | None = None):
        _require_inside(path, "unlink", dir_fd=dir_fd)
        return original_unlink(path, dir_fd=dir_fd)

    def rmdir(path: Any, *, dir_fd: int | None = None):
        _require_inside(path, "rmdir", dir_fd=dir_fd)
        return original_rmdir(path, dir_fd=dir_fd)

    def rename(
        src: Any,
        dst: Any,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ):
        _require_inside(src, "rename source", dir_fd=src_dir_fd)
        _require_inside(dst, "rename destination", dir_fd=dst_dir_fd)
        return original_rename(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def replace(
        src: Any,
        dst: Any,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ):
        _require_inside(src, "replace source", dir_fd=src_dir_fd)
        _require_inside(dst, "replace destination", dir_fd=dst_dir_fd)
        return original_replace(src, dst, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    def link(
        src: Any,
        dst: Any,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ):
        # A hard link to an outside inode would let a later in-sandbox write
        # mutate the outside file, so both ends must be inside.
        _require_inside(src, "hard-link source", dir_fd=src_dir_fd)
        _require_inside(dst, "hard-link destination", dir_fd=dst_dir_fd)
        return original_link(
            src,
            dst,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    def symlink(
        src: Any,
        dst: Any,
        target_is_directory: bool = False,
        *,
        dir_fd: int | None = None,
    ):
        # The link target may point anywhere; opening through it is checked
        # again after path resolution. Creating the link itself may only touch
        # the sandbox.
        _require_inside(dst, "symlink destination", dir_fd=dir_fd)
        return original_symlink(src, dst, target_is_directory, dir_fd=dir_fd)

    def chmod(
        path: Any,
        mode: int,
        *,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ):
        _require_inside(path, "chmod", dir_fd=dir_fd)
        return original_chmod(path, mode, dir_fd=dir_fd, follow_symlinks=follow_symlinks)

    def utime(
        path: Any,
        times: Any = None,
        *,
        ns: Any = None,
        dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ):
        _require_inside(path, "utime", dir_fd=dir_fd)
        return original_utime(
            path,
            times,
            ns=ns,
            dir_fd=dir_fd,
            follow_symlinks=follow_symlinks,
        )

    def truncate(path: Any, length: int):
        if not isinstance(path, int):
            _require_inside(path, "truncate")
        return original_truncate(path, length)

    os.mkdir = mkdir
    os.remove = remove
    os.unlink = unlink
    os.rmdir = rmdir
    os.rename = rename
    os.replace = replace
    os.link = link
    os.symlink = symlink
    os.chmod = chmod
    os.utime = utime
    os.truncate = truncate


def _sqlite_uri_path(value: str) -> Path | None:
    if not value.startswith("file:"):
        return None
    parsed = urlsplit(value)
    if parsed.query and "mode=memory" in parsed.query:
        return None
    raw = unquote(parsed.path)
    if parsed.netloc:
        raw = f"//{parsed.netloc}{raw}"
    return _canonical(url2pathname(raw))


def _install_sqlite_guard() -> None:
    original_connect = sqlite3.connect

    def connect(database: Any, *args: Any, **kwargs: Any):
        if database in (":memory:", ""):
            return original_connect(database, *args, **kwargs)
        if not isinstance(database, (str, bytes, os.PathLike)):
            return original_connect(database, *args, **kwargs)

        text = os.fsdecode(os.fspath(database))
        uri_path = _sqlite_uri_path(text)
        path = uri_path if uri_path is not None else _canonical(text)
        if _inside(path):
            return original_connect(database, *args, **kwargs)

        # External project/index DBs are inputs. Force them read-only even when
        # legacy callers use plain sqlite3.connect(path), so a build subcommand
        # cannot turn an input-looking path into an overwrite target.
        if not path.exists():
            raise _blocked("SQLite create", path)
        if text.startswith("file:") and "mode=ro" in text:
            return original_connect(database, *args, **kwargs)
        if "uri" in kwargs and not bool(kwargs["uri"]):
            kwargs = dict(kwargs)
            kwargs.pop("uri", None)
        readonly_uri = path.resolve().as_uri() + "?mode=ro"
        kwargs = dict(kwargs)
        kwargs["uri"] = True
        return original_connect(readonly_uri, *args, **kwargs)

    sqlite3.connect = connect


def _install_subprocess_guard() -> None:
    original_popen = subprocess.Popen
    python = Path(sys.executable).resolve(strict=False)

    def popen(args: Any, *pargs: Any, **kwargs: Any):
        if kwargs.get("shell"):
            raise PermissionError("MCP filesystem sandbox blocks shell subprocesses")
        if isinstance(args, (str, bytes)) or not args:
            raise PermissionError("MCP filesystem sandbox requires an explicit Python subprocess")
        executable = kwargs.get("executable") or args[0]
        try:
            candidate = _canonical(executable)
        except (TypeError, ValueError, OSError):
            raise PermissionError(f"MCP filesystem sandbox blocked subprocess: {executable}")
        if candidate != python:
            raise PermissionError(f"MCP filesystem sandbox blocked non-Python subprocess: {executable}")
        text_args = [os.fsdecode(os.fspath(item)) for item in args[1:] if isinstance(item, (str, bytes, os.PathLike))]
        if any(item in {"-S", "-I", "-E"} for item in text_args):
            raise PermissionError("MCP filesystem sandbox blocked Python flags that bypass sandbox bootstrap")
        return original_popen(args, *pargs, **kwargs)

    subprocess.Popen = popen


def install_from_env() -> None:
    """Install the guard when launched by the MCP server."""

    global _INSTALLED, _ROOT
    if _INSTALLED:
        return
    raw = os.environ.get(SANDBOX_ENV)
    if not raw:
        return
    root = Path(raw).expanduser().resolve(strict=False)
    if not root.is_dir():
        raise RuntimeError(f"MCP sandbox root does not exist: {root}")
    _ROOT = root
    sys.addaudithook(_guard_tempfile_event)
    _install_open_guards()
    _install_mutation_guards()
    _install_sqlite_guard()
    _install_subprocess_guard()
    _INSTALLED = True
