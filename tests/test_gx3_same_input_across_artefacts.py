from __future__ import annotations

"""Can you tell that the logic, the comments and the comm settings agree?

That is the question #49 asks the evidence to answer, and until now the answer
was no. Three artefacts get built from one project -- a cross-reference, a
survey package, a set of communication CSVs -- and get read side by side. Any
one of them could have been made from a different project, or from the same
project before an edit, and nothing in them said so.

They now carry the same fingerprint, so agreement is checkable rather than
assumed.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import shutil
import io
from contextlib import closing, redirect_stdout, redirect_stderr
from unittest.mock import patch
from pathlib import Path

from gx3cli.gx3_input_identity import fingerprint
from gx3cli.gx3_synthetic_project import create_demo_line_project


ROOT = Path(__file__).resolve().parents[1]


def env_for_repo() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def run(module: str, args: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd, env=env_for_repo(), text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    assert completed.returncode == 0, f"{module}: {completed.stdout}"
    return completed.stdout


def test_three_artefacts_of_one_project_agree_on_the_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        expected = fingerprint(project)
        assert expected

        run("gx3cli.gx3_xref", ["--root", str(project), "--db", str(work / "x.sqlite"), "build"], work)
        run("gx3cli.gx3_project_survey",
            ["--root", str(project), "--output-dir", str(work / "sv"), "--prefix", "sv"], work)
        run("gx3cli.extract_comm_refresh_areas",
            ["--root", str(project), "--output-dir", str(work / "cm"), "--prefix", "cm"], work)

        con = sqlite3.connect(work / "x.sqlite")
        xref_input = dict(con.execute("select key, value from meta")).get("input_sha256")
        con.close()
        survey = json.loads((work / "sv" / "sv_manifest.json").read_text(encoding="utf-8"))
        comm = json.loads((work / "cm" / "cm_manifest.json").read_text(encoding="utf-8"))

        stamps = {
            "xref": xref_input,
            "survey": survey["input_sha256"],
            "comm": comm["input_sha256"],
        }
        assert set(stamps.values()) == {expected}, stamps


def test_an_artefact_from_a_changed_project_no_longer_agrees() -> None:
    # The point of the fingerprint: an edit between two builds is visible,
    # rather than two reports about different projects being read together.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        run("gx3cli.gx3_xref", ["--root", str(project), "--db", str(work / "x.sqlite"), "build"], work)

        comment_db = next(project.glob("*_DC.db"))
        con = sqlite3.connect(comment_db)
        con.execute("update COMMENT_DATA set CmtData = CmtData || ' edited'")
        con.commit()
        con.close()

        run("gx3cli.extract_comm_refresh_areas",
            ["--root", str(project), "--output-dir", str(work / "cm"), "--prefix", "cm"], work)

        con = sqlite3.connect(work / "x.sqlite")
        xref_input = dict(con.execute("select key, value from meta")).get("input_sha256")
        con.close()
        comm = json.loads((work / "cm" / "cm_manifest.json").read_text(encoding="utf-8"))
        assert xref_input != comm["input_sha256"], "an edit between builds went unnoticed"


def test_scan_order_rejects_a_foreign_xref_before_syncing_it() -> None:
    """A failed identity check must leave the foreign database untouched.

    scan-order used to write Project A's POU order into Project B's xref and
    only then call the fingerprint validator. The command failed, but the
    unrelated database had already been changed.
    """
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project_a = create_demo_line_project(work / "a", overwrite=True)
        project_b = create_demo_line_project(work / "b", overwrite=True)

        # Make A a genuinely different input while preserving a valid project.
        comment_db = next(project_a.glob("*_DC.db"))
        con = sqlite3.connect(comment_db)
        con.execute("update COMMENT_DATA set CmtData = CmtData || ' project-a'")
        con.commit()
        con.close()

        db = work / "b_xref.sqlite"
        run("gx3cli.gx3_xref", ["--root", str(project_b), "--db", str(db), "build"], work)

        con = sqlite3.connect(db)
        con.execute(
            "insert or replace into meta(key, value) values ('pou_order_rows', 'foreign-sentinel')"
        )
        con.commit()
        con.close()

        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "gx3cli.gx3_scan_order",
                "M100",
                "--root",
                str(project_a),
                "--db",
                str(db),
            ],
            cwd=work,
            env=env_for_repo(),
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert completed.returncode != 0, completed.stdout
        assert "xref db was built from a different input" in completed.stdout, completed.stdout

        con = sqlite3.connect(db)
        after = dict(con.execute("select key, value from meta")).get("pou_order_rows")
        con.close()
        assert after == "foreign-sentinel", "scan-order mutated the foreign xref before rejecting it"


def test_lint_and_health_reject_foreign_lite_and_close_open_xref() -> None:
    from gx3cli import gx3_lint, gx3_audit
    from gx3cli.gx3_workspace import prepare
    from gx3cli.gx3_cli import project_label_from_root
    from gx3cli.gx3_input_identity import file_digest

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        one = create_demo_line_project(work / "one", overwrite=True)
        other = create_demo_line_project(work / "other", overwrite=True)
        with closing(sqlite3.connect(next(other.glob("*_DC.db")))) as con, con:
            con.execute("update COMMENT_DATA set CmtData = CmtData || ' other project'")
        first, second = prepare(one), prepare(other)
        mixed = work / "mixed"
        mixed.mkdir()
        label = project_label_from_root(one)
        xref = mixed / f"{label}_xref.sqlite"
        lite = mixed / f"{label}.sqlite"
        shutil.copy2(first.xref.path, xref)
        shutil.copy2(second.index.path, lite)
        before = file_digest(lite)
        for module in (gx3_lint, gx3_audit):
            opened = []
            real_open = module.open_checked_xref

            def track(*args, **kwargs):
                con = real_open(*args, **kwargs)
                opened.append(con)
                return con

            with patch.object(module, "open_checked_xref", side_effect=track), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                try:
                    if module is gx3_lint:
                        gx3_lint.main([str(one), "--xref-db", str(xref), "--index-db", str(lite),
                                       "--checks", "unused-device", "--out-prefix", str(work / "lint")])
                    else:
                        gx3_audit.collect_project_health(one, index_dir=mixed, link_db=work / "absent.sqlite")
                except SystemExit as exc:
                    assert "index db was built from a different input" in str(exc), exc
                else:
                    raise AssertionError(f"{module.__name__} accepted foreign lite")
            assert len(opened) == 1 and opened[0] is not None
            try:
                opened[0].execute("select 1")
            except sqlite3.ProgrammingError:
                pass
            else:
                raise AssertionError("xref remained open after lite rejection")
        assert file_digest(lite) == before, "read-only validation changed foreign evidence"
        assert not (work / "lint_summary.json").exists()
        shutil.copy2(first.index.path, lite)
        for expected in ("checked", "not_evaluated"):
            if expected == "not_evaluated":
                lite.unlink()
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                assert gx3_lint.main([str(one), "--xref-db", str(xref), "--index-db", str(lite),
                                      "--checks", "unused-device", "--format", "json",
                                      "--out-prefix", str(work / "lint")]) == 0
            report = json.loads(out.getvalue())
            assert report["checks"]["unused-device"]["state"] == expected, report
        moved = xref.with_suffix(".moved")
        xref.rename(moved)
        moved.rename(xref)


def test_builds_preserve_existing_index_when_inputs_change_or_population_fails() -> None:
    from test_gx3_shared_reach import write_program
    from gx3cli.gx3_intermediate_tool import generate_rung
    from gx3cli import gx3_xref as xref, gx3_index_lite as lite

    for module, method, population in ((xref, xref.build, "_populate_xref"),
                                       (lite, lite.build_index, "_populate_index")):
        for failure in ("LDDB", "STDB", "before-stamp", "exception"):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "project"
                output = Path(tmp) / "index.sqlite"
                write_program(root, [("_guid/off", generate_rung(
                    {"device": "SM401"}, {"type": "coil", "device": "M100"})[0])])
                with closing(sqlite3.connect(root / "002_STDB.db")) as st, st:
                    st.execute("create table Source(Code text)")
                    st.execute("insert into Source values ('D100 := D101;')")
                argv = (["--root", str(root), "--db", str(output), "build"] if module is xref else
                        ["build", "--root", str(root), "--out", str(output), "--comm-dir", tmp])
                args = module.build_parser().parse_args(argv)
                with redirect_stdout(io.StringIO()):
                    method(args)
                before = output.read_bytes()
                original = getattr(module, population)
                handles = []

                def mutate_after_population(call_args, con):
                    handles.append(con)
                    if failure == "before-stamp":
                        loader_name = "read_ladder_rows" if module is xref else "load_rows"
                        loader = getattr(module, loader_name)

                        def change_after_load(*load_args, **load_kwargs):
                            loaded = loader(*load_args, **load_kwargs)
                            with closing(sqlite3.connect(root / "001_LDDB.db")) as source, source:
                                source.execute("update LadderBlocks set data=replace(data, 'a=100', 'a=101')")
                            return loaded

                        with patch.object(module, loader_name, side_effect=change_after_load):
                            result = original(call_args, con)
                        # This mixed artifact's stored fingerprint would pass
                        # the former reader validation without the pre-check.
                        assert con.execute("select value from meta where key='input_sha256'").fetchone()[0] == fingerprint(root)
                        return result
                    result = original(call_args, con)
                    if failure == "exception":
                        raise RuntimeError("injected population failure")
                    path = root / ("001_LDDB.db" if failure == "LDDB" else "002_STDB.db")
                    with closing(sqlite3.connect(path)) as source, source:
                        source.execute("update LadderBlocks set data=replace(data, 'a=100', 'a=101')" if failure == "LDDB" else
                                       "update Source set Code='D100 := D102;'")
                    return result

                with patch.object(module, population, side_effect=mutate_after_population), redirect_stdout(io.StringIO()):
                    try:
                        method(args)
                    except (RuntimeError, SystemExit) as exc:
                        assert "failure" in str(exc) or "changed" in str(exc), exc
                    else:
                        raise AssertionError("mixed input was published")
                assert output.read_bytes() == before
                assert not list(output.parent.glob("*.building"))
                try:
                    handles[0].execute("select 1")
                except sqlite3.ProgrammingError:
                    pass
                else:
                    raise AssertionError("staged build handle leaked")
                with redirect_stdout(io.StringIO()):
                    method(args)
                with closing(sqlite3.connect(output)) as con:
                    assert con.execute("select value from meta where key='input_sha256'").fetchone()[0] == fingerprint(root)


def test_atomic_build_keeps_new_failures_absent_and_respects_active_wal() -> None:
    from gx3cli.gx3_index_build import atomic_index_build
    from test_gx3_shared_reach import write_program

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        write_program(root, [])
        output = Path(tmp) / "index.sqlite"
        try:
            with atomic_index_build(root, output):
                raise RuntimeError("new build fails")
        except RuntimeError:
            pass
        assert not output.exists()

        def populate(con):
            con.execute("create table meta(key text, value text)")
            con.execute("insert into meta values ('input_sha256', ?)", (fingerprint(root),))

        with atomic_index_build(root, output) as con:
            populate(con)
        with closing(sqlite3.connect(output)) as reader:
            reader.execute("pragma journal_mode=WAL")
            reader.execute("select * from meta").fetchall()
            before = output.read_bytes()
            try:
                with atomic_index_build(root, output) as con:
                    populate(con)
            except SystemExit as exc:
                assert "WAL" in str(exc), exc
            else:
                raise AssertionError("active WAL artifact replaced")
            assert output.read_bytes() == before
        assert not list(Path(tmp).glob("*.building"))
        source = root / "001_LDDB.db"
        source_before = source.read_bytes()
        try:
            with atomic_index_build(root, source):
                raise AssertionError("source file was accepted as output")
        except SystemExit as exc:
            assert "overlaps" in str(exc), exc
        assert source.read_bytes() == source_before
        with closing(sqlite3.connect(source)) as writer:
            writer.execute("pragma journal_mode=WAL")
            writer.execute("select * from LadderBlocks").fetchall()
            try:
                with atomic_index_build(root, output):
                    raise AssertionError("active source WAL was omitted from the fingerprint")
            except SystemExit as exc:
                assert "project input" in str(exc) and "WAL" in str(exc), exc


def main() -> int:
    test_lint_and_health_reject_foreign_lite_and_close_open_xref()
    test_three_artefacts_of_one_project_agree_on_the_input()
    test_an_artefact_from_a_changed_project_no_longer_agrees()
    test_scan_order_rejects_a_foreign_xref_before_syncing_it()
    test_builds_preserve_existing_index_when_inputs_change_or_population_fails()
    test_atomic_build_keeps_new_failures_absent_and_respects_active_wal()
    print("same input across artefacts checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
