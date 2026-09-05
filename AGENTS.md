# Agent Guidance

Use `gx3-cli` structured commands before raw searches when analyzing GX Works3
projects. Keep project files local unless the user explicitly authorizes sharing
them.

Use repo skills when the task matches them:

- `skills/gx3-existing-project-audit/SKILL.md` for read-only analysis of existing
  `.gx3` projects, devices, ladders, and project risks.
- `skills/gx3-failure-corpus/SKILL.md` when GX3 parsing, xref, ladder-print, or
  lint fails in a way that should become a regression fixture.

Before finishing code changes, run `python run_tests.py` from the repository
root and report any remaining failures.

Passing tests are not the finish line. Before opening a pull request, work
through `docs/REVIEW_QUESTIONS_JA.md` and apply its questions to your own
change. It exists because every bug in this project that was shaped like a
success -- exit code 0, `checked`, zero findings -- was found by reading the
code against its own stated contract, and never by running the tests that came
with it. Record in the pull request what the questions left standing.

Before preparing a pull request or changing source code for an external
contributor workflow, read `CONTRIBUTING.md` and follow its Windows-first PR and
data-safety rules.

When source code is the problem, do not stop at a workaround. Reproduce the
failure, capture a `.gx3` case with `failure-corpus` when applicable, make a
focused code fix, run the checks, and prepare a pull request. Keep Windows CLI
behavior as the primary target; preserve Mac/Linux support when practical.
