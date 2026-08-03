from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gx3cli.review_gx3_project import (
    LadderRow,
    comment_for_device,
    load_comments_for_root,
    load_rows,
)
from gx3cli.trace_gx3_device_dependencies import driver_index
from gx3cli.gx3_ladder_logic import enable_logic_for_output, logic_to_text, output_elements_for
from gx3cli.gx3_external_inputs import (
    collect_external_inputs,
    load_refresh_areas,
    load_unit_io_areas,
    plc_device_text,
    render_markdown as render_external_inputs_markdown,
)
from gx3cli.gx3_cli import module_argv, python_env
from gx3cli.gx3_project_paths import default_comm_prefix, default_output_prefix, default_project_root, find_comment_db


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_ROOT = default_project_root()
MOJIBAKE_MARKERS = ("â", "ã", "Ã", "ï")
MOJIBAKE_REPLACEMENTS = {
    "\u00e2\u2013\u00bd": "\u25bd",
    "\u00e2\u2020\u2018": "\u2191",
    "\u00e2\u02dc\u2020": "\u2606",
    "\u00e2\u02dc\u2026": "\u2605",
}


def display_text(value: object) -> str:
    text = ("" if value is None else str(value)).replace("\r", " ").replace("\n", " ")
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    if not any(marker in text for marker in MOJIBAKE_MARKERS):
        return text
    for encoding in ("cp1252", "latin1"):
        try:
            repaired = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        if repaired and sum(repaired.count(marker) for marker in MOJIBAKE_MARKERS) < sum(
            text.count(marker) for marker in MOJIBAKE_MARKERS
        ):
            return repaired
    return text


@dataclass(frozen=True)
class OutputSet:
    index: Path
    compact_context: Path
    foundation: Path
    equipment: Path
    device_map_csv: Path
    device_map_md: Path
    ladder_toc_csv: Path
    ladder_toc_md: Path
    important_conditions: Path
    equipment_memo: Path
    external_inputs_csv: Path
    external_inputs_md: Path
    manifest: Path


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


def run_source_reports(root: Path, review_prefix: str) -> None:
    commands = [
        ["extract_comm_refresh_areas"],
        ["extract_hmi_build_info"],
        ["extract_used_devices_without_comments"],
        ["extract_gx3_extended_instruction_knowledge"],
        ["review_gx3_project", str(root), "--prefix", review_prefix],
    ]
    for command in commands:
        completed = subprocess.run(
            module_argv(command[0], command[1:]),
            cwd=BASE_DIR,
            env=python_env(str(root)),
            check=False,
        )
        if completed.returncode != 0:
            raise SystemExit(f"source report command failed: {' '.join(command)}")


def sqlite_table_count(path: Path, table: str) -> int:
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        value = con.execute(f"select count(*) from {table}").fetchone()[0]
        con.close()
        return int(value)
    except sqlite3.Error:
        return 0


def collect_foundation(root: Path, rows: list[LadderRow]) -> dict[str, Any]:
    parse_counts = Counter(row.parse_status for row in rows)
    lddb_paths = sorted(root.glob("*_LDDB.db"))
    stepinfo_paths = sorted(root.glob("*_StepInfo.db"))
    db_paths = sorted(root.glob("*.db"))
    comment_db = find_comment_db(root)
    comment_devices = sqlite_table_count(comment_db, "DEVICE_DATA") if comment_db else 0
    comment_rows = sqlite_table_count(comment_db, "COMMENT_DATA") if comment_db else 0
    return {
        "root": str(root),
        "ladder_rows": len(rows),
        "lddb_count": len(lddb_paths),
        "stepinfo_count": len(stepinfo_paths),
        "db_count": len(db_paths),
        "comment_devices": comment_devices,
        "comment_rows": comment_rows,
        "parse_counts": dict(parse_counts),
        "files": {
            "UnitConfig.dat": (root / "UnitConfig.dat").exists(),
            "UNIT.PRM": (root / "UNIT.PRM").exists(),
            "SYSTEM.PRM": (root / "SYSTEM.PRM").exists(),
            "CPU.PRM": (root / "CPU.PRM").exists(),
            "LabelData.db": (root / "LabelData.db").exists(),
        },
    }


DEVICE_CLASS_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("abnormal_alarm", re.compile(r"異常|NG|T\.O|timeout|alarm|error", re.IGNORECASE)),
    ("auto_operation", re.compile(r"自動|auto|サイクル|cycle", re.IGNORECASE)),
    ("manual_single_action", re.compile(r"手動|単動|manual|jog|single", re.IGNORECASE)),
    ("origin_home", re.compile(r"原点|原位置|home|origin", re.IGNORECASE)),
    ("safety_interlock", re.compile(r"非常|安全|扉|ドア|カバー|ロック|元圧|電源|safe|door|cover|lock", re.IGNORECASE)),
    ("io_sensor_output", re.compile(r"端|センサ|PB|SW|シリンダ|モータ|C/V|CV|sensor|button|switch|motor", re.IGNORECASE)),
    ("communication", re.compile(r"通信|送信|受信|MES|SLMP|CC-Link|EtherNet|Ethernet|読出|書込", re.IGNORECASE)),
    ("work_shift_info", re.compile(r"ワーク|ﾜｰｸ|キャリア|ｷｬﾘｱ|シフト|在荷|work|carrier|shift", re.IGNORECASE)),
    ("mode_selection", re.compile(r"モード|選択|mode|select", re.IGNORECASE)),
    ("timer_counter", re.compile(r"タイマ|ﾀｲﾏ|時間|遅延|回数|カウント|count|timer", re.IGNORECASE)),
]


def classify_device(device_type: str, comment: str, roles: Counter[str]) -> str:
    text = comment or ""
    for name, pattern in DEVICE_CLASS_PATTERNS:
        if pattern.search(text):
            return name
    if device_type in {"X", "Y"}:
        return "io_sensor_output"
    if device_type in {"SB", "SW", "W", "B"}:
        return "communication_or_word_area"
    if device_type in {"T", "C"}:
        return "timer_counter"
    if roles.get("c") or roles.get("SET") or roles.get("RST"):
        return "internal_state_or_output"
    return "unclassified"


def build_device_map(
    rows: list[LadderRow],
    comments: dict[tuple[str, int], Any],
) -> list[dict[str, object]]:
    usage: dict[str, dict[str, Any]] = {}
    for row in rows:
        for occ in row.occurrences:
            rec = usage.setdefault(
                occ.device,
                {
                    "device": occ.device,
                    "device_type": occ.device_type,
                    "number": occ.number,
                    "roles": Counter(),
                    "occurrences": 0,
                    "first_lddb": row.lddb,
                    "first_pos": row.pos,
                    "first_title": row.title,
                    "titles": Counter(),
                },
            )
            rec["occurrences"] += 1
            rec["roles"][occ.role] += 1
            if row.title:
                rec["titles"][row.title] += 1

    out: list[dict[str, object]] = []
    for rec in usage.values():
        device_type = str(rec["device_type"])
        number = int(rec["number"])
        comment = comment_for_device(device_type, number, comments)
        roles: Counter[str] = rec["roles"]
        category = classify_device(device_type, comment, roles)
        out.append(
            {
                "category": category,
                "device": rec["device"],
                "plc_device": plc_device_text(device_type, number, str(rec["device"])),
                "device_type": device_type,
                "number": number,
                "comment": comment,
                "occurrences": rec["occurrences"],
                "roles": "; ".join(f"{k}={v}" for k, v in roles.most_common()),
                "first_lddb": rec["first_lddb"],
                "first_pos": rec["first_pos"],
                "first_title": rec["first_title"],
                "top_titles": " | ".join(title for title, _ in rec["titles"].most_common(3)),
            }
        )
    return sorted(out, key=lambda item: (str(item["category"]), str(item["device_type"]), int(item["number"])))


def build_ladder_toc(rows: list[LadderRow]) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row.lddb, row.title or "(no title)")
        rec = grouped.setdefault(
            key,
            {
                "lddb": row.lddb,
                "title": row.title or "(no title)",
                "row_count": 0,
                "first_pos": row.pos,
                "last_pos": row.pos,
                "parse_status": Counter(),
                "drivers": Counter(),
                "contacts": Counter(),
            },
        )
        rec["row_count"] += 1
        rec["first_pos"] = min(rec["first_pos"], row.pos)
        rec["last_pos"] = max(rec["last_pos"], row.pos)
        rec["parse_status"][row.parse_status] += 1
        for occ in row.occurrences:
            if occ.role in {"a", "b"}:
                rec["contacts"][occ.device] += 1
            else:
                rec["drivers"][occ.device] += 1

    out: list[dict[str, object]] = []
    for rec in grouped.values():
        out.append(
            {
                "lddb": rec["lddb"],
                "title": rec["title"],
                "row_count": rec["row_count"],
                "first_pos": rec["first_pos"],
                "last_pos": rec["last_pos"],
                "parse_status": "; ".join(f"{k}={v}" for k, v in rec["parse_status"].most_common()),
                "top_driver_devices": "; ".join(f"{k}={v}" for k, v in rec["drivers"].most_common(8)),
                "top_condition_devices": "; ".join(f"{k}={v}" for k, v in rec["contacts"].most_common(8)),
                "section_kind": classify_section(str(rec["title"])),
            }
        )
    return sorted(out, key=lambda item: (str(item["lddb"]), int(item["first_pos"])))


SECTION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("abnormal", re.compile(r"異常|abnormal|alarm|error", re.IGNORECASE)),
    ("auto_operation", re.compile(r"自動|auto", re.IGNORECASE)),
    ("manual_operation", re.compile(r"手動|単動|manual|JOG", re.IGNORECASE)),
    ("origin_home", re.compile(r"原点|原位置|origin|home", re.IGNORECASE)),
    ("communication", re.compile(r"通信|MES|Ethernet|CC-Link|SLMP", re.IGNORECASE)),
    ("io", re.compile(r"入力|出力|input|output|I/O", re.IGNORECASE)),
    ("work_shift_info", re.compile(r"ワーク|ﾜｰｸ|シフト|shift|work", re.IGNORECASE)),
    ("data", re.compile(r"データ|data|設定|set", re.IGNORECASE)),
    ("operation_ready", re.compile(r"運転準備|準備|ready|preparation", re.IGNORECASE)),
]


def classify_section(title: str) -> str:
    for name, pattern in SECTION_PATTERNS:
        if pattern.search(title):
            return name
    return "other"


IMPORTANT_PATTERNS = re.compile(
    r"運転準備|全運転準備|自動運転開始|自動.*モード|手動.*モード|空運転|原点|原位置|"
    r"非常停止|異常無|リセット|安全|扉|元圧|電源|開始条件|動作可|選択可|モード中|"
    r"Operation preparation|Automatic operation|Manual|Origin|Ready",
    re.IGNORECASE,
)


def build_important_conditions(
    device_map: list[dict[str, object]],
    rows: list[LadderRow],
    max_items: int,
) -> list[dict[str, object]]:
    drivers = driver_index(rows, include_reset=True)
    candidates = [
        item
        for item in device_map
        if IMPORTANT_PATTERNS.search(str(item.get("comment", "")))
        or str(item.get("category")) in {"safety_interlock", "origin_home", "mode_selection"}
    ]
    candidates.sort(key=lambda item: (-safe_int(item.get("occurrences")), str(item.get("device"))))
    out: list[dict[str, object]] = []
    for item in candidates[:max_items]:
        device = str(item["device"])
        on_texts: list[str] = []
        for row in drivers.get(device, [])[:3]:
            for output in output_elements_for(row, device):
                if output.role == "RST":
                    continue
                on_texts.append(logic_to_text(enable_logic_for_output(row, output)))
        out.append(
            {
                **item,
                "driver_row_count": len(drivers.get(device, [])),
                "sample_on_logic": " OR ".join(dict.fromkeys(on_texts[:3])),
            }
        )
    return out


def render_foundation(summary: dict[str, Any]) -> str:
    lines = [
        "# 01 Analysis Foundation",
        "",
        f"- source root: `{summary['root']}`",
        f"- LDDB count: {summary['lddb_count']}",
        f"- StepInfo DB count: {summary['stepinfo_count']}",
        f"- SQLite DB count: {summary['db_count']}",
        f"- ladder rows: {summary['ladder_rows']}",
        f"- comment devices: {summary['comment_devices']}",
        f"- comment rows: {summary['comment_rows']}",
        "",
        "## Parse Status",
    ]
    for key, value in sorted(summary["parse_counts"].items()):
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## Key Files"])
    for key, value in summary["files"].items():
        lines.append(f"- {key}: {'exists' if value else 'missing'}")
    return "\n".join(lines)


def render_equipment(units: list[dict[str, str]], areas: list[dict[str, str]]) -> str:
    lines = ["# 02 Equipment / Unit Configuration", ""]
    if not units:
        lines.append("No communication unit CSV found. Run `python .\\gx3_cli.py comm-refresh` first or pass `--comm-dir/--comm-prefix`.")
    else:
        lines.append("## Units")
        for row in units:
            ip = row.get("parameter_ip_addresses") or row.get("parameter_db_basic_ip_values") or ""
            lines.append(
                f"- object {row.get('object_id')}: {row.get('unit_name')} "
                f"slot={row.get('slot_number')} start_io={row.get('start_io_hex')} "
                f"connection={row.get('connection') or '-'} ip={ip or '-'}"
            )
    lines.append("")
    lines.append("## Refresh / Network Areas")
    if not areas:
        lines.append("- No refresh area CSV found.")
    else:
        for row in areas[:40]:
            lines.append(
                f"- {row.get('network_label')} {row.get('area_kind')} {row.get('direction')}: "
                f"{row.get('device_start')}..{row.get('device_end')} "
                f"unit={row.get('unit_name')} station={row.get('remote_station_module_strings') or '-'}"
            )
        if len(areas) > 40:
            lines.append(f"- ... {len(areas) - 40} more rows")
    return "\n".join(lines)


def render_device_map_md(device_map: list[dict[str, object]]) -> str:
    counts = Counter(str(item["category"]) for item in device_map)
    type_counts = Counter(str(item["device_type"]) for item in device_map)
    role_counts: Counter[str] = Counter()
    for item in device_map:
        for part in str(item.get("roles", "")).split("; "):
            if not part:
                continue
            try:
                role, count = part.rsplit("=", 1)
                role_counts[role] += int(count)
            except ValueError:
                continue
    lines = ["# 03 Device Map", "", "## Device Type Counts"]
    for device_type, count in type_counts.most_common():
        lines.append(f"- {device_type}: {count}")
    lines.extend(["", "## Role Totals"])
    for role, count in role_counts.most_common(30):
        lines.append(f"- {role}: {count}")
    lines.extend(["", "## Category Counts"])
    for category, count in counts.most_common():
        lines.append(f"- {category}: {count}")
    lines.append("")
    lines.append("## Representative Devices")
    for category, _ in counts.most_common():
        lines.append("")
        lines.append(f"### {category}")
        for item in [row for row in device_map if row["category"] == category][:12]:
            plc_device = str(item.get("plc_device") or item["device"])
            raw = f" raw={item['device']}" if plc_device != str(item["device"]) else ""
            lines.append(
                f"- `{plc_device}`{raw} {item.get('comment') or ''} "
                f"occ={item['occurrences']} roles={item['roles']}"
            )
    return "\n".join(lines)


def render_ladder_toc_md(toc: list[dict[str, object]]) -> str:
    counts = Counter(str(item["section_kind"]) for item in toc)
    lines = ["# 04 Ladder Table Of Contents", "", "## Section Counts"]
    for kind, count in counts.most_common():
        lines.append(f"- {kind}: {count}")
    lines.append("")
    lines.append("## Sections")
    for row in toc:
        lines.append(
            f"- `{row['lddb']}:{row['first_pos']}..{row['last_pos']}` "
            f"[{row['section_kind']}] rows={row['row_count']} {row['title']}"
        )
    return "\n".join(lines)


def render_important_conditions(items: list[dict[str, object]]) -> str:
    lines = [
        "# 05 Common Important Conditions",
        "",
        "These are high-leverage devices to understand before purpose-specific analysis.",
        "The sample logic is topology-derived when a driver row was found.",
        "",
    ]
    for item in items:
        lines.append(f"## {item['device']} {item.get('comment') or ''}".rstrip())
        lines.append(f"- category: {item['category']}")
        lines.append(f"- occurrences: {item['occurrences']}")
        lines.append(f"- driver rows: {item['driver_row_count']}")
        if item.get("sample_on_logic"):
            lines.append(f"- sample ON logic: `{item['sample_on_logic']}`")
        lines.append("")
    return "\n".join(lines)


def render_equipment_memo(
    foundation: dict[str, Any],
    units: list[dict[str, str]],
    device_map: list[dict[str, object]],
    toc: list[dict[str, object]],
    important: list[dict[str, object]],
) -> str:
    section_counts = Counter(str(item["section_kind"]) for item in toc)
    device_counts = Counter(str(item["category"]) for item in device_map)
    unit_names = [row.get("unit_name", "") for row in units if row.get("unit_name")]
    lines = [
        "# 06 Equipment Memo",
        "",
        "## Inferred Equipment Character",
        "- This appears to be an automated inspection / transfer machine with VO/IR, negative-can, height, X-ray, discharge, return, and work-shift data handling sections.",
        "- The ladder has explicit areas for operation preparation, auto operation, manual/single-action operation, origin summary, abnormal handling, work shift information, and communication/data handling.",
        "- Network/unit evidence indicates Ethernet, CC IE Field, CC-Link, MES, recorder, and EtherNet/IP related modules.",
        "",
        "## Evidence",
        f"- ladder rows: {foundation['ladder_rows']}",
        f"- LDDB count: {foundation['lddb_count']}",
        f"- units found: {len(units)}",
        f"- unit names: {', '.join(unit_names[:12])}",
        "",
        "## Ladder Section Balance",
    ]
    for kind, count in section_counts.most_common():
        lines.append(f"- {kind}: {count}")
    lines.extend(["", "## Device Area Balance"])
    for kind, count in device_counts.most_common():
        lines.append(f"- {kind}: {count}")
    lines.extend(["", "## First Conditions To Memorize"])
    for item in important[:15]:
        lines.append(f"- `{item['device']}` {item.get('comment') or ''}")
    lines.extend(
        [
            "",
            "## Recommended Next Step",
            "- For abnormal analysis: start from L abnormal flags, inspect ON cause, hold condition, reset condition, related counter/setting registers, and display number linkage.",
            "- For device ON analysis: run `trace-device <device> --strict-logic`, then stop at clear human-level conditions and recursively expand ambiguous devices such as operation-ready, selection-ready, and start-condition bits.",
        ]
    )
    return "\n".join(lines)


def output_set(output_dir: Path, prefix: str) -> OutputSet:
    return OutputSet(
        index=output_dir / f"{prefix}_index.md",
        compact_context=output_dir / f"{prefix}_context_compact.md",
        foundation=output_dir / f"{prefix}_01_analysis_foundation.md",
        equipment=output_dir / f"{prefix}_02_equipment_config.md",
        device_map_csv=output_dir / f"{prefix}_03_device_map.csv",
        device_map_md=output_dir / f"{prefix}_03_device_map.md",
        ladder_toc_csv=output_dir / f"{prefix}_04_ladder_toc.csv",
        ladder_toc_md=output_dir / f"{prefix}_04_ladder_toc.md",
        important_conditions=output_dir / f"{prefix}_05_important_conditions.md",
        equipment_memo=output_dir / f"{prefix}_06_equipment_memo.md",
        external_inputs_csv=output_dir / f"{prefix}_07_external_inputs.csv",
        external_inputs_md=output_dir / f"{prefix}_07_external_inputs.md",
        manifest=output_dir / f"{prefix}_manifest.json",
    )


def render_index(paths: OutputSet) -> str:
    return "\n".join(
        [
            "# Project Survey Index",
            "",
            "Read this first:",
            f"- [{paths.compact_context.name}]({paths.compact_context.name})",
            "",
            "Use CLI/SQLite for details:",
            "- `python .\\gx3_cli.py query-device DEVICE`",
            "- `python .\\gx3_cli.py query-comment TEXT`",
            "- `python .\\gx3_cli.py query-cycle TEXT --start N --end N`",
            "- `python .\\gx3_cli.py query-external TEXT`",
            "- `python .\\gx3_cli.py quick-device DEVICE --ja`",
            "",
            "Detailed tables:",
            f"- [{paths.device_map_csv.name}]({paths.device_map_csv.name})",
            f"- [{paths.ladder_toc_csv.name}]({paths.ladder_toc_csv.name})",
            f"- [{paths.external_inputs_csv.name}]({paths.external_inputs_csv.name})",
            f"- [{paths.manifest.name}]({paths.manifest.name})",
        ]
    )


def render_compact_context(
    foundation: dict[str, Any],
    units: list[dict[str, str]],
    areas: list[dict[str, str]],
    device_map: list[dict[str, object]],
    toc: list[dict[str, object]],
    important: list[dict[str, object]],
    external_inputs: list[dict[str, object]],
) -> str:
    category_counts = Counter(str(row.get("category", "")) for row in device_map)
    section_counts = Counter(str(row.get("section_kind", "")) for row in toc)
    external_counts = Counter(str(row.get("source_kind", "")) for row in external_inputs)
    semantic_counts = Counter(str(row.get("semantic_group", "")) for row in external_inputs)

    lines: list[str] = [
        "# GX3 Compact Context",
        "",
        "## Project",
        f"- root: `{foundation.get('root', '')}`",
        f"- ladder rows: {foundation.get('ladder_rows', 0)}",
        f"- parse status: {foundation.get('parse_status', {})}",
        "",
        "## Read Rules",
        "- Start with this file, then use CLI queries for details.",
        "- Do not read `_ARCHIVE_DELETE_CANDIDATES_*` or `_KEEP_*` unless explicitly requested.",
        "- For ON-condition analysis, prefer `python .\\gx3_cli.py trace-device DEVICE --strict-logic --compact --max-depth 4`.",
        "- Treat constant-FALSE branches as disabled unless explicitly requested.",
        "",
        "## Device Areas",
    ]
    for name, count in category_counts.most_common(8):
        lines.append(f"- {name}: {count}")

    lines.extend(["", "## Ladder Sections"])
    for name, count in section_counts.most_common(8):
        lines.append(f"- {name}: {count}")

    lines.extend(["", "## Equipment / Communication"])
    lines.append(f"- configured units: {len(units)}")
    lines.append(f"- refresh areas: {len(areas)}")
    for row in areas[:5]:
        label = display_text(row.get("network_label") or row.get("unit_name") or row.get("module_name") or "area")
        device_range = display_text(row.get("device_range") or row.get("device") or "")
        area_kind = display_text(row.get("area_kind") or row.get("kind") or "")
        lines.append(f"- {label}: {area_kind} {device_range}".rstrip())
    if len(areas) > 5:
        lines.append(f"- ... {len(areas) - 5} more refresh areas")

    lines.extend(["", "## Important Condition Devices"])
    for row in important[:10]:
        device = row.get("device", "")
        comment = display_text(row.get("comment", ""))
        category = display_text(row.get("category", ""))
        occurrences = row.get("occurrences", "")
        lines.append(f"- `{device}` {comment} ({category}, occurrences={occurrences})".rstrip())

    lines.extend(["", "## External / Boundary Inputs"])
    for name, count in external_counts.most_common(6):
        lines.append(f"- {name}: {count}")
    if semantic_counts:
        lines.append("")
        lines.append("Semantic groups:")
        for name, count in semantic_counts.most_common(6):
            lines.append(f"- {name}: {count}")

    lines.extend(
        [
            "",
            "## Detailed Files",
            "- `<prefix>_index.md`: ordered survey outputs",
            "- `<prefix>_03_device_map.csv`: device map details",
            "- `<prefix>_04_ladder_toc.csv`: ladder section details",
            "- `<prefix>_07_external_inputs.csv`: external/HMI/communication boundaries",
        ]
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a project survey package in the recommended analysis order.")
    parser.add_argument("--root", default=str(DEFAULT_ROOT), help="extracted project folder")
    parser.add_argument("--output-dir", default="outputs", help="directory for survey outputs")
    parser.add_argument("--prefix", default=default_output_prefix("survey"), help="output filename prefix")
    parser.add_argument("--review-prefix", default=default_output_prefix("review"), help="prefix used if refreshing source review reports")
    parser.add_argument("--important-limit", type=int, default=80, help="maximum common condition devices to summarize")
    parser.add_argument("--refresh-source-reports", action="store_true", help="run existing source report generators first")
    parser.add_argument("--comm-dir", default=".", help="directory containing communication CSV reports")
    parser.add_argument("--comm-prefix", default=default_comm_prefix(), help="communication CSV prefix")
    parser.add_argument(
        "--compact-md-only",
        action="store_true",
        help="write only compact/index Markdown; keep detailed information in CSV/SQLite",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    if args.refresh_source_reports:
        run_source_reports(root, args.review_prefix)

    output_dir = Path(args.output_dir)
    paths = output_set(output_dir, args.prefix)
    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)
    foundation = collect_foundation(root, rows)

    comm_dir = Path(args.comm_dir)
    units_csv = comm_dir / f"{args.comm_prefix}_units.csv"
    refresh_csv = comm_dir / f"{args.comm_prefix}_refresh_areas.csv"
    units = read_csv(units_csv)
    areas = read_csv(refresh_csv)
    refresh_areas = load_refresh_areas(refresh_csv)
    unit_io_areas = load_unit_io_areas(units_csv)
    device_map = build_device_map(rows, comments)
    toc = build_ladder_toc(rows)
    important = build_important_conditions(device_map, rows, args.important_limit)
    external_inputs = collect_external_inputs(rows, comments, refresh_areas, unit_io_areas)

    write_text(paths.compact_context, render_compact_context(foundation, units, areas, device_map, toc, important, external_inputs))
    if not args.compact_md_only:
        write_text(paths.foundation, render_foundation(foundation))
        write_text(paths.equipment, render_equipment(units, areas))
    write_csv(
        paths.device_map_csv,
        device_map,
        [
            "category",
            "device",
            "plc_device",
            "device_type",
            "number",
            "comment",
            "occurrences",
            "roles",
            "first_lddb",
            "first_pos",
            "first_title",
            "top_titles",
        ],
    )
    if not args.compact_md_only:
        write_text(paths.device_map_md, render_device_map_md(device_map))
    write_csv(
        paths.ladder_toc_csv,
        toc,
        [
            "lddb",
            "title",
            "section_kind",
            "row_count",
            "first_pos",
            "last_pos",
            "parse_status",
            "top_driver_devices",
            "top_condition_devices",
        ],
    )
    if not args.compact_md_only:
        write_text(paths.ladder_toc_md, render_ladder_toc_md(toc))
        write_text(paths.important_conditions, render_important_conditions(important))
        write_text(paths.equipment_memo, render_equipment_memo(foundation, units, device_map, toc, important))
    write_csv(
        paths.external_inputs_csv,
        external_inputs,
        [
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
        ],
    )
    if not args.compact_md_only:
        write_text(paths.external_inputs_md, render_external_inputs_markdown(external_inputs, 20, refresh_areas))
    write_text(paths.index, render_index(paths))

    manifest = {
        "root": str(root),
        "outputs": {key: str(value) for key, value in paths.__dict__.items()},
        "counts": {
            "ladder_rows": len(rows),
            "device_map_rows": len(device_map),
            "ladder_toc_rows": len(toc),
            "important_conditions": len(important),
            "external_inputs": len(external_inputs),
            "units": len(units),
            "refresh_areas": len(areas),
        },
    }
    write_text(paths.manifest, json.dumps(manifest, ensure_ascii=False, indent=2))

    print(f"survey package written: {paths.index}")
    for key, value in paths.__dict__.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
