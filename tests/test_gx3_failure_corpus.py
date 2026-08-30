from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import zipfile
from pathlib import Path

from gx3cli.gx3_failure_corpus import capture_case, extract_gx3, read_manifest, replay_argv, run_corpus
from gx3cli.gx3_synthetic_project import create_synthetic_project


def capture_args(corpus: Path, root: Path, case_id: str) -> argparse.Namespace:
    return argparse.Namespace(
        corpus=str(corpus),
        root=str(root),
        gx3=None,
        case_id=case_id,
        overwrite=False,
        reason="regression fixture for a parser failure",
        failed_command="gx3-cli doctor --root {root} --warn-only --no-script-check",
        notes="captured by test",
        inactive=False,
        expect_doctor=True,
        expect_xref=True,
        expect_ladder_print=True,
        expect_failed_command=True,
    )


def create_fbd_only_project(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "CPU.PRM").write_bytes(b"\0")
    con = sqlite3.connect(root / "001_FBDDB.db")
    con.execute("create table Dummy(id integer)")
    con.commit()
    con.close()
    return root


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = create_synthetic_project(work / "fixture")
        corpus = work / "corpus"

        assert capture_case(capture_args(corpus, root, "synthetic-failure")) == 0

        manifest = read_manifest(corpus)
        cases = manifest["cases"]
        assert len(cases) == 1, cases
        assert cases[0]["case_file"] == "cases/synthetic-failure/case.json"
        case_file = corpus / cases[0]["case_file"]
        assert case_file.exists()
        case = json.loads(case_file.read_text(encoding="utf-8"))
        assert case["id"] == "synthetic-failure"
        assert case["project"] == "project"
        assert (case_file.parent / case["project"]).exists()
        assert case["reason"] == "regression fixture for a parser failure"

        result = run_corpus(argparse.Namespace(corpus=str(corpus), reports_dir="", warn_only=False))
        assert result == 0
        summaries = list((corpus / "reports").glob("*/summary.json"))
        assert summaries, "run should write a summary report"
        summary = json.loads(summaries[0].read_text(encoding="utf-8"))
        checks = {check["name"]: check for check in summary["results"][0]["checks"]}
        assert checks["failed_command"]["passed"] is True

        rel_work = work / "relative"
        rel_work.mkdir()
        rel_root = create_synthetic_project(rel_work / "fixture")
        old_cwd = Path.cwd()
        try:
            import os

            os.chdir(rel_work)
            assert capture_case(capture_args(Path(".gx3_failures"), rel_root, "relative-corpus")) == 0
            rel_manifest = read_manifest(Path(".gx3_failures"))
            assert rel_manifest["cases"][0]["case_file"] == "cases/relative-corpus/case.json"
            assert run_corpus(argparse.Namespace(corpus=".gx3_failures", reports_dir="", warn_only=False)) == 0
        finally:
            os.chdir(old_cwd)

        fbd_root = create_fbd_only_project(work / "fbd-only")
        fbd_corpus = work / "fbd-corpus"
        assert capture_case(capture_args(fbd_corpus, fbd_root, "fbd-only")) == 0
        assert run_corpus(argparse.Namespace(corpus=str(fbd_corpus), reports_dir="", warn_only=False)) == 0
        fbd_summary_path = next((fbd_corpus / "reports").glob("*/summary.json"))
        fbd_summary = json.loads(fbd_summary_path.read_text(encoding="utf-8"))
        fbd_checks = {check["name"]: check for check in fbd_summary["results"][0]["checks"]}
        assert fbd_checks["format_inventory"]["formats"]["FBDDB"] == 1
        assert "unsupported/non-ladder formats detected" in fbd_checks["schema"]["detail"]
        assert fbd_checks["xref"]["detail"].startswith("skipped: no LDDB")
        assert fbd_checks["ladder_print"]["detail"].startswith("skipped: no LDDB")

        try:
            replay_argv("gx3-cli doctor --root {root}; rm -rf x", root, work)
        except ValueError as exc:
            assert "shell metacharacters" in str(exc)
        else:
            raise AssertionError("unsafe replay command should be rejected")
        spaced_args = replay_argv("gx3-cli doctor --root {root}", work / "path with spaces", work)
        assert str(work / "path with spaces") in spaced_args

        bad_zip = work / "bad.gx3"
        with zipfile.ZipFile(bad_zip, "w") as zf:
            zf.writestr("../escape.txt", "bad")
        try:
            extract_gx3(bad_zip, work / "bad_extract")
        except SystemExit as exc:
            assert "unsafe gx3 archive member path" in str(exc)
        else:
            raise AssertionError("unsafe archive member should be rejected")

    print("all failure-corpus checks passed")


if __name__ == "__main__":
    main()
