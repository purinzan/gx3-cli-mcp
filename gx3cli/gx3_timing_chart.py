from __future__ import annotations

import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DetectedSignal:
    role: str
    direction: str
    sender_project: str
    sender_device: str
    receiver_project: str
    receiver_device: str
    sender_comment: str
    receiver_comment: str
    link_type: str
    confidence: str
    sender_condition: str = ""
    receiver_condition: str = ""


@dataclass(frozen=True)
class DataGroup:
    direction: str
    sender_project: str
    receiver_project: str
    devices: tuple[str, ...]
    receiver_row: str
    receiver_trigger: str
    receiver_action: str
    sender_condition: str
    confidence: str


DEVICE_RE = re.compile(r"^([A-Z]+)(\d+)$", re.IGNORECASE)
HANDSHAKE_ROLE_ORDER = [
    "auto",
    "ready",
    "request",
    "running",
    "data_flag",
    "normal",
    "complete",
]
ROLE_LABELS = {
    "auto": "Auto",
    "ready": "Ready",
    "request": "Request",
    "running": "Running",
    "data_flag": "Data",
    "normal": "Normal",
    "complete": "Complete",
    "unknown": "Unknown",
}


def markdown_table(headers: list[str], rows: list[list[str]]) -> str:
    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        out.append("| " + " | ".join(cell.replace("\n", "<br>") for cell in row) + " |")
    return "\n".join(out)


def write_or_print(text: str, output: str | None) -> None:
    if output:
        Path(output).write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)


def parse_device(device: str) -> tuple[str, int]:
    match = DEVICE_RE.match(device)
    if not match:
        return "", -1
    return match.group(1).upper(), int(match.group(2))


def role_kind(text: str) -> str:
    role = (text or "").casefold()
    if "request" in role or "req" in role:
        return "request"
    if "ready" in role or "available" in role or "accept" in role:
        return "ready"
    if "data" in role or "payload" in role or "word" in role:
        return "data_flag"
    if "normal" in role or "healthy" in role or "no fault" in role or "ok" in role:
        return "normal"
    if "complete" in role or "done" in role or "finish" in role:
        return "complete"
    if "running" in role or "busy" in role or "in progress" in role:
        return "running"
    if "auto" in role or "automatic" in role or "plc run" in role or "plcrun" in role:
        return "auto"
    return "unknown"


def open_link_map(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"link-map db not found: {path} (run link-map build first)")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def project_xref_paths(link_con: sqlite3.Connection) -> dict[str, Path]:
    return {
        str(row["label"]): Path(str(row["xref_db"]))
        for row in link_con.execute("select label, xref_db from project")
    }


def open_xref(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"xref db not found: {path}")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def first_comment(con: sqlite3.Connection, device: str) -> str:
    row = con.execute(
        "select comment from xref where device=? and comment<>'' limit 1",
        (device,),
    ).fetchone()
    return str(row["comment"]) if row else ""


def same_row_conditions(con: sqlite3.Connection, lddb: str, pos: int) -> str:
    rows = con.execute(
        """
        select device, role, comment
        from xref
        where lddb=? and pos=? and access='read' and role in ('a', 'b')
        order by id
        """,
        (lddb, pos),
    ).fetchall()
    roles_by_device: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        roles_by_device[str(row["device"])].add(str(row["role"]))
    parts: list[str] = []
    noted_both: set[str] = set()
    for row in rows:
        device = str(row["device"])
        if len(roles_by_device[device]) > 1:
            if device not in noted_both:
                noted_both.add(device)
                parts.append(f"({device} both a/b contacts in one row)")
            continue
        prefix = "/" if row["role"] == "b" else ""
        comment = f" {row['comment']}" if row["comment"] else ""
        parts.append(f"{prefix}{device}{comment}".strip())
    return " AND ".join(parts)


def device_writer_condition(con: sqlite3.Connection, device: str) -> str:
    row = con.execute(
        """
        select lddb, pos
        from xref
        where device=? and access in ('write', 'both')
        order by pos
        limit 1
        """,
        (device,),
    ).fetchone()
    if not row:
        return ""
    return same_row_conditions(con, str(row["lddb"]), int(row["pos"]))


def device_reader_condition(con: sqlite3.Connection, device: str) -> str:
    row = con.execute(
        """
        select lddb, pos
        from xref
        where device=? and access='read'
        order by pos
        limit 1
        """,
        (device,),
    ).fetchone()
    if not row:
        return ""
    return same_row_conditions(con, str(row["lddb"]), int(row["pos"]))


def const_numbers(text: str) -> list[int]:
    return [int(x) for x in re.findall(r"-?\d+", text or "")]


def instruction_span(opcode: str, const_args: str) -> int:
    op = opcode.upper()
    nums = const_numbers(const_args)
    if op in {"BMOV", "BMOVP"} and nums:
        return max(1, nums[0])
    if op in {"FMOV", "FMOVP"} and nums:
        return max(1, nums[-1])
    if op in {"DMOV", "DMOVP", "INT2DINT"}:
        return 2
    return 1


def expanded_w_reads(con: sqlite3.Connection, lddb: str, pos: int) -> tuple[str, ...]:
    devices: set[str] = set()
    for row in con.execute(
        """
        select device, opcode, role, const_args
        from xref
        where lddb=? and pos=? and access='read' and device_type='W'
        order by id
        """,
        (lddb, pos),
    ):
        prefix, number = parse_device(str(row["device"]))
        if prefix != "W":
            continue
        opcode = str(row["opcode"] or row["role"] or "")
        length = instruction_span(opcode, str(row["const_args"] or ""))
        for offset in range(length):
            devices.add(f"W{number + offset}")
    return tuple(sorted(devices, key=lambda d: parse_device(d)[1]))


def link_rows_between(link_con: sqlite3.Connection, project_a: str, project_b: str) -> list[sqlite3.Row]:
    return link_con.execute(
        """
        select *
        from link_map
        where (project_a=? and project_b=?) or (project_a=? and project_b=?)
        order by direction, role, device_a, device_b
        """,
        (project_a, project_b, project_b, project_a),
    ).fetchall()


def orient_link(row: sqlite3.Row, project_a: str, project_b: str) -> tuple[str, str, str, str]:
    direction = str(row["direction"] or "")
    if direction == f"{project_a}_to_{project_b}":
        sender_device = str(row["device_a"] if row["project_a"] == project_a else row["device_b"])
        receiver_device = str(row["device_b"] if row["project_b"] == project_b else row["device_a"])
        return project_a, sender_device, project_b, receiver_device
    if direction == f"{project_b}_to_{project_a}":
        sender_device = str(row["device_a"] if row["project_a"] == project_b else row["device_b"])
        receiver_device = str(row["device_b"] if row["project_b"] == project_a else row["device_a"])
        return project_b, sender_device, project_a, receiver_device
    return str(row["project_a"]), str(row["device_a"]), str(row["project_b"]), str(row["device_b"])


def detect_signals(project_a: str, project_b: str, link_db: Path) -> tuple[list[DetectedSignal], list[DataGroup]]:
    link_con = open_link_map(link_db)
    xref_paths = project_xref_paths(link_con)
    xrefs = {project: open_xref(path) for project, path in xref_paths.items()}
    try:
        links = link_rows_between(link_con, project_a, project_b)
        detected: list[DetectedSignal] = []
        for row in links:
            link_type = str(row["link_type"])
            role = str(row["role"] or "")
            kind = role_kind(role)
            dev_a = str(row["device_a"])
            dev_b = str(row["device_b"])
            type_a, _ = parse_device(dev_a)
            type_b, _ = parse_device(dev_b)
            if kind == "unknown" and not (link_type == "exact-device" and type_a == "W" and type_b == "W"):
                continue
            sender_project, sender_device, receiver_project, receiver_device = orient_link(row, project_a, project_b)
            if kind == "unknown":
                continue
            sender_con = xrefs.get(sender_project)
            receiver_con = xrefs.get(receiver_project)
            detected.append(
                DetectedSignal(
                    role=kind,
                    direction=str(row["direction"] or ""),
                    sender_project=sender_project,
                    sender_device=sender_device,
                    receiver_project=receiver_project,
                    receiver_device=receiver_device,
                    sender_comment=first_comment(sender_con, sender_device) if sender_con else "",
                    receiver_comment=first_comment(receiver_con, receiver_device) if receiver_con else "",
                    link_type=link_type,
                    confidence=str(row["confidence"] or ""),
                    sender_condition=device_writer_condition(sender_con, sender_device) if sender_con else "",
                    receiver_condition=device_reader_condition(receiver_con, receiver_device) if receiver_con else "",
                )
            )
        data_groups = detect_data_groups(project_a, project_b, links, xrefs)
        return dedupe_detected_signals(detected), data_groups
    finally:
        for con in xrefs.values():
            con.close()
        link_con.close()


def dedupe_detected_signals(signals: list[DetectedSignal]) -> list[DetectedSignal]:
    best: dict[tuple[str, str, str], DetectedSignal] = {}
    priority = {"high": 0, "medium": 1, "low": 2}

    def names_receiver(sig: DetectedSignal) -> bool:
        return sig.receiver_project.casefold() in (sig.sender_comment or "").casefold()

    def pair_offset(sig: DetectedSignal) -> int | None:
        sender_number = parse_device(sig.sender_device)[1]
        receiver_number = parse_device(sig.receiver_device)[1]
        if sender_number < 0 or receiver_number < 0:
            return None
        return sender_number - receiver_number

    offset_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    for sig in signals:
        offset = pair_offset(sig)
        if offset is not None:
            offset_counts[sig.direction][offset] += 1
    majority_offset: dict[str, int] = {}
    for direction, counts in offset_counts.items():
        offset, count = max(counts.items(), key=lambda item: item[1])
        if count >= 2:
            majority_offset[direction] = offset

    def matches_band(sig: DetectedSignal) -> bool:
        offset = pair_offset(sig)
        return offset is not None and majority_offset.get(sig.direction) == offset

    for sig in signals:
        key = (sig.role, sig.direction, sig.receiver_device)
        current = best.get(key)
        if current is None:
            best[key] = sig
            continue
        sig_priority = priority.get(sig.confidence, 9)
        current_priority = priority.get(current.confidence, 9)
        if sig_priority < current_priority:
            best[key] = sig
        elif sig_priority == current_priority:
            if matches_band(sig) and not matches_band(current):
                best[key] = sig
            elif matches_band(sig) == matches_band(current) and names_receiver(sig) and not names_receiver(current):
                best[key] = sig
    return sorted(
        best.values(),
        key=lambda s: (
            HANDSHAKE_ROLE_ORDER.index(s.role) if s.role in HANDSHAKE_ROLE_ORDER else 99,
            s.direction,
            s.sender_device,
            s.receiver_device,
        ),
    )


def detect_data_groups(
    project_a: str,
    project_b: str,
    links: list[sqlite3.Row],
    xrefs: dict[str, sqlite3.Connection],
) -> list[DataGroup]:
    candidate_links = []
    for row in links:
        dev_a = str(row["device_a"])
        dev_b = str(row["device_b"])
        type_a, _ = parse_device(dev_a)
        type_b, _ = parse_device(dev_b)
        if str(row["link_type"]) == "exact-device" and type_a == "W" and type_b == "W":
            candidate_links.append(row)

    by_receiver_row: dict[tuple[str, str, int, str], set[str]] = defaultdict(set)
    row_senders: dict[tuple[str, str, int, str], set[tuple[str, str]]] = defaultdict(set)
    for row in candidate_links:
        sender_project, sender_device, receiver_project, receiver_device = orient_link(row, project_a, project_b)
        if sender_project != project_a or receiver_project != project_b:
            continue
        receiver_con = xrefs.get(receiver_project)
        if not receiver_con:
            continue
        for rr in receiver_con.execute(
            """
            select lddb, pos, opcode, role
            from xref
            where device=? and access='read'
            order by pos
            """,
            (receiver_device,),
        ):
            opcode = str(rr["opcode"] or rr["role"] or "")
            if opcode.upper() not in {"BMOV", "BMOVP", "MOV", "MOVP", "DMOV", "DMOVP"}:
                continue
            key = (receiver_project, str(rr["lddb"]), int(rr["pos"]), opcode)
            by_receiver_row[key].add(receiver_device)
            row_senders[key].add((sender_project, sender_device))

    groups: list[tuple[int, DataGroup]] = []
    for key, devices in by_receiver_row.items():
        receiver_project, lddb, pos, opcode = key
        receiver_con = xrefs[receiver_project]
        senders = sorted(row_senders[key])
        sender_project = senders[0][0] if senders else project_a
        sender_condition = ""
        for candidate_project, candidate_device in senders:
            sender_con = xrefs.get(candidate_project)
            if not sender_con:
                continue
            sender_condition = device_writer_condition(sender_con, candidate_device)
            if sender_condition:
                sender_project = candidate_project
                break
        conditions = same_row_conditions(receiver_con, lddb, pos)
        action = data_receiver_action(receiver_con, lddb, pos)
        display_devices = expanded_w_reads(receiver_con, lddb, pos) or tuple(sorted(devices, key=lambda d: parse_device(d)[1]))
        score = len(devices)
        if "complete" in conditions.casefold() or "done" in conditions.casefold():
            score += 100
        if "L" in conditions:
            score += 10
        groups.append(
            (
                score,
                DataGroup(
                    direction=f"{sender_project}_to_{receiver_project}",
                    sender_project=sender_project,
                    receiver_project=receiver_project,
                    devices=display_devices,
                    receiver_row=f"{lddb}:{pos}",
                    receiver_trigger=conditions,
                    receiver_action=action or f"{opcode} read",
                    sender_condition=sender_condition,
                    confidence="draft",
                ),
            )
        )
    return [group for _score, group in sorted(groups, key=lambda item: item[0], reverse=True)[:5]]


def data_receiver_action(con: sqlite3.Connection, lddb: str, pos: int) -> str:
    rows = con.execute(
        """
        select device, device_type, number, access, opcode, role, arg_index, const_args, detail
        from xref
        where lddb=? and pos=? and access in ('read', 'write', 'both')
        order by id
        """,
        (lddb, pos),
    ).fetchall()
    actions: list[str] = []
    seen: set[str] = set()
    i = 0
    while i < len(rows):
        row = rows[i]
        opcode = str(row["opcode"] or row["role"] or "").upper()
        source = str(row["device"])
        if opcode in {"BMOV", "BMOVP", "MOV", "MOVP", "DMOV", "DMOVP"} and row["access"] == "read" and source.startswith("W"):
            target = ""
            for j in range(i + 1, len(rows)):
                candidate = rows[j]
                candidate_opcode = str(candidate["opcode"] or candidate["role"] or "").upper()
                if candidate_opcode != opcode:
                    continue
                if candidate["access"] in {"write", "both"}:
                    target = str(candidate["device"])
                    break
            if target:
                length = instruction_span(opcode, str(row["const_args"] or ""))
                count = f" K{length}" if opcode.startswith("BMOV") and length > 1 else ""
                text = f"{opcode} {source}{count} -> {target}"
                if text not in seen:
                    actions.append(text)
                    seen.add(text)
        i += 1

    for row in rows:
        opcode = str(row["opcode"] or row["role"] or "").upper()
        if opcode != "SET" or row["access"] not in {"write", "both"}:
            continue
        device = str(row["device"])
        detail = str(row["detail"] or "")
        if detail.startswith("bit=K0") and str(row["device_type"]) == "ZR":
            display = f"K0M{row['number']}"
        else:
            display = device
        text = f"SET {display}"
        if text not in seen:
            actions.append(text)
            seen.add(text)
    return "; ".join(actions)


def first_by_role(signals: list[DetectedSignal], role: str, direction: str | None = None) -> DetectedSignal | None:
    for sig in signals:
        if sig.role == role and (direction is None or sig.direction == direction):
            return sig
    return None


def render_detected_markdown(project_a: str, project_b: str, signals: list[DetectedSignal], data_groups: list[DataGroup]) -> str:
    a_to_b = f"{project_a}_to_{project_b}"
    b_to_a = f"{project_b}_to_{project_a}"
    ready = first_by_role(signals, "ready", b_to_a)
    request = first_by_role(signals, "request", a_to_b)
    running = first_by_role(signals, "running", a_to_b)
    complete = first_by_role(signals, "complete", b_to_a)
    normal = first_by_role(signals, "normal", b_to_a) or first_by_role(signals, "normal")
    data = data_groups[0] if data_groups else None

    phase_rows = [
        ["S0", "Idle", f"{project_a}: waiting", f"{project_b}: waiting", "Initial state; unknown conditions require review"],
        ["S1", "Ready", signal_cell(ready, receiver_side=True), signal_cell(ready), condition_cell(ready, "sender")],
        ["S2", "Request", signal_cell(request), signal_cell(request, receiver_side=True), condition_cell(request, "sender")],
        ["S3", "Running/Data Valid", signal_cell(running), signal_cell(running, receiver_side=True), condition_cell(running, "sender")],
        ["S4", "Data Capture", f"valid data: {data.sender_condition if data else '?'}", data_capture_cell(data), data.receiver_trigger if data else "?"],
        ["S5", "Complete", signal_cell(complete, receiver_side=True), signal_cell(complete), condition_cell(complete, "sender")],
        ["S6", "Normal", f"{project_a}: confirm local complete bits", signal_cell(normal), condition_cell(normal, "sender")],
    ]

    signal_rows = []
    for sig in signals:
        signal_rows.append(
            [
                ROLE_LABELS.get(sig.role, sig.role),
                sig.direction,
                f"{sig.sender_project}:{sig.sender_device}",
                sig.sender_comment,
                f"{sig.receiver_project}:{sig.receiver_device}",
                sig.receiver_comment,
                sig.sender_condition or "?",
                sig.confidence,
            ]
        )
    data_rows = [
        [
            group.direction,
            ", ".join(group.devices),
            group.sender_condition or "?",
            group.receiver_row,
            group.receiver_trigger or "?",
            group.receiver_action or "?",
            group.confidence,
        ]
        for group in data_groups
    ]

    sections = [
        f"# Detected Handoff Timing Draft: {project_a} -> {project_b}",
        "",
        "This draft is generated from link_map and xref DBs. Items with `?` need human confirmation.",
        "",
        "## Draft Phases",
        markdown_table(["Step", "Event", project_a, project_b, "Condition / Evidence"], phase_rows),
        "",
        "Note: condition cells are same-row contacts joined with AND. Parallel branches may still require manual review.",
        "",
        "## Detected Signals",
        markdown_table(
            ["Role", "Direction", "Sender", "Sender Comment", "Receiver", "Receiver Comment", "Sender ON/Valid Condition", "Confidence"],
            signal_rows or [["?", "?", "?", "?", "?", "?", "?", "?"]],
        ),
        "",
        "## Data Capture Candidates",
        markdown_table(
            ["Direction", "W Devices", "Sender Valid Condition", "Receiver Row", "Receiver Trigger", "Receiver Action", "Confidence"],
            data_rows or [["?", "?", "?", "?", "?", "?", "?"]],
        ),
        "",
        "## Mermaid",
        render_detected_mermaid(project_a, project_b, ready, request, running, data, complete, normal),
        "",
        "## Notes",
        "- Word-device rows are grouped by receiver read row.",
        "- Sender valid conditions are xref same-row contact lists, not topology-perfect ladder logic.",
        "- This command does not prove final internal completion bits; use xref and trace-device for the listed devices.",
    ]
    return "\n".join(sections) + "\n"


def signal_cell(sig: DetectedSignal | None, receiver_side: bool = False) -> str:
    if not sig:
        return "?"
    if receiver_side:
        return f"{sig.receiver_device} {sig.receiver_comment}".strip()
    return f"{sig.sender_device} {sig.sender_comment}".strip()


def condition_cell(sig: DetectedSignal | None, side: str) -> str:
    if not sig:
        return "?"
    return sig.sender_condition if side == "sender" else sig.receiver_condition


def data_capture_cell(group: DataGroup | None) -> str:
    if not group:
        return "?"
    return f"{', '.join(group.devices)} read at {group.receiver_row}: {group.receiver_action}"


def render_detected_mermaid(
    project_a: str,
    project_b: str,
    ready: DetectedSignal | None,
    request: DetectedSignal | None,
    running: DetectedSignal | None,
    data: DataGroup | None,
    complete: DetectedSignal | None,
    normal: DetectedSignal | None,
) -> str:
    def msg(sig: DetectedSignal | None, fallback: str) -> str:
        if not sig:
            return fallback + " ?"
        return f"{ROLE_LABELS.get(sig.role, sig.role)} {sig.sender_device}->{sig.receiver_device}"

    lines = [
        "```mermaid",
        "sequenceDiagram",
        f"    participant A as {project_a}",
        f"    participant B as {project_b}",
        f"    B-->>A: {msg(ready, 'ready')}",
        f"    A-->>B: {msg(request, 'request')}",
        f"    A-->>B: {msg(running, 'running')}",
    ]
    if data:
        lines.append(f"    A-->>B: data {', '.join(data.devices)}")
        lines.append(f"    B->>B: capture at {data.receiver_trigger or data.receiver_row}")
    else:
        lines.append("    A-->>B: data ?")
        lines.append("    B->>B: capture ?")
    lines.extend(
        [
            f"    B-->>A: {msg(complete, 'complete')}",
            f"    B-->>A: {msg(normal, 'normal')}",
            "```",
        ]
    )
    return "\n".join(lines)


def render_detected_csv(signals: list[DetectedSignal], data_groups: list[DataGroup]) -> str:
    import io

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(["kind", "role", "direction", "sender", "receiver", "sender_comment", "receiver_comment", "condition", "confidence"])
    for sig in signals:
        writer.writerow(
            [
                "signal",
                sig.role,
                sig.direction,
                f"{sig.sender_project}:{sig.sender_device}",
                f"{sig.receiver_project}:{sig.receiver_device}",
                sig.sender_comment,
                sig.receiver_comment,
                sig.sender_condition,
                sig.confidence,
            ]
        )
    for group in data_groups:
        writer.writerow(
            [
                "data",
                "data_word",
                group.direction,
                group.sender_project,
                group.receiver_project,
                "",
                ", ".join(group.devices),
                f"send={group.sender_condition}; receive={group.receiver_trigger}; action={group.receiver_action}",
                group.confidence,
            ]
        )
    return buffer.getvalue()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate generic GX3 handoff timing drafts from link-map and xref DBs.")
    sub = parser.add_subparsers(dest="scenario", required=True)
    p = sub.add_parser("detect", help="detect a handoff draft from link-map and xref DBs")
    p.add_argument("project_a", help="upstream/sender project label")
    p.add_argument("project_b", help="downstream/receiver project label")
    p.add_argument("--link-db", default=".gx3_index/link_map.sqlite")
    p.add_argument("--format", choices=["markdown", "csv", "mermaid"], default="markdown")
    p.add_argument("-o", "--output", help="write output to file instead of stdout")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    signals, data_groups = detect_signals(args.project_a, args.project_b, Path(args.link_db))
    if args.format == "markdown":
        text = render_detected_markdown(args.project_a, args.project_b, signals, data_groups)
    elif args.format == "csv":
        text = render_detected_csv(signals, data_groups)
    else:
        ready = first_by_role(signals, "ready", f"{args.project_b}_to_{args.project_a}")
        request = first_by_role(signals, "request", f"{args.project_a}_to_{args.project_b}")
        running = first_by_role(signals, "running", f"{args.project_a}_to_{args.project_b}")
        complete = first_by_role(signals, "complete", f"{args.project_b}_to_{args.project_a}")
        normal = first_by_role(signals, "normal", f"{args.project_b}_to_{args.project_a}") or first_by_role(signals, "normal")
        text = render_detected_mermaid(
            args.project_a,
            args.project_b,
            ready,
            request,
            running,
            data_groups[0] if data_groups else None,
            complete,
            normal,
        ) + "\n"
    write_or_print(text, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
