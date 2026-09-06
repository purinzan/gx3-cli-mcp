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

import argparse
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
from gx3cli.gx3_device_dictionary import collect_dictionary
from gx3cli.gx3_label_resolve import (
    LABELS_ABSENT,
    LABELS_UNKNOWN_SCHEMA,
    LABELS_UNREADABLE,
    load_label_resolver,
)
from gx3cli.gx3_link_map import ProjectSpec, load_project_devices
from gx3cli.gx3_xref import main as xref_main, print_cross_where_used
from test_gx3_shared_reach import coil, write_program


ROOT = Path(__file__).resolve().parents[1]


def two_projects(work: Path) -> None:
    write_program(work / "_extracted_one", [("_guid/a", coil("a", 1, 100))])
    write_program(work / "_extracted_two", [("_guid/a", coil("a", 2, 200))])


def build_xref(root: Path, db: Path) -> None:
    with contextlib.redirect_stdout(io.StringIO()):
        assert xref_main(["--root", str(root), "--db", str(db), "build"]) == 0


def write_index_root(db: Path, root: Path) -> None:
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("create table meta(key text primary key, value text not null)")
    con.execute("insert into meta(key, value) values ('root', ?)", (str(root),))
    con.commit()
    con.close()


def foreign_xref_fixture(work: Path) -> tuple[Path, Path, Path]:
    one = work / "one"
    two = work / "two"
    write_program(one, [("_guid/a", coil("a", 1, 100))])
    write_program(two, [("_guid/a", coil("a", 2, 200))])
    db = work / "two_xref.sqlite"
    build_xref(two, db)
    return one, two, db


def assert_foreign_xref_rejected(call) -> None:
    try:
        call()
    except SystemExit as stopped:
        assert "xref db was built from a different input" in str(stopped), str(stopped)
    else:
        raise AssertionError("a foreign xref was accepted as if it belonged to the selected project")


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
        assert "_extracted_one" in body and "_extracted_two" in body, body


def test_naming_the_project_settles_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        assert run_cli(work, ["metrics", "--root", str(work / "_extracted_one")]).returncode == 0


def test_help_and_global_version_never_ask_for_a_project() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        assert run_cli(work, ["metrics", "--help"]).returncode == 0
        assert run_cli(work, ["--version"]).returncode == 0
        assert run_cli(work, ["list"]).returncode == 0


def test_project_version_command_does_require_a_project() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        previous = Path.cwd()
        os.chdir(work)
        try:
            message = ambiguous_project("version", [])
            assert "more than one project" in message, message
            assert ambiguous_project("version", ["--root", str(work / "_extracted_one")]) == ""
        finally:
            os.chdir(previous)


def test_a_project_named_positionally_settles_it_too() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        assert ambiguous_project(
            "semantic-diff", [str(work / "_extracted_one"), str(work / "_extracted_two")]
        ) == ""


def test_an_unrelated_gx3_argument_does_not_bypass_root_ambiguity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        previous = Path.cwd()
        os.chdir(work)
        try:
            message = ambiguous_project("metrics", ["--output", "report.gx3"])
            assert "more than one project" in message, message
        finally:
            os.chdir(previous)


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


def test_base_root_plus_child_root_is_ambiguous() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        write_program(work, [("_guid/a", coil("a", 1, 100))])
        write_program(work / "_extracted_child", [("_guid/a", coil("a", 2, 200))])
        previous = Path.cwd()
        os.chdir(work)
        try:
            message = ambiguous_project("metrics", [])
            assert "more than one project" in message, message
            assert str(work.resolve()) in message
            assert "_extracted_child" in message
        finally:
            os.chdir(previous)


def test_multiple_index_roots_stop_the_run_without_local_project_folders() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        top = Path(tmp)
        work = top / "cli"
        work.mkdir()
        one = top / "remote_one"
        two = top / "remote_two"
        write_program(one, [("_guid/a", coil("a", 1, 100))])
        write_program(two, [("_guid/a", coil("a", 2, 200))])
        write_index_root(work / ".gx3_index" / "one.sqlite", one)
        write_index_root(work / ".gx3_index" / "two.sqlite", two)

        result = run_cli(work, ["metrics"])
        body = result.stdout + result.stderr
        assert result.returncode != 0, body
        assert "more than one project" in body, body
        assert "remote_one" in body and "remote_two" in body, body


def test_environment_root_settles_ambiguity() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        previous = Path.cwd()
        old_project = os.environ.get("PROJECT_ROOT")
        old_legacy = os.environ.get("GX3_ROOT")
        os.chdir(work)
        os.environ["PROJECT_ROOT"] = str(work / "_extracted_one")
        os.environ.pop("GX3_ROOT", None)
        try:
            assert ambiguous_project("metrics", []) == ""
        finally:
            os.chdir(previous)
            if old_project is None:
                os.environ.pop("PROJECT_ROOT", None)
            else:
                os.environ["PROJECT_ROOT"] = old_project
            if old_legacy is None:
                os.environ.pop("GX3_ROOT", None)
            else:
                os.environ["GX3_ROOT"] = old_legacy


def test_no_project_command_is_not_blocked_by_neighboring_projects() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        two_projects(work)
        previous = Path.cwd()
        os.chdir(work)
        try:
            assert ambiguous_project("live-read", ["--ip", "127.0.0.1", "--device", "D0", "--dry-run"]) == ""
        finally:
            os.chdir(previous)


def test_device_dictionary_rejects_another_projects_xref() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        one, _two, foreign = foreign_xref_fixture(Path(tmp))
        assert_foreign_xref_rejected(lambda: collect_dictionary(one, foreign))


def test_link_map_rejects_another_projects_xref() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        one, _two, foreign = foreign_xref_fixture(Path(tmp))
        assert_foreign_xref_rejected(
            lambda: load_project_devices(ProjectSpec(label="one", root=one, db=foreign))
        )


def test_cross_where_used_rejects_a_swapped_linked_xref() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        one, _two, foreign = foreign_xref_fixture(work)
        link_db = work / "link_map.sqlite"
        con = sqlite3.connect(link_db)
        con.executescript(
            """
            create table project(label text primary key, root text not null, xref_db text not null);
            create table link_map(
                id integer primary key autoincrement,
                project_a text not null, device_a text not null,
                project_b text not null, device_b text not null,
                link_type text not null, link_addr text,
                direction text, confidence text, role text, evidence text
            );
            """
        )
        con.execute(
            "insert into project(label, root, xref_db) values (?, ?, ?)",
            ("other", str(one), str(foreign)),
        )
        con.execute(
            "insert into link_map(project_a, device_a, project_b, device_b, link_type, "
            "link_addr, direction, confidence, role, evidence) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("current", "M100", "other", "M200", "exact-device", "M100", "current_to_other", "high", "", "test"),
        )
        con.commit()
        con.close()

        args = argparse.Namespace(
            link_db=str(link_db), project="current", root=str(one),
            cross_limit=20, cross_xref_limit=20,
        )
        assert_foreign_xref_rejected(
            lambda: print_cross_where_used(args, "M100")
        )


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
    assert resolver.resolve_token("D100") is None
    assert "D100" not in resolver.unresolved


def main() -> int:
    test_two_projects_side_by_side_stop_the_run()
    test_naming_the_project_settles_it()
    test_help_and_global_version_never_ask_for_a_project()
    test_project_version_command_does_require_a_project()
    test_a_project_named_positionally_settles_it_too()
    test_an_unrelated_gx3_argument_does_not_bypass_root_ambiguity()
    test_one_project_is_never_ambiguous()
    test_base_root_plus_child_root_is_ambiguous()
    test_multiple_index_roots_stop_the_run_without_local_project_folders()
    test_environment_root_settles_ambiguity()
    test_no_project_command_is_not_blocked_by_neighboring_projects()
    test_device_dictionary_rejects_another_projects_xref()
    test_link_map_rejects_another_projects_xref()
    test_cross_where_used_rejects_a_swapped_linked_xref()
    test_a_label_database_that_will_not_open_stops_the_build()
    test_no_label_database_is_not_a_failure()
    test_a_schema_this_build_does_not_know_is_reported_not_fatal()
    test_an_empty_label_file_means_no_labels()
    test_an_unresolved_label_token_is_remembered()
    print("input trust checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
