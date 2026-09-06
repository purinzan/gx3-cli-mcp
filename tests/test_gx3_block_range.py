from __future__ import annotations

"""A block instruction names only the first device of the run it writes.

BMOV .. D64061 K4 writes D64061 through D64064, and the ladder spells only
D64061. The cross-reference recorded that one device, so "where is D64063
written" answered "no occurrences" -- which reads as "nothing writes this
device", not as "this tool cannot see it". On one real project 64302 devices
were in that state.

The count operand is named "(n)" in the operand tables, so the length comes
from the manuals rather than from a hand-kept list of block instructions.
"""

import argparse
import io
import sqlite3
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from gx3cli.gx3_arg_decode import block_span, parse_row_occurrences
from gx3cli.gx3_index_lite import DEVICE_NAMING, create_schema, device_map, occupied_intervals
from gx3cli.gx3_xref import XREF_DECODER, rows_for_device, stamp_decoder


# SM400 driving BMOV D64060 D64061 K4: reads from D64060, writes the four
# devices starting at D64061.
ROW = (
    "V1:9:1:1:4:1:2:3:a:SM:BMOV:D:D:K_1:cb{fg=fg{dim=6x1:es=["
    "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=400:vt=nn}]}:pos=0,0}:"
    "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
    "d{s=#:a=64060:vt=nn}:d{s=#:a=64061:vt=nn}:c{s=#:v=4}]}:pos=1,0}]}}"
)


def operation_row(opcode: str, types: str, raw_args: str) -> str:
    """One contact followed by an arbitrary instruction for span regressions."""
    argc = raw_args.count(":") + 1
    attrs = ":".join(["1"] * max(2, argc))
    return (
        f"V1:9:1:1:{attrs}:a:SM:{opcode}:{types}:cb{{fg=fg{{dim=6x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=400:vt=nn}]}:pos=0,0}:"
        f"e{{s=ce{{op=cl{{op=#:ct=a:as=[as{{vt=A16}}]}}:args=[{raw_args}]}}:pos=1,0}}]}}}}"
    )


def operation_spans(data: str, opcode: str) -> dict[str, tuple[str, int]]:
    operations, status = parse_row_occurrences(data)
    assert status == "exact", (opcode, status)
    entry = next(item for item in operations if item[1] == opcode)
    return {occ.device: (occ.access, occ.range_len) for occ in entry[2] if occ.device.startswith("D")}


XREF_SCHEMA = """
create table xref (
    id integer primary key autoincrement,
    device text,
    device_type text,
    number integer,
    range_len integer not null default 1,
    access text,
    role text,
    opcode text,
    arg_index integer,
    const_args text,
    detail text,
    access_basis text,
    lddb text,
    pos integer,
    pou text,
    step integer,
    title text,
    comment text,
    parse_status text
)
"""


def test_the_destination_carries_the_length_the_manual_names() -> None:
    operations, status = parse_row_occurrences(ROW)
    assert status == "exact", status
    bmov = [entry for entry in operations if entry[1] == "BMOV"][0]
    spans = {occ.device: (occ.access, occ.range_len) for occ in bmov[2]}
    assert spans["D64061"] == ("write", 4), spans
    # BMOV copies a run: the source covers the same four devices. Which
    # instructions do that is written down per instruction, because the
    # operand tables spell BMOV and FMOV identically -- see
    # SOURCE_RUN_OPERANDS, and the fill case below.
    assert spans["D64060"] == ("read", 4), spans


def test_a_fill_reads_one_device_however_many_it_writes() -> None:
    fill = ROW.replace(":BMOV:", ":FMOV:")
    operations, _ = parse_row_occurrences(fill)
    fmov = [entry for entry in operations if entry[1] == "FMOV"][0]
    spans = {occ.device: (occ.access, occ.range_len) for occ in fmov[2]}
    assert spans["D64061"] == ("write", 4), spans
    assert spans["D64060"] == ("read", 1), spans


def test_a_double_word_fill_multiplies_element_count_by_width() -> None:
    row = operation_row(
        "DFMOV", "D:D:K_1",
        "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}:c{s=#:v=5}",
    )
    spans = operation_spans(row, "DFMOV")
    assert spans["D100"] == ("read", 2), spans
    assert spans["D200"] == ("write", 10), spans


def test_word_to_byte_and_byte_to_word_use_physical_word_spans() -> None:
    wtob = operation_row(
        "WTOB", "D:D:K_1",
        "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}:c{s=#:v=5}",
    )
    btow = operation_row(
        "BTOW", "D:D:K_1",
        "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}:c{s=#:v=5}",
    )
    wtob_spans = operation_spans(wtob, "WTOB")
    btow_spans = operation_spans(btow, "BTOW")

    # WTOB separates five bytes: three source words contain those bytes, and
    # five destination word devices each receive one separated byte.
    assert wtob_spans["D100"] == ("read", 3), wtob_spans
    assert wtob_spans["D200"] == ("write", 5), wtob_spans

    # BTOW consumes five low bytes from five source devices and packs them into
    # three destination words (the last high byte is zero for an odd count).
    assert btow_spans["D100"] == ("read", 5), btow_spans
    assert btow_spans["D200"] == ("write", 3), btow_spans


def test_block_add_writes_d_and_covers_all_three_blocks() -> None:
    row = operation_row(
        "BK+", "D:D:D:K_1",
        "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}:d{s=#:a=300:vt=nn}:c{s=#:v=5}",
    )
    spans = operation_spans(row, "BK+")
    assert spans["D100"] == ("read", 5), spans
    assert spans["D200"] == ("read", 5), spans
    assert spans["D300"] == ("write", 5), spans


def test_an_indexed_block_source_is_not_materialized_as_a_static_run() -> None:
    indexed = ROW.replace(
        ":BMOV:D:D:K_1:",
        ":BMOV:D:Zs:D:K_1:",
    ).replace(
        "d{s=#:a=64060:vt=nn}:d{s=#:a=64061:vt=nn}",
        "M{b=d{s=#:a=64060:vt=nn}:m=d{s=#:a=2:vt=nn}}:d{s=#:a=64061:vt=nn}",
    )
    operations, status = parse_row_occurrences(indexed)
    assert status == "exact", status
    bmov = next(entry for entry in operations if entry[1] == "BMOV")
    spans = {occ.device: (occ.access, occ.range_len) for occ in bmov[2]}
    assert spans["D64060"] == ("read", 1), spans
    assert spans["Z2"] == ("read", 1), spans
    assert spans["D64061"] == ("write", 4), spans


def test_an_instruction_with_no_count_operand_is_left_alone() -> None:
    # MOV takes (s) and (d); no "(n)", so nothing to span.
    assert block_span("MOV", ["d{s=#:a=1:vt=nn}", "d{s=#:a=2:vt=nn}"]) == (1, "")


def test_a_count_held_in_a_device_is_reported_as_unknown() -> None:
    # The run is as long as that device says at runtime. Reporting a length
    # would put occurrences on devices the instruction may never touch.
    length, basis = block_span("BMOV", ["d{s=#:a=1:vt=nn}", "d{s=#:a=2:vt=nn}", "d{s=#:a=3:vt=nn}"])
    assert length == 0, (length, basis)
    assert "device" in basis, basis


def build_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(XREF_SCHEMA)
    stamp_decoder(con)
    con.execute(
        "insert into xref(device, device_type, number, range_len, access, role, opcode,"
        " lddb, pos, pou) values ('D64061', 'D', 64061, 4, 'write', 'BMOV', 'BMOV', 'a.db', 1, 'P1')"
    )
    con.execute(
        "insert into xref(device, device_type, number, range_len, access, role, opcode,"
        " lddb, pos, pou) values ('D70000', 'D', 70000, 0, 'write', 'BMOV', 'BMOV', 'a.db', 2, 'P1')"
    )
    con.commit()
    con.close()


def test_a_search_finds_the_device_the_run_writes_without_naming() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "xref.sqlite"
        build_db(path)
        con = sqlite3.connect(path)
        con.row_factory = sqlite3.Row

        for device in ("D64061", "D64062", "D64063", "D64064"):
            rows = rows_for_device(con, device, 10)
            assert len(rows) == 1, f"{device} was not found in the run: {rows}"

        # One past the end is a different device and must stay unfound.
        assert rows_for_device(con, "D64065", 10) == [], "the run reached too far"
        assert rows_for_device(con, "D64060", 10) == [], "the run reached backwards"

        # A run of unknown length is found only where it starts.
        assert len(rows_for_device(con, "D70000", 10)) == 1
        assert rows_for_device(con, "D70001", 10) == [], "an unknown length was guessed at"

        con.close()


def decoder_generation(stamp: str) -> int:
    """The number at the end of "arg-decode-N"."""
    return int(stamp.rsplit("-", 1)[1])


def test_the_stamp_moved_with_the_change() -> None:
    # Count-unit semantics change persisted physical coverage even when the GX3
    # input fingerprint stayed the same, so old xref databases must be rebuilt.
    # Asserted as "at least", not as a literal: later coverage changes move the
    # stamp too without making this test meaningless.
    assert decoder_generation(XREF_DECODER) >= 5, XREF_DECODER


def add_index_device(con: sqlite3.Connection, device: str, device_type: str, number: int) -> None:
    con.execute(
        """
        insert into devices(
            device, device_type, number, comment, occurrences, driver_rows,
            condition_uses, roles, first_lddb, first_pos, first_title
        ) values (?, ?, ?, '', 1, 1, 0, '', '', 0, '')
        """,
        (device, device_type, number),
    )


def make_lite_index(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    create_schema(con)
    con.execute("insert into meta(key, value) values ('device_naming', ?)", (DEVICE_NAMING,))
    return con


def test_device_map_does_not_call_covered_write_members_free() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_device_map_") as tmp:
        db = Path(tmp) / "index.sqlite"
        con = make_lite_index(db)
        add_index_device(con, "D400", "D", 400)
        add_index_device(con, "D410", "D", 410)
        con.execute(
            """
            insert into covered_ranges(device_type, start, length, access, opcode, lddb, pos)
            values ('D', 400, 4, 'write', 'BMOV', 'SYNTH', 1)
            """
        )
        con.commit()
        assert occupied_intervals(con)["D"] == [(400, 403), (410, 410)]
        con.close()

        out = io.StringIO()
        args = argparse.Namespace(db=str(db), root="", min_free=1, types="D", max_gaps=8)
        with redirect_stdout(out):
            assert device_map(args) == 0
        text = out.getvalue()
        assert "D404-D409(6)" in text, text
        assert "D401" not in text and "D402" not in text and "D403" not in text, text


def test_device_map_treats_read_only_covered_ranges_as_occupied() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_device_map_read_") as tmp:
        db = Path(tmp) / "index.sqlite"
        con = make_lite_index(db)
        add_index_device(con, "D500", "D", 500)
        add_index_device(con, "D510", "D", 510)
        con.execute(
            """
            insert into covered_ranges(device_type, start, length, access, opcode, lddb, pos)
            values ('D', 503, 3, 'read', 'BMOV', 'SYNTH', 2)
            """
        )
        con.commit()
        assert occupied_intervals(con)["D"] == [(500, 500), (503, 505), (510, 510)]
        con.close()

        out = io.StringIO()
        args = argparse.Namespace(db=str(db), root="", min_free=1, types="D", max_gaps=8)
        with redirect_stdout(out):
            device_map(args)
        text = out.getvalue()
        assert "D501-D502(2)" in text, text
        assert "D506-D509(4)" in text, text
        assert "D503-D505" not in text, text


def test_device_map_merges_overlapping_named_and_covered_ranges_once() -> None:
    with tempfile.TemporaryDirectory(prefix="gx3_device_map_overlap_") as tmp:
        db = Path(tmp) / "index.sqlite"
        con = make_lite_index(db)
        add_index_device(con, "D601", "D", 601)
        con.executemany(
            """
            insert into covered_ranges(device_type, start, length, access, opcode, lddb, pos)
            values ('D', ?, ?, 'write', 'BMOV', 'SYNTH', ?)
            """,
            [(600, 4, 1), (602, 4, 2)],
        )
        con.commit()
        assert occupied_intervals(con)["D"] == [(600, 605)]
        con.close()


def main() -> int:
    test_the_destination_carries_the_length_the_manual_names()
    test_a_fill_reads_one_device_however_many_it_writes()
    test_a_double_word_fill_multiplies_element_count_by_width()
    test_word_to_byte_and_byte_to_word_use_physical_word_spans()
    test_block_add_writes_d_and_covers_all_three_blocks()
    test_an_indexed_block_source_is_not_materialized_as_a_static_run()
    test_an_instruction_with_no_count_operand_is_left_alone()
    test_a_count_held_in_a_device_is_reported_as_unknown()
    test_a_search_finds_the_device_the_run_writes_without_naming()
    test_the_stamp_moved_with_the_change()
    test_device_map_does_not_call_covered_write_members_free()
    test_device_map_treats_read_only_covered_ranges_as_occupied()
    test_device_map_merges_overlapping_named_and_covered_ranges_once()
    print("block range checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
