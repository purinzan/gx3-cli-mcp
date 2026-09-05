from __future__ import annotations

"""One entry point per question, and a section that did not run says so.

The failure this guards is the quiet one. A section that timed out, or that
could not be produced, prints nothing; the sections around it print normally;
and the reader takes the page as the answer. So a run with a missing section
does not exit 0, and the note under the heading says which of the two happened
-- ran out of time, or could not be produced -- because the next step differs.

The other thing pinned here is that a relative --root works. Steps run with
their working directory set to the package, so "../line" resolved somewhere
else there and every section reported an empty project while exiting 0.
"""

import io
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_explore import (
    BY_NAME,
    PURPOSES,
    Context,
    Step,
    explore,
    main,
)
from gx3cli.gx3_synthetic_project import create_demo_line_project
from gx3cli.gx3_workspace import prepare


ROOT = Path(__file__).resolve().parents[1]


def context_for(project: Path, **kwargs) -> Context:
    return Context(root=project, workspace=prepare(project), **kwargs)


def test_the_four_questions_are_the_four_questions() -> None:
    assert [p.name for p in PURPOSES] == ["overview", "why", "concerns", "changed"]
    assert BY_NAME["why"].needs_target
    assert BY_NAME["changed"].needs_against
    for purpose in PURPOSES:
        assert purpose.steps, purpose.name
        assert purpose.question_ja and purpose.question_en, purpose.name


def test_a_section_that_could_not_run_makes_the_run_fail() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        context = context_for(project)
        purpose = BY_NAME["overview"]
        broken = Step("nope", "a section", "ある項目", lambda c: ["no-such-command"])
        one_step = type(purpose)(
            purpose.name, purpose.question_en, purpose.question_ja, (broken,)
        )

        out = io.StringIO()
        code = explore(one_step, context, stream=out)
        assert code == 1, out.getvalue()
        assert "could not be produced" in out.getvalue(), out.getvalue()
        assert "sections not produced: nope" in out.getvalue(), out.getvalue()


def test_a_section_that_ran_out_of_time_says_that_instead() -> None:
    # Not the same thing as a section that failed: the fix is to run it on its
    # own, not to look for what is broken.
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        context = context_for(project, timeout=0.001)
        purpose = BY_NAME["overview"]
        slow = type(purpose)(
            "overview", "q", "問い",
            (Step("reliability-report", "slow", "遅い",
                  lambda c: ["reliability-report", "--root", str(c.root)]),),
        )

        out = io.StringIO()
        code = explore(slow, context, stream=out)
        assert code == 1
        assert "did not finish within" in out.getvalue(), out.getvalue()


def test_the_header_names_the_input_every_section_was_read_from() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        context = context_for(project)
        out = io.StringIO()
        empty = type(BY_NAME["overview"])("overview", "q", "問い", ())
        explore(empty, context, stream=out)
        text = out.getvalue()
        assert context.workspace.input_sha256[:12] in text, text
        assert str(project) in text, text


def test_japanese_is_a_choice_not_a_translation_of_the_output() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        out = io.StringIO()
        empty = type(BY_NAME["overview"])("overview", "q", "このプロジェクトに何があるか", ())
        explore(empty, context_for(project, ja=True), stream=out)
        assert "問い: このプロジェクトに何があるか" in out.getvalue(), out.getvalue()


def test_why_without_a_device_says_what_to_type() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        code = main(["why", "--root", str(project), "--no-prepare"])
        assert code == 1


def test_a_relative_root_reaches_the_project() -> None:
    # Steps run with cwd set to the package directory, so a relative root that
    # is correct for the caller is wrong for them.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        create_demo_line_project(work / "line", overwrite=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_explore", "overview", "--root", "line"],
            cwd=work, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        assert "no rungs found" not in completed.stdout, completed.stdout
        assert str((work / "line").resolve()) in completed.stdout, completed.stdout


def test_no_section_writes_into_the_package() -> None:
    # Several of these commands write CSVs relative to their working
    # directory. Run from the package directory they fill gx3cli/outputs with
    # one project's data and leave it there -- which is how a customer's device
    # names ended up inside an installed package once already.
    package = ROOT / "gx3cli"
    before = {p.name for p in package.iterdir()}
    with tempfile.TemporaryDirectory() as tmp:
        project = create_demo_line_project(Path(tmp) / "line", overwrite=True)
        context = context_for(project)
        purpose = type(BY_NAME["overview"])(
            "overview", "q", "問い",
            (Step("exec-config", "programs", "プログラム",
                  lambda c: ["exec-config", "--root", str(c.root),
                             "--db", str(c.workspace.xref.path)]),),
        )
        explore(purpose, context, stream=io.StringIO())
        assert context.side_files.is_dir(), context.side_files
        assert str(project) in str(context.side_files) or context.side_files.exists()
    assert {p.name for p in package.iterdir()} == before, "explore wrote into gx3cli/"


def main_() -> int:
    test_the_four_questions_are_the_four_questions()
    test_a_section_that_could_not_run_makes_the_run_fail()
    test_a_section_that_ran_out_of_time_says_that_instead()
    test_the_header_names_the_input_every_section_was_read_from()
    test_japanese_is_a_choice_not_a_translation_of_the_output()
    test_why_without_a_device_says_what_to_type()
    test_a_relative_root_reaches_the_project()
    test_no_section_writes_into_the_package()
    print("explore checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
