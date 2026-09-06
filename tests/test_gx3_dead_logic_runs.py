from __future__ import annotations

"""dead-logic range handling and project-wide constant propagation regressions."""

import sqlite3
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_dead_logic import (
    propagate_constant_devices,
    read_by_a_run,
    runs_read_by_the_program,
)
from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.review_gx3_project import LadderRow
from gx3cli.gx3_xref_read import counts_for


XREF_SCHEMA = """
create table xref (
    id integer primary key autoincrement,
    device text, device_type text, number integer,
    range_len integer not null default 1,
    access text, role text, opcode text, detail text,
    lddb text, pos integer, pou text, step integer,
    comment text
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
        assert not read_by_a_run(runs, "L", "L12832"), runs
        assert not read_by_a_run(runs, "M", "M12821"), runs
        con.close()


def test_a_run_that_is_only_written_does_not_count_as_read() -> None:
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


def _row(logic: dict, output: str, pos: int, lddb: str) -> LadderRow:
    data, rowsize, _ = generate_rung(logic, {"type": "coil", "device": output})
    return LadderRow(lddb, pos, f"b{pos}", "", 0, rowsize, data, "", [], "exact")


def _insert_xref(
    con: sqlite3.Connection,
    device: str,
    device_type: str,
    number: int,
    access: str,
    role: str,
    lddb: str,
    pos: int,
    pou: str,
    step: int,
) -> None:
    con.execute(
        """
        insert into xref(device, device_type, number, range_len, access, role, opcode,
                         detail, lddb, pos, pou, step, comment)
        values (?, ?, ?, 1, ?, ?, ?, '', ?, ?, ?, ?, '')
        """,
        (device, device_type, number, access, role, role, lddb, pos, pou, step),
    )


def test_constant_state_propagates_across_pous_and_b_contacts() -> None:
    """SM401 -> M100 OFF -> /M100 -> M200 ON -> M200 -> Y0 ON."""
    rows = [
        _row({"device": "SM401"}, "M100", 10, "P1_LDDB.db"),
        _row({"not": {"device": "M100"}}, "M200", 20, "P2_LDDB.db"),
        _row({"device": "M200"}, "Y0", 30, "P3_LDDB.db"),
    ]
    con = sqlite3.connect(":memory:")
    con.executescript(XREF_SCHEMA)
    con.row_factory = sqlite3.Row

    _insert_xref(con, "SM401", "SM", 401, "read", "a", "P1_LDDB.db", 10, "P1", 10)
    _insert_xref(con, "M100", "M", 100, "write", "c", "P1_LDDB.db", 10, "P1", 10)
    _insert_xref(con, "M100", "M", 100, "read", "b", "P2_LDDB.db", 20, "P2", 20)
    _insert_xref(con, "M200", "M", 200, "write", "c", "P2_LDDB.db", 20, "P2", 20)
    _insert_xref(con, "M200", "M", 200, "read", "a", "P3_LDDB.db", 30, "P3", 30)
    _insert_xref(con, "Y0", "Y", 0, "write", "c", "P3_LDDB.db", 30, "P3", 30)
    con.commit()

    facts, findings = propagate_constant_devices(rows, con)
    assert facts["M100"].value is False, facts
    assert facts["M200"].value is True, facts
    assert facts["Y0"].value is True, facts
    assert facts["Y0"].depth >= 3, facts["Y0"]
    assert any(
        item["category"] == "redundant-contact"
        and item["device"] == "M100"
        and item["constant_state"] == "ALWAYS_TRUE"
        for item in findings
    ), findings
    assert any(
        item["category"] == "constant-output"
        and item["device"] == "Y0"
        and item["constant_state"] == "ALWAYS_ON"
        for item in findings
    ), findings
    con.close()


def test_multiple_writers_block_constant_propagation() -> None:
    rows = [
        _row({"device": "SM401"}, "M100", 10, "P1_LDDB.db"),
        _row({"device": "X0"}, "M100", 20, "P2_LDDB.db"),
    ]
    con = sqlite3.connect(":memory:")
    con.executescript(XREF_SCHEMA)
    con.row_factory = sqlite3.Row
    _insert_xref(con, "SM401", "SM", 401, "read", "a", "P1_LDDB.db", 10, "P1", 10)
    _insert_xref(con, "M100", "M", 100, "write", "c", "P1_LDDB.db", 10, "P1", 10)
    _insert_xref(con, "X0", "X", 0, "read", "a", "P2_LDDB.db", 20, "P2", 20)
    _insert_xref(con, "M100", "M", 100, "write", "c", "P2_LDDB.db", 20, "P2", 20)
    con.commit()
    facts, _findings = propagate_constant_devices(rows, con)
    assert "M100" not in facts, facts
    con.close()


def test_multiple_writer_occurrences_on_one_row_still_block_propagation() -> None:
    """Two xref writers sharing (lddb,pos) are still two writers."""
    rows = [_row({"device": "SM401"}, "M100", 10, "P1_LDDB.db")]
    con = sqlite3.connect(":memory:")
    con.executescript(XREF_SCHEMA)
    con.row_factory = sqlite3.Row
    _insert_xref(con, "SM401", "SM", 401, "read", "a", "P1_LDDB.db", 10, "P1", 10)
    _insert_xref(con, "M100", "M", 100, "write", "c", "P1_LDDB.db", 10, "P1", 10)
    _insert_xref(con, "M100", "M", 100, "write", "c", "P1_LDDB.db", 10, "P1", 10)
    con.commit()
    facts, _findings = propagate_constant_devices(rows, con)
    assert "M100" not in facts, facts
    con.close()


def test_both_access_counts_as_read_and_write() -> None:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript(XREF_SCHEMA)
    _insert_xref(con, "M100", "M", 100, "both", "+", "p", 0, "p", 0)
    assert counts_for(con, ["M100"])["M100"] == {"read": 1, "write": 1}
    con.close()


def test_real_cli_range_writer_never_becomes_a_constant() -> None:
    from test_gx3_shared_reach import write_program
    from test_gx3_block_range import operation_row

    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        write_program(root, [
            ("off", generate_rung({"device": "SM401"}, {"type": "coil", "device": "M100"})[0]),
            ("range", operation_row("MOV", "K_1:M:Ks", "c{s=#:v=16}:M{b=d{s=#:a=96:vt=nn}:m=c{s=#:v=4}}")),
            ("use", generate_rung({"device": "M100"}, {"type": "coil", "device": "Y0"})[0]),
            ("outside", generate_rung({"device": "SM401"}, {"type": "coil", "device": "M112"})[0]),
        ])
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONIOENCODING="utf-8")

        def cli(*args: str) -> str:
            result = subprocess.run(
                [sys.executable, "-m", "gx3cli.gx3_cli", *args, "--root", str(root)],
                cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8",
            )
            assert result.returncode == 0, (args, result.stdout, result.stderr)
            return result.stdout

        cli("xref", "build")
        cli("index-lite", "build")
        xref = json.loads(cli("xref", "where-used", "M100", "--format", "json"))
        assert xref["results"][0]["total_counts"]["writers"] == 2, xref
        dead = cli("dead-logic")
        assert "M100=OFF" not in dead and "Y0=OFF" not in dead, dead
        assert "M112=OFF" in dead, dead  # one past the end stays eligible
        trace = json.loads(cli("trace-device", "Y0", "--strict-logic", "--format", "json"))
        assert trace["stats"]["prequeue_pruned_dependency_refs"] == 0, trace


def test_unresolved_or_unindexed_ranges_cannot_prove_ownership() -> None:
    for span, detail, access in [(16, "", "write"), (0, "", "write"), (1, "Z0 indexed", "write"), (1, "", "ref")]:
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(XREF_SCHEMA)
        _insert_xref(con, "M100", "M", 100, "write", "c", "p", 10, "p", 10)
        _insert_xref(con, "M96", "M", 96, access, "MOV", "p", 20, "p", 20)
        con.execute("update xref set range_len=?, detail=? where number=96", (span, detail))
        facts, _ = propagate_constant_devices([_row({"device": "SM401"}, "M100", 10, "p")], con)
        assert "M100" not in facts, (span, detail, access, facts)
        con.close()


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} dead-logic checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
