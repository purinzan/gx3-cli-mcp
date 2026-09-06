from __future__ import annotations

"""Three ways an answer could be about something other than what was asked.

#97 -- with two projects side by side, the auto-detection picks the newest and
every command reports on it without saying so. That sits upstream of every
fingerprint check: those prove an index belongs to the root that was analysed,
never that the root was the one meant.

#88 -- several commands hold a --root and opened the cross-reference raw, so
alarms from one project could be printed beside comments from another and the
run would finish cleanly.

#90 -- a LabelData.db that would not open returned the same empty resolver as a
project with no labels, so every label-named operand vanished from the
cross-reference and nothing said so.

What is pinned here is mostly the shape of the refusal, because the failure
mode in all three is a run that completes.
"""

import contextlib
import io
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_cli import ambiguous_project
from gx3cli.gx3_label_resolve import (
    LABELS_ABSENT,
    LABELS_UNKNOWN_SCHEMA,
    LABELS_UNREADABLE,
    load_label_resolver,
)
from gx3cli.gx3_xref import main as xref_main
from test_gx3_shared_reach import coil, write_program


ROOT = Path(__file__).resolve().parents[1]


def two_projects(work: Path) -> None:
    write_program(work / "_extracted_one", [("_guid/a", coil("a", 1, 100))])
    write_program(work / "_extracted_two", [("_guid/a", coil("a", 2, 200))])


def run_cli(work: Path, args: list[str]) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.pop("PROJECT_ROOT", None)
    env.pop("GX3_ROOT", None)
    return subprocess.run(
        [sys.executable, "-m", "gx3cli.gx3_cli", *args],
        cwd=work, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )


def test_two_projects_side_by_side_stop_the_run() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        result = run_cli(work, ["metrics"])
        assert result.returncode != 0, result.stdout
        body = result.stdout + result.stderr
        assert "more than one project" in body, body
        # Both are named, so the reader can choose rather than guess at what
        # the tool was choosing between.
        assert "_extracted_one" in body and "_extracted_two" in body, body


def test_naming_the_project_settles_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        assert run_cli(work, ["metrics", "--root", str(work / "_extracted_one")]).returncode == 0


def test_help_and_version_never_ask_for_a_project() -> None:
    # The addendum's trap: argparse evaluates `default=default_project_root()`
    # before parsing, so a check placed there would break --help and even an
    # explicit --root.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        assert run_cli(work, ["metrics", "--help"]).returncode == 0
        assert run_cli(work, ["--version"]).returncode == 0
        assert run_cli(work, ["list"]).returncode == 0


def test_a_project_named_positionally_settles_it_too() -> None:
    # `semantic-diff old new` says which projects it means and never consults
    # the auto-detection.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        assert ambiguous_project(
            "semantic-diff", [str(work / "_extracted_one"), str(work / "_extracted_two")]
        ) == ""


def test_one_project_is_never_ambiguous() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "_extracted_one", [("_guid/a", coil("a", 1, 100))])
        previous = Path.cwd()
        os.chdir(work)
        try:
            assert ambiguous_project("metrics", []) == ""
        finally:
            os.chdir(previous)


def test_a_label_database_that_will_not_open_stops_the_build() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/a", coil("a", 1, 100))])
        (work / "p" / "LabelData.db").write_bytes(b"not a database at all")

        resolver = load_label_resolver(work / "p")
        assert resolver.status == LABELS_UNREADABLE, resolver.status
        assert resolver.fatal and not resolver.usable

        try:
            with contextlib.redirect_stdout(io.StringIO()):
                xref_main(["--root", str(work / "p"), "--db", str(work / "x.sqlite"), "build"])
        except SystemExit as stopped:
            assert "could not be read" in str(stopped), str(stopped)
        else:
            raise AssertionError("a cross-reference was built with the labels silently missing")


def test_no_label_database_is_not_a_failure() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/a", coil("a", 1, 100))])
        resolver = load_label_resolver(work / "p")
        assert resolver.status == LABELS_ABSENT, resolver.status
        assert resolver.usable and not resolver.fatal


def test_a_schema_this_build_does_not_know_is_reported_not_fatal() -> None:
    # A label database from another GX Works3 version opens and holds tables
    # this does not read. Refusing to analyse the ladder over that costs more
    # than it saves; calling it "no labels" is the bug.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/a", coil("a", 1, 100))])
        con = sqlite3.connect(work / "p" / "LabelData.db")
        con.execute("create table SomethingElse (id integer)")
        con.commit()
        con.close()

        resolver = load_label_resolver(work / "p")
        assert resolver.status == LABELS_UNKNOWN_SCHEMA, resolver.status
        assert not resolver.usable, "an unknown schema must not read as 'no labels'"
        assert not resolver.fatal, "it must not stop the run either"


def test_an_empty_label_file_means_no_labels() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work / "p", [("_guid/a", coil("a", 1, 100))])
        (work / "p" / "LabelData.db").write_bytes(b"")
        assert load_label_resolver(work / "p").status == LABELS_ABSENT


def test_an_unresolved_label_token_is_remembered() -> None:
    from gx3cli.gx3_label_resolve import EMPTY

    resolver = EMPTY
    resolver.unresolved.clear()
    assert resolver.resolve_token("_lid/TableA/7") is None
    assert "_lid/TableA/7" in resolver.unresolved
    # Not a label token at all: nothing to record.
    assert resolver.resolve_token("D100") is None
    assert "D100" not in resolver.unresolved


def main() -> int:
    test_two_projects_side_by_side_stop_the_run()
    test_naming_the_project_settles_it()
    test_help_and_version_never_ask_for_a_project()
    test_a_project_named_positionally_settles_it_too()
    test_one_project_is_never_ambiguous()
    test_a_label_database_that_will_not_open_stops_the_build()
    test_no_label_database_is_not_a_failure()
    test_a_schema_this_build_does_not_know_is_reported_not_fatal()
    test_an_empty_label_file_means_no_labels()
    test_an_unresolved_label_token_is_remembered()
    print("input trust checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
