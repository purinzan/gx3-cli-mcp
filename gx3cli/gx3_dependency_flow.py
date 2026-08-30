from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gx3cli.gx3_device_name import format_device as _format_device
from gx3cli.extract_gx3_extended_instruction_knowledge import (
    element_meta,
    extract_dim,
    extract_elements,
    parse_header_ops,
)
from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.review_gx3_project import (
    LadderRow,
    arg_number,
    comment_for_device,
    load_comments_for_root,
    load_rows,
    operation_device_types,
)
from gx3cli.trace_gx3_device_dependencies import DRIVER_ROLES, ON_DRIVER_ROLES, normalize_device
from gx3cli.gx3_ladder_logic import enable_logic_for_output, logic_stats, logic_to_text
from gx3cli.gx3_project_paths import default_project_root


CONTACT_ROLES = {"a", "b"}
DEVICE_ARG_RE = re.compile(r"d\{s=#:a=(-?\d+):vt=nn\}|d\(a=(-?\d+)\)")
GROUP_ARG_RE = re.compile(
    r"([A-Z]+)\{b=d\{s=#:a=(-?\d+):vt=nn\}:m=c\{s=#:v=(\d+)\}\}"
)
VERTICAL_RE = re.compile(r"v\{pos=(\d+),(\d+)\}")


@dataclass(frozen=True)
class DeviceRef:
    device: str
    device_type: str
    number: int
    label: str = ""
    group_size: int = 0
    group_members: tuple[str, ...] = ()

    @property
    def display(self) -> str:
        return self.label or self.device

    @property
    def is_group(self) -> bool:
        return bool(self.group_size)


@dataclass
class FlowElement:
    kind: str
    role: str
    opcode: str
    category: str
    element_kind: str
    x: int
    y: int
    end_x: int = 0
    devices: list[DeviceRef] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)

    @property
    def is_wire(self) -> bool:
        return self.kind == "wire"

    @property
    def is_condition(self) -> bool:
        return self.role in CONTACT_ROLES or self.element_kind == "ct"

    @property
    def is_driver(self) -> bool:
        return self.role in DRIVER_ROLES

    def needs_state_text(self) -> str:
        if self.role == "a":
            return "ON"
        if self.role == "b":
            return "OFF"
        if self.opcode:
            const = f" {', '.join(self.constants)}" if self.constants else ""
            return f"{self.opcode}{const}".strip()
        return "condition"


def parse_pos(value: str) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        x_text, y_text = value.split(",", 1)
        return int(x_text), int(y_text)
    except ValueError:
        return None


def parse_dim_width(dim: str) -> int:
    match = re.match(r"(\d+)x(\d+)", dim or "")
    return int(match.group(1)) if match else 0


def device_comment(device: str, comments: dict[tuple[str, int], CommentInfo]) -> str:
    match = re.fullmatch(r"([A-Z]+)(-?\d+)", device)
    if not match:
        return ""
    return comment_for_device(match.group(1), int(match.group(2)), comments)


def bit_group_members(device_type: str, number: int, k_count: int) -> tuple[str, ...]:
    if device_type not in {"X", "Y", "M", "L", "B"}:
        return ()
    return tuple(f"{device_type}{number + offset}" for offset in range(k_count * 4))


def device_refs_from_raw(raw: str, default_device_type: str) -> list[DeviceRef]:
    refs: list[DeviceRef] = []
    group_spans: list[tuple[int, int]] = []
    for match in GROUP_ARG_RE.finditer(raw):
        device_type = match.group(1)
        number = int(match.group(2))
        k_count = int(match.group(3))
        members = bit_group_members(device_type, number, k_count)
        group_spans.append(match.span())
        refs.append(
            DeviceRef(
                device=_format_device(device_type, number),
                device_type=device_type,
                number=number,
                label=f"K{k_count}{device_type}{number}",
                group_size=k_count * 4,
                group_members=members,
            )
        )

    def inside_group(index: int) -> bool:
        return any(start <= index < end for start, end in group_spans)

    for match in DEVICE_ARG_RE.finditer(raw):
        if inside_group(match.start()):
            continue
        number_text = match.group(1) or match.group(2)
        if not number_text or not default_device_type:
            continue
        number = int(number_text)
        refs.append(DeviceRef(f"{default_device_type}{number}", default_device_type, number))

    seen: set[str] = set()
    unique: list[DeviceRef] = []
    for ref in refs:
        key = ref.display
        if key in seen:
            continue
        unique.append(ref)
        seen.add(key)
    return unique


def constants_from_raw(raw: str) -> list[str]:
    raw = GROUP_ARG_RE.sub("", raw)
    out: list[str] = []
    for value in re.findall(r"c\{s=#:v=([^:}]+)", raw):
        out.append(f"K{value}")
    return out[:3]


def positioned_elements(row: LadderRow) -> list[FlowElement]:
    header_ops = parse_header_ops(row.data)
    op_device_types = operation_device_types(row.data)
    raw_elements = extract_elements(row.data)
    elements: list[FlowElement] = []
    op_index = 0

    for raw in raw_elements:
        meta = element_meta(raw)
        pos = parse_pos(str(meta.get("pos", "")))
        if pos is None:
            continue
        x, y = pos
        if str(meta.get("element_kind", "")) == "wire":
            elements.append(FlowElement("wire", "", "", "", "wire", x, y))
            continue
        if op_index >= len(header_ops):
            continue
        header = header_ops[op_index]
        default_device_type = header.device_type
        role = header.op
        kind = "instruction"
        category = ""
        opcode = ""
        if header.op in {"a", "b", "c"}:
            kind = "contact" if header.op in {"a", "b"} else "coil"
        else:
            opcode = header.op
            role = header.op
            default_device_type = op_device_types[op_index] if op_index < len(op_device_types) else ""
            category = str(row.operations[op_index].get("category", "")) if op_index < len(row.operations) else ""

        devices = device_refs_from_raw(raw, default_device_type)
        elements.append(
            FlowElement(
                kind=kind,
                role=role,
                opcode=opcode,
                category=category,
                element_kind=str(meta.get("element_kind", "")),
                x=x,
                y=y,
                devices=devices,
                constants=constants_from_raw(raw),
            )
        )
        op_index += 1

    vertical_x_by_y: dict[int, set[int]] = defaultdict(set)
    for x_text, y_text in VERTICAL_RE.findall(row.data):
        vertical_x_by_y[int(y_text)].add(int(x_text))

    starts_by_y: dict[int, set[int]] = defaultdict(set)
    for element in elements:
        starts_by_y[element.y].add(element.x)
    for y, xs in vertical_x_by_y.items():
        starts_by_y[y].update(xs)
        starts_by_y[y - 1].update(xs)
    width = parse_dim_width(row.dim) or parse_dim_width(extract_dim(row.data))

    for element in elements:
        boundaries = sorted(x for x in starts_by_y[element.y] if x > element.x)
        element.end_x = boundaries[0] if boundaries else max(width, element.x + 1)
    return elements


def driver_index(rows: list[LadderRow], include_reset: bool) -> dict[str, list[LadderRow]]:
    roles = DRIVER_ROLES if include_reset else ON_DRIVER_ROLES
    index: dict[str, list[LadderRow]] = defaultdict(list)
    for row in rows:
        seen: set[str] = set()
        for occ in row.occurrences:
            if occ.role in roles and occ.device not in seen:
                index[occ.device].append(row)
                seen.add(occ.device)
    return index


def row_key(row: LadderRow) -> str:
    return f"{row.lddb}:{row.pos}"


def output_elements_for(row: LadderRow, device: str) -> list[FlowElement]:
    return [
        element
        for element in positioned_elements(row)
        if element.is_driver and any(ref.device == device for ref in element.devices)
    ]


def collect_path_elements(row: LadderRow, output: FlowElement) -> list[FlowElement]:
    elements = positioned_elements(row)
    by_end: dict[tuple[int, int], list[FlowElement]] = defaultdict(list)
    verticals: set[tuple[int, int]] = {
        (int(x_text), int(y_text)) for x_text, y_text in VERTICAL_RE.findall(row.data)
    }
    for element in elements:
        by_end[(element.end_x, element.y)].append(element)

    found: dict[tuple[int, int, int], FlowElement] = {}
    visited_boundaries: set[tuple[int, int]] = set()
    queue: deque[tuple[int, int]] = deque([(output.x, output.y)])

    while queue:
        boundary = queue.popleft()
        if boundary in visited_boundaries:
            continue
        visited_boundaries.add(boundary)
        x, y = boundary

        for predecessor in by_end.get((x, y), []):
            key = (predecessor.x, predecessor.y, predecessor.end_x)
            found[key] = predecessor
            if predecessor.x > 0:
                queue.append((predecessor.x, predecessor.y))

        if (x, y) in verticals:
            queue.append((x, y - 1))
        if (x, y + 1) in verticals:
            queue.append((x, y + 1))

    return sorted(found.values(), key=lambda item: (item.y, item.x, item.end_x))


def dependency_refs_for_output(row: LadderRow, output: FlowElement) -> list[tuple[FlowElement, DeviceRef]]:
    refs: list[tuple[FlowElement, DeviceRef]] = []
    for element in collect_path_elements(row, output):
        if element.is_wire or element.is_driver or not element.is_condition:
            continue
        for ref in element.devices:
            refs.append((element, ref))
    return refs


def build_flow(
    root: Path,
    target_device: str,
    max_devices: int,
    include_reset: bool,
    expand_bit_groups: bool,
) -> dict[str, Any]:
    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)
    drivers = driver_index(rows, include_reset=include_reset)
    target = normalize_device(target_device)

    queue: deque[tuple[str, int]] = deque([(target, 0)])
    visited: set[str] = set()
    truncated = False
    devices: dict[str, dict[str, Any]] = {}
    row_records: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, Any]] = []
    truncated_reasons: set[str] = set()

    while queue:
        device, depth = queue.popleft()
        if device in visited:
            continue
        if len(visited) >= max_devices:
            truncated = True
            truncated_reasons.add("max_devices")
            break
        visited.add(device)

        rows_for_device = drivers.get(device, [])
        devices[device] = {
            "device": device,
            "comment": device_comment(device, comments),
            "depth": depth,
            "driver_row_count": len(rows_for_device),
            "terminal": not rows_for_device,
        }

        for row in rows_for_device:
            rkey = row_key(row)
            row_records.setdefault(
                rkey,
                {
                    "row_id": rkey,
                    "lddb": row.lddb,
                    "pos": row.pos,
                    "block_id": row.block_id,
                    "title": row.title,
                    "dim": row.dim,
                    "parse_status": row.parse_status,
                    "outputs": [],
                },
            )
            outputs = output_elements_for(row, device)
            if not outputs:
                continue
            for output in outputs:
                enable_logic = enable_logic_for_output(row, output)
                row_records[rkey]["outputs"].append(
                    {
                        "device": device,
                        "role": output.role,
                        "effect": "OFF/reset" if output.role == "RST" else "ON",
                        "position": f"{output.x},{output.y}",
                        "enable_logic": enable_logic,
                        "enable_logic_text": logic_to_text(enable_logic),
                        "logic_stats": logic_stats(enable_logic),
                    }
                )
                edges.append(
                    {
                        "from": rkey,
                        "to": device,
                        "kind": "drives",
                        "label": "RST/OFF" if output.role == "RST" else output.role or "coil",
                    }
                )

                for element, ref in dependency_refs_for_output(row, output):
                    dep_label = ref.display
                    if ref.display not in devices:
                        member_driver_count = (
                            sum(1 for member in ref.group_members if drivers.get(member)) if ref.is_group else len(drivers.get(ref.device, []))
                        )
                        devices[ref.display] = {
                            "device": ref.display,
                            "comment": (
                                f"{ref.group_size}点: {ref.group_members[0]}..{ref.group_members[-1]}"
                                if ref.is_group and ref.group_members
                                else device_comment(ref.device, comments)
                            ),
                            "depth": depth + 1,
                            "driver_row_count": member_driver_count,
                            "terminal": member_driver_count == 0,
                            "group_members": list(ref.group_members),
                        }
                    edges.append(
                        {
                            "from": dep_label,
                            "to": rkey,
                            "kind": "depends",
                            "label": element.needs_state_text(),
                            "opcode": element.opcode or element.role,
                            "position": f"{element.x},{element.y}",
                        }
                    )

                    if ref.is_group:
                        enqueue_devices = list(ref.group_members) if expand_bit_groups else []
                    else:
                        enqueue_devices = [ref.device]
                    for enqueue_device in enqueue_devices:
                        if enqueue_device == device or enqueue_device in visited:
                            continue
                        if drivers.get(enqueue_device):
                            queue.append((enqueue_device, depth + 1))

    edge_counter = Counter(edge["from"] for edge in edges if edge["kind"] == "depends")
    return {
        "target": {"device": target, "comment": device_comment(target, comments)},
        "source_root": str(root),
        "include_reset": include_reset,
        "expand_bit_groups": expand_bit_groups,
        "max_devices": max_devices,
        "truncated": truncated,
        "truncated_reasons": sorted(truncated_reasons),
        "stats": {
            "devices": len(devices),
            "traced_devices": len(visited),
            "rows": len(row_records),
            "edges": len(edges),
            "terminal_devices": sum(1 for item in devices.values() if item.get("terminal")),
        },
        "devices": list(devices.values()),
        "rows": list(row_records.values()),
        "edges": edges,
        "top_dependencies": [{"device": device, "count": count} for device, count in edge_counter.most_common(30)],
    }


def compact_text(text: str, max_len: int) -> str:
    value = " ".join((text or "").split())
    if len(value) <= max_len:
        return value
    return value[: max_len - 1] + "..."


def mermaid_id(prefix: str, key: str) -> str:
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def mermaid_label(text: str) -> str:
    return (
        text.replace("\\", "/")
        .replace('"', "'")
        .replace("|", "/")
        .replace("[", "(")
        .replace("]", ")")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", "<br/>")
    )


def device_node_label(device: dict[str, Any], label_width: int) -> str:
    comment = compact_text(str(device.get("comment", "")), label_width)
    if comment:
        return f"{device['device']}\n{comment}"
    return str(device["device"])


def row_node_label(row: dict[str, Any], label_width: int) -> str:
    title = compact_text(str(row.get("title", "")), label_width)
    base = f"row {row['pos']}\n{row['lddb']}"
    return f"{base}\n{title}" if title else base


def format_mermaid(flow: dict[str, Any], label_width: int = 48) -> str:
    lines = ["flowchart TD"]
    target_device = flow["target"]["device"]
    device_ids: dict[str, str] = {}
    row_ids: dict[str, str] = {}

    for device in flow["devices"]:
        key = str(device["device"])
        node_id = mermaid_id("D", key)
        device_ids[key] = node_id
        label = mermaid_label(device_node_label(device, label_width))
        lines.append(f'  {node_id}["{label}"]')

    for row in flow["rows"]:
        key = str(row["row_id"])
        node_id = mermaid_id("R", key)
        row_ids[key] = node_id
        label = mermaid_label(row_node_label(row, label_width))
        lines.append(f'  {node_id}{{"{label}"}}')

    edge_seen: set[tuple[str, str, str, str]] = set()
    for edge in flow["edges"]:
        from_key = str(edge["from"])
        to_key = str(edge["to"])
        from_id = row_ids.get(from_key) or device_ids.get(from_key) or mermaid_id("D", from_key)
        to_id = row_ids.get(to_key) or device_ids.get(to_key) or mermaid_id("D", to_key)
        label = mermaid_label(compact_text(str(edge.get("label", "")), 32))
        edge_key = (from_id, to_id, label, str(edge.get("kind", "")))
        if edge_key in edge_seen:
            continue
        edge_seen.add(edge_key)
        if edge["kind"] == "drives":
            lines.append(f"  {from_id} -->|{label}| {to_id}")
        else:
            lines.append(f"  {from_id} -->|{label}| {to_id}")

    target_id = device_ids.get(target_device)
    if target_id:
        lines.append(f"  class {target_id} target")
    terminal_ids = [device_ids[str(item["device"])] for item in flow["devices"] if item.get("terminal")]
    if terminal_ids:
        lines.append(f"  class {','.join(terminal_ids)} terminal")
    row_node_ids = ",".join(row_ids.values())
    if row_node_ids:
        lines.append(f"  class {row_node_ids} row")

    lines.extend(
        [
            "  classDef target fill:#fff0d6,stroke:#b35a00,stroke-width:2px",
            "  classDef terminal fill:#f7f7f7,stroke:#888,stroke-dasharray: 4 3",
            "  classDef row fill:#e8f1ff,stroke:#2d5f9a",
        ]
    )
    return "\n".join(lines)


def format_markdown(flow: dict[str, Any], label_width: int = 48) -> str:
    target = flow["target"]
    lines = [
        f"# Dependency flow: {target['device']} {target.get('comment', '')}".rstrip(),
        "",
        "- Device -> row: the device is used as a condition or instruction operand on the path to that row output.",
        "- Row -> device: the row drives that coil/output.",
        "- Dashed-looking gray terminal nodes have no upstream coil driver in the parsed ladder data.",
        "",
        "```mermaid",
        format_mermaid(flow, label_width=label_width),
        "```",
        "",
        "## Summary",
        f"- source: `{flow['source_root']}`",
        f"- devices shown: {flow['stats']['devices']}",
        f"- traced devices with drivers: {flow['stats']['traced_devices']}",
        f"- rows: {flow['stats']['rows']}",
        f"- edges: {flow['stats']['edges']}",
        f"- truncated: {flow['truncated']}",
        f"- truncated reasons: {', '.join(flow.get('truncated_reasons', [])) or 'none'}",
    ]
    logic_lines: list[str] = []
    for row in flow["rows"]:
        for output in row.get("outputs", []):
            text = output.get("enable_logic_text", "")
            if not text:
                continue
            logic_lines.append(
                f"- `{row['row_id']}` -> `{output['device']}` at `{output['position']}`: `{text}`"
            )
    if logic_lines:
        lines.extend(["", "## Enable Logic"])
        lines.extend(logic_lines[:80])
        if len(logic_lines) > 80:
            lines.append(f"- ... {len(logic_lines) - 80} more outputs omitted")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create a flow-style upstream dependency graph for a ladder coil/device."
    )
    parser.add_argument("device", help="target device")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--max-devices", type=int, default=2000, help="maximum traced driver devices")
    parser.add_argument("--exclude-reset", action="store_true", help="ignore RST rows as drivers")
    parser.add_argument(
        "--expand-bit-groups",
        action="store_true",
        help="enqueue members of K<n>M/K<n>L bit groups as upstream devices",
    )
    parser.add_argument("--label-width", type=int, default=48, help="maximum comment chars in graph labels")
    parser.add_argument("--format", choices=["markdown", "mermaid", "json"], default="markdown")
    parser.add_argument("-o", "--output", help="write output to file")
    return parser


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    flow = build_flow(
        root=Path(args.root),
        target_device=args.device,
        max_devices=args.max_devices,
        include_reset=not args.exclude_reset,
        expand_bit_groups=args.expand_bit_groups,
    )
    if args.format == "json":
        output = json.dumps(flow, ensure_ascii=False, indent=2)
    elif args.format == "mermaid":
        output = format_mermaid(flow, label_width=args.label_width)
    else:
        output = format_markdown(flow, label_width=args.label_width)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
