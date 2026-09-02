from __future__ import annotations

"""Full cross-reference with read/write classification and downstream trace.

Unlike the lite index (which keeps only the first device of each instruction),
this tool decodes every argument of every operation, including:
- plain devices              d{a=N}            e.g. MOV source and destination
- buffer memory access       B{b=..:e=..} + header ``Us:G``  ->  U70\\G123
- digit-specified bits       M{b=..:m=c{v=k}} + header ``M:Ks`` -> K2M35001
- bit-of-word / indexed      M{..} + header ``Dots``/``Z``

Each occurrence is classified as read / write / both / ref (unknown opcode).

Subcommands:
  build       parse all LDDBs and write .gx3_index/<label>_xref.sqlite
  where-used  list writers and readers of one device (with POU name and step)
  downstream  BFS impact trace: devices written by rows that read the target
  export      dump the xref table (optionally one device) to CSV
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

from gx3cli.gx3_device_name import format_device as _format_device, split_device as _split_device
from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_intermediate_tool import read_ladder_rows
from gx3cli.gx3_program_map import load_program_map
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.review_gx3_project import extract_title, load_comments_for_root
from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.gx3_label_resolve import load_label_resolver


DEVICE_NAME_RE = re.compile(r"^([A-Z]+)(\d+)$", re.IGNORECASE)


# The decoding contract this database was written under. Every consumer reads
# the stored occurrences as-is, so a database built before a decoder change
# keeps answering with the old reading -- and lint, trace-device, dead-logic
# and timing-chart have no way to tell. Bump this whenever the decoder changes
# which devices a rung yields, how they are spelled, or how access is
# classified; index-lite guards its own spelling change the same way.
#
#   arg-decode-2: buffer memory keeps its index register (U0\G10Z0) instead of
#   handing the header token to the next operand, and an index register is
#   recorded as read rather than inheriting the operand's access.
XREF_DECODER = "arg-decode-2"


def stamp_decoder(con: sqlite3.Connection) -> None:
    """Record which decoder wrote this database's occurrences."""
    con.execute("create table if not exists meta(key text primary key, value text not null)")
    con.execute(
        "insert or replace into meta(key, value) values ('decoder', ?)", (XREF_DECODER,)
    )


def rebuild_hint(path: Path) -> str:
    return f"rebuild it: gx3-cli xref build --root <project> --db {path}"


def check_decoder(path: Path, con: sqlite3.Connection) -> None:
    """Refuse a database whose occurrences were decoded by another version."""
    row = con.execute("select value from meta where key='decoder'").fetchone()
    stored = (row[0] if row else "") if not isinstance(row, sqlite3.Row) else row["value"]
    if stored == XREF_DECODER:
        return
    con.close()
    raise SystemExit(
        f"xref db was built by a different decoder version: {path}\n"
        f"  stored: {stored or '(none)'}   expected: {XREF_DECODER}\n"
        "Its occurrences are the old reading of the ladder, so every answer\n"
        f"taken from it would be stale. {rebuild_hint(path)}"
    )


def open_xref_db(path: Path, read_only: bool = False) -> sqlite3.Connection:
    """Open a cross-reference database, checked against the decoder version."""
    uri = f"file:{path}?mode=ro" if read_only else str(path)
    con = sqlite3.connect(uri, uri=read_only)
    con.row_factory = sqlite3.Row
    check_decoder(path, con)
    return con


def default_db_path(root: Path) -> Path:
    name = root.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"
    return Path(".gx3_index") / f"{label}_xref.sqlite"


def project_label_from_root(root: Path) -> str:
    name = root.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"


def build(args: argparse.Namespace) -> int:
    root = Path(args.root)
    out = Path(args.db or default_db_path(root))
    out.parent.mkdir(parents=True, exist_ok=True)

    print(f"loading program map from {root} ...")
    pm = load_program_map(root)
    labels = load_label_resolver(root)
    if labels:
        print(f"resolved {len(labels)} label references from LabelData.db")
    comments = load_comments_for_root(root)
    print("parsing ladder rows ...")
    rows_by_db = read_ladder_rows(root)

    con = sqlite3.connect(out)
    con.executescript(
        """
        drop table if exists xref;
        drop table if exists meta;
        create table meta(key text primary key, value text not null);
        create table xref(
            id integer primary key autoincrement,
            device text not null,
            device_type text not null,
            number integer not null,
            access text not null,
            role text not null,
            opcode text,
            arg_index integer,
            const_args text,
            detail text,
            access_basis text,
            lddb text not null,
            pos integer not null,
            pou text,
            step integer,
            title text,
            comment text,
            parse_status text
        );
        """
    )

    records = []
    row_count = 0
    for lddb, rows in rows_by_db.items():
        pou = pm.label(lddb)
        current_title = ""
        for raw in rows:
            data = str(raw["data"])
            blocktype = int(raw["blocktype"])
            if blocktype in {1, 2}:
                t = extract_title(data)
                if t:
                    current_title = t
            if blocktype != 0:
                continue
            row_count += 1
            pos = int(float(raw["pos"]))
            step = pm.step_of(lddb, pos)
            ops, status = parse_row_occurrences(data, labels)
            for role, opcode, occs, consts in ops:
                for occ in occs:
                    info = comments.get((occ.device_type, occ.number), CommentInfo())
                    comment = info.japanese or info.english or info.all_text or ""
                    records.append(
                        (
                            occ.device, occ.device_type, occ.number, occ.access,
                            role, opcode, occ.arg_index, consts, occ.detail,
                            occ.access_basis,
                            lddb, pos, pou, step, current_title, comment, status,
                        )
                    )
    con.executemany(
        """
        insert into xref(
            device, device_type, number, access, role, opcode, arg_index, const_args,
            detail, access_basis, lddb, pos, pou, step, title, comment, parse_status
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    con.executescript(
        """
        create index idx_xref_device on xref(device);
        create index idx_xref_row on xref(lddb, pos);
        create index idx_xref_access on xref(access);
        """
    )
    con.execute("insert into meta(key, value) values ('root', ?)", (str(root),))
    con.execute("insert into meta(key, value) values ('rows', ?)", (str(row_count),))
    con.execute("insert into meta(key, value) values ('records', ?)", (str(len(records)),))
    stamp_decoder(con)
    con.commit()
    con.close()
    print(f"xref written: {out}")
    print(f"rows={row_count} occurrences={len(records)}")
    for w in pm.warnings:
        print(f"warning: {w}")
    return 0


def open_db(args: argparse.Namespace) -> sqlite3.Connection:
    root = Path(args.root)
    path = Path(args.db or default_db_path(root))
    if not path.exists():
        raise SystemExit(
            f"xref db not found: {path} "
            f"(run: python -m gx3cli.gx3_cli xref build --root {root})"
        )
    return open_xref_db(path)


def normalize_device(text: str) -> str:
    parsed = _split_device(text)
    if parsed is not None:
        return _format_device(*parsed)
    return text.strip()


def fmt_row(r: sqlite3.Row) -> str:
    step = f"st{r['step']}" if r["step"] is not None else "st?"
    opcode = r["opcode"] or r["role"]
    detail = f" [{r['detail']}]" if r["detail"] else ""
    consts = f" k={r['const_args']}" if r["const_args"] else ""
    basis = f" basis={r['access_basis']}" if "access_basis" in r.keys() and r["access_basis"] else ""
    title = f" | {r['title']}" if r["title"] else ""
    return f"  {r['pou']:<6} {step:<7} {opcode:<9} {r['access']:<5}{detail}{consts}{basis}{title}"


def row_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def where_used(args: argparse.Namespace) -> int:
    device = normalize_device(args.device)
    con = open_db(args)
    rows = con.execute(
        "select * from xref where device=? order by pou, pos limit ?", (device, args.limit)
    ).fetchall()
    if not rows:
        print(f"no occurrences: {device}")
        return 1
    comment = next((r["comment"] for r in rows if r["comment"]), "")
    writers = [r for r in rows if r["access"] in {"write", "both"}]
    readers = [r for r in rows if r["access"] == "read"]
    refs = [r for r in rows if r["access"] == "ref"]
    if args.json:
        print(
            json.dumps(
                {
                    "command": "xref where-used",
                    "root": str(args.root),
                    "results": [
                        {
                            "device": device,
                            "comment": comment,
                            "writers": [row_dict(r) for r in writers],
                            "readers": [row_dict(r) for r in readers],
                            "refs": [row_dict(r) for r in refs],
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        con.close()
        return 0
    print(f"{device} {comment}".rstrip())
    print(f"\nWriters ({len(writers)}):")
    for r in writers:
        print(fmt_row(r))
    print(f"\nReaders ({len(readers)}):")
    for r in readers:
        print(fmt_row(r))
    if refs:
        print(f"\nUnclassified refs ({len(refs)}):")
        for r in refs:
            print(fmt_row(r))
    con.close()
    if args.cross:
        print_cross_where_used(args, device)
    return 0


def print_cross_where_used(args: argparse.Namespace, device: str) -> None:
    link_db = Path(args.link_db)
    if not link_db.exists():
        print(f"\nCross-link targets: link-map db not found: {link_db}")
        return
    project = args.project or project_label_from_root(Path(args.root))
    link_con = sqlite3.connect(link_db)
    link_con.row_factory = sqlite3.Row
    rows = link_con.execute(
        """
        select *, project_b as other_project, device_b as other_device
        from link_map
        where project_a=? and device_a=?
        union all
        select *, project_a as other_project, device_a as other_device
        from link_map
        where project_b=? and device_b=?
        order by confidence desc, other_project, other_device
        """,
        (project, device, project, device),
    ).fetchall()
    print(f"\nCross-link targets via {link_db} ({project}:{device}):")
    if not rows:
        print("  (none)")
        link_con.close()
        return
    for link in rows[: args.cross_limit]:
        other_project = str(link["other_project"])
        other_device = str(link["other_device"])
        print(
            f"  -> {other_project}:{other_device} "
            f"type={link['link_type']} dir={link['direction']} confidence={link['confidence']} role={link['role']}"
        )
        db_row = link_con.execute("select xref_db from project where label=?", (other_project,)).fetchone()
        if not db_row:
            print("     xref db: unknown project in link-map")
            continue
        xref_path = Path(str(db_row["xref_db"]))
        if not xref_path.exists():
            print(f"     xref db missing: {xref_path}")
            continue
        other_con = sqlite3.connect(xref_path)
        other_con.row_factory = sqlite3.Row
        other_rows = other_con.execute(
            "select * from xref where device=? order by pou, pos limit ?",
            (other_device, args.cross_xref_limit),
        ).fetchall()
        if not other_rows:
            print("     no xref rows")
            other_con.close()
            continue
        writers = [r for r in other_rows if r["access"] in {"write", "both"}]
        readers = [r for r in other_rows if r["access"] == "read"]
        if writers:
            print(f"     Writers ({len(writers)} shown):")
            for r in writers:
                print("   " + fmt_row(r))
        if readers:
            print(f"     Readers ({len(readers)} shown):")
            for r in readers:
                print("   " + fmt_row(r))
        other_con.close()
    if len(rows) > args.cross_limit:
        print(f"  ... {len(rows) - args.cross_limit} more cross-link targets suppressed")
    link_con.close()


def downstream(args: argparse.Namespace) -> int:
    device = normalize_device(args.device)
    con = open_db(args)
    read_access = ("read",) if args.strict_bit else ("read", "ref", "both")
    write_access = ("write", "both")

    def written_with(dev: str) -> list[sqlite3.Row]:
        roles = ""
        if args.strict_bit:
            roles = " and r1.role in ('a','b') and r2.role in ('c','SET','RST','PLS','PLF','OUT__16','OUTH__16')"
        q = f"""
            select distinct r2.device as device, r2.comment as comment, r2.pou as pou,
                   r2.step as step, r2.role as role, r2.access as access
            from xref r1 join xref r2 on r1.lddb = r2.lddb and r1.pos = r2.pos
            where r1.device = ?
              and r1.access in ({','.join('?' * len(read_access))})
              and r2.access in ({','.join('?' * len(write_access))})
              and r2.device <> r1.device
              {roles}
        """
        return con.execute(q, (dev, *read_access, *write_access)).fetchall()

    start_comment = con.execute(
        "select comment from xref where device=? and comment<>'' limit 1", (device,)
    ).fetchone()
    print(f"downstream impact of {device} {start_comment[0] if start_comment else ''}".rstrip())
    print(f"(max-depth={args.max_depth}, strict-bit={args.strict_bit})\n")

    visited = {device}
    frontier = [(device, 0)]
    total = 0
    while frontier:
        dev, depth = frontier.pop(0)
        if depth >= args.max_depth:
            continue
        children = written_with(dev)
        shown = 0
        for ch in children:
            child = ch["device"]
            if child in visited:
                continue
            visited.add(child)
            total += 1
            shown += 1
            if total > args.max_nodes:
                print(f"... truncated at {args.max_nodes} devices")
                con.close()
                return 0
            indent = "  " * (depth + 1)
            step = f"st{ch['step']}" if ch["step"] is not None else ""
            comment = ch["comment"] or ""
            print(f"{indent}{child:<14} {ch['role']:<8} {ch['pou']:<6}{step:<7} {comment}")
            frontier.append((child, depth + 1))
            if shown >= args.max_children:
                remaining = len(children) - shown
                if remaining > 0:
                    print(f"{indent}... {remaining} more direct targets of {dev} suppressed")
                break
    print(f"\ntotal affected devices: {total} (visited within depth {args.max_depth})")
    con.close()
    return 0


def export(args: argparse.Namespace) -> int:
    con = open_db(args)
    if args.device:
        rows = con.execute(
            "select * from xref where device=? order by pou, pos", (normalize_device(args.device),)
        ).fetchall()
    else:
        rows = con.execute("select * from xref order by device_type, number, pou, pos").fetchall()
    out = Path(args.output)
    fields = [
        "device", "device_type", "number", "access", "role", "opcode", "arg_index",
        "const_args", "detail", "access_basis", "lddb", "pos", "pou", "step", "title", "comment", "parse_status",
    ]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(fields)
        for r in rows:
            w.writerow([r[k] for k in fields])
    print(f"exported {len(rows)} rows -> {out}")
    con.close()
    return 0


def stats(args: argparse.Namespace) -> int:
    con = open_db(args)
    for key, value in con.execute("select key, value from meta"):
        print(f"{key}: {value}")
    for access, count in con.execute("select access, count(*) from xref group by access order by 2 desc"):
        print(f"access {access}: {count}")
    for kind, count in con.execute(
        "select device_type, count(distinct device) from xref group by device_type order by 2 desc limit 15"
    ):
        print(f"type {kind}: {count} devices")
    con.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--db", default=None, help="xref sqlite path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("build", help="build the xref database")
    p.set_defaults(func=build)

    p = sub.add_parser("where-used", help="list writers/readers of one device")
    p.add_argument("device")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("--cross", action="store_true", help="also show linked devices in other project xref DBs")
    p.add_argument("--project", default=None, help="current project label for --cross; defaults from --root")
    p.add_argument("--link-db", default=".gx3_index/link_map.sqlite", help="link-map sqlite path for --cross")
    p.add_argument("--cross-limit", type=int, default=20, help="maximum linked devices to show")
    p.add_argument("--cross-xref-limit", type=int, default=80, help="maximum xref rows per linked device")
    p.add_argument("--json", action="store_true", help="emit a common JSON envelope")
    p.set_defaults(func=where_used)

    p = sub.add_parser("downstream", help="BFS impact trace from one device")
    p.add_argument("device")
    p.add_argument("--max-depth", type=int, default=3)
    p.add_argument("--max-nodes", type=int, default=150)
    p.add_argument("--max-children", type=int, default=20)
    p.add_argument("--strict-bit", action="store_true", help="contacts -> coils only")
    p.set_defaults(func=downstream)

    p = sub.add_parser("export", help="export xref to CSV")
    p.add_argument("--device", default=None)
    p.add_argument("-o", "--output", default="outputs/xref_export.csv")
    p.set_defaults(func=export)

    p = sub.add_parser("stats", help="show xref statistics")
    p.set_defaults(func=stats)
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
