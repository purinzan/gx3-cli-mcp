from __future__ import annotations

"""One-page parser reliability report for sales and risk disclosure."""

import argparse
import json
from collections import Counter
from pathlib import Path

from gx3cli.analyze_gx3_intermediate_parse_gaps import ProjectInput, collect_project, project_label_from_root
from gx3cli.gx3_coverage import collect_device_rows, collect_instruction_rows
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.gx3_output import add_format_alias, fold_format_alias


def build_report(root: Path) -> dict[str, object]:
    label = project_label_from_root(root)
    gaps = collect_project(ProjectInput(label, root))
    instructions = collect_instruction_rows(root)
    devices = collect_device_rows(root)
    gap_reasons = Counter(str(row["likely_reason"]) for row in gaps)
    gap_priorities = Counter(str(row["priority"]) for row in gaps)
    unsupported = [row for row in instructions if row["decoder_status"] != "classified"]
    partial_rows = sum(int(row.get("partial_parse_rows", 0)) for row in instructions)
    instruction_count = sum(int(row.get("count", 0)) for row in instructions)
    gap_rate = (len(gaps) / instruction_count) if instruction_count else 0.0
    return {
        "project": label,
        "root": str(root),
        "instruction_rows": instruction_count,
        "gap_rows": len(gaps),
        "gap_rate": round(gap_rate, 6),
        "partial_parse_rows": partial_rows,
        "gap_reasons": dict(sorted(gap_reasons.items())),
        "gap_priorities": dict(sorted(gap_priorities.items())),
        "unsupported_instruction_count": len(unsupported),
        "unsupported_instructions": [
            {
                "opcode": row["opcode"],
                "count": row["count"],
                "status": row["decoder_status"],
                "partial_parse_rows": row["partial_parse_rows"],
            }
            for row in unsupported[:50]
        ],
        "device_types": [
            {
                "device_type": row["device_type"],
                "count": row["count"],
                "status": row["cli_status"],
                "category": row["category"],
            }
            for row in devices
        ],
        "policy": [
            "This report is a parser-confidence disclosure, not a safety certification.",
            "Unsupported or partial rows are preserved for read-only evidence but should not be marketed as fully decoded.",
            "Projects outside the verified GX Works3/device matrix must be disclosed as unverified.",
        ],
    }


def markdown_report(report: dict[str, object]) -> str:
    lines = [
        "# GX3 Parser Reliability Report",
        "",
        f"- Project: {report['project']}",
        f"- Instruction rows: {report['instruction_rows']}",
        f"- Gap rows: {report['gap_rows']}",
        f"- Gap rate: {report['gap_rate']}",
        f"- Partial parse rows: {report['partial_parse_rows']}",
        f"- Unsupported instruction count: {report['unsupported_instruction_count']}",
        "",
        "## Gap Priorities",
    ]
    priorities = report["gap_priorities"]
    if isinstance(priorities, dict) and priorities:
        for key, value in priorities.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- none")
    lines.extend(["", "## Gap Reasons"])
    reasons = report["gap_reasons"]
    if isinstance(reasons, dict) and reasons:
        for key, value in reasons.items():
            lines.append(f"- {key}: {value}")
    else:
        lines.append("- none")
    lines.extend(["", "## Unsupported Instructions"])
    unsupported = report["unsupported_instructions"]
    if isinstance(unsupported, list) and unsupported:
        for row in unsupported:
            lines.append(f"- {row['opcode']}: count={row['count']}, status={row['status']}, partial={row['partial_parse_rows']}")
    else:
        lines.append("- none")
    lines.extend(["", "## Policy"])
    for item in report["policy"]:
        lines.append(f"- {item}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Write a one-page parser reliability report.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder or .gx3")
    parser.add_argument("-o", "--output", default="gx3_reliability_report.md")
    parser.add_argument("--json", action="store_true", help="write JSON instead of Markdown")
    add_format_alias(parser)
    args = parser.parse_args(argv)
    fold_format_alias(args)
    root = resolve_project_root(args.root)
    report = build_report(root)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.json:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        out.write_text(markdown_report(report), encoding="utf-8")
    print(f"reliability report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
