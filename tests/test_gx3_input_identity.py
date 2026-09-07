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
import csv
import json
import subprocess
import sys
from unittest.mock import patch
from contextlib import closing
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
    from gx3cli.gx3_xref import main as xref_main

    assert xref_main(["--root", str(root), "--db", str(path), "build"]) == 0


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
        # Omitting root is not a bypass for missing construction evidence.
        try:
            con = open_xref_db(db)
        except SystemExit as exc:
            assert "build contract" in str(exc), exc
        else:
            con.close()
            raise AssertionError("legacy construction accepted without root")


def test_real_indexes_reject_missing_identity_and_removed_inputs() -> None:
    from gx3cli.gx3_workspace import prepare, locate
    from gx3cli.gx3_index_lite import open_existing

    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        built = prepare(project)
        for artifact, opener in ((built.xref, open_xref_db), (built.index, open_existing)):
            with closing(sqlite3.connect(artifact.path)) as con, con:
                saved = con.execute("select value from meta where key='input_sha256'").fetchone()[0]
                con.execute("delete from meta where key='input_sha256'")
            try:
                opener(artifact.path, root=project)
            except SystemExit as exc:
                assert "cannot be verified" in str(exc), exc
            else:
                raise AssertionError("missing identity accepted")
            assert not getattr(locate(project), artifact.kind).usable
            with closing(sqlite3.connect(artifact.path)) as con, con:
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
                with closing(sqlite3.connect(built.xref.path)) as con, con:
                    original = con.execute("select value from meta where key=?", (key,)).fetchone()[0]
                    con.execute("delete from meta where key=?", (key,))
                context = load_trace_constant_context(project, [], [])
                assert not context.enabled and not context.facts, context
                assert "xref unavailable" in context.reason, context
                trace = build_trace(project, "Y0", max_depth=2, max_devices=20,
                                    include_reset=True, strict_logic=True)
                assert trace["target"]["device"] == "Y0", trace
                with closing(sqlite3.connect(built.xref.path)) as con, con:
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
    test_fingerprint_buffers_preserve_digest_at_size_boundaries()
    test_external_csv_changes_reject_and_rebuild_real_index()
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


def test_external_csv_changes_reject_and_rebuild_real_index() -> None:
    from gx3cli import gx3_index_lite as lite
    from gx3cli.gx3_workspace import prepare, locate

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        workspace = prepare(project)
        index = workspace.index.path
        xref_bytes = workspace.xref.path.read_bytes()
        csv_path = work / "explicit # comm_refresh_areas.csv"
        units = work / "explicit # comm_units.csv"

        def refresh(label: str) -> None:
            with csv_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["device_start", "device_end", "network_label", "direction"])
                writer.writerow(["X0", "X0", label, "receive"])

        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "PYTHONIOENCODING": "utf-8"}
        def query():
            return subprocess.run([sys.executable, "-m", "gx3cli.gx3_index_lite", "device", "X0",
                                   "--root", str(project), "--db", str(index), "--json"],
                                  cwd=work, env=env, capture_output=True, text=True, encoding="utf-8", timeout=20)

        def rejected():
            saved = index.read_bytes()
            result = query()
            assert result.returncode != 0 and "CSV" in result.stderr, result
            assert index.read_bytes() == saved
            assert not locate(project).index.usable
            moved = index.with_suffix(".moved")
            index.rename(moved)
            moved.rename(index)

        refresh("network-before")
        args = ["build", "--root", str(project), "--out", str(index),
                "--refresh-csv", str(csv_path), "--unit-csv", str(units)]
        assert lite.main(args) == 0
        result = query()
        assert result.returncode == 0 and "network-before" in result.stdout, result
        assert json.loads(result.stdout)
        baseline = fingerprint(project)
        stat = csv_path.stat()
        refresh("network-after!")  # same bytes/mtime, different contents
        assert csv_path.stat().st_size == stat.st_size
        os.utime(csv_path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        assert fingerprint(project) == baseline
        rejected()
        previous_cwd = Path.cwd()
        elsewhere = work / "elsewhere"
        elsewhere.mkdir()
        try:
            os.chdir(elsewhere)
            rebuilt = prepare(project)
        finally:
            os.chdir(previous_cwd)
        assert rebuilt.built == ["index"] and rebuilt.reused == ["xref"], rebuilt
        assert workspace.xref.path.read_bytes() == xref_bytes
        assert "network-after!" in query().stdout
        assert prepare(project).built == []

        # Optional absent CSV becoming present is an input change, as are
        # changed unit contents and removal. Refresh and units are independent.
        units.write_text("start_io_dec,io_points\n0,16\n", encoding="utf-8")
        rejected()
        assert prepare(project).built == ["index"]
        units.write_text("start_io_dec,io_points\n0,32\n", encoding="utf-8")
        rejected()
        assert prepare(project).built == ["index"]
        units.unlink()
        rejected()
        assert prepare(project).built == ["index"]
        csv_path.unlink()
        rejected()
        assert prepare(project).built == ["index"]
        refresh("network-before")
        rejected()
        assert prepare(project).built == ["index"]

        # A real decode followed by a changed dependency must not publish a
        # mixed build or overwrite the prior usable artifact.
        saved = index.read_bytes()
        original = lite.load_refresh_areas
        def change_after_read(path):
            result = original(path)
            refresh("network-after!")
            return result
        with patch.object(lite, "load_refresh_areas", change_after_read):
            try:
                lite.main(args)
            except SystemExit as exc:
                assert "CSV inputs changed during" in str(exc), exc
            else:
                raise AssertionError("mixed CSV build published")
        assert index.read_bytes() == saved
        assert not list(index.parent.glob("*.building"))
        assert prepare(project).built == ["index"]

        # Current project identity alone cannot validate a pre-dependency DB.
        with closing(sqlite3.connect(index)) as con, con:
            con.execute("delete from meta where key='external_dependencies'")
        rejected()
        assert prepare(project).built == ["index"]

        csv_bytes = csv_path.read_bytes()
        try:
            lite.main(["build", "--root", str(project), "--out", str(csv_path),
                       "--refresh-csv", str(csv_path), "--unit-csv", str(units)])
        except SystemExit as exc:
            assert "overlaps" in str(exc), exc
        else:
            raise AssertionError("index overwrote its CSV dependency")
        assert csv_path.read_bytes() == csv_bytes


def test_fingerprint_buffers_preserve_digest_at_size_boundaries() -> None:
    import hashlib
    from gx3cli.gx3_input_identity import CHUNK, file_digest
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "content.db"
        for size in (0, 1, 65535, 65536, 65537, CHUNK - 1, CHUNK, CHUNK + 1, CHUNK * 2 + 1):
            payload = (b"source-version-" * (size // 15 + 1))[:size]
            path.write_bytes(payload)
            assert file_digest(path) == hashlib.sha256(payload).hexdigest(), size


if __name__ == "__main__":
    raise SystemExit(main())
