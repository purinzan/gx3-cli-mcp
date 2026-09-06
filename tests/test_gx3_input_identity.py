from __future__ import annotations

"""An answer has to say which input it came from.

An index or a cross-reference recorded the path it was built from. A path is
not an identity: the folder behind it can be rebuilt, edited or replaced by a
different project, and every answer afterwards is about a file nobody opened.
The same failure has already happened here in another form -- three commands
ignored --root and answered about whatever they auto-detected.

Issue #49 asks for the input to be part of the evidence, and for it to be
possible to check that the logic, the comments and the communication settings
came from the same input. So the fingerprint covers all of them.
"""

import sqlite3
import os
import tempfile
import shutil
from pathlib import Path

from gx3cli.gx3_input_identity import fingerprint, input_files
from gx3cli.gx3_synthetic_project import create_demo_line_project
from gx3cli.gx3_xref import open_xref_db, stamp_decoder


def test_a_folder_with_no_ladder_has_no_identity() -> None:
    # Not a hash of nothing: two unrelated empty folders must not look like the
    # same input.
    with tempfile.TemporaryDirectory() as tmp:
        assert fingerprint(Path(tmp)) == ""


def test_the_same_project_hashes_the_same_and_a_changed_one_does_not() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        first = fingerprint(project)
        assert first, "a project with a ladder should have a fingerprint"
        assert fingerprint(project) == first, "hashing twice should agree"

        # A comment changed is a changed input: an answer built before it is
        # about a different project than the one in front of you.
        comment_db = next(project.glob("*_DC.db"), None)
        assert comment_db is not None, sorted(p.name for p in project.iterdir())
        con = sqlite3.connect(comment_db)
        con.execute("update COMMENT_DATA set CmtData = CmtData || ' changed'")
        con.commit()
        con.close()
        assert fingerprint(project) != first


def test_the_ladder_the_comments_and_the_parameters_all_count() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        names = {p.name for p in input_files(project)}
        assert any(name.endswith("_LDDB.db") for name in names), names
        assert any(name.endswith("_DC.db") for name in names), names

        # Something the analysis does not depend on does not change it.
        before = fingerprint(project)
        (project / "notes.txt").write_text("scratch", encoding="utf-8")
        assert fingerprint(project) == before


def make_stamped_xref(path: Path, root: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("create table xref (id integer primary key, device text)")
    stamp_decoder(con, root)
    con.commit()
    con.close()


def test_a_database_built_from_another_project_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        one = create_demo_line_project(work / "one", overwrite=True)
        other = create_demo_line_project(work / "other", overwrite=True)
        # Two fixtures of the same shape: make them differ the way two real
        # projects would.
        comment_db = next(other.glob("*_DC.db"))
        con = sqlite3.connect(comment_db)
        con.execute("update COMMENT_DATA set CmtData = CmtData || ' other line'")
        con.commit()
        con.close()
        assert fingerprint(one) != fingerprint(other)

        db = work / "one_xref.sqlite"
        make_stamped_xref(db, one)

        # Opened against the project it was built from: fine.
        con = open_xref_db(db, root=one)
        con.close()

        # Opened against a different project: refused, with both fingerprints.
        try:
            open_xref_db(db, root=other)
        except SystemExit as exc:
            message = str(exc)
            assert "different input" in message, message
            assert "Rebuild it" in message, message
            return
        raise AssertionError("a database from another project was accepted")


def test_a_database_with_no_input_recorded_requires_rebuild_for_root() -> None:
    # Decoder compatibility is not proof of project identity.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        db = work / "old_xref.sqlite"
        con = sqlite3.connect(db)
        con.execute("create table xref (id integer primary key, device text)")
        stamp_decoder(con)  # no root: no input recorded
        con.commit()
        con.close()
        try:
            open_xref_db(db, root=project)
        except SystemExit as exc:
            assert "cannot be verified" in str(exc), exc
        else:
            raise AssertionError("unstamped database accepted for project")
        # Standalone inspection without a claimed project remains supported.
        con = open_xref_db(db)
        con.close()


def test_real_indexes_reject_missing_identity_and_removed_inputs() -> None:
    from gx3cli.gx3_workspace import prepare, locate
    from gx3cli.gx3_index_lite import open_existing

    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        built = prepare(project)
        for artifact, opener in ((built.xref, open_xref_db), (built.index, open_existing)):
            with sqlite3.connect(artifact.path) as con:
                saved = con.execute("select value from meta where key='input_sha256'").fetchone()[0]
                con.execute("delete from meta where key='input_sha256'")
            try:
                opener(artifact.path, root=project)
            except SystemExit as exc:
                assert "cannot be verified" in str(exc), exc
            else:
                raise AssertionError("missing identity accepted")
            assert not getattr(locate(project), artifact.kind).usable
            with sqlite3.connect(artifact.path) as con:
                con.execute("insert into meta values ('input_sha256', ?)", (saved,))
        for path in input_files(project):
            path.unlink()
        assert not locate(project).ready
        for artifact, opener in ((built.xref, open_xref_db), (built.index, open_existing)):
            try:
                opener(artifact.path, root=project)
            except SystemExit as exc:
                assert "cannot be verified" in str(exc), exc
            else:
                raise AssertionError("removed input accepted")
            # Failed validation must not retain a Windows file lock.
            moved = artifact.path.with_suffix(".moved")
            artifact.path.rename(moved)
            moved.rename(artifact.path)


def test_rejected_xref_disables_only_optional_trace_pruning() -> None:
    from gx3cli.gx3_workspace import prepare
    from gx3cli.gx3_topology_conditions import load_trace_constant_context
    from gx3cli.trace_gx3_device_dependencies import build_trace

    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        built = prepare(project)
        previous = Path.cwd()
        try:
            os.chdir(tmp)
            for key in ("input_sha256", "decoder"):
                with sqlite3.connect(built.xref.path) as con:
                    original = con.execute("select value from meta where key=?", (key,)).fetchone()[0]
                    con.execute("delete from meta where key=?", (key,))
                context = load_trace_constant_context(project, [], [])
                assert not context.enabled and not context.facts, context
                assert "xref unavailable" in context.reason, context
                trace = build_trace(project, "Y0", max_depth=2, max_devices=20,
                                    include_reset=True, strict_logic=True)
                assert trace["target"]["device"] == "Y0", trace
                with sqlite3.connect(built.xref.path) as con:
                    con.execute("insert into meta values (?, ?)", (key, original))
        finally:
            os.chdir(previous)


def test_new_analysis_dependencies_invalidate_real_indexes() -> None:
    from gx3cli.gx3_workspace import prepare, locate, OTHER_INPUT

    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        built = prepare(project)
        baseline = fingerprint(project)
        for name in ("sample_STDB.db", "sample_DM.db", "sample_FBDDB.db", "module.db",
                     "SourceInfo.CAB", "Config.xml", "motion.iut", "parameters.w3pa"):
            path = project / name
            assert not path.exists(), name
            path.write_bytes(b"synthetic new input")
            assert fingerprint(project) != baseline, name
            state = locate(project)
            assert state.index.state == state.xref.state == OTHER_INPUT, (name, state)
            try:
                open_xref_db(built.xref.path, root=project)
            except SystemExit as exc:
                assert "different input" in str(exc), exc
            else:
                raise AssertionError(f"stale xref accepted after {name}")
            path.unlink()
            assert fingerprint(project) == baseline
        moved = Path(tmp) / "moved"
        shutil.copytree(project, moved)
        assert fingerprint(moved) == baseline
        (project / "report.csv").write_text("output", encoding="utf-8")
        (project / "cache.sqlite").write_bytes(b"generated")
        assert fingerprint(project) == baseline


def main() -> int:
    test_rejected_xref_disables_only_optional_trace_pruning()
    test_new_analysis_dependencies_invalidate_real_indexes()
    test_a_folder_with_no_ladder_has_no_identity()
    test_the_same_project_hashes_the_same_and_a_changed_one_does_not()
    test_the_ladder_the_comments_and_the_parameters_all_count()
    test_a_database_built_from_another_project_is_refused()
    test_a_database_with_no_input_recorded_requires_rebuild_for_root()
    test_real_indexes_reject_missing_identity_and_removed_inputs()
    print("input identity checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
