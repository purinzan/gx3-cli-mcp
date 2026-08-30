from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gx3cli.gx3_device_name import format_device as _format_device
from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.review_gx3_project import LadderRow, comment_for_device, load_comments_for_root, load_rows
from gx3cli.gx3_project_paths import default_comm_prefix, default_output_prefix, default_project_root


CONTACT_ROLES = {"a", "b"}
PREDICATE_CONDITION_ROLES = {"=", "==", "<>", "<=", ">=", "<", ">"}
CONDITION_ROLES = CONTACT_ROLES | PREDICATE_CONDITION_ROLES
DRIVER_ROLES = {"c", "SET", "PLS", "PLF", "OUT__16", "OUTH__16", "RST"}
HEX_DEVICE_TYPES = {"X", "Y", "B", "W", "SB", "SW"}
CONSTANT_DEVICES = {
    "SM400": ("constant_true", "always_on"),
    "SM401": ("constant_false", "always_off"),
}
DEVICE_TEXT_RE = re.compile(r"^([A-Z]+)([0-9A-F]+)$", re.IGNORECASE)


@dataclass(frozen=True)
class RefreshArea:
    object_id: str
    network_label: str
    area_kind: str
    direction: str
    device_type: str
    start_text: str
    end_text: str
    start_value: int
    end_value: int
    unit_name: str
    slot_number: str
    unit_start_io: str
    station: str


@dataclass(frozen=True)
class UnitIoArea:
    object_id: str
    unit_name: str
    connection: str
    slot_number: str
    start_io_text: str
    start_value: int
    end_value: int


SEMANTIC_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("unused_or_spare", re.compile(r"予備|未使用|spare|unused", re.IGNORECASE)),
    ("safety_input", re.compile(r"非常|安全|扉|ドア|ｶﾊﾞｰ|カバー|ﾛｯｸ|ロック|EMS|EMG|door|cover|\block\b|interlock", re.IGNORECASE)),
    ("operator_input", re.compile(r"PB|SW|押|ボタン|ﾎﾞﾀﾝ|操作|選択|起動|停止|リセット|ﾘｾｯﾄ|button|switch", re.IGNORECASE)),
    ("servo_drive_feedback", re.compile(r"サーボ|ｻｰﾎﾞ|軸|アンプ|ｱﾝﾌﾟ|servo|drive|axis", re.IGNORECASE)),
    ("equipment_communication", re.compile(r"上流|下流|他設備|前工程|後工程|通信|送信|受信|要求|許可|PLC|MES|SLMP|CC-Link|EtherNet|Ethernet", re.IGNORECASE)),
    ("sensor_position_input", re.compile(r"センサ|ｾﾝｻ|検知|端|到着|確認|ワーク|ﾜｰｸ|キャリア|ｷｬﾘｱ|有|無し|満|空|sensor|limit|position", re.IGNORECASE)),
    ("inspection_measurement_data", re.compile(r"測定|計測|検査|判定|閾値|値|データ|ﾃﾞｰﾀ|inspection|measure", re.IGNORECASE)),
    ("setting_or_hmi_data", re.compile(r"設定|選択No|画面|パラメータ|ﾊﾟﾗﾒｰﾀ|モード|ﾓｰﾄﾞ|setting|parameter|screen", re.IGNORECASE)),
]


def device_address_value(device_type: str, number: int | str) -> int:
    if isinstance(number, int):
        return number
    text = str(number).upper()
    base = 16 if device_type.upper() in HEX_DEVICE_TYPES else 10
    return int(text, base)


def parse_device_text(device: str) -> tuple[str, int] | None:
    match = DEVICE_TEXT_RE.fullmatch(device.strip().upper())
    if not match:
        return None
    device_type = match.group(1)
    return device_type, device_address_value(device_type, match.group(2))


def plc_device_text(device_type: str, number: int, fallback: str = "") -> str:
    if device_type.upper() in HEX_DEVICE_TYPES:
        return f"{device_type}{number:X}"
    return fallback or _format_device(device_type, number)


def find_comm_csv(name_suffix: str, path: Path | None = None) -> Path:
    """Resolve a communication CSV: explicit path, then outputs/, then CWD."""
    if path is not None:
        return path
    name = f"{default_comm_prefix()}_{name_suffix}"
    for candidate in (Path("outputs") / name, Path(name)):
        if candidate.exists():
            return candidate
    print(f"warning: {name} not found in outputs/ or CWD; network boundaries unavailable", file=sys.stderr)
    return Path(name)


def load_refresh_areas(path: Path | None = None) -> list[RefreshArea]:
    path = find_comm_csv("refresh_areas.csv", path)
    if not path.exists():
        return []
    areas: list[RefreshArea] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            start = str(row.get("device_start", "")).strip().upper()
            end = str(row.get("device_end", "")).strip().upper()
            start_parsed = parse_device_text(start)
            end_parsed = parse_device_text(end)
            if not start_parsed or not end_parsed:
                continue
            if start_parsed[0] != end_parsed[0]:
                continue
            areas.append(
                RefreshArea(
                    object_id=str(row.get("object_id", "")),
                    network_label=str(row.get("network_label", "")),
                    area_kind=str(row.get("area_kind", "")),
                    direction=str(row.get("direction", "")),
                    device_type=start_parsed[0],
                    start_text=start,
                    end_text=end,
                    start_value=start_parsed[1],
                    end_value=end_parsed[1],
                    unit_name=str(row.get("unit_name", "")),
                    slot_number=str(row.get("slot_number", "")),
                    unit_start_io=str(row.get("unit_start_io", "")),
                    station=str(row.get("remote_station_module_strings", "")),
                )
            )
    return areas


def load_unit_io_areas(path: Path | None = None) -> list[UnitIoArea]:
    path = find_comm_csv("units.csv", path)
    if not path.exists():
        return []
    areas: list[UnitIoArea] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                start_value = int(str(row.get("start_io_dec", "")).strip())
                points = int(str(row.get("io_points", "")).strip() or "0")
            except ValueError:
                continue
            if points <= 0:
                continue
            areas.append(
                UnitIoArea(
                    object_id=str(row.get("object_id", "")),
                    unit_name=str(row.get("unit_name", "")),
                    connection=str(row.get("connection", "")),
                    slot_number=str(row.get("slot_number", "")),
                    start_io_text=str(row.get("start_io_hex", "")),
                    start_value=start_value,
                    end_value=start_value + points - 1,
                )
            )
    return areas


def refresh_area_for(device_type: str, number: int, areas: list[RefreshArea]) -> RefreshArea | None:
    try:
        value = device_address_value(device_type, number)
    except ValueError:
        return None
    for area in areas:
        if area.device_type == device_type and area.start_value <= value <= area.end_value:
            return area
    return None


def unit_io_area_for(device_type: str, number: int, areas: list[UnitIoArea]) -> UnitIoArea | None:
    if device_type not in {"X", "Y"}:
        return None
    try:
        value = device_address_value(device_type, number)
    except ValueError:
        return None
    for area in areas:
        if area.start_value <= value <= area.end_value:
            return area
    return None


def infer_semantic_group(comment: str) -> str:
    for name, pattern in SEMANTIC_PATTERNS:
        if pattern.search(comment or ""):
            return name
    return "external_unknown"


def classify_external_contact(
    device_type: str,
    number: int,
    device: str,
    comment: str,
    has_driver: bool,
    refresh_areas: list[RefreshArea] | None = None,
    unit_io_areas: list[UnitIoArea] | None = None,
) -> dict[str, object]:
    refresh_areas = refresh_areas or []
    unit_io_areas = unit_io_areas or []
    if device in CONSTANT_DEVICES:
        source_kind, detail = CONSTANT_DEVICES[device]
        return {
            "source_kind": source_kind,
            "semantic_group": "constant",
            "source_detail": detail,
            "trace_boundary": True,
            "stop_reason": "hard_constant",
            "refresh_area": "",
            "refresh_network_label": "",
            "refresh_unit_name": "",
            "refresh_slot_number": "",
            "refresh_unit_start_io": "",
            "refresh_area_kind": "",
            "refresh_direction": "",
            "refresh_device_range": "",
            "refresh_station_modules": "",
        }
    if has_driver:
        return {
            "source_kind": "internal_logic",
            "semantic_group": "internal_logic",
            "source_detail": "driven by PLC ladder coil/set/reset/timer output",
            "trace_boundary": False,
            "stop_reason": "",
            "refresh_area": "",
            "refresh_network_label": "",
            "refresh_unit_name": "",
            "refresh_slot_number": "",
            "refresh_unit_start_io": "",
            "refresh_area_kind": "",
            "refresh_direction": "",
            "refresh_device_range": "",
            "refresh_station_modules": "",
        }

    area = refresh_area_for(device_type, number, refresh_areas)
    unit_area = unit_io_area_for(device_type, number, unit_io_areas)
    if device_type in {"SM", "SD"}:
        return {
            "source_kind": "system_special_device",
            "semantic_group": "system_clock_or_status",
            "source_detail": "PLC special relay/register",
            "trace_boundary": True,
            "stop_reason": "system_special_device",
            "refresh_area": "",
            "refresh_network_label": "",
            "refresh_unit_name": "",
            "refresh_slot_number": "",
            "refresh_unit_start_io": "",
            "refresh_area_kind": "",
            "refresh_direction": "",
            "refresh_device_range": "",
            "refresh_station_modules": "",
        }

    semantic = infer_semantic_group(comment)
    area_label = ""
    if area:
        area_label = (
            f"{area.network_label}:{area.area_kind}:{area.direction}:"
            f"{area.start_text}..{area.end_text}"
        )

    if device_type == "X":
        if area:
            source_kind = "remote_input"
            detail = "remote input refreshed from network station"
        elif unit_area:
            source_kind = "unit_io_input"
            detail = "module input/status in configured unit I/O range"
        else:
            source_kind = "physical_input"
            detail = "PLC input contact"
    elif device_type in {"B", "W", "SB", "SW"}:
        source_kind = "network_or_link_data"
        detail = "link relay/register or communication refresh area"
    elif device_type in {"D", "R", "ZR", "SD"}:
        source_kind = "word_data_or_setting"
        detail = "word register, HMI setting, recipe, measurement, or communication data"
    elif device_type in {"M", "L"}:
        source_kind = "terminal_internal_relay_external_or_hmi"
        detail = "not driven in ladder; likely HMI, external write, unused, or generated outside parsed ladder"
    elif device_type in {"T", "C"}:
        source_kind = "timer_counter_terminal"
        detail = "timer/counter contact without detected driver"
    elif device_type == "Y":
        source_kind = "unit_io_output_feedback_or_unresolved" if unit_area else "plc_output_feedback_or_unresolved"
        detail = "module output/status in configured unit I/O range" if unit_area else "output contact without detected driver"
    else:
        source_kind = "terminal_unknown"
        detail = "no PLC ladder driver found"

    source_unit_kind = ""
    source_unit_name = ""
    source_unit_connection = ""
    source_unit_slot_number = ""
    source_unit_start_io = ""
    source_unit_area = ""
    if area:
        source_unit_kind = "refresh_area"
        source_unit_name = area.unit_name
        source_unit_connection = area.network_label
        source_unit_slot_number = area.slot_number
        source_unit_start_io = area.unit_start_io
        source_unit_area = f"{area.area_kind} {area.direction} {area.start_text}..{area.end_text}"
    elif unit_area:
        source_unit_kind = "direct_unit_io"
        source_unit_name = unit_area.unit_name
        source_unit_connection = unit_area.connection
        source_unit_slot_number = unit_area.slot_number
        source_unit_start_io = unit_area.start_io_text
        source_unit_area = f"{device_type}{unit_area.start_value:X}..{device_type}{unit_area.end_value:X}"

    return {
        "source_kind": source_kind,
        "semantic_group": semantic,
        "source_detail": detail,
        "trace_boundary": True,
        "stop_reason": "external_or_terminal_contact",
        "refresh_area": area_label,
        "refresh_network_label": area.network_label if area else "",
        "refresh_unit_name": area.unit_name if area else "",
        "refresh_slot_number": area.slot_number if area else "",
        "refresh_unit_start_io": area.unit_start_io if area else "",
        "refresh_area_kind": area.area_kind if area else "",
        "refresh_direction": area.direction if area else "",
        "refresh_device_range": f"{area.start_text}..{area.end_text}" if area else "",
        "refresh_station_modules": area.station if area else "",
        "source_unit_kind": source_unit_kind,
        "source_unit_name": source_unit_name,
        "source_unit_connection": source_unit_connection,
        "source_unit_slot_number": source_unit_slot_number,
        "source_unit_start_io": source_unit_start_io,
        "source_unit_area": source_unit_area,
    }


def build_driver_index(rows: list[LadderRow]) -> dict[str, list[LadderRow]]:
    index: dict[str, list[LadderRow]] = {}
    for row in rows:
        for occ in row.occurrences:
            if occ.role not in DRIVER_ROLES:
                continue
            index.setdefault(occ.device, []).append(row)
    return index


def collect_external_inputs(
    rows: list[LadderRow],
    comments: dict[tuple[str, int], CommentInfo],
    refresh_areas: list[RefreshArea],
    unit_io_areas: list[UnitIoArea] | None = None,
) -> list[dict[str, object]]:
    unit_io_areas = unit_io_areas or []
    drivers = build_driver_index(rows)
    usage: dict[str, dict[str, Any]] = {}

    for row in rows:
        for occ in row.occurrences:
            if occ.role not in CONDITION_ROLES:
                continue
            comment = comment_for_device(occ.device_type, occ.number, comments)
            classification = classify_external_contact(
                occ.device_type,
                occ.number,
                occ.device,
                comment,
                has_driver=bool(drivers.get(occ.device)),
                refresh_areas=refresh_areas,
                unit_io_areas=unit_io_areas,
            )
            if not classification["trace_boundary"]:
                continue

            rec = usage.setdefault(
                occ.device,
                {
                    "device": occ.device,
                    "plc_device": plc_device_text(occ.device_type, occ.number, occ.device),
                    "device_type": occ.device_type,
                    "number": occ.number,
                    "comment": comment,
                    "occurrences": 0,
                    "required_on_count": 0,
                    "required_off_count": 0,
                    "predicate_count": 0,
                    "predicate_roles": Counter(),
                    "source_kind": classification["source_kind"],
                    "semantic_group": classification["semantic_group"],
                    "source_detail": classification["source_detail"],
                    "stop_reason": classification["stop_reason"],
                    "refresh_area": classification["refresh_area"],
                    "refresh_network_label": classification["refresh_network_label"],
                    "refresh_unit_name": classification["refresh_unit_name"],
                    "refresh_slot_number": classification["refresh_slot_number"],
                    "refresh_unit_start_io": classification["refresh_unit_start_io"],
                    "refresh_area_kind": classification["refresh_area_kind"],
                    "refresh_direction": classification["refresh_direction"],
                    "refresh_device_range": classification["refresh_device_range"],
                    "refresh_station_modules": classification["refresh_station_modules"],
                    "source_unit_kind": classification.get("source_unit_kind", ""),
                    "source_unit_name": classification.get("source_unit_name", ""),
                    "source_unit_connection": classification.get("source_unit_connection", ""),
                    "source_unit_slot_number": classification.get("source_unit_slot_number", ""),
                    "source_unit_start_io": classification.get("source_unit_start_io", ""),
                    "source_unit_area": classification.get("source_unit_area", ""),
                    "first_lddb": row.lddb,
                    "first_pos": row.pos,
                    "first_title": row.title,
                    "titles": Counter(),
                },
            )
            rec["occurrences"] += 1
            if occ.role == "a":
                rec["required_on_count"] += 1
            elif occ.role == "b":
                rec["required_off_count"] += 1
            else:
                rec["predicate_count"] += 1
                rec["predicate_roles"][occ.role] += 1
            if row.title:
                rec["titles"][row.title] += 1

    out: list[dict[str, object]] = []
    for rec in usage.values():
        out.append(
            {
                "source_kind": rec["source_kind"],
                "semantic_group": rec["semantic_group"],
                "device": rec["device"],
                "plc_device": rec["plc_device"],
                "device_type": rec["device_type"],
                "number": rec["number"],
                "comment": rec["comment"],
                "occurrences": rec["occurrences"],
                "required_on_count": rec["required_on_count"],
                "required_off_count": rec["required_off_count"],
                "predicate_count": rec["predicate_count"],
                "predicate_roles": "; ".join(f"{k}={v}" for k, v in rec["predicate_roles"].most_common()),
                "source_detail": rec["source_detail"],
                "stop_reason": rec["stop_reason"],
                "refresh_area": rec["refresh_area"],
                "refresh_network_label": rec["refresh_network_label"],
                "refresh_unit_name": rec["refresh_unit_name"],
                "refresh_slot_number": rec["refresh_slot_number"],
                "refresh_unit_start_io": rec["refresh_unit_start_io"],
                "refresh_area_kind": rec["refresh_area_kind"],
                "refresh_direction": rec["refresh_direction"],
                "refresh_device_range": rec["refresh_device_range"],
                "refresh_station_modules": rec["refresh_station_modules"],
                "source_unit_kind": rec["source_unit_kind"],
                "source_unit_name": rec["source_unit_name"],
                "source_unit_connection": rec["source_unit_connection"],
                "source_unit_slot_number": rec["source_unit_slot_number"],
                "source_unit_start_io": rec["source_unit_start_io"],
                "source_unit_area": rec["source_unit_area"],
                "first_lddb": rec["first_lddb"],
                "first_pos": rec["first_pos"],
                "first_title": rec["first_title"],
                "top_titles": " | ".join(title for title, _ in rec["titles"].most_common(3)),
            }
        )
    return sorted(
        out,
        key=lambda row: (
            str(row["source_kind"]),
            str(row["semantic_group"]),
            str(row["device_type"]),
            int(row["number"]),
        ),
    )


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = [
        "source_kind",
        "semantic_group",
        "device",
        "plc_device",
        "device_type",
        "number",
        "comment",
        "occurrences",
        "required_on_count",
        "required_off_count",
        "predicate_count",
        "predicate_roles",
        "source_detail",
        "stop_reason",
        "refresh_area",
        "refresh_network_label",
        "refresh_unit_name",
        "refresh_slot_number",
        "refresh_unit_start_io",
        "refresh_area_kind",
        "refresh_direction",
        "refresh_device_range",
        "refresh_station_modules",
        "source_unit_kind",
        "source_unit_name",
        "source_unit_connection",
        "source_unit_slot_number",
        "source_unit_start_io",
        "source_unit_area",
        "first_lddb",
        "first_pos",
        "first_title",
        "top_titles",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def refresh_source_label(row: dict[str, object]) -> str:
    return (
        f"{row.get('refresh_network_label')} / {row.get('refresh_unit_name')} "
        f"slot={row.get('refresh_slot_number')} "
        f"{row.get('refresh_area_kind')} {row.get('refresh_direction')} "
        f"{row.get('refresh_device_range')}"
    ).strip()


def configured_refresh_label(area: RefreshArea) -> str:
    return (
        f"{area.network_label} / {area.unit_name} slot={area.slot_number} "
        f"{area.area_kind} {area.direction} {area.start_text}..{area.end_text}"
    ).strip()


def unit_source_label(row: dict[str, object]) -> str:
    connection = str(row.get("source_unit_connection") or "").strip()
    unit = str(row.get("source_unit_name") or "").strip()
    unit_text = " ".join(part for part in [connection, unit] if part)
    slot = str(row.get("source_unit_slot_number") or "").strip()
    slot_text = f"slot={slot}" if slot else ""
    return " ".join(
        part
        for part in [
            str(row.get("source_unit_kind") or "").strip(),
            "/",
            unit_text,
            slot_text,
            str(row.get("source_unit_area") or "").strip(),
        ]
        if part
    ).strip()


def render_markdown(
    rows: list[dict[str, object]],
    max_examples: int,
    refresh_areas: list[RefreshArea] | None = None,
) -> str:
    refresh_areas = refresh_areas or []
    by_source = Counter(str(row["source_kind"]) for row in rows)
    by_semantic = Counter(str(row["semantic_group"]) for row in rows)
    by_refresh = Counter(
        refresh_source_label(row)
        for row in rows
        if row.get("refresh_area")
    )
    by_unit_source = Counter(
        unit_source_label(row)
        for row in rows
        if row.get("source_unit_kind")
    )
    lines = [
        "# External / Terminal Contact Inputs",
        "",
        "These contacts are trace boundaries: they are not driven by an internal PLC ladder coil in the parsed project.",
        "Explain them as real-world input, remote station signal, HMI/setting data, communication data, or hard constant instead of recursively expanding them.",
        "",
        "## Counts By Source Kind",
    ]
    for name, count in by_source.most_common():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Counts By Semantic Group"])
    for name, count in by_semantic.most_common():
        lines.append(f"- {name}: {count}")
    lines.extend(["", "## Counts By Refresh Source"])
    if by_refresh:
        for name, count in by_refresh.most_common():
            lines.append(f"- {name}: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Counts By Unit Source"])
    if by_unit_source:
        for name, count in by_unit_source.most_common():
            lines.append(f"- {name}: {count}")
    else:
        lines.append("- none")
    lines.extend(["", "## Configured Refresh Areas"])
    if refresh_areas:
        for area in refresh_areas:
            station = f" station_modules={area.station}" if area.station else ""
            lines.append(f"- {configured_refresh_label(area)}{station}")
    else:
        lines.append("- none")
    lines.extend(["", "## Mapped Devices By Refresh Source"])
    mapped_by_source: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if row.get("refresh_area"):
            mapped_by_source[refresh_source_label(row)].append(row)
    if mapped_by_source:
        for label, group_rows in sorted(
            mapped_by_source.items(),
            key=lambda item: (-len(item[1]), item[0]),
        ):
            lines.extend(["", f"### {label}"])
            station_modules = next(
                (str(row.get("refresh_station_modules")) for row in group_rows if row.get("refresh_station_modules")),
                "",
            )
            if station_modules:
                lines.append(f"- station modules: {station_modules}")
            for row in sorted(
                group_rows,
                key=lambda item: (str(item.get("device_type")), int(item.get("number", 0))),
            ):
                state = []
                if int(row["required_on_count"]):
                    state.append(f"ON refs={row['required_on_count']}")
                if int(row["required_off_count"]):
                    state.append(f"OFF refs={row['required_off_count']}")
                if int(row.get("predicate_count", 0)):
                    roles = f" {row['predicate_roles']}" if row.get("predicate_roles") else ""
                    state.append(f"predicate refs={row['predicate_count']}{roles}")
                lines.append(
                    f"- `{row.get('plc_device') or row['device']}` {row.get('comment') or ''} "
                    f"(raw={row['device']}) [{row['semantic_group']}] "
                    f"occ={row['occurrences']} {', '.join(state)}"
                )
    else:
        lines.append("- none")
    lines.extend(["", "## Mapped Devices By Unit Source"])
    mapped_by_unit_source: dict[str, list[dict[str, object]]] = defaultdict(list)
    for row in rows:
        if row.get("source_unit_kind"):
            mapped_by_unit_source[unit_source_label(row)].append(row)
    if mapped_by_unit_source:
        for label, group_rows in sorted(
            mapped_by_unit_source.items(),
            key=lambda item: (-len(item[1]), item[0]),
        ):
            lines.extend(["", f"### {label}"])
            for row in sorted(
                group_rows,
                key=lambda item: (str(item.get("device_type")), int(item.get("number", 0))),
            ):
                state = []
                if int(row["required_on_count"]):
                    state.append(f"ON refs={row['required_on_count']}")
                if int(row["required_off_count"]):
                    state.append(f"OFF refs={row['required_off_count']}")
                if int(row.get("predicate_count", 0)):
                    roles = f" {row['predicate_roles']}" if row.get("predicate_roles") else ""
                    state.append(f"predicate refs={row['predicate_count']}{roles}")
                lines.append(
                    f"- `{row.get('plc_device') or row['device']}` {row.get('comment') or ''} "
                    f"(raw={row['device']}) [{row['semantic_group']}] "
                    f"occ={row['occurrences']} {', '.join(state)}"
                )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Usage Rule",
            "- `internal_logic`: recurse upstream.",
            "- `constant_false` or logic `FALSE`: stop; that branch is disabled.",
            "- other source kinds: stop recursion and explain the required ON/OFF state as field/HMI/communication condition.",
            "",
            "## Representative Devices",
        ]
    )
    for source_kind, _ in by_source.most_common():
        lines.extend(["", f"### {source_kind}"])
        source_rows = [row for row in rows if row["source_kind"] == source_kind]
        source_rows.sort(key=lambda row: -int(row["occurrences"]))
        for row in source_rows[:max_examples]:
            state = []
            if int(row["required_on_count"]):
                state.append(f"ON refs={row['required_on_count']}")
            if int(row["required_off_count"]):
                state.append(f"OFF refs={row['required_off_count']}")
            if int(row.get("predicate_count", 0)):
                roles = f" {row['predicate_roles']}" if row.get("predicate_roles") else ""
                state.append(f"predicate refs={row['predicate_count']}{roles}")
            area = f" area={row['refresh_area']}" if row.get("refresh_area") else ""
            unit = ""
            if row.get("refresh_unit_name"):
                unit = (
                    f" unit={row.get('refresh_unit_name')}"
                    f" slot={row.get('refresh_slot_number')}"
                )
            lines.append(
                f"- `{row.get('plc_device') or row['device']}` {row.get('comment') or ''} "
                f"(raw={row['device']}) "
                f"[{row['semantic_group']}] occ={row['occurrences']} "
                f"{', '.join(state)}{area}{unit}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract external/terminal contact inputs from a project.")
    comm_prefix = default_comm_prefix()
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--output-dir", default="outputs", help="output directory")
    parser.add_argument("--prefix", default=default_output_prefix("external_inputs"), help="output filename prefix")
    parser.add_argument("--refresh-csv", default=f"{comm_prefix}_refresh_areas.csv", help="communication refresh area CSV")
    parser.add_argument("--unit-csv", default=f"{comm_prefix}_units.csv", help="communication/unit configuration CSV")
    parser.add_argument("--max-examples", type=int, default=20, help="examples per source kind in Markdown")
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    output_dir = Path(args.output_dir)
    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)
    refresh_areas = load_refresh_areas(Path(args.refresh_csv))
    unit_io_areas = load_unit_io_areas(Path(args.unit_csv))
    external_rows = collect_external_inputs(rows, comments, refresh_areas, unit_io_areas)

    csv_path = output_dir / f"{args.prefix}.csv"
    md_path = output_dir / f"{args.prefix}.md"
    manifest_path = output_dir / f"{args.prefix}_manifest.json"
    write_csv(csv_path, external_rows)
    md_path.write_text(render_markdown(external_rows, args.max_examples, refresh_areas) + "\n", encoding="utf-8")
    manifest = {
        "root": str(root),
        "outputs": {"csv": str(csv_path), "markdown": str(md_path), "manifest": str(manifest_path)},
        "counts": {
            "external_or_terminal_contacts": len(external_rows),
            "by_source_kind": dict(Counter(str(row["source_kind"]) for row in external_rows)),
            "by_semantic_group": dict(Counter(str(row["semantic_group"]) for row in external_rows)),
            "by_refresh_source": dict(
                Counter(
                    refresh_source_label(row)
                    for row in external_rows
                    if row.get("refresh_area")
                )
            ),
            "by_unit_source": dict(
                Counter(
                    unit_source_label(row)
                    for row in external_rows
                    if row.get("source_unit_kind")
                )
            ),
            "configured_refresh_areas": len(refresh_areas),
            "configured_unit_io_areas": len(unit_io_areas),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"external input report written: {md_path}")
    print(f"csv: {csv_path}")
    print(f"manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
