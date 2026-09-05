from __future__ import annotations

"""The index a command reads is the one built from the project it was asked about.

Two things went wrong before this existed. A lint run in a temporary directory
found half the findings it had found the day before, because the index it
looked for was spelled relative to the working directory and the one that
existed was somewhere else -- an empty index reports nothing found, which reads
as a clean project. And an index left over from a previous version, or from an
earlier save of the same project, was "built" as far as the guide was
concerned.

So: found wherever it already is, judged by what it was built from rather than
by whether the file exists, and rebuilt only when the answer would differ.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_input_identity import fingerprint
from gx3cli.gx3_synthetic_project import create_demo_line_project
from gx3cli.gx3_workspace import MISSING, OLD_BUILD, OTHER_INPUT, READY, locate, prepare


ROOT = Path(__file__).resolve().parents[1]


def edit(project: Path) -> None:
    """Change the project the way saving an edited comment would."""
    con = sqlite3.connect(next(project.glob("*_DC.db")))
    con.execute("update COMMENT_DATA set CmtData = CmtData || ' edited'")
    con.commit()
    con.close()


def test_nothing_built_yet_says_so() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        workspace = locate(project)
        assert workspace.index.state == MISSING, workspace.index
        assert workspace.xref.state == MISSING, workspace.xref
        assert not workspace.ready


def test_preparing_builds_both_and_records_the_input() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        workspace = prepare(project)
        assert workspace.ready, workspace.as_dict()
        assert workspace.built == ["index", "xref"], workspace.built
        assert workspace.input_sha256 == fingerprint(project)
        assert workspace.index.path.exists() and workspace.xref.path.exists()


def test_a_second_run_on_the_same_input_rebuilds_nothing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        first = prepare(project)
        stamps = (first.index.path.stat().st_mtime_ns, first.xref.path.stat().st_mtime_ns)

        again = prepare(project)
        assert again.built == [], again.built
        assert again.reused == ["index", "xref"], again.reused
        # Not rebuilt, rather than rebuilt to the same content.
        assert (again.index.path.stat().st_mtime_ns, again.xref.path.stat().st_mtime_ns) == stamps


def test_an_edit_makes_what_was_built_unusable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        prepare(project)
        edit(project)

        stale = locate(project)
        assert stale.index.state == OTHER_INPUT, stale.index
        assert stale.xref.state == OTHER_INPUT, stale.xref
        assert not stale.ready

        fresh = prepare(project)
        assert fresh.built == ["index", "xref"], fresh.built
        assert fresh.ready and fresh.input_sha256 == fingerprint(project)


def test_an_index_from_an_older_build_is_not_treated_as_built() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        built = prepare(project)
        con = sqlite3.connect(built.xref.path)
        con.execute("update meta set value='arg-decode-1' where key='decoder'")
        con.commit()
        con.close()

        workspace = locate(project)
        assert workspace.xref.state == OLD_BUILD, workspace.xref
        assert "arg-decode-1" in workspace.xref.detail, workspace.xref.detail
        assert workspace.index.state == READY, workspace.index


def test_an_index_built_from_another_directory_is_found_not_duplicated() -> None:
    # The failure this exists for: build from one working directory, ask from
    # another, and the second one finds nothing and reports an empty answer.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "sub" / "line", overwrite=True)
        elsewhere = work / "elsewhere"
        elsewhere.mkdir()

        previous = Path.cwd()
        os.chdir(elsewhere)
        try:
            built = prepare(project)
            assert built.directory == (work / "sub" / ".gx3_index").resolve(), built.directory
        finally:
            os.chdir(previous)

        found = locate(project)
        assert found.directory == built.directory, (found.directory, built.directory)
        assert found.ready, found.as_dict()


def test_the_guide_calls_a_stale_cross_reference_not_built() -> None:
    from gx3cli.gx3_guide import gather, suggest

    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        prepare(project)
        edit(project)

        evidence = gather(project)
        assert not evidence.xref_built, evidence
        assert evidence.index_note, evidence
        commands = [s.command for s in suggest(evidence)]
        assert "workspace --prepare" in commands, commands


def test_the_command_runs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_workspace", "--root", str(project), "--prepare"],
            cwd=tmp, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        assert completed.returncode == 0, completed.stdout
        assert "ready" in completed.stdout, completed.stdout


def main() -> int:
    test_nothing_built_yet_says_so()
    test_preparing_builds_both_and_records_the_input()
    test_a_second_run_on_the_same_input_rebuilds_nothing()
    test_an_edit_makes_what_was_built_unusable()
    test_an_index_from_an_older_build_is_not_treated_as_built()
    test_an_index_built_from_another_directory_is_found_not_duplicated()
    test_the_guide_calls_a_stale_cross_reference_not_built()
    test_the_command_runs()
    print("workspace checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
