from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_topology_conditions import load_trace_constant_context


def test_trace_constant_pruning_is_disabled_without_index_lite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        previous = Path.cwd()
        os.chdir(tmp)
        try:
            root = Path("project")
            root.mkdir()
            context = load_trace_constant_context(root, [], [])
        finally:
            os.chdir(previous)

    assert context.enabled is False, context
    assert context.facts == {}, context
    assert "index-lite database not found" in context.reason, context.reason


def test_trace_constant_pruning_is_disabled_for_malformed_index_lite() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        previous = Path.cwd()
        os.chdir(tmp)
        try:
            root = Path("project")
            root.mkdir()
            index_dir = Path(".gx3_index")
            index_dir.mkdir()
            con = sqlite3.connect(index_dir / "project.sqlite")
            con.execute("create table unrelated(value text)")
            con.commit()
            con.close()
            context = load_trace_constant_context(root, [], [])
        finally:
            os.chdir(previous)

    assert context.enabled is False, context
    assert context.facts == {}, context
    assert "index-lite unavailable for constant pruning" in context.reason, context.reason


def main() -> int:
    tests = [
        test_trace_constant_pruning_is_disabled_without_index_lite,
        test_trace_constant_pruning_is_disabled_for_malformed_index_lite,
    ]
    for test in tests:
        test()
    print(f"{len(tests)} trace constant-context checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
