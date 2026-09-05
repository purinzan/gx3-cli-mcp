from __future__ import annotations

"""Where this project's index lives, and whether it still answers for it.

Every command that needs a cross-reference takes a `--db`, and every one of
them spells the default itself: `.gx3_index/<label>_xref.sqlite`, relative to
the current directory. So the same project indexed from two directories has two
indexes, and a command run from a third finds neither and reports what an empty
index reports -- nothing found. That is not hypothetical: a lint run in a
temporary directory dropped from 4002 findings to 2002 for exactly this reason,
and the drop looked like a code change.

This is the one place that answers three questions: where the index for a root
is, whether the one on disk was built from this input by this build, and how to
get one that was. Nothing here decides what an analysis says -- it decides
which file the analysis reads.

Reuse is by input fingerprint, storage version and analyzer version, never by
timestamp. Being written a second later is normal for a file an editor saves;
two different fingerprints are not.
"""

import argparse
import contextlib
import io
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from gx3cli.gx3_cli import project_label_from_root
from gx3cli.gx3_index_lite import DEVICE_NAMING
from gx3cli.gx3_input_identity import fingerprint, short
from gx3cli.gx3_output import add_format_argument, emit
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.gx3_version import package_version
from gx3cli.gx3_xref import XREF_DECODER


INDEX_DIR_NAME = ".gx3_index"

READY = "ready"
MISSING = "missing"
OTHER_INPUT = "other-input"
OLD_BUILD = "old-build"
UNREADABLE = "unreadable"


@dataclass(frozen=True)
class Artefact:
    """One database, and whether it answers for the project asked about."""

    kind: str  # "index" or "xref"
    path: Path
    state: str
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.state == READY

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": str(self.path),
            "state": self.state,
            "detail": self.detail,
        }


@dataclass
class Workspace:
    root: Path
    directory: Path
    label: str
    input_sha256: str
    index: Artefact
    xref: Artefact
    built: list[str] = field(default_factory=list)
    reused: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return self.index.usable and self.xref.usable

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "directory": str(self.directory),
            "label": self.label,
            "input_sha256": self.input_sha256,
            "index": self.index.as_dict(),
            "xref": self.xref.as_dict(),
            "built": list(self.built),
            "reused": list(self.reused),
        }


def candidate_dirs(root: Path) -> list[Path]:
    """Directories an index for this root may already be sitting in.

    The first is where a new one goes: beside the project, so that working from
    another directory does not lose it. The others are where the existing
    commands put theirs, and are searched so an index built before this module
    existed is found rather than silently rebuilt next to it.
    """
    root = Path(root).resolve()
    out: list[Path] = []
    for candidate in (
        root.parent / INDEX_DIR_NAME,
        Path.cwd() / INDEX_DIR_NAME,
        root / INDEX_DIR_NAME,
    ):
        if candidate not in out:
            out.append(candidate)
    return out


def meta_of(path: Path) -> dict[str, str] | None:
    """The meta table of a database, {} if unreadable, None if not there."""
    if not path.exists():
        return None
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        return {}
    try:
        return {str(key): str(value) for key, value in con.execute("select key, value from meta")}
    except sqlite3.Error:
        return {}
    finally:
        con.close()


def _judge(kind: str, path: Path, expected_input: str) -> Artefact:
    meta = meta_of(path)
    if meta is None:
        return Artefact(kind, path, MISSING, "not built yet")
    if not meta:
        return Artefact(kind, path, UNREADABLE, "no meta table; not a database this build wrote")

    stored_input = meta.get("input_sha256", "")
    if stored_input and expected_input and stored_input != expected_input:
        return Artefact(
            kind,
            path,
            OTHER_INPUT,
            f"built from {short(stored_input)}, this project is {short(expected_input)}",
        )
    if not stored_input:
        return Artefact(kind, path, OLD_BUILD, "built before inputs were stamped")

    if kind == "xref":
        stored = meta.get("decoder", "")
        if stored != XREF_DECODER:
            return Artefact(
                kind, path, OLD_BUILD,
                f"decoder {stored or '(none)'}, this build reads {XREF_DECODER}",
            )
    else:
        stored = meta.get("device_naming", "")
        if stored != DEVICE_NAMING:
            return Artefact(
                kind, path, OLD_BUILD,
                f"device naming {stored or '(none)'}, this build writes {DEVICE_NAMING}",
            )

    version = meta.get("analyzer_version", "")
    if version and version != package_version():
        return Artefact(kind, path, OLD_BUILD, f"built by {version}, this is {package_version()}")
    return Artefact(kind, path, READY, f"input {short(stored_input)}")


def locate(root: Path) -> Workspace:
    """Find the index and cross-reference for a root, without building either."""
    root = Path(root)
    label = project_label_from_root(root)
    expected = fingerprint(root)
    directory = candidate_dirs(root)[0]

    for candidate in candidate_dirs(root):
        index = _judge("index", candidate / f"{label}.sqlite", expected)
        xref = _judge("xref", candidate / f"{label}_xref.sqlite", expected)
        if index.state == MISSING and xref.state == MISSING:
            continue
        # A directory holding something for this project is the one in use,
        # whatever state it is in: rebuilding means rebuilding that, not
        # writing a second copy somewhere else and leaving a stale one behind.
        return Workspace(root, candidate, label, expected, index, xref)

    return Workspace(
        root,
        directory,
        label,
        expected,
        Artefact("index", directory / f"{label}.sqlite", MISSING, "not built yet"),
        Artefact("xref", directory / f"{label}_xref.sqlite", MISSING, "not built yet"),
    )


def _build(module_main: Callable[[list[str]], int], argv: list[str], quiet: bool) -> None:
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink) if quiet else contextlib.nullcontext():
        code = module_main(argv)
    if code != 0:
        raise SystemExit(f"could not build: {' '.join(argv)}\n{sink.getvalue()}")


def prepare(root: Path, *, rebuild: bool = False, quiet: bool = True) -> Workspace:
    """Return a workspace whose index and cross-reference answer for this root.

    Whatever was already built from this input by this build is left alone: a
    rebuild rebuilds the files that are wrong, not everything.
    """
    from gx3cli import gx3_index_lite, gx3_xref

    workspace = locate(root)
    workspace.directory.mkdir(parents=True, exist_ok=True)

    if rebuild or not workspace.index.usable:
        _build(
            gx3_index_lite.main,
            ["build", "--root", str(root), "--out", str(workspace.index.path)],
            quiet,
        )
        workspace.built.append("index")
    else:
        workspace.reused.append("index")

    if rebuild or not workspace.xref.usable:
        _build(
            gx3_xref.main,
            ["--root", str(root), "--db", str(workspace.xref.path), "build"],
            quiet,
        )
        workspace.built.append("xref")
    else:
        workspace.reused.append("xref")

    rechecked = locate(root)
    workspace.index = rechecked.index
    workspace.xref = rechecked.xref
    return workspace


def render(workspace: Workspace) -> list[str]:
    lines = [
        f"project:   {workspace.root}",
        f"input:     {short(workspace.input_sha256)}",
        f"index dir: {workspace.directory}",
        "",
    ]
    for artefact in (workspace.index, workspace.xref):
        lines.append(f"  {artefact.kind:<6} {artefact.state:<12} {artefact.detail}")
        lines.append(f"         {artefact.path}")
    if workspace.built or workspace.reused:
        lines.append("")
        if workspace.built:
            lines.append("built:  " + ", ".join(workspace.built))
        if workspace.reused:
            lines.append("reused: " + ", ".join(workspace.reused))
    if not workspace.ready and not workspace.built:
        lines.append("")
        lines.append("Nothing here is ready to answer. Build it: gx3-cli workspace --prepare")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Show, or prepare, the index and cross-reference for a project."
    )
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--prepare", action="store_true", help="build whatever is missing or stale")
    parser.add_argument("--rebuild", action="store_true", help="rebuild both, even when usable")
    add_format_argument(parser, json_shorthand=False)
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.is_dir():
        print(f"not a project directory: {root}")
        return 1

    if args.prepare or args.rebuild:
        workspace = prepare(root, rebuild=args.rebuild, quiet=True)
    else:
        workspace = locate(root)
    return emit(args, text=lambda: render(workspace), data=workspace.as_dict)


if __name__ == "__main__":
    raise SystemExit(main())
