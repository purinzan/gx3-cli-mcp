from __future__ import annotations

"""Project-wide maintainability Doctor for GX Works3 projects.

This is an orchestration/reporting layer over existing lint/xref/index evidence.
It ranks evidence for a maintainer inheriting a project without the original
author.  Scores are navigation heuristics, not PLC safety certification.
"""

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

from gx3cli.gx3_analysis_state import checked
from gx3cli.gx3_cli import project_label_from_root
from gx3cli.gx3_lint import CHECKS, LintContext, open_checked_xref, open_optional
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.review_gx3_project import load_comments_for_root, load_rows


DOCTOR_CHECKS = (
    "duplicate-coil",
    "multi-writer",
    "alarm-quality",
    "unused-device",
    "comment-conflict",
    "link-range",
)

DIMENSIONS = (
    "Maintainability",
    "Traceability",
    "Change safety",
    "Troubleshootability",
    "Documentation",
)

SEVERITY_SCORE = {"critical": 120, "high": 90, "medium": 55, "low": 25, "info": 12}
SEVERITY_PENALTY = {"critical": 12, "high": 8, "medium": 4, "low": 2, "info": 1}
CHECK_BOOST = {
    "duplicate-coil": 22,
    "multi-writer": 24,
    "link-range": 28,
    "alarm-quality": 14,
    "io-comment-gap": 12,
    "comment-conflict": 6,
    "unused-device": 0,
}
CHECK_DIMENSIONS = {
    "duplicate-coil": ("Maintainability", "Traceability", "Change safety"),
    "multi-writer": ("Maintainability", "Traceability", "Change safety"),
    "alarm-quality": ("Troubleshootability", "Documentation"),
    "unused-device": ("Maintainability", "Traceability"),
    "comment-conflict": ("Documentation", "Traceability"),
    "link-range": ("Traceability", "Change safety", "Troubleshootability"),
    "io-comment-gap": ("Documentation", "Troubleshootability"),
}
WHY = {
    "duplicate-coil": "one output has more than one ladder driver, so an edit can change which rung owns the final state",
    "multi-writer": "one device is written from several rungs/POUs, so ownership and change impact are distributed",
    "alarm-quality": "an alarm/fault path is hard to understand or clear from the project evidence alone",
    "unused-device": "stale or one-way device usage can make legacy/spare logic look live",
    "comment-conflict": "duplicate or conflicting comment text can lead a maintainer to the wrong device",
    "link-range": "a communication receive/link device is also written locally, obscuring the true value owner",
    "io-comment-gap": "a physical I/O address has no project comment, so its field meaning is not recoverable from the project alone",
}
NEXT_REVIEW = {
    "duplicate-coil": "review every writer and execution order before changing this output",
    "multi-writer": "identify the intended owner for each mode and verify all writers before editing",
    "alarm-quality": "confirm trigger, hold and reset/clear behavior on the cited alarm rung",
    "unused-device": "confirm spare/output-only/HMI usage before reusing or deleting the address",
    "comment-conflict": "compare each cited rung and repair comments only after device identity is confirmed",
    "link-range": "verify partner PLC/link-refresh ownership before changing the local writer",
    "io-comment-gap": "identify the field signal from drawings/I/O lists and add a project comment before modification",
}


def collect_io_comment_gaps(ctx: LintContext) -> list[dict[str, object]]:
    """Flag uncommented physical X/Y without flooding on internal relays."""
    if ctx.xref is None:
        ctx.cannot_evaluate("io-comment-gap", "no cross-reference database", "gx3-cli xref build --root <project>")
        return []
    rows = ctx.xref.execute(
        """
        select device, device_type, max(coalesce(comment, '')) as comment,
               min(pou) as pou, min(step) as step, count(*) as uses
        from xref
        where device_type in ('X', 'Y')
        group by device, device_type
        order by device_type, device
        """
    ).fetchall()
    out: list[dict[str, object]] = []
    for row in rows:
        if str(row["comment"] or "").strip():
            continue
        dev_type = str(row["device_type"])
        out.append(
            {
                "check": "io-comment-gap",
                "severity": "medium" if dev_type == "Y" else "info",
                "device": str(row["device"]),
                "comment": "",
                "count": int(row["uses"] or 0),
                "locations": f"{row['pou'] or '?'} st{row['step'] if row['step'] is not None else '?'}",
                "detail": f"physical {dev_type} device is used but has no project comment",
                "review_note": NEXT_REVIEW["io-comment-gap"],
            }
        )
    return out


def finding_priority(finding: dict[str, object]) -> int:
    severity = str(finding.get("severity") or "info").lower()
    check = str(finding.get("check") or "")
    score = SEVERITY_SCORE.get(severity, 10) + CHECK_BOOST.get(check, 0)
    device = str(finding.get("device") or "")
    if device.startswith("Y"):
        score += 18
    if not str(finding.get("comment") or "").strip():
        score += 4
    score += min(int(finding.get("count") or 0), 10)
    return score


def normalize_finding(finding: dict[str, object]) -> dict[str, object]:
    item = dict(finding)
    check = str(item.get("check") or "")
    item["priority"] = finding_priority(item)
    item["why_it_matters"] = WHY.get(check, "review this evidence before changing the related logic")
    item["next_review"] = str(item.get("review_note") or NEXT_REVIEW.get(check, "review the cited ladder evidence"))
    return item


def health_scores(findings: list[dict[str, object]]) -> dict[str, int]:
    """Heuristic navigation scores; each finding class is capped for noise."""
    by_check: dict[str, list[dict[str, object]]] = defaultdict(list)
    for finding in findings:
        by_check[str(finding.get("check") or "")].append(finding)
    penalty: dict[str, int] = defaultdict(int)
    for check, items in by_check.items():
        raw = sum(SEVERITY_PENALTY.get(str(item.get("severity") or "info").lower(), 1) for item in items)
        check_penalty = min(raw, 35)
        for dimension in CHECK_DIMENSIONS.get(check, ("Maintainability",)):
            penalty[dimension] += check_penalty
    return {dimension: max(0, 100 - min(penalty.get(dimension, 0), 100)) for dimension in DIMENSIONS}


def health_label(scores: dict[str, int]) -> str:
    value = min(scores.values()) if scores else 0
    if value >= 85:
        return "GOOD"
    if value >= 70:
        return "FAIR"
    if value >= 50:
        return "POOR"
    return "CRITICAL"


def build_report(
    root: Path,
    findings_by_check: dict[str, list[dict[str, object]]],
    states: dict[str, object],
    top: int,
) -> dict[str, object]:
    findings = [normalize_finding(item) for items in findings_by_check.values() for item in items]
    findings.sort(
        key=lambda item: (-int(item["priority"]), str(item.get("check") or ""), str(item.get("device") or ""))
    )
    scores = health_scores(findings)
    checks: dict[str, object] = {}
    inconclusive: list[str] = []
    for name, items in findings_by_check.items():
        state = states.get(name, checked())
        if not bool(getattr(state, "conclusive", True)):
            inconclusive.append(name)
        state_dict = state.as_dict() if hasattr(state, "as_dict") else {"status": "unknown"}
        severities = sorted({str(item.get("severity") or "info") for item in items})
        checks[name] = {
            "count": len(items),
            "by_severity": {severity: sum(1 for item in items if str(item.get("severity") or "info") == severity) for severity in severities},
            **state_dict,
        }

    provisional_health = health_label(scores)
    health = "INCOMPLETE" if inconclusive else provisional_health
    return {
        "root": str(root),
        "mode": "project-health",
        "health": health,
        "provisional_health": provisional_health if inconclusive else None,
        "score_kind": "heuristic-maintainability-navigation-not-safety-certification",
        "scores": scores,
        "analysis": {
            "evaluated": len(findings_by_check) - len(inconclusive),
            "total_checks": len(findings_by_check),
            "inconclusive": inconclusive,
        },
        "checks": checks,
        "total_findings": len(findings),
        "top_risks": findings[: max(0, top)],
    }


def collect_project_health(
    root: Path,
    *,
    index_dir: Path,
    link_db: Path,
    refresh_csv: str = "",
    top: int = 10,
) -> dict[str, object]:
    label = project_label_from_root(root)
    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)
    xref = open_checked_xref(index_dir / f"{label}_xref.sqlite", root)
    lite = open_optional(index_dir / f"{label}.sqlite")
    link = open_optional(link_db)
    ctx = LintContext(
        root=root,
        rows=rows,
        comments=comments,
        xref=xref,
        lite=lite,
        link=link,
        project_label=label,
        refresh_csv=refresh_csv,
    )
    findings_by_check: dict[str, list[dict[str, object]]] = {}
    try:
        for name in DOCTOR_CHECKS:
            func = CHECKS.get(name, (None, ""))[0]
            if func is not None:
                findings_by_check[name] = func(ctx)
        findings_by_check["io-comment-gap"] = collect_io_comment_gaps(ctx)
        states = {name: ctx.states.get(name, checked()) for name in findings_by_check}
        return build_report(root, findings_by_check, states, top)
    finally:
        for con in (xref, lite, link):
            if isinstance(con, sqlite3.Connection):
                con.close()


def print_text(report: dict[str, object]) -> None:
    print(f"PROJECT HEALTH: {report['health']}  (heuristic maintainability view)")
    if report.get("provisional_health"):
        print(f"Provisional from evaluated checks: {report['provisional_health']}")
    print("")
    for name in DIMENSIONS:
        print(f"{name:<20} {int(report['scores'][name]):>3}/100")
    analysis = report["analysis"]
    print(
        f"\nAnalysis coverage: {analysis['evaluated']}/{analysis['total_checks']} checks evaluated"
        + (f"; inconclusive: {', '.join(analysis['inconclusive'])}" if analysis["inconclusive"] else "")
    )
    print(f"Total findings: {report['total_findings']}")
    print("\nTop risks")
    if not report["top_risks"]:
        print("  none from the evaluated checks")
        return
    for index, item in enumerate(report["top_risks"], start=1):
        print(f"{index:>2}. {str(item.get('severity') or 'info').upper():<8} [{item.get('check')}] {item.get('device') or '-'}")
        if item.get("detail"):
            print(f"    {item['detail']}")
        if item.get("locations"):
            print(f"    evidence: {item['locations']}")
        print(f"    why: {item['why_it_matters']}")
        print(f"    review: {item['next_review']}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project-wide maintainability Doctor for a GX Works3 project")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project root")
    parser.add_argument("--index-dir", default=".gx3_index", help="directory containing lite/xref indexes")
    parser.add_argument("--link-db", default=".gx3_index/link_map.sqlite", help="optional cross-project link-map DB")
    parser.add_argument("--refresh-csv", default="", help="optional communication refresh-area CSV")
    parser.add_argument("--top", type=int, default=10, help="number of ranked risks to show")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--out", default="", help="optional JSON report path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_project_root(args.root)
    report = collect_project_health(
        root,
        index_dir=Path(args.index_dir),
        link_db=Path(args.link_db),
        refresh_csv=args.refresh_csv,
        top=args.top,
    )
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
