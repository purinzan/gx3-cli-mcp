from __future__ import annotations

"""Probe ``*_MilDB.db`` rows and extract device references."""

import argparse
import csv
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

from gx3cli.gx3_device_name import device_radix, format_device as _format_device
from gx3cli.gx3_project_paths import default_output_prefix, default_project_root


DEVICE_TYPES = {
    "M", "SM", "L", "F", "V", "S", "X", "Y", "B", "SB",
    "D", "SD", "ZR", "W", "R", "SW", "T", "C", "ST", "Z",
    "J", "DX", "DY",
}
ROLE_TOKENS = {
    "A", "B", "L", "OUT", "RST", "SET", "MRD", "MPS", "MPP", "ANB", "ORB", "INV",
    "MOV", "FMOV", "NE", "EQ", "GE", "LE", "GT", "LT", "=", "<>", "<", ">", "<=", ">=",
    "OUTH", "BSET", "BKRST", "INC", "DEC", "BMOV", "INT2INT", "AddOpe", "SubOpe",
    "MulOpe", "DivOpe", "SUM", "WSUM", "LIMIT", "DABIN", "BINDA", "DATERD",
        "SEC2DATE", "DATE2SEC", "WTOB", "BTOW", "BKCMP_EQ", "AND",
}
TOKEN_RE = re.compile(r"(?:[A-Z][A-Za-z0-9_]*|K_-?\d+|[<>]=?|<>|=)")
D_ARG_RE = re.compile(r"d\{s=#:a=(-?\d+):vt=([A-Za-z0-9_]+)\}")
BIT_ARG_RE = re.compile(
    r"M\{b=d\{s=#:a=(-?\d+):vt=([A-Za-z0-9_]+)\}:m=c\{s=#:v=(-?\d+)(?::si=[A-Za-z]+)?\}\}"
)
STATEMENT_RE = re.compile(r"ma\{k=@FE/STATEMENT:ps=\[p\{k=TEXT:v=#\}:p\{k=TYPE:v=#\}\]\}")


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def device_name(dev_type: str, address: int, bit_number: int | None = None) -> str:
    if dev_type.endswith(".Dots"):
        base = dev_type.removesuffix(".Dots")
        suffix = f".{bit_number}" if bit_number is not None else ""
        return f"{base}{address}{suffix}"
    if device_radix(dev_type) == 16:
        return f"{dev_type}{address:X}"
    return _format_device(dev_type, address)


def header_tokens(data: str) -> list[str]:
    prefix = data.split(":ms{", 1)[0]
    parts = prefix.split(":")
    out: list[str] = []
    for token in reversed(parts):
        if TOKEN_RE.fullmatch(token):
            out.append(token)
            continue
        break
    return list(reversed(out))


def role_device_pairs(tokens: list[str]) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    previous_op = ""
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in ROLE_TOKENS and index + 1 < len(tokens) and tokens[index + 1] in DEVICE_TYPES:
            dev_type = tokens[index + 1]
            step = 2
            if index + 2 < len(tokens) and tokens[index + 2] == "Dots":
                dev_type = f"{dev_type}.Dots"
                step = 3
            pairs.append((token, dev_type))
            previous_op = token
            index += step
            continue
        if token in DEVICE_TYPES:
            dev_type = token
            step = 1
            if index + 1 < len(tokens) and tokens[index + 1] == "Dots":
                dev_type = f"{dev_type}.Dots"
                step = 2
            pairs.append((previous_op, dev_type))
            index += step
            continue
        elif not token.startswith("K_"):
            previous_op = token
        index += 1
    return pairs


def access_for_role(role: str) -> str:
    if role == "":
        return "read"
    if role in {
        "A", "B", "L", "MRD", "MPS", "MPP", "ANB", "ORB", "INV",
        "NE", "EQ", "GE", "LE", "GT", "LT", "=", "<>", "<", ">", "<=", ">=",
        "AND", "BKCMP_EQ", "H_1", "H_2", "Ks", "Zs", "Dots", "G", "N", "P",
    }:
        return "read"
    if role in {
        "OUT", "OUTH", "RST", "SET", "BSET", "BKRST", "MOV", "FMOV", "BMOV",
        "INC", "DEC", "INT2INT", "AddOpe", "SubOpe", "MulOpe", "DivOpe",
        "SUM", "WSUM", "LIMIT", "DABIN", "BINDA", "DATERD", "SEC2DATE", "DATE2SEC",
        "WTOB", "BTOW",
    }:
        return "write"
    return "unknown"


def extract_args(data: str) -> list[dict[str, object]]:
    bit_spans: list[tuple[int, int]] = []
    args: list[dict[str, object]] = []
    for match in BIT_ARG_RE.finditer(data):
        bit_spans.append(match.span())
        args.append(
            {
                "pos": match.start(),
                "address": int(match.group(1)),
                "vt": match.group(2),
                "bit_number": int(match.group(3)),
            }
        )
    for match in D_ARG_RE.finditer(data):
        start, end = match.span()
        if any(bit_start <= start and end <= bit_end for bit_start, bit_end in bit_spans):
            continue
        args.append(
            {
                "pos": start,
                "address": int(match.group(1)),
                "vt": match.group(2),
                "bit_number": None,
            }
        )
    return sorted(args, key=lambda row: int(row["pos"]))


def statement_text(data: str) -> str:
    marker = ":i:ms{"
    if marker not in data or "@FE/STATEMENT" not in data:
        return ""
    # Statement rows keep the visible text in the V1 header immediately before
    # ":i:ms{".  The fourth field is the text length; taking the last text field
    # is the least brittle representation for this report.
    prefix = data.split(marker, 1)[0]
    parts = prefix.split(":")
    return parts[-1] if parts else ""


def parse_mil_row(data: str) -> list[dict[str, object]]:
    tokens = header_tokens(data)
    pairs = role_device_pairs(tokens)
    args = extract_args(data)
    rows: list[dict[str, object]] = []
    for index, arg in enumerate(args):
        role, dev_type = pairs[index] if index < len(pairs) else ("", "")
        if not dev_type:
            continue
        address = int(arg["address"])
        bit_number = arg["bit_number"]
        rows.append(
            {
                "arg_index": index,
                "role": role,
                "device_type": dev_type,
                "device": device_name(dev_type, address, bit_number if isinstance(bit_number, int) else None) if dev_type else "",
                "address": address,
                "bit_number": "" if bit_number is None else bit_number,
                "vt": arg["vt"],
                "access": access_for_role(role),
            }
        )
    return rows


def probe_db(path: Path, root: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    row_rows: list[dict[str, object]] = []
    ref_rows: list[dict[str, object]] = []
    role_counts: Counter[str] = Counter()
    try:
        rows = con.execute("select id, pos, data, ConvTarget from MIL order by cast(pos as real), id").fetchall()
        for row in rows:
            data = row["data"] or ""
            tokens = header_tokens(data)
            roles = [role for role, _dev in role_device_pairs(tokens)]
            role_counts.update(r for r in roles if r)
            stmt = statement_text(data)
            parsed_refs = parse_mil_row(data)
            row_rows.append(
                {
                    "mildb": rel(path, root),
                    "id": row["id"],
                    "pos": row["pos"],
                    "conv_target": row["ConvTarget"],
                    "statement": stmt,
                    "header_tokens": " ".join(tokens),
                    "device_ref_count": len(parsed_refs),
                    "raw_head": data[:300],
                }
            )
            for ref in parsed_refs:
                ref_rows.append(
                    {
                        "mildb": rel(path, root),
                        "id": row["id"],
                        "pos": row["pos"],
                        **ref,
                    }
                )
    finally:
        con.close()
    summary = {
        "mildb": rel(path, root),
        "rows": len(row_rows),
        "device_refs": len(ref_rows),
        "read_refs": sum(1 for r in ref_rows if r["access"] == "read"),
        "write_refs": sum(1 for r in ref_rows if r["access"] == "write"),
        "unknown_access_refs": sum(1 for r in ref_rows if r["access"] == "unknown"),
        "role_counts": "; ".join(f"{k}:{v}" for k, v in role_counts.most_common()),
    }
    return row_rows, ref_rows, summary


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Extract *_MilDB.db row and device-reference evidence.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    dbs = sorted(root.glob("*_MilDB.db"))
    if not dbs:
        raise SystemExit(f"no *_MilDB.db files found under: {root}")
    all_rows: list[dict[str, object]] = []
    all_refs: list[dict[str, object]] = []
    summaries: list[dict[str, object]] = []
    for db in dbs:
        rows, refs, summary = probe_db(db, root)
        if rows or refs:
            all_rows.extend(rows)
            all_refs.extend(refs)
            summaries.append(summary)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or default_output_prefix("mildb")
    base = out_dir / prefix
    write_csv(base.with_name(base.name + "_summary.csv"), summaries)
    write_csv(base.with_name(base.name + "_rows.csv"), all_rows)
    write_csv(base.with_name(base.name + "_refs.csv"), all_refs)
    top = {
        "root": str(root),
        "mildb_files": len(dbs),
        "nonempty_mildb_files": len(summaries),
        "rows": len(all_rows),
        "device_refs": len(all_refs),
        "write_refs": sum(1 for r in all_refs if r["access"] == "write"),
        "unknown_access_refs": sum(1 for r in all_refs if r["access"] == "unknown"),
    }
    (base.with_name(base.name + "_summary.json")).write_text(
        json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(top, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
