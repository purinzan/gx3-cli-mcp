from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from gx3cli.extract_comm_refresh_areas import iter_utf16_strings_any_alignment
from gx3cli.gx3_external_inputs import (
    collect_external_inputs,
    load_refresh_areas,
    load_unit_io_areas,
)
from gx3cli.gx3_project_paths import default_comm_prefix, default_output_prefix, default_project_root
from gx3cli.gx3_device_name import device_radix, format_device
from gx3cli.gx3_arg_decode import DecodedOperation, parse_row_operations
from gx3cli.review_gx3_project import (
    CommentInfo,
    LadderRow,
    comment_for_device,
    load_comments_for_root,
    load_rows,
)


AJ65_RE = re.compile(r"^AJ65")
CONST_SIGN_RE = re.compile(r"si=([^:}]+)")


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def device_role_from_comment(comment: str) -> str:
    if "局番" in comment:
        return "station_no"
    if "ｱｸｾｽｺｰﾄﾞ" in comment or "アクセスコード" in comment:
        return "access_attribute_code"
    if "ﾊﾞｯﾌｧｱﾄﾞﾚｽ" in comment or "バッファアドレス" in comment:
        return "buffer_address"
    if "書込/読込点数" in comment:
        return "points"
    if "受信ﾌﾚｰﾑ登録1" in comment:
        return "receive_frame_1"
    if "登録ﾌﾚｰﾑ2" in comment:
        return "receive_frame_2"
    if "登録ﾌﾚｰﾑ3" in comment:
        return "receive_frame_3"
    if "登録ﾌﾚｰﾑ4" in comment:
        return "receive_frame_4"
    if "読取桁数" in comment:
        return "read_digits"
    if "ﾌﾛｰ制御" in comment or "フロー制御" in comment:
        return "flow_control"
    if "ﾜｰﾄﾞ/ﾊﾞｲﾄ" in comment:
        return "word_byte_select"
    return "setting"


def infer_equipment_name(settings: dict[str, dict[str, object]]) -> str:
    for item in settings.values():
        comment = str(item.get("comment", ""))
        for marker in (" 局番", " ｱｸｾｽ", " ﾊﾞｯﾌｧ", " 書込", " 受信"):
            if marker in comment:
                return comment.split(marker, 1)[0].strip()
    for item in settings.values():
        comment = str(item.get("comment", "")).strip()
        if comment:
            return comment.split()[0]
    return ""


def first_const(operation: DecodedOperation) -> tuple[int, str] | None:
    for index, value in operation.constant_values.items():
        signedness = ""
        if index < len(operation.raw_args):
            match = CONST_SIGN_RE.search(operation.raw_args[index])
            signedness = match.group(1) if match else ""
        try:
            return int(value), signedness
        except ValueError:
            return None
    return None


def device_at(operation: DecodedOperation, arg_index: int) -> str:
    return next((arg.device for arg in operation.args if arg.arg_index == arg_index), "")


def extract_aj65bt_r2n_settings(
    rows: list[LadderRow],
    comments: dict[tuple[str, int], CommentInfo],
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in rows:
        if "AJ65BT-R2N" not in row.title:
            continue
        operations, _status = parse_row_operations(row.data)
        if not any(operation.role in {"GP.RIWT", "GP.RIRD"} for operation in operations):
            continue

        settings: dict[str, dict[str, object]] = {}
        gp_ops: list[DecodedOperation] = []
        for operation in operations:
            if operation.role == "MOVP":
                const = first_const(operation)
                device = next((arg for arg in reversed(operation.args) if arg.device_type == "D"), None)
                if const is None or device is None:
                    continue
                value, si = const
                comment = comment_for_device(device.device_type, device.number, comments)
                role = device_role_from_comment(comment)
                settings[role] = {
                    "device": device.device,
                    "value": value,
                    "signedness": si,
                    "comment": comment,
                }
            elif operation.role in {"GP.RIWT", "GP.RIRD"}:
                gp_ops.append(operation)

        equipment_name = infer_equipment_name(settings)
        for op in gp_ops:
            row_out: dict[str, object] = {
                "equipment_name": equipment_name,
                "opcode": op.role,
                "module_unit": device_at(op, 0),
                "control_base": device_at(op, 1),
                "frame_base": device_at(op, 2),
                "complete_device": device_at(op, 3),
                "lddb": row.lddb,
                "pos": row.pos,
                "title": row.title,
                "conditions": "; ".join(
                    f"{occ.role}:{occ.device} {comment_for_device(occ.device_type, occ.number, comments)}"
                    for occ in row.occurrences
                    if occ.role in {"a", "b"}
                ),
                "settings_summary": "; ".join(
                    f"{role}={item['value']}({item['device']} {item['comment']})"
                    for role, item in settings.items()
                ),
            }
            for role, item in settings.items():
                value = int(item["value"])
                row_out[f"{role}_device"] = item["device"]
                row_out[f"{role}_value"] = value
                row_out[f"{role}_hex"] = f"0x{value:X}"
                row_out[f"{role}_comment"] = item["comment"]
            out.append(row_out)
    return out


def collapse_adjacent_modules(modules: list[tuple[int, str]]) -> list[dict[str, object]]:
    groups: list[dict[str, object]] = []
    for offset, name in modules:
        if groups and groups[-1]["module_name"] == name:
            groups[-1]["count"] = int(groups[-1]["count"]) + 1
            groups[-1]["offsets"].append(offset)
        else:
            groups.append({"module_name": name, "count": 1, "offsets": [offset]})
    return groups


def clean_module_name(text: str) -> str:
    out = ""
    for ch in text:
        if ord(ch) < 32:
            break
        out += ch
    return out


def device_value(text: str) -> tuple[str, int] | None:
    value_text = str(text).strip().upper()
    prefix = ""
    value = ""
    for candidate in ("SB", "SW", "ZR", "SM", "SD", "X", "Y", "B", "W", "M", "L", "D", "R", "T", "C", "Z"):
        if value_text.startswith(candidate):
            prefix = candidate
            value = value_text[len(candidate) :]
            break
    if not prefix or not value or not re.fullmatch(r"[0-9A-F]+", value):
        return None
    base = device_radix(prefix)
    return prefix, int(value, base)


def device_text(prefix: str, value: int) -> str:
    return format_device(prefix, value)


def range_text(prefix: str, start: int, points: int) -> str:
    if points <= 0:
        return ""
    return f"{device_text(prefix, start)}..{device_text(prefix, start + points - 1)}"


def module_capability(module_name: str) -> dict[str, object]:
    if module_name == "汎用リモートI/O局":
        return {"station_type": "generic_remote_io_station", "rx_points": 32, "ry_points": 32, "rwr_words": 0, "rww_words": 0}
    if module_name == "AJ65SBTB1-32D":
        return {"station_type": "remote_input_module", "rx_points": 32, "ry_points": 0, "rwr_words": 0, "rww_words": 0}
    if module_name == "AJ65SBTB1-32TE1":
        return {"station_type": "remote_output_module", "rx_points": 0, "ry_points": 32, "rwr_words": 0, "rww_words": 0}
    if module_name == "AJ65SBTB1-32DTE1":
        return {"station_type": "mixed_remote_io_module", "rx_points": 16, "ry_points": 16, "rwr_words": 0, "rww_words": 0}
    if module_name == "AJ65BT-R2N":
        return {"station_type": "remote_device_station_serial", "rx_points": 32, "ry_points": 32, "rwr_words": 4, "rww_words": 4}
    return {"station_type": "remote_station", "rx_points": 32, "ry_points": 32, "rwr_words": 0, "rww_words": 0}


def station_record_offsets(path: Path) -> list[tuple[int, str, int]]:
    data = path.read_bytes()
    out: list[tuple[int, str, int]] = []
    for off, text in iter_utf16_strings_any_alignment(path):
        if not (AJ65_RE.match(text) or text.startswith("汎用リモートI/O局")):
            continue
        name = clean_module_name(text)
        marker_rel = len(name) * 2
        if off + marker_rel + 2 > len(data):
            continue
        marker = int.from_bytes(data[off + marker_rel : off + marker_rel + 2], "little")
        if marker != 1:
            continue
        station_offset = off + marker_rel + 64
        if station_offset + 2 > len(data):
            continue
        station_no = int.from_bytes(data[station_offset : station_offset + 2], "little")
        if 1 <= station_no <= 64:
            out.append((off, name, station_no))
    return out


def extract_remote_station_assignments(
    refresh_rows: list[dict[str, str]],
    root: Path,
    r2n_rows: list[dict[str, object]],
) -> list[dict[str, object]]:
    r2n_station_to_equipment: dict[int, str] = {}
    for row in r2n_rows:
        value = row.get("station_no_value", "")
        if value in ("", None):
            continue
        try:
            r2n_station_to_equipment[int(str(value))] = str(row.get("equipment_name", ""))
        except ValueError:
            continue

    areas_by_file: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in refresh_rows:
        evidence_file = row.get("evidence_file", "")
        if evidence_file:
            areas_by_file[evidence_file].append(row)

    out: list[dict[str, object]] = []
    for evidence_file, areas in areas_by_file.items():
        path = root / evidence_file
        if not path.exists():
            continue
        records = station_record_offsets(path)
        if not records:
            continue
        network = areas[0].get("network_label", "")
        object_id = areas[0].get("object_id", "")
        unit = areas[0].get("unit_name", "")
        slot = areas[0].get("slot_number", "")
        rx = next((r for r in areas if r.get("area_kind") == "remote_input_RX"), {})
        ry = next((r for r in areas if r.get("area_kind") == "remote_output_RY"), {})
        rwr = next((r for r in areas if r.get("area_kind") == "remote_register_RWr"), {})
        rww = next((r for r in areas if r.get("area_kind") == "remote_register_RWw"), {})
        rx_start = device_value(rx.get("device_start", ""))
        rx_end = device_value(rx.get("device_end", ""))
        ry_start = device_value(ry.get("device_start", ""))
        ry_end = device_value(ry.get("device_end", ""))
        rwr_start = device_value(rwr.get("device_start", ""))
        rwr_end = device_value(rwr.get("device_end", ""))
        rww_start = device_value(rww.get("device_start", ""))
        rww_end = device_value(rww.get("device_end", ""))
        for off, module_name, station_no in sorted(records, key=lambda item: item[2]):
            caps = module_capability(module_name)
            rx_base = rx_start[1] + (station_no - 1) * 0x20 if rx_start else -1
            ry_base = ry_start[1] + (station_no - 1) * 0x20 if ry_start else -1
            rwr_base = rwr_start[1] + (station_no - 1) * 4 if rwr_start else -1
            rww_base = rww_start[1] + (station_no - 1) * 4 if rww_start else -1
            if (
                int(caps["rx_points"]) > 0
                and rx_start
                and rx_end
                and rx_base + int(caps["rx_points"]) - 1 > rx_end[1]
            ):
                continue
            if (
                int(caps["ry_points"]) > 0
                and ry_start
                and ry_end
                and ry_base + int(caps["ry_points"]) - 1 > ry_end[1]
            ):
                continue
            if (
                int(caps["rwr_words"]) > 0
                and rwr_start
                and rwr_end
                and rwr_base + int(caps["rwr_words"]) - 1 > rwr_end[1]
            ):
                continue
            if (
                int(caps["rww_words"]) > 0
                and rww_start
                and rww_end
                and rww_base + int(caps["rww_words"]) - 1 > rww_end[1]
            ):
                continue
            rwr_prefix = rwr_start[0] if rwr_start else "W"
            rww_prefix = rww_start[0] if rww_start else "W"
            out.append(
                {
                    "network_label": network,
                    "object_id": object_id,
                    "unit_name": unit,
                    "slot_number": slot,
                    "station_no": station_no,
                    "module_name": module_name,
                    "station_type": caps["station_type"],
                    "equipment_name": r2n_station_to_equipment.get(station_no, ""),
                    "rx_points_used": caps["rx_points"],
                    "ry_points_used": caps["ry_points"],
                    "rwr_words_used": caps["rwr_words"],
                    "rww_words_used": caps["rww_words"],
                    "rx_range": range_text("X", rx_base, int(caps["rx_points"])) if rx_base >= 0 else "",
                    "ry_range": range_text("Y", ry_base, int(caps["ry_points"])) if ry_base >= 0 else "",
                    "rwr_range": range_text(rwr_prefix, rwr_base, int(caps["rwr_words"])) if rwr_base >= 0 else "",
                    "rww_range": range_text(rww_prefix, rww_base, int(caps["rww_words"])) if rww_base >= 0 else "",
                    "station_rx_base": device_text("X", rx_base) if rx_base >= 0 else "",
                    "station_ry_base": device_text("Y", ry_base) if ry_base >= 0 else "",
                    "station_rwr_base": device_text(rwr_prefix, rwr_base) if rwr_base >= 0 else "",
                    "station_rww_base": device_text(rww_prefix, rww_base) if rww_base >= 0 else "",
                    "evidence_file": evidence_file,
                    "evidence_offset_hex": f"0x{off:X}",
                    "confidence": "high_binary_station_no"
                    if module_name == "AJ65BT-R2N" and station_no in r2n_station_to_equipment
                    else "medium_binary_station_no",
                    "note": "Station number decoded from w3pa module record; X/Y/W ranges use standard CC-Link per-station offset formula.",
                }
            )
    return out


def assignment_for_device(row: dict[str, object], assignments: list[dict[str, object]]) -> dict[str, object] | None:
    parsed = device_value(str(row.get("plc_device") or row.get("device") or ""))
    if not parsed:
        return None
    prefix, value = parsed
    range_key = {"X": "rx_range", "Y": "ry_range", "W": "rwr_range"}.get(prefix)
    if not range_key:
        return None
    for assignment in assignments:
        text = str(assignment.get(range_key, ""))
        if not text or ".." not in text:
            continue
        start_text, end_text = text.split("..", 1)
        start = device_value(start_text)
        end = device_value(end_text)
        if start and end and start[0] == prefix and start[1] <= value <= end[1]:
            return assignment
    return None


def enrich_external_rows_with_station(
    external_rows: list[dict[str, object]],
    assignments: list[dict[str, object]],
) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in external_rows:
        item = dict(row)
        assignment = assignment_for_device(item, assignments)
        if assignment:
            item.update(
                {
                    "remote_station_no": assignment.get("station_no", ""),
                    "remote_module_name": assignment.get("module_name", ""),
                    "remote_station_type": assignment.get("station_type", ""),
                    "remote_equipment_name": assignment.get("equipment_name", ""),
                    "remote_rx_range": assignment.get("rx_range", ""),
                    "remote_ry_range": assignment.get("ry_range", ""),
                    "remote_rwr_range": assignment.get("rwr_range", ""),
                    "remote_rww_range": assignment.get("rww_range", ""),
                    "remote_assignment_confidence": assignment.get("confidence", ""),
                }
            )
        else:
            item.update(
                {
                    "remote_station_no": "",
                    "remote_module_name": "",
                    "remote_station_type": "",
                    "remote_equipment_name": "",
                    "remote_rx_range": "",
                    "remote_ry_range": "",
                    "remote_rwr_range": "",
                    "remote_rww_range": "",
                    "remote_assignment_confidence": "",
                }
            )
        out.append(item)
    return out


def add_assignment_usage_counts(
    assignment_rows: list[dict[str, object]],
    external_rows: list[dict[str, object]],
) -> None:
    counts: dict[tuple[str, int], int] = defaultdict(int)
    examples: dict[tuple[str, int], list[str]] = defaultdict(list)
    for row in external_rows:
        station = row.get("remote_station_no", "")
        network = str(row.get("refresh_network_label") or row.get("source_unit_connection") or "unknown_network")
        if station in ("", None):
            continue
        try:
            key = (network, int(str(station)))
        except ValueError:
            continue
        counts[key] += 1
        if len(examples[key]) < 8:
            examples[key].append(f"{row.get('plc_device') or row.get('device')} {row.get('comment') or ''}".strip())
    for row in assignment_rows:
        try:
            key = (str(row.get("network_label") or ""), int(str(row.get("station_no") or "0")))
        except ValueError:
            key = ("", 0)
        row["used_external_device_count"] = counts.get(key, 0)
        row["used_external_device_examples"] = "; ".join(examples.get(key, []))


def extract_remote_station_candidates(refresh_rows: list[dict[str, str]], root: Path) -> list[dict[str, object]]:
    areas_by_file: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in refresh_rows:
        evidence_file = row.get("evidence_file", "")
        if evidence_file:
            areas_by_file[evidence_file].append(row)

    out: list[dict[str, object]] = []
    for evidence_file, areas in areas_by_file.items():
        path = root / evidence_file
        if not path.exists():
            continue
        modules = [(off, text) for off, text in iter_utf16_strings_any_alignment(path) if AJ65_RE.match(text)]
        if not modules:
            continue
        groups = collapse_adjacent_modules(modules)
        network = areas[0].get("network_label", "")
        object_id = areas[0].get("object_id", "")
        unit = areas[0].get("unit_name", "")
        slot = areas[0].get("slot_number", "")
        rx = next((r for r in areas if r.get("area_kind") == "remote_input_RX"), {})
        ry = next((r for r in areas if r.get("area_kind") == "remote_output_RY"), {})
        rwr = next((r for r in areas if r.get("area_kind") == "remote_register_RWr"), {})
        rww = next((r for r in areas if r.get("area_kind") == "remote_register_RWw"), {})
        for index, group in enumerate(groups, start=1):
            offsets = [int(v) for v in group["offsets"]]
            out.append(
                {
                    "network_label": network,
                    "object_id": object_id,
                    "unit_name": unit,
                    "slot_number": slot,
                    "station_index_candidate": index,
                    "module_name": group["module_name"],
                    "duplicate_count_in_w3pa": group["count"],
                    "first_offset_hex": f"0x{min(offsets):X}",
                    "all_offsets_hex": "; ".join(f"0x{v:X}" for v in offsets),
                    "rx_refresh_range": f"{rx.get('device_start', '')}..{rx.get('device_end', '')}".strip("."),
                    "ry_refresh_range": f"{ry.get('device_start', '')}..{ry.get('device_end', '')}".strip("."),
                    "rwr_refresh_range": f"{rwr.get('device_start', '')}..{rwr.get('device_end', '')}".strip("."),
                    "rww_refresh_range": f"{rww.get('device_start', '')}..{rww.get('device_end', '')}".strip("."),
                    "evidence_file": evidence_file,
                    "confidence": "candidate_string_order_only",
                    "note": "Station number and exact terminal range require GX Works3 station assignment confirmation.",
                }
            )
    return out


def render_markdown(
    assignment_rows: list[dict[str, object]],
    station_rows: list[dict[str, object]],
    r2n_rows: list[dict[str, object]],
    external_rows: list[dict[str, object]],
) -> str:
    lines = [
            "# Communication Detail",
        "",
        "This report goes deeper than refresh areas. It separates confirmed unit/range mapping from station/module candidates and ladder-side AJ65BT-R2N parameter writes.",
        "",
        "## Confirmed Unit Sources",
    ]
    unit_counts: dict[str, int] = defaultdict(int)
    for row in external_rows:
        key = " / ".join(
            part
            for part in [
                str(row.get("source_unit_kind") or ""),
                str(row.get("source_unit_connection") or ""),
                str(row.get("source_unit_name") or ""),
                f"slot={row.get('source_unit_slot_number')}" if row.get("source_unit_slot_number") else "",
                str(row.get("source_unit_area") or ""),
            ]
            if part
        )
        if key:
            unit_counts[key] += 1
    if unit_counts:
        for key, count in sorted(unit_counts.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- {key}: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Decoded Remote Station Assignments"])
    if assignment_rows:
        for row in assignment_rows:
            equipment = f" {row['equipment_name']}" if row.get("equipment_name") else ""
            ranges = ", ".join(
                part
                for part in [
                    f"RX {row['rx_range']}" if row.get("rx_range") else "",
                    f"RY {row['ry_range']}" if row.get("ry_range") else "",
                    f"RWr {row['rwr_range']}" if row.get("rwr_range") else "",
                    f"RWw {row['rww_range']}" if row.get("rww_range") else "",
                ]
                if part
            )
            lines.append(
                f"- {row['network_label']} station {row['station_no']}: "
                f"{row['module_name']}{equipment}, {ranges} "
                f"used_devices={row.get('used_external_device_count', 0)} "
                f"confidence={row['confidence']}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Remote Station Candidates"])
    if station_rows:
        for row in station_rows:
            lines.append(
                f"- {row['network_label']} station? {row['station_index_candidate']}: "
                f"{row['module_name']} offsets={row['all_offsets_hex']} "
                f"RX={row['rx_refresh_range']} RWr={row['rwr_refresh_range']}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## AJ65BT-R2N Ladder Parameter Writes"])
    if r2n_rows:
        for row in r2n_rows:
            station = row.get("station_no_value", "")
            buffer_address = row.get("buffer_address_value", "")
            points = row.get("points_value", "")
            lines.append(
                f"- {row.get('equipment_name') or '(unknown equipment)'}: "
                f"{row.get('opcode')} {row.get('module_unit')} "
                f"station={station} buffer={buffer_address} points={points} "
                f"row={row.get('lddb')}:{row.get('pos')}"
            )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Interpretation Rule",
            "- `refresh_area` rows are confirmed to the PLC unit, slot, direction, and device range.",
            "- `direct_unit_io` rows are confirmed as module I/O/status bits by unit head I/O range.",
            "- `station_index_candidate` rows are evidence from w3pa string order only; use them as a checklist, not final terminal numbers.",
            "- `Decoded Remote Station Assignments` rows decode station number from each w3pa module record. These are stronger than the earlier string-order candidates.",
            "- `AJ65BT-R2N` rows are ladder-written communication parameters and are stronger evidence for measurement devices using `GP.RIWT/GP.RIRD` through `U6`.",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build detailed communication source reports.")
    comm_prefix = default_comm_prefix()
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=default_output_prefix("comm_detail"))
    parser.add_argument("--refresh-csv", default=f"{comm_prefix}_refresh_areas.csv")
    parser.add_argument("--unit-csv", default=f"{comm_prefix}_units.csv")
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    output_dir = Path(args.output_dir)

    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)
    refresh_rows = read_csv(Path(args.refresh_csv))
    refresh_areas = load_refresh_areas(Path(args.refresh_csv))
    unit_io_areas = load_unit_io_areas(Path(args.unit_csv))
    external_rows = collect_external_inputs(rows, comments, refresh_areas, unit_io_areas)
    station_rows = extract_remote_station_candidates(refresh_rows, root)
    r2n_rows = extract_aj65bt_r2n_settings(rows, comments)
    assignment_rows = extract_remote_station_assignments(refresh_rows, root, r2n_rows)
    external_rows = enrich_external_rows_with_station(external_rows, assignment_rows)
    add_assignment_usage_counts(assignment_rows, external_rows)

    assignment_csv = output_dir / f"{args.prefix}_remote_station_assignments.csv"
    station_csv = output_dir / f"{args.prefix}_remote_station_candidates.csv"
    r2n_csv = output_dir / f"{args.prefix}_aj65bt_r2n_settings.csv"
    external_csv = output_dir / f"{args.prefix}_external_source_detail.csv"
    md_path = output_dir / f"{args.prefix}.md"
    manifest_path = output_dir / f"{args.prefix}_manifest.json"

    write_csv(
        assignment_csv,
        assignment_rows,
        [
            "network_label",
            "object_id",
            "unit_name",
            "slot_number",
            "station_no",
            "module_name",
            "station_type",
            "equipment_name",
            "rx_points_used",
            "ry_points_used",
            "rwr_words_used",
            "rww_words_used",
            "rx_range",
            "ry_range",
            "rwr_range",
            "rww_range",
            "used_external_device_count",
            "used_external_device_examples",
            "station_rx_base",
            "station_ry_base",
            "station_rwr_base",
            "station_rww_base",
            "evidence_file",
            "evidence_offset_hex",
            "confidence",
            "note",
        ],
    )
    write_csv(
        station_csv,
        station_rows,
        [
            "network_label",
            "object_id",
            "unit_name",
            "slot_number",
            "station_index_candidate",
            "module_name",
            "duplicate_count_in_w3pa",
            "first_offset_hex",
            "all_offsets_hex",
            "rx_refresh_range",
            "ry_refresh_range",
            "rwr_refresh_range",
            "rww_refresh_range",
            "evidence_file",
            "confidence",
            "note",
        ],
    )
    write_csv(
        r2n_csv,
        r2n_rows,
        [
            "equipment_name",
            "opcode",
            "module_unit",
            "station_no_value",
            "station_no_hex",
            "buffer_address_value",
            "buffer_address_hex",
            "points_value",
            "points_hex",
            "access_attribute_code_value",
            "access_attribute_code_hex",
            "receive_frame_1_value",
            "receive_frame_1_hex",
            "control_base",
            "frame_base",
            "complete_device",
            "conditions",
            "settings_summary",
            "lddb",
            "pos",
            "title",
        ],
    )
    write_csv(
        external_csv,
        external_rows,
        [
            "source_kind",
            "semantic_group",
            "plc_device",
            "device",
            "comment",
            "occurrences",
            "source_unit_kind",
            "source_unit_name",
            "source_unit_connection",
            "source_unit_slot_number",
            "source_unit_start_io",
            "source_unit_area",
            "refresh_area",
            "refresh_station_modules",
            "remote_station_no",
            "remote_module_name",
            "remote_station_type",
            "remote_equipment_name",
            "remote_rx_range",
            "remote_ry_range",
            "remote_rwr_range",
            "remote_rww_range",
            "remote_assignment_confidence",
            "first_lddb",
            "first_pos",
            "first_title",
        ],
    )
    md_path.write_text(render_markdown(assignment_rows, station_rows, r2n_rows, external_rows) + "\n", encoding="utf-8")

    manifest = {
        "root": str(root),
        "outputs": {
            "markdown": str(md_path),
            "remote_station_assignments_csv": str(assignment_csv),
            "remote_station_candidates_csv": str(station_csv),
            "aj65bt_r2n_settings_csv": str(r2n_csv),
            "external_source_detail_csv": str(external_csv),
            "manifest": str(manifest_path),
        },
        "counts": {
            "remote_station_candidates": len(station_rows),
            "remote_station_assignments": len(assignment_rows),
            "aj65bt_r2n_setting_rows": len(r2n_rows),
            "external_source_rows": len(external_rows),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"communication detail report written: {md_path}")
    print(f"station assignments: {assignment_csv}")
    print(f"station candidates: {station_csv}")
    print(f"AJ65BT-R2N settings: {r2n_csv}")
    print(f"external source detail: {external_csv}")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
