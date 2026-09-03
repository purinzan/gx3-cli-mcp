from __future__ import annotations

"""Read a rung, rebuild it, and check the two agree.

Every other check in this repository asks the tool whether it understood a
project, and takes its word. parse_status="exact" is something the decoder says
about itself, and this session found seven or eight rungs where it said that
and was wrong -- a dropped contact, an operand read as the count instead of the
destination, a label with no identity at all.

This is the one check that does not take the tool's word. A rung is read into
its enable logic, that logic is generated back into the intermediate spelling,
and the result is compared against the bytes it came from. A rung that comes
back identical was understood; one that does not is worth looking at.

Three outcomes, and the middle one matters:

  identical   the generated row matches the original byte for byte
  equivalent  the two rows differ in shape but read back to the same devices
              and accesses -- generation puts logic through DNF, so a shared
              branch becomes repeated contacts across two rows
  differs     the two disagree about which devices are touched, or how

Only "differs" is a finding. "equivalent" is the expected result for any rung
whose original topology shares a contact between branches, and rebuilding the
original shape is a separate problem from reading it correctly.

Rungs that cannot be turned into an AST at all are counted and named rather
than skipped: a rung gated by a normally-closed SM400 is always false and has
no logic to rebuild, which is the right answer, but a rung that fails for some
other reason is a gap worth seeing.
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gx3cli.extract_gx3_extended_instruction_knowledge import (
    LABEL_TOKEN_PREFIX,
    header_tokens,
)
from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_label_resolve import LabelResolver, load_label_resolver
from gx3cli.gx3_ladder_logic import enable_logic_for_output, positioned_elements
from gx3cli.gx3_output import add_format_argument, emit
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.review_gx3_project import LadderRow, load_rows

IDENTICAL = "identical"
EQUIVALENT = "equivalent"
DIFFERS = "differs"

# Output roles generate_rung() can rebuild. Anything else is reported as
# out of scope rather than counted as a failure to read.
_OUTPUT_KINDS = {"c": "coil", "SET": "set", "RST": "rst", "PLS": "pls"}


@dataclass
class Result:
    lddb: str
    pos: int
    device: str
    verdict: str
    note: str = ""

    @property
    def location(self) -> str:
        return f"{self.lddb}:{self.pos}"


@dataclass
class Summary:
    checked: int = 0
    identical: int = 0
    equivalent: int = 0
    differs: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    findings: list[Result] = field(default_factory=list)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1

    @property
    def agreement(self) -> float:
        """Share of checked rungs that read back the same, identical or not."""
        if not self.checked:
            return 0.0
        return round((self.identical + self.equivalent) / self.checked, 4)


def label_tokens_by_name(row: LadderRow, labels: LabelResolver) -> dict[str, str]:
    """Resolved label name back to the "_lid/..." token the header carries.

    Generation needs the reference, not the name: the name is what a human
    reads, the reference is what the row stores.
    """
    out: dict[str, str] = {}
    tokens = header_tokens(row.data)
    for index, token in enumerate(tokens):
        if token not in ("a", "b", "c") or index + 1 >= len(tokens):
            continue
        following = tokens[index + 1]
        if not following.startswith(LABEL_TOKEN_PREFIX):
            continue
        ref = labels.resolve_token(following)
        if ref is not None:
            out[ref.name] = following
    return out


def logic_to_ast(node: dict[str, Any], refs: dict[str, str]) -> dict[str, Any]:
    """The enable-logic tree as generate_rung() wants it."""
    op = node.get("op")
    if op == "contact":
        device = str(node.get("device") or node.get("raw_device") or "")
        if not device:
            raise ValueError("contact carries no device")
        device = refs.get(device, device)
        term: dict[str, Any] = {"device": device}
        return {"not": term} if node.get("role") == "b" else term
    if op in ("and", "or"):
        return {op: [logic_to_ast(arg, refs) for arg in node.get("args", [])]}
    raise ValueError(f"cannot rebuild a {op} node")


def _occurrences(data: str, labels: LabelResolver | None) -> set[tuple[str, str, str]]:
    """Which devices the row touches, and how -- as a set.

    Order and repetition are deliberately dropped. Generation puts logic
    through DNF, so a contact shared between two branches in the original
    appears once per branch in the rebuild. That is a different drawing of the
    same rung, not a different rung, and the question here is whether the two
    agree about which devices are read and written.
    """
    decoded, _status = parse_row_occurrences(data, labels)
    return {
        (role, occ.device, occ.access)
        for role, _opcode, occs, _consts in decoded
        for occ in occs
    }


def check_row(row: LadderRow, labels: LabelResolver, summary: Summary) -> Result | None:
    drivers = [e for e in positioned_elements(row, labels) if e.is_driver]
    if not drivers:
        summary.skip("no driven output")
        return None
    if len(drivers) > 1:
        summary.skip("several outputs on one rung")
        return None
    driver = drivers[0]
    devices = [ref.device for ref in driver.devices]
    if len(devices) != 1:
        summary.skip("output does not name exactly one device")
        return None

    kind = _OUTPUT_KINDS.get(driver.role or driver.opcode)
    if kind is None:
        summary.skip(f"output {driver.role or driver.opcode} cannot be rebuilt yet")
        return None

    refs = label_tokens_by_name(row, labels)
    try:
        ast = logic_to_ast(enable_logic_for_output(row, driver, labels), refs)
    except ValueError as error:
        summary.skip(f"no AST: {error}")
        return None

    try:
        generated, _rowsize, _ops = generate_rung(
            ast, {"type": kind, "device": refs.get(devices[0], devices[0])}
        )
    except ValueError as error:
        summary.skip(f"cannot generate: {error}")
        return None

    summary.checked += 1
    if generated == row.data:
        summary.identical += 1
        return Result(row.lddb, int(row.pos), devices[0], IDENTICAL)
    if _occurrences(row.data, labels) == _occurrences(generated, labels):
        summary.equivalent += 1
        return Result(row.lddb, int(row.pos), devices[0], EQUIVALENT, "differs in shape only")
    summary.differs += 1
    result = Result(row.lddb, int(row.pos), devices[0], DIFFERS, "reads back as different devices")
    summary.findings.append(result)
    return result


def check_project(root: Path) -> Summary:
    labels = load_label_resolver(root)
    summary = Summary()
    for row in load_rows(root, {}):
        if int(row.blocktype) != 0:
            continue
        check_row(row, labels, summary)
    return summary


def render(summary: Summary) -> list[str]:
    lines = [
        "round-trip: read each rung, rebuild it, compare",
        "",
        f"  checked      {summary.checked}",
        f"  identical    {summary.identical}",
        f"  equivalent   {summary.equivalent}  (same devices, different shape)",
        f"  differs      {summary.differs}",
    ]
    if summary.checked:
        lines.append(f"  agreement    {summary.agreement:.1%}")
    if summary.skipped:
        lines.append("")
        lines.append("not checked")
        width = max(len(reason) for reason in summary.skipped)
        for reason, count in sorted(summary.skipped.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {reason:<{width}}  {count}")
    if summary.findings:
        lines.append("")
        lines.append("rungs that read back differently")
        for finding in summary.findings[:40]:
            lines.append(f"  {finding.location}  -> {finding.device}")
    return lines


def to_json(summary: Summary) -> dict[str, Any]:
    return {
        "checked": summary.checked,
        "identical": summary.identical,
        "equivalent": summary.equivalent,
        "differs": summary.differs,
        "agreement": summary.agreement,
        "not_checked": summary.skipped,
        "findings": [
            {"lddb": f.lddb, "pos": f.pos, "device": f.device, "note": f.note}
            for f in summary.findings
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify reading by rebuilding each rung and comparing it to the original."
    )
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument(
        "--fail-on-differs",
        action="store_true",
        help="exit non-zero if any rung reads back as different devices",
    )
    add_format_argument(parser, json_shorthand=False)
    args = parser.parse_args(argv)

    summary = check_project(resolve_project_root(Path(args.root)))
    code = emit(args, text=lambda: render(summary), data=lambda: to_json(summary))
    if code == 0 and args.fail_on_differs and summary.differs:
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
