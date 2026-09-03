from __future__ import annotations

"""The remaining ways an answer came back empty or misdirected.

Each of these was measured on a real project before it was fixed:

- A digit specification covers four bits per digit. K2M3410 is M3410 through
  M3417, and only M3410 was recorded, so a search for M3413 answered "no
  occurrences" -- the same wrong answer a block instruction used to give.
- lint's unused-device check reads the lite index, which did not carry the
  runs, so 269 of its findings were devices a block instruction or a digit
  specification writes without naming.
- A path or setup failure was reported as an unsupported GX Works3 format,
  pointing the user at the parser-gap issue form for a directory that did not
  exist.
- Every project has a MilDB beside each ladder program, so counting it as a
  non-ladder program told every project it contained a format the tool cannot
  read.
"""

import sqlite3
import tempfile
from pathlib import Path

from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_cli import format_subprocess_failure
from gx3cli.gx3_format import build_format_inventory
from gx3cli.gx3_lint import covered_device_ranges, is_covered
from gx3cli.gx3_synthetic_project import create_demo_line_project
from gx3cli.gx3_xref import rows_for_device, stamp_decoder


# SM400 driving FROM with a digit-specified destination K4M49000.
DIGIT_ROW = (
    "V1:8:1:2:4:1:3:1:2:3:a:SM:FROM:U:K_1:M:Ks:K_1:cb{fg=fg{dim=6x1:es=["
    "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=400:vt=nn}]}:pos=0,0}:"
    "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
    "d{s=#:a=1:vt=nn}:c{s=#:v=0}:M{b=d{s=#:a=49000:vt=nn}:m=c{s=#:v=4}}:c{s=#:v=1}]}:pos=1,0}]}}"
)

XREF_SCHEMA = """
create table xref (
    id integer primary key autoincrement,
    device text, device_type text, number integer,
    range_len integer not null default 1,
    access text, role text, opcode text, arg_index integer,
    const_args text, detail text, access_basis text,
    lddb text, pos integer, pou text, step integer,
    title text, comment text, parse_status text
)
"""


def test_a_digit_specification_covers_four_bits_per_digit() -> None:
    operations, status = parse_row_occurrences(DIGIT_ROW)
    assert status == "exact", status
    occs = [occ for _r, _o, args, _c in operations for occ in args]
    target = [occ for occ in occs if occ.device == "M49000"]
    assert target, occs
    assert target[0].range_len == 16, (target[0].device, target[0].range_len, target[0].detail)


def test_a_search_finds_a_bit_inside_a_digit_specification() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "xref.sqlite"
        con = sqlite3.connect(path)
        con.executescript(XREF_SCHEMA)
        stamp_decoder(con)
        con.execute(
            "insert into xref(device, device_type, number, range_len, access, role, opcode,"
            " lddb, pos, pou) values ('M49000','M',49000,16,'write','FROM','FROM','a.db',1,'P1')"
        )
        con.commit()
        con.row_factory = sqlite3.Row

        for number in (49000, 49007, 49015):
            assert rows_for_device(con, f"M{number}", 10), f"M{number} was not found"
        assert rows_for_device(con, "M49016", 10) == [], "the digit span reached too far"
        con.close()


def test_the_lint_check_can_see_the_runs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "lite.sqlite"
        con = sqlite3.connect(path)
        con.execute(
            "create table covered_ranges (device_type text, start integer, length integer,"
            " access text, opcode text, lddb text, pos integer)"
        )
        con.execute("insert into covered_ranges values ('D', 28000, 2000, 'write', 'FMOVP', 'a.db', 1)")
        con.commit()
        covered = covered_device_ranges(con)
        assert is_covered(covered, "D", 28010), covered
        assert not is_covered(covered, "D", 30000), covered
        assert not is_covered(covered, "M", 28010), covered
        con.close()

    # An index built before the runs were recorded still answers, rather than
    # failing the whole check.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "old.sqlite"
        con = sqlite3.connect(path)
        con.execute("create table devices (device text)")
        con.commit()
        assert covered_device_ranges(con) == {}
        con.close()


def test_a_path_failure_is_not_reported_as_a_parser_gap() -> None:
    setup = format_subprocess_failure("FileNotFoundError: outputs/lint_duplicate-coil.csv")
    assert "path or setup problem" in setup, setup
    assert "parser-gap" not in setup, setup
    assert "doctor" in setup, setup

    missing_db = format_subprocess_failure("sqlite3.OperationalError: no such table: DEVICE_DATA")
    assert "path or setup problem" in missing_db, missing_db

    # A real parser failure keeps the report path.
    parser = format_subprocess_failure("ValueError: unknown element shape in cb{...}")
    assert "parser coverage gap" in parser, parser
    assert "parser-gap" in parser, parser


def test_a_ladder_only_project_is_not_called_unsupported() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        inventory = build_format_inventory(root)
        assert inventory.has_ladder
        # A MilDB sits beside each ladder program; it is not another language.
        assert not inventory.has_non_ladder_programs, inventory.as_dict()
        assert "MilDB" not in inventory.unsupported_program_detail()


def main() -> int:
    test_a_digit_specification_covers_four_bits_per_digit()
    test_a_search_finds_a_bit_inside_a_digit_specification()
    test_the_lint_check_can_see_the_runs()
    test_a_path_failure_is_not_reported_as_a_parser_gap()
    test_a_ladder_only_project_is_not_called_unsupported()
    print("coverage gap checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
