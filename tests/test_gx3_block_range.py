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

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_arg_decode import block_span, parse_row_occurrences
from gx3cli.gx3_xref import XREF_DECODER, rows_for_device, stamp_decoder


# SM400 driving BMOV D64060 D64061 K4: reads from D64060, writes the four
# devices starting at D64061.
ROW = (
    "V1:9:1:1:4:1:2:3:a:SM:BMOV:D:D:K_1:cb{fg=fg{dim=6x1:es=["
    "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=400:vt=nn}]}:pos=0,0}:"
    "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
    "d{s=#:a=64060:vt=nn}:d{s=#:a=64061:vt=nn}:c{s=#:v=4}]}:pos=1,0}]}}"
)

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
    # Whether a source covers the same run differs by instruction and the
    # operand tables do not say, so the source stays a single device.
    assert spans["D64060"] == ("read", 1), spans


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


def test_the_stamp_moved_with_the_change() -> None:
    # Existing databases have no range_len, so they answer the old way; the
    # decoder version is what stops them being read as if they did.
    assert XREF_DECODER == "arg-decode-3", XREF_DECODER


def main() -> int:
    test_the_destination_carries_the_length_the_manual_names()
    test_an_instruction_with_no_count_operand_is_left_alone()
    test_a_count_held_in_a_device_is_reported_as_unknown()
    test_a_search_finds_the_device_the_run_writes_without_naming()
    test_the_stamp_moved_with_the_change()
    print("block range checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
