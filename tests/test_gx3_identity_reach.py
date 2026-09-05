from __future__ import annotations

"""Every artefact says which input it is of, not just the cross-reference.

The cross-reference was stamped first. An index built from another project
still answered, and a survey package -- which outlives the folder it was made
from -- had nothing in it that could tell a reader months later whether it was
about the project in front of them.

A database built before inputs were stamped is still accepted. That is a
migration allowance, not a design: the naming and decoder checks already refuse
the ones that would answer differently, and a second failure for the same thing
helps nobody. When the allowance ends, this test is where to say so.
"""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_index_lite import open_existing
from gx3cli.gx3_input_identity import fingerprint
from gx3cli.gx3_synthetic_project import create_demo_line_project


ROOT = Path(__file__).resolve().parents[1]


def build_index(project: Path, out: Path) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "gx3cli.gx3_index_lite", "build",
         "--root", str(project), "--out", str(out)],
        cwd=out.parent, env=env, text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
    )
    assert completed.returncode == 0, completed.stdout


def differ(project: Path) -> None:
    """Make a copy of the fixture read as a different project."""
    comment_db = next(project.glob("*_DC.db"))
    con = sqlite3.connect(comment_db)
    con.execute("update COMMENT_DATA set CmtData = CmtData || ' other line'")
    con.commit()
    con.close()


def test_an_index_records_the_input_it_was_built_from() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        index = work / "line.sqlite"
        build_index(project, index)

        con = sqlite3.connect(index)
        meta = dict(con.execute("select key, value from meta"))
        con.close()
        assert meta.get("input_sha256") == fingerprint(project), meta
        assert meta.get("analyzer_version"), meta


def test_an_index_from_another_project_is_refused() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        one = create_demo_line_project(work / "one", overwrite=True)
        other = create_demo_line_project(work / "other", overwrite=True)
        differ(other)
        index = work / "one.sqlite"
        build_index(one, index)

        open_existing(index, one).close()  # the project it is of: fine
        try:
            open_existing(index, other)
        except SystemExit as exc:
            assert "different input" in str(exc), str(exc)
            return
        raise AssertionError("an index from another project was accepted")


def test_asking_without_a_root_still_works() -> None:
    # The query commands can be pointed straight at a database. Without a
    # project to compare against there is nothing to check, and refusing would
    # only break a working call.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        index = work / "line.sqlite"
        build_index(project, index)
        open_existing(index).close()


def test_a_survey_package_says_which_input_it_describes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_project_survey", "--root", str(project),
             "--output-dir", str(work / "out"), "--prefix", "sv"],
            cwd=work, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        assert completed.returncode == 0, completed.stdout
        manifest = json.loads((work / "out" / "sv_manifest.json").read_text(encoding="utf-8"))
        assert manifest["input_sha256"] == fingerprint(project), manifest["input_sha256"]
        assert manifest["analyzer_version"], manifest


def main() -> int:
    test_an_index_records_the_input_it_was_built_from()
    test_an_index_from_another_project_is_refused()
    test_asking_without_a_root_still_works()
    test_a_survey_package_says_which_input_it_describes()
    print("identity reach checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
