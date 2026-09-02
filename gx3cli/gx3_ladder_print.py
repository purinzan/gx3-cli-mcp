from __future__ import annotations

"""Render ladder programs in the GX Works3 print-to-text-file layout.

Produces the same fixed-grid text form as GX Works3's print output
(reference: a real print such as 13_P04.txt): 12 cells x 14 display
columns, contacts as ``|{    }|``-style box-drawing symbols, coils as a
circle in the last cell, instruction boxes right-aligned, and device
comments wrapped to 12 columns x 4 lines under each symbol row.

Display width follows MS Gothic conventions: East-Asian wide/fullwidth
and ambiguous characters occupy 2 columns, everything else 1.
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from gx3cli.gx3_operand_parse import parse_operands
from gx3cli.gx3_device_name import HEX_DEVICE_TYPES, device_radix, format_device, hex_number
from gx3cli.extract_gx3_extended_instruction_knowledge import (
    LABEL_TOKEN_PREFIX,
    extract_args_text,
    extract_elements,
    element_meta,
    header_tokens,
    top_level_items,
)
from gx3cli.gx3_intermediate_tool import parse_header_ops
from gx3cli.extract_gx3_extended_instruction_knowledge import DEVICE_TYPES
from gx3cli.gx3_ladder_logic import VERTICAL_RE, parse_pos
from gx3cli.gx3_program_map import ProgramMap, load_program_map
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.review_gx3_project import DEVICE_CODE_BY_TYPE, LadderRow
from gx3cli.gx3_project_paths import find_comment_db
from gx3cli.gx3_label_resolve import LabelResolver, load_label_resolver


# ---------------------------------------------------------------------------
# Layout constants (measured from a real GX Works3 print).

CELL_W = 14          # display columns per grid cell
N_CELLS = 12         # grid cells per printed row
LEFT_RAIL = 28       # display column of the left power rail
CELL0 = LEFT_RAIL + 2
RIGHT_RAIL = CELL0 + CELL_W * N_CELLS - 2  # 196: last cell is 2 cols short
TOTAL_W = RIGHT_RAIL + 2
COMMENT_W = 12       # comment wrap width inside one cell
COMMENT_LINES = 4    # comment lines reserved under each symbol row
STEP_END = LEFT_RAIL  # step number is right-aligned ending here

def contact_symbol(role: str, ct_code: str) -> str:
    if ct_code == "p":
        return "┤ ↑ ├"
    if ct_code == "f":
        return "┤ ↓ ├"
    if role == "b":
        return "┤ ／ ├"
    return "┤    ├"

HLINE = "─"
VLINE = "│"
COIL = "○"

# inline ops drawn as a bare symbol on the wire instead of a box
INLINE_SYMBOL_OPS = {"INV": "／", "ME": "↑", "MEF": "↓"}

JUNCTION = {
    # (up, down, left, right) -> box drawing char
    (True, True, True, True): "┼",   # +
    (True, True, True, False): "┤",  # -|
    (True, True, False, True): "├",  # |-
    (True, False, True, True): "┴",  # _|_
    (False, True, True, True): "┬",  # T
    (True, False, False, True): "└", # L
    (True, False, True, False): "┘", # _|
    (False, True, False, True): "┌",
    (False, True, True, False): "┐",
    (True, True, False, False): VLINE,
}


def char_width(ch: str) -> int:
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1


def text_width(text: str) -> int:
    return sum(char_width(ch) for ch in text)


class Line:
    """A fixed-width display line addressed by display column."""

    def __init__(self, width: int = TOTAL_W) -> None:
        self.cols: list[str | None] = [" "] * width

    def put(self, col: int, text: str) -> None:
        for ch in text:
            w = char_width(ch)
            if col + w > len(self.cols):
                break
            # clear continuation cells we are about to overlap
            if self.cols[col] is None and col > 0:
                self.cols[col - 1] = " "
            self.cols[col] = ch
            for k in range(1, w):
                self.cols[col + k] = None
            after = col + w
            if after < len(self.cols) and self.cols[after] is None:
                self.cols[after] = " "
            col += w

    def char_at(self, col: int) -> str:
        if col < 0 or col >= len(self.cols):
            return " "
        c = self.cols[col]
        if c is None:
            for back in range(col - 1, -1, -1):
                if self.cols[back] is not None:
                    return self.cols[back]
        return c or " "

    def text(self) -> str:
        return "".join(c for c in self.cols if c is not None).rstrip()


def pad_field(text: str, width: int) -> str:
    w = text_width(text)
    if w > width:
        # trim by display width
        out = ""
        used = 0
        for ch in text:
            cw = char_width(ch)
            if used + cw > width:
                break
            out += ch
            used += cw
        return out + " " * (width - used)
    return text + " " * (width - w)


def wrap_display(text: str, width: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    cur = ""
    used = 0
    for ch in text:
        cw = char_width(ch)
        if used + cw > width:
            lines.append(cur)
            cur = ch
            used = cw
            if len(lines) >= max_lines:
                return lines[:max_lines]
        else:
            cur += ch
            used += cw
    if cur:
        lines.append(cur)
    return lines[:max_lines]


# ---------------------------------------------------------------------------
# Ordered display operands (order matters: OUT T0 K5, <> K0 D1986, ...).


def display_operands(
    raw_args: list[str], arg_tokens: list[str], labels: LabelResolver | None = None
) -> list[str]:
    """Decode every argument into its display text, in instruction order.

    The walk over the header tokens lives in gx3_operand_parse, shared with the
    cross-reference; this spells the result the way GX Works3 prints it, with
    the modifier folded into the name (K4M100, D100.5, D100Z2).
    """
    out: list[str] = []
    for operand in parse_operands(raw_args, arg_tokens, allow_pointer=True):
        if operand.kind == "label":
            ref = labels.resolve_token(operand.label_token) if labels is not None and operand.label_token else None
            out.append(ref.name if ref is not None else "?")
            continue

        if operand.kind == "const":
            token = operand.const_token
            value = operand.const_value or "?"
            if operand.raw.startswith("M{"):
                # A constant base with an index register: K2400Z2.
                index = f"Z{operand.index_reg}" if operand.index_reg else ""
                out.append(f"K{value}{index}")
                continue
            prefix = token.split("_", 1)[0] if token and token[0] in "KHE" else "K"
            if token == "String":
                out.append(f'"{value}"')
            elif prefix == "H":
                try:
                    out.append(f"H{int(value):X}")
                except ValueError:
                    out.append(f"H{value}")
            else:
                out.append(f"{prefix}{value}")
            continue

        if operand.kind == "buffer":
            if operand.bit:
                modifier = f".{int(operand.bit):X}"
            elif operand.index_reg:
                modifier = f"Z{operand.index_reg}"
            else:
                modifier = ""
            out.append(f"U{operand.unit:X}\\G{operand.number}{modifier}")
            continue

        if operand.kind != "device" or operand.number is None:
            out.append("?")
            continue

        number = int(operand.number)
        if operand.device_type == "P":
            out.append(f"#P{number}")
            continue
        dev_text = format_device(operand.device_type, number) if operand.device_type else f"?{number}"
        if operand.index_reg:
            out.append(f"{dev_text}Z{operand.index_reg}")
        elif operand.digit:
            out.append(f"K{operand.digit}{dev_text}")
        elif operand.bit:
            out.append(f"{dev_text}.{int(operand.bit):X}")
        else:
            out.append(dev_text)

    return out


# Device spelling lives in gx3_device_name so every command agrees on it; these
# names stay importable from here for the modules that already use them.
def parse_display_device(text: str) -> tuple[str, int] | None:
    if text.startswith("#"):
        text = text[1:]
    ug = re.fullmatch(r"U([0-9A-F]+)\\G(\d+)(?:\..+)?", text)
    if ug:
        return f"U{ug.group(1)}G", int(ug.group(2))
    m = re.fullmatch(r"([A-Z]+)(-?[0-9A-F]+)(?:Z\d+)?(?:\..+)?", text)
    if not m:
        return None
    dev_type = m.group(1)
    base = device_radix(dev_type)
    try:
        return dev_type, int(m.group(2), base)
    except ValueError:
        return None


OPCODE_SUFFIX_RE = re.compile(r"__(\d+)$")


def display_opcode(op: str) -> str:
    m = OPCODE_SUFFIX_RE.search(op)
    if not m:
        return op
    base = op[: m.start()]
    if m.group(1) == "32":
        return f"D{base}"
    return base


# ---------------------------------------------------------------------------
# Rung model: one op per drawable element, with grid position.


class Op:
    def __init__(
        self,
        role: str,
        x: int,
        y: int,
        ct_code: str,
        operands: list[str],
        element_kind: str,
        is_32bit: bool = False,
        note: str = "",
    ) -> None:
        self.role = role
        self.x = x
        self.y = y
        self.ct_code = ct_code
        self.operands = operands
        self.element_kind = element_kind
        self.is_32bit = is_32bit
        self.note = note

    def opcode_text(self) -> str:
        base = display_opcode(self.role)
        # 32-bit variants of symbolic ops (=, <>, +, ...) print with a D
        # prefix; named ops already carry their width in the name
        if self.is_32bit and base and not base[0].isalpha() and not base[0] == "$":
            return f"D{base}"
        return base

    @property
    def is_contact(self) -> bool:
        return self.role in {"a", "b"}

    @property
    def is_coil(self) -> bool:
        return self.role == "c"

    @property
    def is_inline_box(self) -> bool:
        # comparison / condition instruction drawn inside the logic flow
        return (not self.is_contact) and (not self.is_coil) and self.element_kind == "ct"

    @property
    def is_driver_box(self) -> bool:
        return (not self.is_contact) and (not self.is_coil) and self.element_kind != "ct"


def parse_rung(
    row: LadderRow, labels: LabelResolver | None = None
) -> tuple[list[Op], list[tuple[int, int]], list[tuple[int, int, int]]]:
    """Return (ops, verticals[(x,y)], wires[(x,y,end_x)])."""
    tokens = header_tokens(row.data)
    header_ops = parse_header_ops(row.data)
    raw_elements = extract_elements(row.data)
    ce_elements = [e for e in raw_elements if "s=ce{" in e]

    ops: list[Op] = []
    wires: list[tuple[int, int, int]] = []
    op_index = 0
    for raw in raw_elements:
        meta = element_meta(raw)
        pos = parse_pos(str(meta.get("pos", "")))
        if pos is None:
            continue
        x, y = pos
        if str(meta.get("element_kind", "")) == "wire" or raw.startswith("e{s=wire"):
            wires.append((x, y, x + 1))
            continue
        if op_index >= len(header_ops):
            continue
        hop = header_ops[op_index]
        args_text = extract_args_text(raw)
        raw_args = top_level_items(args_text) if args_text else []
        next_op_token = (
            header_ops[op_index + 1].token_index
            if op_index + 1 < len(header_ops)
            else len(tokens)
        )
        arg_tokens = tokens[hop.token_index + 1 : next_op_token]
        operands = display_operands(raw_args, arg_tokens, labels)
        note = ""
        if ":note=" in raw and arg_tokens:
            note = arg_tokens[-1]
        ops.append(
            Op(
                role=hop.op,
                x=x,
                y=y,
                ct_code=str(meta.get("ct_code", "")),
                operands=operands,
                element_kind=str(meta.get("element_kind", "")),
                is_32bit="vt=A32" in raw,
                note=note,
            )
        )
        op_index += 1

    verticals = [(int(xt), int(yt)) for xt, yt in VERTICAL_RE.findall(row.data)]
    return ops, verticals, wires


POINTER_RE = re.compile(r"p\{s=d\{s=#:a=(-?\d+)[^{}]*\}:pos=(\d+),(\d+)\}")


def parse_pointers(data: str) -> list[tuple[int, int]]:
    """Pointer labels attached to the rung: [(pointer_no, y)]."""
    return [(int(m.group(1)), int(m.group(3))) for m in POINTER_RE.finditer(data)]


# ---------------------------------------------------------------------------
# Rendering.


def cell_col(x: int) -> int:
    return CELL0 + CELL_W * x


def boundary_col(x: int) -> int:
    return CELL0 + CELL_W * x - 2


def box_text(opcode: str, operands: list[str]) -> str:
    if not operands:
        return "[" + pad_field(opcode, 10) + "]"
    fields = [pad_field(opcode, 13)]
    for operand in operands[:-1]:
        fields.append(pad_field(operand, 14))
    fields.append(pad_field(operands[-1], 11))
    return "[" + "".join(fields) + "]"


def box_cells(operands: list[str]) -> int:
    return 1 + len(operands) if operands else 1


def load_print_comments(root: Path) -> dict[tuple[str, int], str]:
    """Display comments exactly as stored (no strip: GX wraps raw text,
    so leading/trailing fullwidth spaces are layout-significant)."""
    import sqlite3

    comment_db = find_comment_db(root)
    if comment_db is None or not comment_db.exists():
        return {}
    con = sqlite3.connect(f"file:{comment_db}?mode=ro", uri=True)
    cur = con.cursor()
    type_by_code = {code: dev_type for dev_type, code in DEVICE_CODE_BY_TYPE.items()}
    # In the comment DB ZR comments are stored under DevCode 40 (verified
    # against a real GX Works3 print); plain code 35 rows do not appear.
    type_by_code[40] = "ZR"
    type_by_code[101] = "P"  # pointer comments
    comments: dict[tuple[str, int], str] = {}
    for seq, dev_code, ext_code, ext_no, dev_no in cur.execute(
        "select SEQ, DevCode, ExtCode, ExtNo, DevNoLow from DEVICE_DATA"
    ).fetchall():
        if int(ext_code or 0) == 208:  # buffer memory U<unit>\G<no>
            dev_type = f"U{int(ext_no):X}G"
        else:
            dev_type = type_by_code.get(int(dev_code), "")
        if not dev_type:
            continue
        by_no: dict[int, str] = {}
        for cmt_no, text in cur.execute(
            """
            select CmtNo, CmtData from COMMENT_DATA
            where DeviceSEQ=? and coalesce(DelFlag, 0)=0
              and coalesce(CmtData, '')<>''
            order by CmtNo
            """,
            (seq,),
        ).fetchall():
            by_no.setdefault(int(cmt_no), str(text))
        value = by_no.get(5) or by_no.get(6) or (next(iter(by_no.values())) if by_no else "")
        if value:
            comments[(dev_type, int(dev_no))] = value
    con.close()
    return comments


def comment_text_for(device: str, comments: dict[tuple[str, int], str]) -> str:
    parsed = parse_display_device(device)
    if parsed is None:
        return ""
    return comments.get(parsed, "")


def live_value_label(value: object) -> str:
    if isinstance(value, bool):
        return "ON" if value else "OFF"
    if isinstance(value, (int, float)):
        return f"{value}"
    return str(value)


def truthy_live_value(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        text = value.strip().upper()
        if text in {"ON", "TRUE", "1"}:
            return True
        if text in {"OFF", "FALSE", "0"}:
            return False
    return None


def next_device(device: str, offset: int) -> str | None:
    parsed = parse_display_device(device)
    if parsed is None:
        return None
    dev_type, number = parsed
    return format_device(dev_type, number + offset)


def load_live_values(path: str | None) -> dict[str, object]:
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    if isinstance(data, dict) and isinstance(data.get("values"), dict):
        for device, value in data["values"].items():
            parsed = parse_display_device(str(device))
            values[format_device(*parsed) if parsed else str(device).upper()] = value
        return values
    if isinstance(data, dict) and isinstance(data.get("values"), list) and data.get("device"):
        for offset, value in enumerate(data["values"]):
            device = next_device(str(data["device"]), offset)
            if device:
                values[device] = value
        return values
    if isinstance(data, list):
        for item in data:
            if not isinstance(item, dict) or "device" not in item or "value" not in item:
                continue
            parsed = parse_display_device(str(item["device"]))
            values[format_device(*parsed) if parsed else str(item["device"]).upper()] = item["value"]
        return values
    raise ValueError("live values must be a live-read JSON object, a device mapping, or a list of device/value objects")


def place_live_annotation(
    canvas: "RungCanvas",
    y: int,
    col: int,
    role: str,
    device: str,
    live_values: dict[str, object],
) -> None:
    if not live_values:
        return
    parsed = parse_display_device(device)
    key = format_device(*parsed) if parsed else device.upper()
    if key not in live_values:
        return
    value = live_values[key]
    state = truthy_live_value(value)
    suffix = ""
    if role == "a":
        suffix = "pass" if state is True else "block" if state is False else ""
    elif role == "b":
        suffix = "pass" if state is False else "block" if state is True else ""
    text = f"live:{live_value_label(value)}" + (f" {suffix}" if suffix else "")
    canvas.comment_line(y, 0).put(col, pad_field(text, COMMENT_W)[:COMMENT_W])


DIGIT_DEV_RE = re.compile(r"^K\d([A-Z]+)([0-9A-F]+)$")


def operand_comment_device(operand: str) -> str:
    """Device whose comment is shown under an operand field."""
    if not operand or operand[0] in "\"":
        return ""
    m = DIGIT_DEV_RE.match(operand)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    if re.fullmatch(r"[KHE]-?[0-9A-F.]+", operand):
        return ""
    return operand


class RungCanvas:
    """Per grid row: [note,] device, blank, symbol, comments x4, spacer."""

    def __init__(self, height: int, note_rows: set[int] | None = None) -> None:
        self.block = 3 + COMMENT_LINES + 1
        self.note_rows = note_rows or set()
        self.offsets: list[int] = []
        total = 0
        for y in range(height):
            self.offsets.append(total)
            total += self.block + (1 if y in self.note_rows else 0)
        self.lines = [Line() for _ in range(total)]
        self.height = height

    def line(self, y: int, offset: int) -> Line:
        base = self.offsets[y] + (1 if y in self.note_rows else 0)
        return self.lines[base + offset]

    def note_line(self, y: int) -> Line:
        return self.lines[self.offsets[y]]

    def line_index(self, y: int, offset: int) -> int:
        return self.offsets[y] + (1 if y in self.note_rows else 0) + offset

    def device_line(self, y: int) -> Line:
        return self.line(y, 0)

    def symbol_line(self, y: int) -> Line:
        return self.line(y, 2)

    def comment_line(self, y: int, i: int) -> Line:
        return self.line(y, 3 + i)


def op_cells_of(op: Op) -> int:
    if op.is_contact or op.is_coil:
        return 1
    if op.is_inline_box and display_opcode(op.role) == "INV" and not op.operands:
        return 1
    return box_cells(op.operands)


def blocked(
    a_lend: int,
    b_lstart: int,
    incident_x: set[int],
) -> bool:
    """A horizontal gap is not wired when a vertical (incident to this row)
    stands strictly between the two elements: the right element then taps
    that vertical instead of continuing the left chain."""
    return any(a_lend < vx <= b_lstart for vx in incident_x)


def gap_is_wired(
    a: tuple[int, int, int, int, str],
    b: tuple[int, int, int, int, str],
    incident_x: set[int],
) -> bool:
    """Decide whether the drawn gap between two row items carries a wire.

    - the left chain ends where it merges into a vertical (unless the item
      IS that vertical's stub)
    - a vertical strictly between the two items separates them
    - a vertical at the right item's position feeds that element from the
      branch, so nothing crosses the gap -- but wiring INTO a vertical stub
      (a right-side merge) is a real connection
    """
    _, _, _, a_lend, a_kind = a
    _, _, b_lstart, _, b_kind = b
    if a_kind == "stub" and b_kind == "stub":
        # two verticals with nothing between them: a real bridge would be
        # stored as explicit wire elements
        return False
    for vx in incident_x:
        if vx == a_lend and not (a_kind == "stub" and vx == a[2]):
            return False
        if a_lend < vx < b_lstart:
            return False
        if vx == b_lstart and b_kind != "stub" and b_lstart > a_lend:
            return False
    return True


def render_rung(
    row: LadderRow,
    comments: dict[tuple[str, int], str],
    step: int | None,
    live_values: dict[str, object] | None = None,
    labels: LabelResolver | None = None,
) -> list[str]:
    ops, verticals, wires = parse_rung(row, labels)
    vset_logical = set(verticals)
    max_y = 0
    for op in ops:
        max_y = max(max_y, op.y)
    for _, y in verticals:
        max_y = max(max_y, y)
    for _, y, _ in wires:
        max_y = max(max_y, y)

    # ---- fold rungs wider than the printed 12-cell grid --------------------
    # The overflowing right part of the grid moves onto continuation rows
    # below the rung; rows whose wiring crosses the fold are linked with
    # "K<n> →" markers (exactly like the GX Works3 print output).
    right_markers: dict[int, str] = {}
    left_markers: dict[int, str] = {}
    moved_ops = [op for op in ops if op.x + op_cells_of(op) > N_CELLS]
    if moved_ops:
        # every op that pokes past the marker cell moves too
        moved_ops = [op for op in ops if op.x + op_cells_of(op) > N_CELLS - 1]
        x_base = min(op.x for op in moved_ops)
        moved_ids = {id(op) for op in moved_ops}
        incident_by_row: dict[int, set[int]] = {}
        for vx, vy in vset_logical:
            incident_by_row.setdefault(vy - 1, set()).add(vx)
            incident_by_row.setdefault(vy, set()).add(vx)

        crossing_rows: list[int] = []
        cont_content_rows: list[int] = []
        for y in sorted({op.y for op in ops}):
            stay = [op for op in ops if op.y == y and id(op) not in moved_ids]
            moved_here = [op for op in ops if op.y == y and id(op) in moved_ids]
            if not moved_here and not any(
                vx >= x_base for vx in incident_by_row.get(y, set())
            ):
                continue
            cont_content_rows.append(y)
            if stay and moved_here:
                last_stay = max(op.x + op_cells_of(op) for op in stay)
                first_moved = min(op.x for op in moved_here)
                if not blocked(last_stay, first_moved, incident_by_row.get(y, set())):
                    crossing_rows.append(y)

        cont_pos = {y: max_y + 1 + i for i, y in enumerate(cont_content_rows)}
        for i, y in enumerate(crossing_rows):
            label = f"K{i}"
            right_markers[y] = label
            left_markers[cont_pos[y]] = label

        for op in moved_ops:
            op.x = op.x - x_base + 1
            op.y = cont_pos[op.y]
        new_verticals: list[tuple[int, int]] = []
        for vx, vy in verticals:
            if vx < x_base:
                new_verticals.append((vx, vy))
                continue
            upper = cont_pos.get(vy - 1)
            lower = cont_pos.get(vy)
            if upper is None or lower is None:
                continue
            for py in range(upper + 1, lower + 1):
                new_verticals.append((vx - x_base + 1, py))
        verticals = new_verticals
        new_wires = []
        for wx, wy, wend in wires:
            if wx + 1 <= N_CELLS - 1:
                new_wires.append((wx, wy, wend))
            elif wx >= x_base and wy in cont_pos:
                new_wires.append((wx - x_base + 1, cont_pos[wy], wend - x_base + 1))
        wires = new_wires
        max_y = max_y + len(cont_content_rows)

    height = max_y + 1
    note_rows = {op.y for op in ops if op.note}
    canvas = RungCanvas(height, note_rows)

    # rails
    for idx, line in enumerate(canvas.lines):
        line.put(LEFT_RAIL, VLINE)
        line.put(RIGHT_RAIL, VLINE)

    # step number on first symbol line
    if step is not None:
        label = f"({step})"
        canvas.symbol_line(0).put(STEP_END - len(label), label)

    # pointer labels (subroutine / jump targets) in the left margin,
    # with the pointer's device comment wrapped underneath
    for pointer_no, py in parse_pointers(row.data):
        if 0 <= py <= max_y:
            canvas.symbol_line(py).put(2, f"#P{pointer_no}")
            text = comments.get(("P", pointer_no), "")
            for i, part in enumerate(wrap_display(text, COMMENT_W, COMMENT_LINES)):
                canvas.comment_line(py, i).put(2, part)

    # horizontal content per row
    ends_at_rail: set[int] = set()
    for x, y, end_x in wires:
        line = canvas.symbol_line(y)
        line.put(cell_col(x), HLINE * (CELL_W // 2))

    # items per row: (drawn_start, drawn_end, logical_start, logical_end, kind)
    items_by_y: dict[int, list[tuple[int, int, int, int, str]]] = {}

    def add_item(
        y: int, dstart: int, dend: int, lstart: int, lend: int, kind: str = "op"
    ) -> None:
        items_by_y.setdefault(y, []).append((dstart, dend, lstart, lend, kind))

    for x, y, end_x in wires:
        canvas.symbol_line(y).put(cell_col(x), HLINE * (CELL_W // 2))
        add_item(y, cell_col(x), cell_col(x) + CELL_W, x, x + 1)

    for op in ops:
        y = op.y
        sym_line = canvas.symbol_line(y)
        dev_line = canvas.device_line(y)
        col = cell_col(op.x)
        if op.is_contact:
            sym_line.put(col, contact_symbol(op.role, op.ct_code))
            sym_line.put(col + 8, HLINE * 3)
            device = op.operands[0] if op.operands else "?"
            dev_line.put(col, device)
            place_comment(canvas, y, col, comment_text_for(device, comments))
            place_live_annotation(canvas, y, col, op.role, device, live_values or {})
            add_item(y, col, col + CELL_W, op.x, op.x + 1)
        elif (
            op.is_inline_box
            and op.opcode_text() in INLINE_SYMBOL_OPS
            and not op.operands
        ):
            sym_line.put(col, HLINE + INLINE_SYMBOL_OPS[op.opcode_text()] + HLINE * 5)
            add_item(y, col, col + CELL_W, op.x, op.x + 1)
        elif op.is_coil:
            lx = max(op.x, 0)
            col = RIGHT_RAIL - 12
            sym_line.put(col, HLINE + COIL + HLINE * 4)
            device = op.operands[0] if op.operands else "?"
            dev_line.put(col, device)
            place_comment(canvas, y, col, comment_text_for(device, comments))
            place_live_annotation(canvas, y, col, "coil", device, live_values or {})
            ends_at_rail.add(y)
            add_item(y, col, RIGHT_RAIL, lx, lx)
            if cell_col(lx) < col:
                add_item(y, cell_col(lx), cell_col(lx), lx, lx, "stub")
        elif op.is_inline_box:
            text = box_text(op.opcode_text(), op.operands)
            sym_line.put(col, text)
            fill_from = col + text_width(text)
            fill_to = cell_col(op.x + box_cells(op.operands))
            if fill_to > fill_from:
                sym_line.put(fill_from, HLINE * ((fill_to - fill_from) // 2))
            place_box_comments(canvas, y, col, op.operands, comments)
            add_item(y, col, max(fill_to, fill_from), op.x, op.x + box_cells(op.operands))
        else:  # driver box, right aligned
            text = box_text(op.opcode_text(), op.operands)
            col = RIGHT_RAIL - text_width(text)
            sym_line.put(col, text)
            place_box_comments(canvas, y, col, op.operands, comments)
            ends_at_rail.add(y)
            lx = max(op.x, 0)
            add_item(y, col, RIGHT_RAIL, lx, lx)
            if cell_col(lx) < col:
                add_item(y, cell_col(lx), cell_col(lx), lx, lx, "stub")
        if op.note:
            canvas.note_line(y).put(col, op.note)

    # wrap markers: "K<n> →" in the last cell of the folded row and in the
    # first cell of its continuation row
    marker_text_w = COMMENT_W  # 12 display columns: label padded to 10 + arrow
    for y, label in right_markers.items():
        col = RIGHT_RAIL - marker_text_w
        canvas.symbol_line(y).put(col, pad_field(label, 10) + "→")
        ends_at_rail.add(y)
        add_item(y, col, RIGHT_RAIL, N_CELLS - 1, N_CELLS - 1, "marker")
    for y, label in left_markers.items():
        canvas.symbol_line(y).put(CELL0, pad_field(label, 10) + "→")
        add_item(y, CELL0, CELL0 + marker_text_w, 0, 0, "marker")

    # the first row always leaves the left rail
    add_item(0, LEFT_RAIL, CELL0, 0, 0, "rail")

    # left-rail connector: row 0 and rows whose leftmost element sits at x=0
    rail_rows = {0} | {
        y
        for y, items in items_by_y.items()
        if min(d for d, _, _, _, _ in items) == CELL0
    }
    for y in rail_rows:
        canvas.symbol_line(y).put(LEFT_RAIL, "├")

    # verticals participate as zero-width connection stubs
    vset = set(verticals)
    incident: dict[int, set[int]] = {}
    for vx, vy in vset:
        incident.setdefault(vy - 1, set()).add(vx)
        incident.setdefault(vy, set()).add(vx)
    # ... where they terminate or where an element taps them at its own x
    ops_x_by_y: dict[int, set[int]] = {}
    for op in ops:
        ops_x_by_y.setdefault(op.y, set()).add(op.x)
    for x, vy in vset:
        b = boundary_col(x) if x > 0 else LEFT_RAIL
        for y in (vy - 1, vy):
            up = (x, y) in vset
            down = (x, y + 1) in vset
            connects = up != down or x in ops_x_by_y.get(y, set())
            if connects and 0 <= y <= max_y and y in items_by_y:
                add_item(y, b, b + 2, x, x, "stub")

    # wire the gaps between consecutive connected items on each row
    for y, items in items_by_y.items():
        items.sort()
        sym_line = canvas.symbol_line(y)
        for a, b in zip(items, items[1:]):
            if b[0] <= a[1]:
                continue
            if not gap_is_wired(a, b, incident.get(y, set())):
                continue
            sym_line.put(a[1], HLINE * ((b[0] - a[1]) // 2))

    # right rail connector on driver rows
    for y in ends_at_rail:
        canvas.symbol_line(y).put(RIGHT_RAIL, "┤")

    # verticals: v{pos=x,y} connects symbol rows y-1 and y at boundary x
    for x, y in sorted(vset):
        b = boundary_col(x) if x > 0 else LEFT_RAIL
        # intermediate physical lines between the two symbol lines
        start_idx = canvas.line_index(y - 1, 3)
        end_idx = canvas.line_index(y, 2)
        for idx in range(start_idx, end_idx):
            canvas.lines[idx].put(b, VLINE)

    junction_points = {(x, y - 1) for x, y in vset} | {(x, y) for x, y in vset}
    for x, y in sorted(junction_points):
        if y < 0 or y > max_y:
            continue
        b = boundary_col(x) if x > 0 else LEFT_RAIL
        sym = canvas.symbol_line(y)
        up = (x, y) in vset
        down = (x, y + 1) in vset
        left = sym.char_at(b - 1) not in (" ",)
        right = sym.char_at(b + 2) not in (" ",)
        ch = JUNCTION.get((up, down, left, right))
        if ch:
            sym.put(b, ch)

    out = [line.text() for line in canvas.lines]
    # trim trailing all-blank spacer lines? keep fixed block layout
    return out


def place_comment(canvas: RungCanvas, y: int, col: int, text: str) -> None:
    if not text:
        return
    for i, part in enumerate(wrap_display(text, COMMENT_W, COMMENT_LINES)):
        canvas.comment_line(y, i).put(col, part)


def place_box_comments(
    canvas: RungCanvas,
    y: int,
    box_col: int,
    operands: list[str],
    comments: dict[tuple[str, int], str],
) -> None:
    for i, operand in enumerate(operands):
        device = operand_comment_device(operand)
        if not device:
            continue
        text = comment_text_for(device, comments)
        if not text:
            continue
        place_comment(canvas, y, box_col + 14 * (i + 1), text)


def render_end_block(step: int | None) -> list[str]:
    lines = [Line() for _ in range(3 + COMMENT_LINES + 1)]
    for line in lines:
        line.put(LEFT_RAIL, VLINE)
        line.put(RIGHT_RAIL, VLINE)
    sym = lines[2]
    if step is not None:
        label = f"({step})"
        sym.put(STEP_END - len(label), label)
    sym.put(LEFT_RAIL, "├")
    text = box_text("END", [])
    col = RIGHT_RAIL - text_width(text)
    sym.put(CELL0, HLINE * ((col - CELL0) // 2))
    sym.put(col, text)
    sym.put(RIGHT_RAIL, "┤")
    return [line.text() for line in lines]


def render_statement(row_data: str) -> list[str]:
    from gx3cli.extract_gx3_extended_instruction_knowledge import extract_title_text

    text = extract_title_text(row_data)
    return [f"  {text}"] if text else []


def render_entries(
    root: Path,
    lddb_name: str,
    comments: dict[tuple[str, int], str],
    program_map: ProgramMap | None,
    live_values: dict[str, object] | None = None,
) -> list[dict]:
    """Render every row once, keeping section/pos/device metadata for filtering.

    Each entry: {blocktype, pos, title, devices, lines}. Concatenating all
    entries' ``lines`` in order reproduces the full GX-print output byte-for-byte.
    """
    from gx3cli.gx3_intermediate_tool import read_ladder_rows
    from gx3cli.extract_gx3_extended_instruction_knowledge import extract_title_text
    from gx3cli.gx3_arg_decode import parse_row_occurrences

    labels = load_label_resolver(root)
    rows_by_db = read_ladder_rows(root)
    raw_rows = rows_by_db.get(lddb_name)
    if raw_rows is None:
        raise SystemExit(f"LDDB not found: {lddb_name}")

    entries: list[dict] = []
    for raw in raw_rows:
        blocktype = int(raw["blocktype"])
        data = str(raw["data"])
        pos = int(float(str(raw["pos"]))) if raw["pos"] is not None else None
        if blocktype in (1, 2):
            entries.append({
                "blocktype": blocktype, "pos": pos,
                "title": extract_title_text(data),
                "devices": frozenset(), "live_devices": [], "step": None, "lines": render_statement(data),
            })
            continue
        if blocktype == 5:  # END block
            step = program_map.step_of(lddb_name, pos) if program_map else None
            entries.append({
                "blocktype": 5, "pos": pos, "title": None,
                "devices": frozenset(), "live_devices": [], "step": step, "lines": render_end_block(step),
            })
            continue
        if blocktype != 0:
            entries.append({
                "blocktype": blocktype, "pos": pos, "title": None,
                "devices": frozenset(), "live_devices": [], "step": None, "lines": [],
            })
            continue
        row = LadderRow(
            lddb=lddb_name,
            pos=pos,
            block_id=str(raw["id"]),
            title="",
            blocktype=blocktype,
            rowsize=int(raw["rowsize"] or 0),
            data=data,
            dim="",
            operations=[],
            parse_status="",
        )
        step = program_map.step_of(lddb_name, row.pos) if program_map else None
        try:
            ops, _status = parse_row_occurrences(data, labels)
            devices = frozenset(occ.device for _r, _o, occs, _c in ops for occ in occs)
            live_devices = [
                {"role": role, "opcode": opcode, "device": occ.device}
                for role, opcode, occs, _c in ops
                for occ in occs
            ]
        except Exception:
            devices = frozenset()
            live_devices = []
        entries.append({
            "blocktype": 0, "pos": pos, "title": None,
            "devices": devices, "live_devices": live_devices, "step": step, "lines": render_rung(row, comments, step, live_values, labels),
        })
    return entries


def render_program(
    root: Path,
    lddb_name: str,
    comments: dict[tuple[str, int], str],
    program_map: ProgramMap | None,
    title: str = "",
    live_values: dict[str, object] | None = None,
) -> list[str]:
    out: list[str] = []
    for entry in render_entries(root, lddb_name, comments, program_map, live_values):
        out.extend(entry["lines"])
    return out


def live_overlay_for_devices(devices: list[dict[str, str]], live_values: dict[str, object]) -> list[dict[str, object]]:
    overlay: list[dict[str, object]] = []
    for row in devices:
        device = row["device"]
        role = row.get("role", "")
        parsed = parse_display_device(device)
        key = format_device(*parsed) if parsed else device.upper()
        item: dict[str, object] = {"device": key, "role": role, "condition": "unknown"}
        if key in live_values:
            value = live_values[key]
            state = truthy_live_value(value)
            item["value"] = value
            item["state"] = state
            if role == "a":
                item["condition"] = "pass" if state is True else "block" if state is False else "unknown"
            elif role == "b":
                item["condition"] = "pass" if state is False else "block" if state is True else "unknown"
            else:
                item["condition"] = "observed"
        overlay.append(item)
    return overlay


def entries_to_json(root: Path, lddb_name: str, entries: list[dict], live_values: dict[str, object]) -> dict[str, object]:
    rows: list[dict[str, object]] = []
    for entry in entries:
        devices = sorted(str(device) for device in entry["devices"])
        live_devices = entry.get("live_devices") or [{"device": device, "role": ""} for device in devices]
        rows.append(
            {
                "blocktype": entry["blocktype"],
                "pos": entry["pos"],
                "step": entry.get("step"),
                "title": entry["title"],
                "devices": devices,
                "live_overlay": live_overlay_for_devices(live_devices, live_values),
                "lines": entry["lines"],
            }
        )
    return {"root": str(root), "program": lddb_name, "rows": rows}


def scan_sections(entries: list[dict]) -> list[dict]:
    """Group entries into sections delimited by 2-space title rows."""
    sections: list[dict] = []
    cur: dict | None = None
    for e in entries:
        if e["blocktype"] in (1, 2) and e["title"]:
            cur = {"title": e["title"], "start_pos": None, "end_pos": None, "rungs": 0}
            sections.append(cur)
        elif e["blocktype"] == 0 and cur is not None:
            if cur["start_pos"] is None:
                cur["start_pos"] = e["pos"]
            cur["end_pos"] = e["pos"]
            cur["rungs"] += 1
    return sections


def format_section_list(sections: list[dict]) -> list[str]:
    lines = [f"{len(sections)} sections (use --section \"title\" or --pos-range A-B):"]
    for s in sections:
        span = f"{s['start_pos']}..{s['end_pos']}" if s["start_pos"] is not None else "(no rungs)"
        lines.append(f"  pos {span:<18} rungs={s['rungs']:<4} {s['title']}")
    return lines


def select_entries(
    entries: list[dict],
    sections: list[str] | None = None,
    pos_range: tuple[int, int] | None = None,
    device: str | None = None,
) -> list[dict]:
    """Keep only entries matching any given filter (union). Section titles that
    introduce a kept rung are kept for context."""
    keep = [False] * len(entries)
    if pos_range is not None:
        lo, hi = pos_range
        for i, e in enumerate(entries):
            if e["pos"] is not None and lo <= e["pos"] <= hi:
                keep[i] = True
    if sections:
        selecting = False
        for i, e in enumerate(entries):
            # Only a non-empty title starts/ends a section (matches scan_sections);
            # empty-title statement rows are invisible and must not break selection.
            if e["blocktype"] in (1, 2) and e["title"]:
                selecting = any(s in e["title"] for s in sections)
            if selecting:
                keep[i] = True
    if device is not None:
        target = device.strip().upper()
        for i, e in enumerate(entries):
            if e["blocktype"] == 0 and target in {d.upper() for d in e["devices"]}:
                keep[i] = True
                for j in range(i - 1, -1, -1):
                    if entries[j]["blocktype"] in (1, 2) and entries[j]["title"]:
                        keep[j] = True
                        break
    return [e for i, e in enumerate(entries) if keep[i]]


def resolve_lddb(root: Path, program: str, pm: ProgramMap) -> str:
    candidates = sorted(root.glob("*_LDDB.db"))
    for p in candidates:
        if pm.label(p.name) == program:
            return p.name
    if program in pm.program_files and len(candidates) == 1:
        return candidates[0].name
    for p in candidates:
        if p.name.startswith(program):
            return p.name
    names = ", ".join(pm.label(p.name) for p in candidates)
    raise SystemExit(f"program not found: {program} (available: {names})")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a ladder program in GX Works3 print-text layout."
    )
    parser.add_argument("program", help="program name (POU / program file) or LDDB file name")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--title", default="", help="program title for the [Title] header")
    parser.add_argument("-o", "--output", help="write output to file (default stdout); parent dirs are created")
    parser.add_argument(
        "--encoding",
        choices=["utf-8", "utf-16"],
        default="utf-8",
        help="output file encoding (utf-16 matches GX Works3 print output)",
    )
    parser.add_argument(
        "--list-sections",
        action="store_true",
        help="list section titles with their pos range and rung count, then exit",
    )
    parser.add_argument(
        "--section",
        action="append",
        metavar="TITLE",
        help="render only sections whose title contains TITLE (repeatable)",
    )
    parser.add_argument(
        "--pos-range",
        metavar="A-B",
        help="render only rows whose step pos is within A-B (inclusive)",
    )
    parser.add_argument(
        "--device",
        help="render only rungs that reference DEVICE (any role), plus their section title",
    )
    parser.add_argument(
        "--live-values",
        help="JSON from gx3-cli live-read --format json, a device->value mapping, or a list of device/value objects",
    )
    parser.add_argument("--format", choices=["text", "json"], default="text", help="output rendered text or row JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    root = resolve_project_root(args.root)
    pm = load_program_map(root)
    lddb_name = args.program if args.program.endswith("_LDDB.db") else resolve_lddb(root, args.program, pm)
    comments = load_print_comments(root)
    live_values = load_live_values(args.live_values)

    filtering = args.list_sections or args.section or args.pos_range or args.device
    if not filtering:
        entries = render_entries(root, lddb_name, comments, pm, live_values=live_values)
        lines = [line for entry in entries for line in entry["lines"]]
    else:
        entries = render_entries(root, lddb_name, comments, pm, live_values=live_values)
        if args.list_sections:
            lines = format_section_list(scan_sections(entries))
            entries = []
        else:
            pos_range = None
            if args.pos_range:
                parts = args.pos_range.split("-", 1)
                if len(parts) != 2 or not parts[0].strip().isdigit() or not parts[1].strip().isdigit():
                    raise SystemExit(f"--pos-range must be A-B with integers (got {args.pos_range!r})")
                pos_range = (int(parts[0]), int(parts[1]))
            selected = select_entries(
                entries,
                sections=args.section or None,
                pos_range=pos_range,
                device=args.device,
            )
            if not selected:
                raise SystemExit("no rows matched --section/--pos-range/--device (try --list-sections)")
            lines = []
            for entry in selected:
                lines.extend(entry["lines"])
            entries = selected

    if args.format == "json":
        text = json.dumps(entries_to_json(root, lddb_name, entries, live_values), ensure_ascii=False, indent=2)
        if args.output:
            out_path = Path(args.output)
            if out_path.parent and not out_path.parent.exists():
                out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(text + "\n", encoding="utf-8")
            print(f"written: {args.output} ({len(entries)} rows)")
        else:
            print(text)
        return 0

    text = "\r\n".join(lines) + "\r\n"
    if args.output:
        out_path = Path(args.output)
        if out_path.parent and not out_path.parent.exists():
            out_path.parent.mkdir(parents=True, exist_ok=True)
        if args.encoding == "utf-16":
            out_path.write_bytes(b"\xff\xfe" + text.encode("utf-16-le"))
        else:
            out_path.write_text(text, encoding="utf-8", newline="")
        print(f"written: {args.output} ({len(lines)} lines)")
    else:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
