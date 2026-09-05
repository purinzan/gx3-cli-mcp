from __future__ import annotations

"""dead-logic says a device is written but never read. Is it never read?

A block instruction or a digit specification reaches devices the ladder never
names. K8L12800 compares L12800 through L12831; L12821 is one of them, written
by a coil and read by that comparison every scan. Counting reads per device
name did not see it, so dead-logic reported it as never read -- 175 of its 4113
"never read" findings on one real project were of that kind.

This is the same false positive lint's unused-device had, in a module that
reads the cross-reference directly rather than the lite index.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_dead_logic import read_by_a_run, runs_read_by_the_program


XREF_SCHEMA = """
create table xref (
    id integer primary key autoincrement,
    device text, device_type text, number integer,
    range_len integer not null default 1,
    access text, role text, opcode text, detail text,
    lddb text, pos integer, pou text, step integer
)
"""


def build(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(XREF_SCHEMA)
    con.execute(
        "insert into xref(device, device_type, number, range_len, access, role, opcode, detail)"
        " values ('L12800','L',12800,32,'read','=','=','digit=K8; covers 32 devices')"
    )
    con.execute(
        "insert into xref(device, device_type, number, range_len, access, role, opcode)"
        " values ('D64061','D',64061,4,'write','BMOV','BMOV')"
    )
    con.commit()
    con.row_factory = sqlite3.Row
    return con


def test_a_bit_inside_a_read_run_counts_as_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        con = build(Path(tmp) / "xref.sqlite")
        runs = runs_read_by_the_program(con)
        assert read_by_a_run(runs, "L", "L12821"), runs
        assert read_by_a_run(runs, "L", "L12800"), runs
        assert read_by_a_run(runs, "L", "L12831"), runs
        # One past the end is a different device.
        assert not read_by_a_run(runs, "L", "L12832"), runs
        # Another type is not covered by an L run.
        assert not read_by_a_run(runs, "M", "M12821"), runs
        con.close()


def test_a_run_that_is_only_written_does_not_count_as_read() -> None:
    # BMOV writes D64061..D64064. That says nothing about them being read, and
    # treating it as a read would hide the finding this check exists for.
    with tempfile.TemporaryDirectory() as tmp:
        con = build(Path(tmp) / "xref.sqlite")
        runs = runs_read_by_the_program(con)
        assert not read_by_a_run(runs, "D", "D64063"), runs
        con.close()


def test_a_database_without_spans_still_answers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.sqlite"
        con = sqlite3.connect(path)
        con.execute("create table xref (device text)")
        con.commit()
        con.row_factory = sqlite3.Row
        assert runs_read_by_the_program(con) == {}
        con.close()


def main() -> int:
    test_a_bit_inside_a_read_run_counts_as_read()
    test_a_run_that_is_only_written_does_not_count_as_read()
    test_a_database_without_spans_still_answers()
    print("dead-logic run checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
