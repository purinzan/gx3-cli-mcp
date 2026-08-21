from __future__ import annotations

"""Probe GX Works3 ConvertData containers.

This is a read-only reverse-engineering helper.  It documents the parts of
ConvertData that are now understood well enough to verify automatically:

* Program.qpg / PLCWriteProgram.qpg have a 12-byte wrapper header.
* The third u32 is the metadata byte length.
* The first u32 inside the metadata is a size field.  In most saves it is the
  appended ProgramFilePCode length; in some saves it is the full qpg size.
* ProgramFilePCode.pcode is appended verbatim at offset 12 + metadata_len.
* PouPCode.pcode records are length-prefixed records:
  u32 length, u8 tag 0x11, 16-byte little-endian GUID, payload...
  The GUIDs match LDDB LadderBlocks.id and StepInfo T_Block.BlockID.
"""

import argparse
import csv
import hashlib
import json
import sqlite3
import struct
import sys
import uuid
from collections import Counter
from pathlib import Path

from gx3cli.gx3_intermediate_tool import (
    find_stepinfo_map,
    normalize_guid,
    pcode_path_for_stepinfo,
    read_ladder_rows,
    read_stepinfo_blocks,
)
from gx3cli.gx3_program_map import load_program_map, qpg_pou_name_records
from gx3cli.gx3_project_paths import (
    default_output_prefix,
    default_project_root,
    iter_convertdata_entries,
    resolve_project_root,
)


def sha1_hex(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def read_text_decimal(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace").strip()


def parse_qpg(qpg: Path, root: Path) -> dict[str, object]:
    data = qpg.read_bytes()
    row: dict[str, object] = {
        "qpg": rel(qpg, root),
        "size": len(data),
        "header0_hex": "",
        "header1_hex": "",
        "metadata_len": "",
        "metadata_sha1": "",
        "metadata_size_field": "",
        "metadata_size_field_kind": "",
        "embedded_pcode_offset": "",
        "embedded_pcode_size": "",
        "programfile_pcode": "",
        "programfile_pcode_size": "",
        "programfile_pcode_sha1": "",
        "tail_sha1": "",
        "tail_matches_programfile": "",
        "size_field_matches_tail": "",
        "size_field_matches_qpg": "",
        "plcwrite_qpg": "",
        "plcwrite_qpg_matches": "",
        "pou_names": "",
        "status": "too_short",
    }
    if len(data) < 12:
        return row

    header0, header1, metadata_len = struct.unpack_from("<III", data, 0)
    pcode_offset = 12 + metadata_len
    row.update(
        {
            "header0_hex": f"0x{header0:08X}",
            "header1_hex": f"0x{header1:08X}",
            "metadata_len": metadata_len,
            "embedded_pcode_offset": pcode_offset,
            "status": "bounds_error" if pcode_offset > len(data) else "ok",
        }
    )
    if pcode_offset > len(data):
        return row

    metadata = data[12:pcode_offset]
    tail = data[pcode_offset:]
    size_field = struct.unpack_from("<I", metadata, 0)[0] if len(metadata) >= 4 else None
    size_kind = ""
    if size_field == len(tail):
        size_kind = "tail_pcode_size"
    elif size_field == len(data):
        size_kind = "qpg_total_size"
    elif size_field is not None:
        size_kind = "unknown"
    row.update(
        {
            "metadata_sha1": sha1_hex(metadata),
            "metadata_size_field": "" if size_field is None else size_field,
            "metadata_size_field_kind": size_kind,
            "embedded_pcode_size": len(tail),
            "tail_sha1": sha1_hex(tail),
            "size_field_matches_tail": "" if size_field is None else size_field == len(tail),
            "size_field_matches_qpg": "" if size_field is None else size_field == len(data),
            "pou_names": " / ".join(qpg_pou_name_records(data)),
        }
    )

    program_id = qpg.parent.name
    if "\\" in qpg.name:
        parts = qpg.name.split("\\")
        program_id = parts[1] if len(parts) == 3 else program_id
    sibling_paths = {
        entry.member_name: entry.path
        for entry in iter_convertdata_entries(root)
        if entry.program_id == program_id
    }

    program_pcode = sibling_paths.get("ProgramFilePCode.pcode", qpg.with_name("ProgramFilePCode.pcode"))
    if program_pcode.exists():
        pcode_data = program_pcode.read_bytes()
        row.update(
            {
                "programfile_pcode": rel(program_pcode, root),
                "programfile_pcode_size": len(pcode_data),
                "programfile_pcode_sha1": sha1_hex(pcode_data),
                "tail_matches_programfile": tail == pcode_data,
            }
        )

    plcwrite = sibling_paths.get("PLCWriteProgram.qpg", qpg.with_name("PLCWriteProgram.qpg"))
    if plcwrite.exists():
        row["plcwrite_qpg"] = rel(plcwrite, root)
        row["plcwrite_qpg_matches"] = plcwrite.read_bytes() == data
    return row


def parse_pou_pcode_records(path: Path, root: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    data = path.read_bytes()
    records: list[dict[str, object]] = []
    off = 0
    junk = 0
    while off + 21 <= len(data):
        length = struct.unpack_from("<I", data, off)[0]
        if 21 <= length <= len(data) - off and data[off + 4] == 0x11:
            try:
                guid = str(uuid.UUID(bytes_le=data[off + 5 : off + 21]))
            except ValueError:
                guid = ""
            if guid:
                payload = data[off + 21 : off + length]
                records.append(
                    {
                        "pcode": rel(path, root),
                        "record_index": len(records),
                        "offset": off,
                        "length": length,
                        "tag_hex": "0x11",
                        "guid": guid,
                        "payload_size": len(payload),
                        "payload_sha1": sha1_hex(payload),
                        "payload_head_hex": payload[:16].hex(" "),
                    }
                )
                off += length
                continue
        junk += 1
        off += 1

    summary = {
        "pcode": rel(path, root),
        "size": len(data),
        "record_count": len(records),
        "parsed_record_bytes": sum(int(r["length"]) for r in records),
        "junk_or_gap_bytes": junk + max(0, len(data) - off),
        "sha1": sha1_hex(data),
    }
    return records, summary


def read_stepinfo_record_map(root: Path, stepinfo: str) -> dict[str, dict[str, object]]:
    blocks, _steps = read_stepinfo_blocks(root, stepinfo)
    return {str(block["guid"]): block for block in blocks if block.get("guid")}


def count_rows(table_path: Path, query: str) -> int:
    if not table_path.exists():
        return 0
    con = sqlite3.connect(f"file:{table_path}?mode=ro", uri=True)
    try:
        row = con.execute(query).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.Error:
        return 0
    finally:
        con.close()


def build_probe(root: Path) -> dict[str, object]:
    qpg_rows = [parse_qpg(entry.path, root) for entry in iter_convertdata_entries(root, "Program.qpg")]

    ladder_rows = read_ladder_rows(root)
    stepinfo_by_lddb = find_stepinfo_map(root, ladder_rows)
    pm = load_program_map(root)

    pcode_rows: list[dict[str, object]] = []
    pcode_summaries: list[dict[str, object]] = []
    pcode_record_by_path_guid: dict[tuple[str, str], dict[str, object]] = {}
    for entry in iter_convertdata_entries(root, "PouPCode.pcode"):
        pcode = entry.path
        records, summary = parse_pou_pcode_records(pcode, root)
        pcode_rows.extend(records)
        pcode_summaries.append(summary)
        for rec in records:
            pcode_record_by_path_guid[(str(rec["pcode"]), str(rec["guid"]))] = rec

    join_rows: list[dict[str, object]] = []
    missing_pcode = 0
    missing_stepinfo = 0
    pcode_order_violations = 0
    matched_record_keys: set[tuple[str, str]] = set()
    for lddb, rows in sorted(ladder_rows.items()):
        stepinfo = stepinfo_by_lddb.get(lddb, "")
        step_map = read_stepinfo_record_map(root, stepinfo) if stepinfo else {}
        pcode_path = pcode_path_for_stepinfo(root, stepinfo) if stepinfo else Path()
        pcode_rel = rel(pcode_path, root) if stepinfo else ""
        previous_offset = -1
        hexid = lddb.split("_")[0]
        info = pm.pous.get(hexid)
        for row in sorted(rows, key=lambda r: (float(r["pos"]), str(r["id"]))):
            guid = normalize_guid(row.get("id"))
            if not guid:
                continue
            step_block = step_map.get(guid)
            record = pcode_record_by_path_guid.get((pcode_rel, guid))
            if step_block is None:
                missing_stepinfo += 1
            if record is None:
                missing_pcode += 1
            offset = int(record["offset"]) if record else -1
            order_ok = "" if record is None else offset >= previous_offset
            if record is not None:
                matched_record_keys.add((pcode_rel, guid))
                if offset < previous_offset:
                    pcode_order_violations += 1
                previous_offset = offset
            join_rows.append(
                {
                    "lddb": lddb,
                    "pou_name": info.name if info else "",
                    "program_file": info.program_file if info else "",
                    "pou_dir": info.pou_dir if info else "",
                    "program_dir": info.program_dir if info else "",
                    "pos": row["pos"],
                    "blocktype": row["blocktype"],
                    "guid": guid,
                    "stepinfo": stepinfo,
                    "stepinfo_pos": step_block.get("pos", "") if step_block else "",
                    "stepinfo_step_size": step_block.get("step_size", "") if step_block else "",
                    "pcode": pcode_rel,
                    "pcode_offset": offset if record else "",
                    "pcode_record_length": record.get("length", "") if record else "",
                    "pcode_payload_head_hex": record.get("payload_head_hex", "") if record else "",
                    "pcode_order_ok": order_ok,
                }
            )

    extra_records = [
        r
        for r in pcode_rows
        if (str(r["pcode"]), str(r["guid"])) not in matched_record_keys
    ]

    qpg_status = Counter(str(r["status"]) for r in qpg_rows)
    summary = {
        "root": str(root),
        "qpg_files": len(qpg_rows),
        "qpg_status": dict(qpg_status),
        "qpg_tail_matches_programfile_false": sum(1 for r in qpg_rows if r["tail_matches_programfile"] is False),
        "qpg_size_field_kind": dict(Counter(str(r["metadata_size_field_kind"]) for r in qpg_rows)),
        "qpg_size_field_unknown": sum(1 for r in qpg_rows if r["metadata_size_field_kind"] == "unknown"),
        "plcwrite_qpg_mismatch": sum(1 for r in qpg_rows if r["plcwrite_qpg_matches"] is False),
        "pou_pcode_files": len(pcode_summaries),
        "pou_pcode_records": len(pcode_rows),
        "lddb_rows_with_guid": len(join_rows),
        "missing_stepinfo_rows": missing_stepinfo,
        "missing_pou_pcode_rows": missing_pcode,
        "extra_pou_pcode_records": len(extra_records),
        "pcode_order_violations": pcode_order_violations,
        "lddb_files": len(ladder_rows),
        "stepinfo_links": len(stepinfo_by_lddb),
        "program_qpg_total_bytes": sum(int(r["size"]) for r in qpg_rows),
        "pou_pcode_total_bytes": sum(int(r["size"]) for r in pcode_summaries),
        "label": root.name.removeprefix("_extracted_"),
    }
    return {
        "summary": summary,
        "qpg_rows": qpg_rows,
        "pcode_rows": pcode_rows,
        "pcode_summaries": pcode_summaries,
        "join_rows": join_rows,
        "extra_pcode_rows": extra_records,
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
    parser = argparse.ArgumentParser(description="Probe GX Works3 ConvertData qpg/pcode layout.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    root = resolve_project_root(args.root)
    if not iter_convertdata_entries(root):
        raise SystemExit(f"ConvertData not found under: {root}")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or default_output_prefix("convertdata")

    result = build_probe(root)
    summary = result["summary"]
    base = out_dir / prefix
    (base.with_name(base.name + "_summary.json")).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    write_csv(base.with_name(base.name + "_qpg.csv"), result["qpg_rows"])
    write_csv(base.with_name(base.name + "_pou_pcode_summary.csv"), result["pcode_summaries"])
    write_csv(base.with_name(base.name + "_pou_pcode_records.csv"), result["pcode_rows"])
    write_csv(base.with_name(base.name + "_guid_join.csv"), result["join_rows"])
    write_csv(base.with_name(base.name + "_extra_pou_pcode_records.csv"), result["extra_pcode_rows"])

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
