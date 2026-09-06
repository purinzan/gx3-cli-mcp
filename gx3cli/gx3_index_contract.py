from __future__ import annotations

"""Structural query contracts for generated indexes, not input semantics.

A matching decoder/fingerprint cannot make a dropped table readable. Check the
columns consumers need before advertising a workspace as reusable. Extra tables
and columns are permitted. This does not certify row contents or source coverage.
"""

import sqlite3
from pathlib import Path


XREF_COLUMNS = {
    "xref": "id device device_type number range_len access role opcode arg_index const_args detail access_basis lddb pos pou step title comment parse_status",
    "xref_members": "src_id member_device device_type number run_offset",
    "data_flow": "id source_device destination_device opcode source_arg_index destination_arg_index range_count source_word_width destination_word_width read_modify_write confidence parse_status lddb pos pou step title source_comment destination_comment source_range_len destination_range_len source_device_type source_number source_detail destination_detail",
    "st_sources": "id source_kind source_file source_location pou coverage reason",
    "st_refs": "id source_id symbol resolved_device access source_kind source_file source_location pou statement_index coverage reason",
}

LITE_COLUMNS = {
    "comments": "device device_type number japanese english all_text",
    "covered_ranges": "device_type start length access opcode lddb pos",
    "devices": "device device_type number comment occurrences driver_rows condition_uses roles first_lddb first_pos first_title",
    "ladder_rows": "row_id lddb pos block_id title rowsize parse_status devices",
    "device_usages": "id device device_type number role is_driver is_condition row_id lddb pos title parse_status row_conditions row_condition_comments row_all_devices",
    "external_sources": "device device_type number comment occurrences required_on_count required_off_count source_kind semantic_group source_detail stop_reason refresh_area refresh_network_label refresh_unit_name refresh_slot_number refresh_device_range source_unit_kind source_unit_name source_unit_connection source_unit_slot_number source_unit_area first_lddb first_pos first_title",
}


def schema_gaps(con: sqlite3.Connection, kind: str, tables: tuple[str, ...] | None = None) -> list[str]:
    required = {"xref": XREF_COLUMNS, "index": LITE_COLUMNS}[kind]
    if tables is not None:
        required = {table: required[table] for table in tables}
    if not required:
        return []
    actual: dict[str, set[str]] = {}
    for table, column in con.execute(
        "select m.name, p.name from sqlite_master m "
        "join pragma_table_info(m.name) p where m.type='table' "
        f"and m.name in ({','.join('?' for _ in required)})",
        tuple(required),
    ):
        actual.setdefault(str(table), set()).add(str(column))
    gaps = []
    for table, columns in required.items():
        if table not in actual:
            gaps.append(f"missing table {table}")
        else:
            missing = set(columns.split()) - actual[table]
            if missing:
                gaps.append(f"{table} missing columns: {', '.join(sorted(missing))}")
    return gaps


def check_schema(con: sqlite3.Connection, kind: str, path: Path, tables: tuple[str, ...] | None = None) -> None:
    gaps = schema_gaps(con, kind, tables)
    if gaps:
        command = "xref" if kind == "xref" else "index-lite"
        raise SystemExit(
            f"{kind} db has an incompatible or incomplete schema: {path}\n"
            + "\n".join(f"  {gap}" for gap in gaps)
            + f"\nRebuild it: gx3-cli {command} build --root <project>"
        )
