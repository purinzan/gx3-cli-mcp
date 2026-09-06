from __future__ import annotations

"""Finding the cross-reference a command needs, without asking the caller.

A command that needs value-flow edges should not make the reader remember
where the index lives; the workspace already knows, and it is the same answer
every other command gets. A command that was given a path uses that path.

Nothing here builds anything. A missing cross-reference means no value edges,
which the callers report as a limit on the answer rather than as an error --
they had no value edges at all before this existed.
"""

import argparse
from pathlib import Path

from gx3cli.gx3_xref import open_xref_db


def validated_xref(path: Path, root: Path) -> Path:
    """Return an existing xref only after proving which project built it.

    The flow readers themselves intentionally know only SQLite/data_flow. The
    project-aware boundary is here, where both the chosen root and xref path are
    available. A stale or swapped xref must be rejected before those readers can
    combine its value edges with another project's ladder.
    """
    if not path.exists():
        return path
    con = open_xref_db(path, read_only=True, root=root)
    con.close()
    return path


def flow_xref_db(args: argparse.Namespace, root: Path) -> Path | None:
    named = getattr(args, "xref_db", "") or ""
    if named:
        return validated_xref(Path(named), root)
    try:
        from gx3cli.gx3_workspace import locate

        artefact = locate(root).xref
    except Exception:
        return None
    return validated_xref(artefact.path, root) if artefact.usable else None
