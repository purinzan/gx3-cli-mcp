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


def flow_xref_db(args: argparse.Namespace, root: Path) -> Path | None:
    named = getattr(args, "xref_db", "") or ""
    if named:
        return Path(named)
    try:
        from gx3cli.gx3_workspace import locate

        artefact = locate(root).xref
    except Exception:
        return None
    return artefact.path if artefact.usable else None
