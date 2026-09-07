from __future__ import annotations

"""Publish derived SQLite indexes only after a stable-input build."""

from contextlib import contextmanager
import fnmatch
import os
from pathlib import Path
import sqlite3
import tempfile

from gx3cli.gx3_input_identity import ANALYSIS_INPUTS, input_version as _version


BUILD_CONTRACT = "stable-project-input-build-1"


def require_build_contract(con: sqlite3.Connection, path: Path) -> None:
    """A matching content stamp alone cannot certify a pre-guard build."""
    row = con.execute("select value from meta where key='build_contract'").fetchone()
    if row is None or row[0] != BUILD_CONTRACT:
        raise SystemExit(
            f"index construction cannot be verified against the stable-input build contract: {path}\n"
            "Rebuild this project's xref/index-lite with the current build command."
        )


def _output_stamp(path: Path) -> tuple | None:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return None
    return stat.st_size, stat.st_mtime_ns, stat.st_ino


@contextmanager
def atomic_index_build(root: Path, output: Path, *, connect=None):
    """Keep the previous artifact on errors; caller must not close the handle.

    Uses content and stat change detection, not a lock on project source files.
    External CSV provenance and multi-artifact atomicity are separate contracts.
    """
    root, output = root.resolve(), output.resolve()
    if output.parent == root and any(fnmatch.fnmatch(output.name, p) for p in ANALYSIS_INPUTS):
        raise SystemExit("index output overlaps project analysis inputs; choose an external path or .sqlite output")
    initial = _version(root)
    previous_output = _output_stamp(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".building", dir=output.parent)
    os.close(fd)
    staged = Path(name)
    con = None
    try:
        con = (connect or sqlite3.connect)(staged)
        yield con
        final = _version(root)
        stored = con.execute("select value from meta where key='input_sha256'").fetchone()
        if initial != final or stored is None or stored[0] != initial[0]:
            raise SystemExit("project inputs changed during index construction; previous index preserved; retry the build")
        con.execute("insert or replace into meta(key, value) values ('build_contract', ?)", (BUILD_CONTRACT,))
        con.commit()
        con.close()
        con = None
        if Path(str(output) + "-wal").exists() or Path(str(output) + "-shm").exists():
            raise SystemExit("index has active SQLite WAL sidecars; close its users before rebuilding; previous index preserved")
        if _output_stamp(output) != previous_output:
            raise SystemExit("index destination changed during construction; newer output preserved; retry the build")
        os.replace(staged, output)
    finally:
        if con is not None:
            con.close()
        staged.unlink(missing_ok=True)
