from __future__ import annotations

"""stdio MCP server for the GX3 analysis CLI.

Design goals:
- Project-read-only by default. Commands that modify a PLC project are NOT
  exposed.
- Typed tools for the main analysis commands so an agent gets a precise
  argument schema instead of a free-form command line.
- A restricted generic runner (`gx3_run_command`) as an escape hatch, still
  limited to an MCP-safe allowlist.
- Large outputs are capped and spilled to a file so a single response cannot
  blow past the client's context window.

It bundles no sample projects and reads only the extracted project it is
pointed at via `root`.
"""

import json
import os
import subprocess
import sys
import time
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from gx3cli.gx3_cli import COMMANDS, PACKAGE, cli_argv, python_env
from gx3cli.gx3_project_paths import LEGACY_ROOT_ENV, ROOT_ENV
from gx3cli.gx3_version import package_version, version_line


# We implement exactly one protocol version and always advertise it, rather than
# echoing whatever the client requested.
PROTOCOL_VERSION = "2025-06-18"
SERVER_INFO = {"name": "gx3-cli-mcp", "version": package_version()}

# Sent in the initialize result. MCP clients place this in the model's system
# prompt, which makes it the one place where answer formatting can be enforced
# for every conversation instead of hoping each tool description is read.
SERVER_INSTRUCTIONS = """\
This server analyzes a GX Works3 PLC project. When you report anything from it
to a user, follow these two rules without exception.

1. NEVER name a device without its device comment.

   Write the device and its comment together every time the device appears in
   your answer, including in lists, tables, and restatements of a condition:

       M100 (Synthetic origin-return request)
       X16 (Synthetic emergency stop input)

   A bare `M100` is not an acceptable answer to a human. The number alone is
   meaningless to the engineer reading it, and forcing them to look each one up
   defeats the purpose of this tool. Device comments come back from
   `gx3_xref_where_used`, `gx3_trace_device`, and `query-device`; if a device
   genuinely has no comment, write `M100 (no comment)` so the absence is
   explicit rather than ambiguous.

2. Explain ladder logic with the TXT ladder diagram, never with JSON.

   When the answer involves how a rung is wired, show the actual ladder text
   from `gx3_ladder_print`, or from `ladder-diagram` with `--format text`
   (its default) or `--format markdown`. Quote it verbatim inside a code block
   so the contacts and coils stay aligned.

   Do NOT paste `--format json` output, dictionaries, or any structured dump as
   the explanation of a circuit. JSON is for you to parse, not for a person to
   read. A PLC engineer reads ladder; showing them the JSON that describes the
   ladder is strictly worse than showing them the ladder.

   Use `gx3_ladder_print` with `section`, `pos_range`, or `device` to narrow the
   output to the circuit under discussion rather than dumping a whole program.

These rules apply to what you show the user. You may of course request and
parse JSON internally.
"""
MAX_TIMEOUT_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 90
MAX_OUTPUT_CHARS = 40000

# Commands that modify PLC project files. Some backing modules stay in the
# package because analyzers reuse their parsers, but these command names are
# never runnable through MCP.
PROJECT_MUTATING_COMMANDS = {"intermediate", "roundtrip", "instruction-edit-tests", "export-samples"}

# Commands that do not mutate a PLC project but create local demo artifacts.
# Keep these in the local CLI only so the MCP surface stays analysis-focused.
LOCAL_STATE_COMMANDS = {"synthetic-project"}
EXTERNAL_IO_COMMANDS = {"live-read"}

MCP_DISABLED_COMMANDS = PROJECT_MUTATING_COMMANDS | LOCAL_STATE_COMMANDS | EXTERNAL_IO_COMMANDS

# Query verbs implemented directly in gx3_cli (not in COMMANDS).
QUERY_COMMANDS = {
    "list",
    "context",
    "all-reports",
    "quick-device",
    "query-device",
    "query-comment",
    "query-external",
    "query-cycle",
    "device-map",
    "instruction-coverage",
    "device-coverage",
}

READ_ONLY_COMMANDS = (set(COMMANDS) | QUERY_COMMANDS) - MCP_DISABLED_COMMANDS


@dataclass(frozen=True)
class TypedTool:
    name: str
    command: str
    description: str
    input_schema: dict[str, Any]
    build_args: Callable[[dict[str, Any]], list[str]]


def _flag(arguments: dict[str, Any], key: str, flag: str, default: bool = False) -> list[str]:
    return [flag] if bool(arguments.get(key, default)) else []


def _opt(arguments: dict[str, Any], key: str, flag: str) -> list[str]:
    value = arguments.get(key)
    return [flag, str(value)] if value not in (None, "") else []


def _require(arguments: dict[str, Any], key: str) -> str:
    value = arguments.get(key)
    if value in (None, ""):
        raise ValueError(f"missing required argument: {key}")
    return str(value)


TYPED_TOOLS: list[TypedTool] = [
    TypedTool(
        name="gx3_trace_device",
        command="trace-device",
        description=(
            "Trace a device's ON/OFF/hold conditions from exact ladder topology. "
            "MC master-control zone conditions are folded into the enable logic, and "
            "the output warns on multi-OUT-coil devices and rows below a conditional jump. "
            "The output carries each device's comment: keep every device paired with its "
            "comment when you report the result, and show the rung itself with "
            "gx3_ladder_print rather than describing it in prose."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Target device, e.g. Y10, M4801, D200."},
                "root": {"type": "string", "description": "Extracted project folder."},
                "strict_logic": {"type": "boolean", "default": True, "description": "Derive AND/OR from topology (recommended)."},
                "compact": {"type": "boolean", "default": True, "description": "Evidence-focused compact summary."},
                "max_depth": {"type": "integer", "default": 4, "minimum": 1, "maximum": 12},
                "ja": {"type": "boolean", "default": False, "description": "Japanese headings."},
            },
            "required": ["device", "root"],
            "additionalProperties": False,
        },
        build_args=lambda a: [
            _require(a, "device"), "--root", _require(a, "root"),
            *_flag(a, "strict_logic", "--strict-logic", True),
            *_flag(a, "compact", "--compact", True),
            "--max-depth", str(int(a.get("max_depth", 4))),
            *_flag(a, "ja", "--ja", False),
        ],
    ),
    TypedTool(
        name="gx3_interlock_check",
        command="interlock-check",
        description=(
            "Static satisfiability check: can two coils' ON/enable conditions be true at the "
            "same time? A 'mutually-exclusive' verdict is sound; a 'simultaneous-possible' "
            "verdict returns a witness assignment but is not a reachability proof."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "device_a": {"type": "string", "description": "First coil/device."},
                "device_b": {"type": "string", "description": "Second coil/device."},
                "root": {"type": "string", "description": "Extracted project folder."},
                "max_depth": {"type": "integer", "default": 1, "minimum": 0, "maximum": 4, "description": "Upstream substitution depth."},
                "max_vars": {"type": "integer", "default": 24, "minimum": 1, "maximum": 40, "description": "Variable cap for the SAT search."},
            },
            "required": ["device_a", "device_b", "root"],
            "additionalProperties": False,
        },
        build_args=lambda a: [
            _require(a, "device_a"), _require(a, "device_b"), "--root", _require(a, "root"),
            "--max-depth", str(int(a.get("max_depth", 1))),
            "--max-vars", str(int(a.get("max_vars", 24))),
        ],
    ),
    TypedTool(
        name="gx3_xref_where_used",
        command="xref",
        description=(
            "Writers and readers of a device with POU name and real step. "
            "The first line carries the device comment; repeat it alongside the device in "
            "your answer instead of reporting a bare device number. "
            "Requires the xref DB: run `gx3-cli xref build --root <root>` once per project first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "device": {"type": "string", "description": "Device to look up."},
                "root": {"type": "string", "description": "Extracted project folder."},
            },
            "required": ["device", "root"],
            "additionalProperties": False,
        },
        build_args=lambda a: ["where-used", _require(a, "device"), "--root", _require(a, "root")],
    ),
    TypedTool(
        name="gx3_data_flow",
        command="data-flow",
        description=(
            "Build conservative argument-level source-to-destination value-flow edges. "
            "Known transfer and other classified instructions produce edges with "
            "argument indexes, ranges, widths, execution condition, and confidence. "
            "Unknown or partially parsed operations are returned as unresolved records; "
            "no guessed edge is emitted. Device comments are included when available."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Extracted project folder."},
                "device": {"type": "string", "description": "Only edges touching this device."},
                "opcode": {"type": "string", "description": "Only this exact instruction opcode."},
                "format": {"type": "string", "enum": ["text", "json", "csv"], "default": "json"},
            },
            "required": ["root"],
            "additionalProperties": False,
        },
        build_args=lambda a: [
            "--root", _require(a, "root"),
            "--format", str(a.get("format", "json")),
            *_opt(a, "device", "--device"),
            *_opt(a, "opcode", "--opcode"),
        ],
    ),
    TypedTool(
        name="gx3_lint",
        command="lint",
        description=(
            "Run static review checks over the whole project and return a per-check summary with finding "
            "counts by severity. Checks: duplicate coils, multi-writers, alarm quality, unused devices, "
            "contradictory comments, linked-device writes, and signed/width/division type problems. "
            "Reads the project; WRITES one CSV per check plus a JSON summary into the working directory. "
            "Findings are advisory: a duplicate coil may be a deliberate SET/RST pair, so report the "
            "severity and let the engineer judge. Needs the index and xref DBs for the full check set."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Extracted project folder."},
                "checks": {"type": "string", "description": "Comma-separated check names, or 'all' (default)."},
            },
            "required": ["root"],
            "additionalProperties": False,
        },
        build_args=lambda a: [_require(a, "root"), *_opt(a, "checks", "--checks")],
    ),
    TypedTool(
        name="gx3_dead_logic",
        command="dead-logic",
        description=(
            "Find logic that can never do anything: contacts that can never close, NC contacts on "
            "always-ON relays, coils and words written but never read, and SET latches with no matching "
            "RST. Returns findings grouped by kind with the device, its comment and where it appears. "
            "Reads the project; WRITES a CSV under the output directory. Devices refreshed from a "
            "network are excluded, since a remote station may be the real reader. Requires the xref DB."
        ),
        input_schema={
            "type": "object",
            "properties": {"root": {"type": "string", "description": "Extracted project folder."}},
            "required": ["root"],
            "additionalProperties": False,
        },
        build_args=lambda a: ["--root", _require(a, "root")],
    ),
    TypedTool(
        name="gx3_device_map",
        command="device-map",
        description=(
            "Report, per device type, which numbers are in use, how densely, and where the free gaps are. "
            "Use it to pick an unused range before adding devices, or to see how a project is laid out. "
            "Read-only; returns a table and writes nothing. Requires the index-lite DB: run "
            "`gx3-cli index-lite build --root <root>` once per project first."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Extracted project folder."},
                "types": {"type": "string", "description": "Comma-separated device types, e.g. M,D,W."},
                "min_free": {"type": "integer", "description": "Only report free gaps at least this large."},
            },
            "required": ["root"],
            "additionalProperties": False,
        },
        build_args=lambda a: ["--root", _require(a, "root"), *_opt(a, "types", "--types"), *_opt(a, "min_free", "--min-free")],
    ),
    TypedTool(
        name="gx3_alarm_map",
        command="alarm-map",
        description=(
            "Inventory every alarm and fault device with what triggers it, whether it latches, any timer "
            "setpoint, and what resets it. Answers \"what raises this alarm and how is it cleared\" without "
            "reading the ladder by hand. Reads the project; WRITES a CSV. An alarm with no reset condition "
            "is worth flagging to the user: it cannot be cleared without a power cycle. Requires the xref DB."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Extracted project folder."},
                "mode": {"type": "string", "enum": ["list", "show"], "default": "list"},
                "device": {"type": "string", "description": "Device to show (required when mode=show)."},
            },
            "required": ["root"],
            "additionalProperties": False,
        },
        build_args=lambda a: [
            str(a.get("mode", "list")),
            *([_require(a, "device")] if a.get("mode") == "show" else []),
            "--root", _require(a, "root"),
        ],
    ),
    TypedTool(
        name="gx3_semantic_diff",
        command="semantic-diff",
        description=(
            "Compare two versions of a project rung by rung and return what was added, removed or changed. "
            "Rungs are matched by their stable block GUID rather than by position, so inserting a rung "
            "does not report everything after it as changed. Use it to review what a revision actually "
            "did. Reads both projects; WRITES a detail CSV. Neither project is modified."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "old": {"type": "string", "description": "Old project folder or .gx3."},
                "new": {"type": "string", "description": "New project folder or .gx3."},
            },
            "required": ["old", "new"],
            "additionalProperties": False,
        },
        build_args=lambda a: [_require(a, "old"), _require(a, "new")],
    ),
    TypedTool(
        name="gx3_network_map",
        command="network-map",
        description=(
            "Map how this PLC is wired to everything outside it: IP addresses, CC-Link and SCON stations, "
            "and safety relationships, aggregated into one view. Use it to find which unit or remote "
            "station owns a device before assuming the program drives it. Reads the project and the comm "
            "detail CSVs; WRITES its own CSVs under the output directory. It does not communicate with "
            "any device -- everything is read from the project files."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "root": {"type": "string", "description": "Extracted project folder."},
                "prefix": {"type": "string", "description": "Communication prefix (optional)."},
            },
            "required": ["root"],
            "additionalProperties": False,
        },
        build_args=lambda a: ["--root", _require(a, "root"), *_opt(a, "prefix", "--prefix")],
    ),
    TypedTool(
        name="gx3_ladder_print",
        command="ladder-print",
        description=(
            "Render a program in GX Works3 print-text layout. This is the output to show a "
            "user when explaining how a circuit is wired: quote it verbatim in a code block, "
            "never a JSON dump of the same rung. Output can be large; pass 'output' to write "
            "it to a file. To emit only the circuit under discussion, use 'list_sections' to "
            "discover section titles, then filter with 'section', 'pos_range' (A-B), or "
            "'device'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "program": {"type": "string", "description": "Program/POU name or <hash>_LDDB.db."},
                "root": {"type": "string", "description": "Extracted project folder."},
                "output": {"type": "string", "description": "Optional output file path (-o); parent dirs are created."},
                "list_sections": {"type": "boolean", "description": "List section titles with pos range and rung count, then exit."},
                "section": {"type": "string", "description": "Render only sections whose title contains this text."},
                "pos_range": {"type": "string", "description": "Render only rows whose step pos is within A-B (inclusive)."},
                "device": {"type": "string", "description": "Render only rungs referencing this device (any role), plus their section title."},
            },
            "required": ["program", "root"],
            "additionalProperties": False,
        },
        build_args=lambda a: [
            _require(a, "program"), "--root", _require(a, "root"),
            *_opt(a, "output", "-o"),
            *_flag(a, "list_sections", "--list-sections"),
            *_opt(a, "section", "--section"),
            *_opt(a, "pos_range", "--pos-range"),
            *_opt(a, "device", "--device"),
        ],
    ),
]

TYPED_BY_NAME = {tool.name: tool for tool in TYPED_TOOLS}


# --------------------------------------------------------------------------
# JSON-RPC plumbing
# --------------------------------------------------------------------------


def send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def response(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def error_response(message_id: Any, code: int, message: str, data: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": message_id, "error": error}


def text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


# --------------------------------------------------------------------------
# Tool listing
# --------------------------------------------------------------------------


def command_summary() -> str:
    rows = [
        f"{name}: {COMMANDS[name].summary}" if name in COMMANDS else name
        for name in sorted(READ_ONLY_COMMANDS)
    ]
    header = (
        "Project-read-only GX3 analysis commands (project-mutating commands "
        "and local demo-generation commands are not available through this server):"
    )
    return header + "\n" + "\n".join(f"  {row}" for row in rows)


GENERIC_TOOL = {
    "name": "gx3_run_command",
    "description": (
        "Escape hatch: run any MCP-allowed, project-read-only GX3 CLI command "
        "with explicit arguments. Project-mutating commands and local demo "
        "generation commands are rejected. "
        "Prefer the typed tools above when one fits."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "MCP-allowed GX3 CLI command name."},
            "args": {"type": "array", "items": {"type": "string"}, "default": []},
            "root": {"type": "string", "description": "Optional extracted project root."},
            "timeout_seconds": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT_SECONDS, "default": DEFAULT_TIMEOUT_SECONDS},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
}

LIST_TOOL = {
    "name": "gx3_list_commands",
    "description": (
        "List every project-read-only GX3 CLI command this server will run, with a one-line summary "
        "of each. Read-only; touches no project. Call this before gx3_run_command when no typed tool "
        "fits, to see what is actually available rather than guessing a command name."
    ),
    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
}


def list_tools_result() -> dict[str, Any]:
    tools = [LIST_TOOL]
    tools.extend(
        {"name": tool.name, "description": tool.description, "inputSchema": tool.input_schema}
        for tool in TYPED_TOOLS
    )
    tools.append(GENERIC_TOOL)
    return {"tools": tools}


# --------------------------------------------------------------------------
# Command execution
# --------------------------------------------------------------------------


def _spill(text: str, command: str) -> str:
    spill_dir = Path(os.environ.get("GX3_MCP_OUTPUT_DIR") or (Path(tempfile.gettempdir()) / "gx3-mcp-output"))
    spill_dir.mkdir(parents=True, exist_ok=True)
    path = spill_dir / f"{command}-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.txt"
    path.write_text(text, encoding="utf-8")
    head = text[:MAX_OUTPUT_CHARS]
    return (
        head
        + f"\n\n... [output truncated: {len(text)} chars total; full output written to {path}. "
        "For a stable location, re-run the command with an explicit output path (e.g. -o).]"
    )


def run_cli(command: str, args: list[str], root: str | None, timeout_seconds: int) -> dict[str, Any]:
    if command in PROJECT_MUTATING_COMMANDS:
        raise ValueError(f"command '{command}' modifies the project and is disabled on the MCP server")
    if command in LOCAL_STATE_COMMANDS:
        raise ValueError(f"command '{command}' creates local demo artifacts and is disabled on the MCP server")
    if command in EXTERNAL_IO_COMMANDS:
        raise ValueError(f"command '{command}' connects to external equipment and is disabled on the MCP server")
    if command not in READ_ONLY_COMMANDS:
        raise ValueError(f"unknown or non-allowed command: {command}")

    env = python_env(root)  # sets PYTHONPATH for the package + PROJECT_ROOT/GX3_ROOT
    env.setdefault("PYTHONIOENCODING", "utf-8")
    if root:
        root_path = str(Path(str(root)).expanduser())
        env[ROOT_ENV] = root_path
        env[LEGACY_ROOT_ENV] = root_path

    completed = subprocess.run(
        cli_argv([command, *args]),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    body = "\n".join(
        part
        for part in [f"exit_code={completed.returncode}", completed.stdout.strip(), completed.stderr.strip()]
        if part
    )
    if len(body) > MAX_OUTPUT_CHARS:
        body = _spill(body, command)
    return text_result(body, is_error=completed.returncode != 0)


def run_generic(arguments: dict[str, Any]) -> dict[str, Any]:
    command = str(arguments.get("command", "")).strip()
    raw_args = arguments.get("args", []) or []
    if not isinstance(raw_args, list) or not all(isinstance(item, str) for item in raw_args):
        raise ValueError("args must be an array of strings")
    timeout_seconds = max(1, min(MAX_TIMEOUT_SECONDS, int(arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))))
    return run_cli(command, list(raw_args), arguments.get("root"), timeout_seconds)


def run_typed(tool: TypedTool, arguments: dict[str, Any]) -> dict[str, Any]:
    args = tool.build_args(arguments)
    timeout_seconds = max(1, min(MAX_TIMEOUT_SECONDS, int(arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))))
    return run_cli(tool.command, args, arguments.get("root"), timeout_seconds)


def call_tool(params: dict[str, Any]) -> dict[str, Any]:
    name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise ValueError("tool arguments must be an object")
    if name == "gx3_list_commands":
        return text_result(command_summary())
    if name == "gx3_run_command":
        return run_generic(arguments)
    tool = TYPED_BY_NAME.get(str(name))
    if tool is not None:
        return run_typed(tool, arguments)
    raise ValueError(f"unknown tool: {name}")


def handle(message: dict[str, Any]) -> dict[str, Any] | None:
    message_id = message.get("id")
    method = message.get("method")
    params = message.get("params") or {}
    if method == "initialize":
        return response(
            message_id,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": SERVER_INSTRUCTIONS,
            },
        )
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return response(message_id, {})
    if method == "tools/list":
        return response(message_id, list_tools_result())
    if method == "tools/call":
        try:
            return response(message_id, call_tool(params))
        except Exception as exc:  # MCP tool errors are returned as tool results, not JSON-RPC errors.
            return response(message_id, text_result(str(exc), is_error=True))
    return error_response(message_id, -32601, f"method not found: {method}")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in {"--version", "-V"}:
        print(version_line("gx3-mcp-server"))
        return 0
    # MCP clients launch this server without PYTHONIOENCODING, so on Windows
    # stdout defaults to cp1252 and any non-Latin-1 device comment would crash
    # the process mid-response. Force UTF-8 on both streams.
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            send(error_response(None, -32700, "parse error", str(exc)))
            continue
        if not isinstance(message, dict):
            send(error_response(None, -32600, "invalid request"))
            continue
        try:
            result = handle(message)
        except Exception as exc:
            result = error_response(message.get("id"), -32603, "internal error", str(exc))
        if result is not None and "id" in message:
            send(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
