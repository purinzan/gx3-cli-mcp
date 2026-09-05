from __future__ import annotations

"""An empty answer says which kind of empty it is.

Found by working through docs/REVIEW_QUESTIONS_JA.md against the code:

Question 3 -- what input makes a success-shaped answer wrongly? Asking about a
device that is not in the project at all returned "devices=1, driver_rows=0,
truncated=False", the same answer as a device that is present and undriven.
`xref where-used` has always said "no occurrences" and exited non-zero; the
other two commands did not.

Question 5 -- does a correction reach every walk? `trace-device` builds its
drivers from coil roles, so a word device written by an instruction had no
drivers at all: on a real project one with sixteen writing rows reported
driver_rows=0 as a complete answer.
"""

import contextlib
import io
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_analysis_state import DISCOVERY, NOT_EVALUATED
from gx3cli.gx3_ladder_report import build
from gx3cli.review_gx3_project import load_comments_for_root, load_rows
from gx3cli.trace_gx3_device_dependencies import build_trace, driver_index
from test_gx3_shared_reach import BMOV, MOV_FROM_MIDDLE, build_xref, coil, write_program


def a_program(work: Path) -> Path:
    write_program(work / "p", [("_guid/a", coil("a", 1, 100))])
    return work / "p"


def trace_for(root: Path, device: str, depth: int = 3) -> dict:
    return build_trace(
        root=root, target_device=device, max_depth=depth,
        max_devices=50, include_reset=True, strict_logic=False,
    )


def test_a_device_that_is_not_in_the_project_is_not_an_answer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = a_program(Path(tmp))
        trace = trace_for(root, "M9999")
        analysis = trace["analysis"]
        assert analysis["state"] == NOT_EVALUATED, analysis
        assert analysis["stage"] == DISCOVERY, analysis
        assert "does not appear" in analysis["reason"], analysis
        assert "where-used" in analysis["next_step"], analysis


def test_a_device_that_is_present_and_undriven_still_answers() -> None:
    # The opposite error would be as bad: an input nothing writes is a real
    # answer, and the most useful one this tool gives.
    with tempfile.TemporaryDirectory() as tmp:
        root = a_program(Path(tmp))
        trace = trace_for(root, "M1")  # a contact, read but never written
        assert trace["analysis"]["state"] != NOT_EVALUATED, trace["analysis"]


def test_an_instruction_that_writes_is_a_driver() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/b", BMOV), ("_guid/m", MOV_FROM_MIDDLE)])
        comments = load_comments_for_root(work / "p")
        drivers = driver_index(load_rows(work / "p", comments))
        assert drivers.get("D900"), "a device a MOV writes had no driver rows"

        trace = trace_for(work / "p", "D900")
        assert trace["stats"]["driver_rows"] >= 1, trace["stats"]
        assert trace["analysis"]["state"] != NOT_EVALUATED, trace["analysis"]


def test_a_report_for_a_device_it_cannot_find_says_so() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_program(work)
        db = build_xref(root, work / "x.sqlite")

        report = build(root, db, "001_LDDB.db", device="M9999", limit=0)
        assert report.rungs == [], report.rungs
        assert report.state.state == NOT_EVALUATED, report.state
        assert report.state.stage == DISCOVERY, report.state

        from gx3cli.gx3_ladder_report import render_html

        assert "M9999" in render_html(report), "the page does not say what it looked for"


def test_a_report_for_a_device_it_does_find_is_not_flagged() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_program(work)
        db = build_xref(root, work / "x.sqlite")
        report = build(root, db, "001_LDDB.db", device="M100", limit=0)
        assert report.rungs, "the fixture's own device was not found"
        assert report.state.state != NOT_EVALUATED, report.state


def main() -> int:
    test_a_device_that_is_not_in_the_project_is_not_an_answer()
    test_a_device_that_is_present_and_undriven_still_answers()
    test_an_instruction_that_writes_is_a_driver()
    test_a_report_for_a_device_it_cannot_find_says_so()
    test_a_report_for_a_device_it_does_find_is_not_flagged()
    print("empty answer checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
