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
from gx3cli.gx3_arg_decode import write_indices
from gx3cli.gx3_label_resolve import LabelResolver, load_label_resolver
from gx3cli.gx3_output import add_format_argument, emit
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.review_gx3_project import LadderRow, load_rows


@dataclass(frozen=True)
class RungText:
    """One driven device and the condition that drives it."""

    lddb: str
    pos: int
    title: str
    opcode: str
    device: str
    condition: str

    @property
    def location(self) -> str:
        return f"{self.lddb}:{self.pos}"

    def to_line(self, width: int = 0) -> str:
        arrow = f"{self.opcode} {self.device}" if self.opcode not in ("", "OUT") else self.device
        return f"{self.location:<{width}}  {self.condition} -> {arrow}"


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


def driver_elements(row: LadderRow, labels: LabelResolver | None = None) -> list[FlowElement]:
    """Every element the rung writes through, in reading order.

    output_elements_for() answers for one named device; a whole-program view
    needs all of them, and a rung can drive several.
    """
    elements = [e for e in positioned_elements(row, labels) if written_devices(e)]
    return sorted(elements, key=lambda element: (element.y, element.x))


def rung_texts(row: LadderRow, labels: LabelResolver | None = None) -> list[RungText]:
    out: list[RungText] = []
    for element in driver_elements(row, labels):
        try:
            condition = simplify(logic_to_text(enable_logic_for_output(row, element, labels)))
        except Exception:
            # A rung shape the topology reader cannot fold into an expression
            # is reported as such rather than skipped, so a program does not
            # quietly come back shorter than it is.
            condition = "?"
        for device in written_devices(element):
            out.append(
                RungText(
                    lddb=row.lddb,
                    pos=int(row.pos),
                    title=row.title or "",
                    opcode=(element.opcode or "").upper(),
                    device=device,
                    condition=condition,
                )
            )
    return out


def collect(root: Path, lddb: str = "", device: str = "") -> list[RungText]:
    labels = load_label_resolver(root)
    out: list[RungText] = []
    for row in load_rows(root, {}):
        if int(row.blocktype) != 0:
            continue
        if lddb and row.lddb != lddb:
            continue
        for text in rung_texts(row, labels):
            if device and text.device != device:
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
            "pos": item.pos,
            "title": item.title,
            "opcode": item.opcode,
            "device": item.device,
            "condition": item.condition,
        }
        for item in items
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print a program as one line per rung: condition -> driven device."
    )
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--program", default="", help="limit to one LDDB, e.g. 001_LDDB.db")
    parser.add_argument("--device", default="", help="limit to rungs driving this device")
    parser.add_argument("--no-titles", action="store_true", help="omit section titles")
    add_format_argument(parser, json_shorthand=False)
    args = parser.parse_args(argv)

    items = collect(Path(args.root), args.program, args.device)
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
