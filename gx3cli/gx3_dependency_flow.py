from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

from gx3cli.gx3_device_name import split_device as _split_device
from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.review_gx3_project import (
    LadderRow,
    comment_for_device,
    load_comments_for_root,
    load_rows,
)
from gx3cli.trace_gx3_device_dependencies import DRIVER_ROLES, ON_DRIVER_ROLES, normalize_device
from gx3cli.gx3_ladder_logic import (
    DeviceRef,
    FlowElement,
    enable_logic_for_output,
    logic_stats,
    logic_to_text,
    output_elements_for,
    positioned_elements,
)
from gx3cli.gx3_project_paths import default_project_root


def device_comment(device: str, comments: dict[tuple[str, int], CommentInfo]) -> str:
    parsed = _split_device(device)
    if parsed is None:
        return ""
    return comment_for_device(parsed[0], parsed[1], comments)


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


def condition_elements_by_position(row: LadderRow) -> dict[str, FlowElement]:
    return {
        f"{element.x},{element.y}": element
        for element in positioned_elements(row)
        if element.is_condition and not element.is_wire and not element.is_driver
    }


def logic_dependency_positions(logic: dict[str, Any]) -> list[str]:
    positions: list[str] = []
    seen: set[str] = set()

    def visit(node: dict[str, Any]) -> None:
        op = node.get("op")
        if op in {"contact", "predicate"}:
            position = str(node.get("position", ""))
            if position and position not in seen:
                positions.append(position)
                seen.add(position)
        elif op in {"and", "or"}:
            for child in node.get("args", []):
                if isinstance(child, dict):
                    visit(child)

    visit(logic)
    return positions


def collect_path_elements(row: LadderRow, output: FlowElement) -> list[FlowElement]:
    """Return condition elements that actually participate in output logic.

    Kept as a compatibility helper for callers that imported the old
    coordinate-backtracking function. The result is now derived from
    ``enable_logic_for_output`` so dead branches, blank gaps, and sink leakage
    are not reported as dependencies.
    """

    elements_by_position = condition_elements_by_position(row)
    logic = enable_logic_for_output(row, output)
    return [
        elements_by_position[position]
        for position in logic_dependency_positions(logic)
        if position in elements_by_position
    ]


def dependency_refs_for_output(row: LadderRow, output: FlowElement) -> list[tuple[FlowElement, DeviceRef]]:
    logic = enable_logic_for_output(row, output)
    elements_by_position = condition_elements_by_position(row)

    refs: list[tuple[FlowElement, DeviceRef]] = []

    def visit(node: dict[str, Any]) -> None:
        op = node.get("op")
        if op == "contact":
            element = elements_by_position.get(str(node.get("position", "")))
            if element is None:
                return
            raw_device = str(node.get("raw_device") or node.get("device") or "")
            for ref in element.devices:
                if ref.device == raw_device or ref.display == node.get("device"):
                    refs.append((element, ref))
                    return
        elif op == "predicate":
            element = elements_by_position.get(str(node.get("position", "")))
            if element is None:
                return
            logic_devices = {
                str(ref_record.get("raw_device") or ref_record.get("device"))
                for ref_record in node.get("devices", [])
            }
            logic_displays = {str(ref_record.get("device")) for ref_record in node.get("devices", [])}
            for ref in element.devices:
                if ref.device in logic_devices or ref.display in logic_displays:
                    refs.append((element, ref))
        elif op in {"and", "or"}:
            for child in node.get("args", []):
                if isinstance(child, dict):
                    visit(child)

    visit(logic)
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
