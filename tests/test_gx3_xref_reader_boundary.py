from __future__ import annotations

"""A reader of the cross-reference cannot look a device up by hand.

`xref.device` is the device an instruction names, not the devices it touches:
one row with `range_len = 4` is a BMOV covering four of them. Every reader that
wrote `where device = ?` therefore missed the middles of runs, and five of them
did -- ladder-report reported zero writers for a device a BMOV fills, scan-order
could not find a stale read inside any run, alarm-map lost resets, timing-chart
lost signal conditions, lint lost 1,269 conflicts on one real project.

Each was fixed as it was found, which is the part that does not scale: the next
reader can write the same lookup, and nothing would say so. So this fails when
one does.

The list of exceptions is meant to stay short and each entry to say why. An
allowlist that grows without reasons is the convention it replaced.
"""

import re
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_xref_read import (
    COVERED,
    NAMED_ONLY,
    counts_for,
    device_match,
    has_members,
    occurrences_of,
)
from test_gx3_lint_block_runs import bmov, mov
from test_gx3_shared_reach import build_xref, write_program


ROOT = Path(__file__).resolve().parents[1]

# Modules allowed to look a device up without the reader, and why. A raw
# lookup is right when the question really is about the device an instruction
# spells, rather than the devices it reaches.
EXEMPT = {
    # Builds the member index itself, and its own range predicate is the one
    # the reader was extracted from.
    "gx3_xref.py": "defines the range predicate and builds xref_members",
    # The reader.
    "gx3_xref_read.py": "is the boundary",
    # Groups occurrences by the rung they sit on, not by device reach.
    "gx3_link_map.py": "matches devices across projects by name, not by run",
}

LOOKUP = re.compile(r"""(?:where|and)\s+device\s*=\s*\?""", re.IGNORECASE)


def statement_around(lines: list[str], index: int, span: int = 6) -> str:
    """The lines a SQL statement is likely spread across.

    The lookup and the table it reads are often on different lines, and a
    device filter against another table -- `external_sources`, say -- is not
    what this is about.
    """
    start = max(0, index - span)
    return " ".join(lines[start : index + span + 1])


def test_no_new_reader_looks_a_device_up_by_hand() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "gx3cli").glob("*.py")):
        if path.name in EXEMPT:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for index, line in enumerate(lines):
            if not LOOKUP.search(line):
                continue
            context = statement_around(lines, index)
            if not re.search("from" + chr(92) + "s+xref", context, re.IGNORECASE):
                continue  # a device filter on some other table
            offenders.append(f"{path.name}:{index + 1}: {line.strip()}")
    assert not offenders, (
        "these look a device up without the range-aware reader; use "
        "gx3_xref_read.occurrences_of / counts_for / device_match, or add an "
        "entry to EXEMPT saying why the exact name is what the question means:\n"
        + "\n".join(offenders)
    )


def a_project(work: Path) -> tuple[Path, Path]:
    write_program(work / "p", [("_guid/b", bmov(300, 400, 4)), ("_guid/m", mov(500, 401))])
    return work / "p", build_xref(work / "p", work / "x.sqlite")


def test_the_member_index_holds_every_device_a_row_covers() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _, db = a_project(Path(tmp))
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            assert has_members(con)
            rows = con.execute(
                "select member_device, run_offset from xref_members m "
                "join xref x on x.id = m.src_id "
                "where x.opcode = 'BMOV' and x.access = 'write' order by run_offset"
            ).fetchall()
        finally:
            con.close()
        assert [r["member_device"] for r in rows] == ["D400", "D401", "D402", "D403"], rows
        assert [r["run_offset"] for r in rows] == [0, 1, 2, 3], rows


def test_asking_about_the_middle_of_a_run_finds_the_instruction() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _, db = a_project(Path(tmp))
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            covered = occurrences_of(con, "D401", access=("write", "both"))
            named = occurrences_of(con, "D401", access=("write", "both"), scope=NAMED_ONLY)
            past_end = occurrences_of(con, "D404", access=("write", "both"))
        finally:
            con.close()

        assert {str(r["opcode"]) for r in covered} == {"BMOV", "MOV"}, [dict(r) for r in covered]
        # The named-only view is what a rung listing wants: the BMOV's rung
        # says D400, not D401.
        assert {str(r["opcode"]) for r in named} == {"MOV"}, [dict(r) for r in named]
        assert past_end == [], past_end


def test_counts_follow_the_same_rule() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _, db = a_project(Path(tmp))
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            totals = counts_for(con, ["D401", "D404"])
        finally:
            con.close()
        assert totals["D401"]["write"] == 2, totals
        assert totals["D404"] == {"read": 0, "write": 0}, totals


def test_a_database_without_the_index_still_answers() -> None:
    # Older cross-references have no member table. Degrading to the exact name
    # is a narrower answer; crashing is not an answer at all, and the first
    # attempt at this fix crashed four commands.
    with tempfile.TemporaryDirectory() as tmp:
        _, db = a_project(Path(tmp))
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            con.execute("drop table xref_members")
            con.commit()
            assert not has_members(con)
            source, match = device_match(con)
            assert "xref_members" not in source and "xref_members" not in match
            rows = occurrences_of(con, "D401", access=("write", "both"))
            assert {str(r["opcode"]) for r in rows} == {"MOV"}, [dict(r) for r in rows]
        finally:
            con.close()


def test_a_run_of_unknown_length_gets_one_member() -> None:
    # `BMOV D300 D400 D10` writes as many words as D10 holds when it runs. Rows
    # invented here would put occurrences on devices the instruction may never
    # touch.
    from test_gx3_shared_reach import rung

    dynamic = rung("BMOV:D:D:D", "d{s=#:a=300:vt=nn}:d{s=#:a=400:vt=nn}:d{s=#:a=10:vt=nn}")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/b", dynamic)])
        db = build_xref(work / "p", work / "x.sqlite")
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            members = con.execute(
                "select m.member_device from xref_members m join xref x on x.id = m.src_id "
                "where x.access = 'write'"
            ).fetchall()
        finally:
            con.close()
        assert [r["member_device"] for r in members] == ["D400"], members


def main() -> int:
    test_no_new_reader_looks_a_device_up_by_hand()
    test_the_member_index_holds_every_device_a_row_covers()
    test_asking_about_the_middle_of_a_run_finds_the_instruction()
    test_counts_follow_the_same_rule()
    test_a_database_without_the_index_still_answers()
    test_a_run_of_unknown_length_gets_one_member()
    print("xref reader boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
