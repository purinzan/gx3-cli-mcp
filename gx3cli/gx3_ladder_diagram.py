from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from gx3cli.extract_gx3_extended_instruction_knowledge import extract_dim
from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.gx3_ladder_logic import (
    FlowElement,
    VERTICAL_RE,
    enable_logic_for_output,
    logic_to_text,
    normalize_device,
    output_elements_for,
    parse_dim,
    positioned_elements,
)
from gx3cli.review_gx3_project import LadderRow, comment_for_device, load_comments_for_root, load_rows
from gx3cli.gx3_project_paths import default_project_root


DEFAULT_CELL_WIDTH = 16
DRIVER_ROLES = {"c", "SET", "RST", "PLS", "PLF", "OUT__16", "OUTH__16"}


def compact_text(text: str, max_len: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= max_len:
        return value
    return value[: max(0, max_len - 3)] + "..."


def display_opcode(opcode: str) -> str:
    return opcode.replace("__16", "")


def element_operands(element: FlowElement) -> list[str]:
    return [*element.constants, *[ref.display for ref in element.devices]]


def element_symbol(element: FlowElement) -> str:
    operands = element_operands(element)
    if element.is_wire:
        return ""

    if element.role in {"a", "b"}:
        device = operands[0] if operands else "?"
        prefix = "/" if element.role == "b" else ""
        edge_prefix = "P " if element.ct_code == "p" else ""
        return f"[{prefix}{edge_prefix}{device}]"

    if element.is_driver:
        if element.role == "c":
            body = operands[0] if operands else "?"
        else:
            body = " ".join([display_opcode(element.role), *operands]).strip()
        return f"({body})"

    opcode = display_opcode(element.opcode or element.role or element.kind or "?")
    body = " ".join([opcode, *operands]).strip()
    return f"[{body}]"


def row_dimensions(row: LadderRow, elements: list[FlowElement]) -> tuple[int, int]:
    width, height = parse_dim(row.dim or extract_dim(row.data))
    if elements:
        width = max(width, max(element.end_x for element in elements))
        height = max(height, max(element.y for element in elements) + 1)
    for x_text, y_text in VERTICAL_RE.findall(row.data):
        width = max(width, int(x_text) + 1)
        height = max(height, int(y_text) + 1)
    return max(width, 1), max(height, 1)


def center_on_wire(symbol: str, width: int) -> str:
    if not symbol:
        return "-" * width
    left = max(0, (width - len(symbol)) // 2)
    right = max(0, width - len(symbol) - left)
    return ("-" * left) + symbol + ("-" * right)


def render_row_diagram(row: LadderRow, cell_width: int = DEFAULT_CELL_WIDTH) -> list[str]:
    elements = positioned_elements(row)
    width, height = row_dimensions(row, elements)
    symbols = {element_symbol(element) for element in elements if not element.is_wire}
    effective_cell_width = max(cell_width, *[len(symbol) + 4 for symbol in symbols], 4)

    segments: dict[int, dict[int, str]] = defaultdict(dict)
    for element in elements:
        if element.x < 0 or element.x >= width or element.y < 0 or element.y >= height:
            continue
        if element.is_wire:
            segments[element.y][element.x] = center_on_wire("", effective_cell_width)
        else:
            segments[element.y][element.x] = center_on_wire(element_symbol(element), effective_cell_width)
        for x in range(element.x + 1, min(element.end_x, width)):
            segments[element.y].setdefault(x, center_on_wire("", effective_cell_width))

    vertical_by_y: dict[int, set[int]] = defaultdict(set)
    for x_text, y_text in VERTICAL_RE.findall(row.data):
        x = int(x_text)
        y = int(y_text)
        if 0 <= y - 1 < height:
            vertical_by_y[y - 1].add(x)
        if 0 <= y < height:
            vertical_by_y[y].add(x)

    lines: list[str] = []
    blank = " " * effective_cell_width
    for y in range(height):
        parts = ["|"]
        for x in range(width):
            cell = segments[y].get(x, blank)
            parts.append(cell)
            boundary = x + 1
            if boundary == width:
                parts.append("|")
            elif boundary in vertical_by_y.get(y, set()):
                left_has_wire = x in segments[y]
                right_has_wire = (x + 1) in segments[y]
                parts.append("+" if left_has_wire or right_has_wire else "|")
            else:
                left_has_wire = x in segments[y]
                right_has_wire = (x + 1) in segments[y]
                parts.append("-" if left_has_wire and right_has_wire else " ")
        lines.append("".join(parts).rstrip())
    return lines


def driver_outputs(row: LadderRow) -> list[FlowElement]:
    return [element for element in positioned_elements(row) if element.is_driver]


def output_label(output: FlowElement) -> str:
    operands = element_operands(output)
    device = operands[0] if operands else "?"
    if output.role == "c":
        return device
    return " ".join([display_opcode(output.role), *operands]).strip()


def device_comments_for_row(
    row: LadderRow,
    comments: dict[tuple[str, int], CommentInfo],
    max_comments: int,
    comment_width: int,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for element in positioned_elements(row):
        for ref in element.devices:
            if ref.display in seen:
                continue
            seen.add(ref.display)
            comment = comment_for_device(ref.device_type, ref.number, comments)
            if not comment:
                continue
            records.append({"device": ref.display, "comment": compact_text(comment, comment_width)})
            if len(records) >= max_comments:
                return records
    return records


def find_rows(
    rows: list[LadderRow],
    device: str | None,
    lddb: str | None,
    pos: float | None,
) -> list[tuple[LadderRow, list[FlowElement]]]:
    if lddb is not None and pos is not None:
        matches = [
            row
            for row in rows
            if row.lddb == lddb and float(row.pos) == float(pos)
        ]
        if device:
            target = normalize_device(device)
            return [(row, output_elements_for(row, target)) for row in matches]
        return [(row, driver_outputs(row)) for row in matches]

    if not device:
        return []
    target = normalize_device(device)
    out: list[tuple[LadderRow, list[FlowElement]]] = []
    for row in rows:
        outputs = output_elements_for(row, target)
        if outputs:
            out.append((row, outputs))
    return out


def build_payload(
    root: Path,
    device: str | None,
    lddb: str | None,
    pos: float | None,
    cell_width: int,
    include_comments: bool,
    max_comments: int,
    comment_width: int,
) -> dict[str, Any]:
    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)
    matches = find_rows(rows, device, lddb, pos)
    target = normalize_device(device) if device else ""
    row_records: list[dict[str, Any]] = []

    for row, outputs in matches:
        output_records: list[dict[str, Any]] = []
        for output in outputs:
            logic = enable_logic_for_output(row, output)
            output_records.append(
                {
                    "label": output_label(output),
                    "role": output.role,
                    "position": f"{output.x},{output.y}",
                    "enable_logic_text": logic_to_text(logic),
                }
            )
        row_records.append(
            {
                "row_id": f"{row.lddb}:{row.pos}",
                "lddb": row.lddb,
                "pos": row.pos,
                "title": row.title,
                "dim": row.dim or extract_dim(row.data),
                "parse_status": row.parse_status,
                "outputs": output_records,
                "diagram": render_row_diagram(row, cell_width=cell_width),
                "comments": (
                    device_comments_for_row(row, comments, max_comments=max_comments, comment_width=comment_width)
                    if include_comments
                    else []
                ),
            }
        )

    return {
        "target_device": target,
        "source_root": str(root),
        "row_count": len(row_records),
        "rows": row_records,
    }


def format_text(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    target = payload.get("target_device") or "(row selection)"
    lines.append(f"Ladder diagram: {target}")
    lines.append(f"Source: {payload['source_root']}")
    lines.append(f"Rows: {payload['row_count']}")
    lines.append("")

    for row in payload["rows"]:
        title = f" {row['title']}" if row.get("title") else ""
        lines.append(f"== {row['row_id']}{title} ==")
        outputs = ", ".join(output["label"] for output in row.get("outputs", [])) or "(none)"
        lines.append(f"dim={row.get('dim', '')} parse={row.get('parse_status', '')} target_outputs={outputs}")
        for line in row["diagram"]:
            lines.append(line)
        for output in row.get("outputs", []):
            if output.get("enable_logic_text"):
                lines.append(f"enable {output['label']}: {output['enable_logic_text']}")
        if row.get("comments"):
            lines.append("devices:")
            for rec in row["comments"]:
                lines.append(f"  - {rec['device']}: {rec['comment']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def format_markdown(payload: dict[str, Any]) -> str:
    target = payload.get("target_device") or "row selection"
    lines = [
        f"# Ladder diagram: {target}",
        "",
        f"- source: `{payload['source_root']}`",
        f"- rows: {payload['row_count']}",
        "",
    ]
    for row in payload["rows"]:
        title = f" {row['title']}" if row.get("title") else ""
        lines.append(f"## `{row['row_id']}`{title}")
        outputs = ", ".join(f"`{output['label']}`" for output in row.get("outputs", [])) or "(none)"
        lines.append(f"- dim: `{row.get('dim', '')}`")
        lines.append(f"- parse: `{row.get('parse_status', '')}`")
        lines.append(f"- target_outputs: {outputs}")
        lines.extend(["", "```text", *row["diagram"], "```"])
        logic_lines = [output for output in row.get("outputs", []) if output.get("enable_logic_text")]
        if logic_lines:
            lines.extend(["", "### Enable Logic"])
            for output in logic_lines:
                lines.append(f"- `{output['label']}`: `{output['enable_logic_text']}`")
        if row.get("comments"):
            lines.extend(["", "### Devices"])
            for rec in row["comments"]:
                lines.append(f"- `{rec['device']}`: {rec['comment']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render ladder rows as an ASCII ladder diagram.")
    parser.add_argument("device", nargs="?", help="target output device")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--lddb", help="render a specific LDDB row")
    parser.add_argument("--pos", type=float, help="render a specific row position")
    parser.add_argument("--cell-width", type=int, default=DEFAULT_CELL_WIDTH, help="minimum rendered cell width")
    parser.add_argument("--no-comments", action="store_true", help="omit device comment list")
    parser.add_argument("--max-comments", type=int, default=80, help="maximum device comments per row")
    parser.add_argument("--comment-width", type=int, default=80, help="maximum chars per comment")
    parser.add_argument("--format", choices=["text", "markdown", "json"], default="text")
    parser.add_argument("-o", "--output", help="write output to file")
    return parser


def resolve_lddb_name(root: Path, lddb: str) -> str:
    """Accept a bare hash, ``<hash>_LDDB``, or full ``<hash>_LDDB.db`` name.

    On no match, raise with the list of available LDDB names so the user does
    not get a silent "no ladder rows found".
    """
    available = sorted(p.name for p in root.glob("*_LDDB.db"))
    candidates = [lddb]
    if not lddb.endswith("_LDDB.db"):
        candidates.append(lddb if lddb.endswith("_LDDB") else f"{lddb}_LDDB")
        candidates.append(f"{lddb}.db" if lddb.endswith("_LDDB") else f"{lddb}_LDDB.db")
    for cand in candidates:
        if cand in available:
            return cand
    raise SystemExit(
        f"LDDB not found: {lddb!r}. Pass the full name (e.g. '{available[0]}') "
        f"or its hash. Available: {', '.join(available) if available else '(none)'}"
    )


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    if (args.lddb is None) != (args.pos is None):
        raise SystemExit("--lddb and --pos must be provided together")
    if not args.device and not args.lddb:
        raise SystemExit("provide a target device or --lddb/--pos")

    root = Path(args.root)
    if args.lddb is not None:
        args.lddb = resolve_lddb_name(root, args.lddb)

    payload = build_payload(
        root=root,
        device=args.device,
        lddb=args.lddb,
        pos=args.pos,
        cell_width=args.cell_width,
        include_comments=not args.no_comments,
        max_comments=args.max_comments,
        comment_width=args.comment_width,
    )
    if payload["row_count"] == 0:
        target = args.device or f"{args.lddb}:{args.pos}"
        raise SystemExit(f"no ladder rows found for {target}")

    if args.format == "json":
        output = json.dumps(payload, ensure_ascii=False, indent=2)
    elif args.format == "markdown":
        output = format_markdown(payload)
    else:
        output = format_text(payload)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
