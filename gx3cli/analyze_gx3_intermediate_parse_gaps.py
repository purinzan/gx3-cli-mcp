from __future__ import annotations

import argparse
import csv
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from gx3cli.extract_gx3_extended_instruction_knowledge import extract_elements, parse_header_ops
from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_intermediate_tool import read_ladder_rows
from gx3cli.gx3_program_map import load_program_map
from gx3cli.gx3_project_paths import default_output_path, default_project_root
from gx3cli.review_gx3_project import extract_title


OUT_CSV = default_output_path("ladder_intermediate_parse_gaps", "csv")
OUT_MATRIX = default_output_path("ladder_intermediate_parse_gap_matrix", "csv")
OUT_SUMMARY = default_output_path("ladder_intermediate_parse_gap_summary", "txt")

BIT_DEVICE_TYPES = {"X", "Y", "DX", "DY", "M", "L", "B", "SM", "SB", "T", "ST", "C", "F", "V", "S"}
WORD_DEVICE_TYPES = {"D", "W", "ZR", "R", "Z", "SW", "SD", "UG", "J"}
MODULE_OPS = {"GP.RIWT", "GP.RIRD"}
INDEXED_RE = re.compile(r"(?:[XMLBYMSFDWZR]+)\{")


@dataclass(frozen=True)
class ProjectInput:
    label: str
    root: Path


def project_label_from_root(root: Path) -> str:
    name = root.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"


def parse_project(value: str) -> ProjectInput:
    if "=" in value:
        label, root = value.split("=", 1)
        label = label.strip()
    else:
        root = value
        label = project_label_from_root(Path(root))
    if not label:
        raise argparse.ArgumentTypeError(f"empty project label: {value!r}")
    path = Path(root)
    if not path.exists():
        raise argparse.ArgumentTypeError(f"project root not found: {path}")
    return ProjectInput(label, path)


def reason_for(raw: str, delta: int, ops: list[str]) -> str:
    op_set = set(ops)
    if "$MOV" in raw and "String" in raw:
        return "string_literal_tokens_in_header"
    if op_set & MODULE_OPS or any(op in raw for op in MODULE_OPS):
        return "module_instruction_or_complex_argument"
    if "FOR" in op_set or "NEXT" in op_set:
        return "loop_control"
    if INDEXED_RE.search(raw):
        return "indexed_or_bit_range_argument"
    if delta > 0:
        return "more_header_ops_than_ce_elements"
    if delta < 0:
        return "more_ce_elements_than_header_ops"
    return "unknown"


def priority_for(reason: str, bit_devices: set[str], word_devices: set[str]) -> str:
    has_bit = bool(bit_devices)
    has_word = bool(word_devices)
    if reason == "module_instruction_or_complex_argument":
        return "P1_comm_boundary" if has_bit or has_word else "P2_comm_no_device"
    if reason == "indexed_or_bit_range_argument" and has_bit:
        return "P1_indexed_bit"
    if reason == "loop_control" and has_bit:
        return "P2_loop_with_bit"
    if has_bit:
        return "P2_trace_relevant_bit"
    if has_word:
        return "P3_word_only"
    return "P4_no_trace_device"


def compact_devices(devices: set[str], limit: int = 12) -> str:
    ordered = sorted(devices, key=lambda d: (re.sub(r"\d+$", "", d), int(re.search(r"-?\d+$", d).group(0)) if re.search(r"-?\d+$", d) else 0, d))
    shown = ordered[:limit]
    suffix = f" +{len(ordered) - limit}" if len(ordered) > limit else ""
    return " ".join(shown) + suffix


def row_devices(raw: str) -> tuple[set[str], set[str], int, str]:
    bit_devices: set[str] = set()
    word_devices: set[str] = set()
    all_devices: set[str] = set()
    _ops, status = parse_row_occurrences(raw)
    for _role, _opcode, occs, _consts in _ops:
        for occ in occs:
            if occ.device_type in {"?", ""}:
                continue
            all_devices.add(occ.device)
            if occ.device_type in BIT_DEVICE_TYPES:
                bit_devices.add(occ.device)
            elif occ.device_type in WORD_DEVICE_TYPES:
                word_devices.add(occ.device)
    return bit_devices, word_devices, len(all_devices), status


def op_sequence_for(raw: str) -> list[str]:
    sequence: list[str] = []
    for op in parse_header_ops(raw):
        if op.op in {"a", "b", "c"}:
            sequence.append(f"{op.op}:{op.device_type}")
        else:
            sequence.append(op.op)
    return sequence


def collect_project(project: ProjectInput) -> list[dict[str, object]]:
    rows_by_db = read_ladder_rows(project.root)
    program_map = load_program_map(project.root)
    out: list[dict[str, object]] = []

    for lddb, rows in rows_by_db.items():
        current_title = ""
        for row in rows:
            blocktype = int(row["blocktype"] or 0)
            raw = str(row["data"])
            if blocktype in {1, 2}:
                title = extract_title(raw)
                if title:
                    current_title = title
            if blocktype != 0:
                continue

            ce_count = len([e for e in extract_elements(raw) if "s=ce{" in e])
            ops = op_sequence_for(raw)
            delta = len(ops) - ce_count
            if delta == 0:
                continue

            bit_devices, word_devices, device_count, decoder_status = row_devices(raw)
            reason = reason_for(raw, delta, ops)
            priority = priority_for(reason, bit_devices, word_devices)
            pos = int(float(row["pos"]))
            out.append(
                {
                    "project": project.label,
                    "root": str(project.root),
                    "lddb": lddb,
                    "pou": program_map.label(lddb),
                    "step": program_map.step_of(lddb, pos) or "",
                    "pos": pos,
                    "block_id": row["id"],
                    "title": current_title,
                    "blocktype": blocktype,
                    "dim": extract_dim_from_raw(raw),
                    "rowsize": row["rowsize"],
                    "ce_count": ce_count,
                    "operation_count": len(ops),
                    "delta": delta,
                    "op_sequence": " ".join(ops),
                    "likely_reason": reason,
                    "priority": priority,
                    "trace_impact": "yes" if bit_devices else "no",
                    "bit_device_count": len(bit_devices),
                    "bit_devices": compact_devices(bit_devices),
                    "word_device_count": len(word_devices),
                    "word_devices": compact_devices(word_devices),
                    "device_count": device_count,
                    "decoder_status": decoder_status,
                    "raw_prefix": raw[:500],
                }
            )
    return out


def extract_dim_from_raw(raw: str) -> str:
    match = re.search(r":st\{[^{}]*?dim=([^:}]+)", raw)
    return match.group(1) if match else ""


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def matrix_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    samples: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for row in rows:
        key = (str(row["project"]), str(row["likely_reason"]), str(row["priority"]))
        rec = grouped.setdefault(
            key,
            {
                "project": key[0],
                "likely_reason": key[1],
                "priority": key[2],
                "gap_rows": 0,
                "trace_impact_rows": 0,
                "bit_device_rows": 0,
                "word_device_rows": 0,
                "total_bit_devices": 0,
                "total_word_devices": 0,
                "samples": "",
            },
        )
        rec["gap_rows"] = int(rec["gap_rows"]) + 1
        if row["trace_impact"] == "yes":
            rec["trace_impact_rows"] = int(rec["trace_impact_rows"]) + 1
        if int(row["bit_device_count"]):
            rec["bit_device_rows"] = int(rec["bit_device_rows"]) + 1
        if int(row["word_device_count"]):
            rec["word_device_rows"] = int(rec["word_device_rows"]) + 1
        rec["total_bit_devices"] = int(rec["total_bit_devices"]) + int(row["bit_device_count"])
        rec["total_word_devices"] = int(rec["total_word_devices"]) + int(row["word_device_count"])
        if len(samples[key]) < 5:
            samples[key].append(f"{row['pou']}:{row['step'] or row['pos']}:{row['pos']}")
    for key, rec in grouped.items():
        rec["samples"] = "; ".join(samples[key])
    return sorted(
        grouped.values(),
        key=lambda rec: (
            str(rec["project"]),
            -int(rec["trace_impact_rows"]),
            -int(rec["gap_rows"]),
            str(rec["likely_reason"]),
            str(rec["priority"]),
        ),
    )


def write_summary(path: Path, rows: list[dict[str, object]], matrix: list[dict[str, object]], projects: list[ProjectInput]) -> None:
    project_counts = Counter(str(row["project"]) for row in rows)
    reason_counts = Counter(str(row["likely_reason"]) for row in rows)
    priority_counts = Counter(str(row["priority"]) for row in rows)
    impact_count = sum(1 for row in rows if row["trace_impact"] == "yes")

    lines = [
        "Intermediate parse gap summary",
        "==============================",
        "",
        "Projects:",
    ]
    for project in projects:
        lines.append(f"  {project.label}: {project.root}")
    lines.extend(
        [
            "",
            f"gap_rows: {len(rows)}",
            f"trace_impact_rows: {impact_count}",
            "",
            "Likely reasons:",
        ]
    )
    for reason, count in reason_counts.most_common():
        lines.append(f"  {reason}: {count}")
    lines.append("")
    lines.append("Priority buckets:")
    for priority, count in priority_counts.most_common():
        lines.append(f"  {priority}: {count}")
    lines.append("")
    lines.append("Project totals:")
    for project, count in project_counts.most_common():
        impacted = sum(1 for row in rows if row["project"] == project and row["trace_impact"] == "yes")
        lines.append(f"  {project}: gaps={count}, trace_impact={impacted}")
    lines.append("")
    lines.append("Reason x project matrix:")
    for rec in matrix:
        lines.append(
            f"  {rec['project']} {rec['likely_reason']} {rec['priority']}: "
            f"gaps={rec['gap_rows']}, impact={rec['trace_impact_rows']}, samples={rec['samples']}"
        )
    lines.extend(
        [
            "",
            "Policy:",
            "  Gap rows remain safe for raw-preserved roundtrip.",
            "  Treat P1 rows as trace blind spots until the row reason is decoded.",
            "  Structured editing is still blocked for these rows; read-only xref/trace support can be added reason by reason.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Summarize ladder intermediate parse gaps and trace-impact priority.")
    parser.add_argument("--root", action="append", default=None, help="project root; repeatable")
    parser.add_argument("--project", action="append", default=None, type=parse_project, help="LABEL=ROOT; repeatable")
    parser.add_argument("--csv", default=str(OUT_CSV), help="gap row CSV output path")
    parser.add_argument("--matrix", default=str(OUT_MATRIX), help="reason/project matrix CSV output path")
    parser.add_argument("--summary", default=str(OUT_SUMMARY), help="text summary output path")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    projects: list[ProjectInput] = []
    if args.project:
        projects.extend(args.project)
    if args.root:
        projects.extend(parse_project(root) for root in args.root)
    if not projects:
        root = default_project_root()
        projects.append(ProjectInput(project_label_from_root(root), root))

    rows: list[dict[str, object]] = []
    for project in projects:
        rows.extend(collect_project(project))

    fields = [
        "project",
        "root",
        "lddb",
        "pou",
        "step",
        "pos",
        "block_id",
        "title",
        "blocktype",
        "dim",
        "rowsize",
        "ce_count",
        "operation_count",
        "delta",
        "op_sequence",
        "likely_reason",
        "priority",
        "trace_impact",
        "bit_device_count",
        "bit_devices",
        "word_device_count",
        "word_devices",
        "device_count",
        "decoder_status",
        "raw_prefix",
    ]
    matrix = matrix_rows(rows)
    write_csv(Path(args.csv), rows, fields)
    write_csv(
        Path(args.matrix),
        matrix,
        [
            "project",
            "likely_reason",
            "priority",
            "gap_rows",
            "trace_impact_rows",
            "bit_device_rows",
            "word_device_rows",
            "total_bit_devices",
            "total_word_devices",
            "samples",
        ],
    )
    write_summary(Path(args.summary), rows, matrix, projects)
    print(f"Wrote {args.csv} ({len(rows)} rows)")
    print(f"Wrote {args.matrix} ({len(matrix)} rows)")
    print(f"Wrote {args.summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
