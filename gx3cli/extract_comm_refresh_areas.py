from __future__ import annotations

import csv
import argparse
import re
import sqlite3
import struct
from collections import Counter
from pathlib import Path

from gx3cli.gx3_project_paths import default_comm_prefix, default_project_root, find_comment_db
from gx3cli.gx3_device_name import device_radix

ROOT = default_project_root()
UNIT_CONFIG = ROOT / "UnitConfig.dat"
COMMENT_DB = find_comment_db(ROOT) or ROOT / "_comments_DC.db"
COMM_PREFIX = default_comm_prefix()

OUT_UNITS = Path(f"{COMM_PREFIX}_units.csv")
OUT_AREAS = Path(f"{COMM_PREFIX}_refresh_areas.csv")
OUT_HINTS = Path(f"{COMM_PREFIX}_device_comment_hints.csv")
OUT_SLMP = Path(f"{COMM_PREFIX}_ethernet_slmp_device_candidates.csv")
OUT_SUMMARY = Path(f"{COMM_PREFIX}_refresh_area_summary.txt")

DEVICE_CODE_NAMES = {
    1: "M",
    2: "SM",
    3: "L",
    16: "X",
    17: "Y",
    20: "B",
    32: "D",
    33: "SD",
    35: "ZR",
    40: "W",
    48: "R",
    49: "SW",
    66: "T",
}

REFRESH_ROLE_BY_INDEX = {
    0: ("SB", "link_special_relay", "diagnostic/status/control"),
    1: ("SW", "link_special_register", "diagnostic/status/control"),
    2: ("X", "remote_input_RX", "incoming_to_plc"),
    3: ("Y", "remote_output_RY", "outgoing_from_plc"),
    4: ("W", "remote_register_RWr", "incoming_to_plc"),
    5: ("W", "remote_register_RWw", "outgoing_from_plc"),
}


def ip_from_blob(value: bytes | None) -> str:
    if not value or len(value) < 4:
        return ""
    if value[:4] == b"\x00\x00\x00\x00":
        return ""
    return ".".join(str(b) for b in value[:4])


def decode_utf16_view_array(value: bytes | None) -> str:
    if not value:
        return ""
    out: list[str] = []
    pos = 0
    while pos + 4 <= len(value):
        length = struct.unpack_from("<I", value, pos)[0]
        pos += 4
        byte_len = length * 2
        if length <= 0 or pos + byte_len > len(value):
            break
        out.append(value[pos : pos + byte_len].decode("utf-16le", errors="replace"))
        pos += byte_len
    return " / ".join(out)


def read_unit_config() -> dict[int, dict[str, object]]:
    con = sqlite3.connect(UNIT_CONFIG)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    units: dict[int, dict[str, object]] = {}
    for row in cur.execute("select * from Object order by ObjectID").fetchall():
        units[row["ObjectID"]] = {
            "object_id": row["ObjectID"],
            "unit_name": row["ObjectName"],
            "connection": "",
        }

    for row in cur.execute("select * from Unit").fetchall():
        if row["ObjectID"] not in units:
            continue
        units[row["ObjectID"]].update(
            {
                "base_object_id": row["ObjectIDOfBaseUnit"],
                "slot_number": row["SlotNumber"],
                "io_number": row["IONumber"],
                "io_occupation": row["IOOccupation"],
            }
        )

    for row in cur.execute("select * from NetworkUnit").fetchall():
        if row["ObjectID"] not in units:
            continue
        units[row["ObjectID"]].update(
            {
                "network_station_type": row["StationType"],
                "network_station_number": row["StationNumber"],
                "network_mode": row["Mode"],
            }
        )

    for row in cur.execute("select * from PropertyConnectionPoint").fetchall():
        if row["ObjectID"] not in units:
            continue
        units[row["ObjectID"]]["connection"] = decode_utf16_view_array(
            row["ConnectionTypeViewArray"]
        )

    param_rows = cur.execute("select * from ParameterUnit order by ObjectID, Identificationkey").fetchall()
    per_object: dict[int, list[sqlite3.Row]] = {}
    for row in param_rows:
        per_object.setdefault(row["ObjectID"], []).append(row)

    for object_id, rows in per_object.items():
        if object_id not in units:
            continue
        first = rows[0]
        ip_list = [ip_from_blob(r["IPAddressArray"]) for r in rows if ip_from_blob(r["IPAddressArray"])]
        units[object_id].update(
            {
                "start_io": first["StartIO"],
                "io_point": first["IOPoint"],
                "unit_nw_type": first["UnitNWType"],
                "network_no": first["NetworkNo"],
                "group_no": first["GroupNo"],
                "station_no": first["StationNo"],
                "mode_settings": first["ModeSettings"],
                "occupied_station": first["OccupiedStation"],
                "extent_cycle": first["ExtentCycle"],
                "cclink_type": first["CCLinkType"],
                "parameter_ip_addresses": " / ".join(ip_list),
            }
        )

    con.close()
    return units


def read_unit_parameter_dbs() -> dict[tuple[str, int], dict[str, object]]:
    result: dict[tuple[str, int], dict[str, object]] = {}
    skip_fragments = ("_LDDB", "_MilDB", "_StepInfo", "_DC", "_DM")
    for path in sorted(ROOT.glob("*.db")):
        if any(fragment in path.name for fragment in skip_fragments):
            continue
        try:
            con = sqlite3.connect(path)
            cur = con.cursor()
            tables = {r[0] for r in cur.execute("select name from sqlite_master where type='table'").fetchall()}
            if "DeviceInfo" not in tables:
                con.close()
                continue
            info = dict(cur.execute("select Label, Data from DeviceInfo").fetchall())
            model = info.get("DeviceModel", "")
            head_io = int(info.get("_HeadIO", "-1") or -1)

            ip_values: list[str] = []
            if "PARAM_BasicSetting" in tables:
                for data, in cur.execute("select Data from PARAM_BasicSetting").fetchall():
                    if isinstance(data, str) and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}", data):
                        ip_values.append(data)

            result[(model, head_io)] = {
                "parameter_db": path.name,
                "device_model": model,
                "head_io": head_io,
                "base_no": info.get("_BaseNo", ""),
                "slot_no": info.get("_SlotNo", ""),
                "occupancy_point": info.get("_OccupancyPoint", ""),
                "refresh_group": info.get("_RefreshGroup", ""),
                "refresh_group_no": info.get("_RefreshGroupNo", ""),
                "refresh_mode": info.get("_RefreshMode", ""),
                "refresh_group_xy": info.get("_RefreshGroupXY", ""),
                "refresh_group_sync": info.get("_RefreshGroupSync", ""),
                "basic_ip_values": " / ".join(ip_values),
            }
            con.close()
        except sqlite3.Error:
            continue
    return result


def iter_utf16_strings(path: Path) -> list[tuple[int, str]]:
    data = path.read_bytes()
    strings: list[tuple[int, str]] = []
    for start in range(0, len(data) - 2, 2):
        prev = 0 if start < 2 else data[start - 2] | (data[start - 1] << 8)
        if is_printable_utf16(prev):
            continue
        chars: list[str] = []
        pos = start
        while pos + 1 < len(data):
            val = data[pos] | (data[pos + 1] << 8)
            if val == 0:
                break
            if not is_printable_utf16(val):
                break
            chars.append(chr(val))
            pos += 2
        if len(chars) >= 2:
            strings.append((start, "".join(chars)))
    return strings


def iter_utf16_strings_any_alignment(path: Path) -> list[tuple[int, str]]:
    data = path.read_bytes()
    strings: list[tuple[int, str]] = []
    seen: set[tuple[int, str]] = set()
    for start in range(0, len(data) - 2):
        prev = None if start < 2 else data[start - 2] | (data[start - 1] << 8)
        if prev is not None and is_printable_utf16(prev):
            continue
        chars: list[str] = []
        pos = start
        while pos + 1 < len(data):
            val = data[pos] | (data[pos + 1] << 8)
            if val == 0:
                break
            if not is_printable_utf16(val):
                break
            chars.append(chr(val))
            pos += 2
        if len(chars) >= 2:
            item = (start, "".join(chars))
            if item not in seen:
                strings.append(item)
                seen.add(item)
    return strings


def is_printable_utf16(value: int) -> bool:
    return (
        0x20 <= value <= 0x7E
        or 0x3000 <= value <= 0x9FFF
        or 0xFF00 <= value <= 0xFFEF
    )


def find_rj61_object_id(path: Path, object_ids: set[int]) -> int | None:
    data = path.read_bytes()
    needle = "RJ61BT11".encode("utf-16le")
    off = data.find(needle)
    if off < 0:
        return None
    for pos in range(off, min(len(data) - 1, off + 256), 2):
        value = struct.unpack_from("<H", data, pos)[0]
        if value in object_ids:
            return value
    return None


def read_size_after_device_string(path: Path, offset: int, device: str) -> int:
    data = path.read_bytes()
    encoded_len_with_null = len(device.encode("utf-16le")) + 2
    size_offset = offset + encoded_len_with_null + 0x22
    if size_offset + 2 > len(data):
        return 0
    return struct.unpack_from("<H", data, size_offset)[0]


def end_device(start: str, count: int) -> str:
    match = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", start)
    if not match or count <= 0:
        return ""
    prefix, number = match.groups()
    base = device_radix(prefix)
    end_value = int(number, base) + count - 1
    if base == 16:
        return f"{prefix}{end_value:0{len(number)}X}"
    return f"{prefix}{end_value:0{len(number)}d}"


def classify_refresh_device(
    device: str, prefix_index: dict[str, int]
) -> tuple[str, str, str]:
    prefix_match = re.match(r"[A-Z]+", device)
    prefix = prefix_match.group(0) if prefix_match else ""
    prefix_index[prefix] = prefix_index.get(prefix, 0) + 1
    index = prefix_index[prefix]

    if prefix == "SB":
        return "SB", "link_special_relay", "diagnostic/status/control"
    if prefix == "SW":
        return "SW", "link_special_register", "diagnostic/status/control"
    if prefix == "X":
        return "X", "remote_input_RX", "incoming_to_plc"
    if prefix == "Y":
        return "Y", "remote_output_RY", "outgoing_from_plc"
    if prefix in {"W", "D"}:
        if index == 1:
            return prefix, "remote_register_RWr", "incoming_to_plc"
        return prefix, "remote_register_RWw", "outgoing_from_plc"
    if prefix == "B":
        return "B", "link_relay_or_buffer", "diagnostic/status/control"
    return prefix, "unknown", "unknown"


def rj61_w3pa_paths_with_devices() -> list[Path]:
    device_pattern = re.compile(r"^(?:SB|SW|X|Y|W|B|D)[0-9A-F]+$")
    paths: list[Path] = []
    for path in sorted(ROOT.glob("*.w3pa")):
        strings = iter_utf16_strings_any_alignment(path)
        values = [s for _, s in strings]
        if not any(s.startswith("RJ61BT11") for s in values):
            continue
        if any(device_pattern.fullmatch(s) for s in values):
            paths.append(path)
    return paths


def extract_cclink_refresh_areas(units: dict[int, dict[str, object]]) -> list[dict[str, object]]:
    rj61_ids = {object_id for object_id, u in units.items() if u.get("unit_name") == "RJ61BT11"}
    rj61_units = sorted(rj61_ids, key=lambda oid: (units[oid].get("slot_number", 999), oid))
    fallback_file_to_object = {
        path.name: rj61_units[index]
        for index, path in enumerate(rj61_w3pa_paths_with_devices())
        if index < len(rj61_units)
    }
    rows: list[dict[str, object]] = []
    device_pattern = re.compile(r"^(?:SB|SW|X|Y|W|B|D)[0-9A-F]+$")

    for path in sorted(ROOT.glob("*.w3pa")):
        object_id = fallback_file_to_object.get(path.name)
        if object_id is None:
            object_id = find_rj61_object_id(path, rj61_ids)
        if object_id is None:
            continue
        strings = iter_utf16_strings_any_alignment(path)
        devices = [(off, s) for off, s in strings if device_pattern.fullmatch(s)]
        if len(devices) < 2:
            continue
        module_counts = Counter(
            s
            for _, s in strings
            if s.startswith("AJ65") or re.fullmatch(r"R[XY][0-9A-Z]+", s)
        )
        module_summary = "; ".join(f"{name}:{count}" for name, count in sorted(module_counts.items()))
        network_label = f"CCLINK_slot{units[object_id].get('slot_number', '')}_object_{object_id}"
        prefix_index: dict[str, int] = {}

        for index, (off, device) in enumerate(devices):
            expected_prefix, area_kind, direction = classify_refresh_device(device, prefix_index)
            size = read_size_after_device_string(path, off, device)
            prefix = re.match(r"[A-Z]+", device).group(0) if re.match(r"[A-Z]+", device) else ""
            rows.append(
                {
                    "object_id": object_id,
                    "network_label": network_label,
                    "unit_name": units[object_id]["unit_name"],
                    "base_object_id": units[object_id].get("base_object_id", ""),
                    "slot_number": units[object_id].get("slot_number", ""),
                    "unit_start_io": units[object_id].get("start_io", ""),
                    "area_kind": area_kind,
                    "direction": direction,
                    "device_start": device,
                    "device_end": end_device(device, size),
                    "points_or_words": size,
                    "device_prefix": prefix,
                    "expected_prefix": expected_prefix,
                    "evidence_file": path.name,
                    "evidence_offset_hex": f"0x{off:X}",
                    "confidence": (
                        "high_string_evidence_manual_format_inference"
                        if prefix in {"SB", "SW", "X", "Y", "W"}
                        else "medium_string_evidence_module_format_inference"
                    ),
                    "remote_station_module_strings": module_summary,
                }
            )
    return rows


def read_comment_hints() -> list[dict[str, object]]:
    global COMMENT_DB
    if not COMMENT_DB.exists():
        COMMENT_DB = find_comment_db(ROOT) or COMMENT_DB
    if not COMMENT_DB.exists():
        return []
    con = sqlite3.connect(COMMENT_DB)
    cur = con.cursor()
    rows: list[dict[str, object]] = []
    wanted = {
        20: [(0, 8), (128, 136), (256, 264), (512, 523), (640, 653)],
        40: [(0, 12), (6800, 6810), (7300, 7312), (7800, 7812), (8000, 8010), (8400, 8412), (8500, 8512)],
        49: [(128, 129), (640, 641)],
    }
    for dev_code, ranges in wanted.items():
        dev_name = DEVICE_CODE_NAMES.get(dev_code, str(dev_code))
        for start, end in ranges:
            result = cur.execute(
                """
                select d.DevNoLow, d.BitNo, c.CmtNo, c.CmtData
                from DEVICE_DATA d
                join COMMENT_DATA c on c.DeviceSEQ = d.SEQ
                where d.DevCode = ?
                  and d.DevNoLow >= ?
                  and d.DevNoLow < ?
                  and coalesce(c.DelFlag, 0) = 0
                  and coalesce(c.CmtData, '') <> ''
                order by d.DevNoLow, d.BitNo, c.CmtNo
                limit 40
                """,
                (dev_code, start, end),
            ).fetchall()
            for dev_no, bit_no, cmt_no, cmt_data in result:
                rows.append(
                    {
                        "device_type": dev_name,
                        "device": f"{dev_name}{dev_no}" + (f".{bit_no}" if bit_no else ""),
                        "device_no_raw": dev_no,
                        "bit_no": bit_no,
                        "comment_no": cmt_no,
                        "comment": cmt_data,
                        "source": COMMENT_DB.name,
                    }
                )
    con.close()
    return rows


def extract_ethernet_slmp_candidates() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    device_pattern = re.compile(r"^(?:B|X|Y|W)[0-9A-F]+$")
    section_markers = {"Ethernet通信インタフェース情報セクション", "SLMP接続機器", "EthernetPort"}
    strong_markers = {"Ethernet通信インタフェース情報セクション", "EthernetPort", "GT27-SVGA"}
    for path in sorted(ROOT.glob("*.w3pa")):
        strings = iter_utf16_strings_any_alignment(path)
        string_values = [s for _, s in strings]
        if not any(marker in string_values for marker in strong_markers):
            continue
        devices = [(off, s) for off, s in strings if device_pattern.fullmatch(s)]
        if not devices:
            continue
        marker_summary = "; ".join(
            f"{name}:{count}"
            for name, count in sorted(Counter(s for s in string_values if s in section_markers or s.startswith("GT")).items())
        )
        for off, device in devices:
            rows.append(
                {
                    "source_type": "ethernet_slmp_candidate",
                    "device": device,
                    "direction": "unknown_external_slmp_access_candidate",
                    "points_or_words": "",
                    "evidence_file": path.name,
                    "evidence_offset_hex": f"0x{off:X}",
                    "section_or_peer_strings": marker_summary,
                    "confidence": "medium_string_evidence_size_not_decoded",
                    "note": "Found near Ethernet communication interface / SLMP connection device strings; size and read/write direction are not decoded.",
                }
            )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract communication units and refresh areas.")
    parser.add_argument("--root", default=str(ROOT), help="extracted project folder")
    parser.add_argument("--output-dir", default=".", help="output directory")
    parser.add_argument("--prefix", default=default_comm_prefix(), help="output prefix")
    return parser


def configure_paths(root: Path, output_dir: Path, prefix: str) -> None:
    global ROOT, UNIT_CONFIG, COMMENT_DB, OUT_UNITS, OUT_AREAS, OUT_HINTS, OUT_SLMP, OUT_SUMMARY
    ROOT = root
    UNIT_CONFIG = ROOT / "UnitConfig.dat"
    COMMENT_DB = find_comment_db(ROOT) or ROOT / "_comments_DC.db"
    output_dir.mkdir(parents=True, exist_ok=True)
    OUT_UNITS = output_dir / f"{prefix}_units.csv"
    OUT_AREAS = output_dir / f"{prefix}_refresh_areas.csv"
    OUT_HINTS = output_dir / f"{prefix}_device_comment_hints.csv"
    OUT_SLMP = output_dir / f"{prefix}_ethernet_slmp_device_candidates.csv"
    OUT_SUMMARY = output_dir / f"{prefix}_refresh_area_summary.txt"


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    configure_paths(Path(args.root), Path(args.output_dir), args.prefix)
    units = read_unit_config()
    param_dbs = read_unit_parameter_dbs()

    unit_rows: list[dict[str, object]] = []
    for object_id, unit in sorted(units.items()):
        name = str(unit.get("unit_name", ""))
        connection = str(unit.get("connection", ""))
        is_comm = (
            object_id == 1
            or "RJ71" in name
            or "RJ61" in name
            or "RD81" in name
            or "Ethernet" in connection
            or "CC" in connection
        )
        if not is_comm:
            continue
        start_value = unit["start_io"] if "start_io" in unit else unit.get("io_number", -1)
        start_io = int(start_value) if start_value not in (None, "") else -1
        param = param_dbs.get((name.replace("_", "").replace("(CCIEF)", "").replace("(E+E)", ""), start_io), {})
        if not param:
            param = next(
                (
                    db
                    for (model, head_io), db in param_dbs.items()
                    if model == name and head_io == start_io
                ),
                {},
            )
        unit_rows.append(
            {
                "object_id": object_id,
                "unit_name": name,
                "connection": connection,
                "base_object_id": unit.get("base_object_id", ""),
                "slot_number": unit.get("slot_number", ""),
                "start_io_dec": start_io if start_io >= 0 else "",
                "start_io_hex": f"0x{start_io:X}" if start_io >= 0 else "",
                "io_points": unit.get("io_point", unit.get("io_occupation", "")),
                "unit_nw_type": unit.get("unit_nw_type", ""),
                "network_no": unit.get("network_no", ""),
                "station_no": unit.get("station_no", ""),
                "mode_settings": unit.get("mode_settings", ""),
                "occupied_station": unit.get("occupied_station", ""),
                "extent_cycle": unit.get("extent_cycle", ""),
                "cclink_type": unit.get("cclink_type", ""),
                "parameter_ip_addresses": unit.get("parameter_ip_addresses", ""),
                "parameter_db": param.get("parameter_db", ""),
                "parameter_db_basic_ip_values": param.get("basic_ip_values", ""),
                "refresh_group": param.get("refresh_group", ""),
                "refresh_group_no": param.get("refresh_group_no", ""),
                "refresh_mode": param.get("refresh_mode", ""),
            }
        )

    area_rows = extract_cclink_refresh_areas(units)
    hint_rows = read_comment_hints()
    slmp_rows = extract_ethernet_slmp_candidates()

    write_csv(
        OUT_UNITS,
        unit_rows,
        [
            "object_id",
            "unit_name",
            "connection",
            "base_object_id",
            "slot_number",
            "start_io_dec",
            "start_io_hex",
            "io_points",
            "unit_nw_type",
            "network_no",
            "station_no",
            "mode_settings",
            "occupied_station",
            "extent_cycle",
            "cclink_type",
            "parameter_ip_addresses",
            "parameter_db",
            "parameter_db_basic_ip_values",
            "refresh_group",
            "refresh_group_no",
            "refresh_mode",
        ],
    )
    write_csv(
        OUT_AREAS,
        area_rows,
        [
            "object_id",
            "network_label",
            "unit_name",
            "base_object_id",
            "slot_number",
            "unit_start_io",
            "area_kind",
            "direction",
            "device_start",
            "device_end",
            "points_or_words",
            "device_prefix",
            "expected_prefix",
            "evidence_file",
            "evidence_offset_hex",
            "confidence",
            "remote_station_module_strings",
        ],
    )
    write_csv(
        OUT_HINTS,
        hint_rows,
        ["device_type", "device", "device_no_raw", "bit_no", "comment_no", "comment", "source"],
    )
    write_csv(
        OUT_SLMP,
        slmp_rows,
        [
            "source_type",
            "device",
            "direction",
            "points_or_words",
            "evidence_file",
            "evidence_offset_hex",
            "section_or_peer_strings",
            "confidence",
            "note",
        ],
    )

    incoming = [
        r
        for r in area_rows
        if r["direction"] == "incoming_to_plc" or r["area_kind"] in ("link_special_register", "link_special_relay")
    ]
    lines = [
        "Communication refresh area summary / 通信リフレッシュエリア調査",
        "===============================================================",
        "",
        f"Source folder: {ROOT}",
        f"Unit config: {UNIT_CONFIG}",
        f"Generated CSV: {OUT_UNITS}, {OUT_AREAS}, {OUT_HINTS}, {OUT_SLMP}",
        "",
        "Main findings / 要点",
        "--------------------",
        "- UnitConfig.dat はSQLite。ユニット名、スロット、先頭I/O、接続種別、IPの一部を取得できる。",
        "- RJ61BT11のCC-Linkリフレッシュデバイス文字列はw3pa内にある。形式は完全デコードではないが、デバイス文字列と近傍の点数ワードが整合している。",
        "- X/Y/B/W/SB/SW系の終了アドレスはGX表示に合わせて16進進みとして計算した。",
        "- RJ71EIP91はユニットDBからIP/basic parameterは取れるが、この抽出ではEtherNet/IPの直接B/Wリフレッシュ割付は見つかっていない。",
        "- Ethernet/SLMP/GOTらしい設定はw3pa内の通信セクション文字列とデバイス文字列から候補抽出する。サイズ・方向が未デコードのものは候補扱い。",
        "",
        "Communication units / 通信系ユニット",
        "------------------------------------",
    ]
    for r in unit_rows:
        ip_text = r["parameter_ip_addresses"] or r["parameter_db_basic_ip_values"] or "-"
        lines.append(
            f"- Object {r['object_id']}: {r['unit_name']}, connection={r['connection']}, "
            f"base={r['base_object_id']}, slot={r['slot_number']}, start_io={r['start_io_dec']} ({r['start_io_hex']}), "
            f"IPs={ip_text}, param_db={r['parameter_db'] or '-'}"
        )

    lines.extend(["", "CC-Link refresh areas / CC-Linkリフレッシュ領域", "------------------------------------------------"])
    for r in area_rows:
        lines.append(
            f"- {r['network_label']} object {r['object_id']} {r['area_kind']}: "
            f"{r['device_start']}..{r['device_end']} ({r['points_or_words']}), "
            f"direction={r['direction']}, evidence={r['evidence_file']}@{r['evidence_offset_hex']}"
        )

    lines.extend(["", "Incoming/status areas to inspect first / 取得・状態監視で優先確認する領域", "---------------------------------------------------------------------"])
    for r in incoming:
        lines.append(
            f"- {r['network_label']} {r['area_kind']}: {r['device_start']}..{r['device_end']} "
            f"({r['direction']})"
        )

    lines.extend(["", "Ethernet/SLMP candidates / Ethernet・SLMP系の候補", "----------------------------------------------------"])
    if slmp_rows:
        for r in slmp_rows:
            lines.append(
                f"- {r['device']}: direction/size not decoded, evidence={r['evidence_file']}@{r['evidence_offset_hex']}, "
                f"strings={r['section_or_peer_strings']}"
            )
    else:
        lines.append("- No Ethernet/SLMP device candidate strings found.")

    lines.extend(
        [
            "",
            "Device comment hints / コメントDBから見える補助情報",
            "--------------------------------------------------",
            "- Comment hints are sampled from common B/W/SW communication ranges when present.",
            "- B comments often describe upstream/own/downstream equipment link and conveyor handshaking.",
            "- W comments often describe station/work data style word areas.",
            "",
            "Caution / 注意",
            "--------------",
            "- これは抽出結果であり、三菱公式フォーマットデコーダではない。",
            "- w3paのリフレッシュブロックを書き換える場合は、GX Works3で同一設定を変更保存したbefore/after比較を先に取ること。",
        ]
    )
    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
