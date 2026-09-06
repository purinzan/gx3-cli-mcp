from __future__ import annotations

"""One range, asked about through both indexes, answered the same way.

#84's remaining consumer gap. `covered_ranges` has held block-instruction runs
since they were decoded, and `query-device` started from `devices`, which holds
only the names a rung spells. So the index knew that a BMOV writes D402 and the
question could not reach the knowledge:

    xref  D402  ->  BMOV, write
    lite  D402  ->  device not found

"Not found" is the right answer for a device nothing touches. It was being
given for a device something writes every scan.

What is deliberately not done: turning a covered device into occurrences of its
own. Four devices under one run are one occurrence covering four, and inventing
rows would make the counts wrong in the other direction. The answer names the
run instead.
"""

import contextlib
import io
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_index_lite import main as lite_main
from gx3cli.gx3_xref_read import occurrences_of
from test_gx3_lint_block_runs import bmov, mov
from test_gx3_shared_reach import build_xref, write_program


def both_indexes(work: Path) -> tuple[Path, Path, Path]:
    """One project, one BMOV run, built into the cross-reference and the index."""
    write_program(work / "p", [("_guid/b", bmov(300, 400, 4)), ("_guid/m", mov(401, 900))])
    xref = build_xref(work / "p", work / "x.sqlite")
    with contextlib.redirect_stdout(io.StringIO()):
        assert (
            lite_main(["build", "--root", str(work / "p"), "--out", str(work / "lite.sqlite")])
            == 0
        )
    return work / "p", xref, work / "lite.sqlite"


def ask_lite(lite: Path, root: Path, device: str) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = lite_main(["device", device, "--db", str(lite), "--root", str(root)])
        except SystemExit as exit_error:
            code = int(exit_error.code or 0)
    return code, out.getvalue()


def ask_xref(xref: Path, device: str) -> list[str]:
    con = sqlite3.connect(xref)
    con.row_factory = sqlite3.Row
    try:
        return [str(row["opcode"]) for row in occurrences_of(con, device)]
    finally:
        con.close()


def test_both_indexes_agree_that_the_run_reaches_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, xref, lite = both_indexes(work)

        assert "BMOV" in ask_xref(xref, "D402"), ask_xref(xref, "D402")
        code, body = ask_lite(lite, root, "D402")
        assert code == 0, body
        assert "D400" in body and "BMOV" in body, body
        assert "not found" not in body, body


def test_a_covered_device_is_not_dressed_up_as_its_own_occurrence() -> None:
    # The count belongs to the run, not to each device the run reaches.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, _, lite = both_indexes(work)
        _, body = ask_lite(lite, root, "D402")
        assert "occurrences=" not in body, body
        assert "length=4" in body, body


def test_named_and_covered_evidence_survive_together() -> None:
    # D401 is named by MOV and is also the second destination word of the BMOV.
    # A point query must not let the exact named row hide the covering run.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, xref, lite = both_indexes(work)
        assert set(ask_xref(xref, "D401")) == {"BMOV", "MOV"}

        code, body = ask_lite(lite, root, "D401")
        assert code == 0, body
        assert "occurrences=1" in body, body
        assert "Covered ranges:" in body, body
        assert "D400" in body and "BMOV" in body, body
        assert "offset=1" in body and "length=4" in body, body


def test_a_device_the_ladder_names_answers_as_before() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, _, lite = both_indexes(work)
        code, body = ask_lite(lite, root, "D900")
        assert code == 0, body
        assert "occurrences=" in body, body


def test_one_past_the_end_is_still_not_found() -> None:
    # The opposite error would be as bad: a device nothing touches has to be
    # answerable as such, or the range check means nothing.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, xref, lite = both_indexes(work)
        assert ask_xref(xref, "D404") == [], ask_xref(xref, "D404")
        code, body = ask_lite(lite, root, "D404")
        assert code == 1, body
        assert "not found" in body, body


def test_the_json_form_carries_named_and_covered_semantics() -> None:
    import json

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, _, lite = both_indexes(work)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            try:
                lite_main(
                    ["device", "D401", "--db", str(lite), "--root", str(root), "--json"]
                )
            except SystemExit:
                pass
        payload = json.loads(out.getvalue())
        results = payload.get("results") or payload.get("data") or []
        assert results, payload
        result = results[0]
        assert result.get("occurrences") == 1, result
        covered = result.get("covered_by") or []
        assert covered, result
        bmov = next(item for item in covered if item.get("opcode") == "BMOV")
        assert bmov.get("covered_by") == "D400", bmov
        assert bmov.get("run_offset") == 1, bmov
        assert bmov.get("run_length") == 4, bmov
        assert bmov.get("match_kind") == "covered", bmov


def main() -> int:
    test_both_indexes_agree_that_the_run_reaches_it()
    test_a_covered_device_is_not_dressed_up_as_its_own_occurrence()
    test_named_and_covered_evidence_survive_together()
    test_a_device_the_ladder_names_answers_as_before()
    test_one_past_the_end_is_still_not_found()
    test_the_json_form_carries_named_and_covered_semantics()
    print("covered lookup checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
