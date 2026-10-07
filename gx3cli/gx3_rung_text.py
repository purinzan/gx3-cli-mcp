from __future__ import annotations

"""A whole program as one line per rung, for reading rather than for print.

`ladder-print` reproduces the GX Works3 print layout, box drawing and all,
which is what you want when checking output against the engineering tool. It is
the wrong shape for anything that has to read the logic: the four rungs of the
smallest project here come to 11.6 KB at 280 columns, and reconstructing the
circuit from the rules costs more than reading it.

`matiec-st` already turns a rung into a boolean expression, but for one target
device at a time and wrapped in a MATIEC program with variable declarations,
because its job is to hand the logic to a syntax checker.

What was missing is the plain reading form -- every rung of a program, one line
each, source condition to driven device:

    001_LDDB.db:13  X10 AND M2 -> M10
    003_LDDB.db:24  (IN_Start OR Start_Latch) AND /IN_Stop -> Start_Latch

The topology is not re-derived here. The expression comes from the same
enable_logic_for_output() that matiec-st uses, so the two cannot disagree about
what a rung means.
"""

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gx3cli.gx3_ladder_logic import (
    FlowElement,
    enable_logic_for_output,
    logic_to_text,
    positioned_elements,
)
from gx3cli.gx3_ladder_csv import parse_csv_rows, read_csv_records
from gx3cli.gx3_intermediate_tool import extract_dim
from gx3cli.gx3_arg_decode import row_operations, write_indices
from gx3cli.gx3_ladder_print import load_print_comments, comment_text_for, operand_comment_device
from gx3cli.gx3_program_map import load_program_map
from gx3cli.gx3_label_resolve import LabelResolver, load_label_resolver
from gx3cli.gx3_output import add_format_argument, emit
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.review_gx3_project import LadderRow, load_rows, extract_title, operation_model


@dataclass(frozen=True)
class RungText:
    """One output instruction, its known write target, and incoming condition."""

    lddb: str
    pos: int
    title: str
    opcode: str
    device: str
    condition: str
    # The GX Works3 step number, where the program map knows it. It is not the
    # same number as pos, which is this format's own row offset -- and a bare
    # ":2048" reads exactly like a step to someone looking for the rung in GX
    # Works3.
    step: int | None = None
    comments: dict[str, str] | None = None
    operands: tuple[str, ...] = ()
    diagnostic: str = ""
    raw_args: tuple[str, ...] | None = None
    arg_tokens: tuple[str, ...] | None = None
    raw_data: str = ""

    @property
    def location(self) -> str:
        if self.step is not None:
            return f"{self.lddb} st{self.step}"
        return f"{self.lddb} pos{self.pos}"

    def to_line(self, width: int = 0) -> str:
        arrow = f"{self.opcode} {self.device}" if self.opcode not in ("", "OUT") else self.device
        if self.operands and (self.opcode != "OUT" or len(self.operands) > 1):
            arrow = f"{self.opcode} {' '.join(self.operands)}"
        line = f"{self.location:<{width}}  {self.condition} -> {arrow.rstrip()}"
        if self.diagnostic:
            line += "  # diagnostic=" + json.dumps(self.diagnostic, ensure_ascii=False)
        if self.comments:
            line += "  # " + ", ".join(
                f"{device}={json.dumps(text, ensure_ascii=False)}"
                for device, text in self.comments.items()
            )
        return line


# logic_to_text() brackets each leaf so a term is unambiguous inside a larger
# string. Reading a whole program, the brackets are noise on every line, and
# the parentheses already carry the grouping. They are stripped here rather
# than in logic_to_text, which other callers rely on as it is.
_LEAF = re.compile(r"\[([^\[\]]*)\]")


def simplify(condition: str) -> str:
    """Drop the leaf brackets, and the outermost parentheses if they wrap all."""
    text = _LEAF.sub(r"\1", condition)
    if text.startswith("(") and text.endswith(")"):
        depth = 0
        for index, char in enumerate(text):
            depth += (char == "(") - (char == ")")
            if depth == 0 and index < len(text) - 1:
                return text
        return text[1:-1]
    return text


def written_devices(element: FlowElement) -> list[str]:
    """The devices this element writes, or [] if it writes none.

    is_driver covers the coil-like roles: c, SET, RST, PLS, PLF and the OUT
    variants. A data instruction is not one of them, so MOV D0 D10 used to
    leave no line at all and a program came back missing every rung that moved
    a value. The manuals name the destination operand of every instruction, so
    the write positions decide it here.
    """
    written = [ref.device for ref in element.devices if ref.is_written]
    devices = [ref.device for ref in element.devices]
    if element.is_driver:
        return written or devices or ["?"]
    if written:
        return written
    opcode = (element.opcode or "").strip()
    if not opcode or not devices:
        return []
    # The write positions are operand positions. Passing len(devices) asked
    # the manuals about an instruction with fewer operands than this one has,
    # and the index then landed on a source: a "D+ D32706 D37426 D32706" was
    # reported as driving D37426, which it reads.
    argc = element.argc or len(devices)
    indices, _rmw = write_indices(opcode, argc)
    if not indices:
        return []
    refs = element.devices
    named = [ref.device for ref in refs if ref.arg_index in indices]
    if named:
        return named
    if argc != len(devices):
        # The operand at that position is a constant, or the device list has
        # been collapsed; naming one by position here would be a guess.
        return []
    return [devices[i] for i in sorted(indices) if i < len(devices)]


def rung_texts(
    row: LadderRow, labels: LabelResolver | None = None, step: int | None = None,
    comments: dict | None = None,
) -> list[RungText]:
    out: list[RungText] = []
    operations, status = row_operations(row, labels)
    elements = positioned_elements(row, labels) if status == "exact" else []
    if status != "exact" or sum(not element.is_wire for element in elements) != len(operations):
        # A missing header operation shifts every subsequent ce association.
        # Preserve the row rather than assigning guessed operands or writes.
        return [RungText(
            row.lddb, int(row.pos), row.title or "", "UNPARSED", "", "?", step=step,
            comments={} if comments is not None else None,
            diagnostic="partial decode: instruction alignment or element positions are incomplete",
            raw_data=row.data,
        )]
    by_index = {operation.op_index: operation for operation in operations}
    unsupported = {
        element.op_index: instruction_diagnostic(element)
        for element in elements if element.kind == "instruction"
    }
    unsupported = {index: message for index, message in unsupported.items() if message}
    row_diagnostic = "; ".join(unsupported.values())
    candidates = {}
    label_names = set()
    if comments is not None:
        for element in elements:
            for index, operand in enumerate(element.operands):
                # Use decoded operand identity, never device-looking label names
                # or quoted constants. Dynamic addresses must not borrow a base comment.
                refs = [ref for ref in element.devices if ref.arg_index == index]
                if any(ref.device_type == "LABEL" for ref in refs):
                    label_names.add(operand)
                    continue
                if not refs:
                    continue
                text = comment_text_for(operand_comment_device(operand), comments)
                if text:
                    candidates[operand] = text
    for name in label_names:
        candidates.pop(name, None)
    for element in sorted(
        (element for element in elements if written_devices(element)
         or (element.kind == "instruction" and (not element.is_condition or element.op_index in unsupported))),
        key=lambda element: (element.y, element.x),
    ):
        diagnostic = row_diagnostic
        try:
            condition = simplify(logic_to_text(enable_logic_for_output(row, element, labels)))
        except Exception:
            # A rung shape the topology reader cannot fold into an expression
            # is reported as such rather than skipped, so a program does not
            # quietly come back shorter than it is.
            condition = "?"
            diagnostic = "; ".join(filter(None, (diagnostic, "topology: incoming condition could not be resolved")))
        if row_diagnostic:
            condition = f"? (local condition: {condition})"
        unresolved = any("?" in operand and not operand.startswith('"') for operand in element.operands)
        if unresolved:
            diagnostic = "; ".join(filter(None, (diagnostic, "decode: unresolved operand or label")))
        operation = by_index[element.op_index]
        preserve_raw = unresolved or element.op_index in unsupported
        operands = tuple(element.operands) if element.kind == "instruction" else ()
        for device in written_devices(element) or [""]:
            if device == "?":
                device = ""
            out.append(
                RungText(
                    lddb=row.lddb,
                    pos=int(row.pos),
                    title=row.title or "",
                    opcode=(element.opcode or "").upper(),
                    device=device,
                    condition=condition,
                    step=step,
                    operands=operands,
                    diagnostic=diagnostic,
                    raw_args=tuple(operation.raw_args) if preserve_raw else None,
                    arg_tokens=tuple(operation.arg_tokens) if preserve_raw else None,
                    comments=visible_comments(condition, device, candidates, operands) if comments is not None else None,
                )
            )
    return out


def instruction_diagnostic(element: FlowElement) -> str:
    if element.role in {"MC", "MCR", "CALL", "CALLP", "CJ", "SCJ", "JMP", "RET", "IRET", "FOR", "NEXT"}:
        return f"unsupported control flow {element.opcode} at {element.x},{element.y}"
    if element.role != "EG" and write_indices(element.role, element.argc)[0] is None:
        return f"unsupported instruction {element.opcode} at {element.x},{element.y}: writes and effects unknown"
    return ""


# Quoted constants are consumed as whole tokens and never annotated. Taking
# whole identifiers prevents M1 from matching M10, D1.2, D1Z2 or a label suffix.
_COMMENT_TOKEN = re.compile(r'"(?:\\.|[^"\\])*"|[^\s()\[\],/<>!=&|]+')


def visible_comments(
    condition: str, device: str, candidates: dict[str, str], operands: tuple[str, ...] = (),
) -> dict[str, str]:
    names = _COMMENT_TOKEN.findall(condition) + [device, *operands]
    return {name: candidates[name] for name in names if name in candidates}


def matches_device(item: RungText, device: str) -> bool:
    # With unknown writes, a filter cannot establish that this row is unrelated.
    return not device or item.device.upper() == device.upper() or (not item.device and bool(item.diagnostic))


_DATA_COLUMNS = (
    "data",
    "ladder_data",
    "block_data",
    "rung_data",
    "serialized",
    "serialized_data",
    "body",
)
_POS_COLUMNS = ("pos", "position", "step", "step_no", "stepno", "ステップ", "行")
_LDDB_COLUMNS = ("lddb", "program", "pou", "file", "プログラム", "POU")
_TITLE_COLUMNS = ("title", "section", "comment", "コメント", "タイトル")
_BLOCKTYPE_COLUMNS = ("blocktype", "block_type", "type")
_ROWSIZE_COLUMNS = ("rowsize", "row_size")


def _norm_header(value: str) -> str:
    return value.strip().lstrip("\ufeff").lower().replace(" ", "_").replace("-", "_")


def _first(row: dict[str, str], names: tuple[str, ...]) -> str:
    normalized = {_norm_header(k): v for k, v in row.items()}
    for name in names:
        key = _norm_header(name)
        if key in normalized and str(normalized[key]).strip():
            return str(normalized[key]).strip()
    return ""


def _to_int(value: str, default: int = 0) -> int:
    try:
        return int(float(str(value).strip()))
    except Exception:
        return default


def load_rows_from_csv(path: Path) -> list[LadderRow]:
    rows = read_csv_records(path)
    out: list[LadderRow] = []
    current_title = ""
    for index, raw in enumerate(rows, start=1):
        data = _first(raw, _DATA_COLUMNS)
        if not data:
            continue
        blocktype = _to_int(_first(raw, _BLOCKTYPE_COLUMNS), 0)
        if blocktype in {1, 2}:
            current_title = extract_title(data) or _first(raw, _TITLE_COLUMNS) or current_title
            continue
        if blocktype != 0:
            continue
        operations = operation_model(data)
        ce_count = data.count("s=ce{")
        parse_status = "exact" if ce_count == len(operations) else "partial"
        out.append(
            LadderRow(
                lddb=_first(raw, _LDDB_COLUMNS) or path.name,
                pos=_to_int(_first(raw, _POS_COLUMNS), index),
                block_id=str(raw.get("id") or raw.get("block_id") or index),
                title=_first(raw, _TITLE_COLUMNS) or current_title,
                blocktype=blocktype,
                rowsize=_to_int(_first(raw, _ROWSIZE_COLUMNS), 0),
                data=data,
                dim=extract_dim(data),
                operations=operations,
                parse_status=parse_status,
            )
        )
    return out


def collect_csv(path: Path, device: str = "") -> list[RungText]:
    rows = read_csv_records(path)
    if not rows:
        return []
    if any(_first(row, _DATA_COLUMNS) for row in rows):
        out: list[RungText] = []
        for row in load_rows_from_csv(path):
            for text in rung_texts(row):
                if not matches_device(text, device):
                    continue
                out.append(text)
        return out
    items = [
        RungText(item.instruction.program, item.instruction.step,
                 item.instruction.title, item.instruction.opcode, item.device,
                 item.condition, step=item.instruction.step,
                 operands=item.instruction.operands, diagnostic=item.diagnostic)
        for item in parse_csv_rows(path, rows)
    ]
    return [item for item in items if matches_device(item, device)]


def collect(root: Path, lddb: str = "", device: str = "", *, comments: bool = False) -> list[RungText]:
    labels = load_label_resolver(root)
    program_map = load_program_map(root)
    comment_map = load_print_comments(root) if comments else None
    out: list[RungText] = []
    for row in load_rows(root, {}):
        if int(row.blocktype) != 0:
            continue
        if lddb and row.lddb != lddb:
            continue
        step = program_map.step_of(row.lddb, int(row.pos))
        for text in rung_texts(row, labels, step, comment_map):
            if not matches_device(text, device):
                continue
            out.append(text)
    return out


def render_text(items: list[RungText], show_titles: bool = True) -> list[str]:
    width = max((len(item.location) for item in items), default=0)
    lines: list[str] = []
    current_title = None
    for item in items:
        if show_titles and item.title and item.title != current_title:
            current_title = item.title
            lines.append("")
            lines.append(f"# {item.title}")
        lines.append(item.to_line(width))
    return lines


def to_json(items: list[RungText]) -> list[dict[str, Any]]:
    return [
        {
            "lddb": item.lddb,
            # Both, named: pos is this format's row offset, step is the number
            # GX Works3 shows.
            "pos": item.pos,
            "step": item.step,
            "title": item.title,
            "opcode": item.opcode,
            "device": item.device,
            "condition": item.condition,
            **({"operands": list(item.operands)} if item.operands else {}),
            **({"diagnostic": item.diagnostic} if item.diagnostic else {}),
            **({"raw_args": list(item.raw_args)} if item.raw_args is not None else {}),
            **({"arg_tokens": list(item.arg_tokens)} if item.arg_tokens is not None else {}),
            **({"raw_data": item.raw_data} if item.raw_data else {}),
            **({"comments": item.comments} if item.comments is not None else {}),
        }
        for item in items
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a program as one line per rung: condition -> driven device."
    )
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--csv", default="", help="read GX Works3 listed-instruction CSV or internal ladder rows")
    parser.add_argument("--program", default="", help="limit to one LDDB, e.g. 001_LDDB.db")
    parser.add_argument("--device", default="", help="limit to rungs driving this device")
    parser.add_argument("--no-titles", action="store_true", help="omit section titles")
    parser.add_argument("--comments", action="store_true", help="append device comments on each line; include a comments map in JSON")
    add_format_argument(parser, json_shorthand=False)
    args = parser.parse_args(argv)

    try:
        items = collect_csv(Path(args.csv), args.device) if args.csv else collect(Path(args.root), args.program, args.device, comments=args.comments)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if args.csv and args.program:
        items = [item for item in items if item.lddb == args.program]
    if not items:
        print("no rungs found")
        return 0

    return emit(
        args,
        text=lambda: "\n".join(render_text(items, show_titles=not args.no_titles)).lstrip("\n"),
        data=lambda: to_json(items),
    )


if __name__ == "__main__":
    raise SystemExit(main())
