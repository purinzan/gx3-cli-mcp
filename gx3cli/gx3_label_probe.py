from __future__ import annotations

"""Extract GX Works3 LabelData.db and SourceInfo structure metadata."""

import argparse
import csv
import json
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from pathlib import Path

from gx3cli.gx3_project_paths import default_output_prefix, default_project_root
from gx3cli.gx3_tools import parse_inner_container, parse_sourceinfo_outer, text_of_payload


def read_sourceinfo_structs(root: Path) -> dict[str, dict[str, str]]:
    cab = root / "SourceInfo.CAB"
    result: dict[str, dict[str, str]] = {}
    if not cab.exists():
        return result
    for entry_name, payload in parse_sourceinfo_outer(cab):
        if "StructDefine" not in entry_name:
            continue
        for inner_name, body in parse_inner_container(payload):
            if not inner_name.endswith(".Xml"):
                continue
            label_id = inner_name.removesuffix(".Xml")
            text = text_of_payload(body)
            try:
                elem = ET.fromstring(text)
            except ET.ParseError:
                continue
            result[label_id] = {
                "sourceinfo_xml": inner_name,
                "structure_name": elem.attrib.get("Name", ""),
                "structure_title": elem.attrib.get("ProjDataTitle", ""),
                "structure_comment": elem.attrib.get("ProjDataComment", ""),
                "traceability_id": elem.attrib.get("TraceabilityId", ""),
                "modified_date": elem.attrib.get("ModifiedDate", ""),
            }
    return result


def open_label_db(root: Path) -> sqlite3.Connection:
    path = root / "LabelData.db"
    if not path.exists():
        raise SystemExit(f"LabelData.db not found: {path}")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("select 1 from sqlite_master where type='table' and name=?", (table,)).fetchone() is not None


def column_values(con: sqlite3.Connection) -> dict[tuple[str, int], dict[int, dict[str, object]]]:
    values: dict[tuple[str, int], dict[int, dict[str, object]]] = defaultdict(dict)
    if not table_exists(con, "ColumnDataTbl"):
        return values
    for row in con.execute("select LabelID, RowID, ColumnID, ColumnStrValue, ColumnIntValue from ColumnDataTbl"):
        values[(str(row["LabelID"]), int(row["RowID"]))][int(row["ColumnID"])] = {
            "str": row["ColumnStrValue"] or "",
            "int": row["ColumnIntValue"],
        }
    return values


def row_name(cols: dict[int, dict[str, object]]) -> str:
    return str(cols.get(2, {}).get("str", ""))


def build_probe(root: Path) -> dict[str, object]:
    structs = read_sourceinfo_structs(root)
    con = open_label_db(root)
    cols = column_values(con)

    label_rows: list[dict[str, object]] = []
    labels: dict[str, dict[str, object]] = {}
    if table_exists(con, "LabelTbl"):
        for row in con.execute("select LabelID, LabelTypeID from LabelTbl order by LabelID"):
            label_id = str(row["LabelID"])
            labels[label_id] = {
                "label_id": label_id,
                "label_type_id": row["LabelTypeID"],
                **structs.get(label_id, {}),
            }

    if table_exists(con, "RowTbl"):
        for row in con.execute("select LabelID, RowID, RowNo, VarType, BitCount, StateFlag, CheckerState from RowTbl order by LabelID, RowNo, RowID"):
            label_id = str(row["LabelID"])
            c = cols.get((label_id, int(row["RowID"])), {})
            meta = structs.get(label_id, {})
            label_rows.append(
                {
                    "label_id": label_id,
                    "structure_name": meta.get("structure_name", ""),
                    "structure_title": meta.get("structure_title", ""),
                    "row_id": row["RowID"],
                    "row_no": row["RowNo"],
                    "row_name": row_name(c),
                    "var_type": row["VarType"],
                    "bit_count": row["BitCount"],
                    "state_flag": row["StateFlag"],
                    "checker_state": row["CheckerState"],
                    "column3_str": c.get(3, {}).get("str", ""),
                    "column3_int": c.get(3, {}).get("int", ""),
                    "column6_str": c.get(6, {}).get("str", ""),
                    "column6_int": c.get(6, {}).get("int", ""),
                }
            )

    row_lookup = {
        (str(r["label_id"]), int(r["row_id"])): r
        for r in label_rows
    }

    comment_rows: list[dict[str, object]] = []
    if table_exists(con, "CommentDataTbl"):
        for row in con.execute(
            "select LabelID, RowID, ColumnID, ArrayOffset, BitPosition, CommentStrValue from CommentDataTbl order by LabelID, RowID, ColumnID, ArrayOffset, BitPosition"
        ):
            label_id = str(row["LabelID"])
            row_id = int(row["RowID"])
            base = row_lookup.get((label_id, row_id), {})
            comment_rows.append(
                {
                    "label_id": label_id,
                    "structure_name": base.get("structure_name", structs.get(label_id, {}).get("structure_name", "")),
                    "structure_title": base.get("structure_title", structs.get(label_id, {}).get("structure_title", "")),
                    "row_id": row_id,
                    "row_name": base.get("row_name", ""),
                    "column_id": row["ColumnID"],
                    "array_offset": row["ArrayOffset"],
                    "bit_position": row["BitPosition"],
                    "comment": row["CommentStrValue"],
                }
            )

    assign_rows: list[dict[str, object]] = []
    if table_exists(con, "DeviceAssignTbl"):
        for row in con.execute(
            """
            select LabelID, RowID, RelatedLabelID, RelatedRowID, InstanceID,
                   MELSECDevice, IECAddress, InitialValue, Offset, UseBit,
                   BaseRegister, BaseRegisterNo, BaseRegisterOffset,
                   BaseRegisterBitPosition, AliasInstanceID, ManualSet
            from DeviceAssignTbl
            order by LabelID, RowID, RelatedLabelID, RelatedRowID, InstanceID
            """
        ):
            label_id = str(row["LabelID"])
            related_label_id = str(row["RelatedLabelID"])
            related_row_id = int(row["RelatedRowID"])
            related = row_lookup.get((related_label_id, related_row_id), {})
            owner = row_lookup.get((label_id, int(row["RowID"])), {})
            assign_rows.append(
                {
                    "label_id": label_id,
                    "row_id": row["RowID"],
                    "owner_structure": owner.get("structure_name", structs.get(label_id, {}).get("structure_name", "")),
                    "owner_row_name": owner.get("row_name", ""),
                    "related_label_id": related_label_id,
                    "related_row_id": related_row_id,
                    "related_structure": related.get("structure_name", structs.get(related_label_id, {}).get("structure_name", "")),
                    "related_row_name": related.get("row_name", ""),
                    "instance_id": row["InstanceID"],
                    "melsec_device": row["MELSECDevice"],
                    "iec_address": row["IECAddress"],
                    "initial_value": row["InitialValue"],
                    "offset": row["Offset"],
                    "use_bit": row["UseBit"],
                    "base_register": row["BaseRegister"],
                    "base_register_no": row["BaseRegisterNo"],
                    "base_register_offset": row["BaseRegisterOffset"],
                    "base_register_bit_position": row["BaseRegisterBitPosition"],
                    "alias_instance_id": row["AliasInstanceID"],
                    "manual_set": row["ManualSet"],
                }
            )

    array_rows: list[dict[str, object]] = []
    if table_exists(con, "ArrayInfoTbl"):
        for row in con.execute("select LabelID, RowID, ArrayNo, ArrayCount, StartNo from ArrayInfoTbl order by LabelID, RowID, ArrayNo"):
            label_id = str(row["LabelID"])
            base = row_lookup.get((label_id, int(row["RowID"])), {})
            array_rows.append(
                {
                    "label_id": label_id,
                    "structure_name": base.get("structure_name", structs.get(label_id, {}).get("structure_name", "")),
                    "row_id": row["RowID"],
                    "row_name": base.get("row_name", ""),
                    "array_no": row["ArrayNo"],
                    "array_count": row["ArrayCount"],
                    "start_no": row["StartNo"],
                }
            )
    con.close()

    summary_rows = []
    row_counts = Counter(str(r["label_id"]) for r in label_rows)
    comment_counts = Counter(str(r["label_id"]) for r in comment_rows)
    assign_counts = Counter(str(r["related_label_id"]) for r in assign_rows)
    for label_id, label in sorted(labels.items()):
        summary_rows.append(
            {
                **label,
                "row_count": row_counts.get(label_id, 0),
                "comment_count": comment_counts.get(label_id, 0),
                "device_assign_count": assign_counts.get(label_id, 0),
            }
        )

    return {
        "summary_rows": summary_rows,
        "label_rows": label_rows,
        "comment_rows": comment_rows,
        "assign_rows": assign_rows,
        "array_rows": array_rows,
        "summary": {
            "root": str(root),
            "labels": len(summary_rows),
            "label_rows": len(label_rows),
            "label_comments": len(comment_rows),
            "device_assignments": len(assign_rows),
            "array_rows": len(array_rows),
            "sourceinfo_structs": len(structs),
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
    parser = argparse.ArgumentParser(description="Extract LabelData.db rows, comments, arrays, and device assignments.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    result = build_probe(root)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or default_output_prefix("labels")
    base = out_dir / prefix
    write_csv(base.with_name(base.name + "_summary.csv"), result["summary_rows"])
    write_csv(base.with_name(base.name + "_rows.csv"), result["label_rows"])
    write_csv(base.with_name(base.name + "_comments.csv"), result["comment_rows"])
    write_csv(base.with_name(base.name + "_device_assignments.csv"), result["assign_rows"])
    write_csv(base.with_name(base.name + "_arrays.csv"), result["array_rows"])
    (base.with_name(base.name + "_summary.json")).write_text(
        json.dumps(result["summary"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
