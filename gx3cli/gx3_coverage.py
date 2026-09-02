from __future__ import annotations

"""Coverage reports for instruction and device knowledge used by the CLI.

This is not a manual parser yet. It answers the immediate question:
which instructions/devices appear in a project, which ones the current CLI can
classify, and which ones fall back to unknown/ref handling.
"""

import argparse
import csv
import re
from collections import Counter, defaultdict
from pathlib import Path

from gx3cli.extract_gx3_extended_instruction_knowledge import DEVICE_TYPES, KNOWN_OPS, header_tokens, parse_header_ops
from gx3cli.gx3_arg_decode import ARITH_OPS, COMPARE_RE, WRITE_ARG_TABLE, base_opcode, parse_row_occurrences
from gx3cli.gx3_intermediate_tool import read_ladder_rows
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.gx3_label_resolve import load_label_resolver


CONTACT_OPS = {"a", "b", "c", "EG"}


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def decoder_status(op: str) -> tuple[str, str]:
    base = base_opcode(op)
    if op in CONTACT_OPS:
        return "classified", "ladder contact/coil"
    if base in WRITE_ARG_TABLE:
        return "classified", "write-arg table"
    if base in ARITH_OPS:
        return "classified", "arithmetic table"
    if COMPARE_RE.match(base):
        return "classified", "compare regex"
    if op in KNOWN_OPS:
        return "known-header-only", "known op, but read/write table missing"
    return "unknown", "not in known op catalog"


def collect_instruction_rows(root: Path) -> list[dict[str, object]]:
    labels = load_label_resolver(root)
    counts: Counter[str] = Counter()
    ref_counts: Counter[str] = Counter()
    partial_counts: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for lddb, rows in read_ladder_rows(root).items():
        for raw in rows:
            if int(raw.get("blocktype", -1)) != 0:
                continue
            data = str(raw.get("data", ""))
            ops = parse_header_ops(data)
            _decoded, status = parse_row_occurrences(data, labels)
            for hop in ops:
                op = hop.op
                if op in CONTACT_OPS:
                    continue
                counts[op] += 1
                samples.setdefault(op, f"{lddb}:{int(float(raw['pos']))}")
                if status != "exact":
                    partial_counts[op] += 1
            for role, opcode, _occs, _consts in _decoded:
                op = opcode or role
                if op in CONTACT_OPS:
                    continue
                st, _why = decoder_status(op)
                if st != "classified":
                    ref_counts[op] += 1
    rows_out = []
    all_ops = sorted(counts)
    for op in all_ops:
        status, basis = decoder_status(op)
        rows_out.append(
            {
                "opcode": op,
                "base_opcode": base_opcode(op),
                "count": counts[op],
                "decoder_status": status,
                "basis": basis,
                "ref_or_unknown_rows": ref_counts[op],
                "partial_parse_rows": partial_counts[op],
                "sample": samples.get(op, ""),
            }
        )
    return rows_out


def collect_device_rows(root: Path) -> list[dict[str, object]]:
    labels = load_label_resolver(root)
    counts: Counter[str] = Counter()
    details: Counter[str] = Counter()
    samples: dict[str, str] = {}
    label_count = 0
    for lddb, rows in read_ladder_rows(root).items():
        for raw in rows:
            if int(raw.get("blocktype", -1)) != 0:
                continue
            data = str(raw.get("data", ""))
            tokens = header_tokens(data)
            for token in tokens:
                alias = {"Us": "U", "Zs": "Z"}.get(token, token)
                if alias in DEVICE_TYPES:
                    counts[alias] += 1
                    samples.setdefault(alias, f"{lddb}:{int(float(raw['pos']))}")
                elif token.startswith("_lid/"):
                    label_count += 1
            decoded, _status = parse_row_occurrences(data, labels)
            for _role, _opcode, occs, _consts in decoded:
                for occ in occs:
                    counts[occ.device_type] += 1
                    if occ.detail:
                        details[occ.device_type] += 1
                    samples.setdefault(occ.device_type, f"{lddb}:{int(float(raw['pos']))}")
    if label_count:
        counts["LABEL"] += label_count
    rows_out = []
    for dev_type in sorted(counts):
        known = dev_type in DEVICE_TYPES or dev_type in {"UG", "LABEL"}
        category = device_category(dev_type)
        rows_out.append(
            {
                "device_type": dev_type,
                "count": counts[dev_type],
                "cli_status": "known" if known else "unknown",
                "category": category,
                "indexed_or_special_count": details[dev_type],
                "sample": samples.get(dev_type, ""),
            }
        )
    return rows_out


def device_category(dev_type: str) -> str:
    if dev_type in {"X", "Y", "DX", "DY"}:
        return "io-bit"
    if dev_type in {"M", "L", "B", "SB", "SM", "S", "F", "V"}:
        return "internal/link/special bit"
    if dev_type in {"D", "W", "R", "ZR", "Z", "SW", "SD"}:
        return "word/register"
    if dev_type in {"T", "ST", "C"}:
        return "timer/counter"
    if dev_type in {"U", "G", "UG", "J"}:
        return "module/buffer"
    if dev_type == "LABEL":
        return "label reference"
    return "unknown"


def print_summary(rows: list[dict[str, object]], key: str, status_key: str) -> None:
    summary = Counter(str(row[status_key]) for row in rows)
    print(", ".join(f"{k}={summary[k]}" for k in sorted(summary)) or "none")
    for row in rows[:30]:
        print(
            f"{str(row[key]):<18} {str(row[status_key]):<18} "
            f"count={row.get('count')} basis={row.get('basis') or row.get('category')} sample={row.get('sample')}"
        )
    if len(rows) > 30:
        print(f"... {len(rows) - 30} more")


def cmd_instructions(args: argparse.Namespace) -> int:
    rows = collect_instruction_rows(Path(args.root))
    rows.sort(key=lambda r: ({"unknown": 0, "known-header-only": 1, "classified": 2}.get(str(r["decoder_status"]), 9), -int(r["count"]), str(r["opcode"])))
    if args.output:
        write_csv(Path(args.output), rows, ["opcode", "base_opcode", "count", "decoder_status", "basis", "ref_or_unknown_rows", "partial_parse_rows", "sample"])
        print(f"instruction coverage CSV: {args.output} rows={len(rows)}")
    print_summary(rows, "opcode", "decoder_status")
    return 0


def cmd_devices(args: argparse.Namespace) -> int:
    rows = collect_device_rows(Path(args.root))
    rows.sort(key=lambda r: (0 if r["cli_status"] == "unknown" else 1, str(r["device_type"])))
    if args.output:
        write_csv(Path(args.output), rows, ["device_type", "count", "cli_status", "category", "indexed_or_special_count", "sample"])
        print(f"device coverage CSV: {args.output} rows={len(rows)}")
    print_summary(rows, "device_type", "cli_status")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report what instruction/device knowledge the CLI covers.")
    parser.add_argument("--root", default=str(default_project_root()))
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("instructions", help="instruction opcode coverage")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_instructions)
    p = sub.add_parser("devices", help="device type coverage")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_devices)
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
