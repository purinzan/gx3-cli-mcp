from __future__ import annotations

import csv
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from gx3cli.gx3_project_paths import default_comm_prefix, default_output_path, default_project_root, find_comment_db

ROOT = default_project_root()
COMMENT_DB = find_comment_db(ROOT) or ROOT / "_comments_DC.db"
COMM_PREFIX = default_comm_prefix()
COMM_REFRESH_CSV = Path(f"{COMM_PREFIX}_refresh_areas.csv")
SLMP_CANDIDATE_CSV = Path(f"{COMM_PREFIX}_ethernet_slmp_device_candidates.csv")

OUT_IO = default_output_path("io_comment_inventory", "csv")
OUT_IO_MISSING = default_output_path("io_missing_comment_todo", "csv")
OUT_INPUT_MONITOR = default_output_path("input_monitor_candidates", "csv")
OUT_OUTPUT_MANUAL = default_output_path("output_manual_operation_candidates", "csv")
OUT_SINGLE_ACTION = default_output_path("single_action_candidates", "csv")
OUT_LADDER_OCC = default_output_path("ladder_device_occurrences", "csv")
OUT_SUMMARY = default_output_path("hmi_build_info_summary", "txt")

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

NON_REPORT_D_OPERANDS = {"Zs", "Ats", "Ks", "N", "Z", "G"}
D_OPERAND_TYPES = set(DEVICE_CODE_BY_TYPE) | NON_REPORT_D_OPERANDS

INT_TOKEN_RE = re.compile(r"-?\d+$")
TITLE_RE = re.compile(r"^V1:\d+:\d+:(.*?):st\{")
B_OPERAND_RE = re.compile(
    r"B\{b=d\{s=#:a=(-?\d+):vt=nn\}:e=d\{s=#:a=(-?\d+):vt=nn\}:vt=([A-Za-z]+)\}"
)
D_OPERAND_RE = re.compile(r"d\{s=#:a=(-?\d+):vt=nn\}")

ACTION_KEYWORDS = re.compile(
    r"前進|後退|出|戻|上昇|下降|昇降|開|閉|ロック|ﾛｯｸ|解除|"
    r"爪|基準|位置決|ガイド|ｶﾞｲﾄﾞ|アーム|ｱｰﾑ|"
    r"シャッター|ｼｬｯﾀｰ|ストッパ|ｽﾄｯﾊﾟ|プッシャ|ﾌﾟｯｼｬ|"
    r"エスケープ|ｴｽｹｰﾌﾟ|搬送|回転|運転|起動|停止|C/V|CV|ｺﾝﾍﾞｱ|コンベア|"
    r"advance|retreat|up|down|open|close|rotation|conveyor|motor|start|stop",
    re.IGNORECASE,
)
MOTOR_KEYWORDS = re.compile(
    r"C/V|CV|回転|運転|起動|停止|ﾓｰﾀ|モータ|motor|conveyor|ｺﾝﾍﾞｱ|コンベア|集塵機",
    re.IGNORECASE,
)
CYLINDER_KEYWORDS = re.compile(
    r"前進|後退|出|戻|上昇|下降|昇降|爪|基準|位置決|ガイド|ｶﾞｲﾄﾞ|"
    r"アーム|ｱｰﾑ|シャッター|ｼｬｯﾀｰ|ストッパ|ｽﾄｯﾊﾟ|"
    r"プッシャ|ﾌﾟｯｼｬ|エスケープ|ｴｽｹｰﾌﾟ|advance|retreat|up|down",
    re.IGNORECASE,
)
LAMP_KEYWORDS = re.compile(r"LP|ランプ|ﾗﾝﾌﾟ|ブザー|ﾌﾞｻﾞｰ|蛍光灯|lamp|buzzer|light", re.IGNORECASE)
COMM_KEYWORDS = re.compile(r"通信|送信|受信|要求|読出|EtherNet|SR-X|測定器|初期化|エラークリア|error clear", re.IGNORECASE)
LOCK_KEYWORDS = re.compile(r"ロック|ﾛｯｸ|扉|カバー|ｶﾊﾞｰ|ECB|lock|door|cover", re.IGNORECASE)


@dataclass
class CommentInfo:
    exists: bool = False
    japanese: str = ""
    english: str = ""
    all_text: str = ""


@dataclass
class Occurrence:
    device_type: str
    number: int
    role: str
    role_kind: str
    lddb: str
    pos: int
    block_id: str
    title: str
    parse_status: str
    row_devices: list[str] = field(default_factory=list)
    row_conditions: list[str] = field(default_factory=list)
    row_condition_comments: list[str] = field(default_factory=list)

    @property
    def device(self) -> str:
        return format_device(self.device_type, self.number)


def open_sqlite(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    return con


def format_device(device_type: str, number: int) -> str:
    return f"{device_type}{number}"


def hex_candidate(device_type: str, number: int) -> str:
    if device_type in {"X", "Y", "B", "W", "SW", "SB"}:
        return f"{device_type}{number:X}"
    return ""


def load_comments() -> dict[tuple[str, int], CommentInfo]:
    con = sqlite3.connect(COMMENT_DB)
    cur = con.cursor()
    comments: dict[tuple[str, int], CommentInfo] = {}

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
    con.close()
    return comments


def extract_title(data: str) -> str:
    m = TITLE_RE.search(data)
    return m.group(1).strip() if m else ""


def parse_header_operand_refs(data: str) -> list[tuple[str, str]]:
    """Return (role, device_type) entries in header order.

    Example: a:X is a contact, b:X is a normally-closed contact, c:Y is a coil.
    Function destinations such as MOV:K_1:D keep the previous functional token.
    """
    if ":cb{" not in data:
        return []
    prefix = data.split(":cb{", 1)[0]
    tokens = prefix.split(":")[1:]  # drop V1
    refs: list[tuple[str, str]] = []
    last_non_int = ""
    for token in tokens:
        if INT_TOKEN_RE.fullmatch(token):
            continue
        if token in D_OPERAND_TYPES:
            refs.append((last_non_int, token))
        last_non_int = token
    return refs


def parse_operand_numbers(data: str) -> list[int]:
    out: list[int] = []
    i = 0
    while i < len(data):
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


def role_kind(role: str) -> str:
    if role == "a":
        return "contact_a"
    if role == "b":
        return "contact_b"
    if role == "c":
        return "coil_or_destination"
    if role:
        return f"function_or_compare:{role}"
    return "unknown"


def read_comm_areas() -> list[dict[str, object]]:
    if not COMM_REFRESH_CSV.exists():
        return []
    rows: list[dict[str, object]] = []
    with COMM_REFRESH_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            start = str(row.get("device_start", ""))
            end = str(row.get("device_end", ""))
            m1 = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", start)
            m2 = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", end)
            if not m1 or not m2:
                continue
            prefix = m1.group(1)
            count_text = str(row.get("points_or_words", "0"))
            try:
                point_count = int(count_text)
            except ValueError:
                point_count = 0
            if prefix in {"X", "Y"}:
                start_no = int(m1.group(2), 16)
                end_no = start_no + point_count - 1 if point_count else int(m2.group(2), 16)
            else:
                start_no = int(m1.group(2))
                end_no = start_no + point_count - 1 if point_count else int(m2.group(2), 16)
            rows.append(
                {
                    "device_type": prefix,
                    "start_no": start_no,
                    "end_no": end_no,
                    "network_label": row.get("network_label", ""),
                    "area_kind": row.get("area_kind", ""),
                    "direction": row.get("direction", ""),
                    "device_start": start,
                    "device_end": end,
                }
            )
    return rows


def read_slmp_candidates() -> set[str]:
    if not SLMP_CANDIDATE_CSV.exists():
        return set()
    with SLMP_CANDIDATE_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        return {row["device"] for row in csv.DictReader(f) if row.get("device")}


def classify_area(device_type: str, number: int, comm_areas: list[dict[str, object]], slmp_devices: set[str]) -> str:
    device = format_device(device_type, number)
    if device in slmp_devices or hex_candidate(device_type, number) in slmp_devices:
        return "ethernet_slmp_candidate"
    matches = []
    for area in comm_areas:
        if area["device_type"] != device_type:
            continue
        if int(area["start_no"]) <= number <= int(area["end_no"]):
            matches.append(f"{area['network_label']}:{area['area_kind']}:{area['direction']}")
    if matches:
        return "; ".join(matches)
    if device_type == "X":
        return "input_or_unit_signal"
    if device_type == "Y":
        return "output_or_unit_signal"
    if device_type in {"B", "W", "SW", "SB"}:
        return "link_or_word_device"
    return ""


def collect_ladder_occurrences(comments: dict[tuple[str, int], CommentInfo]) -> tuple[list[Occurrence], Counter]:
    occurrences: list[Occurrence] = []
    stats = Counter()

    for db_path in sorted(ROOT.glob("*_LDDB.db")):
        con = open_sqlite(db_path)
        cur = con.cursor()
        rows = cur.execute("select id, pos, blocktype, data from LadderBlocks order by pos").fetchall()
        con.close()

        last_title = ""
        for block_id, pos, blocktype, data in rows:
            pos_i = int(float(pos))
            if blocktype in (1, 2):
                title = extract_title(data)
                if title:
                    last_title = title
                continue
            if blocktype != 0 or ":cb{" not in data:
                continue

            refs = parse_header_operand_refs(data)
            numbers = parse_operand_numbers(data)
            parse_status = "exact" if len(refs) == len(numbers) else "partial"
            stats[f"{parse_status}_rows"] += 1

            row_entries: list[tuple[str, int, str]] = []
            for (role, dev_type), number in zip(refs, numbers):
                if dev_type not in DEVICE_CODE_BY_TYPE:
                    continue
                row_entries.append((dev_type, int(number), role))
            row_devices = [format_device(dt, n) for dt, n, _ in row_entries]
            condition_entries = [(dt, n, r) for dt, n, r in row_entries if r in {"a", "b"}]
            row_conditions = [f"{r}:{format_device(dt, n)}" for dt, n, r in condition_entries]
            row_condition_comments = []
            for dt, n, r in condition_entries:
                comment = comments.get((dt, n), CommentInfo()).japanese or comments.get((dt, n), CommentInfo()).english
                if comment:
                    row_condition_comments.append(f"{r}:{format_device(dt, n)}={comment}")

            for dev_type, number, role in row_entries:
                occurrences.append(
                    Occurrence(
                        device_type=dev_type,
                        number=number,
                        role=role,
                        role_kind=role_kind(role),
                        lddb=db_path.name,
                        pos=pos_i,
                        block_id=str(block_id),
                        title=last_title,
                        parse_status=parse_status,
                        row_devices=row_devices,
                        row_conditions=row_conditions,
                        row_condition_comments=row_condition_comments,
                    )
                )

    stats["occurrences"] = len(occurrences)
    return occurrences, stats


def summarize_usage(occurrences: list[Occurrence]) -> dict[tuple[str, int], dict[str, object]]:
    usage: dict[tuple[str, int], dict[str, object]] = {}
    for occ in occurrences:
        key = (occ.device_type, occ.number)
        rec = usage.setdefault(
            key,
            {
                "occurrence_count": 0,
                "contact_a_count": 0,
                "contact_b_count": 0,
                "coil_count": 0,
                "function_or_other_count": 0,
                "first_lddb": occ.lddb,
                "first_pos": occ.pos,
                "first_title": occ.title,
                "first_block_id": occ.block_id,
            },
        )
        rec["occurrence_count"] = int(rec["occurrence_count"]) + 1
        if occ.role == "a":
            rec["contact_a_count"] = int(rec["contact_a_count"]) + 1
        elif occ.role == "b":
            rec["contact_b_count"] = int(rec["contact_b_count"]) + 1
        elif occ.role == "c":
            rec["coil_count"] = int(rec["coil_count"]) + 1
        else:
            rec["function_or_other_count"] = int(rec["function_or_other_count"]) + 1
    return usage


def comment_status(info: CommentInfo) -> str:
    if not info.exists:
        return "device_row_missing"
    if info.japanese or info.english or info.all_text:
        return "comment_ok"
    return "comment_blank"


def classify_action(text: str) -> str:
    labels = []
    if MOTOR_KEYWORDS.search(text):
        labels.append("motor_cv")
    if CYLINDER_KEYWORDS.search(text):
        labels.append("cylinder_actuator")
    if LOCK_KEYWORDS.search(text):
        labels.append("lock_safety")
    if LAMP_KEYWORDS.search(text):
        labels.append("lamp_buzzer")
    if COMM_KEYWORDS.search(text):
        labels.append("communication_external")
    return ";".join(labels) if labels else "other"


def make_condition_text(occ: Occurrence, comments: dict[tuple[str, int], CommentInfo], only_types: set[str] | None = None) -> tuple[str, str]:
    devices = []
    comment_texts = []
    for cond in occ.row_conditions:
        role, device = cond.split(":", 1)
        m = re.fullmatch(r"([A-Z]+)(\d+)", device)
        if not m:
            continue
        dev_type, number_s = m.group(1), m.group(2)
        if only_types and dev_type not in only_types:
            continue
        number = int(number_s)
        devices.append(f"{role}:{device}")
        info = comments.get((dev_type, number), CommentInfo())
        text = info.japanese or info.english
        if text:
            comment_texts.append(f"{role}:{device}={text}")
    return "; ".join(devices), "; ".join(comment_texts)


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    comments = load_comments()
    occurrences, stats = collect_ladder_occurrences(comments)
    usage = summarize_usage(occurrences)
    comm_areas = read_comm_areas()
    slmp_devices = read_slmp_candidates()

    occ_rows = []
    for occ in occurrences:
        info = comments.get((occ.device_type, occ.number), CommentInfo())
        occ_rows.append(
            {
                "device": occ.device,
                "device_type": occ.device_type,
                "number_raw": occ.number,
                "hex_address_candidate": hex_candidate(occ.device_type, occ.number),
                "role": occ.role,
                "role_kind": occ.role_kind,
                "comment_status": comment_status(info),
                "comment_ja": info.japanese,
                "comment_en": info.english,
                "lddb": occ.lddb,
                "pos": occ.pos,
                "block_id": occ.block_id,
                "title": occ.title,
                "parse_status": occ.parse_status,
            }
        )

    io_keys = {
        key
        for key in comments
        if key[0] in {"X", "Y"}
    } | {key for key in usage if key[0] in {"X", "Y"}}
    io_rows = []
    for dev_type, number in sorted(io_keys, key=lambda k: (k[0], k[1])):
        info = comments.get((dev_type, number), CommentInfo())
        rec = usage.get((dev_type, number), {})
        io_rows.append(
            {
                "device": format_device(dev_type, number),
                "device_type": dev_type,
                "number_raw": number,
                "hex_address_candidate": hex_candidate(dev_type, number),
                "area_source": classify_area(dev_type, number, comm_areas, slmp_devices),
                "comment_status": comment_status(info),
                "comment_ja": info.japanese,
                "comment_en": info.english,
                "used_in_ladder": "yes" if rec else "no",
                "occurrence_count": rec.get("occurrence_count", 0),
                "contact_a_count": rec.get("contact_a_count", 0),
                "contact_b_count": rec.get("contact_b_count", 0),
                "coil_count": rec.get("coil_count", 0),
                "first_lddb": rec.get("first_lddb", ""),
                "first_pos": rec.get("first_pos", ""),
                "first_title": rec.get("first_title", ""),
            }
        )

    input_monitor_keys: set[tuple[str, int]] = set()
    for occ in occurrences:
        if occ.role in {"a", "b"} and occ.device_type in {"X", "B", "W", "SW"}:
            input_monitor_keys.add((occ.device_type, occ.number))
    for key in comments:
        if key[0] in {"B", "SW"}:
            input_monitor_keys.add(key)
    for row in comm_areas:
        if row["direction"] in {"incoming_to_plc", "diagnostic/status/control"}:
            dev_type = str(row["device_type"])
            if dev_type in {"X", "SW"}:
                for n in range(int(row["start_no"]), int(row["end_no"]) + 1):
                    if (dev_type, n) in comments or (dev_type, n) in usage:
                        input_monitor_keys.add((dev_type, n))
            elif dev_type == "W":
                for n in range(int(row["start_no"]), int(row["end_no"]) + 1):
                    if (dev_type, n) in comments or (dev_type, n) in usage:
                        input_monitor_keys.add((dev_type, n))

    input_rows = []
    for dev_type, number in sorted(input_monitor_keys, key=lambda k: (k[0], k[1])):
        info = comments.get((dev_type, number), CommentInfo())
        rec = usage.get((dev_type, number), {})
        area = classify_area(dev_type, number, comm_areas, slmp_devices)
        if dev_type == "X":
            group = "physical_or_remote_input"
        elif dev_type in {"B", "SW"}:
            group = "communication_status_bit"
        elif dev_type == "W":
            group = "communication_word_data"
        else:
            group = "monitor_candidate"
        input_rows.append(
            {
                "monitor_group": group,
                "device": format_device(dev_type, number),
                "device_type": dev_type,
                "number_raw": number,
                "hex_address_candidate": hex_candidate(dev_type, number),
                "area_source": area,
                "comment_status": comment_status(info),
                "comment_ja": info.japanese,
                "comment_en": info.english,
                "occurrence_count": rec.get("occurrence_count", 0),
                "contact_a_count": rec.get("contact_a_count", 0),
                "contact_b_count": rec.get("contact_b_count", 0),
                "first_title": rec.get("first_title", ""),
                "first_lddb": rec.get("first_lddb", ""),
                "first_pos": rec.get("first_pos", ""),
            }
        )

    y_coil_occ = [occ for occ in occurrences if occ.device_type == "Y" and occ.role == "c"]
    y_first_by_device: dict[int, Occurrence] = {}
    y_counts = Counter()
    for occ in y_coil_occ:
        y_counts[occ.number] += 1
        y_first_by_device.setdefault(occ.number, occ)

    output_rows = []
    single_rows = []
    for number in sorted(y_first_by_device):
        occ = y_first_by_device[number]
        info = comments.get(("Y", number), CommentInfo())
        all_text = " ".join([info.japanese, info.english])
        searchable_text = " ".join([info.japanese, info.english, occ.title])
        cond_devices, cond_comments = make_condition_text(occ, comments)
        x_devices, x_comments = make_condition_text(occ, comments, {"X"})
        action_category = classify_action(all_text)
        row = {
            "device": format_device("Y", number),
            "number_raw": number,
            "hex_address_candidate": hex_candidate("Y", number),
            "area_source": classify_area("Y", number, comm_areas, slmp_devices),
            "comment_status": comment_status(info),
            "comment_ja": info.japanese,
            "comment_en": info.english,
            "coil_occurrence_count": y_counts[number],
            "action_category": action_category,
            "drive_title": occ.title,
            "drive_lddb": occ.lddb,
            "drive_pos": occ.pos,
            "drive_block_id": occ.block_id,
            "drive_conditions": cond_devices,
            "drive_condition_comments": cond_comments,
            "x_feedback_or_interlock_devices_in_drive_rung": x_devices,
            "x_feedback_or_interlock_comments": x_comments,
            "recommended_manual_note": "manual command should be ORed only before final output while keeping safety/interlock contacts",
        }
        output_rows.append(row)
        if (
            ACTION_KEYWORDS.search(searchable_text)
            and ("motor_cv" in action_category or "cylinder_actuator" in action_category)
            and "lamp_buzzer" not in action_category
            and "communication_external" not in action_category
        ):
            single_rows.append(row.copy())

    write_csv(
        OUT_LADDER_OCC,
        occ_rows,
        [
            "device",
            "device_type",
            "number_raw",
            "hex_address_candidate",
            "role",
            "role_kind",
            "comment_status",
            "comment_ja",
            "comment_en",
            "lddb",
            "pos",
            "block_id",
            "title",
            "parse_status",
        ],
    )
    write_csv(
        OUT_IO,
        io_rows,
        [
            "device",
            "device_type",
            "number_raw",
            "hex_address_candidate",
            "area_source",
            "comment_status",
            "comment_ja",
            "comment_en",
            "used_in_ladder",
            "occurrence_count",
            "contact_a_count",
            "contact_b_count",
            "coil_count",
            "first_lddb",
            "first_pos",
            "first_title",
        ],
    )
    write_csv(
        OUT_IO_MISSING,
        [row for row in io_rows if row["comment_status"] != "comment_ok" or row["used_in_ladder"] == "yes" and row["comment_status"] != "comment_ok"],
        [
            "device",
            "device_type",
            "number_raw",
            "hex_address_candidate",
            "area_source",
            "comment_status",
            "comment_ja",
            "comment_en",
            "used_in_ladder",
            "occurrence_count",
            "contact_a_count",
            "contact_b_count",
            "coil_count",
            "first_lddb",
            "first_pos",
            "first_title",
        ],
    )
    write_csv(
        OUT_INPUT_MONITOR,
        input_rows,
        [
            "monitor_group",
            "device",
            "device_type",
            "number_raw",
            "hex_address_candidate",
            "area_source",
            "comment_status",
            "comment_ja",
            "comment_en",
            "occurrence_count",
            "contact_a_count",
            "contact_b_count",
            "first_title",
            "first_lddb",
            "first_pos",
        ],
    )
    write_csv(
        OUT_OUTPUT_MANUAL,
        output_rows,
        [
            "device",
            "number_raw",
            "hex_address_candidate",
            "area_source",
            "comment_status",
            "comment_ja",
            "comment_en",
            "coil_occurrence_count",
            "action_category",
            "drive_title",
            "drive_lddb",
            "drive_pos",
            "drive_block_id",
            "drive_conditions",
            "drive_condition_comments",
            "x_feedback_or_interlock_devices_in_drive_rung",
            "x_feedback_or_interlock_comments",
            "recommended_manual_note",
        ],
    )
    write_csv(
        OUT_SINGLE_ACTION,
        single_rows,
        [
            "device",
            "number_raw",
            "hex_address_candidate",
            "area_source",
            "comment_status",
            "comment_ja",
            "comment_en",
            "coil_occurrence_count",
            "action_category",
            "drive_title",
            "drive_lddb",
            "drive_pos",
            "drive_block_id",
            "drive_conditions",
            "drive_condition_comments",
            "x_feedback_or_interlock_devices_in_drive_rung",
            "x_feedback_or_interlock_comments",
            "recommended_manual_note",
        ],
    )

    io_by_type = Counter(row["device_type"] for row in io_rows)
    io_missing = Counter(row["device_type"] for row in io_rows if row["comment_status"] != "comment_ok")
    used_io_missing = Counter(
        row["device_type"]
        for row in io_rows
        if row["used_in_ladder"] == "yes" and row["comment_status"] != "comment_ok"
    )
    action_counts = Counter(row["action_category"] for row in output_rows)

    lines = [
        "HMI / manual operation build information / HMI・手動操作用 抽出情報",
        "====================================================================",
        "",
        "目的:",
        "  現状回路から、次を作るための元情報を抽出する。",
        "  1. I/Oコメントを完璧にする",
        "  2. 入力モニタを作る",
        "  3. 出力の手動操作を作る",
        "  4. 各シリンダ・モータの単体動作確認を作る",
        "",
        "生成ファイル:",
        f"  {OUT_IO}: X/Y I/Oコメント台帳。コメント、回路使用有無、接点/コイル使用数を含む。",
        f"  {OUT_IO_MISSING}: X/Yコメント未登録・空欄のTODOリスト。",
        f"  {OUT_INPUT_MONITOR}: 入力モニタ候補。X、B、W、SWの監視候補を含む。",
        f"  {OUT_OUTPUT_MANUAL}: Y出力コイルの駆動回路一覧。手動操作の設計元情報。",
        f"  {OUT_SINGLE_ACTION}: シリンダ・モータ・ロック等の単体動作確認候補。",
        f"  {OUT_LADDER_OCC}: ラダー内デバイス出現一覧。詳細確認用。",
        "",
        "解析件数:",
        f"  exact_ladder_rows: {stats['exact_rows']}",
        f"  partial_ladder_rows: {stats['partial_rows']}",
        f"  total_device_occurrences: {stats['occurrences']}",
        "",
        "I/Oコメント台帳:",
    ]
    for dev_type in sorted(io_by_type):
        lines.append(
            f"  {dev_type}: {io_by_type[dev_type]}件, コメント未登録/空欄={io_missing[dev_type]}件, "
            f"回路使用ありで未登録/空欄={used_io_missing[dev_type]}件"
        )
    lines.extend(
        [
            "",
            "出力手動操作・単体動作候補:",
            f"  Yコイル出力: {len(output_rows)}件",
            f"  ランプ/通信系を除いた単体動作候補: {len(single_rows)}件",
        ]
    )
    for category, count in action_counts.most_common():
        lines.append(f"  {category}: {count}")
    lines.extend(
        [
            "",
            "重要メモ:",
            "  - number_raw はLDDB/コメントDBに保存されている番号。",
            "  - X/Y/B/W/SW はGX表示が16進表記に見える場合があるため、hex_address_candidate も併記した。",
            "  - 出力手動操作を作る場合、安全接点をバイパスしない。drive_conditions と x_feedback_or_interlock を見て、どこに手動指令をORするか決める。",
            "  - 単体動作候補はYコイルのコメント/タイトルをキーワード抽出したもの。実装前に対象/非対象をレビューする。",
        ]
    )
    OUT_SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Wrote {OUT_IO} ({len(io_rows)} rows)")
    print(f"Wrote {OUT_IO_MISSING}")
    print(f"Wrote {OUT_INPUT_MONITOR} ({len(input_rows)} rows)")
    print(f"Wrote {OUT_OUTPUT_MANUAL} ({len(output_rows)} rows)")
    print(f"Wrote {OUT_SINGLE_ACTION} ({len(single_rows)} rows)")
    print(f"Wrote {OUT_LADDER_OCC} ({len(occ_rows)} rows)")
    print(f"Wrote {OUT_SUMMARY}")


if __name__ == "__main__":
    main()
