from __future__ import annotations

"""Commands that build their project root at import time have no --root option
and read the environment instead. gx3_cli has to put the requested root there,
or asking about one project is answered with another one -- successfully, and
without a warning, which is the worst shape a wrong answer can take."""

import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_cli import COMMANDS
from gx3cli.gx3_project_paths import ROOT_ENV
from gx3cli.gx3_synthetic_project import create_demo_line_project


REPO = Path(__file__).resolve().parents[1]

# The commands with no --root option of their own: their module-level ROOT is
# whatever default_project_root() resolved at import time.
ENV_ROOT_COMMANDS = ("used-devices", "hmi-build-info", "extended-instructions")


def ladder_row_count(root: Path) -> int:
    total = 0
    for lddb in sorted(root.glob("*_LDDB.db")):
        con = sqlite3.connect(f"file:{lddb}?mode=ro", uri=True)
        total += con.execute("select count(*) from LadderBlocks where blocktype = 0").fetchone()[0]
        con.close()
    return total


def test_env_root_commands_have_no_root_option() -> None:
    # If one of these grows a real --root option this list should shrink; the
    # check below would then exercise a path that no longer exists.
    for command in ENV_ROOT_COMMANDS:
        source = (REPO / "gx3cli" / COMMANDS[command].script).read_text(encoding="utf-8")
        assert '"--root"' not in source, f"{command} now parses --root; update this test"


def test_requested_root_reaches_a_command_that_reads_the_environment() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        wanted = create_demo_line_project(work / "wanted", overwrite=True)
        # This second project is what auto-detection picks instead (the
        # "_extracted_*" name wins), so the run fails if --root is dropped on
        # the way. Part of its ladder is removed so the two projects can be
        # told apart by row count.
        decoy = create_demo_line_project(work / "_extracted_other", overwrite=True)
        for lddb in sorted(decoy.glob("*_LDDB.db"))[:2]:
            con = sqlite3.connect(lddb)
            con.execute("delete from LadderBlocks")
            con.commit()
            con.close()

        wanted_rows = ladder_row_count(wanted)
        decoy_rows = ladder_row_count(decoy)
        assert wanted_rows > decoy_rows > 0, (wanted_rows, decoy_rows)

        env = dict(os.environ)
        env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        env.pop(ROOT_ENV, None)
        completed = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_cli", "extended-instructions", "--root", str(wanted)],
            cwd=work,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout

        summary = work / "project_extended_instruction_knowledge.txt"
        assert summary.exists(), completed.stdout
        text = summary.read_text(encoding="utf-8")
        match = re.search(r"実ラダー行: (\d+)", text)
        assert match, "the report did not state its ladder row count:\n" + text[:800]
        reported = int(match.group(1))
        assert reported == wanted_rows, (
            f"the report covers {reported} ladder rows; the requested project has {wanted_rows} "
            f"and the auto-detected one has {decoy_rows} -- --root was dropped"
        )


def main() -> int:
    test_env_root_commands_have_no_root_option()
    test_requested_root_reaches_a_command_that_reads_the_environment()
    print("cli root passthrough checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
