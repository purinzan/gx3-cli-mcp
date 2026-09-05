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
from pathlib import Path

from gx3cli.gx3_input_identity import fingerprint
from gx3cli.gx3_synthetic_project import create_demo_line_project


ROOT = Path(__file__).resolve().parents[1]


def run(module: str, args: list[str], cwd: Path) -> str:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd, env=env, text=True, encoding="utf-8", errors="replace",
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


def main() -> int:
    test_three_artefacts_of_one_project_agree_on_the_input()
    test_an_artefact_from_a_changed_project_no_longer_agrees()
    print("same input across artefacts checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
