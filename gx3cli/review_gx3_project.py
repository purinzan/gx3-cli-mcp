from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import sqlite3

from gx3cli.gx3_device_name import format_device as _format_device
from gx3cli.gx3_analysis_state import PARTIAL, AnalysisState, checked
from gx3cli.extract_hmi_build_info import CommentInfo, DEVICE_CODE_BY_TYPE, comment_status
from gx3cli.extract_gx3_extended_instruction_knowledge import (
    DEVICE_TYPE_ALIASES,
    DEVICE_TYPES,
    LABEL_DEVICE_TYPE,
    LABEL_TOKEN_PREFIX,
    header_tokens,
    is_op_like,
)
from gx3cli.gx3_arg_decode import parse_row_occurrences as decode_row_occurrences, parse_row_operations
from gx3cli.gx3_intermediate_tool import decode_data, extract_dim, operation_model, parse_header_ops, read_ladder_rows
from gx3cli.gx3_project_paths import default_output_prefix, default_project_root, find_comment_db


TITLE_RE = re.compile(r"^V1:\d+:\d+:(.*?):st\{")
SAFETY_RE = re.compile(
    r"非常|安全|扉|ドア|カバー|ロック|原点|停止|インターロック|異常|EMS|EMG|safe|safety|door|cover|lock|stop|interlock|alarm",
    re.IGNORECASE,
)
POSITION_RE = re.compile(
    r"上限|下限|端|位置|確認|原点|前進端|後退端|上昇端|下降端|到着|完了|position|limit|origin|home|check|complete",
    re.IGNORECASE,
)
MODE_RE = re.compile(
    r"自動|手動|運転準備|動作中|モード|サイクル|原点復帰|manual|auto|mode|ready|cycle",
    re.IGNORECASE,
)
TIMEOUT_RE = re.compile(
    r"時間|ﾀｲﾏ|タイマ|異常|監視|待ち|delay|timer|timeout|alarm",
    re.IGNORECASE,
)
PERMISSIVE_RE = re.compile(
    r"運転可|起動可|動作可|許可|準備完了|準備入|異常なし|正常|OK|ready|permit|enable|available|allowed",
    re.IGNORECASE,
)
COMMAND_RE = re.compile(
    r"指令|要求|起動|運転|開始|動作|前進|後退|上昇|下降|開|閉|回転|command|request|start|run|move",
    re.IGNORECASE,
)
MATERIAL_RE = re.compile(
    r"在荷|ﾜｰｸ|ワーク|製品|品物|無し|有り|満杯|空|work|part|present|empty|no work",
    re.IGNORECASE,
)
BYPASS_RE = re.compile(
    r"無効|バイパス|ﾊﾞｲﾊﾟｽ|解除|強制|スキップ|skip|bypass|disable|force|release",
    re.IGNORECASE,
)
ACTUATOR_RE = re.compile(
    r"シリンダ|ﾘﾌﾀ|リフタ|モータ|ﾓｰﾀ|C/V|CV|コンベア|搬送|前進|後退|上昇|下降|開|閉|ロック|クランプ|回転|運転|起動|停止|cylinder|motor|conveyor",
    re.IGNORECASE,
)
NON_ACTUATOR_OUTPUT_RE = re.compile(
    r"ランプ|ﾗﾝﾌﾟ|LP|表示|データ|ﾃﾞｰﾀ|要求|許可|完了|クリア|ｸﾘｱ|書込|読込|BLOAD|受信|送信|方式|処理|lamp|display|data|request|clear|write|read",
    re.IGNORECASE,
)
TRACE_DEVICE_TYPES = {"M", "L", "B", "F", "V", "S"}
BENIGN_DUPLICATE_TEXT_RE = re.compile(
    r"(debug|For debugging|Example|Change history|"
    r"\u30c7\u30d0\u30c3\u30b0|\u5909\u66f4\u5c65\u6b74|\u554f\u3044\u5408\u308f\u305b)",
    re.IGNORECASE,
)
INDEXED_DETAIL_RE = re.compile(r"\bindexed\b", re.IGNORECASE)


CONDITION_WEIGHTS = {
    "safety": 100,
    "permissive": 90,
    "bypass_or_disable": 85,
    "position": 70,
    "mode": 50,
    "command": 40,
    "material": 35,
    "timer_or_monitor": 30,
    "mutual_interlock": 30,
    "unknown": 10,
}


@dataclass
class DeviceOcc:
    device: str
    device_type: str
    number: int
    role: str
    lddb: str
    pos: int
    block_id: str
    title: str
    parse_status: str
    access: str = ""
    detail: str = ""
    # How many devices the occurrence covers; see ArgOcc.range_len.
    range_len: int = 1
    row_conditions: list[str] = field(default_factory=list)
    row_condition_comments: list[str] = field(default_factory=list)
    row_all_devices: list[str] = field(default_factory=list)


@dataclass
class LadderRow:
    lddb: str
    pos: int
    block_id: str
    title: str
    blocktype: int
    rowsize: int
    data: str
    dim: str
    operations: list[dict[str, object]]
    parse_status: str
    occurrences: list[DeviceOcc] = field(default_factory=list)


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def load_comments_for_root(root: Path) -> dict[tuple[str, int], CommentInfo]:
    comment_db = find_comment_db(root)
    if comment_db is None or not comment_db.exists():
        return {}
    con = sqlite3.connect(comment_db)
    cur = con.cursor()
    comments: dict[tuple[str, int], CommentInfo] = {}
    # A comment database that is not shaped the way this expects raises
    # partway through, and without this the connection stayed open --
    # which on Windows means the file cannot be removed afterwards.
    try:
        for dev_type, dev_code in DEVICE_CODE_BY_TYPE.items():
            rows = cur.execute("select SEQ, DevNoLow from DEVICE_DATA where DevCode=?", (dev_code,)).fetchall()
            for seq, dev_no in rows:
                key = (dev_type, int(dev_no))
                info = comments.setdefault(key, CommentInfo(exists=True))
                c_rows = cur.execute(
                    """
                    select CmtNo, CmtData
                    from COMMENT_DATA
                    where DeviceSEQ=?
                      and coalesce(DelFlag, 0)=0
                      and trim(coalesce(CmtData, ''))<>''
                    order by CmtNo
                    """,
                    (seq,),
                ).fetchall()
                texts: list[str] = []
                for cmt_no, text in c_rows:
                    value = str(text).strip()
                    if not value:
                        continue
                    texts.append(value)
                    if cmt_no == 5 and not info.japanese:
                        info.japanese = value
                    elif cmt_no == 6 and not info.english:
                        info.english = value
                info.all_text = " / ".join(dict.fromkeys(texts))
    finally:
        con.close()
    return comments


def extract_title(data: str) -> str:
    match = TITLE_RE.search(data)
    return match.group(1).strip() if match else ""


def format_device(device_type: str, number: int) -> str:
    return _format_device(device_type, number)


def operation_device_types(data: str) -> list[str]:
    tokens = header_tokens(data)
    device_types: list[str] = []
    skip_string_literal_tokens = 0
    for i, token in enumerate(tokens):
        if skip_string_literal_tokens:
            skip_string_literal_tokens -= 1
            continue
        if token == "String":
            skip_string_literal_tokens = 2
            continue
        if token in {"a", "b", "c"} and i + 1 < len(tokens):
            device_type = DEVICE_TYPE_ALIASES.get(tokens[i + 1], tokens[i + 1])
            if device_type in DEVICE_TYPES:
                device_types.append(device_type)
            elif tokens[i + 1].startswith(LABEL_TOKEN_PREFIX):
                # keep index-aligned with parse_header_ops (label bit contact)
                device_types.append(LABEL_DEVICE_TYPE)
        elif is_op_like(token):
            dev_type = ""
            for candidate in tokens[i + 1 :]:
                if candidate in DEVICE_TYPES:
                    dev_type = candidate
                    break
                if is_op_like(candidate) or candidate in {"a", "b", "c"}:
                    break
            device_types.append(dev_type)
    return device_types


def parse_device_occurrences(row: LadderRow, comments: dict[tuple[str, int], CommentInfo]) -> list[DeviceOcc]:
    """Decode every device argument of every operation (transfer destinations,
    buffer memory, digit-specified and indexed devices included)."""
    occs: list[DeviceOcc] = []
    decoded, _status = decode_row_occurrences(row.data)
    for role, _opcode, args, _consts in decoded:
        for arg in args:
            if arg.device_type in {"?", ""}:
                continue
            occs.append(
                DeviceOcc(
                    device=arg.device,
                    device_type=arg.device_type,
                    number=arg.number,
                    role=role,
                    lddb=row.lddb,
                    pos=row.pos,
                    block_id=row.block_id,
                    title=row.title,
                    parse_status=row.parse_status,
                    access=arg.access,
                    detail=arg.detail,
                    range_len=arg.range_len,
                )
            )

    row_conditions = [f"{occ.role}:{occ.device}" for occ in occs if occ.role in {"a", "b"}]
    row_all_devices = [occ.device for occ in occs]
    row_condition_comments: list[str] = []
    for occ in occs:
        if occ.role not in {"a", "b"}:
            continue
        info = comments.get((occ.device_type, occ.number), CommentInfo())
        text = info.japanese or info.english or info.all_text
        if text:
            row_condition_comments.append(f"{occ.role}:{occ.device}={text}")
    for occ in occs:
        occ.row_conditions = row_conditions
        occ.row_condition_comments = row_condition_comments
        occ.row_all_devices = row_all_devices
    return occs


def load_rows(root: Path, comments: dict[tuple[str, int], CommentInfo]) -> list[LadderRow]:
    rows_by_db = read_ladder_rows(root)
    out: list[LadderRow] = []
    for lddb, rows in rows_by_db.items():
        current_title = ""
        for raw in rows:
            data = str(raw["data"])
            blocktype = int(raw["blocktype"])
            if blocktype in {1, 2}:
                title = extract_title(data)
                if title:
                    current_title = title
            if blocktype != 0:
                continue
            operations = operation_model(data)
            ce_count = data.count("s=ce{")
            parse_status = "exact" if ce_count == len(operations) else "partial"
            row = LadderRow(
                lddb=lddb,
                pos=int(float(raw["pos"])),
                block_id=str(raw["id"]),
                title=current_title,
                blocktype=blocktype,
                rowsize=int(raw["rowsize"] or 0),
                data=data,
                dim=extract_dim(data),
                operations=operations,
                parse_status=parse_status,
            )
            row.occurrences = parse_device_occurrences(row, comments)
            out.append(row)
    return out


def device_comment_text(info: CommentInfo) -> str:
    return info.japanese or info.english or info.all_text


def review_missing_comments(rows: list[LadderRow], comments: dict[tuple[str, int], CommentInfo]) -> list[dict[str, object]]:
    usage: dict[tuple[str, int], dict[str, object]] = {}
    for row in rows:
        for occ in row.occurrences:
            if occ.device_type not in DEVICE_CODE_BY_TYPE:
                continue  # UG / Z / index registers have no comment area
            key = (occ.device_type, occ.number)
            rec = usage.setdefault(
                key,
                {
                    "device": occ.device,
                    "device_type": occ.device_type,
                    "number": occ.number,
                    "count": 0,
                    "roles": Counter(),
                    "sample_lddb": occ.lddb,
                    "sample_pos": occ.pos,
                    "sample_title": occ.title,
                },
            )
            rec["count"] = int(rec["count"]) + 1
            rec["roles"][occ.role] += 1
    out = []
    for key, rec in sorted(usage.items(), key=lambda item: (item[0][0], item[0][1])):
        info = comments.get(key, CommentInfo())
        status = comment_status(info)
        if status == "comment_ok":
            continue
        out.append(
            {
                "severity": "medium" if rec["device_type"] in {"X", "Y"} else "low",
                "device": rec["device"],
                "device_type": rec["device_type"],
                "number": rec["number"],
                "comment_status": status,
                "occurrence_count": rec["count"],
                "roles": "; ".join(f"{k}={v}" for k, v in rec["roles"].most_common()),
                "sample_lddb": rec["sample_lddb"],
                "sample_pos": rec["sample_pos"],
                "sample_title": rec["sample_title"],
            }
        )
    return out


def review_duplicate_coils(rows: list[LadderRow], comments: dict[tuple[str, int], CommentInfo]) -> list[dict[str, object]]:
    drivers: dict[str, list[DeviceOcc]] = defaultdict(list)
    for row in rows:
        for occ in row.occurrences:
            if occ.role == "c" or occ.role in {"SET", "RST", "PLS", "PLF", "OUT__16", "OUTH__16"}:
                drivers[occ.device].append(occ)
    out = []
    for device, occs in sorted(drivers.items()):
        unique_locs = {(occ.lddb, occ.pos, occ.role) for occ in occs}
        if len(unique_locs) <= 1:
            continue
        first = occs[0]
        info = comments.get((first.device_type, first.number), CommentInfo())
        roles = Counter(occ.role for occ in occs)
        severity = "high" if first.device_type in {"Y", "M", "L"} and roles.get("c", 0) > 1 else "medium"
        note = "same output/coil driven from multiple places; confirm intentional SET/RST pattern or consolidate"
        text = " ".join([device_comment_text(info), *[occ.title for occ in occs]])
        normal_coils = [occ for occ in occs if occ.role == "c"]
        normal_condition_keys = {tuple(occ.row_conditions) for occ in normal_coils}
        if severity == "high" and BENIGN_DUPLICATE_TEXT_RE.search(text):
            severity = "medium"
            note = "duplicate coil is in a debug/example/change-history area; review before treating as a live logic conflict"
        elif severity == "high" and normal_coils and all(INDEXED_DETAIL_RE.search(occ.detail or "") for occ in normal_coils):
            severity = "medium"
            note = "duplicate coil uses indexed device addressing; verify index range before treating the base device as duplicated"
        elif severity == "high" and len(normal_coils) > 1 and len(normal_condition_keys) == 1:
            severity = "medium"
            note = "duplicate normal coils share the same condition signature; redundant, but not competing writes"
        out.append(
            {
                "severity": severity,
                "device": device,
                "comment": device_comment_text(info),
                "driver_count": len(unique_locs),
                "roles": "; ".join(f"{k}={v}" for k, v in roles.most_common()),
                "locations": " | ".join(f"{occ.lddb}:{occ.pos}:{occ.role}:{occ.title}" for occ in occs[:10]),
                "review_note": note,
            }
        )
    return out


def driver_index(rows: list[LadderRow]) -> dict[str, list[LadderRow]]:
    index: dict[str, list[LadderRow]] = defaultdict(list)
    for row in rows:
        for occ in row.occurrences:
            if occ.role == "c" or occ.role in {"SET", "RST", "PLS", "PLF", "OUT__16", "OUTH__16"}:
                index[occ.device].append(row)
    return index


def comment_for_device(device_type: str, number: int, comments: dict[tuple[str, int], CommentInfo]) -> str:
    return device_comment_text(comments.get((device_type, number), CommentInfo()))


def condition_text(occ: DeviceOcc, comments: dict[tuple[str, int], CommentInfo]) -> str:
    return " ".join(
        [
            occ.device,
            occ.role,
            occ.title,
            comment_for_device(occ.device_type, occ.number, comments),
        ]
    )


def classify_condition(occ: DeviceOcc, comments: dict[tuple[str, int], CommentInfo]) -> set[str]:
    text = condition_text(occ, comments)
    classes: set[str] = set()
    if SAFETY_RE.search(text):
        classes.add("safety")
    if PERMISSIVE_RE.search(text):
        classes.add("permissive")
    if BYPASS_RE.search(text):
        classes.add("bypass_or_disable")
    if POSITION_RE.search(text) or occ.device_type == "X":
        classes.add("position")
    if MODE_RE.search(text):
        classes.add("mode")
    if COMMAND_RE.search(text):
        classes.add("command")
    if MATERIAL_RE.search(text):
        classes.add("material")
    if TIMEOUT_RE.search(text) or occ.device_type in {"T", "ST"}:
        classes.add("timer_or_monitor")
    if occ.role == "b":
        classes.add("mutual_interlock")
    if not classes:
        classes.add("unknown")
    return classes


def summarize_condition_classes(conditions: list[DeviceOcc], comments: dict[tuple[str, int], CommentInfo]) -> dict[str, object]:
    class_counter: Counter[str] = Counter()
    evidence: list[str] = []
    max_weight = 0
    for occ in conditions:
        classes = classify_condition(occ, comments)
        for cls in classes:
            class_counter[cls] += 1
            max_weight = max(max_weight, CONDITION_WEIGHTS.get(cls, 0))
        comment = comment_for_device(occ.device_type, occ.number, comments)
        evidence.append(f"{occ.role}:{occ.device}={comment or occ.title}=>{'+'.join(sorted(classes))}")
    critical_classes = ["safety", "permissive", "bypass_or_disable", "position", "mode", "material", "timer_or_monitor", "mutual_interlock"]
    return {
        "condition_classes": "; ".join(f"{cls}={class_counter[cls]}" for cls in critical_classes if class_counter[cls]),
        "condition_importance_max": max_weight,
        "key_condition_evidence": " | ".join(evidence[:20]),
        "has_permissive_condition": "yes" if class_counter["permissive"] else "no",
        "has_command_condition": "yes" if class_counter["command"] else "no",
        "has_material_condition": "yes" if class_counter["material"] else "no",
        "has_bypass_or_disable_condition": "yes" if class_counter["bypass_or_disable"] else "no",
    }


def row_condition_text(row: LadderRow) -> str:
    return " ".join(
        [
            row.title,
            " ".join(cond for occ in row.occurrences for cond in occ.row_conditions),
            " ".join(cond for occ in row.occurrences for cond in occ.row_condition_comments),
        ]
    )


def trace_upstream_conditions(
    start_conditions: list[DeviceOcc],
    drivers: dict[str, list[LadderRow]],
    comments: dict[tuple[str, int], CommentInfo],
    max_depth: int = 3,
) -> dict[str, object]:
    queue: list[tuple[DeviceOcc, int, str]] = []
    for occ in start_conditions:
        if occ.role not in {"a", "b"} or occ.device_type not in TRACE_DEVICE_TYPES:
            continue
        queue.append((occ, 1, occ.device))

    visited_devices: set[str] = set()
    visited_rows: set[tuple[str, int]] = set()
    trace_rows: list[str] = []
    trace_devices: list[str] = []
    trace_comments: list[str] = []
    all_text_parts: list[str] = []
    has_x = False
    has_timer = False
    has_opposite_interlock = False
    upstream_class_counter: Counter[str] = Counter()
    upstream_evidence: list[str] = []

    while queue:
        occ, depth, path = queue.pop(0)
        if depth > max_depth or occ.device in visited_devices:
            continue
        visited_devices.add(occ.device)
        trace_devices.append(occ.device)
        cmt = comment_for_device(occ.device_type, occ.number, comments)
        if cmt:
            trace_comments.append(f"{occ.device}={cmt}")
            all_text_parts.append(cmt)

        for row in drivers.get(occ.device, [])[:4]:
            row_key = (row.lddb, row.pos)
            if row_key in visited_rows:
                continue
            visited_rows.add(row_key)
            conditions = [cond for cond in row.occurrences if cond.role in {"a", "b"}]
            condition_list = "; ".join(f"{cond.role}:{cond.device}" for cond in conditions)
            comment_list = "; ".join(
                cond.row_condition_comments[0] if cond.row_condition_comments else "" for cond in conditions[:1]
            )
            trace_rows.append(f"d{depth}:{occ.device}<-{row.lddb}:{row.pos}:{row.title}:{condition_list}")
            all_text_parts.extend([row.title, row_condition_text(row)])
            if any(cond.device_type == "X" for cond in conditions):
                has_x = True
            if any(cond.device_type in {"T", "ST"} for cond in conditions):
                has_timer = True
            if any(cond.role == "b" for cond in conditions):
                has_opposite_interlock = True
            summary = summarize_condition_classes(conditions, comments)
            for part in str(summary["condition_classes"]).split("; "):
                if not part or "=" not in part:
                    continue
                cls, count = part.split("=", 1)
                upstream_class_counter[cls] += int(count)
            if summary["key_condition_evidence"]:
                upstream_evidence.append(str(summary["key_condition_evidence"]))
            for cond in conditions:
                if cond.device_type in TRACE_DEVICE_TYPES and cond.device not in visited_devices:
                    queue.append((cond, depth + 1, f"{path}>{cond.device}"))

    all_text = " ".join(all_text_parts)
    return {
        "upstream_trace_devices": "; ".join(dict.fromkeys(trace_devices)),
        "upstream_trace_comments": "; ".join(dict.fromkeys(trace_comments[:20])),
        "upstream_trace_rows": " | ".join(trace_rows[:20]),
        "upstream_trace_row_count": len(trace_rows),
        "upstream_has_x_condition": "yes" if has_x else "no",
        "upstream_has_safety_keyword": "yes" if SAFETY_RE.search(all_text) else "no",
        "upstream_has_position_keyword": "yes" if POSITION_RE.search(all_text) else "no",
        "upstream_has_mode_keyword": "yes" if MODE_RE.search(all_text) else "no",
        "upstream_has_timer_or_timeout": "yes" if has_timer or TIMEOUT_RE.search(all_text) else "no",
        "upstream_has_b_contact_interlock": "yes" if has_opposite_interlock else "no",
        "upstream_condition_classes": "; ".join(
            f"{cls}={upstream_class_counter[cls]}"
            for cls in ["safety", "permissive", "bypass_or_disable", "position", "mode", "command", "material", "timer_or_monitor", "mutual_interlock"]
            if upstream_class_counter[cls]
        ),
        "upstream_key_condition_evidence": " | ".join(upstream_evidence[:20]),
        "upstream_has_permissive_condition": "yes" if upstream_class_counter["permissive"] else "no",
        "upstream_has_command_condition": "yes" if upstream_class_counter["command"] else "no",
        "upstream_has_material_condition": "yes" if upstream_class_counter["material"] else "no",
        "upstream_has_bypass_or_disable_condition": "yes" if upstream_class_counter["bypass_or_disable"] else "no",
    }


def review_interlock_candidates(rows: list[LadderRow], comments: dict[tuple[str, int], CommentInfo]) -> list[dict[str, object]]:
    drivers = driver_index(rows)
    out = []
    for row in rows:
        coils = [occ for occ in row.occurrences if occ.role == "c" and occ.device_type == "Y"]
        if not coils:
            continue
        conditions = [occ for occ in row.occurrences if occ.role in {"a", "b"}]
        condition_text = " ".join([*row.title.split(), *[comment for occ in conditions for comment in occ.row_condition_comments]])
        has_x = any(occ.device_type == "X" for occ in conditions)
        has_safety = bool(SAFETY_RE.search(condition_text))
        has_position = bool(POSITION_RE.search(condition_text))
        has_mode = bool(MODE_RE.search(condition_text))
        has_timeout = any(occ.device_type in {"T", "ST"} for occ in conditions) or bool(TIMEOUT_RE.search(condition_text))
        has_b_interlock = any(occ.role == "b" for occ in conditions)
        direct_summary = summarize_condition_classes(conditions, comments)
        trace = trace_upstream_conditions(conditions, drivers, comments)
        for coil in coils:
            info = comments.get((coil.device_type, coil.number), CommentInfo())
            coil_comment = device_comment_text(info)
            output_text = " ".join([coil.device, coil_comment, row.title])
            actuator_like = bool(ACTUATOR_RE.search(output_text)) and not bool(NON_ACTUATOR_OUTPUT_RE.search(output_text))
            if not actuator_like:
                continue
            direct_support_count = sum([has_x, has_safety, has_position, has_mode, has_timeout, has_b_interlock])
            has_critical_direct = direct_summary["has_permissive_condition"] == "yes" or direct_summary["has_bypass_or_disable_condition"] == "yes" or has_safety
            has_critical_upstream = trace["upstream_has_permissive_condition"] == "yes" or trace["upstream_has_bypass_or_disable_condition"] == "yes" or trace["upstream_has_safety_keyword"] == "yes"
            upstream_support_count = sum(
                1
                for key in [
                    "upstream_has_x_condition",
                    "upstream_has_safety_keyword",
                    "upstream_has_position_keyword",
                    "upstream_has_mode_keyword",
                    "upstream_has_timer_or_timeout",
                    "upstream_has_b_contact_interlock",
                ]
                if trace.get(key) == "yes"
            )
            needs_review = len(conditions) <= 1 or (actuator_like and direct_support_count < 2)
            if not needs_review:
                continue
            if upstream_support_count >= 3 and has_critical_upstream:
                severity = "low"
                review_status = "upstream_conditions_found"
                recommended_action = "最終Y段は簡素だが、上流に重要条件が見える。重要条件の意味と仕様一致を確認する。"
            elif upstream_support_count >= 1:
                severity = "medium"
                review_status = "trace_required_partial_upstream"
                recommended_action = "上流条件は一部見える。重要条件が許可/安全/抑止/位置/モードのどれかを分類して確認する。"
            else:
                severity = "high" if actuator_like else "medium"
                review_status = "interlock_gap_suspected"
                recommended_action = "最終Y段にも上流にも重要条件が見えにくい。許可・安全・抑止条件の不足疑いとして重点確認する。"
            out.append(
                {
                    "severity": severity,
                    "review_status": review_status,
                    "device": coil.device,
                    "comment": coil_comment,
                    "lddb": row.lddb,
                    "pos": row.pos,
                    "title": row.title,
                    "actuator_like": "yes" if actuator_like else "no",
                    "condition_count": len(conditions),
                    "has_x_condition": "yes" if has_x else "no",
                    "has_safety_keyword": "yes" if has_safety else "no",
                    "has_position_keyword": "yes" if has_position else "no",
                    "has_mode_keyword": "yes" if has_mode else "no",
                    "has_timer_or_timeout": "yes" if has_timeout else "no",
                    "has_b_contact_interlock": "yes" if has_b_interlock else "no",
                    "has_critical_direct_condition": "yes" if has_critical_direct else "no",
                    "conditions": "; ".join(coil.row_conditions),
                    "condition_comments": "; ".join(coil.row_condition_comments),
                    **direct_summary,
                    **trace,
                    "has_critical_upstream_condition": "yes" if has_critical_upstream else "no",
                    "recommended_action": recommended_action,
                    "review_note": "candidate only: final output rungs are often simplified; judge after upstream condition trace",
                }
            )
    return out


def review_timer_settings(rows: list[LadderRow], comments: dict[tuple[str, int], CommentInfo]) -> list[dict[str, object]]:
    out = []
    for row in rows:
        operations, _status = parse_row_operations(row.data)
        for operation in operations:
            opcode = operation.role
            if opcode not in {"OUT__16", "OUTH__16"}:
                continue
            timer = next((arg for arg in operation.args if arg.arg_index == 0 and arg.device_type in {"T", "C"}), None)
            if timer is None:
                continue
            setting = next(iter(operation.constant_values.values()), "")
            source_op = row.operations[operation.op_index] if operation.op_index < len(row.operations) else {}
            info = comments.get((timer.device_type, timer.number), CommentInfo())
            out.append(
                {
                    "device": timer.device,
                    "device_type": timer.device_type,
                    "number": timer.number,
                    "comment": device_comment_text(info),
                    "opcode": opcode,
                    "setting_raw": setting,
                    "lddb": row.lddb,
                    "pos": row.pos,
                    "title": row.title,
                    "conditions": "; ".join([occ.row_conditions[0] if occ.row_conditions else "" for occ in row.occurrences[:1]]),
                    "arg_shapes": source_op.get("arg_shapes", ""),
                }
            )
    return out


def review_changeability(rows: list[LadderRow]) -> list[dict[str, object]]:
    out = []
    for row in rows:
        instructions = [str(op.get("opcode", "")) for op in row.operations if op.get("kind") == "instruction"]
        category_counts = Counter(str(op.get("category", "")) for op in row.operations if op.get("kind") == "instruction")
        risk = 0
        reasons = []
        if row.parse_status != "exact":
            risk += 5
            reasons.append("parse_partial")
        if row.rowsize >= 6:
            risk += 3
            reasons.append("large_rowsize")
        if len(row.operations) >= 12:
            risk += 3
            reasons.append("many_operations")
        if any(cat in category_counts for cat in {"module_or_buffer_access", "loop_control", "conversion_or_string", "other_instruction"}):
            risk += 4
            reasons.append("unsupported_or_external_instruction")
        if any(op in {"BMOV", "BMOVP", "FOR", "NEXT", "GP.RIWT", "GP.RIRD"} for op in instructions):
            risk += 3
            reasons.append("wide_or_control_instruction")
        if risk == 0:
            continue
        out.append(
            {
                "risk_score": risk,
                "severity": "high" if risk >= 7 else "medium",
                "lddb": row.lddb,
                "pos": row.pos,
                "block_id": row.block_id,
                "title": row.title,
                "rowsize": row.rowsize,
                "dim": row.dim,
                "operation_count": len(row.operations),
                "parse_status": row.parse_status,
                "instructions": "; ".join(instructions),
                "reasons": "; ".join(reasons),
                "review_note": "higher score means harder to safely auto-edit; inspect before changing",
            }
        )
    return sorted(out, key=lambda item: int(item["risk_score"]), reverse=True)


def review_maintainability(
    rows: list[LadderRow],
    missing_comments: list[dict[str, object]],
    duplicate_coils: list[dict[str, object]],
    interlock_candidates: list[dict[str, object]],
    changeability: list[dict[str, object]],
) -> list[dict[str, object]]:
    by_title: dict[str, dict[str, object]] = {}
    for row in rows:
        rec = by_title.setdefault(
            row.title or "(no title)",
            {
                "title": row.title or "(no title)",
                "rung_count": 0,
                "operation_count": 0,
                "large_rung_count": 0,
                "partial_parse_count": 0,
                "instruction_count": 0,
            },
        )
        rec["rung_count"] = int(rec["rung_count"]) + 1
        rec["operation_count"] = int(rec["operation_count"]) + len(row.operations)
        if row.rowsize >= 6:
            rec["large_rung_count"] = int(rec["large_rung_count"]) + 1
        if row.parse_status != "exact":
            rec["partial_parse_count"] = int(rec["partial_parse_count"]) + 1
        rec["instruction_count"] = int(rec["instruction_count"]) + sum(1 for op in row.operations if op.get("kind") == "instruction")

    def add_counts(items: list[dict[str, object]], key_name: str, counter_name: str) -> None:
        for item in items:
            title = str(item.get(key_name, ""))
            if not title:
                continue
            rec = by_title.setdefault(title, {"title": title, "rung_count": 0, "operation_count": 0, "large_rung_count": 0, "partial_parse_count": 0, "instruction_count": 0})
            rec[counter_name] = int(rec.get(counter_name, 0)) + 1

    add_counts(missing_comments, "sample_title", "missing_comment_count")
    add_counts(interlock_candidates, "title", "interlock_candidate_count")
    add_counts(changeability, "title", "changeability_risk_count")
    for item in duplicate_coils:
        for loc in str(item.get("locations", "")).split(" | "):
            parts = loc.split(":")
            if len(parts) >= 4:
                title = ":".join(parts[3:])
                rec = by_title.setdefault(title, {"title": title, "rung_count": 0, "operation_count": 0, "large_rung_count": 0, "partial_parse_count": 0, "instruction_count": 0})
                rec["duplicate_coil_count"] = int(rec.get("duplicate_coil_count", 0)) + 1

    rows_out = []
    for rec in by_title.values():
        score = (
            int(rec.get("large_rung_count", 0)) * 2
            + int(rec.get("partial_parse_count", 0)) * 4
            + int(rec.get("missing_comment_count", 0))
            + int(rec.get("duplicate_coil_count", 0)) * 3
            + int(rec.get("interlock_candidate_count", 0)) * 3
            + int(rec.get("changeability_risk_count", 0)) * 2
        )
        if score == 0:
            continue
        rec["maintainability_score"] = score
        rec["review_note"] = "section-level hotspot; prioritize comments, duplicate outputs, and risky rungs"
        rows_out.append(rec)
    return sorted(rows_out, key=lambda item: int(item["maintainability_score"]), reverse=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate static project review reports")
    parser.add_argument("root", nargs="?", default=str(default_project_root()))
    parser.add_argument("--prefix", default=default_output_prefix("review"))
    args = parser.parse_args()

    root = Path(args.root)
    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)

    missing_comments = review_missing_comments(rows, comments)
    duplicate_coils = review_duplicate_coils(rows, comments)
    interlock_candidates = review_interlock_candidates(rows, comments)
    timer_settings = review_timer_settings(rows, comments)
    changeability = review_changeability(rows)
    maintainability = review_maintainability(rows, missing_comments, duplicate_coils, interlock_candidates, changeability)

    prefix = args.prefix
    write_csv(
        Path(f"{prefix}_missing_device_comments.csv"),
        missing_comments,
        ["severity", "device", "device_type", "number", "comment_status", "occurrence_count", "roles", "sample_lddb", "sample_pos", "sample_title"],
    )
    write_csv(
        Path(f"{prefix}_duplicate_coils.csv"),
        duplicate_coils,
        ["severity", "device", "comment", "driver_count", "roles", "locations", "review_note"],
    )
    write_csv(
        Path(f"{prefix}_interlock_candidates.csv"),
        interlock_candidates,
        [
            "severity",
            "review_status",
            "device",
            "comment",
            "lddb",
            "pos",
            "title",
            "actuator_like",
            "condition_count",
            "has_x_condition",
            "has_safety_keyword",
            "has_position_keyword",
            "has_mode_keyword",
            "has_timer_or_timeout",
            "has_b_contact_interlock",
            "has_critical_direct_condition",
            "condition_classes",
            "condition_importance_max",
            "key_condition_evidence",
            "has_permissive_condition",
            "has_command_condition",
            "has_material_condition",
            "has_bypass_or_disable_condition",
            "conditions",
            "condition_comments",
            "upstream_trace_devices",
            "upstream_trace_comments",
            "upstream_trace_rows",
            "upstream_trace_row_count",
            "upstream_has_x_condition",
            "upstream_has_safety_keyword",
            "upstream_has_position_keyword",
            "upstream_has_mode_keyword",
            "upstream_has_timer_or_timeout",
            "upstream_has_b_contact_interlock",
            "upstream_condition_classes",
            "upstream_key_condition_evidence",
            "upstream_has_permissive_condition",
            "upstream_has_command_condition",
            "upstream_has_material_condition",
            "upstream_has_bypass_or_disable_condition",
            "has_critical_upstream_condition",
            "recommended_action",
            "review_note",
        ],
    )
    write_csv(
        Path(f"{prefix}_timer_settings.csv"),
        timer_settings,
        ["device", "device_type", "number", "comment", "opcode", "setting_raw", "lddb", "pos", "title", "conditions", "arg_shapes"],
    )
    write_csv(
        Path(f"{prefix}_changeability.csv"),
        changeability,
        ["risk_score", "severity", "lddb", "pos", "block_id", "title", "rowsize", "dim", "operation_count", "parse_status", "instructions", "reasons", "review_note"],
    )
    write_csv(
        Path(f"{prefix}_maintainability.csv"),
        maintainability,
        [
            "maintainability_score",
            "title",
            "rung_count",
            "operation_count",
            "large_rung_count",
            "partial_parse_count",
            "instruction_count",
            "missing_comment_count",
            "duplicate_coil_count",
            "interlock_candidate_count",
            "changeability_risk_count",
            "review_note",
        ],
    )

    # Counts below are over the rows that were read. A row the decoder could
    # not fully interpret contributes whatever it managed, and a report that
    # only prints counts reads as though every row was understood.
    partial_rows = [row for row in rows if row.parse_status != "exact"]
    if partial_rows:
        analysis = AnalysisState(
            PARTIAL,
            reason=f"{len(partial_rows)} of {len(rows)} rungs were not fully interpreted",
            next_step="gx3-cli parse-gaps --root <project>",
        )
    else:
        analysis = checked()

    summary = {
        "root": str(root),
        "analysis": analysis.as_dict(),
        "rung_count": len(rows),
        "partial_rung_count": len(partial_rows),
        "missing_device_comments": len(missing_comments),
        "duplicate_coils": len(duplicate_coils),
        "interlock_candidates": len(interlock_candidates),
        "timer_settings": len(timer_settings),
        "changeability_items": len(changeability),
        "maintainability_hotspots": len(maintainability),
        "outputs": [
            f"{prefix}_missing_device_comments.csv",
            f"{prefix}_duplicate_coils.csv",
            f"{prefix}_interlock_candidates.csv",
            f"{prefix}_timer_settings.csv",
            f"{prefix}_changeability.csv",
            f"{prefix}_maintainability.csv",
            f"{prefix}_summary.json",
        ],
    }
    Path(f"{prefix}_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(f"{prefix}_summary.txt").write_text(
        "\n".join(
            [
                "Static review summary",
                "=====================",
                f"root: {root}",
                f"result: {analysis.line()}",
                f"rung_count: {len(rows)}",
                f"missing_device_comments: {len(missing_comments)}",
                f"duplicate_coils: {len(duplicate_coils)}",
                f"interlock_candidates: {len(interlock_candidates)}",
                f"timer_settings: {len(timer_settings)}",
                f"changeability_items: {len(changeability)}",
                f"maintainability_hotspots: {len(maintainability)}",
                "",
                "Notes:",
                "- duplicate_coils and missing_device_comments are stronger static findings.",
                "- interlock_candidates, changeability, and maintainability are screening hints and require engineering review.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
