from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_doctor import _project_health_args, main as doctor_main
from gx3cli.gx3_lint import LintContext
from gx3cli.gx3_project_health import build_report, collect_io_comment_gaps, finding_priority
from gx3cli.gx3_synthetic_project import create_synthetic_project


def test_priority_promotes_high_risk_and_physical_output() -> None:
    high_y = {
        "check": "multi-writer",
        "severity": "high",
        "device": "Y10",
        "comment": "",
        "count": 3,
    }
    stale = {
        "check": "unused-device",
        "severity": "info",
        "device": "M100",
        "comment": "old flag",
        "count": 1,
    }
    assert finding_priority(high_y) > finding_priority(stale)


def test_build_report_ranks_and_scores_dimensions() -> None:
    findings = {
        "multi-writer": [
            {
                "check": "multi-writer",
                "severity": "high",
                "device": "D100",
                "comment": "recipe value",
                "count": 3,
                "locations": "P1 st10 | P2 st20 | P3 st30",
                "detail": "three writers",
                "review_note": "review writers",
            }
        ],
        "io-comment-gap": [
            {
                "check": "io-comment-gap",
                "severity": "medium",
                "device": "Y20",
                "comment": "",
                "count": 2,
                "locations": "MAIN st40",
                "detail": "physical output has no comment",
                "review_note": "identify field signal",
            }
        ],
    }
    report = build_report(Path("fixture"), findings, {}, top=10)
    assert report["total_findings"] == 2
    assert report["top_risks"][0]["device"] in {"D100", "Y20"}
    assert report["scores"]["Change safety"] < 100
    assert report["scores"]["Documentation"] < 100
    assert report["score_kind"].startswith("heuristic")
    assert report["health"] != "INCOMPLETE"


def test_io_comment_gap_only_flags_uncommented_physical_io() -> None:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table xref(device text, device_type text, comment text, pou text, step integer)")
    con.executemany(
        "insert into xref values(?,?,?,?,?)",
        [
            ("X0", "X", "Start PB", "MAIN", 1),
            ("X1", "X", "", "MAIN", 2),
            ("Y0", "Y", "", "MAIN", 3),
            ("M0", "M", "", "MAIN", 4),
        ],
    )
    ctx = LintContext(root=Path("fixture"), rows=[], comments={}, xref=con)
    findings = collect_io_comment_gaps(ctx)
    assert {f["device"] for f in findings} == {"X1", "Y0"}
    assert next(f for f in findings if f["device"] == "Y0")["severity"] == "medium"
    assert next(f for f in findings if f["device"] == "X1")["severity"] == "info"
    con.close()


def test_doctor_project_health_mode_delegates_and_reports_partial_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "fixture")
        index_dir = work / "indexes"
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = doctor_main(
                [
                    "--project-health",
                    "--root",
                    str(root),
                    "--index-dir",
                    str(index_dir),
                    "--link-db",
                    str(index_dir / "link_map.sqlite"),
                    "--format",
                    "json",
                    "--top",
                    "3",
                ]
            )
        assert code == 0
        report = json.loads(buf.getvalue())
        assert report["mode"] == "project-health"
        assert report["analysis"]["total_checks"] >= 1
        assert report["analysis"]["inconclusive"]
        assert report["health"] == "INCOMPLETE"
        assert report["provisional_health"] is not None
        assert len(report["top_risks"]) <= 3


def test_mode_flag_is_removed_before_forwarding() -> None:
    assert _project_health_args(["--project-health", "--root", "abc", "--top", "5"]) == [
        "--root",
        "abc",
        "--top",
        "5",
    ]
    assert _project_health_args(["--root", "abc"]) is None


def main() -> None:
    test_priority_promotes_high_risk_and_physical_output()
    test_build_report_ranks_and_scores_dimensions()
    test_io_comment_gap_only_flags_uncommented_physical_io()
    test_doctor_project_health_mode_delegates_and_reports_partial_coverage()
    test_mode_flag_is_removed_before_forwarding()
    print("project health checks passed")


if __name__ == "__main__":
    main()
