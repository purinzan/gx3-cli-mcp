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
import csv
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
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
KNOWN_UNMIGRATED_QUERIES = {}

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


def test_scan_order_and_xref_share_physical_writers_end_to_end() -> None:
    from gx3cli.gx3_scan_order import load_rows_for_device, load_all_device_rows, writers, readers
    from gx3cli.gx3_xref import open_xref_db
    from gx3cli.gx3_timing_chart import device_reader_condition, RowIndex
    from test_gx3_shared_reach import rung

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = work / "p", work / "xref.sqlite"
        write_program(root, [
            ("block", bmov(300, 400, 4)), ("single", mov(500, 401)),
            ("read", mov(401, 900)),
            ("both", rung("D+:D:D", "d{s=#:a=500:vt=nn}:d{s=#:a=600:vt=nn}")),
        ])
        build_xref(root, db)
        # Hand-specified expectations, not golden values copied from another consumer.
        expected = {"D400": (0, 1), "D401": (1, 2), "D403": (0, 1),
                    "D404": (0, 0), "D600": (1, 1), "D601": (1, 1), "D602": (0, 0)}
        con = open_xref_db(db, root=root)
        try:
            totals = counts_for(con, expected)
            all_rows = load_all_device_rows(con)
            condition = device_reader_condition(con, "D601", RowIndex(root))
            assert condition == "[M1]", ("timing consumer lost the both-access high-word reader", condition)
            for device, (read_count, write_count) in expected.items():
                one = load_rows_for_device(con, device)
                all_device = all_rows.get(device, [])
                assert {row.row_id for row in one} == {row.row_id for row in all_device}
                assert (len(readers(one)), len(writers(one))) == (read_count, write_count)
                assert totals[device] == {"read": read_count, "write": write_count}
        finally:
            con.close()
        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8")
        for device, (read_count, write_count) in expected.items():
            scan = subprocess.run(
                [sys.executable, "-m", "gx3cli.gx3_scan_order", device, "--root", str(root),
                 "--db", str(db), "--no-sync-db"], cwd=tmp, env=env,
                capture_output=True, text=True, encoding="utf-8", timeout=20)
            xref = subprocess.run(
                [sys.executable, "-m", "gx3cli.gx3_xref", "--root", str(root), "--db", str(db),
                 "where-used", device, "--json", "--limit", "-1"], cwd=tmp, env=env,
                capture_output=True, text=True, encoding="utf-8", timeout=20)
            code = 0 if read_count + write_count else 1
            assert scan.returncode == xref.returncode == code, (scan.stdout, scan.stderr, xref.stdout, xref.stderr)
            result = json.loads(xref.stdout)["results"][0]
            assert result["total_counts"]["readers"] == read_count, result
            assert result["total_counts"]["writers"] == write_count, result
            if code == 0:
                assert f"writers={write_count} readers={read_count}" in scan.stdout, scan.stdout
        alarm = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_alarm_map", "--root", str(root), "--db", str(db), "show", "D601"],
            cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)
        assert alarm.returncode == 0 and "[M1]" in alarm.stdout, (alarm.stdout, alarm.stderr)
        from gx3cli.gx3_ladder_report import build as build_report, render_html
        report = build_report(root, db, "001_LDDB.db")
        assert report.devices["D401"]["project"] == {"read": 1, "write": 2}
        assert "D401" in render_html(report)
        # A legacy/missing member table must not silently fall back at the CLI.
        with closing(sqlite3.connect(db)) as corrupt, corrupt:
            corrupt.execute("drop table xref_members")
        before = db.read_bytes()
        rejected = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_scan_order", "D401", "--root", str(root),
             "--db", str(db), "--no-sync-db"], cwd=tmp, env=env,
            capture_output=True, text=True, encoding="utf-8", timeout=20)
        assert rejected.returncode != 0 and "xref_members" in rejected.stderr, rejected
        assert "writers=0" not in rejected.stdout and db.read_bytes() == before


def test_timing_reports_preserve_covered_both_reader() -> None:
    from test_gx3_shared_reach import rung
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        roots = [work / "a", work / "b"]
        databases = [work / "a.sqlite", work / "b.sqlite"]
        instructions = [rung("DMOV:D:D", "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}"),
                        rung("D+:D:D", "d{s=#:a=500:vt=nn}:d{s=#:a=600:vt=nn}")]
        for root, db, instruction in zip(roots, databases, instructions):
            write_program(root, [("operation", instruction)])
            build_xref(root, db)
        links = work / "links.sqlite"
        # Explicit link metadata is input; both xrefs come from real decoders.
        with closing(sqlite3.connect(links)) as con, con:
            con.execute("create table project(label, root, xref_db)")
            con.executemany("insert into project values (?,?,?)", [(label, str(root), str(db)) for label, root, db in zip(("A", "B"), roots, databases)])
            con.execute("create table link_map(project_a,device_a,project_b,device_b,link_type,link_addr,direction,confidence,role,evidence)")
            con.execute("insert into link_map values ('A','D201','B','D601','comment-role','','A_to_B','high','request','synthetic mapping')")
        for format_name in ("csv", "markdown"):
            result = subprocess.run(
                [sys.executable, "-m", "gx3cli.gx3_timing_chart", "detect", "A", "B", "--link-db", str(links), "--format", format_name],
                cwd=tmp, env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONIOENCODING="utf-8"),
                capture_output=True, text=True, encoding="utf-8", timeout=20)
            assert result.returncode == 0, (result.stdout, result.stderr)
            if format_name == "csv":
                signal, = list(csv.DictReader(io.StringIO(result.stdout)))
                assert signal["sender"] == "A:D201" and signal["receiver"] == "B:D601", signal
                assert signal["condition"] == signal["receiver_condition"] == "[M1]", signal
            else:
                assert "Receiver Read-Site Condition" in result.stdout and "[M1]" in result.stdout


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
    from gx3cli.review_gx3_project import load_rows
    from gx3cli.gx3_ladder_logic import enable_logic_for_device, logic_to_text

    instruction = rung(
        f"{opcode}:D:D",
        "d{s=#:a=100:vt=nn}:d{s=#:a=200:vt=nn}",
    )
    write_program(work / "p", [("_guid/op", instruction)])
    row = load_rows(work / "p", {})[0]
    width = 4 if opcode == "EDMOV" else 2
    for number in range(200, 200 + width):
        assert logic_to_text(enable_logic_for_device(row, f"D{number}")) == "[M1]"
    assert logic_to_text(enable_logic_for_device(row, f"D{200 + width}")) == "FALSE"
    assert logic_to_text(enable_logic_for_device(row, "D101")) == "FALSE", "a wide read is not an output"
    return build_xref(work / "p", work / "x.sqlite")


def test_merging_operand_names_does_not_promote_read_width_to_write_width() -> None:
    # Supplementary adapter unit contract; real decoder/DB/consumer above.
    from gx3cli.gx3_arg_decode import ArgOcc
    from gx3cli.gx3_ladder_logic import device_refs_from_args
    read = ArgOcc("D100", "D", 100, "read", 0, range_len=4)
    write = ArgOcc("D100", "D", 100, "write", 1, range_len=1)
    for args in ([read, write], [write, read]):
        ref, = device_refs_from_args(args)
        assert ref.access == "both" and ref.write_range_len == 1, ref
    for uncertain in (ArgOcc("D100", "D", 100, "write", 0, range_len=0),
                      ArgOcc("D100", "D", 100, "write", 0, detail="indexed Z0", range_len=4)):
        ref, = device_refs_from_args([uncertain])
        assert ref.write_range_len == 0, ref
        known = ArgOcc("D100", "D", 100, "write", 1, range_len=2)
        ref, = device_refs_from_args([uncertain, known])
        assert ref.write_range_len == 2, ref


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
    test_timing_reports_preserve_covered_both_reader()
    test_merging_operand_names_does_not_promote_read_width_to_write_width()
    test_scan_order_and_xref_share_physical_writers_end_to_end()
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
