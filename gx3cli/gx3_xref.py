from __future__ import annotations

"""Full cross-reference with read/write classification and downstream trace.

Unlike the lite index (which keeps only the first device of each instruction),
this tool decodes every argument of every operation, including:
- plain devices              d{a=N}            e.g. MOV source and destination
- buffer memory access       B{b=..:e=..} + header ``Us:G``  ->  U70\\G123
- digit-specified bits       M{b=..:m=c{v=k}} + header ``M:Ks`` -> K2M35001
- bit-of-word / indexed      M{..} + header ``Dots``/``Z``

Supported simple Structured Text assignments are also indexed as partial
cross-reference evidence. Unsupported ST syntax is never guessed and keeps the
coverage state partial.

Each occurrence is classified as read / write / both / ref (unknown opcode).

Subcommands:
  build       parse LDDBs plus supported ST references and write xref sqlite
  where-used  list writers and readers of one device/label
  downstream  BFS impact trace: devices written by rows that read the target
  export      dump the xref table (optionally one device) to CSV
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from gx3cli.gx3_device_name import format_device as _format_device, split_device as _split_device
from gx3cli.gx3_arg_decode import parse_row_operations, parse_row_occurrences  # legacy import facade
from gx3cli.gx3_data_flow import decoded_rows, operation_records
from gx3cli.gx3_reach import has_value_edges, reach
from gx3cli.gx3_input_identity import fingerprint, mismatch_message
from gx3cli.gx3_intermediate_tool import read_ladder_rows
from gx3cli.gx3_program_map import load_program_map
from gx3cli.gx3_version import package_version
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.review_gx3_project import extract_title, load_comments_for_root
from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.gx3_label_resolve import load_label_resolver
from gx3cli.gx3_format import enumerate_inline_st_sources, enumerate_st_sources
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
#
#   arg-decode-4: an operand records the devices its own type occupies, so
#   DMOV D100 D200 covers D100..D101 and D200..D201 rather than one device
#   each. Coverage changed, so a database built by the previous decoder holds
#   fewer members than this build would find and must not be reused: the
#   fingerprint is the same input, and the answer would still be narrower.
#
#   arg-decode-strefs-5: supported simple ST/inline-ST assignments contribute
#   read/write evidence. The database also records unresolved ST sources and
#   label-only references so absence can be reported as partial, not complete.
#
#   arg-decode-strefs-countspans-6: counted operands use documented physical
#   device spans instead of assuming every (n) is already a device count.
#   DFMOV, WTOB, BTOW and BK+ therefore change persisted coverage; indexed
#   counted bases remain statically unexpanded.
# v7 preserves independent physical spans in data_flow; count is not extent.
# v8 preserves LD block/operation/element locations instead of dropping them.
XREF_DECODER = "arg-decode-label-scopes-10"


def stamp_decoder(con: sqlite3.Connection, root: Path | None = None) -> None:
    """Record which decoder wrote this database, and from which input."""
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
        "Its occurrences are the old reading of the ladder/ST inputs, so every answer\n"
        f"taken from it would be stale. {rebuild_hint(path)}"
    )


def check_input(path: Path, con: sqlite3.Connection, root: Path | None) -> None:
    """Refuse a database built from a different project than the one asked for."""
    if root is None:
        return
    row = con.execute("select value from meta where key='input_sha256'").fetchone()
    stored = (row["value"] if isinstance(row, sqlite3.Row) else row[0]) if row else ""
    actual = fingerprint(Path(root))
    if stored and actual and actual == stored:
        return
    con.close()
    raise SystemExit(
        mismatch_message("xref db", path, stored, actual, rebuild_hint(path).replace("rebuild it: ", ""))
    )


def open_xref_db(
    path: Path, read_only: bool = True, root: Path | None = None, *, snapshot: bool | None = None
) -> sqlite3.Connection:
    """Open a database checked against the decoder and input.

    Read-only snapshots are the default. Explicit writable maintenance callers
    keep their existing transaction policy unless snapshot=True is supplied.
    The caller must close the returned handle; source files are not pinned.
    """
    uri = f"file:{quote(path.absolute().as_posix(), safe='/:')}?mode=ro" if read_only else str(path)
    con = sqlite3.connect(uri, uri=read_only)
    con.row_factory = sqlite3.Row
    pin_snapshot = read_only if snapshot is None else snapshot
    try:
        if pin_snapshot:
            con.execute("begin")
        check_decoder(path, con)
        check_input(path, con, root)
        if root is not None:
            from gx3cli.gx3_index_contract import check_schema

            # Value flow is an optional capability; its consumers check it
            # separately. Where-used still works without a data_flow table.
            check_schema(con, "xref", path, ("xref", "xref_members", "st_sources", "st_refs"))
        from gx3cli.gx3_index_build import require_build_contract

        require_build_contract(con, path)
    except BaseException:
        con.close()
        raise
    return con


def member_rows(con: sqlite3.Connection) -> list[tuple]:
    """One line per device an occurrence covers, including the one it names."""
    rows = con.execute(
        "select id, device, device_type, number, range_len from xref"
    ).fetchall()
    out: list[tuple] = []
    for row in rows:
        src_id, device, dev_type, number, length = row
        out.append((src_id, device, dev_type, number, 0))
        for offset in range(1, max(1, int(length or 1))):
            out.append(
                (
                    src_id,
                    _format_device(dev_type, number + offset),
                    dev_type,
                    number + offset,
                    offset,
                )
            )
    return out


def flow_edge_rows(root: Path, *, edges=None) -> list[tuple]:
    """The directed ladder value-flow edges of a project, ready to store.

    ST references intentionally do not invent value-flow edges here. A simple
    assignment proves reads/writes for xref, but the first issue scope does not
    claim a complete ST evaluator or interprocedural flow model.
    """
    from gx3cli.gx3_data_flow import build_report

    if edges is None:
        edges = build_report(root).get("edges", []) or []
    rows: list[tuple] = []
    for edge in edges:
        rows.append(
            (
                edge.get("source_device", ""),
                edge.get("destination_device", ""),
                edge.get("opcode", ""),
                edge.get("source_arg_index"),
                edge.get("destination_arg_index"),
                int(edge.get("range_count", 1)),
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
                int(edge["source_range_len"]),
                int(edge["destination_range_len"]),
                (_split_device(edge.get("source_device", "")) or ("", 0))[0],
                (_split_device(edge.get("source_device", "")) or ("", 0))[1],
                edge.get("source_detail", ""),
                edge.get("destination_detail", ""),
                edge.get("block_id"),
                edge.get("operation_index"),
                edge.get("element_position"),
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


def _label_device_for_symbol(labels: object, symbol: str) -> tuple[str, str]:
    """Resolve one ST label to one physical device only when unambiguous.

    LabelResolver intentionally exposes token lookup because ladder operands
    carry label IDs, not names. ST already contains names, so this bridge scans
    the resolver's decoded entries and refuses ambiguous/local-label mappings.
    The label still remains in st_refs even when no physical address is safe to
    materialize into the device xref.
    """
    entries = getattr(labels, "_entries", {})
    unique: dict[tuple[object, ...], object] = {}
    for ref in entries.values():
        if str(getattr(ref, "name", "")).casefold() != symbol.casefold():
            continue
        key = (
            getattr(ref, "name", ""),
            getattr(ref, "label_class", ""),
            getattr(ref, "data_type", ""),
            getattr(ref, "comment", ""),
            tuple(getattr(ref, "devices", ())),
        )
        unique[key] = ref
    matches = list(unique.values())
    if not matches:
        return "", "label has no decoded LabelData.db match"
    if len(matches) != 1:
        return "", "label name is ambiguous across decoded label tables"
    physical: list[str] = []
    for raw in getattr(matches[0], "devices", ()):
        parsed = _split_device(str(raw))
        if parsed is not None:
            physical.append(_format_device(*parsed))
    physical = list(dict.fromkeys(physical))
    if len(physical) == 1:
        return physical[0], f"label {symbol} assigned to {physical[0]}"
    if not physical:
        return "", "local/unassigned label has no single physical device"
    return "", "label maps to multiple physical devices; not materialized"


def collect_st_evidence(
    root: Path,
    rows_by_db: dict[str, list[object]],
    pm: object,
    labels: object,
    comments: dict[tuple[str, int], CommentInfo],
) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Return st_sources rows, st_refs rows, and physical xref rows."""
    pou_by_file: dict[str, str] = {}
    for lddb in rows_by_db:
        pou_by_file[lddb] = pm.label(lddb)
    for path in sorted(root.glob("*_STDB.db")):
        pou_by_file[path.name] = pm.label(path.name)

    sources = [
        *enumerate_st_sources(root, pou_by_file),
        *enumerate_inline_st_sources(rows_by_db, pou_by_file),
    ]
    source_rows: list[tuple] = []
    ref_rows: list[tuple] = []
    xref_rows: list[tuple] = []
    for source_id, source in enumerate(sources, 1):
        reason = "; ".join(source.reasons)
        source_rows.append(
            (
                source_id,
                source.source_kind,
                source.source_file,
                source.source_location,
                source.pou,
                source.coverage,
                reason,
            )
        )
        for ref in source.references:
            resolved = ""
            resolution_reason = ""
            parsed = _split_device(ref.symbol)
            if parsed is not None:
                resolved = _format_device(*parsed)
            else:
                resolved, resolution_reason = _label_device_for_symbol(labels, ref.symbol)
            combined_reason = "; ".join(
                part for part in (ref.reason, resolution_reason) if part
            )
            ref_rows.append(
                (
                    source_id,
                    ref.symbol,
                    resolved,
                    ref.access,
                    ref.source_kind,
                    ref.source_file,
                    ref.source_location,
                    ref.pou,
                    ref.statement_index,
                    ref.coverage,
                    combined_reason,
                )
            )
            if not resolved:
                continue
            resolved_parsed = _split_device(resolved)
            if resolved_parsed is None:
                continue
            dev_type, number = resolved_parsed
            info = comments.get((dev_type, number), CommentInfo())
            comment = info.japanese or info.english or info.all_text or ""
            detail_parts = [ref.source_kind, ref.source_location]
            if ref.symbol != resolved:
                detail_parts.append(f"symbol={ref.symbol}")
            xref_rows.append(
                (
                    resolved,
                    dev_type,
                    number,
                    1,
                    ref.access,
                    "ST",
                    ":=",
                    0 if ref.access == "write" else 1,
                    "",
                    " ".join(detail_parts),
                    "structured-text-partial",
                    ref.source_file,
                    ref.statement_index,
                    ref.pou,
                    None,
                    "",
                    comment,
                    f"st-{ref.coverage}",
                )
            )
    return source_rows, ref_rows, xref_rows


def build(args: argparse.Namespace) -> int:
    from gx3cli.gx3_index_build import atomic_index_build

    root = Path(args.root)
    out = Path(args.db or default_db_path(root))
    with atomic_index_build(root, out) as con:
        result = _populate_xref(args, con)
    print(f"xref written: {out}")
    return result


def _populate_xref(args: argparse.Namespace, con: sqlite3.Connection) -> int:
    root = Path(args.root)

    print(f"loading program map from {root} ...")
    pm = load_program_map(root)
    labels = load_label_resolver(root)
    if labels:
        print(f"resolved {len(labels)} label references from LabelData.db")
    elif labels.fatal:
        raise SystemExit(
            f"LabelData.db is present and could not be read: {labels.reason}. "
            "Every label-named operand would be missing from this "
            "cross-reference, and nothing in it would say so. "
            "Fix or remove the file and build again."
        )
    elif not labels.usable:
        print(f"warning: label names are unavailable -- {labels.reason}")
    comments = load_comments_for_root(root)
    print("parsing ladder rows ...")
    rows_by_db = read_ladder_rows(root)

    con.executescript(
        """
        drop table if exists xref;
        drop table if exists meta;
        drop table if exists data_flow;
        drop table if exists xref_members;
        drop table if exists st_sources;
        drop table if exists st_refs;
        create table meta(key text primary key, value text not null);
        create table xref(
            id integer primary key autoincrement,
            device text not null,
            device_type text not null,
            number integer not null,
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
            parse_status text,
            block_id text,
            op_index integer,
            element_position text
        );
        create table st_sources(
            id integer primary key,
            source_kind text not null,
            source_file text not null,
            source_location text not null,
            pou text,
            coverage text not null,
            reason text
        );
        create table st_refs(
            id integer primary key autoincrement,
            source_id integer not null,
            symbol text not null,
            resolved_device text,
            access text not null,
            source_kind text not null,
            source_file text not null,
            source_location text not null,
            pou text,
            statement_index integer not null,
            coverage text not null,
            reason text
        );
        """
    )

    records: list[tuple] = []
    row_count = 0
    flow_edges = []
    for ops, status, location in decoded_rows(rows_by_db, pm, labels):
        row_count += 1
        for operation in ops:
            if operation.opcode:
                flow_edges.extend(asdict(r) for r in operation_records(operation, status, comments, location)
                                  if r.record_kind == "edge")
            for occ in operation.args:
                info = comments.get((occ.device_type, occ.number), CommentInfo())
                comment = info.japanese or info.english or info.all_text or ""
                records.append(
                    (
                        occ.device, occ.device_type, occ.number, occ.range_len, occ.access,
                        operation.role, operation.opcode, occ.arg_index, operation.const_summary, occ.detail,
                        occ.access_basis,
                        location["lddb"], location["pos"], location["pou"], location["step"],
                        location["title"], comment, status,
                        location["block_id"], operation.op_index, operation.element_position,
                    )
                )

    st_source_rows, st_ref_rows, st_xref_rows = collect_st_evidence(
        root, rows_by_db, pm, labels, comments
    )
    # ST has its own source/statement locations; never invent LD coordinates.
    records.extend((*record, None, None, None) for record in st_xref_rows)

    con.executemany(
        """
        insert into xref(
            device, device_type, number, range_len, access, role, opcode, arg_index,
            const_args, detail, access_basis, lddb, pos, pou, step, title, comment,
            parse_status, block_id, op_index, element_position
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        records,
    )
    con.executemany(
        """
        insert into st_sources(id, source_kind, source_file, source_location, pou, coverage, reason)
        values (?, ?, ?, ?, ?, ?, ?)
        """,
        st_source_rows,
    )
    con.executemany(
        """
        insert into st_refs(
            source_id, symbol, resolved_device, access, source_kind, source_file,
            source_location, pou, statement_index, coverage, reason
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        st_ref_rows,
    )

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
            destination_comment text,
            source_range_len integer not null,
            destination_range_len integer not null,
            source_device_type text not null,
            source_number integer not null,
            source_detail text,
            destination_detail text,
            block_id text,
            operation_index integer,
            element_position text
        );
        """
    )
    con.executemany(
        """
        insert into data_flow(
            source_device, destination_device, opcode, source_arg_index,
            destination_arg_index, range_count, source_word_width,
            destination_word_width, read_modify_write, confidence, parse_status,
            lddb, pos, pou, step, title, source_comment, destination_comment,
            source_range_len, destination_range_len, source_device_type,
            source_number, source_detail, destination_detail, block_id, operation_index, element_position
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        flow_edge_rows(root, edges=flow_edges),
    )
    con.executescript(
        """
        create table xref_members(
            src_id integer not null,
            member_device text not null,
            device_type text not null,
            number integer not null,
            run_offset integer not null
        );
        """
    )
    con.executemany(
        "insert into xref_members(src_id, member_device, device_type, number, run_offset)"
        " values (?, ?, ?, ?, ?)",
        member_rows(con),
    )
    con.executescript(
        """
        create index idx_members_device on xref_members(member_device);
        create index idx_members_span on xref_members(device_type, number);
        create index idx_members_src on xref_members(src_id);
        create index idx_flow_source on data_flow(source_device);
        create index idx_flow_source_extent on data_flow(source_device_type, source_number);
        create index idx_flow_destination on data_flow(destination_device);
        create index idx_flow_row on data_flow(lddb, pos);
        create index idx_xref_device on xref(device);
        create index idx_xref_span on xref(device_type, number);
        create index idx_xref_row on xref(lddb, pos);
        create index idx_xref_access on xref(access);
        create index idx_st_refs_symbol on st_refs(symbol collate nocase);
        create index idx_st_refs_device on st_refs(resolved_device);
        create index idx_st_refs_access on st_refs(access);
        create index idx_st_sources_coverage on st_sources(coverage);
        """
    )
    partial_st_sources = sum(1 for row in st_source_rows if row[5] != "supported")
    con.execute("insert into meta(key, value) values ('root', ?)", (str(root),))
    con.execute("insert into meta(key, value) values ('rows', ?)", (str(row_count),))
    con.execute("insert into meta(key, value) values ('records', ?)", (str(len(records)),))
    con.execute("insert into meta(key, value) values ('st_sources', ?)", (str(len(st_source_rows)),))
    con.execute("insert into meta(key, value) values ('st_refs', ?)", (str(len(st_ref_rows)),))
    con.execute("insert into meta(key, value) values ('st_partial_sources', ?)", (str(partial_st_sources),))
    stamp_decoder(con, root)
    con.execute("analyze")
    con.commit()
    print(f"rows={row_count} occurrences={len(records)}")
    if st_source_rows:
        print(
            f"ST evidence: sources={len(st_source_rows)} refs={len(st_ref_rows)} "
            f"physical_xref={len(st_xref_rows)} partial_sources={partial_st_sources}"
        )
        print("warning: ST/inline-ST coverage is partial; unsupported syntax was not guessed")
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
    location = ""
    if "op_index" in r.keys() and r["op_index"] is not None:
        location = f" row={r['pos']} op={r['op_index']} xy={r['element_position'] or '?'}"
    return f"  {r['pou']:<6} {step:<7} {opcode:<9} {r['access']:<5}{detail}{consts}{basis}{location}{title}"


def row_dict(row: sqlite3.Row) -> dict[str, object]:
    return {key: row[key] for key in row.keys()}


def device_filter(device: str) -> tuple[str, tuple[object, ...]]:
    """Compatibility facade; the interval contract lives in the reader."""
    from gx3cli.gx3_xref_read import interval_filter

    return interval_filter(device)


def rows_for_device(con: sqlite3.Connection, device: str, limit: int) -> list[sqlite3.Row]:
    from gx3cli.gx3_xref_read import covered_query

    source, predicate, params = covered_query(con, device)
    return con.execute(
        f"select x.* from {source} where {predicate} order by x.pou, x.pos, x.id limit ?",
        (*params, limit),
    ).fetchall()


def device_counts(con: sqlite3.Connection, device: str) -> dict[str, int]:
    counts, _total = device_count_summary(con, device)
    return counts


def device_count_summary(con: sqlite3.Connection, device: str) -> tuple[dict[str, int], int]:
    """Access totals overlap for `both`; raw occurrences are counted once."""
    from gx3cli.gx3_xref_read import covered_query

    source, predicate, params = covered_query(con, device)
    counts = {"writers": 0, "readers": 0, "refs": 0}
    total = 0
    for row in con.execute(
        f"select x.access, count(*) as n from {source} where {predicate} group by x.access", params
    ):
        n = int(row["n"])
        total += n
        if row["access"] in {"write", "both"}:
            counts["writers"] += n
        if row["access"] in {"read", "both"}:
            counts["readers"] += n
        if row["access"] not in {"read", "write", "both"}:
            counts["refs"] += n
    return counts, total


def st_symbol_rows(con: sqlite3.Connection, symbol: str) -> list[sqlite3.Row]:
    """Label/device references retained from ST, including unassigned labels."""
    return con.execute(
        "select * from st_refs where symbol = ? collate nocase order by source_file, source_location, statement_index, id",
        (symbol,),
    ).fetchall()


def st_coverage(con: sqlite3.Connection) -> dict[str, int | str]:
    row = con.execute(
        "select count(*) as total, sum(case when coverage='partial' then 1 else 0 end) as partial from st_sources"
    ).fetchone()
    total = int(row["total"] or 0)
    partial = int(row["partial"] or 0)
    refs = int(con.execute("select count(*) from st_refs").fetchone()[0] or 0)
    return {
        "state": "partial" if total else "not-present",
        "source_count": total,
        "partial_source_count": partial,
        "reference_count": refs,
    }


def st_coverage_note(con: sqlite3.Connection, *, downstream: bool = False) -> str:
    info = st_coverage(con)
    if not int(info["source_count"]):
        return ""
    suffix = (
        " Downstream traversal does not infer value-flow through ST, so an empty/short downstream result is partial."
        if downstream
        else " Absence of a writer/reader cannot be treated as complete while unresolved ST may contain more references."
    )
    return (
        f"Note: ST/inline-ST coverage is partial: {info['source_count']} source fragment(s), "
        f"{info['partial_source_count']} with unsupported/unidentified syntax, "
        f"{info['reference_count']} supported references indexed.{suffix}"
    )


def indexed_note(con: sqlite3.Connection, device: str) -> str:
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
        counts, total = device_count_summary(con, device)
        note = indexed_note(con, device).strip()
        st_note = st_coverage_note(con)
        symbol_rows = st_symbol_rows(con, device) if _split_device(device) is None else []
        st_info = st_coverage(con)
    finally:
        con.close()
    truncated = len(rows) < total
    warnings = [warning for warning in (note, st_note) if warning]
    if truncated:
        warnings.insert(0, f"Note: showing {len(rows)} of {total} occurrences (--limit {args.limit}); "
                        "writers/readers may be omitted. Increase --limit or use --limit -1 for all occurrences.")
    comment = next((r["comment"] for r in rows if r["comment"]), "")
    writers = [r for r in rows if r["access"] in {"write", "both"}]
    readers = [r for r in rows if r["access"] in {"read", "both"}]
    refs = [r for r in rows if r["access"] == "ref"]
    symbol_counts = {
        "writers": sum(1 for r in symbol_rows if r["access"] == "write"),
        "readers": sum(1 for r in symbol_rows if r["access"] == "read"),
        "refs": sum(1 for r in symbol_rows if r["access"] not in {"read", "write"}),
    }
    found_any = bool(total or symbol_rows)
    cross = cross_where_used(args, device) if args.cross else None
    if args.json:
        print(
            json.dumps(
                {
                    "command": "xref where-used",
                    "root": str(args.root),
                    **({"cross": cross} if cross is not None else {}),
                    "results": [
                        {
                            "device": device,
                            "comment": comment,
                            "writers": [row_dict(r) for r in writers],
                            "readers": [row_dict(r) for r in readers],
                            "refs": [row_dict(r) for r in refs],
                            "st_symbol_refs": [row_dict(r) for r in symbol_rows],
                            "st_symbol_counts": symbol_counts,
                            "coverage": {"st": st_info},
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
        return 0 if found_any else 1
    if not found_any:
        print(f"no occurrences: {device}")
        for warning in warnings:
            print(warning)
        if cross is not None:
            print_cross_where_used(args, device, report=cross)
        return 1
    print(f"{device} {comment}".rstrip())
    for warning in warnings:
        print(warning)

    if total:
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
    if symbol_rows:
        print(f"\nST symbol refs ({len(symbol_rows)}):")
        for r in symbol_rows:
            resolved = f" -> {r['resolved_device']}" if r["resolved_device"] else ""
            print(
                f"  {r['pou'] or '?':<12} {r['access']:<5} {r['source_kind']} "
                f"{r['source_file']} {r['source_location']} stmt={r['statement_index']}{resolved}"
            )
    if args.cross:
        print_cross_where_used(args, device, report=cross)
    return 0


def cross_where_used(args: argparse.Namespace, device: str) -> dict[str, object]:
    from gx3cli.gx3_analysis_state import AnalysisState, NOT_EVALUATED, PARTIAL, TRUNCATED, DISCOVERY, DECODE, SEMANTICS, REACH, worst

    link_db = Path(args.link_db)
    project = args.project or project_label_from_root(Path(args.root))
    report = {"project": project, "device": device, "link_db": str(link_db),
              "scope": "saved link-map rows and indexed occurrences; mapping provenance is not verified",
              "targets": [], "target_count": 0, "returned_target_count": 0, "truncated": False}
    states = []
    if not link_db.exists():
        report["analysis"] = AnalysisState(NOT_EVALUATED, f"link-map db not found: {link_db}", "provide the saved link-map database", stage=DISCOVERY).as_dict()
        return report
    # Path.as_uri() imports urllib.request on Python 3.14. SQLite needs only
    # an escaped local path, not URL-to-path/platform networking machinery.
    link_con = sqlite3.connect("file:" + quote(link_db.resolve().as_posix(), safe="/:") + "?mode=ro", uri=True)
    link_con.row_factory = sqlite3.Row
    try:
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
        states.append(AnalysisState())  # The saved link rows were examined, even if empty.
        selected = rows if args.cross_limit < 0 else rows[:args.cross_limit]
        report.update(target_count=len(rows), returned_target_count=len(selected), truncated=len(selected) < len(rows))
        if report["truncated"]:
            states.append(AnalysisState(TRUNCATED, "linked targets hidden by --cross-limit", "increase --cross-limit or use -1", stage=REACH))
        for link in selected:
            other_project = str(link["other_project"])
            other_device = str(link["other_device"])
            target = {"project": other_project, "device": other_device,
                      **{key: link[key] for key in ("link_type", "direction", "confidence", "role")}}
            report["targets"].append(target)
            db_row = link_con.execute(
                "select root, xref_db from project where label=?", (other_project,)
            ).fetchone()
            if not db_row or not Path(str(db_row["xref_db"])).exists():
                state = AnalysisState(NOT_EVALUATED, "linked project or xref database is unavailable", "restore the linked project/index and verify the mapping", stage=DISCOVERY)
                target["analysis"] = state.as_dict()
                states.append(state)
                continue
            xref_path = Path(str(db_row["xref_db"]))
            other_root = Path(str(db_row["root"]))
            target.update(root=str(other_root), xref_db=str(xref_path))
            other_con = open_xref_db(xref_path, read_only=True, root=other_root)
            try:
                other_rows = rows_for_device(other_con, other_device, args.cross_xref_limit)
                counts, total = device_count_summary(other_con, other_device)
                target_states = [AnalysisState()]  # An empty checked query is not a skipped query.
                warnings = []
                for warning, stage in ((indexed_note(other_con, other_device).strip(), SEMANTICS), (st_coverage_note(other_con), DECODE)):
                    if warning:
                        warnings.append(warning)
                        target_states.append(AnalysisState(PARTIAL, warning, "inspect unresolved references in the linked project", stage=stage))
                truncated = len(other_rows) < total
                if truncated:
                    target_states.append(AnalysisState(TRUNCATED, "linked occurrences hidden by --cross-xref-limit", "increase --cross-xref-limit or use -1", stage=REACH))
                writers = [r for r in other_rows if r["access"] in {"write", "both"}]
                readers = [r for r in other_rows if r["access"] in {"read", "both"}]
                refs = [r for r in other_rows if r["access"] not in {"read", "write", "both"}]
                target.update(writers=[row_dict(r) for r in writers], readers=[row_dict(r) for r in readers], refs=[row_dict(r) for r in refs],
                              total_counts=counts, total_count=total, returned_count=len(other_rows), limit=args.cross_xref_limit,
                              truncated=truncated, warnings=warnings, analysis=worst(target_states).as_dict())
                states.extend(target_states)
            finally:
                other_con.close()
    finally:
        link_con.close()
    report["analysis"] = worst(states).as_dict()
    return report


def print_cross_where_used(args: argparse.Namespace, device: str, *, report: dict | None = None) -> None:
    report = cross_where_used(args, device) if report is None else report
    print(f"\nCross-link targets via {report['link_db']} ({report['project']}:{device}):")
    print("  Scope: " + report["scope"])
    if report["analysis"]["state"] != "checked":
        print(f"  {report['analysis']['state']}: {report['analysis']['reason']}")
    if not report["targets"]:
        print("  (no linked targets shown)")
    for target in report["targets"]:
        print(f"  -> {target['project']}:{target['device']} type={target['link_type']} dir={target['direction']} confidence={target['confidence']} role={target['role']}")
        if "total_count" not in target:
            print("     " + target["analysis"]["reason"])
            continue
        for warning in target["warnings"]:
            print("     " + warning)
        if not target["total_count"]:
            print("     no indexed xref occurrences")
        if target["truncated"]:
            print(f"     showing {target['returned_count']} of {target['total_count']} occurrences (--cross-xref-limit {target['limit']}); increase the limit or use -1")
        for label, key in (("Writers", "writers"), ("Readers", "readers"), ("Unclassified refs", "refs")):
            if target["total_counts"][key]:
                print(f"     {label} ({len(target[key])} shown / {target['total_counts'][key]} total):")
                for row in target[key]:
                    print("   " + fmt_row(row) + span_note(row, target["device"]))
    if report["truncated"]:
        print(f"  ... {report['target_count'] - report['returned_target_count']} more cross-link targets suppressed")


def downstream(args: argparse.Namespace) -> int:
    device = normalize_device(args.device)
    con = open_db(args)
    has_flow = has_value_edges(con)

    start_comment = con.execute(
        "select comment from xref where device=? and comment<>'' limit 1", (device,)
    ).fetchone()
    print(f"downstream impact of {device} {start_comment[0] if start_comment else ''}".rstrip())
    print(f"(max-depth={args.max_depth}, strict-bit={args.strict_bit})")
    st_note = st_coverage_note(con, downstream=True)
    if st_note:
        print(st_note)
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

    found = reach(con, device, args.max_depth, args.max_nodes, args.strict_bit)

    by_source: dict[str, list] = {}
    for item in found.steps:
        by_source.setdefault(item.source, []).append(item)

    for source, items in by_source.items():
        for item in items[: args.max_children]:
            indent = "  " * item.depth
            step = f"st{item.step}" if item.step is not None else ""
            print(
                f"{indent}{item.device:<14} {item.basis:<10} "
                f"{item.pou:<6}{step:<7} {item.comment}".rstrip()
            )
        if len(items) > args.max_children:
            indent = "  " * items[0].depth
            print(
                f"{indent}... {len(items) - args.max_children} more direct targets "
                f"of {source} suppressed"
            )

    total = len(found.steps)
    if found.truncated:
        print(
            f"\n... the walk stopped at {', '.join(sorted(found.stopped))}; "
            "devices past that are not listed"
        )
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

    p = sub.add_parser("where-used", help="list writers/readers of one device or ST label")
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
