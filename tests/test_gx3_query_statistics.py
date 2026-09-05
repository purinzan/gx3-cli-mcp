from __future__ import annotations

"""A database ships knowing which of its indexes is worth using.

Without statistics SQLite picks an index by shape rather than by how many rows
it will touch. On a real project, for "device=? and access=?", it chose the
index on `access` -- 53,000 rows for a read, scanned and then sorted -- over
the one on `device`, which would have found three. 27ms instead of 0.1ms, and
dead-logic runs one such query per device: 6,665 of them, 72 of its 75 seconds.

ANALYZE takes a tenth of a second. So the builders run it, and a database built
before they did is repaired in place rather than declared stale -- it is not
wrong, it is only uninformed, and rebuilding it would cost a hundred times more
than fixing it.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_synthetic_project import create_demo_line_project
from gx3cli.gx3_workspace import prepare


def has_statistics(path: Path) -> bool:
    con = sqlite3.connect(path)
    try:
        row = con.execute(
            "select count(*) from sqlite_master where type='table' and name='sqlite_stat1'"
        ).fetchone()
        return bool(row and row[0])
    finally:
        con.close()


def drop_statistics(path: Path) -> None:
    """Make a database look like one built before the builders ran ANALYZE."""
    con = sqlite3.connect(path)
    con.execute("drop table if exists sqlite_stat1")
    con.commit()
    con.close()


def test_what_the_builders_write_has_statistics() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        workspace = prepare(project)
        assert has_statistics(workspace.xref.path), workspace.xref.path
        assert has_statistics(workspace.index.path), workspace.index.path


def test_a_database_built_without_them_is_repaired_not_rebuilt() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        first = prepare(project)
        drop_statistics(first.xref.path)
        stamp = first.xref.path.stat().st_mtime_ns

        again = prepare(project)
        assert "xref" in again.analysed, again.as_dict()
        assert "xref" in again.reused, "it was rebuilt when it only needed statistics"
        assert "xref" not in again.built, again.built
        assert has_statistics(again.xref.path)
        # Repaired in place: the same file, not a fresh build of it.
        assert again.xref.path.stat().st_mtime_ns != stamp


def test_a_database_that_already_has_them_is_left_alone() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        prepare(project)
        again = prepare(project)
        assert again.analysed == [], again.analysed


def test_the_planner_uses_the_device_index_for_the_query_that_was_slow() -> None:
    # The shape dead-logic asks once per device. With statistics the planner
    # reaches for `device`; without them it reached for `access`.
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        workspace = prepare(project)
        con = sqlite3.connect(workspace.xref.path)
        plan = " ".join(
            str(row[3])
            for row in con.execute(
                "explain query plan select pou, step from xref "
                "where device=? and access in (?) order by pou, step limit ?",
                ("M1", "read", 3),
            )
        )
        con.close()
        assert "idx_xref_access" not in plan, plan


def main() -> int:
    test_what_the_builders_write_has_statistics()
    test_a_database_built_without_them_is_repaired_not_rebuilt()
    test_a_database_that_already_has_them_is_left_alone()
    test_the_planner_uses_the_device_index_for_the_query_that_was_slow()
    print("query statistics checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
