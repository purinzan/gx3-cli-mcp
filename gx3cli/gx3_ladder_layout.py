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
    comment_text_for,
    contact_mark,
    op_cells_of,
    ops_past_grid,
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
    ops, verticals, wires, folds = _fold_to_grid(ops, verticals, wires)
    # Constant width, unless something still reaches past the grid: a rung is
    # drawn too wide far more cheaply than it is drawn with a piece missing.
    max_x = max(
        [GRID_CELLS - 1, *[op.x for op in ops], *[wire[2] for wire in wires],
         *[x for x, _ in verticals]]
    )
    max_y = max([height - 1, *[op.y for op in ops], *[wire[1] for wire in wires], *[y for _, y in verticals], 0])
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
                "ct_code": op.ct_code,
                "element_kind": op.element_kind,
                "operands": operands,
                "label": label,
                "comment": _operand_comment(primary, comments) if primary else "",
                "operand_comments": [
                    {"operand": operand, "comment": _operand_comment(operand, comments)}
                    for operand in operands
                    if _operand_comment(operand, comments)
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


def _fold_to_grid(ops, verticals, wires):
    """Move what does not fit the grid onto continuation rows below.

    Which ops move is decided by ops_past_grid, shared with the printed rung.
    Each pass drops the overflow to a fresh band under everything drawn so far
    and shifts it left to the rail, and the band is recorded so the drawing can
    mark where the rung continues.
    """
    ops = list(ops)
    folds: list[dict[str, int]] = []
    for _ in range(8):  # a rung is not folded forever; this bounds a bad row
        moved = ops_past_grid(ops, GRID_CELLS)
        if not moved:
            break
        moved_ids = {id(op) for op in moved}
        x_base = min(op.x for op in moved)
        band = max([op.y for op in ops] + [y for _, y in verticals] + [w[1] for w in wires]) + 1
        for op in moved:
            op.y = op.y + band
            op.x = op.x - x_base
        verticals = [
            (x - x_base, y + band) if x >= x_base else (x, y) for x, y in verticals
        ]
        wires = [
            (x1 - x_base, y + band, x2 - x_base) if x1 >= x_base else (x1, y, x2)
            for x1, y, x2 in wires
        ]
        folds.append({"y": band, "from_x": x_base})
        if not any(id(op) in moved_ids for op in ops):
            break
    return ops, verticals, wires, folds


def _cell_center(x: int, y: int, y_offset: int = 0) -> tuple[int, int]:
    return RAIL_PAD + x * CELL_W + CELL_W // 2, y_offset + ROW_PAD_Y + y * CELL_H + CELL_H // 2


def _boundary_x(x: int) -> int:
    return RAIL_PAD + x * CELL_W


def _text(label: str, max_chars: int = 22) -> str:
    label = label.replace("\n", " ").strip()
    return label if len(label) <= max_chars else label[: max_chars - 1] + "..."


def _comment_lines(label: str, max_chars: int = 6, max_lines: int = 2) -> list[str]:
    text = label.replace("\r", "").replace("\n", " ").strip()
    if not text:
        return []
    lines = [text[index : index + max_chars] for index in range(0, len(text), max_chars)]
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = _text(lines[-1], max_chars)
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
    if element["kind"] == "coil":
        return max(0, layout_width - 1)
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
    if element.get("element_kind") != "ct":
        return max(1, layout_width - int(element["x"]))
    return max(1, min(1 + len(element.get("operands", [])), layout_width - int(element["x"])))


def _inferred_horizontal_wires(layout: dict[str, Any], y_offset: int) -> list[str]:
    dim = layout["dim"]
    height = int(dim["height"])
    right = RAIL_PAD + max(1, int(dim["width"])) * CELL_W
    by_y: dict[int, list[tuple[int, int]]] = {y: [] for y in range(height)}
    for element in layout["elements"]:
        left, right_edge, cy = _element_box(element, y_offset)
        by_y.setdefault(int(element["y"]), []).append((left, right_edge))

    vertical_x_by_y: dict[int, set[int]] = {}
    for vertical in layout["verticals"]:
        x = _boundary_x(int(vertical["x"]))
        y1 = int(vertical["y1"])
        y2 = int(vertical["y2"])
        vertical_x_by_y.setdefault(y1, set()).add(x)
        vertical_x_by_y.setdefault(y2, set()).add(x)

    lines: list[str] = []
    for y, boxes in sorted(by_y.items()):
        if not boxes and y not in vertical_x_by_y:
            continue
        _, cy = _cell_center(0, y, y_offset)
        anchors: set[int] = set(vertical_x_by_y.get(y, set()))
        if any(int(element["y"]) == y and int(element["x"]) == 0 for element in layout["elements"]):
            anchors.add(RAIL_PAD)
        has_sink = False
        for element in layout["elements"]:
            if int(element["y"]) != y:
                continue
            left, right_edge, _ = _element_box(element, y_offset)
            anchors.add(left)
            anchors.add(right_edge)
            if element["kind"] == "coil" or element.get("element_kind") != "ct":
                has_sink = True
        if has_sink:
            anchors.add(right)
        sorted_anchors = sorted(anchors)
        blockers = sorted(boxes)
        for a, b in zip(sorted_anchors, sorted_anchors[1:]):
            if b <= a + 2:
                continue
            midpoint = (a + b) // 2
            if any(left < midpoint < right_edge for left, right_edge in blockers):
                continue
            lines.append(f'<line class="wire" x1="{a}" y1="{cy}" x2="{b}" y2="{cy}" />')
    return lines


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
    bottom = top + max(1, height - 1) * CELL_H
    left = RAIL_PAD
    right = RAIL_PAD + max(1, width) * CELL_W
    lines = [
        f'<line class="rail" x1="{left}" y1="{top}" x2="{left}" y2="{bottom}" />',
        f'<line class="rail" x1="{right}" y1="{top}" x2="{right}" y2="{bottom}" />',
    ]
    occupied: set[tuple[int, int]] = set()
    for element in layout["elements"]:
        span = _element_span(element, width)
        display_x = _display_x(element, width)
        for cell_x in range(display_x, display_x + span):
            occupied.add((cell_x, int(element["y"])))
    for wire in layout["wires"]:
        y = int(wire["y"])
        x1 = int(wire["x1"])
        x2 = int(wire["x2"])
        _, cy = _cell_center(0, y, y_offset)
        lines.append(f'<line class="wire" x1="{_boundary_x(x1)}" y1="{cy}" x2="{_boundary_x(x2)}" y2="{cy}" />')
    for y in range(height):
        row_elements = [element for element in layout["elements"] if int(element["y"]) == y]
        boundaries = {_boundary_x(_display_x(element, width)) for element in row_elements}
        boundaries.update(_boundary_x(_display_x(element, width) + _element_span(element, width)) for element in row_elements)
        for vertical in layout["verticals"]:
            if int(vertical["y1"]) == y or int(vertical["y2"]) == y:
                boundaries.add(_boundary_x(int(vertical["x"])))
        if any(_display_x(element, width) == 0 for element in row_elements):
            boundaries.add(left)
        if any(element["kind"] == "coil" or element.get("element_kind") != "ct" for element in row_elements):
            boundaries.add(right)
        sorted_boundaries = sorted(boundaries)
        _, cy = _cell_center(0, y, y_offset)
        for a, b in zip(sorted_boundaries, sorted_boundaries[1:]):
            cell_a = round((a - RAIL_PAD) / CELL_W)
            cell_b = round((b - RAIL_PAD) / CELL_W)
            if cell_b <= cell_a:
                continue
            middle_cells = range(cell_a, cell_b)
            if any((cell_x, y) in occupied for cell_x in middle_cells):
                continue
            lines.append(f'<line class="wire" x1="{a}" y1="{cy}" x2="{b}" y2="{cy}" />')
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
                    f'<line class="wire" x1="{cell_left}" y1="{y}" x2="{left_x}" y2="{y}" />',
                    f'<line class="wire" x1="{right_x}" y1="{y}" x2="{cell_right}" y2="{y}" />',
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
        ".bg{fill:#fbfcfd}.rung-bg{fill:#fff}.rail,.wire{stroke:#202832;stroke-width:2.4;stroke-linecap:square}.symbol{fill:#fff;stroke:#202832;stroke-width:2}.mark{stroke:#202832;stroke-width:1.8}.mark-head{fill:none;stroke:#202832;stroke-width:1.8;stroke-linejoin:round}.box{fill:#fff;stroke:#202832;stroke-width:1.8}.opcode-cell{fill:url(#gx3-opcode);stroke:none}.box-separator{stroke:#9aa4b1;stroke-width:1}.label{font:13px Consolas,Meiryo,sans-serif;text-anchor:middle;fill:#0f1720}.opcode-label{font:13px Meiryo,Arial,sans-serif;text-anchor:middle;fill:#1d2630}.operand-label{font:12px Consolas,Meiryo,sans-serif;text-anchor:middle;fill:#111827}.comment{font:9px Meiryo,Arial,sans-serif;text-anchor:middle;fill:#14833b}.pos{font:12px Meiryo,Arial,sans-serif;fill:#53606f}",
        "</style>",
        f'<rect class="bg" x="0" y="0" width="{svg_width}" height="{svg_height}" />',
    ]
    offset = 0
    for rung, height in zip(rungs, rung_heights):
        pos = html.escape(str(rung.get("pos", "")))
        parts.append(f'<rect class="rung-bg" x="0" y="{offset}" width="{svg_width}" height="{height - 10}" />')
        parts.append(f'<text class="pos" x="8" y="{offset + 16}">pos {pos}</text>')
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
