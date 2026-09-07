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

import ast
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

# Exact query exceptions, never a module/function-wide permission to add SQL.
APPROVED_NAMED_QUERIES = {
    ("gx3_xref.py", "downstream", "select comment from xref where device=? and comment<>'' limit 1"):
        "A comment belongs to the named device, not its covering writer's base address.",
    ("gx3_xref.py", "export", "select * from xref where device=? order by pou, pos"):
        "Raw stored-occurrence export with an explicit name filter, not a physical where-used query.",
}

# A known incomplete consumer, tracked explicitly rather than calling the
# whole module safe. Remove this entry when that consumer is migrated.
KNOWN_UNMIGRATED_QUERIES = {
    ("gx3_xref.py", "print_cross_where_used", "select * from xref where device=? order by pou, pos limit ?"):
        "#153 cross-project where-used still loses covered members; not an approved long-term query.",
}

LOOKUP = re.compile(r"\b(?:where|and|or)\s+(?:\w+\.)?device\s*=\s*\?", re.IGNORECASE)


def direct_xref_queries(source: str) -> list[tuple[str, str, int]]:
    """Inspect literal/adjacent-literal/f-string SQL at execute call sites.

    This deliberately does not claim to resolve SQL built via variables or
    arbitrary control flow. Behavioral builder-to-consumer tests remain needed.
    Nearby unrelated strings must not change which table a lookup targets.
    """
    tree = ast.parse(source)
    found = []

    def visit(node: ast.AST, owner: str = "<module>") -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            owner = node.name
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in {"execute", "executemany"} and node.args:
            query = node.args[0]
            if isinstance(query, ast.Constant) and isinstance(query.value, str):
                sql = query.value
            elif isinstance(query, ast.JoinedStr):
                sql = "".join(v.value if isinstance(v, ast.Constant) and isinstance(v.value, str) else "{expression}" for v in query.values)
            else:
                sql = ""
            normalized = " ".join(sql.lower().split())
            if re.search(r"\bfrom\s+xref\b", normalized) and LOOKUP.search(normalized):
                found.append((owner, normalized, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, owner)

    visit(tree)
    return found


def test_no_new_reader_looks_a_device_up_by_hand() -> None:
    offenders: list[str] = []
    seen = set()
    for path in sorted((ROOT / "gx3cli").glob("*.py")):
        for owner, query, line in direct_xref_queries(path.read_text(encoding="utf-8")):
            key = (path.name, owner, query)
            seen.add(key)
            if key not in APPROVED_NAMED_QUERIES and key not in KNOWN_UNMIGRATED_QUERIES:
                offenders.append(f"{path.name}:{line} {owner}: {query}")
    assert not offenders, (
        "these look a device up without the range-aware reader; use "
        "gx3_xref_read.occurrences_of / counts_for / device_match, or add an "
        "an exact query exception with its reason (never exempt a module):\n"
        + "\n".join(offenders)
    )
    assert set(APPROVED_NAMED_QUERIES) | set(KNOWN_UNMIGRATED_QUERIES) == seen, "remove stale query exceptions"


def test_guard_detects_bypasses_and_allows_other_projections() -> None:
    source = '''
def new_reader(con):
    con.execute("select x.* from xref x " "where x.device = ?", ("D401",))
    con.execute(f"select {columns} from xref where device=?", ("D401",))
    con.execute("select * from comments where device=?", ("D401",))
    con.execute("select * from xref where role='c'")
    con.execute("select x.* from xref x join xref_members m on m.src_id=x.id where m.member_device=?", ("D401",))
'''
    found = direct_xref_queries(source)
    assert len(found) == 2, found
    assert all(owner == "new_reader" for owner, _, _ in found)
    assert all(("gx3_xref.py", owner, query) not in APPROVED_NAMED_QUERIES for owner, query, _ in found)
    comment = direct_xref_queries("def downstream(con):\n con.execute(\"select comment from xref where device=? and comment<>'' limit 1\")")
    assert ("gx3_xref.py", comment[0][0], comment[0][1]) in APPROVED_NAMED_QUERIES


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


def test_a_multi_word_operand_covers_the_words_it_occupies() -> None:
    """#96: `DMOV D100 D200` reads D100..D101 and writes D200..D201.

    The ladder names the first of each. `data-flow` has read the operand's
    width from the manuals all along, so the two canonical views of one
    operation disagreed: a pair of words in one, a single device in the other.
    """
    from test_gx3_shared_reach import rung

    dmov = rung("DMOV:D:D", "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}")
    reader = rung("MOV:D:D", "d{s=#:a=201:vt=nn}:d{s=#:a=900:vt=nn}")
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/d", dmov), ("_guid/m", reader)])
        db = build_xref(work / "p", work / "x.sqlite")
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            second = occurrences_of(con, "D201", access=("write", "both"))
            past = occurrences_of(con, "D202", access=("write", "both"))
        finally:
            con.close()
        assert [str(r["opcode"]) for r in second] == ["DMOV"], [dict(r) for r in second]
        assert past == [], past


def multiword_project(work: Path, opcode: str) -> Path:
    from test_gx3_shared_reach import rung

    instruction = rung(
        f"{opcode}:D:D",
        "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}",
    )
    write_program(work / "p", [("_guid/op", instruction)])
    return build_xref(work / "p", work / "x.sqlite")


def opcodes_for(db: Path, device: str, access: tuple[str, ...]) -> list[str]:
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = occurrences_of(con, device, access=access)
        return [str(row["opcode"]) for row in rows]
    finally:
        con.close()


def test_dmov_high_source_word_is_a_read_member() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = multiword_project(Path(tmp), "DMOV")
        assert opcodes_for(db, "D101", ("read", "both")) == ["DMOV"]
        assert opcodes_for(db, "D102", ("read", "both")) == []


def test_dmovp_keeps_the_same_two_word_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = multiword_project(Path(tmp), "DMOVP")
        assert opcodes_for(db, "D101", ("read", "both")) == ["DMOVP"]
        assert opcodes_for(db, "D201", ("write", "both")) == ["DMOVP"]
        assert opcodes_for(db, "D202", ("write", "both")) == []


def test_edmov_covers_all_four_destination_words() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = multiword_project(Path(tmp), "EDMOV")
        for device in ("D200", "D201", "D202", "D203"):
            assert opcodes_for(db, device, ("write", "both")) == ["EDMOV"], device
        assert opcodes_for(db, "D204", ("write", "both")) == []


def test_a_block_count_is_not_multiplied_by_the_operand_width() -> None:
    # The count of a block instruction is already in devices. Multiplying it by
    # the operand width would expand the run twice.
    from gx3cli.gx3_arg_decode import parse_row_occurrences
    from test_gx3_shared_reach import rung

    operations, _ = parse_row_occurrences(
        rung("BMOV:D:D:K_1", "d{s=#:a=300:vt=nn}:d{s=#:a=400:vt=nn}:c{s=#:v=4}")
    )
    spans = {
        occ.device: occ.range_len
        for operation in operations
        for occ in operation[2]
        if occ.device.startswith("D")
    }
    assert spans["D400"] == 4, spans
    assert spans["D300"] == 4, spans


def test_a_single_word_operand_is_unchanged() -> None:
    from gx3cli.gx3_arg_decode import parse_row_occurrences
    from test_gx3_shared_reach import rung

    operations, _ = parse_row_occurrences(
        rung("MOV:D:D", "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}")
    )
    spans = {
        occ.device: occ.range_len
        for operation in operations
        for occ in operation[2]
        if occ.device.startswith("D")
    }
    assert spans == {"D100": 1, "D200": 1}, spans


def test_the_decoder_version_moved_so_older_databases_are_rebuilt() -> None:
    # Coverage changed, so a database built by the previous decoder holds fewer
    # members than this build would find. Its input fingerprint is unchanged,
    # which is exactly why the decoder version has to carry the difference.
    from gx3cli.gx3_xref import XREF_DECODER

    assert int(XREF_DECODER.rsplit("-", 1)[1]) >= 4, XREF_DECODER


def main() -> int:
    test_guard_detects_bypasses_and_allows_other_projections()
    test_no_new_reader_looks_a_device_up_by_hand()
    test_the_member_index_holds_every_device_a_row_covers()
    test_asking_about_the_middle_of_a_run_finds_the_instruction()
    test_counts_follow_the_same_rule()
    test_a_database_without_the_index_still_answers()
    test_a_run_of_unknown_length_gets_one_member()
    test_a_multi_word_operand_covers_the_words_it_occupies()
    test_dmov_high_source_word_is_a_read_member()
    test_dmovp_keeps_the_same_two_word_coverage()
    test_edmov_covers_all_four_destination_words()
    test_a_block_count_is_not_multiplied_by_the_operand_width()
    test_a_single_word_operand_is_unchanged()
    test_the_decoder_version_moved_so_older_databases_are_rebuilt()
    print("xref reader boundary checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
