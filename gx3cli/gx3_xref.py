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
from gx3cli.gx3_reach import has_value_edges, successors
from gx3cli.gx3_input_identity import fingerprint, mismatch_message
from gx3cli.gx3_intermediate_tool import read_ladder_rows
from gx3cli.gx3_program_map import load_program_map
from gx3cli.gx3_version import package_version
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.review_gx3_project import extract_title, load_comments_for_root
from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.gx3_label_resolve import load_label_resolver
from gx3cli.gx3_output import add_format_alias, fold_format_alias


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
#   arg-decode-3: a block instruction's destination records how many devices
#   the run covers, so a search finds a device the run writes without naming.
XREF_DECODER = "arg-decode-3"


def stamp_decoder(con: sqlite3.Connection, root: Path | None = None) -> None:
    """Record which decoder wrote this database, and from which input.

    The path a database was built from is not an identity: the folder behind it
    can be rebuilt, edited or replaced, and every answer afterwards is about a
    file nobody opened. The fingerprint is of the ladder, comments, labels and
    parameters together, so "did the logic and the comments come from the same
    input" is a question this can answer.
    """
    con.execute("create table if not exists meta(key text primary key, value text not null)")
    con.execute(
        "insert or replace into meta(key, value) values ('decoder', ?)", (XREF_DECODER,)
    )
    if root is not None:
        con.execute(
            "insert or replace into meta(key, value) values ('input_sha256', ?)",
            (fingerprint(Path(root)),),
        )
        con.execute(
            "insert or replace into meta(key, value) values ('analyzer_version', ?)",
            (package_version(),),
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


def check_input(path: Path, con: sqlite3.Connection, root: Path | None) -> None:
    """Refuse a database built from a different project than the one asked for."""
    if root is None:
        return
    row = con.execute("select value from meta where key='input_sha256'").fetchone()
    stored = (row["value"] if isinstance(row, sqlite3.Row) else row[0]) if row else ""
    if not stored:
        # Built before inputs were stamped. The decoder check already refuses
        # those, so there is nothing to add here.
        return
    actual = fingerprint(Path(root))
    if not actual or actual == stored:
        return
    con.close()
    raise SystemExit(
        mismatch_message("xref db", path, stored, actual, rebuild_hint(path).replace("rebuild it: ", ""))
    )


def open_xref_db(
    path: Path, read_only: bool = False, root: Path | None = None
) -> sqlite3.Connection:
    """Open a cross-reference database, checked against the decoder and input."""
    uri = f"file:{path}?mode=ro" if read_only else str(path)
    con = sqlite3.connect(uri, uri=read_only)
    con.row_factory = sqlite3.Row
    check_decoder(path, con)
    check_input(path, con, root)
    return con


def flow_edge_rows(root: Path) -> list[tuple]:
    """The directed value-flow edges of a project, ready to store.

    Only the edges: an unresolved record says an operation could not be turned
    into one, and inventing an edge for it is the thing #36 asks not to happen.
    A caller that needs to know an operation went unread has the occurrence
    rows and parse-gaps for that.
    """
    from gx3cli.gx3_data_flow import build_report

    report = build_report(root)
    rows: list[tuple] = []
    for edge in report.get("edges", []) or []:
        rows.append(
            (
                edge.get("source_device", ""),
                edge.get("destination_device", ""),
                edge.get("opcode", ""),
                edge.get("source_arg_index"),
                edge.get("destination_arg_index"),
                int(edge.get("range_count") or 1),
                int(edge.get("source_word_width") or 1),
                int(edge.get("destination_word_width") or 1),
                1 if edge.get("read_modify_write") else 0,
                edge.get("confidence", "unknown"),
                edge.get("parse_status", "exact"),
                edge.get("lddb", ""),
                int(edge.get("pos") or 0),
                edge.get("pou", ""),
                edge.get("step"),
                edge.get("title", ""),
                edge.get("source_comment", ""),
                edge.get("destination_comment", ""),
            )
        )
    return rows


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
        drop table if exists data_flow;
        create table meta(key text primary key, value text not null);
        create table xref(
            id integer primary key autoincrement,
            device text not null,
            device_type text not null,
            number integer not null,
            -- How many devices this occurrence covers. A block instruction
            -- names only the first device of the run it writes, so a search
            -- for one in the middle has to match on the span, not the name.
            range_len integer not null default 1,
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
                            occ.device, occ.device_type, occ.number, occ.range_len, occ.access,
                            role, opcode, occ.arg_index, consts, occ.detail,
                            occ.access_basis,
                            lddb, pos, pou, step, current_title, comment, status,
                        )
                    )
    con.executemany(
        """
        insert into xref(
            device, device_type, number, range_len, access, role, opcode, arg_index,
            const_args, detail, access_basis, lddb, pos, pou, step, title, comment,
            parse_status
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    # The value-flow edges, stored beside the occurrences rather than derived
    # again by every caller. `downstream` joins reads and writes that happen on
    # the same rung, which cannot tell "D100 was moved into D200" from "D100
    # and D200 were mentioned together"; #36 asks for the difference, and for
    # graph, downstream and lint to be able to see it without each of them
    # re-reading every program.
    con.executescript(
        """
        create table data_flow(
            id integer primary key autoincrement,
            source_device text not null,
            destination_device text not null,
            opcode text not null,
            source_arg_index integer,
            destination_arg_index integer,
            range_count integer not null default 1,
            source_word_width integer not null default 1,
            destination_word_width integer not null default 1,
            read_modify_write integer not null default 0,
            confidence text not null default 'unknown',
            parse_status text not null default 'exact',
            lddb text not null,
            pos integer not null,
            pou text,
            step integer,
            title text,
            source_comment text,
            destination_comment text
        );
        """
    )
    con.executemany(
        """
        insert into data_flow(
            source_device, destination_device, opcode, source_arg_index,
            destination_arg_index, range_count, source_word_width,
            destination_word_width, read_modify_write, confidence, parse_status,
            lddb, pos, pou, step, title, source_comment, destination_comment
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        flow_edge_rows(root),
    )
    con.executescript(
        """
        create index idx_flow_source on data_flow(source_device);
        create index idx_flow_destination on data_flow(destination_device);
        create index idx_flow_row on data_flow(lddb, pos);
        create index idx_xref_device on xref(device);
        create index idx_xref_span on xref(device_type, number);
        create index idx_xref_row on xref(lddb, pos);
        create index idx_xref_access on xref(access);
        """
    )
    con.execute("insert into meta(key, value) values ('root', ?)", (str(root),))
    con.execute("insert into meta(key, value) values ('rows', ?)", (str(row_count),))
    con.execute("insert into meta(key, value) values ('records', ?)", (str(len(records)),))
    stamp_decoder(con, root)
    # Without statistics, SQLite picks an index by shape rather than by how
    # many rows it will actually touch. On a real project it chose the index on
    # `access` for "device=? and access=?" -- 53,000 rows for access='read',
    # scanned and sorted, where the index on `device` would have found three.
    # One query took 27ms instead of 0.1ms, and dead-logic runs one per device:
    # 6,665 of them, 72 of its 75 seconds. ANALYZE takes a tenth of a second
    # and is the difference.
    con.execute("analyze")
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
    return open_xref_db(path, root=root)


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


def device_filter(device: str) -> tuple[str, tuple[object, ...]]:
    """Use the same exact/range predicate for page rows and total counts."""
    parsed = _split_device(device)
    if parsed is None:
        return "device = ?", (device,)
    dev_type, number = parsed
    return (
        "device = ? or (device_type = ? and number <= ? "
        "and range_len > 1 and ? < number + range_len)",
        (device, dev_type, number, number),
    )


def rows_for_device(con: sqlite3.Connection, device: str, limit: int) -> list[sqlite3.Row]:
    """Occurrences of a device, including the runs that cover it unnamed.

    A block instruction names only the first device it writes, so "where is
    D64063 written" answered "no occurrences" while a BMOV four devices earlier
    was writing it.

    A run whose length is held in a device (range_len 0) is still found only
    under the device that starts it: how far it reaches is not knowable without
    running the program, and a guess here would put occurrences on devices the
    instruction may never touch.
    """
    predicate, params = device_filter(device)
    return con.execute(
        f"select * from xref where {predicate} order by pou, pos, id limit ?",
        (*params, limit),
    ).fetchall()


def device_counts(con: sqlite3.Connection, device: str) -> dict[str, int]:
    predicate, params = device_filter(device)
    counts = {"writers": 0, "readers": 0, "refs": 0}
    for row in con.execute(
        f"select access, count(*) as n from xref where {predicate} group by access", params
    ):
        group = "writers" if row["access"] in {"write", "both"} else (
            "readers" if row["access"] == "read" else "refs"
        )
        counts[group] += int(row["n"])
    return counts


def indexed_note(con: sqlite3.Connection, device: str) -> str:
    """Warn that index-modified access may reach this device unseen.

    D100Z2 names D100 and reaches whatever D100 plus Z2 is at the time. The
    occurrence is recorded under D100 because that is all the ladder says, so a
    device reached only through an index register appears in no search at all.
    Nothing static can resolve it; saying so is the difference between an
    incomplete answer and a wrong one.
    """
    parsed = _split_device(device)
    if parsed is None:
        return ""
    dev_type, _number = parsed
    row = con.execute(
        """
        select count(*) as n, sum(access in ('write', 'both')) as writes
        from xref
        where device_type = ? and detail like '%indexed%'
        """,
        (dev_type,),
    ).fetchone()
    total = int(row["n"] or 0)
    if not total:
        return ""
    writes = int(row["writes"] or 0)
    return (
        f"\nNote: {total} {dev_type} occurrences are index-modified"
        f" ({writes} of them writes). Which address those reach is only known"
        f" while the program runs, so this list can be incomplete."
    )


def span_note(row: sqlite3.Row, device: str) -> str:
    """Say so when a row was found by its run rather than by its name."""
    if "range_len" not in row.keys() or row["device"] == device:
        return ""
    length = row["range_len"] or 0
    if length > 1:
        last = _format_device(row["device_type"], row["number"] + length - 1)
        return f" [within {row['device']}..{last}]"
    return ""


def where_used(args: argparse.Namespace) -> int:
    device = normalize_device(args.device)
    con = open_db(args)
    try:
        rows = rows_for_device(con, device, args.limit)
        counts = device_counts(con, device)
        note = indexed_note(con, device).strip()
    finally:
        con.close()
    total = sum(counts.values())
    truncated = len(rows) < total
    warnings = [note] if note else []
    if truncated:
        warnings.insert(0, f"Note: showing {len(rows)} of {total} occurrences (--limit {args.limit}); "
                        "writers/readers may be omitted. Increase --limit or use --limit -1 for all occurrences.")
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
                            "total_counts": counts,
                            "total_count": total,
                            "returned_count": len(rows),
                            "limit": args.limit,
                            "truncated": truncated,
                            "warnings": warnings,
                        }
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0 if total else 1
    if not total:
        print(f"no occurrences: {device}")
        for warning in warnings:
            print(warning)
        return 1
    print(f"{device} {comment}".rstrip())
    for warning in warnings:
        print(warning)

    def heading(name: str, shown: int) -> str:
        count = counts[name.lower()]
        return f"\n{name} ({shown} shown / {count} total):" if truncated else f"\n{name} ({shown}):"

    print(heading("Writers", len(writers)))
    for r in writers:
        print(fmt_row(r) + span_note(r, device))
    print(heading("Readers", len(readers)))
    for r in readers:
        print(fmt_row(r) + span_note(r, device))
    if counts["refs"]:
        print(heading("Refs", len(refs)).replace("Refs", "Unclassified refs"))
        for r in refs:
            print(fmt_row(r) + span_note(r, device))
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
    # The walk itself lives in gx3_reach, so a correction to it -- block
    # instruction spans, value edges, exact limit reporting -- reaches this
    # command and change-impact at once instead of one of the two.
    has_flow = has_value_edges(con)

    start_comment = con.execute(
        "select comment from xref where device=? and comment<>'' limit 1", (device,)
    ).fetchone()
    print(f"downstream impact of {device} {start_comment[0] if start_comment else ''}".rstrip())
    print(f"(max-depth={args.max_depth}, strict-bit={args.strict_bit})")
    if has_flow:
        print(
            "basis: `via OPCODE` means the value goes there through that instruction; "
            "same-rung means only that both appear on one rung.\n"
        )
    else:
        print(
            "basis: same-rung only -- this cross-reference holds no value-flow "
            "edges. Rebuild it to tell a transfer from a co-occurrence.\n"
        )

    visited = {device}
    frontier = [(device, 0)]
    total = 0
    while frontier:
        dev, depth = frontier.pop(0)
        if depth >= args.max_depth:
            continue
        children = successors(con, dev, has_flow, args.strict_bit)
        shown = 0
        for ch, basis in children:
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
            print(
                f"{indent}{child:<14} {ch['role']:<8} {basis:<10} "
                f"{ch['pou']:<6}{step:<7} {comment}"
            )
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
    p.add_argument("--limit", type=int, default=200, help="maximum occurrences shown; -1 shows all (totals are always reported)")
    p.add_argument("--cross", action="store_true", help="also show linked devices in other project xref DBs")
    p.add_argument("--project", default=None, help="current project label for --cross; defaults from --root")
    p.add_argument("--link-db", default=".gx3_index/link_map.sqlite", help="link-map sqlite path for --cross")
    p.add_argument("--cross-limit", type=int, default=20, help="maximum linked devices to show")
    p.add_argument("--cross-xref-limit", type=int, default=80, help="maximum xref rows per linked device")
    p.add_argument("--json", action="store_true", help="emit a common JSON envelope")
    add_format_alias(p)
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
    fold_format_alias(args)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
