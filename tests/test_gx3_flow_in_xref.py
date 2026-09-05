from __future__ import annotations

"""A transfer and a coincidence are different facts, and downstream says which.

`downstream` answers "what does this device affect" by joining reads and writes
that happen on the same rung. That cannot tell "D100 was moved into D200" from
"D100 and D200 were both mentioned on one rung", and #36 is about exactly that
difference: without a directed edge, provenance for a word device is a guess
wearing the clothes of an answer.

The edges already existed, in `data-flow`, and any caller that wanted them had
to re-read every program of the project first -- 16 seconds on a real one. They
are now built once, into the cross-reference, where downstream can ask for them
and graph and lint can next.

A cross-reference built before this holds none. That is reported as what it is:
everything reads as same-rung, and the header says the answer cannot tell the
two apart until it is rebuilt.
"""

import argparse
import contextlib
import io
import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_xref import downstream, main as xref_main


def rung(header: str, args: str) -> str:
    """One rung in the stored intermediate form: a contact driving one operation."""
    return (
        f"V1:9:1:1:1:1:1:1:a:M:{header}:cb{{fg=fg{{dim=4x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=1:vt=nn}]}:pos=0,0}:"
        "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
        f"{args}]}}:pos=1,0}}]}}}}"
    )


MOV = rung("MOV:D:D", "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}")
BMOV = rung("BMOV:D:D:K_1", "d{s=#:a=300:vt=nn}:d{s=#:a=400:vt=nn}:c{s=#:v=4}")
PLUS = rung("+:D:D", "d{s=#:a=500:vt=nn}:d{s=#:a=600:vt=nn}")
UNKNOWN = rung("ZZNOSUCH:D:D", "d{s=#:a=700:vt=nn}:d{s=#:a=800:vt=nn}")


def a_project(tmp: Path) -> Path:
    root = tmp / "fixture"
    root.mkdir()
    con = sqlite3.connect(root / "001_LDDB.db")
    con.execute(
        "create table LadderBlocks (id text, pos real, blocktype integer, data text, "
        "rowsize integer, translated integer, ConvTarget integer)"
    )
    con.executemany(
        "insert into LadderBlocks values (?,?,?,?,?,?,?)",
        [
            ("g1", 0.0, 0, MOV, 1, 0, 0),
            ("g2", 16.0, 0, BMOV, 1, 0, 0),
            ("g3", 32.0, 0, PLUS, 1, 0, 0),
            ("g4", 48.0, 0, UNKNOWN, 1, 0, 0),
        ],
    )
    con.commit()
    con.close()
    return root


def build(root: Path, db: Path) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        assert xref_main(["--root", str(root), "--db", str(db), "build"]) == 0


def edges(db: Path) -> list[sqlite3.Row]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("select * from data_flow order by id").fetchall()
    finally:
        con.close()


def test_a_transfer_records_one_directed_edge() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        db = work / "x.sqlite"
        build(a_project(work), db)
        movs = [row for row in edges(db) if row["opcode"] == "MOV"]
        assert len(movs) == 1, [dict(r) for r in movs]
        assert movs[0]["source_device"] == "D100", dict(movs[0])
        assert movs[0]["destination_device"] == "D200", dict(movs[0])
        assert movs[0]["source_arg_index"] != movs[0]["destination_arg_index"]


def test_a_block_transfer_keeps_its_count_and_width() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        db = work / "x.sqlite"
        build(a_project(work), db)
        block = [row for row in edges(db) if row["opcode"] == "BMOV"]
        assert len(block) == 1, [dict(r) for r in block]
        assert int(block[0]["range_count"]) == 4, dict(block[0])
        assert int(block[0]["source_word_width"]) >= 1
        assert int(block[0]["destination_word_width"]) >= 1


def test_a_read_modify_write_says_so() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        db = work / "x.sqlite"
        build(a_project(work), db)
        arithmetic = [row for row in edges(db) if row["opcode"] == "+"]
        assert arithmetic, "the two-operand arithmetic produced no edge"
        assert any(int(row["read_modify_write"]) == 1 for row in arithmetic), [
            dict(r) for r in arithmetic
        ]


def test_an_unknown_instruction_gets_no_edge() -> None:
    # #36: never a guessed edge. An edge here asserts the value goes there.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        db = work / "x.sqlite"
        build(a_project(work), db)
        assert not [row for row in edges(db) if row["opcode"] == "ZZNOSUCH"], "an edge was invented"


def test_every_edge_points_back_at_a_rung_and_an_argument() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        db = work / "x.sqlite"
        build(a_project(work), db)
        for row in edges(db):
            assert row["lddb"], dict(row)
            assert row["pos"] is not None, dict(row)
            assert row["source_arg_index"] is not None, dict(row)
            assert row["destination_arg_index"] is not None, dict(row)
            assert row["confidence"] in {"manual", "fallback", "unknown"}, dict(row)


def run_downstream(db: Path, root: Path, device: str) -> str:
    args = argparse.Namespace(
        db=str(db), root=str(root), device=device,
        max_depth=2, max_nodes=50, max_children=50, strict_bit=False,
    )
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        downstream(args)
    return out.getvalue()


def test_downstream_marks_the_transfer_as_a_transfer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        db = work / "x.sqlite"
        root = a_project(work)
        build(root, db)
        text = run_downstream(db, root, "D100")
        assert "D200" in text, text
        assert "via MOV" in text, text


def test_a_cross_reference_without_edges_says_it_cannot_tell() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        db = work / "x.sqlite"
        root = a_project(work)
        build(root, db)
        con = sqlite3.connect(db)
        con.execute("drop table data_flow")
        con.commit()
        con.close()

        text = run_downstream(db, root, "D100")
        assert "no value-flow" in text, text
        assert "via MOV" not in text, text


def main() -> int:
    test_a_transfer_records_one_directed_edge()
    test_a_block_transfer_keeps_its_count_and_width()
    test_a_read_modify_write_says_so()
    test_an_unknown_instruction_gets_no_edge()
    test_every_edge_points_back_at_a_rung_and_an_argument()
    test_downstream_marks_the_transfer_as_a_transfer()
    test_a_cross_reference_without_edges_says_it_cannot_tell()
    print("flow in xref checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
