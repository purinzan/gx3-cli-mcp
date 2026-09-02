from __future__ import annotations

"""A cross-reference database is a decoding of the ladder, frozen at the moment
it was built. Nothing about it changes when the decoder does, so a database
built before a decoder fix keeps answering with the old reading -- and lint,
trace-device, dead-logic and timing-chart read it without noticing. The lite
index has guarded its own spelling change this way for a while; this is the
same guard for the xref side."""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_xref import XREF_DECODER, open_xref_db, stamp_decoder


def make_db(path: Path, decoder: str | None) -> None:
    con = sqlite3.connect(path)
    con.execute("create table xref (id integer primary key, device text)")
    con.execute("create table meta(key text primary key, value text not null)")
    if decoder is not None:
        con.execute("insert into meta(key, value) values ('decoder', ?)", (decoder,))
    con.commit()
    con.close()


def expect_refused(path: Path, why: str) -> None:
    try:
        con = open_xref_db(path)
    except SystemExit as exc:
        message = str(exc)
        assert "decoder version" in message, message
        assert "rebuild" in message, message
        return
    con.close()
    raise AssertionError(f"{why}: the database was accepted")


def test_a_database_from_another_decoder_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        old = Path(tmp) / "old_xref.sqlite"
        make_db(old, "arg-decode-1")
        expect_refused(old, "a database from an older decoder")

        unstamped = Path(tmp) / "unstamped_xref.sqlite"
        make_db(unstamped, None)
        expect_refused(unstamped, "a database with no decoder recorded")


def test_a_database_from_this_decoder_opens() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        current = Path(tmp) / "current_xref.sqlite"
        make_db(current, XREF_DECODER)
        con = open_xref_db(current)
        con.close()


def test_the_build_stamp_and_the_reader_agree() -> None:
    # stamp_decoder() is what a real build calls, so what it writes has to be
    # what the reader accepts; the two drifting apart is the failure this
    # whole guard exists to prevent.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "built_xref.sqlite"
        con = sqlite3.connect(path)
        con.execute("create table xref (id integer primary key, device text)")
        stamp_decoder(con)
        con.commit()
        con.close()
        con = open_xref_db(path, read_only=True)
        con.close()


def main() -> int:
    test_a_database_from_another_decoder_is_refused()
    test_a_database_from_this_decoder_opens()
    test_the_build_stamp_and_the_reader_agree()
    print("xref decoder version checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
