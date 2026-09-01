# gx3-cli-mcp User Manual

`gx3-cli-mcp` is a local, read-only CLI and MCP server for analyzing GX Works3
(`.gx3`) projects. It helps you inspect devices, comments, cross references,
ladder evidence, communication boundaries, and current PLC values that you
explicitly request.

## What It Does

- Finds where a device is used.
- Searches device comments for startup, stop, alarm, manual, cycle, and state
  signals.
- Traces coil enable conditions and shows nearby ladder evidence.
- Exposes read-only analysis tools to AI agents through MCP.
- Runs static checks such as duplicate coils, multi-writers, dead logic, and
  interlock possibilities.
- Organizes external input, HMI, communication, IP, link, and timing candidates.
- Reads current PLC values only when you explicitly provide the PLC address,
  port, device, and count.

## What It Does Not Do

- It does not edit GX Works3 projects.
- It does not prove machine safety from static analysis alone.
- It does not guarantee every GX Works3 version, PLC model, or program format.
- It does not upload project data unless you explicitly use another service.
- It does not infer a PLC connection from a `.gx3` file and connect by itself.

## Install

```powershell
python -m pip install gx3-cli-mcp
gx3-cli --version
gx3-mcp-server --version
```

For the latest source checkout:

```powershell
python -m pip install git+https://github.com/purinzan/gx3-cli-mcp.git
```

## First Three Commands

Run these first on a real project:

```powershell
gx3-cli doctor --root C:\path\to\project.gx3
gx3-cli index-lite build --root C:\path\to\project.gx3
gx3-cli xref build --root C:\path\to\project.gx3
```

When `--root` points at a `.gx3` file, the CLI extracts it into a local
`.gx3_cache\<sha256>\` folder and analyzes that cache.

## Common Tasks

| Goal | Command |
|---|---|
| Check project readiness | `gx3-cli doctor --root project.gx3` |
| Build the search index | `gx3-cli index-lite build --root project.gx3` |
| Build cross references | `gx3-cli xref build --root project.gx3` |
| Look up one device | `gx3-cli query-device M100 --root project.gx3` |
| Search comments | `gx3-cli query-comment "start" --root project.gx3` |
| Search with synonyms | `gx3-cli query-comment alarm --root project.gx3 --expand-synonyms` |
| Find writers/readers | `gx3-cli xref where-used M100 --root project.gx3` |
| Emit JSON for scripts | `gx3-cli query-device M100 --root project.gx3 --json` |
| Trace coil conditions | `gx3-cli trace-device M100 --root project.gx3 --strict-logic --compact` |
| Print ladder evidence | `gx3-cli ladder-print MAIN --root project.gx3 --device M100` |
| Run static checks | `gx3-cli lint project.gx3` |
| Create a support bundle | `gx3-cli support-bundle --root project.gx3 -o support.zip` |

## Live Read

`live-read` uses MC Protocol/SLMP 3E binary batch read. It reads the current
value only from the PLC endpoint and device range you explicitly provide.

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word --dry-run
gx3-cli live-read --ip <PLC_IP> --port 5000 --device D1000 --count 10 --type word
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json
```

Save JSON and pass it to `ladder-print` to overlay current values on cited
ladder rows:

```powershell
gx3-cli live-read --ip <PLC_IP> --port 5000 --device M100 --count 16 --type bit --format json -o live.json
gx3-cli ladder-print MAIN --root project.gx3 --device M100 --live-values live.json
```

## MCP

Use the MCP server from AI clients that support stdio MCP servers:

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
runner. Demo project generation and `live-read` remain local CLI-only.
