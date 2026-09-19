from __future__ import annotations

"""Acceptance ledger for Doctor project-health initial release (#135)."""

import argparse
import json
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Acceptance:
    key: str
    requirement: str
    implementation: str
    evidence: str
    status: str = "checked"


ACCEPTANCE = [
    Acceptance(
        "separate_workspace_and_project_health",
        "既存doctorのworkspace healthとproject maintainability healthを表示上区別する。",
        "`gx3-cli doctor --project-health` delegates to project-health reporting and emits mode=project-health in JSON.",
        "tests/test_gx3_doctor_next_steps.py::test_project_health_cli_json_entrypoint",
    ),
    Acceptance(
        "single_command_project_diagnosis",
        "1コマンドでプロジェクト全体の診断を実行できる。",
        "`doctor --project-health --root <project>` prepares/reads the real project evidence path through collect_project_health.",
        "tests/test_gx3_doctor_maintainability_profiles.py::_report",
    ),
    Acceptance(
        "five_dimensions",
        "Documentation / Ownership / Complexity / Dead-or-legacy / Troubleshootingの5観点を出す。",
        "Existing scores are Maintainability, Traceability, Change safety, Troubleshootability, Documentation; findings map ownership/complexity/dead-or-legacy into these stable public score names.",
        "tests/test_gx3_doctor_next_steps.py::test_project_health_report_ranks_and_scores_dimensions",
    ),
    Acceptance(
        "counts_and_representative_findings",
        "各軸について点数だけでなく根拠件数・代表findingを出す。",
        "Project-health reports checks, counts, top_risks, reason, evidence, human_check, and related device/POU fields.",
        "tests/test_gx3_doctor_next_steps.py::test_project_health_report_ranks_and_scores_dimensions",
    ),
    Acceptance(
        "top_risks",
        "Top 10 risksを重要度順に表示する。",
        "`build_health_report(..., top=N)` ranks findings by severity and truncates top_risks to N.",
        "tests/test_gx3_doctor_next_steps.py::test_project_health_report_ranks_and_scores_dimensions",
    ),
    Acceptance(
        "reuse_existing_checks",
        "duplicate-coil / multi-writer / unused-device / comment-conflict / alarm-quality等の既存結果を再利用し、別実装しない。",
        "collect_project_health gathers existing lint/dead-logic checks and the good/bad fixture verifies those check outputs instead of parallel scoring.",
        "tests/test_gx3_doctor_maintainability_profiles.py",
    ),
    Acceptance(
        "do_not_score_missing_analysis_as_ok",
        "未解析・未対応領域を問題なしとして採点しない。",
        "Inconclusive core checks mark dimensions as not assessed instead of producing misleading perfect scores.",
        "tests/test_gx3_doctor_next_steps.py::test_missing_core_checks_make_dependent_scores_not_assessed",
    ),
    Acceptance(
        "important_io_comment_gap_weight",
        "重要デバイスのコメント欠落を、全デバイス一律のコメント率より重く扱う。",
        "io-comment-gap is a dedicated Doctor input and bad-maintainability intentionally lacks important X/Y comments.",
        "tests/test_gx3_doctor_maintainability_profiles.py",
    ),
    Acceptance(
        "finding_provenance",
        "1件のfindingからPOU/step/元ラダーへ辿れる。",
        "Findings keep evidence and source fields; project-health top risks expose the originating check record rather than only a score.",
        "tests/test_gx3_doctor_next_steps.py::test_project_health_report_ranks_and_scores_dimensions",
    ),
    Acceptance(
        "json_for_mcp_ai",
        "JSON出力を持ち、MCP/AIがTop 10を説明できる。",
        "`doctor --project-health --format json` returns scores, checks, top_risks, and analysis coverage.",
        "tests/test_gx3_doctor_next_steps.py::test_project_health_cli_json_entrypoint",
    ),
    Acceptance(
        "bad_maintainability_fixture",
        "コメント欠落・複数writer・SET/RST分離・未使用残骸・曖昧命名を入れたbad fixtureを用意する。",
        "create_bad_maintainability_project creates those non-confidential patterns.",
        "tests/test_gx3_doctor_maintainability_profiles.py",
    ),
    Acceptance(
        "good_bad_regression",
        "同じ機能を整理したgood fixtureと比較し、Doctor score/findingが明確に改善することを回帰テストする。",
        "The good/bad test checks lower bad scores and concrete findings while preserving the documented Y0/Y1 field-output truth table.",
        "tests/test_gx3_doctor_maintainability_profiles.py",
    ),
]


def summarize() -> dict[str, object]:
    missing = [item.key for item in ACCEPTANCE if item.status != "checked"]
    return {
        "issue": 135,
        "closeable": not missing,
        "checked_items": len(ACCEPTANCE) - len(missing),
        "required_items": len(ACCEPTANCE),
        "missing_items": missing,
        "items": [asdict(item) for item in ACCEPTANCE],
    }


def _print_text(summary: dict[str, object]) -> None:
    print(f"Issue #135 Doctor acceptance: {summary['checked_items']}/{summary['required_items']} items checked")
    print(f"Closeable: {'yes' if summary['closeable'] else 'no'}")
    if summary["missing_items"]:
        print("Missing items:")
        for key in summary["missing_items"]:
            print(f"- {key}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report Doctor project-health acceptance status for Issue #135.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    args = parser.parse_args(argv)
    summary = summarize()
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_text(summary)
    return 0 if summary["closeable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
