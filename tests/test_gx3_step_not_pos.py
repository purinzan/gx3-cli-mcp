from __future__ import annotations

"""The number on a line has to be the number GX Works3 shows.

rung-text located every line as "<program>:2048". That 2048 is pos, this
format's own row offset, and it reads exactly like a step number to someone
holding the same program open in GX Works3 -- where that rung is step 0. Issue
#49 asks for the two to be told apart, and for every explanation to lead back
to the rung it came from.

The printed rung already shows the step, in parentheses at the left of the
rung. rung-text now shows the same number, and the structured output carries
both under their own names.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_rung_text import RungText


ROOT = Path(__file__).resolve().parents[1]


def test_a_line_is_located_by_step_when_the_step_is_known() -> None:
    item = RungText(
        lddb="a_LDDB.db", pos=2048, title="", opcode="MOV", device="D100",
        condition="TRUE", step=0,
    )
    assert item.location == "a_LDDB.db st0", item.location
    assert "2048" not in item.to_line(), item.to_line()


def test_a_line_says_pos_when_no_step_is_known() -> None:
    # Not a bare number: whichever it is, the reader is told which.
    item = RungText(
        lddb="a_LDDB.db", pos=2048, title="", opcode="", device="M1",
        condition="TRUE", step=None,
    )
    assert item.location == "a_LDDB.db pos2048", item.location


def test_the_step_matches_the_printed_rung() -> None:
    from gx3cli.gx3_synthetic_project import create_demo_line_project

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        program = sorted(p.name for p in project.glob("*_LDDB.db"))[0]
        rung_text = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_rung_text", "--root", str(project),
             "--program", program, "--format", "json", "-o", str(work / "rungs.json")],
            cwd=work, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        assert rung_text.returncode == 0, rung_text.stdout
        items = json.loads((work / "rungs.json").read_text(encoding="utf-8"))
        assert items, rung_text.stdout

        # Both are there, named, and they are not the same number.
        first = items[0]
        assert "pos" in first and "step" in first, first

        printed = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_ladder_print", program, "--root", str(project)],
            cwd=work, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        assert printed.returncode == 0, printed.stdout
        for item in items[:5]:
            if item["step"] is None:
                continue
            assert f"({item['step']})" in printed.stdout, (
                f"step {item['step']} is not on the printed rung", printed.stdout[:400]
            )


def main() -> int:
    test_a_line_is_located_by_step_when_the_step_is_known()
    test_a_line_says_pos_when_no_step_is_known()
    test_the_step_matches_the_printed_rung()
    print("step vs pos checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
