from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gx3cli.gx3_ladder_logic import (
    FlowElement,
    enable_logic_for_output,
    logic_key,
    logic_to_text,
    normalize_device,
    output_elements_for,
)
from gx3cli.review_gx3_project import LadderRow, load_comments_for_root, load_rows
from gx3cli.gx3_project_paths import default_output_prefix, default_project_root


BASE_DIR = Path(__file__).resolve().parent

IEC_RESERVED = {
    "ABS",
    "ACTION",
    "AND",
    "ARRAY",
    "BOOL",
    "BY",
    "CASE",
    "CONFIGURATION",
    "DO",
    "ELSE",
    "ELSIF",
    "END_ACTION",
    "END_CASE",
    "END_CONFIGURATION",
    "END_FOR",
    "END_FUNCTION",
    "END_FUNCTION_BLOCK",
    "END_IF",
    "END_PROGRAM",
    "END_REPEAT",
    "END_RESOURCE",
    "END_STRUCT",
    "END_TYPE",
    "END_VAR",
    "END_WHILE",
    "FALSE",
    "FOR",
    "FUNCTION",
    "FUNCTION_BLOCK",
    "IF",
    "MOD",
    "NOT",
    "OF",
    "OR",
    "PROGRAM",
    "REPEAT",
    "RESOURCE",
    "RETAIN",
    "RETURN",
    "STRUCT",
    "THEN",
    "TO",
    "TRUE",
    "TYPE",
    "UNTIL",
    "VAR",
    "VAR_INPUT",
    "VAR_OUTPUT",
    "VAR_IN_OUT",
    "WHILE",
    "WITH",
    "XOR",
}


@dataclass
class StBuildContext:
    identifiers: dict[str, str] = field(default_factory=dict)
    predicate_placeholders: dict[str, str] = field(default_factory=dict)
    predicate_comments: dict[str, str] = field(default_factory=dict)


def ascii_comment(text: str) -> str:
    return (text or "").replace("*)", "* )").encode("ascii", errors="replace").decode("ascii")


def st_identifier(raw: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_]", "_", raw.strip().upper())
    if not value:
        value = "GX_VALUE"
    if value[0].isdigit():
        value = "GX_" + value
    if value in IEC_RESERVED:
        value = "GX_" + value
    return value


def identifier_for(ctx: StBuildContext, raw: str) -> str:
    key = raw.strip().upper()
    ident = ctx.identifiers.get(key)
    if ident:
        return ident
    base = st_identifier(key)
    used = set(ctx.identifiers.values())
    ident = base
    index = 2
    while ident in used:
        ident = f"{base}_{index}"
        index += 1
    ctx.identifiers[key] = ident
    return ident


def placeholder_for(ctx: StBuildContext, node: dict[str, Any]) -> str:
    key = logic_key(node)
    existing = ctx.predicate_placeholders.get(key)
    if existing:
        return existing
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:10].upper()
    ident = identifier_for(ctx, f"PRED_{digest}")
    ctx.predicate_placeholders[key] = ident
    ctx.predicate_comments[ident] = logic_to_text(node)
    return ident


def logic_to_st(node: dict[str, Any], ctx: StBuildContext) -> str:
    op = node.get("op")
    if op == "true":
        return "TRUE"
    if op == "false":
        return "FALSE"
    if op == "contact":
        device = str(node.get("raw_device") or node.get("device") or "")
        ident = identifier_for(ctx, device)
        if node.get("role") == "b":
            return f"(NOT {ident})"
        return ident
    if op == "predicate":
        return placeholder_for(ctx, node)
    if op == "unknown":
        return placeholder_for(ctx, node)
    if op in {"and", "or"}:
        joiner = f" {op.upper()} "
        args = [logic_to_st(child, ctx) for child in node.get("args", [])]
        if not args:
            return "TRUE" if op == "and" else "FALSE"
        return "(" + joiner.join(args) + ")"
    return placeholder_for(ctx, node)


def output_device(output: FlowElement) -> str:
    if output.devices:
        return output.devices[0].device
    return "UNKNOWN_OUTPUT"


def output_role_text(output: FlowElement) -> str:
    if output.role == "c":
        return "coil"
    return output.role.replace("__16", "")


def rows_for_device(rows: list[LadderRow], device: str) -> list[tuple[LadderRow, list[FlowElement]]]:
    target = normalize_device(device)
    out: list[tuple[LadderRow, list[FlowElement]]] = []
    for row in rows:
        outputs = output_elements_for(row, target)
        if outputs:
            out.append((row, outputs))
    return out


def build_st_payload(root: Path, device: str, program_name: str | None = None) -> dict[str, Any]:
    comments = load_comments_for_root(root)
    del comments  # MATIEC export intentionally keeps the ST source compiler-oriented.
    rows = load_rows(root, {})
    target = normalize_device(device)
    matches = rows_for_device(rows, target)
    ctx = StBuildContext()
    program = st_identifier(program_name or f"{default_output_prefix('matiec').upper()}_{target}")

    row_records: list[dict[str, Any]] = []
    enable_index = 1
    statements: list[dict[str, str]] = []
    for row, outputs in matches:
        for output in outputs:
            logic = enable_logic_for_output(row, output)
            expr = logic_to_st(logic, ctx)
            target_device = output_device(output)
            target_ident = identifier_for(ctx, target_device)
            enable_ident = identifier_for(ctx, f"{target_device}_ENABLE_{enable_index:03d}")
            enable_index += 1
            role = output.role
            row_id = f"{row.lddb}:{row.pos}"
            row_records.append(
                {
                    "row_id": row_id,
                    "title": row.title,
                    "output_device": target_device,
                    "output_role": role,
                    "output_position": f"{output.x},{output.y}",
                    "enable_var": enable_ident,
                    "enable_logic_text": logic_to_text(logic),
                    "st_expression": expr,
                }
            )
            statements.append(
                {
                    "comment": f"{row_id} {output_role_text(output)} {target_device}",
                    "enable_var": enable_ident,
                    "enable_expr": expr,
                    "role": role,
                    "target": target_ident,
                }
            )

    variables = sorted(ctx.identifiers.values())
    return {
        "source_root": str(root),
        "target_device": target,
        "program_name": program,
        "row_count": len(row_records),
        "rows": row_records,
        "variables": variables,
        "predicate_comments": ctx.predicate_comments,
        "statements": statements,
    }


def format_st(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        "(*",
        "Generated from ladder data for MATIEC.",
        "MATIEC does not render Ladder Diagram images; this file is Structured Text",
        "for syntax/logic checking of the extracted rung enable conditions.",
        f"source_root: {ascii_comment(str(payload['source_root']))}",
        f"target_device: {ascii_comment(str(payload['target_device']))}",
        "*)",
        f"PROGRAM {payload['program_name']}",
        "VAR",
    ]
    for variable in payload["variables"]:
        comment = payload.get("predicate_comments", {}).get(variable, "")
        if comment:
            lines.append(f"    {variable} : BOOL; (* {ascii_comment(comment)} *)")
        else:
            lines.append(f"    {variable} : BOOL;")
    lines.extend(["END_VAR", ""])

    for statement in payload["statements"]:
        lines.append(f"(* {ascii_comment(statement['comment'])} *)")
        lines.append(f"{statement['enable_var']} := {statement['enable_expr']};")
        role = statement["role"]
        if role == "c":
            lines.append(f"{statement['target']} := {statement['enable_var']};")
        elif role == "SET":
            lines.append(f"IF {statement['enable_var']} THEN")
            lines.append(f"    {statement['target']} := TRUE;")
            lines.append("END_IF;")
        elif role == "RST":
            lines.append(f"IF {statement['enable_var']} THEN")
            lines.append(f"    {statement['target']} := FALSE;")
            lines.append("END_IF;")
        else:
            lines.append(f"(* Unsupported output role {ascii_comment(role)} is exposed as enable variable only. *)")
        lines.append("")
    lines.append(f"END_PROGRAM")
    return "\n".join(lines)


def resolve_matiec_tool(tool: str) -> str | None:
    if any(separator in tool for separator in ("/", "\\")):
        path = Path(tool)
        if path.exists():
            return str(path)
    resolved = shutil.which(tool)
    if not resolved:
        local_candidate = BASE_DIR / "_tools" / "src" / "matiec-master" / f"{tool}.exe"
        if local_candidate.exists():
            resolved = str(local_candidate)
    return resolved


def run_matiec(tool: str, st_path: Path) -> tuple[int, str, str, str]:
    resolved = resolve_matiec_tool(tool)
    if not resolved:
        return 127, "", f"{tool} not found on PATH or local _tools/src/matiec-master", tool
    try:
        completed = subprocess.run(
            [resolved, str(st_path)],
            cwd=st_path.parent,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
    except PermissionError as exc:
        return 126, "", f"permission denied while executing {resolved}: {exc}", resolved
    except OSError as exc:
        return 126, "", f"failed to execute {resolved}: {exc}", resolved
    return int(completed.returncode), completed.stdout, completed.stderr, resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export topology-derived ladder enable logic as MATIEC-compatible Structured Text."
    )
    parser.add_argument("device", help="target output device")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--program-name", help="IEC PROGRAM name to emit")
    parser.add_argument("--format", choices=["st", "json"], default="st")
    parser.add_argument("-o", "--output", help="write ST/JSON output to file")
    parser.add_argument("--run-matiec", action="store_true", help="run MATIEC after writing the ST file")
    parser.add_argument("--matiec-tool", default="iec2c", help="MATIEC executable to run, usually iec2c or iec2iec")
    return parser


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    payload = build_st_payload(Path(args.root), args.device, program_name=args.program_name)
    if payload["row_count"] == 0:
        raise SystemExit(f"no ladder rows found for {args.device}")

    if args.format == "json":
        output = json.dumps(payload, ensure_ascii=False, indent=2)
        default_suffix = ".json"
    else:
        output = format_st(payload)
        default_suffix = ".st"

    out_path = Path(args.output) if args.output else Path("outputs") / f"{payload['target_device']}_matiec{default_suffix}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(output + "\n", encoding="utf-8")

    if args.run_matiec:
        if args.format != "st":
            raise SystemExit("--run-matiec requires --format st")
        code, stdout, stderr, resolved = run_matiec(args.matiec_tool, out_path)
        print(f"wrote: {out_path}")
        print(f"matiec_tool: {resolved}")
        if stdout:
            print(stdout.rstrip())
        if stderr:
            print(stderr.rstrip(), file=sys.stderr)
        return code

    print(f"wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
