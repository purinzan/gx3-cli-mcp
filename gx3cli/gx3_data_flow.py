from __future__ import annotations

"""Build conservative, argument-level value-flow edges from GX3 ladder rows.

This is deliberately separate from the existing coil/topology dependency flow.
The latter answers which contacts enable a coil; this command answers which
operand supplies a value to another operand, for example ``MOV D100 D200``.

Only relationships supported by the instruction read/write classifier become
edges. Unknown instructions and rows whose operation/element alignment is
partial are emitted as ``unresolved`` records instead of guessed edges.
"""

import argparse
import csv
import io
import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.gx3_arg_decode import (
    ARITH_OPS,
    COMPARE_RE,
    ArgOcc,
    WRITE_ARG_TABLE,
    base_opcode,
    parse_row_operations,
)
from gx3cli.gx3_device_name import format_device, split_device
from gx3cli.gx3_instruction_table import (
    manual_exec_condition,
    manual_operand_types,
    manual_write_indices,
    operand_words,
)
from gx3cli.gx3_intermediate_tool import read_ladder_rows
from gx3cli.gx3_label_resolve import load_label_resolver
from gx3cli.gx3_program_map import load_program_map
from gx3cli.review_gx3_project import extract_title, load_comments_for_root


BLOCK_TRANSFER_BASES = {
    "BMOV",
    "BMOVL",
    "FMOV",
    "DFMOV",
    "BLKMOVB",
    "FMOVL",
    "DFMOVL",
}
INT_RE = re.compile(r"-?\d+")


@dataclass(frozen=True)
class FlowRecord:
    """One directed edge or one conservative unresolved-operation record."""

    record_kind: str
    source_device: str = ""
    source_device_type: str = ""
    destination_device: str = ""
    destination_device_type: str = ""
    source_arg_index: int | None = None
    destination_arg_index: int | None = None
    opcode: str = ""
    operation_index: int = 0
    lddb: str = ""
    pos: int = 0
    pou: str = ""
    step: int | None = None
    title: str = ""
    const_args: str = ""
    source_detail: str = ""
    destination_detail: str = ""
    source_range: str = ""
    destination_range: str = ""
    range_count: int = 1
    source_word_width: int = 1
    destination_word_width: int = 1
    execution_condition: str = ""
    read_modify_write: bool = False
    confidence: str = "unknown"
    parse_status: str = "exact"
    detail: str = ""
    source_comment: str = ""
    destination_comment: str = ""


CSV_FIELDS = list(FlowRecord.__dataclass_fields__)


def semantic_confidence(opcode: str, argc: int) -> str:
    """Explain how much instruction knowledge backs an access classification."""

    op = base_opcode(opcode)
    if manual_write_indices(opcode, argc) is not None:
        return "manual"
    if op != opcode and manual_write_indices(op, argc) is not None:
        return "manual"
    if op in ARITH_OPS or COMPARE_RE.match(op):
        return "manual"
    if op in WRITE_ARG_TABLE:
        return "fallback"
    return "unknown"


def _first_count(const_args: str) -> int:
    if not const_args:
        return 1
    match = INT_RE.search(const_args)
    if not match:
        return 1
    try:
        return max(0, int(match.group(0)))
    except ValueError:
        return 1


def _count_from_value(value: str) -> int:
    match = INT_RE.search(value or "")
    if not match:
        return 1
    try:
        return max(0, int(match.group(0)))
    except ValueError:
        return 1


def transfer_count(opcode: str, const_args: str, constant_values: dict[int, str] | None = None) -> int:
    """Return a block-transfer count without mistaking a constant source for n."""

    if base_opcode(opcode) not in BLOCK_TRANSFER_BASES:
        return 1
    # Block-transfer signatures place (n) at argument 2. Positional values
    # are essential for forms such as FMOV K0 D100 K10.
    if constant_values and 2 in constant_values:
        return _count_from_value(constant_values[2])
    return _first_count(const_args)


def _operand_width(opcode: str, argc: int, arg_index: int) -> int:
    op = base_opcode(opcode)
    types = manual_operand_types(opcode, argc) or manual_operand_types(op, argc)
    if types is None or not 0 <= arg_index < len(types):
        return 1
    return operand_words(types[arg_index])


def _range_for(device: str, count: int, width: int) -> str:
    """Expand a plain device to an inclusive range when its span is known."""

    span = count * width
    if not device or span <= 1:
        return device
    parsed = split_device(device)
    if parsed is None:
        return device
    dev_type, number = parsed
    return f"{device}..{format_device(dev_type, number + span - 1)}"


def _unresolved(
    opcode: str,
    argc: int,
    occs: list[ArgOcc],
    *,
    parse_status: str,
    const_args: str,
    detail: str,
    **metadata: object,
) -> FlowRecord:
    return FlowRecord(
        record_kind="unresolved",
        opcode=opcode,
        const_args=const_args,
        confidence=semantic_confidence(opcode, argc),
        parse_status=parse_status,
        detail=detail,
        **metadata,
    )


def records_for_operation(
    opcode: str,
    argc: int,
    occs: list[ArgOcc],
    *,
    parse_status: str = "exact",
    const_args: str = "",
    operation_index: int = 0,
    lddb: str = "",
    pos: int = 0,
    pou: str = "",
    step: int | None = None,
    title: str = "",
    constant_values: dict[int, str] | None = None,
) -> list[FlowRecord]:
    """Convert one parsed operation into zero or more value-flow records."""

    if not opcode:
        # Contacts and coils participate in ladder topology, not operand value
        # flow. They have already been represented by the existing trace tools.
        return []

    metadata = {
        "operation_index": operation_index,
        "lddb": lddb,
        "pos": pos,
        "pou": pou,
        "step": step,
        "title": title,
    }

    if parse_status != "exact":
        return [
            _unresolved(
                opcode,
                argc,
                occs,
                parse_status=parse_status,
                const_args=const_args,
                detail="operation/element alignment is partial; no edge was inferred",
                **metadata,
            )
        ]

    write_indices, rmw = _write_indices(opcode, argc)
    if write_indices is None:
        return [
            _unresolved(
                opcode,
                argc,
                occs,
                parse_status=parse_status,
                const_args=const_args,
                detail="instruction write semantics are unknown; no edge was inferred",
                **metadata,
            )
        ]

    present_indices = {occ.arg_index for occ in occs}
    missing_destinations = sorted(index for index in write_indices if index not in present_indices)
    if missing_destinations:
        return [
            _unresolved(
                opcode,
                argc,
                occs,
                parse_status=parse_status,
                const_args=const_args,
                detail=f"destination operand(s) could not be decoded: {missing_destinations}",
                **metadata,
            )
        ]

    reads = [occ for occ in occs if occ.access in {"read", "both"}]
    writes = [occ for occ in occs if occ.access in {"write", "both"}]
    if not reads or not writes:
        return []

    count = transfer_count(opcode, const_args, constant_values)
    confidence = semantic_confidence(opcode, argc)
    condition = manual_exec_condition(opcode) or manual_exec_condition(base_opcode(opcode)) or ""
    records: list[FlowRecord] = []
    for source in reads:
        source_width = _operand_width(opcode, argc, source.arg_index)
        for destination in writes:
            destination_width = _operand_width(opcode, argc, destination.arg_index)
            records.append(
                FlowRecord(
                    record_kind="edge",
                    source_device=source.device,
                    source_device_type=source.device_type,
                    destination_device=destination.device,
                    destination_device_type=destination.device_type,
                    source_arg_index=source.arg_index,
                    destination_arg_index=destination.arg_index,
                    opcode=opcode,
                    const_args=const_args,
                    source_detail=source.detail,
                    destination_detail=destination.detail,
                    source_range=_range_for(source.device, count, source_width),
                    destination_range=_range_for(destination.device, count, destination_width),
                    range_count=count,
                    source_word_width=source_width,
                    destination_word_width=destination_width,
                    execution_condition=condition,
                    read_modify_write=bool(rmw or destination.access == "both"),
                    confidence=confidence,
                    parse_status=parse_status,
                    **metadata,
                )
            )
    return records


def _write_indices(opcode: str, argc: int) -> tuple[set[int] | None, bool]:
    """Use the shared classifier without duplicating its fallback semantics."""

    from gx3cli.gx3_arg_decode import write_indices

    return write_indices(opcode, argc)


def _canonical_filter(value: str) -> str:
    parsed = split_device(value)
    return format_device(*parsed) if parsed is not None else value.strip()


def _comment_for(occ: ArgOcc, comments: dict[tuple[str, int], CommentInfo]) -> str:
    info = comments.get((occ.device_type, occ.number), CommentInfo())
    return info.japanese or info.english or info.all_text or ""


def build_report(root: Path, device: str | None = None, opcode: str | None = None) -> dict[str, object]:
    """Parse all LDDBs and return a stable JSON-compatible report."""

    program_map = load_program_map(root)
    labels = load_label_resolver(root)
    comments = load_comments_for_root(root)
    wanted_device = _canonical_filter(device) if device else None
    wanted_opcode = opcode.upper() if opcode else None
    records: list[FlowRecord] = []
    row_count = 0
    operation_count = 0
    partial_rows = 0

    for lddb, rows in read_ladder_rows(root).items():
        pou = program_map.label(lddb)
        current_title = ""
        for raw in rows:
            data = str(raw["data"])
            blocktype = int(raw["blocktype"])
            if blocktype in {1, 2}:
                title = extract_title(data)
                if title:
                    current_title = title
            if blocktype != 0:
                continue
            row_count += 1
            pos = int(float(raw["pos"]))
            ops, status = parse_row_operations(data, labels)
            if status != "exact":
                partial_rows += 1
            step = program_map.step_of(lddb, pos)
            for op in ops:
                operation_count += 1
                operation_opcode = op.opcode
                if not operation_opcode:
                    continue
                if wanted_opcode and operation_opcode.upper() != wanted_opcode:
                    continue
                op_records = records_for_operation(
                    operation_opcode,
                    op.argc,
                    op.args,
                    parse_status=status,
                    const_args=op.const_summary,
                    operation_index=op.op_index,
                    lddb=lddb,
                    pos=pos,
                    pou=pou,
                    step=step,
                    title=current_title,
                    constant_values=op.constant_values,
                )
                for record in op_records:
                    source = next((o for o in op.args if o.device == record.source_device), None)
                    destination = next((o for o in op.args if o.device == record.destination_device), None)
                    record = replace(
                        record,
                        source_comment=_comment_for(source, comments) if source else "",
                        destination_comment=_comment_for(destination, comments) if destination else "",
                    )
                    if wanted_device and wanted_device not in {record.source_device, record.destination_device}:
                        continue
                    records.append(record)

    edges = [asdict(record) for record in records if record.record_kind == "edge"]
    unresolved = [asdict(record) for record in records if record.record_kind == "unresolved"]
    return {
        "command": "data-flow",
        "root": str(root),
        "stats": {
            "rows": row_count,
            "operations": operation_count,
            "partial_rows": partial_rows,
            "edges": len(edges),
            "unresolved": len(unresolved),
        },
        "edges": edges,
        "unresolved": unresolved,
    }


def _device_label(device: str, comment: str) -> str:
    return f"{device} ({comment})" if comment else f"{device} (no comment)"


def print_text(report: dict[str, object]) -> None:
    stats = report["stats"]
    print(
        f"data-flow: edges={stats['edges']} unresolved={stats['unresolved']} "
        f"rows={stats['rows']} partial_rows={stats['partial_rows']}"
    )
    for edge in report["edges"]:
        source = _device_label(str(edge["source_device"]), str(edge["source_comment"]))
        destination = _device_label(str(edge["destination_device"]), str(edge["destination_comment"]))
        rmw = " rmw" if edge["read_modify_write"] else ""
        print(
            f"  {source} --{edge['opcode']}[{edge['source_arg_index']}->{edge['destination_arg_index']}]--> "
            f"{destination} count={edge['range_count']} width={edge['source_word_width']}/{edge['destination_word_width']}"
            f" confidence={edge['confidence']}{rmw} | {edge['pou']}:{edge['pos']}"
        )
    for item in report["unresolved"]:
        print(
            f"  unresolved {item['opcode']} at {item['pou']}:{item['pos']}: "
            f"{item['detail']}"
        )


def print_csv(report: dict[str, object], stream) -> None:
    writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for item in [*report["edges"], *report["unresolved"]]:
        writer.writerow(item)


def main(argv: list[str] | None = None) -> int:
    # A device comment can hold characters the Windows console encoding has no
    # room for -- this project has a comment with a diamond in it -- and
    # without this the command printed 1,156 edges of a real project and then
    # died on the 1,157th.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="extracted GX3 project folder")
    parser.add_argument("--device", default=None, help="only edges touching this device")
    parser.add_argument("--opcode", default=None, help="only this exact instruction opcode")
    parser.add_argument("--format", choices=("text", "json", "csv"), default="text")
    parser.add_argument("--json", action="store_true", help="shorthand for --format json")
    parser.add_argument("-o", "--output", default=None, help="write JSON/CSV/text to this path")
    args = parser.parse_args(argv)
    report = build_report(Path(args.root), args.device, args.opcode)
    output_format = "json" if args.json else args.format
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="") as stream:
            if output_format == "json":
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            elif output_format == "csv":
                print_csv(report, stream)
            else:
                # Keep file output identical to stdout output.
                buffer = io.StringIO()
                original = sys.stdout
                try:
                    sys.stdout = buffer
                    print_text(report)
                finally:
                    sys.stdout = original
                stream.write(buffer.getvalue())
        print(f"data-flow written: {output}")
        return 0
    if output_format == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif output_format == "csv":
        print_csv(report, sys.stdout)
    else:
        print_text(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
