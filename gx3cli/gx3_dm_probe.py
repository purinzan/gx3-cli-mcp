from __future__ import annotations

"""Decode GX Works3 ``*_DM.db`` device memory initial/retained values."""

import argparse
import csv
import json
import sqlite3
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path

from gx3cli.gx3_device_name import device_radix, format_device as _format_device
from gx3cli.gx3_project_paths import default_output_prefix, default_project_root


DEVICE_CODE_NAMES = {
    1: "M",
    2: "SM",
    3: "L",
    4: "F",
    5: "V",
    8: "S",
    16: "X",
    17: "Y",
    20: "B",
    21: "SB",
    32: "D",
    33: "SD",
    35: "ZR",
    40: "W",
    48: "R",
    49: "SW",
    66: "T",
    70: "C",
    74: "ST",
    96: "Z",
}

INFERRED_DEVICE_CODES = {
    4: "low: inferred F from GX convention/comment-only rows",
    5: "low: inferred V from GX convention; no local comments",
    8: "low: inferred S from GX convention; no local comments",
    21: "medium: comment evidence indicates SB (special link relay)",
    70: "high: local comments identify counter",
    74: "high: local comments identify timer/ST",
    96: "medium: matches Z/index-register initial values",
}

BIT_DEVICE_TYPES = {"M", "SM", "L", "F", "V", "S", "X", "Y", "B", "SB"}


def device_code_confidence(code: int) -> str:
    if code in INFERRED_DEVICE_CODES:
        return INFERRED_DEVICE_CODES[code]
    if code in DEVICE_CODE_NAMES:
        return "known"
    return "unknown"


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def device_name(dev_type: str, number: int) -> str:
    if device_radix(dev_type) == 16:
        return f"{dev_type}{number:X}"
    return _format_device(dev_type, number)


def signed16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def load_comment_map(root: Path) -> dict[tuple[int, int, int], str]:
    comments: dict[tuple[int, int, int], str] = {}
    for db in sorted(root.glob("*_DC.db")):
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(
                """
                select d.DevCode, d.DevNoLow, coalesce(d.BitNo, 0) as BitNo, c.CmtData
                from DEVICE_DATA d
                join COMMENT_DATA c on c.DeviceSEQ = d.SEQ
                where coalesce(c.DelFlag, 0) = 0
                  and coalesce(c.CmtData, '') <> ''
                order by d.DevCode, d.DevNoLow, coalesce(d.BitNo, 0), c.CmtNo
                """
            )
            for row in rows:
                key = (int(row["DevCode"]), int(row["DevNoLow"]), int(row["BitNo"] or 0))
                comments.setdefault(key, str(row["CmtData"]))
        except sqlite3.Error:
            pass
        finally:
            con.close()
    return comments


def decode_memory_db(
    db: Path,
    root: Path,
    comments: dict[tuple[int, int, int], str],
    max_rows: int,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    value_rows: list[dict[str, object]] = []
    groups: dict[tuple[int, str], dict[str, object]] = {}
    try:
        rows = con.execute(
            "select MemorySEQ, DevCode, ExtCode, ExtNo, DevNo, MemSize, MemData from MEMORY_DATA order by MemorySEQ"
        )
        for row in rows:
            code = int(row["DevCode"])
            dev_type = DEVICE_CODE_NAMES.get(code, f"DEV{code}")
            mem = row["MemData"] or b""
            word_count = min(int(row["MemSize"] or 0), len(mem) // 2)
            base = int(row["DevNo"] or 0)
            key = (code, dev_type)
            group = groups.setdefault(
                key,
                {
                    "dm_db": rel(db, root),
                    "dev_code": code,
                    "device_type": dev_type,
                    "decode_mode": "bitmask_words" if dev_type in BIT_DEVICE_TYPES else "word_values",
                    "confidence": device_code_confidence(code),
                    "blocks": 0,
                    "storage_words": 0,
                    "nonzero_storage_words": 0,
                    "decoded_nonzero_devices": 0,
                    "first_device": "",
                    "last_device": "",
                    "sample_nonzero": "",
                },
            )
            group["blocks"] = int(group["blocks"]) + 1
            group["storage_words"] = int(group["storage_words"]) + word_count
            if word_count:
                first = device_name(dev_type, base if dev_type not in BIT_DEVICE_TYPES else base * 16)
                last_number = base + word_count - 1
                if dev_type in BIT_DEVICE_TYPES:
                    last_number = (base + word_count - 1) * 16 + 15
                last = device_name(dev_type, last_number)
                if not group["first_device"]:
                    group["first_device"] = first
                group["last_device"] = last

            for i in range(word_count):
                value = struct.unpack_from("<H", mem, i * 2)[0]
                if value == 0:
                    continue
                group["nonzero_storage_words"] = int(group["nonzero_storage_words"]) + 1
                if dev_type in BIT_DEVICE_TYPES:
                    word_base = (base + i) * 16
                    for bit in range(16):
                        if not (value & (1 << bit)):
                            continue
                        number = word_base + bit
                        device = device_name(dev_type, number)
                        group["decoded_nonzero_devices"] = int(group["decoded_nonzero_devices"]) + 1
                        if len(value_rows) < max_rows:
                            value_rows.append(
                                {
                                    "dm_db": rel(db, root),
                                    "memory_seq": row["MemorySEQ"],
                                    "dev_code": code,
                                    "device_type": dev_type,
                                    "device": device,
                                    "decode_mode": "bit_from_word_mask",
                                    "value_unsigned": 1,
                                    "value_signed": 1,
                                    "source_word_device": device_name(dev_type, base + i),
                                    "source_word_value_hex": f"0x{value:04X}",
                                    "comment": comments.get((code, number, 0), ""),
                                }
                            )
                else:
                    number = base + i
                    device = device_name(dev_type, number)
                    group["decoded_nonzero_devices"] = int(group["decoded_nonzero_devices"]) + 1
                    if len(value_rows) < max_rows:
                        value_rows.append(
                            {
                                "dm_db": rel(db, root),
                                "memory_seq": row["MemorySEQ"],
                                "dev_code": code,
                                "device_type": dev_type,
                                "device": device,
                                "decode_mode": "word_value",
                                "value_unsigned": value,
                                "value_signed": signed16(value),
                                "source_word_device": "",
                                "source_word_value_hex": "",
                                "comment": comments.get((code, number, 0), ""),
                            }
                        )
    finally:
        con.close()

    for group in groups.values():
        samples = [
            f"{r['device']}={r['value_unsigned']}"
            for r in value_rows
            if r["dm_db"] == group["dm_db"] and r["dev_code"] == group["dev_code"]
        ][:8]
        group["sample_nonzero"] = "; ".join(samples)
    return list(groups.values()), value_rows


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
    parser = argparse.ArgumentParser(description="Decode *_DM.db device memory values.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--max-value-rows", type=int, default=200000, help="cap rows in the nonzero value CSV")
    args = parser.parse_args(argv)

    root = Path(args.root)
    dbs = sorted(root.glob("*_DM.db"))
    if not dbs:
        raise SystemExit(f"no *_DM.db files found under: {root}")
    comments = load_comment_map(root)
    summaries: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    remaining = max(0, args.max_value_rows)
    for db in dbs:
        db_summary, db_values = decode_memory_db(db, root, comments, remaining)
        summaries.extend(db_summary)
        values.extend(db_values)
        remaining = max(0, args.max_value_rows - len(values))

    unknown_codes = sorted({int(row["dev_code"]) for row in summaries if str(row["device_type"]).startswith("DEV")})
    by_type = Counter(str(row["device_type"]) for row in summaries)
    top = {
        "root": str(root),
        "dm_dbs": len(dbs),
        "summary_rows": len(summaries),
        "nonzero_value_rows_written": len(values),
        "max_value_rows": args.max_value_rows,
        "unknown_dev_codes": unknown_codes,
        "device_types": dict(sorted(by_type.items())),
    }

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or default_output_prefix("dm")
    base = out_dir / prefix
    write_csv(base.with_name(base.name + "_summary.csv"), summaries)
    write_csv(base.with_name(base.name + "_nonzero_values.csv"), values)
    (base.with_name(base.name + "_summary.json")).write_text(
        json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(top, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
