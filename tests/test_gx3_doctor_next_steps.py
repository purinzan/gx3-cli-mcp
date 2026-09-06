from __future__ import annotations

import contextlib
import io
import json
import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_analysis_state import not_evaluated
from gx3cli.gx3_audit import (
    build_health_report,
    collect_constant_chains,
    collect_io_comment_gaps,
    finding_priority,
)
from gx3cli.gx3_doctor import _project_health_args, main as doctor_main
from gx3cli.gx3_lint import LintContext
from gx3cli.gx3_synthetic_project import create_synthetic_project


def run_doctor(args: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = doctor_main(args)
    return code, buf.getvalue()


def test_missing_index_and_xref_show_next_commands() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "fixture")
        index_dir = work / "indexes"

        code, out = run_doctor(
            [
                "--root",
                str(root),
                "--index-dir",
                str(index_dir),
                "--link-db",
                str(index_dir / "link_map.sqlite"),
                "--warn-only",
                "--no-script-check",
            ]
        )

        assert code == 0
        assert "index-lite" in out
        assert f"next: gx3-cli index-lite build --root {root}" in out
        assert f"next: gx3-cli xref build --root {root}" in out
        assert "next: gx3-cli link-map build --project LABEL=<project-root>" in out


def test_missing_root_shows_path_hint() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        missing = Path(tmp) / "missing.gx3"

        code, out = run_doctor(
            [
                "--root",
                str(missing),
                "--warn-only",
                "--no-script-check",
            ]
        )

        assert code == 0
        assert "missing" in out
        assert "next: check --root path" in out


def test_project_health_priority_promotes_high_risk_and_physical_output() -> None:
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


def test_project_health_report_ranks_and_scores_dimensions() -> None:
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
    report = build_health_report(Path("fixture"), findings, {}, top=10)
    assert report["total_findings"] == 2
    assert report["top_risks"][0]["device"] in {"D100", "Y20"}
    assert report["scores"]["Change safety"] < 100
    assert report["scores"]["Documentation"] < 100
    assert report["score_kind"].startswith("heuristic")
    assert report["health"] != "INCOMPLETE"


def test_missing_link_map_is_supplemental_not_core_incomplete() -> None:
    findings = {"link-range": []}
    states = {"link-range": not_evaluated("no cross-project link map")}
    report = build_health_report(Path("fixture"), findings, states, top=10)
    assert report["health"] == "GOOD"
    assert report["analysis"]["core_inconclusive"] == []
    assert report["analysis"]["supplemental_inconclusive"] == ["link-range"]


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
    assert {finding["device"] for finding in findings} == {"X1", "Y0"}
    assert next(finding for finding in findings if finding["device"] == "Y0")["severity"] == "medium"
    assert next(finding for finding in findings if finding["device"] == "X1")["severity"] == "info"
    con.close()


def test_constant_chain_reaches_doctor_as_ranked_causal_evidence() -> None:
    from gx3cli.gx3_intermediate_tool import generate_rung
    from gx3cli.review_gx3_project import load_comments_for_root, load_rows
    from test_gx3_shared_reach import build_xref, write_program

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "p"
        rungs = [
            ("_guid/off", generate_rung({"device": "SM401"}, {"type": "coil", "device": "M100"})[0]),
            ("_guid/on", generate_rung({"not": {"device": "M100"}}, {"type": "coil", "device": "Y0"})[0]),
        ]
        write_program(root, rungs)
        db = build_xref(root, work / "xref.sqlite")
        comments = load_comments_for_root(root)
        rows = load_rows(root, comments)
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        lite_path = work / "lite.sqlite"
        lite = sqlite3.connect(lite_path)
        lite.execute(
            "create table external_sources(device text primary key, source_kind text, semantic_group text)"
        )
        lite.commit()
        lite.row_factory = sqlite3.Row
        try:
            ctx = LintContext(root=root, rows=rows, comments=comments, xref=con, lite=lite)
            findings = collect_constant_chains(ctx, index_db=lite_path)
        finally:
            con.close()
            lite.close()

        by_device = {str(item["device"]): item for item in findings}
        assert "M100" not in by_device, by_device
        assert by_device["Y0"]["constant_state"] == "ALWAYS_ON", by_device
        assert by_device["Y0"]["severity"] == "high", by_device["Y0"]
        assert "SM401" in str(by_device["Y0"]["chain"]), by_device["Y0"]
        assert "M100" in str(by_device["Y0"]["chain"]), by_device["Y0"]

        report = build_health_report(root, {"constant-chain": findings}, {}, top=10)
        y0 = next(item for item in report["top_risks"] if item["device"] == "Y0")
        assert y0["check"] == "constant-chain", y0
        assert y0["severity"] == "high", y0
        assert report["scores"]["Change safety"] < 100, report
        assert report["scores"]["Troubleshootability"] < 100, report


def test_project_health_mode_reports_incomplete_when_core_evidence_is_missing() -> None:
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
        assert report["analysis"]["core_inconclusive"]
        assert report["health"] == "INCOMPLETE"
        assert report["provisional_health"] is not None
        assert len(report["top_risks"]) <= 3


def test_project_health_mode_flag_is_removed_before_forwarding() -> None:
    assert _project_health_args(["--project-health", "--root", "abc", "--top", "5"]) == [
        "--root",
        "abc",
        "--top",
        "5",
    ]
    assert _project_health_args(["--root", "abc"]) is None


def main() -> None:
    test_missing_index_and_xref_show_next_commands()
    test_missing_root_shows_path_hint()
    test_project_health_priority_promotes_high_risk_and_physical_output()
    test_project_health_report_ranks_and_scores_dimensions()
    test_missing_link_map_is_supplemental_not_core_incomplete()
    test_io_comment_gap_only_flags_uncommented_physical_io()
    test_constant_chain_reaches_doctor_as_ranked_causal_evidence()
    test_project_health_mode_reports_incomplete_when_core_evidence_is_missing()
    test_project_health_mode_flag_is_removed_before_forwarding()
    print("doctor next-step and project-health checks passed")


if __name__ == "__main__":
    main()
