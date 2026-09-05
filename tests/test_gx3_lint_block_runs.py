from __future__ import annotations

"""Two rungs writing one word, when one of them names a different device.

Found by working through docs/REVIEW_QUESTIONS_JA.md, question 5: does a
correction reach every reader of the data? Block-instruction runs were recorded
in the cross-reference and honoured by dead-logic and by the walk, and lint
never looked at `range_len` at all. So `BMOV .. D400 K4` and `MOV .. D401` both
write D401, and multi-writer -- the check whose whole subject is a word written
from two places -- reported nothing.

The first fix was worse than the bug: expanding every run into its devices took
the real project from 590 findings to 38,998, which were 1,020 facts. One pair
of block instructions overwriting one range produced 7,679 identical findings.
A list that long is not read, and an unread check finds nothing at all.

So a run is reported as a run: contiguous devices written by the same rungs
collapse into one finding that names the range.
"""

import contextlib
import io
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_lint import LintContext, check_multi_writer
from test_gx3_shared_reach import build_xref, rung, write_program


def bmov(source: int, destination: int, count: int) -> str:
    return rung(
        "BMOV:D:D:K_1",
        f"d{{s=#:a={source}:vt=nn}}:d{{s=#:a={destination}:vt=nn}}:c{{s=#:v={count}}}",
    )


def mov(source: int, destination: int) -> str:
    return rung("MOV:D:D", f"d{{s=#:a={source}:vt=nn}}:d{{s=#:a={destination}:vt=nn}}")


def findings_for(root: Path, db: Path) -> list[dict]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        ctx = LintContext(root=root, rows=[], comments={}, xref=con)
        with contextlib.redirect_stdout(io.StringIO()):
            return check_multi_writer(ctx)
    finally:
        con.close()


def project(work: Path, rungs: list[tuple[str, str]]) -> tuple[Path, Path]:
    write_program(work / "p", rungs)
    return work / "p", build_xref(work / "p", work / "x.sqlite")


def test_a_write_inside_a_block_run_is_a_second_writer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [
            ("_guid/b", bmov(300, 400, 4)),   # writes D400..D403
            ("_guid/m", mov(500, 401)),       # writes D401 as well
        ])
        found = {str(item["device"]): item for item in findings_for(root, db)}
        assert "D401" in found, found
        assert int(found["D401"]["count"]) == 2, found["D401"]


def test_a_device_the_run_does_not_reach_is_not_a_second_writer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [
            ("_guid/b", bmov(300, 400, 4)),   # D400..D403
            ("_guid/m", mov(500, 404)),       # one past the end
        ])
        found = {str(item["device"]) for item in findings_for(root, db)}
        assert "D404" not in found, found


def test_one_overlap_is_one_finding_not_one_per_device() -> None:
    """The failure the first fix caused: 7,679 findings for one pair of rungs."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [
            ("_guid/a", bmov(300, 400, 16)),
            ("_guid/b", bmov(600, 400, 16)),  # the same run, from elsewhere
        ])
        found = findings_for(root, db)
        assert len(found) == 1, [item["device"] for item in found]
        assert str(found[0]["device"]) == "D400..D415", found[0]["device"]
        assert "16 devices" in str(found[0]["detail"]), found[0]["detail"]


def test_devices_with_different_writers_stay_separate() -> None:
    # Collapsing is by "written by the same rungs". Two devices that happen to
    # be neighbours but have different writers are two facts.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [
            ("_guid/a", bmov(300, 400, 4)),
            ("_guid/b", mov(500, 400)),
            ("_guid/c", mov(600, 401)),
        ])
        devices = {str(item["device"]) for item in findings_for(root, db)}
        assert devices == {"D400", "D401"}, devices


def test_a_single_writer_is_still_not_a_finding() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [("_guid/a", bmov(300, 400, 8))])
        assert findings_for(root, db) == []


def main() -> int:
    test_a_write_inside_a_block_run_is_a_second_writer()
    test_a_device_the_run_does_not_reach_is_not_a_second_writer()
    test_one_overlap_is_one_finding_not_one_per_device()
    test_devices_with_different_writers_stay_separate()
    test_a_single_writer_is_still_not_a_finding()
    print("lint block run checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
