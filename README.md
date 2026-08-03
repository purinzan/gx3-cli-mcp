# gx3-cli-mcp

Local GX Works3 project analysis for engineers and AI agents.

`gx3-cli-mcp` provides a Windows-first CLI and stdio MCP server for inspecting
GX Works3 (`.gx3`) projects on your own machine. It helps you search devices,
comments, cross references, ladder conditions, communication boundaries, and
static review signals without modifying the source project.

日本語: GX Works3 (`.gx3`) プロジェクトをローカルで読み取り解析し、AI
エージェントからも使える CLI / MCP サーバーです。プロジェクトを書き換えず、
デバイス、コメント、xref、ラダー根拠、通信境界を確認するための道具です。

This is an unofficial, independent tool. It is not endorsed by Mitsubishi
Electric.

## What You Can Do

- Find where a device is used and whether it is read, written, or referenced.
- Search device comments such as start, stop, alarm, cycle, step, or manual
  operation keywords.
- Trace upstream conditions for a coil and print nearby ladder evidence.
- Build xref and SQLite indexes so AI agents can answer from structured facts.
- Review static risks such as duplicate coils, multi-writers, dead logic, and
  interlock possibilities.
- Inspect external inputs, HMI/communication boundaries, IP maps, link maps,
  timing candidates, and project-wide summaries.

## What It Does Not Do

- It does not include real customer or production PLC projects.
- It does not send project data to an online service by itself.
- It does not provide a GUI in this release.
- It does not require a license token or paid-plan token.
- The MCP server does not expose project-mutating commands.

## Install

```powershell
python -m pip install git+https://github.com/purinzan/gx3-cli-mcp.git
gx3-cli --version
gx3-mcp-server --version
```

For local source checkout:

```powershell
python -m pip install -e .
```

## First Analysis

Run these three commands first for a real project:

```powershell
gx3-cli doctor --root C:\path\to\project.gx3
gx3-cli index-lite build --root C:\path\to\project.gx3
gx3-cli xref build --root C:\path\to\project.gx3
```

Then inspect a device:

```powershell
gx3-cli query-device M100 --root C:\path\to\project.gx3
gx3-cli xref where-used M100 --root C:\path\to\project.gx3
gx3-cli trace-device M100 --root C:\path\to\project.gx3 --strict-logic --compact
```

When you pass a `.gx3` file, the tool extracts it into
`.gx3_cache\<sha256>\` and analyzes that local cache.

## Common Tasks

| Goal | Command |
|---|---|
| Check project readiness | `gx3-cli doctor --root project.gx3` |
| Build the search index | `gx3-cli index-lite build --root project.gx3` |
| Build cross references | `gx3-cli xref build --root project.gx3` |
| Look up one device | `gx3-cli query-device M100 --root project.gx3` |
| Search by comment text | `gx3-cli query-comment "起動" --root project.gx3` |
| Show external/HMI/communication boundary devices | `gx3-cli query-external --root project.gx3` |
| Find cycle, step, or state candidates | `gx3-cli query-cycle --root project.gx3` |
| Show used/free device ranges | `gx3-cli device-map --root project.gx3 --types M,D,W --min-free 100` |
| Show writers/readers | `gx3-cli xref where-used M100 --root project.gx3` |
| Trace coil conditions | `gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact` |
| Print ladder evidence | `gx3-cli ladder-print <PROGRAM_OR_LDDB> --root project.gx3 --device M100` |
| Check static interlock possibility | `gx3-cli interlock-check M100 M200 --root project.gx3` |
| Run static review checks | `gx3-cli lint project.gx3` |
| Create a support summary | `gx3-cli support-bundle --root project.gx3 -o support.zip` |

Use the indexed commands above for normal lookup and discovery. Avoid starting
AI workflows with raw text search over extracted GX3 files; GX Works3 projects
contain binary/database files, and the CLI preserves device, comment, POU, role,
and step context.

## Use With MCP

The MCP server is intended for AI clients that support stdio MCP servers.

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

If your client can resolve console scripts from PATH:

```json
{
  "mcpServers": {
    "gx3": {
      "command": "gx3-mcp-server"
    }
  }
}
```

The MCP server exposes read-only analysis tools and a restricted command
runner. Synthetic demo generation is local CLI-only.

## Demo Project

Use a synthetic project for screenshots, tutorials, and first-time tests.

```powershell
gx3-cli synthetic-project demo.gx3 --overwrite
gx3-cli doctor --root demo.gx3
gx3-cli trace-device M100 --root demo.gx3 --strict-logic --compact
```

## Data And Safety

- Project files stay on your machine unless you pass outputs to another tool.
- Some commands create local files such as SQLite indexes, CSV reports, ZIP
  support bundles, or Markdown summaries.
- Analysis output is advisory. Verify findings in GX Works3 and through your
  own safety/quality process before changing real equipment.
- The tool is not a substitute for PLC validation, machine safety review, or
  official engineering software.

## Documentation

Recommended reading:

1. [User manual (JA)](docs/USER_MANUAL_JA.md): Japanese install and usage guide.
2. [Agent usage guide (JA)](docs/AGENT_USAGE_JA.md): how AI agents should use the indexed tools.
3. [Security note (JA)](docs/SECURITY_JA.md): local data handling and read-only MCP policy.
4. [Validation matrix (JA)](docs/VALIDATION_MATRIX.md): verified scope and limitations.
5. [File usage guide (JA)](docs/FILE_USAGE_GUIDE_JA.md): repository file map for agents and contributors.

MCP configuration examples:

- [MCP client config](docs/mcp_client_config.json): robust `python -m gx3cli.gx3_mcp_server` launch.
- [MCP client config, console script](docs/mcp_client_config_console_script.json): direct `gx3-mcp-server` launch when PATH is reliable.

Distribution terms are defined in [LICENSE.txt](LICENSE.txt).

## For Contributors

Before changing or publishing this project, run the same checks used by CI:

```powershell
python run_tests.py
python scripts\release_gate.py .
python -m build --wheel
python scripts\release_gate.py dist\gx3_cli_mcp-*.whl
```
