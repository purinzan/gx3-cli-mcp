from __future__ import annotations

"""Build a coordinate-based visual IR for GX Works3 ladder rows.

The print renderer is the evidence view. This module is the viewer/export
surface: it keeps GX coordinates, decoded operands, comments, and simple SVG
geometry in structured form so a browser UI can render without reparsing the
fixed-width text output.
"""

import argparse
import html
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gx3cli.gx3_ladder_logic import parse_dim
from gx3cli.gx3_ladder_print import (
    INLINE_SYMBOL_OPS,
    box_cells,
    comment_text_for,
    contact_mark,
    op_cells_of,
    fold_to_grid,
    display_x_for_op,
    text_width,
    wrap_display,
    load_print_comments,
    parse_rung,
    resolve_lddb,
)
from gx3cli.gx3_label_resolve import load_label_resolver
from gx3cli.gx3_program_map import load_program_map
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.gx3_intermediate_tool import read_ladder_rows
from gx3cli.review_gx3_project import LadderRow


CELL_W = 104
CELL_H = 86
# The printed grid is twelve cells wide whatever a rung contains, and GX Works3
# draws it that way. Sizing each rung to its own contents instead made a page of
# rungs a ragged stack of different widths, with the right rail moving from one
# rung to the next.
GRID_CELLS = 12
RAIL_PAD = 32
ROW_PAD_Y = 22
CONTACT_W = 54
CONTACT_H = 26
COIL_W = 56
BOX_H = 58


def _row_from_raw(lddb: str, raw: dict[str, object]) -> LadderRow:
    data = str(raw["data"])
    return LadderRow(
        lddb=lddb,
        pos=int(float(str(raw["pos"]))) if raw["pos"] is not None else None,
        block_id=str(raw["id"]),
        title="",
        blocktype=int(raw["blocktype"] or 0),
        rowsize=int(raw["rowsize"] or 0),
        data=data,
        dim="",
        operations=[],
        parse_status="",
    )


def _operand_comment(operand: str, comments: dict[tuple[str, int], str]) -> str:
    return comment_text_for(operand, comments)


def rung_layout(
    row: LadderRow,
    comments: dict[tuple[str, int], str] | None = None,
    labels: Any | None = None,
    step: int | None = None,
) -> dict[str, Any]:
    comments = comments or {}
    ops, verticals, wires = parse_rung(row, labels)
    width, height = parse_dim(row.dim) if row.dim else (0, 0)
    if not width or not height:
        from gx3cli.extract_gx3_extended_instruction_knowledge import extract_dim

        width, height = parse_dim(extract_dim(row.data))
    # A rung wider than the printed grid folds onto continuation rows, the way
    # the printed one does and on the same rule, so the two break in the same
    # place. Everything then lays out to one width.
    ops, verticals, wires, folds = fold_to_grid(ops, verticals, wires, GRID_CELLS)
    # Ends of wires and verticals are boundaries, not additional cells.
    max_x = max(
        [GRID_CELLS - 1, *[op.x + op_cells_of(op) - 1 for op in ops],
         *[wire[2] - 1 for wire in wires], *[x - 1 for x, _ in verticals]]
    )
    max_y = max([0 if folds else height - 1, *[op.y for op in ops], *[wire[1] for wire in wires], *[y for _, y in verticals], 0])
    max_y = max(max_y, *( [f["y"] for f in folds] or [0] ))

    elements: list[dict[str, Any]] = []
    for op in ops:
        operands = list(op.operands)
        primary = operands[0] if operands else ""
        if op.is_contact:
            kind = "contact"
            label = primary
        elif op.is_coil:
            kind = "coil"
            label = primary
        else:
            kind = "instruction"
            label = " ".join([op.opcode_text(), *operands]).strip()
        elements.append(
            {
                "kind": kind,
                "role": op.role,
                "opcode": op.opcode_text(),
                "x": op.x,
                "y": op.y,
                "display_x": display_x_for_op(op, ops, verticals, wires, GRID_CELLS),
                "ct_code": op.ct_code,
                "element_kind": op.element_kind,
                "operands": operands,
                "label": label,
                "comment": _operand_comment(primary, comments) if primary else "",
                "operand_comments": [
                    {"operand": operand, "comment": _operand_comment(operand, comments)}
                    for operand in operands
                ],
            }
        )

    return {
        "lddb": row.lddb,
        "pos": row.pos,
        "step": step,
        "block_id": row.block_id,
        "dim": {"width": max_x + 1, "height": max_y + 1},
        "elements": elements,
        "wires": [{"x1": x, "y": y, "x2": x2} for x, y, x2 in wires],
        "verticals": [{"x": x, "y1": y - 1, "y2": y} for x, y in verticals],
        "folds": folds,
    }


def _cell_center(x: int, y: int, y_offset: int = 0) -> tuple[int, int]:
    return RAIL_PAD + x * CELL_W + CELL_W // 2, y_offset + ROW_PAD_Y + y * CELL_H + CELL_H // 2


def _boundary_x(x: int) -> int:
    return RAIL_PAD + x * CELL_W


def _text(label: str, max_chars: int = 22) -> str:
    label = label.replace("\n", " ").strip()
    return label if len(label) <= max_chars else label[: max_chars - 1] + "..."


def _comment_lines(label: str, width: int = 12, max_lines: int = 2) -> list[str]:
    text = label.replace("\r", "").replace("\n", " ").strip()
    lines = wrap_display(text, width, max_lines)
    if sum(len(line) for line in lines) < len(text) and lines:
        tail = lines[-1]
        while tail and text_width(tail + "...") > width:
            tail = tail[:-1]
        lines[-1] = tail + "..."
    return lines


def _instruction_lines(element: dict[str, Any]) -> list[str]:
    opcode = str(element.get("opcode") or element.get("role") or "")
    operands = [str(value) for value in element.get("operands", [])]
    if not operands:
        return [opcode]
    if len(operands) <= 2:
        return [opcode, " ".join(operands)]
    return [opcode, *operands[:3]]


def _instruction_cell_texts(element: dict[str, Any]) -> list[str]:
    opcode = str(element.get("opcode") or element.get("role") or "")
    operands = [str(value) for value in element.get("operands", [])]
    return [opcode, *operands]


def _display_x(element: dict[str, Any], layout_width: int) -> int:
    if "display_x" in element:
        return int(element["display_x"])
    if element["kind"] == "coil":
        return max(0, layout_width - 1)
    if element["kind"] == "instruction" and element.get("element_kind") != "ct":
        return max(0, layout_width - _element_span(element, layout_width))
    return int(element["x"])


def _element_box(element: dict[str, Any], y_offset: int) -> tuple[int, int, int]:
    layout_width = int(element.get("_layout_width", 0) or int(element["x"]) + 1)
    x, y = _cell_center(_display_x(element, layout_width), int(element["y"]), y_offset)
    kind = element["kind"]
    if kind == "contact":
        return x - CONTACT_W // 2, x + CONTACT_W // 2, y
    if kind == "coil":
        return x - COIL_W // 2, x + COIL_W // 2, y
    instruction_lines = _instruction_lines(element)
    max_line = max((len(line) for line in instruction_lines), default=8)
    box_w = min(max(118, max_line * 8 + 26), 250)
    return x - box_w // 2, x + box_w // 2, y


def _element_span(element: dict[str, Any], layout_width: int) -> int:
    if element["kind"] in {"contact", "coil"}:
        return 1
    # Match the cell footprint used by the print renderer and grid folding.
    # Output instructions must not expand to consume the remaining power rail.
    return max(1, min(box_cells(element.get("operands", [])), layout_width - int(element["x"])))


def _contact_mark_svg(mark: str, x: int, y: int) -> list[str]:
    """Draw what a contact carries: the NC slash, or a rising/falling arrow.

    Without the arrows a rising-edge contact was drawn exactly like a level one,
    so the picture said "while this is on" where the ladder says "when it turns
    on".
    """
    if mark == "closed":
        return [f'<line class="mark" x1="{x - 12}" y1="{y + 11}" x2="{x + 12}" y2="{y - 11}" />']
    if mark in ("rising", "falling"):
        up = mark == "rising"
        tip_y = y - 12 if up else y + 12
        tail_y = y + 12 if up else y - 12
        head = 5 if up else -5
        return [
            f'<line class="mark" x1="{x}" y1="{tail_y}" x2="{x}" y2="{tip_y}" />',
            f'<polyline class="mark-head" points="{x - 6},{tip_y + head} {x},{tip_y} {x + 6},{tip_y + head}" />',
        ]
    return []


def _inline_symbol_svg(opcode: str, x: int, y: int) -> list[str]:
    """INV as a slash on the wire, ME and MEF as the edge arrows."""
    if opcode == "INV":
        return [f'<line class="mark" x1="{x - 12}" y1="{y + 12}" x2="{x + 12}" y2="{y - 12}" />']
    return _contact_mark_svg("rising" if opcode == "ME" else "falling", x, y)


def _svg_rung(layout: dict[str, Any], y_offset: int) -> list[str]:
    dim = layout["dim"]
    width = int(dim["width"])
    height = int(dim["height"])
    top = y_offset + ROW_PAD_Y
    bottom = top + max(1, height) * CELL_H
    left = RAIL_PAD
    right = RAIL_PAD + max(1, width) * CELL_W
    lines = [
        f'<line class="rail" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" />',
        f'<line class="rail" x1="{right}" y1="{top}" x2="{right}" y2="{bottom}" />',
    ]
    for wire in layout["wires"]:
        y = int(wire["y"])
        x1 = int(wire["x1"])
        x2 = int(wire["x2"])
        _, cy = _cell_center(0, y, y_offset)
        lines.append(f'<line class="wire" x1="{_boundary_x(x1)}" y1="{cy}" x2="{_boundary_x(x2)}" y2="{cy}" />')
    # A moved sink keeps its original input node through a display stem.
    for element in layout["elements"]:
        source = int(element["x"])
        display = _display_x(element, width)
        if display > source:
            _, cy = _cell_center(0, int(element["y"]), y_offset)
            lines.append(f'<line class="wire" x1="{_boundary_x(source)}" y1="{cy}" x2="{_boundary_x(display)}" y2="{cy}" />')
    for vertical in layout["verticals"]:
        x = _boundary_x(int(vertical["x"]))
        _, y1 = _cell_center(0, int(vertical["y1"]), y_offset)
        _, y2 = _cell_center(0, int(vertical["y2"]), y_offset)
        lines.append(f'<line class="wire" x1="{x}" y1="{y1}" x2="{x}" y2="{y2}" />')

    for element in layout["elements"]:
        grid_x = _display_x(element, width)
        grid_y = int(element["y"])
        span = _element_span(element, width)
        cell_left = _boundary_x(grid_x)
        cell_right = _boundary_x(grid_x + span)
        x = (cell_left + cell_right) // 2
        _, y = _cell_center(grid_x, grid_y, y_offset)
        label = html.escape(_text(str(element["label"])))
        comment = html.escape(_text(str(element.get("comment") or ""), 28))
        kind = element["kind"]
        if kind == "contact":
            left_x = (cell_left + cell_right) // 2 - CONTACT_W // 2
            right_x = (cell_left + cell_right) // 2 + CONTACT_W // 2
            lines.extend(
                [
                    f'<line class="wire" x1="{cell_left}" y1="{y}" x2="{left_x + 10}" y2="{y}" />',
                    f'<line class="wire" x1="{right_x - 10}" y1="{y}" x2="{cell_right}" y2="{y}" />',
                    f'<line class="symbol" x1="{left_x + 10}" y1="{y - CONTACT_H // 2}" x2="{left_x + 10}" y2="{y + CONTACT_H // 2}" />',
                    f'<line class="symbol" x1="{right_x - 10}" y1="{y - CONTACT_H // 2}" x2="{right_x - 10}" y2="{y + CONTACT_H // 2}" />',
                    *_contact_mark_svg(contact_mark(str(element["role"]), str(element.get("ct_code", ""))), x, y),
                    f'<text class="label" x="{x}" y="{y - 22}">{label}</text>',
                ]
            )
        elif kind == "coil":
            lines.extend(
                [
                    f'<line class="wire" x1="{cell_left}" y1="{y}" x2="{x - COIL_W // 2}" y2="{y}" />',
                    f'<line class="wire" x1="{x + COIL_W // 2}" y1="{y}" x2="{cell_right}" y2="{y}" />',
                    f'<ellipse class="symbol" cx="{x}" cy="{y}" rx="22" ry="15" />',
                    f'<text class="label" x="{x}" y="{y - 24}">{label}</text>',
                ]
            )
        elif str(element.get("opcode", "")) in INLINE_SYMBOL_OPS and not element.get("operands"):
            # The printed rung draws these on the wire rather than in a box:
            # INV inverts what reached it, ME and MEF are the edge forms. A box
            # with the letters in it reads as an instruction that moves data.
            lines.extend(
                [
                    f'<line class="wire" x1="{cell_left}" y1="{y}" x2="{cell_right}" y2="{y}" />',
                    *_inline_symbol_svg(str(element["opcode"]), x, y),
                ]
            )
        else:
            box_w = max(80, (cell_right - cell_left) - 10)
            box_h = BOX_H
            box_left = cell_left + 5
            box_right = cell_right - 5
            box_top = y - box_h // 2
            cell_texts = _instruction_cell_texts(element)
            cell_count = max(1, len(cell_texts))
            opcode_w = min(CELL_W - 10, box_w)
            operand_count = max(0, cell_count - 1)
            operand_w = (box_w - opcode_w) / operand_count if operand_count else 0
            lines.extend(
                [
                    f'<line class="wire" x1="{cell_left}" y1="{y}" x2="{box_left}" y2="{y}" />',
                    f'<line class="wire" x1="{box_right}" y1="{y}" x2="{cell_right}" y2="{y}" />',
                    f'<rect class="box" x="{box_left}" y="{box_top}" width="{box_w}" height="{box_h}" rx="2" />',
                    f'<rect class="opcode-cell" x="{box_left}" y="{box_top}" width="{opcode_w}" height="{box_h}" />',
                ]
            )
            if operand_count:
                lines.append(f'<line class="box-separator" x1="{box_left + opcode_w}" y1="{box_top}" x2="{box_left + opcode_w}" y2="{box_top + box_h}" />')
            for index in range(1, operand_count):
                sx = box_left + opcode_w + operand_w * index
                lines.append(f'<line class="box-separator" x1="{sx}" y1="{box_top}" x2="{sx}" y2="{box_top + box_h}" />')
            for index, item in enumerate(cell_texts):
                tx = box_left + opcode_w / 2 if index == 0 else box_left + opcode_w + operand_w * (index - 1) + operand_w / 2
                if index == 0:
                    lines.append(f'<text class="opcode-label" x="{tx}" y="{y}" dominant-baseline="middle">{html.escape(_text(item, 16))}</text>')
                else:
                    lines.append(f'<text class="operand-label" x="{tx}" y="{box_top + 15}" dominant-baseline="middle">{html.escape(_text(item, 16))}</text>')
            for index, row in enumerate(element.get("operand_comments", []), start=1):
                if index >= cell_count:
                    continue
                tx = box_left + opcode_w + operand_w * (index - 1) + operand_w / 2
                for line_index, value in enumerate(_comment_lines(str(row.get("comment") or ""))):
                    lines.append(f'<text class="comment" x="{tx}" y="{box_top + 35 + line_index * 11}">{html.escape(value)}</text>')
            comment = ""
        if comment:
            for line_index, value in enumerate(_comment_lines(str(element.get("comment") or ""))):
                lines.append(f'<text class="comment" x="{x}" y="{y + 31 + line_index * 13}">{html.escape(value)}</text>')
    for fold in layout.get("folds", []):
        label = html.escape(str(fold["label"]) + " →")
        for outgoing, row_y in ((True, fold["from_y"]), (False, fold["to_y"])):
            cell_x = width - 1 if outgoing else 0
            x, y = _cell_center(cell_x, row_y, y_offset)
            # A marker is a wire terminal, with a gap to the nearby power rail.
            x1 = _boundary_x(cell_x) if outgoing else x + CELL_W // 3
            x2 = x - CELL_W // 3 if outgoing else _boundary_x(1)
            lines.append(f'<line class="wire" x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" />')
            lines.append(f'<text class="fold-label" x="{x}" y="{y}" dominant-baseline="middle">{label}</text>')
    return [line for line in lines if line]


def layouts_to_svg(payload: dict[str, Any]) -> str:
    rungs = list(payload["rungs"])
    rung_heights = [max(1, int(rung["dim"]["height"])) * CELL_H + ROW_PAD_Y * 2 for rung in rungs]
    max_width = max([1, *[int(rung["dim"]["width"]) for rung in rungs]])
    svg_width = RAIL_PAD * 2 + max_width * CELL_W
    svg_height = max(120, sum(rung_heights) + 28)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{svg_width}" height="{svg_height}" viewBox="0 0 {svg_width} {svg_height}">',
        "<defs>",
        '<linearGradient id="gx3-opcode" x1="0" y1="0" x2="0" y2="1">',
        '<stop offset="0%" stop-color="#f8f9fb" />',
        '<stop offset="50%" stop-color="#cfd4dc" />',
        '<stop offset="100%" stop-color="#9da6b2" />',
        "</linearGradient>",
        "</defs>",
        "<style>",
        ".bg{fill:#fbfcfd}.rung-bg{fill:#fff}.rail,.wire{stroke:#202832;stroke-width:2.4;stroke-linecap:square}.symbol{fill:#fff;stroke:#202832;stroke-width:2}.mark{stroke:#202832;stroke-width:1.8}.mark-head{fill:none;stroke:#202832;stroke-width:1.8;stroke-linejoin:round}.box{fill:#fff;stroke:#202832;stroke-width:1.8}.opcode-cell{fill:url(#gx3-opcode);stroke:none}.box-separator{stroke:#9aa4b1;stroke-width:1}.label{font:13px Consolas,Meiryo,sans-serif;text-anchor:middle;fill:#0f1720}.opcode-label{font:13px Meiryo,Arial,sans-serif;text-anchor:middle;fill:#1d2630}.operand-label{font:12px Consolas,Meiryo,sans-serif;text-anchor:middle;fill:#111827}.comment{font:9px Meiryo,Arial,sans-serif;text-anchor:middle;fill:#14833b}.fold-label{font:12px Consolas,Meiryo,sans-serif;text-anchor:middle;fill:#202832}.pos{font:12px Meiryo,Arial,sans-serif;fill:#53606f}",
        "</style>",
        f'<rect class="bg" x="0" y="0" width="{svg_width}" height="{svg_height}" />',
    ]
    offset = 0
    for rung, height in zip(rungs, rung_heights):
        pos = html.escape(str(rung.get("pos", "")))
        parts.append(f'<rect class="rung-bg" x="0" y="{offset}" width="{svg_width}" height="{height - 10}" />')
        parts.append(f'<text class="pos" x="8" y="{offset + 16}">step {html.escape(str(rung.get("step"))) if rung.get("step") is not None else "?"} · pos {pos}</text>')
        parts.extend(_svg_rung(rung, offset))
        offset += height
    parts.append("</svg>")
    return "\n".join(parts)


@dataclass
class ProjectSources:
    """What every program of one project shares: the map, comments, labels, rows.

    Reading them per program is fine for one program and quadratic for all of
    them -- `read_ladder_rows` opens every LDDB in the project each time it is
    called, so a report over seventy programs read the project seventy times.
    A caller building more than one payload passes this in.
    """

    program_map: Any
    comments: dict
    labels: Any
    rows: dict[str, list[dict[str, object]]]


def load_project_sources(root: Path) -> ProjectSources:
    return ProjectSources(
        program_map=load_program_map(root),
        comments=load_print_comments(root),
        labels=load_label_resolver(root),
        rows=read_ladder_rows(root),
    )


def build_layout_payload(
    root: Path,
    program: str,
    pos_range: tuple[int, int] | None = None,
    device: str | None = None,
    sources: ProjectSources | None = None,
) -> dict[str, Any]:
    sources = sources or load_project_sources(root)
    pm = sources.program_map
    lddb_name = program if program.endswith("_LDDB.db") else resolve_lddb(root, program, pm)
    comments = sources.comments
    labels = sources.labels
    rows = []
    for raw in sources.rows.get(lddb_name, []):
        if int(raw.get("blocktype") or 0) != 0:
            continue
        row = _row_from_raw(lddb_name, raw)
        if pos_range and row.pos is not None and not (pos_range[0] <= row.pos <= pos_range[1]):
            continue
        layout = rung_layout(row, comments, labels, pm.step_of(lddb_name, row.pos) if row.pos is not None else None)
        if device:
            target = device.strip().upper()
            operands = {operand.upper() for element in layout["elements"] for operand in element["operands"]}
            if target not in operands:
                continue
        rows.append(layout)
    return {"root": str(root), "program": lddb_name, "rungs": rows}


def _parse_pos_range(value: str | None) -> tuple[int, int] | None:
    if not value:
        return None
    parts = value.split("-", 1)
    if len(parts) != 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
        raise SystemExit(f"--pos-range must be A-B with integers (got {value!r})")
    return int(parts[0]), int(parts[1])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export GX Works3 ladder coordinate layout as JSON or SVG.")
    parser.add_argument("program", help="program name (POU / program file) or LDDB file name")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--pos-range", metavar="A-B", help="only export rows whose step pos is within A-B")
    parser.add_argument("--device", help="only export rungs that reference DEVICE")
    parser.add_argument("--format", choices=["json", "svg"], default="json")
    parser.add_argument("-o", "--output", help="write output to file")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    root = resolve_project_root(args.root)
    payload = build_layout_payload(root, args.program, _parse_pos_range(args.pos_range), args.device)
    if args.format == "svg":
        text = layouts_to_svg(payload)
    else:
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        out_path = Path(args.output)
        if out_path.parent and not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")
        print(f"written: {args.output} ({len(payload['rungs'])} rungs)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
