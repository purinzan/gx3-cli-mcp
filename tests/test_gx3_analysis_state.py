from __future__ import annotations

"""A count of zero means two different things, and they were reported alike.

A check that ran and found nothing is a clean result. A check that could not
run -- no cross-reference, no index, no ladder rows -- also returned zero, and
the summary recorded `count: 0` for both. Six of lint's eleven checks were in
that state, and a `--fail-on high` gate passed on every one of them, having
looked at nothing.

Issue #49 asks for this: an unsupported input or an unevaluable check must not
be reported as zero findings and treated as normal.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_analysis_state import (
    CHECKED,
    DECODE,
    DISCOVERY,
    NOT_EVALUATED,
    PARTIAL,
    REACH,
    SEMANTICS,
    STAGE_LABELS,
    STAGE_LABELS_JA,
    STAGES,
    TOPOLOGY,
    TRUNCATED,
    AnalysisState,
    checked,
    not_evaluated,
    summarise,
    worst,
)
from gx3cli.gx3_synthetic_project import create_demo_line_project


ROOT = Path(__file__).resolve().parents[1]


def test_a_state_says_why_and_what_to_do() -> None:
    state = not_evaluated("no cross-reference database", "gx3-cli xref build --root <project>")
    assert not state.conclusive
    line = state.line("multi-writer")
    assert "not evaluated" in line
    assert "no cross-reference database" in line
    assert "xref build" in line, line

    body = state.as_dict()
    assert body["state"] == NOT_EVALUATED
    assert body["reason"] == "no cross-reference database"


def test_an_unknown_state_is_refused() -> None:
    # The vocabulary is small on purpose; a free-text state would drift back
    # into "skipped" meaning whatever the writer had in mind that day.
    try:
        AnalysisState("mostly fine")
    except ValueError:
        return
    raise AssertionError("an unknown state was accepted")


def test_one_check_that_did_not_run_makes_the_set_inconclusive() -> None:
    states = {
        "duplicate-coil": checked(),
        "multi-writer": not_evaluated("no cross-reference database"),
        "compare-type": checked(),
    }
    assert worst(list(states.values())).state == NOT_EVALUATED
    summary = summarise(states)
    assert summary["inconclusive"] == ["multi-writer"], summary
    assert summary["by_state"] == {CHECKED: 2, NOT_EVALUATED: 1}, summary


def test_lint_says_which_checks_did_not_run_and_can_fail_on_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")

        # No index and no cross-reference were built, so several checks cannot
        # run. Without --require-evaluated the command still succeeds; what it
        # must not do is call them zero findings and say nothing.
        completed = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_lint", str(project),
             "--out-prefix", str(work / "lint")],
            cwd=work, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False,
        )
        assert completed.returncode == 0, completed.stdout
        assert "did not run" in completed.stdout, completed.stdout

        summary = json.loads((work / "lint_summary.json").read_text(encoding="utf-8"))
        inconclusive = summary["analysis"]["inconclusive"]
        assert inconclusive, summary["analysis"]
        for name in inconclusive:
            entry = summary["checks"][name]
            assert entry["state"] == NOT_EVALUATED, entry
            assert entry["count"] == 0
            # The zero is still there, and now it carries the reason it is zero.
            assert entry.get("reason"), entry

        # A gate can insist that everything was actually examined.
        gated = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_lint", str(project),
             "--out-prefix", str(work / "lint"), "--require-evaluated"],
            cwd=work, env=env, text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
        )
        assert gated.returncode == 2, gated.stdout + gated.stderr
        assert "require-evaluated" in gated.stderr, gated.stderr


def test_a_result_that_is_not_checked_has_to_name_a_stage() -> None:
    """#49 asks the five stages to be part of the shared form, not a comment.

    "partly read" tells a reader that something is missing. Which of the five
    stages it went missing at tells them what to do: a rung whose wiring could
    not be folded into a condition is not fixed by raising a depth limit, and a
    program in a language this does not read is not fixed by either.

    Making it a constructor error rather than a convention is the only way the
    next state added somewhere else carries it too.
    """
    try:
        AnalysisState(PARTIAL, reason="something was missing")
    except ValueError as error:
        assert "stage" in str(error), str(error)
    else:
        raise AssertionError("a partial result was allowed to skip the stage")


def test_checked_needs_no_stage() -> None:
    state = AnalysisState(CHECKED)
    assert state.stage == ""
    assert "[" not in state.line()


def test_the_stage_is_in_the_line_and_in_the_data() -> None:
    state = AnalysisState(TRUNCATED, reason="stopped", stage=REACH)
    assert "how far the search went" in state.line(), state.line()
    assert "問われた範囲" in state.line(ja=True), state.line(ja=True)
    data = state.as_dict()
    assert data["stage"] == REACH, data
    assert data["stage_label"] and data["stage_label_ja"], data


def test_the_five_stages_are_the_five_the_issue_names() -> None:
    assert STAGES == (DISCOVERY, DECODE, TOPOLOGY, SEMANTICS, REACH)
    for stage in STAGES:
        assert STAGE_LABELS[stage] and STAGE_LABELS_JA[stage], stage


def test_an_unknown_stage_is_refused() -> None:
    try:
        AnalysisState(PARTIAL, reason="x", stage="whenever")
    except ValueError as error:
        assert "stage" in str(error), str(error)
    else:
        raise AssertionError("an invented stage was accepted")


def main() -> int:
    test_a_state_says_why_and_what_to_do()
    test_an_unknown_state_is_refused()
    test_one_check_that_did_not_run_makes_the_set_inconclusive()
    test_lint_says_which_checks_did_not_run_and_can_fail_on_it()
    test_a_result_that_is_not_checked_has_to_name_a_stage()
    test_checked_needs_no_stage()
    test_the_stage_is_in_the_line_and_in_the_data()
    test_the_five_stages_are_the_five_the_issue_names()
    test_an_unknown_stage_is_refused()
    print("analysis state checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
