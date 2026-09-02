#!/usr/bin/env python3
"""
Extract devices used in GX Works3/GX3 LadderBlocks and list devices that have no
non-empty device comment in the project device-comment DB.

Device occurrences come from the shared decoder (gx3_arg_decode), the same one
the cross-reference and the lite index use. This module used to pair the header
type tokens with the d{} numbers by position instead, which quietly went wrong
whenever an operand carried a modifier: on one real project it reported an M80
the rung does not contain and dropped the K4M49000 it does, on a row it called
"exact". The count comparison is kept as a diagnostic -- rows where it
disagrees are still written to the mismatch CSV for review -- but it no longer
decides what the report says.
"""

from __future__ import annotations

import csv
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_device_name import format_device
from gx3cli.gx3_project_paths import default_output_path, default_project_root, find_comment_db

ROOT = default_project_root()
COMMENT_DB = find_comment_db(ROOT) or ROOT / "_comments_DC.db"

OUT_MISSING = default_output_path("used_devices_without_comments", "csv")
OUT_MISSING_EXACT = default_output_path("used_devices_without_comments_exact", "csv")
OUT_SUMMARY = default_output_path("used_devices_without_comments_summary", "txt")
OUT_MISMATCH = default_output_path("device_parse_mismatch_rows", "csv")


# Confirmed GX Works3 device code values used by the project comment DB.
DEVICE_CODE_BY_TYPE = {
    "M": 1,
    "SM": 2,
    "L": 3,
    "X": 16,
    "Y": 17,
    "B": 20,
    "D": 32,
    "SD": 33,
    "ZR": 35,
    "W": 40,
    "R": 48,
    "SW": 49,
    "T": 66,
}

# Tokens in the V1 header that consume a d{s=#:a=...} operand but are not
# ordinary commentable devices in this report.
NON_REPORT_D_OPERANDS = {"Zs", "Ats", "Ks", "N", "Z", "G"}
D_OPERAND_TYPES = set(DEVICE_CODE_BY_TYPE) | NON_REPORT_D_OPERANDS

B_OPERAND_RE = re.compile(
    r"B\{b=d\{s=#:a=(-?\d+):vt=nn\}:e=d\{s=#:a=(-?\d+):vt=nn\}:vt=([A-Za-z]+)\}"
)
D_OPERAND_RE = re.compile(r"d\{s=#:a=(-?\d+):vt=nn\}")
INT_TOKEN_RE = re.compile(r"-?\d+")
TITLE_RE = re.compile(r"^V1:\d+:\d+:(.*?):st\{")


@dataclass
class Sample:
    lddb: str
    pos: int
    block_id: str
    title: str
    parse_status: str


@dataclass
class Usage:
    device_type: str
    number: int
    count: int = 0
    exact_count: int = 0
    partial_count: int = 0
    lddbs: set[str] = field(default_factory=set)
    samples: list[Sample] = field(default_factory=list)

    @property
    def device(self) -> str:
        # X, Y, B and W are numbered in hexadecimal. Spelling them here instead
        # of asking gx3_device_name put W132 in this report as "W306", a name
        # no other output uses and no engineer would search for.
        return format_device(self.device_type, self.number)

    @property
    def confidence(self) -> str:
        if self.partial_count and self.exact_count:
            return "mixed"
        if self.partial_count:
            return "partial"
        return "exact"


def open_sqlite(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path)


def extract_title(data: str) -> str:
    m = TITLE_RE.search(data)
    if not m:
        return ""
    return m.group(1).strip()


def parse_operand_numbers(data: str) -> list[int]:
    """Return d-operand numbers in data order.

    B{b=d...:e=d...} contains two d{} tokens for one bit-address operand.
    For alignment with the header operand type list, keep only the base number.
    """
    out: list[int] = []
    i = 0
    n = len(data)
    while i < n:
        mb = B_OPERAND_RE.match(data, i)
        if mb:
            out.append(int(mb.group(1)))
            i = mb.end()
            continue
        md = D_OPERAND_RE.match(data, i)
        if md:
            out.append(int(md.group(1)))
            i = md.end()
            continue
        i += 1
    return out


def parse_header_operand_types(data: str) -> list[str]:
    if ":cb{" not in data:
        return []
    prefix = data.split(":cb{", 1)[0]
    tokens = prefix.split(":")[1:]  # drop V1
    return [t for t in tokens if not INT_TOKEN_RE.fullmatch(t) and t in D_OPERAND_TYPES]


def load_comments() -> tuple[dict[tuple[str, int], bool], dict[tuple[str, int], str]]:
    """Return device-existence and comment map keyed by (device_type, number)."""
    con = open_sqlite(COMMENT_DB)
    cur = con.cursor()

    has_device: dict[tuple[str, int], bool] = {}
    comments: dict[tuple[str, int], list[str]] = defaultdict(list)

    for dev_type, dev_code in DEVICE_CODE_BY_TYPE.items():
        device_rows = cur.execute(
            "select SEQ, DevNoLow from DEVICE_DATA where DevCode=?",
            (dev_code,),
        ).fetchall()
        for seq, dev_no in device_rows:
            key = (dev_type, int(dev_no))
            has_device[key] = True
            rows = cur.execute(
                """
                select CmtData
                from COMMENT_DATA
                where DeviceSEQ=?
                  and coalesce(DelFlag, 0)=0
                  and trim(coalesce(CmtData, ''))<>''
                order by CmtNo
                """,
                (seq,),
            ).fetchall()
            for (text,) in rows:
                s = str(text).strip()
                if s and s not in comments[key]:
                    comments[key].append(s)

    con.close()
    return has_device, {k: " / ".join(v) for k, v in comments.items()}


def row_devices(operations: list) -> list[tuple[str, int]]:
    """Every device the decoder found in a row, in row order with repeats."""
    out: list[tuple[str, int]] = []
    for _role, _opcode, args, _consts in operations:
        for occ in args:
            if occ.device_type and occ.number is not None:
                out.append((occ.device_type, int(occ.number)))
    return out


def collect_usage() -> tuple[dict[tuple[str, int], Usage], list[dict[str, object]], Counter]:
    usage: dict[tuple[str, int], Usage] = {}
    mismatches: list[dict[str, object]] = []
    stats = Counter()

    for db_path in sorted(ROOT.glob("*_LDDB.db")):
        con = open_sqlite(db_path)
        cur = con.cursor()
        rows = cur.execute(
            "select id, pos, blocktype, data from LadderBlocks order by pos"
        ).fetchall()
        con.close()

        last_title = ""
        for block_id, pos, blocktype, data in rows:
            pos_i = int(pos)
            if blocktype in (1, 2):
                title = extract_title(data)
                if title:
                    last_title = title
                continue
            if blocktype != 0 or ":cb{" not in data:
                continue

            stats["ladder_rows"] += 1
            types = parse_header_operand_types(data)
            numbers = parse_operand_numbers(data)
            try:
                operations, decode_status = parse_row_occurrences(data)
            except Exception:
                operations, decode_status = [], "partial"
            # The counts are a diagnostic, not the answer: they disagree on
            # rows the decoder reads correctly (a constant and a digit
            # designation each spend tokens without spending a number).
            parse_status = "exact" if len(types) == len(numbers) else "partial"
            if decode_status != "exact":
                parse_status = "partial"
            if parse_status == "exact":
                stats["exact_rows"] += 1
            else:
                stats["partial_rows"] += 1
                mismatches.append(
                    {
                        "lddb": db_path.name,
                        "pos": pos_i,
                        "block_id": block_id,
                        "type_count": len(types),
                        "number_count": len(numbers),
                        "title": last_title,
                        "header_operand_types": " ".join(types),
                        "data_head": data[:500],
                    }
                )

            # In partial rows, zipping is a best-effort sample. Mismatch rows are
            # separately reported so they can be manually checked before using
            # those rows as authoritative.
            for dev_type, number in row_devices(operations):
                if dev_type not in DEVICE_CODE_BY_TYPE:
                    continue
                key = (dev_type, int(number))
                rec = usage.setdefault(key, Usage(dev_type, int(number)))
                rec.count += 1
                if parse_status == "exact":
                    rec.exact_count += 1
                else:
                    rec.partial_count += 1
                rec.lddbs.add(db_path.name)
                if len(rec.samples) < 3:
                    rec.samples.append(
                        Sample(
                            lddb=db_path.name,
                            pos=pos_i,
                            block_id=str(block_id),
                            title=last_title,
                            parse_status=parse_status,
                        )
                    )

    return usage, mismatches, stats


def write_outputs() -> None:
    has_device, comments = load_comments()
    usage, mismatches, stats = collect_usage()

    missing_rows = []
    for key, rec in usage.items():
        if comments.get(key, ""):
            continue
        status = "comment_blank" if has_device.get(key) else "device_row_missing"
        sample = rec.samples[0] if rec.samples else Sample("", 0, "", "", "")
        missing_rows.append(
            {
                "device": rec.device,
                "device_type": rec.device_type,
                "number": rec.number,
                "comment_status": status,
                "parse_confidence": rec.confidence,
                "occurrence_count": rec.count,
                "exact_occurrences": rec.exact_count,
                "partial_occurrences": rec.partial_count,
                "lddb_count": len(rec.lddbs),
                "first_lddb": sample.lddb,
                "first_pos": sample.pos,
                "first_block_id": sample.block_id,
                "first_title": sample.title,
            }
        )

    missing_rows.sort(key=lambda r: (r["device_type"], int(r["number"])))

    with OUT_MISSING.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "device",
                "device_type",
                "number",
                "comment_status",
                "parse_confidence",
                "occurrence_count",
                "exact_occurrences",
                "partial_occurrences",
                "lddb_count",
                "first_lddb",
                "first_pos",
                "first_block_id",
                "first_title",
            ],
        )
        writer.writeheader()
        writer.writerows(missing_rows)

    exact_missing_rows = [r for r in missing_rows if r["parse_confidence"] == "exact"]
    with OUT_MISSING_EXACT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "device",
                "device_type",
                "number",
                "comment_status",
                "parse_confidence",
                "occurrence_count",
                "exact_occurrences",
                "partial_occurrences",
                "lddb_count",
                "first_lddb",
                "first_pos",
                "first_block_id",
                "first_title",
            ],
        )
        writer.writeheader()
        writer.writerows(exact_missing_rows)

    with OUT_MISMATCH.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "lddb",
                "pos",
                "block_id",
                "type_count",
                "number_count",
                "title",
                "header_operand_types",
                "data_head",
            ],
        )
        writer.writeheader()
        writer.writerows(mismatches)

    by_type_used = Counter(k[0] for k in usage)
    by_type_missing = Counter(r["device_type"] for r in missing_rows)
    by_status = Counter(r["comment_status"] for r in missing_rows)
    by_conf = Counter(r["parse_confidence"] for r in missing_rows)

    lines = [
        "Used devices without comments - summary",
        "=======================================",
        "",
        f"Source folder: {ROOT}",
        f"Comment DB: {COMMENT_DB}",
        f"Missing-comment CSV: {OUT_MISSING}",
        f"Exact-only missing-comment CSV: {OUT_MISSING_EXACT}",
        f"Mismatch rows CSV: {OUT_MISMATCH}",
        "",
        "Parse stats:",
        f"  ladder_rows: {stats['ladder_rows']}",
        f"  exact_rows: {stats['exact_rows']}",
        f"  partial_rows: {stats['partial_rows']}",
        "",
        "Device code map used:",
    ]
    for dev_type, code in DEVICE_CODE_BY_TYPE.items():
        lines.append(f"  {dev_type}: DevCode {code}")

    lines.extend(["", "Unique used devices by type:"])
    for dev_type, count in sorted(by_type_used.items()):
        lines.append(f"  {dev_type}: {count}")

    lines.extend(["", "Devices without comments by type:"])
    for dev_type, count in sorted(by_type_missing.items()):
        lines.append(f"  {dev_type}: {count}")

    lines.extend(["", "Missing-comment status:"])
    for status, count in sorted(by_status.items()):
        lines.append(f"  {status}: {count}")

    lines.extend(["", "Parse confidence on missing-comment devices:"])
    for status, count in sorted(by_conf.items()):
        lines.append(f"  {status}: {count}")

    lines.extend(
        [
            "",
            "Notes:",
            "  comment_blank means DEVICE_DATA has a row, but no non-empty COMMENT_DATA was found.",
            "  device_row_missing means the used device was not found in DEVICE_DATA for the mapped device type.",
            "  partial confidence means at least one occurrence came from a LadderBlocks row whose header operand count",
            "  did not exactly match parsed operand numbers; check the mismatch CSV before using those rows as final.",
        ]
    )

    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_MISSING} ({len(missing_rows)} rows)")
    print(f"Wrote {OUT_MISSING_EXACT} ({len(exact_missing_rows)} rows)")
    print(f"Wrote {OUT_SUMMARY}")
    print(f"Wrote {OUT_MISMATCH} ({len(mismatches)} rows)")


if __name__ == "__main__":
    write_outputs()
