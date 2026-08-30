# Contributing

This project is Windows-first because GX Works3 is normally used on Windows.
Mac/Linux support is welcome when it helps development or offline analysis, but
do not break the Windows CLI path.

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

## Checks

Run these from the repository root:

```powershell
python run_tests.py
python scripts\release_gate.py .
python -m build --wheel
python scripts\release_gate.py dist\gx3_cli_mcp-*.whl
```

The release gate is intentionally strict. If it blocks a file, either remove the
generated/private artifact or explain why the rule itself needs to change.

## Pull Request Notes

In the PR description, include:

- What failed and how to reproduce it.
- What changed in the code.
- Whether the case is LD, FBD, ST, MIL, or another GX Works3 format.
- The `failure-corpus` case id when one was captured.
- The commands you ran for validation.

For private GX3 projects, do not attach the project or ladder body. Describe the
schema, format inventory, command output, or parse signature instead.
