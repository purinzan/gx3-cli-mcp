from __future__ import annotations

"""Static scan-order checks for GX Works3 cross references.

The command compares the program order of xref writers/readers for one device,
or for all pulse-like devices.  It is meant to catch the common intermittent
case where a reader executes before the writer that updates the same signal,
so the reader observes the previous scan's value.

"""

import argparse
import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from gx3cli.gx3_exec_config import program_file_names
from gx3cli.gx3_device_name import BIT_DEVICE_TYPES
from gx3cli.gx3_program_map import PouInfo, load_program_map
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.gx3_xref import default_db_path, normalize_device
from gx3cli.gx3_instruction_table import is_edge_triggered


WRITE_ACCESS = {"write", "both"}
READ_ACCESS = {"read", "both"}
EDGE_WRITE_OPS = {
    "PLS",
    "PLF",
    "MOVP",
    "DMOVP",
    "BMOVP",
    "FMOVP",
    "INCP",
    "DINCP",
    "DECP",
    "DDECP",
}
ONE_SCAN_BIT_WRITE_OPS = {"PLS", "PLF"}
PULSE_COMMENT_RE = re.compile(
    r"(pulse|edge|one[- ]?shot|pls|plf|立上|立下|立ち上|立ち下|ﾜﾝｼｮｯﾄ|ワンショット|ﾊﾟﾙｽ|パルス)",
    re.IGNORECASE,
)
DEVICE_RE = re.compile(r"^([A-Z]+)(\d+)$", re.IGNORECASE)


@dataclass(frozen=True)
class PouOrder:
    pou: str
    lddb_hex: str
    seq: int
    program_file: str
    exec_order: int | None
    program_dir: str
    link_index: int
    confidence: str
    note: str


@dataclass(frozen=True)
class XrefRow:
    row_id: int
    device: str
    access: str
    role: str
    opcode: str
    lddb: str
    pos: int
    pou: str
    step: int | None
    title: str
    comment: str

    @property
    def op(self) -> str:
        return (self.opcode or self.role or "").upper()


@dataclass(frozen=True)
class Finding:
    device: str
    comment: str
    severity: str
    status: str
    classification: str
    reason: str
    order_basis: str
    writer: XrefRow
    reader: XrefRow
    writer_seq: int | None
    reader_seq: int | None
    order_confidence: str


def match_program_file(first_pou_name: str, names: list[str]) -> str:
    if not first_pou_name:
        return ""
    for name in names:
        if (
            name == first_pou_name
            or (name.startswith("PF") and name[2:] == first_pou_name)
            or name.rstrip("_") == first_pou_name.rstrip("_")
            or name.split("_")[0] == first_pou_name
        ):
            return name
    return ""


def build_pou_order(root: Path) -> dict[str, PouOrder]:
    pm = load_program_map(root)
    names = program_file_names(root)
    order_of = {name: idx for idx, name in enumerate(names)}

    groups: dict[str, list[PouInfo]] = {}
    group_seen_index: dict[str, int] = {}
    for info in pm.pous.values():
        group = info.program_dir or "?"
        if group not in group_seen_index:
            group_seen_index[group] = len(group_seen_index)
        groups.setdefault(group, []).append(info)
    for members in groups.values():
        members.sort(key=lambda item: item.link_index)

    matched_label: dict[str, str] = {}
    for group, members in groups.items():
        pf = members[0].program_file if members else ""
        if pf and pf in order_of:
            matched_label[group] = pf
            continue
        first = members[0].name if members else ""
        label = match_program_file(first, names)
        if label:
            matched_label[group] = label

    orders: dict[str, PouOrder] = {}
    for group, members in groups.items():
        label = matched_label.get(group, "")
        exec_order = order_of.get(label)
        if exec_order is not None:
            group_seq = exec_order
            confidence = "exec"
            note = "CPU.PRM program-file match"
        else:
            group_seq = group_seen_index[group]
            confidence = "fallback"
            note = "program-file unresolved; using ProgramMap discovery order"
        for member in members:
            link_index = member.link_index if member.link_index >= 0 else 0
            seq = group_seq * 100000 + link_index
            pou = member.name or member.lddb_hex
            orders[pou] = PouOrder(
                pou=pou,
                lddb_hex=member.lddb_hex,
                seq=seq,
                program_file=label,
                exec_order=exec_order,
                program_dir=group if group != "?" else "",
                link_index=member.link_index,
                confidence=confidence,
                note=note,
            )
    return orders


def sync_pou_order(db_path: Path, orders: dict[str, PouOrder]) -> None:
    con = sqlite3.connect(db_path)
    con.executescript(
        """
        create table if not exists pou_order(
            pou text primary key,
            seq integer not null,
            lddb_hex text,
            program_file text,
            exec_order integer,
            program_dir text,
            link_index integer,
            confidence text,
            note text
        );
        delete from pou_order;
        """
    )
    con.executemany(
        """
        insert into pou_order(
            pou, seq, lddb_hex, program_file, exec_order, program_dir,
            link_index, confidence, note
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                item.pou,
                item.seq,
                item.lddb_hex,
                item.program_file,
                item.exec_order,
                item.program_dir,
                item.link_index,
                item.confidence,
                item.note,
            )
            for item in sorted(orders.values(), key=lambda x: x.seq)
        ],
    )
    con.execute(
        "insert or replace into meta(key, value) values ('pou_order_rows', ?)",
        (str(len(orders)),),
    )
    conf = Counter(item.confidence for item in orders.values())
    con.execute(
        "insert or replace into meta(key, value) values ('pou_order_confidence', ?)",
        (",".join(f"{key}:{conf[key]}" for key in sorted(conf)),),
    )
    con.commit()
    con.close()


def open_xref(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise SystemExit(f"xref db not found: {db_path} (run: gx3_cli.py xref build)")
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con


def to_xref_row(row: sqlite3.Row) -> XrefRow:
    return XrefRow(
        row_id=int(row["id"]),
        device=str(row["device"]),
        access=str(row["access"]),
        role=str(row["role"] or ""),
        opcode=str(row["opcode"] or ""),
        lddb=str(row["lddb"]),
        pos=int(row["pos"]),
        pou=str(row["pou"] or row["lddb"].split("_")[0]),
        step=int(row["step"]) if row["step"] is not None else None,
        title=str(row["title"] or ""),
        comment=str(row["comment"] or ""),
    )


def load_rows_for_device(con: sqlite3.Connection, device: str) -> list[XrefRow]:
    rows = con.execute(
        """
        select * from xref
        where device=?
          and access in ('read', 'write', 'both')
        order by pou, pos, id
        """,
        (device,),
    ).fetchall()
    return [to_xref_row(row) for row in rows]


def load_all_device_rows(con: sqlite3.Connection) -> dict[str, list[XrefRow]]:
    grouped: dict[str, list[XrefRow]] = defaultdict(list)
    for row in con.execute(
        """
        select * from xref
        where access in ('read', 'write', 'both')
        order by device_type, number, pou, pos, id
        """
    ):
        ref = to_xref_row(row)
        grouped[ref.device].append(ref)
    return grouped


def device_comment(rows: list[XrefRow]) -> str:
    return next((row.comment for row in rows if row.comment), "")


def writers(rows: list[XrefRow]) -> list[XrefRow]:
    return [row for row in rows if row.access in WRITE_ACCESS]


def readers(rows: list[XrefRow]) -> list[XrefRow]:
    return [row for row in rows if row.access in READ_ACCESS]


def device_type(device: str) -> str:
    match = DEVICE_RE.match(device)
    return match.group(1).upper() if match else ""


def is_bit_device(device: str) -> bool:
    return device_type(device) in BIT_DEVICE_TYPES


def is_edge_write_op(op: str) -> bool:
    """Does this instruction write once per transition rather than every scan?

    The manuals draw the execution condition beside every instruction, so the
    answer is looked up rather than guessed from the opcode ending in "P".
    That guess was wrong both ways: EXP, NOP, JMP, MPP and PSTOP end in P
    without being pulse forms, and every unsigned pulse instruction ends in
    "_U" instead -- so "+P_U" and the 200-odd like it were read as running
    every scan.

    EDGE_WRITE_OPS stays as the fallback for opcodes the manuals do not carry.
    """
    known = is_edge_triggered(op)
    if known is not None:
        return known
    return op in EDGE_WRITE_OPS or (op.endswith("P") and len(op) > 1)


def traits_for_rows(rows: list[XrefRow]) -> list[str]:
    wrows = writers(rows)
    dev = rows[0].device if rows else ""
    bit = is_bit_device(dev)
    traits: list[str] = []
    if bit and any(row.op in ONE_SCAN_BIT_WRITE_OPS for row in wrows):
        traits.append("one-scan-pulse-bit")
    text = " ".join([device_comment(rows), *[row.title for row in wrows], *[row.op for row in wrows]])
    if bit and PULSE_COMMENT_RE.search(text):
        traits.append("pulse-comment-bit")
    if any(is_edge_write_op(row.op) for row in wrows):
        traits.append("edge-executed-holding-write")
    if any(w.lddb == r.lddb and w.pos == r.pos for w in wrows for r in readers(rows)):
        traits.append("self-row")
    if not traits:
        traits.append("non-pulse")
    return traits


def is_pulse_candidate(rows: list[XrefRow]) -> bool:
    traits = traits_for_rows(rows)
    return any(
        trait in traits
        for trait in {"one-scan-pulse-bit", "pulse-comment-bit", "edge-executed-holding-write"}
    )


def classification(rows: list[XrefRow]) -> str:
    return ",".join(traits_for_rows(rows))


def compare_pair(
    writer: XrefRow,
    reader: XrefRow,
    orders: dict[str, PouOrder],
    trust_fallback_order: bool,
) -> tuple[str, str, str, int | None, int | None, str]:
    if writer.pou == reader.pou:
        if writer.pos > reader.pos:
            return (
                "stale-read",
                "static row order: writer row is after reader row in the same POU",
                "same-pou-pos",
                writer.pos,
                reader.pos,
                "certain",
            )
        if writer.pos < reader.pos:
            return (
                "fresh-read",
                "static row order: writer row is before reader row in the same POU",
                "same-pou-pos",
                writer.pos,
                reader.pos,
                "certain",
            )
        return (
            "same-row",
            "writer and reader are on the same ladder row",
            "same-row",
            writer.pos,
            reader.pos,
            "same-row",
        )

    worder = orders.get(writer.pou)
    rorder = orders.get(reader.pou)
    if worder is None or rorder is None:
        return (
            "unknown-order",
            "POU order is missing for writer or reader",
            "pou-order-missing",
            worder.seq if worder else None,
            rorder.seq if rorder else None,
            "missing",
        )
    if worder.confidence != "exec" or rorder.confidence != "exec":
        return (
            "unknown-order",
            "program-file order is unresolved; fallback discovery order is not used for stale/fresh judgment",
            "pou-order-fallback",
            worder.seq,
            rorder.seq,
            f"{worder.confidence}/{rorder.confidence}",
        )
    if worder.seq > rorder.seq:
        return (
            "stale-read",
            "writer POU executes after reader POU",
            "pou-order",
            worder.seq,
            rorder.seq,
            f"{worder.confidence}/{rorder.confidence}",
        )
    if worder.seq < rorder.seq:
        return (
            "fresh-read",
            "writer POU executes before reader POU",
            "pou-order",
            worder.seq,
            rorder.seq,
            f"{worder.confidence}/{rorder.confidence}",
        )
    return (
        "unknown-order",
        "writer and reader have the same POU sequence but are different POUs",
        "pou-order-tie",
        worder.seq,
        rorder.seq,
        f"{worder.confidence}/{rorder.confidence}",
    )


def severity_for_stale(device: str, traits: list[str]) -> str:
    if "one-scan-pulse-bit" in traits or "pulse-comment-bit" in traits:
        return "HIGH"
    if "edge-executed-holding-write" in traits:
        return "LOW"
    if is_bit_device(device):
        return "MEDIUM"
    return "LOW"


def scan_rows(
    device: str,
    rows: list[XrefRow],
    orders: dict[str, PouOrder],
    trust_fallback_order: bool,
) -> list[Finding]:
    wrows = writers(rows)
    rrows = readers(rows)
    traits = traits_for_rows(rows)
    klass = ",".join(traits)
    comment = device_comment(rows)
    findings: list[Finding] = []
    for writer in wrows:
        for reader in rrows:
            status, reason, basis, writer_seq, reader_seq, confidence = compare_pair(
                writer, reader, orders, trust_fallback_order
            )
            if status == "fresh-read" or status == "same-row":
                severity = "OK"
            elif status == "stale-read":
                severity = severity_for_stale(device, traits)
            else:
                severity = "UNKNOWN"
            findings.append(
                Finding(
                    device=device,
                    comment=comment,
                    severity=severity,
                    status=status,
                    classification=klass,
                    reason=reason,
                    order_basis=basis,
                    writer=writer,
                    reader=reader,
                    writer_seq=writer_seq,
                    reader_seq=reader_seq,
                    order_confidence=confidence,
                )
            )
    return findings


def compact(text: str, width: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= width:
        return text
    return text[: max(0, width - 3)] + "..."


def fmt_ref(row: XrefRow) -> str:
    step = f"st{row.step}" if row.step is not None else "st?"
    return f"{row.pou}:{step}:pos{row.pos}:{row.op or row.role}"


def print_table(rows: list[dict[str, object]], fields: list[str], limit: int | None = None) -> None:
    shown = rows if limit is None else rows[:limit]
    if not shown:
        print("(none)")
        return
    widths = {
        field: min(
            80,
            max(len(field), *(len(str(row.get(field, ""))) for row in shown)),
        )
        for field in fields
    }
    print("  ".join(field.ljust(widths[field]) for field in fields))
    print("  ".join("-" * widths[field] for field in fields))
    for row in shown:
        print("  ".join(str(row.get(field, "")).ljust(widths[field]) for field in fields))
    if limit is not None and len(rows) > limit:
        print(f"... {len(rows) - limit} more")


def finding_to_record(finding: Finding) -> dict[str, object]:
    return {
        "device": finding.device,
        "comment": finding.comment,
        "severity": finding.severity,
        "status": finding.status,
        "classification": finding.classification,
        "reason": finding.reason,
        "order_basis": finding.order_basis,
        "order_confidence": finding.order_confidence,
        "writer_pou": finding.writer.pou,
        "writer_step": finding.writer.step if finding.writer.step is not None else "",
        "writer_pos": finding.writer.pos,
        "writer_opcode": finding.writer.op,
        "writer_title": finding.writer.title,
        "writer_seq": finding.writer_seq if finding.writer_seq is not None else "",
        "reader_pou": finding.reader.pou,
        "reader_step": finding.reader.step if finding.reader.step is not None else "",
        "reader_pos": finding.reader.pos,
        "reader_opcode": finding.reader.op,
        "reader_title": finding.reader.title,
        "reader_seq": finding.reader_seq if finding.reader_seq is not None else "",
    }


CSV_FIELDS = [
    "device",
    "comment",
    "severity",
    "status",
    "classification",
    "reason",
    "order_basis",
    "order_confidence",
    "writer_pou",
    "writer_step",
    "writer_pos",
    "writer_opcode",
    "writer_title",
    "writer_seq",
    "reader_pou",
    "reader_step",
    "reader_pos",
    "reader_opcode",
    "reader_title",
    "reader_seq",
]


def write_csv(path: Path, findings: list[Finding]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for finding in findings:
            writer.writerow(finding_to_record(finding))


def filter_findings(args: argparse.Namespace, findings: list[Finding]) -> list[Finding]:
    if args.include_ok:
        return findings
    allowed = {"stale-read"}
    if args.include_unknown:
        allowed.add("unknown-order")
    return [finding for finding in findings if finding.status in allowed]


def command_one(args: argparse.Namespace, con: sqlite3.Connection, orders: dict[str, PouOrder]) -> int:
    device = normalize_device(args.device)
    rows = load_rows_for_device(con, device)
    if not rows:
        print(f"no xref rows found for {device}")
        return 1
    findings = scan_rows(device, rows, orders, args.trust_fallback_order)
    filtered = filter_findings(args, findings)
    if args.output:
        write_csv(Path(args.output), filtered)
        print(f"scan-order CSV: {args.output} rows={len(filtered)}")
    if args.format == "csv":
        if not args.output:
            raise SystemExit("--format csv requires -o/--output")
        return 0

    conf = Counter(item.confidence for item in orders.values())
    print(f"scan-order: {device} {device_comment(rows)}".rstrip())
    print(f"writers={len(writers(rows))} readers={len(readers(rows))} classification={classification(rows)}")
    print(
        "pou_order: "
        + ", ".join(f"{key}={conf[key]}" for key in sorted(conf))
        + " (fallback order is reported only, never used for stale/fresh judgment)"
    )
    print("")
    rows_out = []
    for finding in filtered:
        rows_out.append(
            {
                "severity": finding.severity,
                "status": finding.status,
                "writer": compact(fmt_ref(finding.writer), 44),
                "reader": compact(fmt_ref(finding.reader), 44),
                "basis": finding.order_basis,
                "reason": compact(finding.reason, 64),
            }
        )
    print_table(rows_out, ["severity", "status", "writer", "reader", "basis", "reason"], args.limit)
    if not filtered:
        print("no stale-read candidates under current options")
    unknown_count = sum(1 for finding in findings if finding.status == "unknown-order")
    if unknown_count and not args.include_unknown:
        print(f"\nunknown-order pairs suppressed: {unknown_count} (use --include-unknown)")
    ok_count = sum(1 for finding in findings if finding.status in {"fresh-read", "same-row"})
    if ok_count and not args.include_ok:
        print(f"OK/same-row pairs suppressed: {ok_count} (use --include-ok)")
    return 0


def command_all(args: argparse.Namespace, con: sqlite3.Connection, orders: dict[str, PouOrder]) -> int:
    grouped = load_all_device_rows(con)
    all_findings: list[Finding] = []
    scanned_devices = 0
    skipped_non_pulse = 0
    for device, rows in grouped.items():
        if not writers(rows) or not readers(rows):
            continue
        if args.pulse_only and not is_pulse_candidate(rows):
            skipped_non_pulse += 1
            continue
        scanned_devices += 1
        all_findings.extend(scan_rows(device, rows, orders, args.trust_fallback_order))
    filtered = filter_findings(args, all_findings)
    filtered.sort(
        key=lambda finding: (
            {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "UNKNOWN": 3, "OK": 4}.get(finding.severity, 9),
            finding.device,
            finding.reader.pou,
            finding.reader.pos,
        )
    )
    if args.output:
        write_csv(Path(args.output), filtered)
        print(f"scan-order CSV: {args.output} rows={len(filtered)} scanned_devices={scanned_devices}")
    if args.format == "csv":
        return 0

    summary = Counter(finding.severity for finding in filtered)
    print(
        f"scan-order --all: devices={len(grouped)} scanned={scanned_devices} "
        f"skipped_non_pulse={skipped_non_pulse}"
    )
    print("findings: " + (", ".join(f"{key}={summary[key]}" for key in sorted(summary)) or "none"))
    rows_out = [
        {
            "severity": finding.severity,
            "device": finding.device,
            "comment": compact(finding.comment, 28),
            "status": finding.status,
            "writer": compact(fmt_ref(finding.writer), 36),
            "reader": compact(fmt_ref(finding.reader), 36),
            "reason": compact(finding.reason, 48),
        }
        for finding in filtered
    ]
    print_table(rows_out, ["severity", "device", "comment", "status", "writer", "reader", "reason"], args.limit)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("device", nargs="?", help="target device; omit with --all")
    parser.add_argument("--all", action="store_true", help="scan all devices with at least one writer and reader")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--db", default=None, help="xref sqlite path")
    parser.add_argument("--format", choices=["text", "csv"], default="text")
    parser.add_argument("-o", "--output", help="CSV output path")
    parser.add_argument("--limit", type=int, default=80, help="maximum text rows to print")
    parser.add_argument(
        "--pulse-only",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="with --all, scan only pulse-like devices by default",
    )
    parser.add_argument("--include-unknown", action="store_true", help="include pairs with unresolved POU order")
    parser.add_argument("--include-ok", action="store_true", help="include fresh-read and same-row pairs")
    parser.add_argument(
        "--trust-fallback-order",
        action="store_true",
        help="deprecated compatibility flag; fallback discovery order is no longer used for stale/fresh judgment",
    )
    parser.add_argument(
        "--no-sync-db",
        action="store_true",
        help="do not write/update the pou_order table in the xref DB",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    if not args.all and not args.device:
        raise SystemExit("DEVICE is required unless --all is used")
    if args.format == "csv" and not args.output:
        raise SystemExit("--format csv requires -o/--output")

    root = Path(args.root)
    db_path = Path(args.db or default_db_path(root))
    orders = build_pou_order(root)
    if not args.no_sync_db:
        sync_pou_order(db_path, orders)
    con = open_xref(db_path)
    try:
        if args.all:
            return command_all(args, con, orders)
        return command_one(args, con, orders)
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
