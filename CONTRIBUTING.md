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

Windows is the primary platform. CI runs the suite on Windows, Linux, and
macOS with Python 3.10 and 3.12, so keep new code free of platform assumptions
such as hard-coded `\` path separators.

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install -e .
```

## If You Find A Source Code Problem

If you clone this repository and find a bug in the CLI, parser, docs, tests, or
agent guidance, please fix it in a pull request when you can. A good fix usually
has this shape:

1. Reproduce the problem with the smallest command possible.
2. If the failure came from a `.gx3`, capture it before changing code:

   ```powershell
   gx3-cli failure-corpus capture --root C:\path\to\project.gx3 --case-id short-name --reason "what failed" --failed-command "gx3-cli <command> --root {root}"
   ```

3. Fix the source code without committing private customer projects, extracted
   databases, generated reports, or machine-specific paths.
4. Add or update a focused test.
5. Run the checks below and include the results in the PR.

For private GX3 projects, do not attach the project or ladder body. Describe the
schema, format inventory, command output, or parse signature instead.

## Checks To Run

Run the same four commands CI runs. All of them must pass before you open a
pull request.

```powershell
python run_tests.py
python scripts\release_gate.py .
python -m build --wheel
python scripts\release_gate.py dist\gx3_cli_mcp-*.whl
```

On macOS or Linux, use forward slashes in those paths.

The release gate is intentionally strict. If it blocks a file, either remove the
generated/private artifact or explain why the rule itself needs to change.

## Pull Request Guidelines

- Keep one pull request to one concern. Small is much easier to merge.
- Add or update a test in `tests/` for any behavior change.
- Match the surrounding code style rather than reformatting nearby code.
- Update the relevant guide in `docs/` when you change user-facing behavior.
- Describe how you verified the change, and on what kind of project.
- Include the `failure-corpus` case id when one was captured.
- Mention whether the affected case is LD, FBD, ST, MIL, or another GX Works3
  format when that context matters.

## Where To Start

Issues labeled `good first issue` are scoped to be approachable without deep
knowledge of the GX Works3 file format. Output formatting, documentation, and
comment-keyword work are good entry points; the ladder parser and xref builder
are the parts that need the most context.

## Releasing

Two steps, in this order.

1. Bump `version` in `pyproject.toml` and the two `version` fields in
   `server.json` to the same value, then tag it. Pushing a `v*` tag builds and
   publishes to PyPI through Trusted Publishing; the workflow fails if the tag
   and the packaged version disagree.

   ```powershell
   git tag v0.1.3
   git push origin v0.1.3
   ```

2. Once the release is live on PyPI, publish the same version to the official
   MCP registry. The registry verifies PyPI ownership by looking for the
   `mcp-name: io.github.purinzan/gx3-cli-mcp` comment at the top of this
   repository's README, which is what PyPI shows as the package description, so
   step 1 has to land first.

   ```powershell
   mcp-publisher login github
   mcp-publisher publish
   ```

   `mcp-publisher login` opens a browser for GitHub authentication, so it has
   to be run by a person.

PyPI never reuses a version number. If a tag is pushed before the version is
bumped, the workflow stops before publishing. Bump and use the next number
rather than retrying the same one.

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
