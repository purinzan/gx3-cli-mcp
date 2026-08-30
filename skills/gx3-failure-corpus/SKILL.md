---
name: gx3-failure-corpus
description: Capture GX3 parser, xref, ladder-print, or lint failures as reusable regression fixtures before fixing gx3-cli.
---

# GX3 Failure Corpus

Use this skill when GX3 analysis fails or produces suspicious output that should
not regress. The goal is to turn one-off failures into repeatable checks.

## When To Capture

Capture a case when any of these happen:

- `doctor`, `xref`, `ladder-print`, `index-lite`, `lint`, or `parse-gaps` fails
  on a valid-looking project.
- A section title, device, comment, POU, or instruction is parsed incorrectly.
- FBD/ST/MIL or another GX Works3 format exposes a coverage gap.
- A CLI fix changes behavior on a real or public `.gx3` and needs a fixture.

## Capture

Record the failing input before changing parser logic.

```sh
gx3-cli failure-corpus capture --root <project.gx3-or-root> --case-id <short-name> --reason "<what failed>"
```

Use `--failed-command "<command>"` when a specific command exposed the problem.
Prefer `{root}` and `{reports_dir}` placeholders inside the command so replay
does not depend on the original local path. The runner replays only `gx3-cli`
or allowed `python -m gx3cli.*` commands, not arbitrary shell strings. Use
`--gx3 <file.gx3>` when preserving the original archive matters. The command
stores `case.json`, the extracted project, and optionally the source archive
under `.gx3_failures/cases/<case-id>/`.

## Verify The Loop

After capture and again after the fix:

```sh
gx3-cli failure-corpus run
python run_tests.py
```

The corpus runner checks format inventory, LDDB schema, `doctor`, `xref build`,
`ladder-print --list-sections`, and the captured failed command for every active
case. If a case contains FBD/ST/MIL program databases but no LDDB, keep it in
the corpus: the runner records it as an unsupported/non-ladder format and skips
ladder-only checks.

## Data Rules

- Do not commit private customer GX3 projects or generated reports from them.
- Public/open-source GX3 files can be committed only when license and project
  policy allow it.
- For private cases, keep the corpus local or in an approved private repo.

## PR Notes

Mention the captured case id, the original failure command, what changed, and
the successful `failure-corpus run` result. If a case cannot be shared, describe
the schema or parse signature without exposing ladder body data.
