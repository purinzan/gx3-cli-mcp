from __future__ import annotations

"""Probe RD77 Simple Motion ``*.iut`` containers.

The value payloads are still proprietary, but the container has enough stable
structure to expose useful evidence:

* Length-prefixed UTF-16 strings use a u32 character count including NUL.
* Every RD77MS16 file seen here has 53 unique ``DataName_\\XXXXXXXX_...``
  section identifiers, each appearing twice.
* Motion setting paths such as ``99999\\9000\\10000\\5000`` also appear twice.
  These paths are useful for diffing axis-specific positioning/block-start
  settings even before every binary value field is decoded.
"""

import argparse
import csv
import json
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

from gx3cli.gx3_project_paths import default_output_prefix, default_project_root


DATA_NAME_RE = re.compile(r"DataName_\\([0-9A-F]{8})_([0-9A-F]+)")
NUMERIC_PATH_RE = re.compile(r"\d+(?:\\\d+)+")
MODULE_RE = re.compile(r"RD77MS16(?:_[0-9A-F]+)?")
ASCII_RE = re.compile(r"[\x20-\x7e]+")


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def u32_at(data: bytes, off: int) -> int | None:
    if 0 <= off <= len(data) - 4:
        return struct.unpack_from("<I", data, off)[0]
    return None


def len_prefixed_strings(path: Path, root: Path) -> list[dict[str, object]]:
    data = path.read_bytes()
    rows: list[dict[str, object]] = []
    seen_offsets: set[int] = set()
    for off in range(0, len(data) - 8):
        if off in seen_offsets:
            continue
        count = u32_at(data, off)
        if count is None or not (2 <= count <= 120):
            continue
        end = off + 4 + count * 2
        if end > len(data):
            continue
        raw = data[off + 4 : end]
        if raw[-2:] != b"\x00\x00":
            continue
        try:
            text = raw[:-2].decode("utf-16le")
        except UnicodeDecodeError:
            continue
        if not ASCII_RE.fullmatch(text):
            continue

        category = "ascii_other"
        code = ""
        stamp = ""
        path_parts = ""
        axis_guess = ""
        path_kind = ""
        if (m := DATA_NAME_RE.fullmatch(text)):
            category = "data_name"
            code, stamp = m.groups()
        elif NUMERIC_PATH_RE.fullmatch(text):
            category = "numeric_path"
            parts = text.split("\\")
            path_parts = str(len(parts))
            axis_guess, path_kind = classify_numeric_path(parts)
        elif MODULE_RE.fullmatch(text):
            category = "module"
        else:
            category = "ascii_other"

        post_start = end
        pre_u32 = [u32_at(data, off - delta) for delta in (24, 20, 16, 12, 8, 4)]
        post_u32 = [u32_at(data, post_start + delta) for delta in (0, 4, 8, 12, 16, 20)]
        rows.append(
            {
                "iut": rel(path, root),
                "offset_hex": f"0x{off:06X}",
                "offset": off,
                "char_count_including_nul": count,
                "category": category,
                "text": text,
                "data_name_code": code,
                "data_name_stamp": stamp,
                "path_parts": path_parts,
                "axis_guess": axis_guess,
                "path_kind": path_kind,
                "pre_u32_m24": "" if pre_u32[0] is None else pre_u32[0],
                "pre_u32_m20": "" if pre_u32[1] is None else pre_u32[1],
                "pre_u32_m16": "" if pre_u32[2] is None else pre_u32[2],
                "pre_u32_m12": "" if pre_u32[3] is None else pre_u32[3],
                "pre_u32_m8": "" if pre_u32[4] is None else pre_u32[4],
                "pre_u32_m4": "" if pre_u32[5] is None else pre_u32[5],
                "post_u32_p0": "" if post_u32[0] is None else post_u32[0],
                "post_u32_p4": "" if post_u32[1] is None else post_u32[1],
                "post_u32_p8": "" if post_u32[2] is None else post_u32[2],
                "post_u32_p12": "" if post_u32[3] is None else post_u32[3],
                "post_u32_p16": "" if post_u32[4] is None else post_u32[4],
                "post_u32_p20": "" if post_u32[5] is None else post_u32[5],
                "post_hex_32": data[post_start : post_start + 32].hex(" "),
            }
        )
        seen_offsets.add(off)
    return rows


def classify_numeric_path(parts: list[str]) -> tuple[str, str]:
    axis_guess = ""
    kind = "numeric_path"
    if len(parts) == 4 and parts[0] == "99999" and parts[1] == "9000":
        try:
            value = int(parts[2])
        except ValueError:
            value = -1
        if value > 0 and value % 10000 == 0:
            axis_guess = str(value // 10000)
            kind = "axis_9000_table"
        else:
            kind = "9000_table"
    elif len(parts) == 5 and parts[0] == "99999":
        try:
            value = int(parts[1])
        except ValueError:
            value = -1
        if value > 0 and value % 10000 == 0:
            axis_guess = str(value // 10000)
            kind = "axis_nested_table"
        else:
            kind = "nested_table"
    elif len(parts) == 3:
        kind = "module_table"
    return axis_guess, kind


def add_occurrence_numbers(rows: list[dict[str, object]]) -> None:
    per_file_text: dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        key = (str(row["iut"]), str(row["text"]))
        per_file_text[key] += 1
        row["occurrence_for_text"] = per_file_text[key]


def build_probe(root: Path) -> dict[str, object]:
    all_rows: list[dict[str, object]] = []
    summary_rows: list[dict[str, object]] = []
    for path in sorted(root.glob("*.iut")):
        rows = len_prefixed_strings(path, root)
        add_occurrence_numbers(rows)
        all_rows.extend(rows)
        counts = Counter(str(r["category"]) for r in rows)
        unique_by_category: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            unique_by_category[str(row["category"])].add(str(row["text"]))
        module_ids = sorted(unique_by_category.get("module", set()))
        data_codes = {str(r["data_name_code"]) for r in rows if r["category"] == "data_name"}
        axes = {str(r["axis_guess"]) for r in rows if r["axis_guess"]}
        summary_rows.append(
            {
                "iut": rel(path, root),
                "size": path.stat().st_size,
                "module_ids": " / ".join(module_ids),
                "lenpref_strings": len(rows),
                "data_name_entries": counts.get("data_name", 0),
                "data_name_unique": len(data_codes),
                "numeric_path_entries": counts.get("numeric_path", 0),
                "numeric_path_unique": len(unique_by_category.get("numeric_path", set())),
                "ascii_other_entries": counts.get("ascii_other", 0),
                "axis_guess_count": len(axes),
                "axis_guess_min": min((int(a) for a in axes), default=""),
                "axis_guess_max": max((int(a) for a in axes), default=""),
            }
        )

    data_name_rows = [r for r in all_rows if r["category"] == "data_name"]
    numeric_path_rows = [r for r in all_rows if r["category"] == "numeric_path"]
    return {
        "summary_rows": summary_rows,
        "strings": all_rows,
        "data_names": data_name_rows,
        "numeric_paths": numeric_path_rows,
        "summary": {
            "root": str(root),
            "iut_files": len(summary_rows),
            "lenpref_strings": len(all_rows),
            "data_name_entries": len(data_name_rows),
            "data_name_unique_total": len({str(r["text"]) for r in data_name_rows}),
            "numeric_path_entries": len(numeric_path_rows),
            "numeric_path_unique_total": len({str(r["text"]) for r in numeric_path_rows}),
        },
    }


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
    parser = argparse.ArgumentParser(description="Probe RD77 *.iut motion setting containers.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    if not list(root.glob("*.iut")):
        raise SystemExit(f"no *.iut files found under: {root}")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or default_output_prefix("iut")
    base = out_dir / prefix

    result = build_probe(root)
    write_csv(base.with_name(base.name + "_summary.csv"), result["summary_rows"])
    write_csv(base.with_name(base.name + "_strings.csv"), result["strings"])
    write_csv(base.with_name(base.name + "_datanames.csv"), result["data_names"])
    write_csv(base.with_name(base.name + "_numeric_paths.csv"), result["numeric_paths"])
    (base.with_name(base.name + "_summary.json")).write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
