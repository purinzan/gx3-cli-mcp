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

from gx3cli.gx3_xref import XREF_DECODER, open_xref_db


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

        before_flow_spans = Path(tmp) / "v6_xref.sqlite"
        make_db(before_flow_spans, "arg-decode-strefs-countspans-6")
        expect_refused(before_flow_spans, "physical flow spans were not persisted in v6")

        unstamped = Path(tmp) / "unstamped_xref.sqlite"
        make_db(unstamped, None)
        expect_refused(unstamped, "a database with no decoder recorded")


def test_current_decoder_alone_is_not_a_verified_build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        current = Path(tmp) / "current_xref.sqlite"
        make_db(current, XREF_DECODER)
        try:
            con = open_xref_db(current)
        except SystemExit as exc:
            assert "build contract" in str(exc), exc
        else:
            con.close()
            raise AssertionError("decoder stamp alone certified an old build")


def test_the_build_stamp_and_the_reader_agree() -> None:
    # Exercise the actual builder: stamp_decoder alone does not establish
    # stable-input construction provenance.
    from test_gx3_shared_reach import write_program
    from gx3cli.gx3_intermediate_tool import generate_rung
    from gx3cli.gx3_xref import build, build_parser

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "built_xref.sqlite"
        root = Path(tmp) / "project"
        write_program(root, [("_guid/rung", generate_rung(
            {"device": "M100"}, {"type": "coil", "device": "Y0"})[0])])
        build(build_parser().parse_args(["--root", str(root), "--db", str(path), "build"]))
        con = open_xref_db(path, read_only=True)
        con.close()


def main() -> int:
    test_a_database_from_another_decoder_is_refused()
    test_current_decoder_alone_is_not_a_verified_build()
    test_the_build_stamp_and_the_reader_agree()
    print("xref decoder version checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
