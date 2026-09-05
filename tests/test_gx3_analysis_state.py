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
    NOT_EVALUATED,
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


def main() -> int:
    test_a_state_says_why_and_what_to_do()
    test_an_unknown_state_is_refused()
    test_one_check_that_did_not_run_makes_the_set_inconclusive()
    test_lint_says_which_checks_did_not_run_and_can_fail_on_it()
    print("analysis state checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
