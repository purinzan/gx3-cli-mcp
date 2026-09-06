from __future__ import annotations

"""dead-logic range handling and project-wide constant propagation regressions."""

import sqlite3
import json
import os
import subprocess
import sys
import tempfile
import shutil
import csv
import io
from contextlib import closing, redirect_stdout
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
-- Unit-only writer graph fixtures explicitly contain no ST sources.
-- Real source coverage is tested via prepare and the STDB fixture below.
create table st_sources (source_file text, source_location text, coverage text, reason text);
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


def test_external_boundary_failures_are_not_empty_evidence() -> None:
    from test_gx3_shared_reach import write_program
    from gx3cli.gx3_workspace import prepare
    from gx3cli.gx3_topology_conditions import load_trace_constant_context
    from gx3cli.gx3_audit import collect_constant_chains
    from gx3cli.gx3_lint import LintContext
    from gx3cli.review_gx3_project import load_comments_for_root, load_rows

    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "project"
        write_program(root, [
            ("_guid/off", generate_rung({"device": "SM401"}, {"type": "coil", "device": "M100"})[0]),
            ("_guid/on", generate_rung({"not": {"device": "M100"}}, {"type": "coil", "device": "Y0"})[0]),
        ])
        built = prepare(root)
        backup = work / "backup.sqlite"
        shutil.copy2(built.index.path, backup)
        comments = load_comments_for_root(root)
        rows = load_rows(root, comments)
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONIOENCODING="utf-8")
        for case in ("valid", "empty", "missing-table", "corrupt", "foreign", "missing"):
            shutil.copy2(backup, built.index.path)
            if case in {"empty", "missing-table", "foreign"}:
                with closing(sqlite3.connect(built.index.path)) as con, con:
                    if case == "empty":
                        con.execute("delete from external_sources")
                    elif case == "missing-table":
                        con.execute("drop table external_sources")
                    else:
                        con.execute("update meta set value='foreign-input' where key='input_sha256'")
            elif case == "corrupt":
                built.index.path.write_bytes(b"not a database")
            elif case == "missing":
                built.index.path.unlink()
            result = subprocess.run([
                sys.executable, "-m", "gx3cli.gx3_dead_logic", "--root", str(root),
                "--db", str(built.xref.path), "--lite-db", str(built.index.path),
                "--output-dir", str(work / "out"), "--prefix", case,
            ], cwd=work, env=env, capture_output=True, text=True, encoding="utf-8")
            assert result.returncode == 0, (case, result.stdout, result.stderr)
            state = json.loads((work / "out" / f"{case}_analysis.json").read_text(encoding="utf-8"))
            expected = case in {"valid", "empty"}
            assert state["boundary_dependent_checks_evaluated"] is expected, (case, state)
            assert state["constant_propagation_evaluated"] is expected, (case, state)
            if case == "empty":
                assert state["external_boundary"]["detail"]["devices"] == 0, state
            if not expected:
                assert state["external_boundary"]["state"] == "not_evaluated", state
                with (work / "out" / f"{case}.csv").open(encoding="utf-8-sig") as handle:
                    findings = list(csv.DictReader(handle))
                assert not any(item["category"] in {"constant-output", "constant-device", "const-off-contact"} for item in findings), findings
            previous = Path.cwd()
            try:
                os.chdir(work)
                context = load_trace_constant_context(root, rows, [])
            finally:
                os.chdir(previous)
            assert context.enabled is expected, (case, context)
            if case == "missing-table":
                with closing(sqlite3.connect(built.xref.path)) as xref, closing(sqlite3.connect(built.index.path)) as lite:
                    xref.row_factory = lite.row_factory = sqlite3.Row
                    ctx = LintContext(root, rows, comments, xref=xref, lite=lite)
                    with redirect_stdout(io.StringIO()):
                        assert collect_constant_chains(ctx, index_db=built.index.path) == []
                    assert not ctx.states["constant-chain"].conclusive


def test_no_writer_is_an_observation_not_a_constant_proof() -> None:
    from test_gx3_shared_reach import write_program
    from test_gx3_block_range import operation_row
    from gx3cli.gx3_workspace import prepare

    repo = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "project"
        write_program(root, [
            ("range", operation_row("MOV", "K_1:M:Ks", "c{s=#:v=16}:M{b=d{s=#:a=96:vt=nn}:m=c{s=#:v=4}}")),
            *[(f"contact-{device}-{role}", generate_rung(
                {"device": device} if role == "a" else {"not": {"device": device}},
                {"type": "coil", "device": "Y0"})[0])
              for device in ("M95", "M96", "M100", "M111", "M112", "L100")
              for role in ("a", "b")],
        ])
        built = prepare(root)
        # Exercise the accepted empty-classification boundary. Instruction/xref
        # facts still come from the real decoder, not handwritten occurrences.
        with closing(sqlite3.connect(built.index.path)) as lite, lite:
            lite.execute("delete from external_sources")
        result = subprocess.run([
            sys.executable, "-m", "gx3cli.gx3_dead_logic", "--root", str(root),
            "--db", str(built.xref.path), "--lite-db", str(built.index.path),
            "--output-dir", str(work / "out"), "--prefix", "contacts",
        ], cwd=work, env=dict(os.environ, PYTHONPATH=str(repo)),
            capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, (result.stdout, result.stderr)
        with (work / "out" / "contacts.csv").open(encoding="utf-8-sig") as handle:
            findings = list(csv.DictReader(handle))
        assert not any(f["category"] in {"const-off-contact", "always-on-contact"} for f in findings), findings
        observations = [f for f in findings if f["category"] == "unwritten-contact"]
        assert {(f["device"], f["contact_role"]) for f in observations} == {
            (device, role) for device in ("M95", "M112", "L100") for role in ("a", "b")
        }, observations
        assert all(f["analysis_state"] == "partial" and not f["constant_state"] for f in observations)
        state = json.loads((work / "out" / "contacts_analysis.json").read_text(encoding="utf-8"))
        assert state["unwritten_contact_analysis"]["stage"] == "semantics", state


def test_partial_st_cannot_hide_a_writer_from_constant_propagation() -> None:
    from test_gx3_shared_reach import write_program
    from gx3cli.gx3_workspace import prepare
    from gx3cli.gx3_topology_conditions import load_trace_constant_context
    from gx3cli.review_gx3_project import load_rows, load_comments_for_root

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        write_program(root, [
            ("off", generate_rung({"device": "SM401"}, {"type": "coil", "device": "M100"})[0]),
            ("use", generate_rung({"device": "M100"}, {"type": "coil", "device": "Y0"})[0]),
        ])
        with closing(sqlite3.connect(root / "002_STDB.db")) as st, st:
            st.execute("create table Source(Pou text, Code text)")
            st.execute("insert into Source values ('STWriter', 'IF X0 THEN M100 := TRUE; END_IF;')")
        built = prepare(root)
        rows = load_rows(root, load_comments_for_root(root))
        with closing(sqlite3.connect(built.xref.path)) as con:
            con.row_factory = sqlite3.Row
            assert con.execute("select coverage from st_sources").fetchone()[0] == "partial"
            # The ST bridge deliberately does not guess this writer. Therefore
            # one indexed writer does not prove exclusive physical ownership.
            assert counts_for(con, ["M100"])["M100"]["write"] == 1
        previous = Path.cwd()
        try:
            os.chdir(tmp)
            context = load_trace_constant_context(root, rows, [])
        finally:
            os.chdir(previous)
        assert not context.enabled and "ST" in context.reason, context
        assert "M100" not in context.facts and "Y0" not in context.facts, context
        assert context.summary()["analysis"]["stage"] == "decode"

        repo = Path(__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(repo), PYTHONIOENCODING="utf-8")
        result = subprocess.run([
            sys.executable, "-m", "gx3cli.gx3_cli", "trace-device", "Y0",
            "--root", str(root), "--strict-logic", "--format", "json",
        ], cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, (result.stdout, result.stderr)
        trace = json.loads(result.stdout)
        assert trace["constant_pruning"]["enabled"] is False, trace
        assert trace["constant_pruning"]["analysis"]["stage"] == "decode", trace
        assert trace["stats"].get("prequeue_pruned_dependency_refs", 0) == 0, trace
        assert any(device["device"] == "M100" for device in trace["devices"]), trace

        result = subprocess.run([
            sys.executable, "-m", "gx3cli.gx3_dead_logic", "--root", str(root),
            "--db", str(built.xref.path), "--lite-db", str(built.index.path),
            "--output-dir", str(Path(tmp) / "out"), "--prefix", "st",
        ], cwd=tmp, env=env, capture_output=True, text=True, encoding="utf-8")
        assert result.returncode == 0, (result.stdout, result.stderr)
        report = json.loads((Path(tmp) / "out/st_analysis.json").read_text(encoding="utf-8"))
        assert report["constant_propagation_evaluated"] is False, report
        assert report["constant_propagation_analysis"]["stage"] == "decode", report

        from gx3cli.gx3_audit import collect_constant_chains
        from gx3cli.gx3_lint import LintContext
        with closing(sqlite3.connect(built.xref.path)) as xref, closing(sqlite3.connect(built.index.path)) as lite:
            xref.row_factory = lite.row_factory = sqlite3.Row
            ctx = LintContext(root, rows, {}, xref=xref, lite=lite)
            assert collect_constant_chains(ctx, index_db=built.index.path) == []
            assert ctx.states["constant-chain"].stage == "decode", ctx.states


def test_supported_st_preserves_known_writer_ownership() -> None:
    from test_gx3_shared_reach import write_program
    from gx3cli.gx3_workspace import prepare
    from gx3cli.review_gx3_project import load_rows, load_comments_for_root

    for text, has_constant in (("D900 := D901;", True), ("M100 := TRUE;", False)):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            write_program(root, [("off", generate_rung(
                {"device": "SM401"}, {"type": "coil", "device": "M100"})[0])])
            with closing(sqlite3.connect(root / "002_STDB.db")) as st, st:
                st.execute("create table Source(Pou text, Code text)")
                st.execute("insert into Source values ('STWriter', ?)", (text,))
            built = prepare(root)
            rows = load_rows(root, load_comments_for_root(root))
            with closing(sqlite3.connect(built.xref.path)) as con:
                con.row_factory = sqlite3.Row
                facts, _ = propagate_constant_devices(rows, con)
            assert ("M100" in facts) is has_constant, (text, facts)


def test_missing_st_coverage_is_not_proof_of_no_st_writers() -> None:
    from gx3cli.gx3_dead_logic import ConstantProofUnavailable

    with closing(sqlite3.connect(":memory:")) as con:
        con.row_factory = sqlite3.Row
        con.executescript(XREF_SCHEMA)
        con.execute("drop table st_sources")
        try:
            propagate_constant_devices([], con)
        except ConstantProofUnavailable as exc:
            assert exc.analysis.state == "not_evaluated", exc.analysis
        else:
            raise AssertionError("missing source coverage was accepted as empty")


def test_fbd_source_prevents_project_wide_constant_proof() -> None:
    from test_gx3_shared_reach import write_program
    from gx3cli.gx3_workspace import prepare
    from gx3cli.gx3_topology_conditions import load_trace_constant_context
    from gx3cli.review_gx3_project import load_rows, load_comments_for_root

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        write_program(root, [("off", generate_rung(
            {"device": "SM401"}, {"type": "coil", "device": "M100"})[0])])
        # The FBD decoder is not available. Do not pretend that an unread
        # program container proves absence of competing writers.
        with closing(sqlite3.connect(root / "002_FBDDB.db")) as fbd, fbd:
            fbd.execute("create table SyntheticProgram (body text)")
            fbd.execute("insert into SyntheticProgram values ('uninterpreted program')")
        prepare(root)
        rows = load_rows(root, load_comments_for_root(root))
        previous = Path.cwd()
        try:
            os.chdir(tmp)
            context = load_trace_constant_context(root, rows, [])
        finally:
            os.chdir(previous)
        assert not context.enabled and "FBD" in context.reason, context
        assert "M100" not in context.facts, context


def test_source_gaps_preserve_all_reasons_and_block_other_exact_rungs() -> None:
    from test_gx3_shared_reach import write_program
    from gx3cli.gx3_workspace import prepare
    from gx3cli.gx3_dead_logic import ConstantProofUnavailable
    from gx3cli.review_gx3_project import load_rows, load_comments_for_root

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        bad_row = generate_rung({"device": "X0"}, {"type": "coil", "device": "M200"})[0]
        assert bad_row.endswith("]}}")
        bad_row = bad_row[:-3] + ":e{s=ce{op=uninterpreted}:pos=4,0}]}}"
        write_program(root, [
            ("off", generate_rung({"device": "SM401"}, {"type": "coil", "device": "M100"})[0]),
            ("unknown", bad_row),
        ])
        with closing(sqlite3.connect(root / "002_STDB.db")) as st, st:
            st.execute("create table Source(Code text)")
            st.execute("insert into Source values ('IF X0 THEN M100 := TRUE; END_IF;')")
        with closing(sqlite3.connect(root / "003_FBDDB.db")) as fbd, fbd:
            fbd.execute("create table SyntheticProgram(body text)")
        built = prepare(root)
        rows = load_rows(root, load_comments_for_root(root))
        assert rows[0].parse_status == "exact" and rows[1].parse_status == "partial", rows
        with closing(sqlite3.connect(built.xref.path)) as con:
            con.row_factory = sqlite3.Row
            try:
                propagate_constant_devices(rows, con, root=root)
            except ConstantProofUnavailable as exc:
                state = json.loads(json.dumps(exc.analysis.as_dict()))
                assert len(state["constraints"]) == 3, state
                reasons = " ".join(item["reason"] for item in state["constraints"])
                assert all(kind in reasons for kind in ("LD", "ST", "FBD")), state
            else:
                raise AssertionError("an exact candidate hid gaps elsewhere in the project")


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
