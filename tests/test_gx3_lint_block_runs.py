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
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_lint import LintContext, check_multi_writer, run_checks
from gx3cli.gx3_external_inputs import read_refresh_areas, load_refresh_areas
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


def test_refresh_evidence_reaches_lint_summary() -> None:
    """Actual LDDB -> decoder -> xref -> check -> summary, with CSV input faults."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [("_guid/m", mov(100, 200))])
        header = "device_start,device_end\n"
        cases = [
            (None, "not_evaluated", "not_evaluated", 0),
            (header, "checked", "checked", 1),
            (header + "D100,D100\n", "checked", "checked", 0),
            (header + "D100,M100\n", "partial", "not_evaluated", 0),
            (header + "D110,D100\n", "partial", "not_evaluated", 0),
            (header + "D100,D100\nD110,M110\n", "partial", "not_evaluated", 0),
            ("unexpected,columns\n", "not_evaluated", "not_evaluated", 0),
            ("device_start,device_end,device_end\n", "not_evaluated", "not_evaluated", 0),
            (header + '"D100,D100\n', "partial", "not_evaluated", 0),
            (header + "D100,\n", "partial", "not_evaluated", 0),
            (header + "D100,D100,extra\n", "partial", "not_evaluated", 0),
            (header + "M1A,M1B\n", "partial", "not_evaluated", 0),
            (header + "XA,XF\n", "checked", "checked", 1),
        ]
        for index, (text, reader_state, check_state, count) in enumerate(cases):
            path = work / f"refresh-{index}.csv"
            if text is not None:
                path.write_text(text, encoding="utf-8")
            evidence = read_refresh_areas(path)
            assert evidence.analysis.state == reader_state, (text, evidence)
            with contextlib.closing(sqlite3.connect(db)) as con:
                con.row_factory = sqlite3.Row
                ctx = LintContext(root=root, rows=[], comments={}, xref=con, refresh_csv=str(path))
                with contextlib.redirect_stdout(io.StringIO()):
                    summary = run_checks(ctx, ["external-value-source"], str(work / f"report-{index}"))
            result = summary["checks"]["external-value-source"]
            assert result["state"] == check_state, (text, result)
            assert result["count"] == count, (text, result)
            assert "provenance" in result["detail"]["scope"], result
            assert bool(summary["analysis"]["inconclusive"]) == (check_state != "checked")
            if text in (header, header + "D100,M100\n"):
                repo = Path(__file__).resolve().parents[1]
                process = subprocess.run(
                    [sys.executable, "-m", "gx3cli.gx3_lint", str(root), "--xref-db", str(db),
                     "--index-db", str(work / "missing-lite.sqlite"), "--link-db", str(work / "missing-link.sqlite"),
                     "--refresh-csv", str(path), "--checks", "external-value-source", "--require-evaluated",
                     "--format", "json", "--out-prefix", str(work / f"cli-{index}")],
                    cwd=tmp, env=dict(os.environ, PYTHONPATH=str(repo), PYTHONIOENCODING="utf-8"),
                    capture_output=True, text=True, encoding="utf-8", timeout=20,
                )
                assert (process.returncode == 0) == (check_state == "checked"), (process.stdout, process.stderr)
                result = json.loads(process.stdout)["checks"]["external-value-source"]
                assert result["state"] == check_state and result["count"] == count, result
        for size in (9, 10, 11):
            bounded = work / "bounded.csv"
            bounded.write_text(header + "D100,D100\n" + "D110,M110\n" * size, encoding="utf-8")
            evidence = read_refresh_areas(bounded)
            assert len(evidence.areas) == 1
            assert evidence.analysis.detail["invalid_count"] == size
            assert len(evidence.analysis.detail["invalid_lines"]) == min(size, 10)
        invalid_encoding = work / "bad-encoding.csv"
        invalid_encoding.write_bytes(b"device_start,device_end\n\xff")
        assert read_refresh_areas(invalid_encoding).analysis.state == "partial"
        try:
            load_refresh_areas(invalid_encoding)
        except UnicodeError:
            pass
        else:
            raise AssertionError("legacy reader must not turn a decoding failure into empty evidence")


def main() -> int:
    test_refresh_evidence_reaches_lint_summary()
    test_a_write_inside_a_block_run_is_a_second_writer()
    test_a_device_the_run_does_not_reach_is_not_a_second_writer()
    test_one_overlap_is_one_finding_not_one_per_device()
    test_devices_with_different_writers_stay_separate()
    test_a_single_writer_is_still_not_a_finding()
    print("lint block run checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
