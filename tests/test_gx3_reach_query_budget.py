from __future__ import annotations

"""Keep the shared reach walk linear in the number of expanded devices.

#76 consolidated change-impact and xref downstream onto one graph walk. A wall
clock threshold is a poor regression test across GitHub runners, but SQLite can
tell us exactly how many SELECT statements the walk issued. This pins the
property we care about: making the specimen four times longer must not turn the
walk into a quadratic storm of repeated database lookups.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_reach import reach
from test_gx3_shared_reach import build_xref, coil, write_program


def query_count_for_chain(length: int) -> tuple[int, int]:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        rungs: list[tuple[str, str]] = []
        source = 1
        for index in range(length):
            destination = 100 + index
            rungs.append((f"_guid/{index}", coil("a", source, destination)))
            source = destination

        write_program(work / "p", rungs)
        db = build_xref(work / "p", work / "x.sqlite")

        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        selects: list[str] = []
        con.set_trace_callback(
            lambda sql: selects.append(sql)
            if sql.lstrip().upper().startswith("SELECT")
            else None
        )
        try:
            walked = reach(
                con,
                "M1",
                max_depth=length + 2,
                max_nodes=length + 2,
            )
        finally:
            con.set_trace_callback(None)
            con.close()

        assert len(walked.steps) == length, (length, len(walked.steps))
        assert not walked.truncated, walked.stopped
        # The start node plus every reached node is expanded once.
        return len(selects), len(walked.steps) + 1


def test_shared_reach_query_count_scales_linearly() -> None:
    small_queries, small_expanded = query_count_for_chain(8)
    large_queries, large_expanded = query_count_for_chain(32)

    assert large_expanded == small_expanded * 4 - 3, (small_expanded, large_expanded)

    # Current implementation is one metadata SELECT plus at most two SELECTs
    # per expanded node (xref and data_flow). Allow room for one additional
    # per-node lookup without making this test implementation-fragile, but a
    # nested re-scan / N^2 regression will fail decisively.
    assert small_queries <= 4 * small_expanded + 4, (small_queries, small_expanded)
    assert large_queries <= 4 * large_expanded + 4, (large_queries, large_expanded)

    # Four times the useful work should stay in the same order of growth. The
    # additive setup query makes the exact ratio a little below four today;
    # five is deliberately generous across harmless internal refactors.
    assert large_queries <= small_queries * 5, (small_queries, large_queries)


def main() -> int:
    test_shared_reach_query_count_scales_linearly()
    print("shared reach query-budget check passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
