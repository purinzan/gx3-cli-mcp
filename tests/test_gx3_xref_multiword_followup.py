from __future__ import annotations

"""Follow-up coverage checks for #96 multi-word operands.

The first fix proved that DMOV's high destination word is searchable. These
checks pin the remaining acceptance cases: the high source word, the pulse
form, and a four-word operand.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_xref_read import occurrences_of
from test_gx3_shared_reach import build_xref, rung, write_program


def project(work: Path, opcode: str) -> Path:
    instruction = rung(
        f"{opcode}:D:D",
        "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}",
    )
    write_program(work / "p", [("_guid/op", instruction)])
    return build_xref(work / "p", work / "x.sqlite")


def opcodes(db: Path, device: str, access: tuple[str, ...]) -> list[str]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = occurrences_of(con, device, access=access)
        return [str(row["opcode"]) for row in rows]
    finally:
        con.close()


def test_dmov_high_source_word_is_a_read_member() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = project(Path(tmp), "DMOV")
        assert opcodes(db, "D101", ("read", "both")) == ["DMOV"]
        assert opcodes(db, "D102", ("read", "both")) == []


def test_dmovp_keeps_the_same_two_word_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = project(Path(tmp), "DMOVP")
        assert opcodes(db, "D101", ("read", "both")) == ["DMOVP"]
        assert opcodes(db, "D201", ("write", "both")) == ["DMOVP"]
        assert opcodes(db, "D202", ("write", "both")) == []


def test_edmov_covers_all_four_destination_words() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = project(Path(tmp), "EDMOV")
        for device in ("D200", "D201", "D202", "D203"):
            assert opcodes(db, device, ("write", "both")) == ["EDMOV"], device
        assert opcodes(db, "D204", ("write", "both")) == []


def main() -> int:
    test_dmov_high_source_word_is_a_read_member()
    test_dmovp_keeps_the_same_two_word_coverage()
    test_edmov_covers_all_four_destination_words()
    print("xref multi-word follow-up checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
