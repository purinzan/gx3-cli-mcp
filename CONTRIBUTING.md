# Contributing

Thanks for looking at `gx3-cli-mcp`. Bug reports, questions, and small pull
requests are all welcome.

日本語で構いません。Issue も PR も日本語で結構です。

## Before You Start

This project is **source-available proprietary software**, not open source. See
[LICENSE.txt](LICENSE.txt) and the license summary in the
[README](README.md#license-in-plain-words).

By opening a pull request you confirm that:

1. You wrote the contribution yourself, or you have the right to submit it.
2. You grant the copyright holder (purinzan) permission to use, modify, and
   distribute your contribution as part of this project under its current
   license and any later license this project adopts.

There is no CLA to sign. Opening the pull request is the confirmation.

## The Hard Rule: No Project Data

This tool reads real PLC projects, so the repository must never contain any.
Never commit:

- `.gx3` / `.gtx` project files or extracted project directories
- generated indexes, CSV reports, or support bundles
- customer, site, or equipment names
- machine IP addresses or local user paths

`.gitignore` and `MANIFEST.in` block the common cases, and
`scripts/release_gate.py` fails CI if anything slips through. Run the gate
before you push.

If you need a project to test against, generate a synthetic one:

```powershell
gx3-cli synthetic-project demo.gx3 --overwrite
gx3-cli doctor --root demo.gx3
```

## Development Setup

Windows is the primary platform, and CI runs on Windows with Python 3.10 and
3.12.

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

## Checks To Run

Run the same four commands CI runs. All of them must pass before you open a
pull request.

```powershell
python run_tests.py
python scripts\release_gate.py .
python -m build --wheel
python scripts\release_gate.py dist\gx3_cli_mcp-*.whl
```

On macOS or Linux, use forward slashes in those paths. The test suite runs
there too, but Windows is what CI verifies.

## Pull Request Guidelines

- Keep one pull request to one concern. Small is much easier to merge.
- Add or update a test in `tests/` for any behavior change.
- Match the surrounding code style rather than reformatting nearby code.
- Update the relevant guide in `docs/` when you change user-facing behavior.
- Describe how you verified the change, and on what kind of project.

## Where To Start

Issues labeled `good first issue` are scoped to be approachable without deep
knowledge of the GX Works3 file format. Output formatting, documentation, and
comment-keyword work are good entry points; the ladder parser and xref builder
are the parts that need the most context.

## Reporting Bugs

A useful report includes:

- the exact command you ran and the full error output
- `gx3-cli --version` and `python --version`
- your OS and GX Works3 version
- whether it reproduces on a synthetic project (`gx3-cli synthetic-project`)

**Never paste real project data, device comments, customer names, or addresses
into an issue.** Reduce the problem to a synthetic project, or describe the
shape of the data instead of pasting it.

## Security

For anything involving data handling or a potential leak, see
[docs/SECURITY_JA.md](docs/SECURITY_JA.md) and report privately rather than in a
public issue.
