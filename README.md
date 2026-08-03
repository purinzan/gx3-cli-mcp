# gx3-cli-mcp

`gx3-cli-mcp` is a local-first analysis CLI and stdio MCP server for
GX Works3 (`.gx3`) projects.

It helps AI agents and engineers inspect PLC project structure, device usage,
cross references, ladder conditions, communication boundaries, and static
quality signals without modifying the source project.

This is an unofficial, independent tool. It is not endorsed by Mitsubishi
Electric.

## Scope

Included:

- `gx3-mcp-server`: stdio MCP server for AI clients.
- `gx3-cli`: local GX3 analysis commands used by the MCP server.
- project-read-only analysis tools: trace, xref, ladder print, lint, reports,
  link maps, network maps, timing drafts, support bundles, and more.
- synthetic demo generation from the local CLI.

Not included:

- real customer or production PLC project files.
- a GUI/desktop shell.
- paid-plan, entitlement, or license-token checks.
- MCP access to project-mutating commands.

## Quick Start

Install from GitHub:

```powershell
python -m pip install git+https://github.com/purinzan/gx3-cli-mcp.git
gx3-cli --version
gx3-mcp-server --version
```

For local development:

```powershell
python -m pip install -e .
gx3-cli --version
gx3-mcp-server --version
```

Run a first local analysis:

```powershell
gx3-cli doctor --root C:\path\to\project.gx3
gx3-cli index-lite build --root C:\path\to\project.gx3
gx3-cli xref build --root C:\path\to\project.gx3
gx3-cli trace-device M100 --root C:\path\to\project.gx3 --strict-logic --compact
```

For public demos and tests, generate a synthetic fixture instead of using a
real project:

```powershell
gx3-cli synthetic-project demo.gx3 --overwrite
gx3-cli doctor --root demo.gx3
gx3-cli trace-device M100 --root demo.gx3 --strict-logic --compact
```

## MCP Server

The MCP server exposes project-read-only analysis tools. It does not write GX
Works3 project files, and synthetic demo generation is local CLI-only.

```json
{
  "mcpServers": {
    "gx3": {
      "command": "python",
      "args": ["-m", "gx3cli.gx3_mcp_server"]
    }
  }
}
```

If your MCP client can resolve Python console scripts from PATH, this shorter
form also works:

```json
{
  "mcpServers": {
    "gx3": {
      "command": "gx3-mcp-server"
    }
  }
}
```

No license token is required for the MCP server in this release.

## Useful Commands

SQLite-first workflow:

```powershell
gx3-cli doctor --root project.gx3
gx3-cli index-lite build --root project.gx3
gx3-cli xref build --root project.gx3
gx3-cli query-device M100 --root project.gx3
gx3-cli query-comment "起動" --root project.gx3
gx3-cli query-external --root project.gx3
gx3-cli query-cycle --root project.gx3
gx3-cli device-map --root project.gx3 --types M,D,W --min-free 100
gx3-cli xref where-used M100 --root project.gx3
```

Use the SQLite-backed commands above for normal lookup and discovery. Do not
start agent workflows by raw text-searching extracted project files; GX3
projects contain binary/database files and the indexed commands preserve
device, comment, POU, role, and cross-reference context.

Evidence commands:

```powershell
gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact
gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100
gx3-cli interlock-check M100 M200 --root project.gx3
gx3-cli lint project.gx3
gx3-cli reliability-report --root project.gx3 -o reliability.md
gx3-cli support-bundle --root project.gx3 -o support.zip
```

Passing `--root project.gx3` extracts the archive into `.gx3_cache\<sha256>\`
and runs analysis from that cache.

## Release Check

Run the source-tree release gate before pushing. Build a wheel, then run the
gate on the built artifact before publishing:

```powershell
python run_tests.py
python scripts\release_gate.py .
python -m build --wheel
python scripts\release_gate.py dist\gx3_cli_mcp-*.whl
```

## Documentation

Recommended reading order:

1. [User manual (JA)](docs/USER_MANUAL_JA.md): install, MCP setup, and common commands.
2. [Agent usage guide (JA)](docs/AGENT_USAGE_JA.md): how Codex/Claude/Cursor should operate this tool.
3. [Security note (JA)](docs/SECURITY_JA.md): local data handling and read-only MCP policy.
4. [Validation matrix (JA)](docs/VALIDATION_MATRIX.md): verified scope and claims that must not be overstated.
5. [File usage guide (JA)](docs/FILE_USAGE_GUIDE_JA.md): repository file map for agents and contributors.

Distribution terms are defined in [LICENSE.txt](LICENSE.txt). Internal drafts
and publication planning notes are intentionally not part of the public user
documentation.

MCP configuration examples:

- [MCP client config](docs/mcp_client_config.json): robust `python -m gx3cli.gx3_mcp_server` launch.
- [MCP client config, console script](docs/mcp_client_config_console_script.json): direct `gx3-mcp-server` launch when PATH is reliable.
