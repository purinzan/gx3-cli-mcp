from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from gx3cli.gx3_device_name import canonical_device as _canonical_device, format_device as _format_device
from gx3cli.gx3_external_inputs import collect_external_inputs, load_refresh_areas, load_unit_io_areas
from gx3cli.gx3_project_paths import default_comm_prefix, default_project_root
from gx3cli.review_gx3_project import comment_for_device, load_comments_for_root, load_rows
from gx3cli.gx3_output import add_format_alias, fold_format_alias


DEVICE_RE = re.compile(r"^([A-Z]+)(-?\d+)$", re.IGNORECASE)
DRIVER_ROLES = {"c", "SET", "RST", "PLS", "PLF", "OUT__16", "OUTH__16"}
CONDITION_ROLES = {"a", "b"}
SYNONYM_GROUPS = [
    ("異常", "故障", "アラーム", "警報", "トラブル", "alarm", "fault", "error"),
    ("自動", "オート", "AUTO", "auto", "automatic"),
    ("手動", "マニュアル", "MANUAL", "manual"),
    ("起動", "始動", "スタート", "START", "start"),
    ("停止", "ストップ", "STOP", "stop"),
    ("完了", "終了", "COMPLETE", "complete", "done"),
    ("確認", "チェック", "OK", "check", "confirm"),
]


def clean_cell(value: object) -> str:
    return ("" if value is None else str(value)).replace("\r", " ").replace("\n", " ")


def default_project_label(root: Path | None = None) -> str:
    root = root or default_project_root()
    name = root.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"


def default_db_path(root: Path | None = None) -> str:
    return str(Path(".gx3_index") / f"{default_project_label(root)}.sqlite")


def normalize_device(value: str) -> str:
    try:
        return _canonical_device(value)
    except ValueError:
        raise SystemExit(f"invalid device: {value}") from None


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def create_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        drop table if exists meta;
        drop table if exists devices;
        drop table if exists comments;
        drop table if exists ladder_rows;
        drop table if exists device_usages;
        drop table if exists covered_ranges;
        drop table if exists external_sources;

        create table meta (
            key text primary key,
            value text not null
        );

        create table comments (
            device text primary key,
            device_type text not null,
            number integer not null,
            japanese text,
            english text,
            all_text text
        );

        -- The runs a block instruction or a digit specification covers. One
        -- row per run, not one per device it reaches: a project has tens of
        -- thousands of covered devices and listing them would drown the
        -- devices table, but a check that asks "is anything writing this?"
        -- has to be able to see them.
        create table covered_ranges (
            device_type text not null,
            start integer not null,
            length integer not null,
            access text not null,
            opcode text,
            lddb text,
            pos integer
        );

        create table devices (
            device text primary key,
            device_type text not null,
            number integer not null,
            comment text,
            occurrences integer not null,
            driver_rows integer not null,
            condition_uses integer not null,
            roles text,
            first_lddb text,
            first_pos integer,
            first_title text
        );

        create table ladder_rows (
            row_id text primary key,
            lddb text not null,
            pos integer not null,
            block_id text,
            title text,
            rowsize integer,
            parse_status text,
            devices text
        );

        create table device_usages (
            id integer primary key autoincrement,
            device text not null,
            device_type text not null,
            number integer not null,
            role text not null,
            is_driver integer not null,
            is_condition integer not null,
            row_id text not null,
            lddb text not null,
            pos integer not null,
            title text,
            parse_status text,
            row_conditions text,
            row_condition_comments text,
            row_all_devices text
        );

        create table external_sources (
            device text primary key,
            device_type text,
            number integer,
            comment text,
            occurrences integer,
            required_on_count integer,
            required_off_count integer,
            source_kind text,
            semantic_group text,
            source_detail text,
            stop_reason text,
            refresh_area text,
            refresh_network_label text,
            refresh_unit_name text,
            refresh_slot_number text,
            refresh_device_range text,
            source_unit_kind text,
            source_unit_name text,
            source_unit_connection text,
            source_unit_slot_number text,
            source_unit_area text,
            first_lddb text,
            first_pos integer,
            first_title text
        );

        create index idx_device_usages_device on device_usages(device);
        create index idx_device_usages_role on device_usages(role);
        create index idx_device_usages_row on device_usages(row_id);
        create index idx_devices_comment on devices(comment);
        create index idx_external_source_kind on external_sources(source_kind);
        """
    )


def row_id(lddb: str, pos: int, block_id: str = "") -> str:
    if block_id:
        return f"{lddb}:{pos}:{block_id}"
    return f"{lddb}:{pos}"


def build_index(args: argparse.Namespace) -> int:
    root = Path(args.root)
    out = Path(args.out or default_db_path(root))
    comm_dir = Path(args.comm_dir)
    refresh_csv = comm_dir / f"{args.comm_prefix}_refresh_areas.csv"
    unit_csv = comm_dir / f"{args.comm_prefix}_units.csv"

    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)
    refresh_areas = load_refresh_areas(refresh_csv)
    unit_io_areas = load_unit_io_areas(unit_csv)
    external_sources = collect_external_inputs(rows, comments, refresh_areas, unit_io_areas)

    con = connect(out)
    create_schema(con)
    con.executemany(
        "insert into meta(key, value) values (?, ?)",
        [
            ("root", str(root)),
            ("refresh_csv", str(refresh_csv)),
            ("unit_csv", str(unit_csv)),
            ("device_naming", DEVICE_NAMING),
            ("ladder_rows", str(len(rows))),
            ("external_sources", str(len(external_sources))),
        ],
    )

    comment_rows = []
    for (dev_type, number), info in sorted(comments.items()):
        device = _format_device(dev_type, number)
        comment_rows.append((device, dev_type, number, info.japanese, info.english, info.all_text))
    con.executemany(
        "insert into comments(device, device_type, number, japanese, english, all_text) values (?, ?, ?, ?, ?, ?)",
        comment_rows,
    )

    ladder_rows = []
    usage_rows = []
    device_stats: dict[str, dict[str, Any]] = {}
    covered_ranges: list[tuple[str, int, int, str, str, str, int]] = []
    for row in rows:
        rid = row_id(row.lddb, row.pos, row.block_id)
        ladder_rows.append(
            (
                rid,
                row.lddb,
                row.pos,
                row.block_id,
                row.title,
                row.rowsize,
                row.parse_status,
                json.dumps(row.occurrences[0].row_all_devices if row.occurrences else [], ensure_ascii=False),
            )
        )
        for occ in row.occurrences:
            # word writes (MOV destination etc.) count as drivers too
            is_driver = int(occ.role in DRIVER_ROLES or getattr(occ, "access", "") in ("write", "both"))
            is_condition = int(occ.role in CONDITION_ROLES)
            usage_rows.append(
                (
                    occ.device,
                    occ.device_type,
                    occ.number,
                    occ.role,
                    is_driver,
                    is_condition,
                    rid,
                    row.lddb,
                    row.pos,
                    row.title,
                    row.parse_status,
                    json.dumps(occ.row_conditions, ensure_ascii=False),
                    json.dumps(occ.row_condition_comments, ensure_ascii=False),
                    json.dumps(occ.row_all_devices, ensure_ascii=False),
                )
            )
            stat = device_stats.setdefault(
                occ.device,
                {
                    "device_type": occ.device_type,
                    "number": occ.number,
                    "occurrences": 0,
                    "driver_rows": set(),
                    "condition_uses": 0,
                    "roles": Counter(),
                    "first_lddb": row.lddb,
                    "first_pos": row.pos,
                    "first_title": row.title,
                },
            )
            if occ.range_len > 1:
                covered_ranges.append(
                    (occ.device_type, occ.number, occ.range_len, occ.access, occ.role, row.lddb, row.pos)
                )
            stat["occurrences"] += 1
            stat["roles"][occ.role] += 1
            if is_driver:
                stat["driver_rows"].add(rid)
            if is_condition:
                stat["condition_uses"] += 1

    con.executemany(
        """
        insert into covered_ranges(device_type, start, length, access, opcode, lddb, pos)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        covered_ranges,
    )
    con.executemany(
        """
        insert into ladder_rows(row_id, lddb, pos, block_id, title, rowsize, parse_status, devices)
        values (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ladder_rows,
    )
    con.executemany(
        """
        insert into device_usages(
            device, device_type, number, role, is_driver, is_condition, row_id, lddb, pos, title,
            parse_status, row_conditions, row_condition_comments, row_all_devices
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        usage_rows,
    )

    device_rows = []
    for device, stat in sorted(device_stats.items()):
        comment = comment_for_device(stat["device_type"], stat["number"], comments)
        roles = ",".join(f"{role}:{count}" for role, count in stat["roles"].most_common())
        device_rows.append(
            (
                device,
                stat["device_type"],
                stat["number"],
                comment,
                stat["occurrences"],
                len(stat["driver_rows"]),
                stat["condition_uses"],
                roles,
                stat["first_lddb"],
                stat["first_pos"],
                stat["first_title"],
            )
        )
    con.executemany(
        """
        insert into devices(
            device, device_type, number, comment, occurrences, driver_rows, condition_uses,
            roles, first_lddb, first_pos, first_title
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        device_rows,
    )

    external_rows = []
    for rec in external_sources:
        external_rows.append(
            (
                rec.get("device", ""),
                rec.get("device_type", ""),
                int(rec.get("number", 0) or 0),
                rec.get("comment", ""),
                int(rec.get("occurrences", 0) or 0),
                int(rec.get("required_on_count", 0) or 0),
                int(rec.get("required_off_count", 0) or 0),
                rec.get("source_kind", ""),
                rec.get("semantic_group", ""),
                rec.get("source_detail", ""),
                rec.get("stop_reason", ""),
                rec.get("refresh_area", ""),
                rec.get("refresh_network_label", ""),
                rec.get("refresh_unit_name", ""),
                rec.get("refresh_slot_number", ""),
                rec.get("refresh_device_range", ""),
                rec.get("source_unit_kind", ""),
                rec.get("source_unit_name", ""),
                rec.get("source_unit_connection", ""),
                rec.get("source_unit_slot_number", ""),
                rec.get("source_unit_area", ""),
                rec.get("first_lddb", ""),
                int(rec.get("first_pos", 0) or 0),
                rec.get("first_title", ""),
            )
        )
    con.executemany(
        """
        insert into external_sources(
            device, device_type, number, comment, occurrences, required_on_count, required_off_count,
            source_kind, semantic_group, source_detail, stop_reason, refresh_area, refresh_network_label,
            refresh_unit_name, refresh_slot_number, refresh_device_range, source_unit_kind, source_unit_name,
            source_unit_connection, source_unit_slot_number, source_unit_area, first_lddb, first_pos, first_title
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        external_rows,
    )

    con.commit()
    con.close()
    print(f"index written: {out}")
    print(f"ladder_rows={len(rows)} devices={len(device_rows)} usages={len(usage_rows)} external_sources={len(external_rows)}")
    return 0


# Bumped whenever the spelling of a stored device changes. An index written
# before X/Y/B/W were stored in hex holds "X520" where this build looks for
# "X208", and a silent miss reads as "device not used" -- the worst possible
# way to be wrong about a PLC project.
DEVICE_NAMING = "hex-1-ranges"


def open_existing(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise SystemExit(f"index db not found: {path}")
    con = connect(path)
    row = con.execute("select value from meta where key='device_naming'").fetchone()
    if row is None or row["value"] != DEVICE_NAMING:
        raise SystemExit(
            f"index db was built by an older version and spells devices differently: {path}\n"
            "X, Y, B and W devices are now numbered in hexadecimal, matching GX Works3.\n"
            "Rebuild it: gx3-cli index-lite build --root <project>"
        )
    return con


def print_rows(rows: list[sqlite3.Row], columns: list[str]) -> None:
    if not rows:
        print("no rows")
        return
    widths = {col: max(len(col), *(len(clean_cell(row[col])) for row in rows)) for col in columns}
    print("  ".join(col.ljust(widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        print("  ".join(clean_cell(row[col]).ljust(widths[col]) for col in columns))


def row_dict(row: sqlite3.Row | dict[str, object]) -> dict[str, object]:
    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def print_json(command: str, root: str | None, results: list[sqlite3.Row | dict[str, object]]) -> None:
    payload = {
        "command": command,
        "root": root or "",
        "results": [row_dict(row) for row in results],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def expanded_terms(text: str) -> list[str]:
    terms = {text}
    folded = text.casefold()
    for group in SYNONYM_GROUPS:
        if any(term.casefold() in folded or folded in term.casefold() for term in group):
            terms.update(group)
    return sorted(terms)


def query_device(args: argparse.Namespace) -> int:
    device = normalize_device(args.device)
    con = open_existing(Path(args.db or default_db_path()))
    rec = con.execute("select * from devices where device=?", (device,)).fetchone()
    if not rec:
        if args.json:
            print_json("query-device", args.root, [])
        else:
            print(f"device not found: {device}")
        con.close()
        return 1
    ext = con.execute("select * from external_sources where device=?", (device,)).fetchone()
    rows = con.execute(
        """
        select role, lddb, pos, title, row_conditions
        from device_usages
        where device=? and is_driver=1
        order by lddb, pos
        limit ?
        """,
        (device, args.limit),
    ).fetchall()
    if args.json:
        print_json(
            "query-device",
            args.root,
            [
                {
                    "device": rec["device"],
                    "comment": rec["comment"] or "",
                    "occurrences": rec["occurrences"],
                    "driver_rows": rec["driver_rows"],
                    "condition_uses": rec["condition_uses"],
                    "roles": rec["roles"],
                    "external": row_dict(ext) if ext else None,
                    "drivers": [row_dict(row) for row in rows],
                    "conditions": [
                        row_dict(row)
                        for row in con.execute(
                            """
                            select role, lddb, pos, title
                            from device_usages
                            where device=? and is_condition=1
                            order by lddb, pos
                            limit ?
                            """,
                            (device, args.limit),
                        ).fetchall()
                    ],
                }
            ],
        )
        con.close()
        return 0
    print(f"{rec['device']} {rec['comment'] or ''}".rstrip())
    print(f"occurrences={rec['occurrences']} driver_rows={rec['driver_rows']} condition_uses={rec['condition_uses']} roles={rec['roles']}")
    if ext:
        print(
            "external: "
            f"{ext['source_kind']} / {ext['semantic_group']} / {ext['source_detail']} "
            f"{ext['refresh_device_range'] or ext['source_unit_area']}"
        )
    print("")
    print("Driver rows:")
    print_rows(rows, ["role", "lddb", "pos", "title", "row_conditions"])
    print("")
    print("Condition uses:")
    rows = con.execute(
        """
        select role, lddb, pos, title
        from device_usages
        where device=? and is_condition=1
        order by lddb, pos
        limit ?
        """,
        (device, args.limit),
    ).fetchall()
    print_rows(rows, ["role", "lddb", "pos", "title"])
    con.close()
    return 0


def query_comment(args: argparse.Namespace) -> int:
    con = open_existing(Path(args.db or default_db_path()))
    terms = expanded_terms(args.text) if args.expand_synonyms else [args.text]
    clauses = " or ".join("comment like ?" for _ in terms)
    rows = con.execute(
        f"""
        select device, comment, occurrences, driver_rows, condition_uses
        from devices
        where {clauses}
        order by occurrences desc
        limit ?
        """,
        (*[f"%{term}%" for term in terms], args.limit),
    ).fetchall()
    if args.json:
        print_json("query-comment", args.root, rows)
    else:
        print_rows(rows, ["device", "comment", "occurrences", "driver_rows", "condition_uses"])
    con.close()
    return 0


def query_external(args: argparse.Namespace) -> int:
    con = open_existing(Path(args.db or default_db_path()))
    if args.device:
        device = normalize_device(args.device)
        rows = con.execute("select * from external_sources where device=?", (device,)).fetchall()
    else:
        rows = con.execute(
            """
            select * from external_sources
            where source_kind like ? or semantic_group like ? or comment like ?
            order by occurrences desc
            limit ?
            """,
            (f"%{args.text}%", f"%{args.text}%", f"%{args.text}%", args.limit),
        ).fetchall()
    print_rows(
        rows,
        [
            "device",
            "comment",
            "occurrences",
            "source_kind",
            "semantic_group",
            "refresh_device_range",
            "source_unit_area",
            "first_title",
        ],
    )
    con.close()
    return 0


def classify_cycle_comment(comment: str) -> str:
    text = comment or ""
    lower = text.lower()
    if "execution conditions" in lower or "startup conditions" in lower or "実行条件" in text or "起動条件" in text or "開始条件" in text:
        return "condition"
    if "完了" in text or "終了" in text or "completed" in lower or "complete" in lower:
        return "complete"
    if "確認ok" in lower or "確認OK" in text or "confirmation ok" in lower or "check ok" in lower:
        return "confirm"
    if "指令" in text or "命令" in text or "要求" in text or "command" in lower or "order" in lower:
        return "command"
    if "動作中" in text or "動作" in text or "運転" in text or "operation" in lower:
        return "operation"
    if "記憶" in text or "starting" in lower or "memory" in lower:
        return "state"
    return "other"


def query_cycle(args: argparse.Namespace) -> int:
    con = open_existing(Path(args.db or default_db_path()))
    dev_type = args.device_type.upper()
    start = int(args.start)
    end = int(args.end)
    if start > end:
        start, end = end, start
    like = f"%{args.text}%"
    rows = con.execute(
        """
        select device, comment, occurrences, driver_rows, condition_uses, first_pos, first_title
        from devices
        where device_type=?
          and number between ? and ?
          and driver_rows > 0
          and (? = '' or comment like ?)
        order by number
        limit ?
        """,
        (dev_type, start, end, args.text, like, args.limit),
    ).fetchall()
    out_rows = []
    for row in rows:
        out_rows.append(
            {
                "device": row["device"],
                "kind": classify_cycle_comment(row["comment"] or ""),
                "comment": row["comment"] or "",
                "first_pos": row["first_pos"],
                "first_title": row["first_title"],
            }
        )
    if args.json:
        print_json("query-cycle", args.root, out_rows)
    else:
        print_rows(out_rows, ["device", "kind", "comment", "first_pos", "first_title"])
    con.close()
    return 0


def device_map(args: argparse.Namespace) -> int:
    con = open_existing(Path(args.db or default_db_path()))
    min_free = int(args.min_free)
    types_filter = {t.strip().upper() for t in args.types.split(",")} if args.types else None
    rows = con.execute("select device_type, number from devices order by device_type, number").fetchall()
    con.close()
    by_type: dict[str, list[int]] = {}
    for row in rows:
        by_type.setdefault(str(row["device_type"]), []).append(int(row["number"]))

    free_col = f"free_ranges(>= {min_free})"
    out_rows: list[dict[str, object]] = []
    for dev_type in sorted(by_type):
        if types_filter and dev_type not in types_filter:
            continue
        nums = sorted(set(by_type[dev_type]))
        if not nums:
            continue
        used = len(nums)
        lo, hi = nums[0], nums[-1]
        span = hi - lo + 1
        gaps: list[tuple[int, int, int]] = []
        prev = None
        for num in nums:
            if prev is not None and num - prev - 1 >= min_free:
                gaps.append((prev + 1, num - 1, num - prev - 1))
            prev = num
        gaps.sort(key=lambda item: item[2], reverse=True)
        free_txt = "; ".join(f"{dev_type}{a}-{dev_type}{b}({c})" for a, b, c in gaps[: args.max_gaps])
        out_rows.append(
            {
                "device_type": dev_type,
                "used": used,
                "range": f"{dev_type}{lo}-{dev_type}{hi}",
                "span": span,
                "density_pct": round(100.0 * used / span, 1) if span else 0.0,
                free_col: free_txt or "(none)",
            }
        )
    print_rows(out_rows, ["device_type", "used", "range", "span", "density_pct", free_col])
    return 0


def stats(args: argparse.Namespace) -> int:
    con = open_existing(Path(args.db or default_db_path()))
    for table in ["devices", "device_usages", "ladder_rows", "comments", "external_sources"]:
        count = con.execute(f"select count(*) from {table}").fetchone()[0]
        print(f"{table}: {count}")
    root = con.execute("select value from meta where key='root'").fetchone()
    if root:
        print(f"root: {root['value']}")
    con.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build and query a lightweight GX3 SQLite index.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="build SQLite index")
    p.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    p.add_argument("--out", default=None, help="output SQLite path; default is .gx3_index/<project>.sqlite")
    p.add_argument("--comm-dir", default="outputs", help="directory containing communication CSV files")
    p.add_argument("--comm-prefix", default=default_comm_prefix(), help="communication CSV prefix")
    p.set_defaults(func=build_index)

    p = sub.add_parser("device", help="query one device")
    p.add_argument("device")
    p.add_argument("--db", default=None)
    p.add_argument("--root", default="")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--json", action="store_true", help="emit a common JSON envelope")
    add_format_alias(p)
    p.set_defaults(func=query_device)

    p = sub.add_parser("comment", help="search device comments")
    p.add_argument("text")
    p.add_argument("--db", default=None)
    p.add_argument("--root", default="")
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--json", action="store_true", help="emit a common JSON envelope")
    add_format_alias(p)
    p.add_argument("--expand-synonyms", action="store_true", help="expand common Japanese/English shop-floor synonyms")
    p.set_defaults(func=query_comment)

    p = sub.add_parser("external", help="query external/HMI/communication boundary devices")
    p.add_argument("text", nargs="?", default="")
    p.add_argument("--device")
    p.add_argument("--db", default=None)
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=query_external)

    p = sub.add_parser("cycle", help="list cycle/step coils from the SQLite index")
    p.add_argument("text", nargs="?", default="", help="comment keyword filter")
    p.add_argument("--db", default=None)
    p.add_argument("--device-type", default="M")
    p.add_argument("--start", default="0", help="start device number")
    p.add_argument("--end", default="999999", help="end device number")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--root", default="")
    p.add_argument("--json", action="store_true", help="emit a common JSON envelope")
    add_format_alias(p)
    p.set_defaults(func=query_cycle)

    p = sub.add_parser("device-map", help="device-type usage ranges, density, and free gaps")
    p.add_argument("--db", default=None)
    p.add_argument("--min-free", type=int, default=100, help="minimum contiguous free size to report")
    p.add_argument("--types", default=None, help="comma-separated device types to include, e.g. D,W,M")
    p.add_argument("--max-gaps", type=int, default=8, help="maximum free ranges to list per type")
    p.set_defaults(func=device_map)

    p = sub.add_parser("stats", help="show index counts")
    p.add_argument("--db", default=None)
    p.set_defaults(func=stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    fold_format_alias(args)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
