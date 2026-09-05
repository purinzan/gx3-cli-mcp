from __future__ import annotations

import json
import re
from copy import deepcopy
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from gx3cli.gx3_device_name import format_device as _format_device, parse_device_name as _parse_device_name
from gx3cli.extract_gx3_extended_instruction_knowledge import (
    element_meta,
    extract_dim,
    extract_elements,
)
from gx3cli.gx3_arg_decode import parse_row_operations
from gx3cli.review_gx3_project import LadderRow
from gx3cli.gx3_label_resolve import LabelResolver


CONTACT_ROLES = {"a", "b"}
ON_DRIVER_ROLES = {"c", "SET", "PLS", "PLF", "OUT__16", "OUTH__16"}
OFF_DRIVER_ROLES = {"RST"}
DRIVER_ROLES = ON_DRIVER_ROLES | OFF_DRIVER_ROLES

DEVICE_RE = re.compile(r"^([A-Z]+)(-?\d+)$", re.IGNORECASE)
DEVICE_ARG_RE = re.compile(r"d\{s=#:a=(-?\d+):vt=nn\}|d\(a=(-?\d+)\)")
DIGIT_DETAIL_RE = re.compile(r"\bdigit=K(\d+)\b")
VERTICAL_RE = re.compile(r"v\{pos=(\d+),(\d+)\}")

# Mitsubishi special relays used as hard constants in this project.
# a-contact SM400 is always true; a-contact SM401 is always false.
CONSTANT_DEVICE_VALUES = {
    "SM400": True,
    "SM401": False,
}


@dataclass(frozen=True)
class DeviceRef:
    device: str
    device_type: str
    number: int
    label: str = ""
    group_size: int = 0
    group_members: tuple[str, ...] = ()
    access: str = ""
    arg_index: int = -1

    @property
    def display(self) -> str:
        return self.label or self.device

    @property
    def is_group(self) -> bool:
        return bool(self.group_size)

    @property
    def is_written(self) -> bool:
        return self.access in {"write", "both"}


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
    ct_code: str = ""
    devices: list[DeviceRef] = field(default_factory=list)
    constants: list[str] = field(default_factory=list)
    # How many operands the instruction takes. The device list is not the
    # operand list -- a constant contributes no device and one device can be
    # two operands -- so anything indexing the manuals' write positions needs
    # this rather than len(devices).
    argc: int = 0

    @property
    def is_wire(self) -> bool:
        return self.kind == "wire"

    @property
    def is_condition(self) -> bool:
        return self.role in CONTACT_ROLES or self.element_kind == "ct"

    @property
    def is_driver(self) -> bool:
        return self.role in DRIVER_ROLES

    @property
    def is_sink(self) -> bool:
        return self.is_driver or any(ref.is_written for ref in self.devices)

    def needs_state_text(self) -> str:
        if self.role == "a":
            return "ON"
        if self.role == "b":
            return "OFF"
        if self.opcode:
            const = f" {', '.join(self.constants)}" if self.constants else ""
            return f"{self.opcode}{const}".strip()
        return "condition"


@dataclass(frozen=True)
class HorizontalEdge:
    """One explicit horizontal conduction step in a ladder row."""

    x1: int
    y: int
    x2: int
    element: FlowElement | None = None


@dataclass(frozen=True)
class TopologyGraph:
    """Directed left-to-right row topology with vertical merge components."""

    x_values: tuple[int, ...]
    horizontal_by_x: dict[int, tuple[HorizontalEdge, ...]]
    vertical_by_x: dict[int, tuple[frozenset[int], ...]]
    left_rail_rows: frozenset[int]
    sink_nodes: frozenset[tuple[int, int]]


@dataclass(frozen=True)
class RowLogicAnalysis:
    """Cached logic analysis for one ladder row."""

    elements: tuple[FlowElement, ...]
    graph: TopologyGraph
    node_logic: dict[tuple[int, int], dict[str, Any]]
    output_logic: dict[tuple[int, int], dict[str, Any]]


def parse_device(text: str) -> tuple[str, int]:
    return _parse_device_name(text)


def normalize_device(text: str) -> str:
    dev_type, number = parse_device(text)
    return _format_device(dev_type, number)


def parse_pos(value: str) -> tuple[int, int] | None:
    if not value:
        return None
    try:
        x_text, y_text = value.split(",", 1)
        return int(x_text), int(y_text)
    except ValueError:
        return None


def parse_dim(dim: str) -> tuple[int, int]:
    match = re.match(r"(\d+)x(\d+)", dim or "")
    if not match:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def parse_dim_width(dim: str) -> int:
    return parse_dim(dim)[0]


def parse_dim_height(dim: str) -> int:
    return parse_dim(dim)[1]


def bit_group_members(device_type: str, number: int, k_count: int) -> tuple[str, ...]:
    if device_type not in {"X", "Y", "M", "L", "B"}:
        return ()
    return tuple(_format_device(device_type, number + offset) for offset in range(k_count * 4))


def device_refs_from_args(args: list[Any]) -> list[DeviceRef]:
    refs: list[DeviceRef] = []
    for arg in args:
        if arg.device_type in {"?", ""}:
            continue
        digit_match = DIGIT_DETAIL_RE.search(arg.detail)
        if digit_match and arg.device_type in {"X", "Y", "M", "L", "B"}:
            k_count = int(digit_match.group(1))
            refs.append(
                DeviceRef(
                    device=arg.device,
                    device_type=arg.device_type,
                    number=arg.number,
                    label=f"K{k_count}{arg.device_type}{arg.number}",
                    group_size=k_count * 4,
                    group_members=bit_group_members(arg.device_type, arg.number, k_count),
                    access=arg.access,
                    arg_index=arg.arg_index,
                )
            )
            continue
        refs.append(DeviceRef(arg.device, arg.device_type, arg.number, access=arg.access, arg_index=arg.arg_index))

    # One device can be several operands of one instruction: D+ D32706 D37426
    # D32706 reads D32706 and writes it. Keeping the first reference and
    # dropping the rest lost the write, so the element looked like it wrote
    # nothing and rung-text fell back to naming an operand by position -- and
    # named a source as the driven device.
    unique: list[DeviceRef] = []
    position: dict[str, int] = {}
    for ref in refs:
        key = ref.display
        at = position.get(key)
        if at is None:
            position[key] = len(unique)
            unique.append(ref)
            continue
        first = unique[at]
        if ref.access and ref.access != first.access:
            merged = "both" if {first.access, ref.access} <= {"read", "write", "both"} else ref.access
            unique[at] = replace(first, access=merged)
    return unique


def positioned_elements(
    row: LadderRow, labels: LabelResolver | None = None
) -> list[FlowElement]:
    operations, _status = parse_row_operations(row.data, labels)
    raw_elements = extract_elements(row.data)
    elements: list[FlowElement] = []
    op_index = 0

    for raw in raw_elements:
        meta = element_meta(raw)
        pos = parse_pos(str(meta.get("pos", "")))
        if pos is None:
            continue
        x, y = pos
        if str(meta.get("element_kind", "")) == "wire" or raw.startswith("e{s=wire"):
            elements.append(FlowElement("wire", "", "", "", "wire", x, y))
            continue
        if op_index >= len(operations):
            continue
        operation = operations[op_index]
        role = operation.role
        kind = "instruction"
        category = ""
        opcode = ""
        if operation.role in {"a", "b", "c"}:
            kind = "contact" if operation.role in {"a", "b"} else "coil"
        else:
            opcode = operation.role
            category = str(row.operations[op_index].get("category", "")) if op_index < len(row.operations) else ""

        devices = device_refs_from_args(operation.args)
        elements.append(
            FlowElement(
                kind=kind,
                role=role,
                opcode=opcode,
                category=category,
                element_kind=str(meta.get("element_kind", "")),
                x=x,
                y=y,
                ct_code=str(meta.get("ct_code", "")),
                devices=devices,
                constants=[f"K{value}" for _, value in sorted(operation.constant_values.items())][:3],
                argc=operation.argc,
            )
        )
        op_index += 1

    for element in elements:
        if element.is_sink:
            element.end_x = element.x
        elif element.kind == "instruction":
            operand_cells = len(element.devices) + len(element.constants)
            element.end_x = element.x + max(1, 1 + operand_cells)
        else:
            element.end_x = element.x + 1
    return elements


def output_elements_for(row: LadderRow, device: str) -> list[FlowElement]:
    target = normalize_device(device)
    return [
        element
        for element in row_logic_analysis(row).elements
        if element.is_sink and any(ref.device == target and ref.is_written for ref in element.devices)
    ]


def logic_true() -> dict[str, Any]:
    return {"op": "true"}


def logic_false(reason: str = "", source: str = "") -> dict[str, Any]:
    node: dict[str, Any] = {"op": "false"}
    if reason:
        node["reason"] = reason
    if source:
        node["source"] = source
    return node


def is_true(node: dict[str, Any]) -> bool:
    return node.get("op") == "true"


def is_false(node: dict[str, Any]) -> bool:
    return node.get("op") == "false"


def logic_key(node: dict[str, Any]) -> str:
    return json.dumps(node, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def and_logic(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    flattened: list[dict[str, Any]] = []
    for node in nodes:
        if is_false(node):
            return logic_false()
        if is_true(node):
            continue
        if node.get("op") == "and":
            flattened.extend(node.get("args", []))
        else:
            flattened.append(node)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for node in flattened:
        key = logic_key(node)
        if key in seen:
            continue
        unique.append(node)
        seen.add(key)
    if not unique:
        return logic_true()
    if len(unique) == 1:
        return unique[0]
    return {"op": "and", "args": unique}


def or_logic(nodes: list[dict[str, Any]]) -> dict[str, Any]:
    flattened: list[dict[str, Any]] = []
    for node in nodes:
        if is_true(node):
            return logic_true()
        if is_false(node):
            continue
        if node.get("op") == "or":
            flattened.extend(node.get("args", []))
        else:
            flattened.append(node)

    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for node in flattened:
        key = logic_key(node)
        if key in seen:
            continue
        unique.append(node)
        seen.add(key)
    if not unique:
        return logic_false()
    if len(unique) == 1:
        return unique[0]
    return {"op": "or", "args": unique}


def device_ref_record(ref: DeviceRef) -> dict[str, Any]:
    return {
        "device": ref.display,
        "raw_device": ref.device,
        "device_type": ref.device_type,
        "number": ref.number,
        "group_size": ref.group_size,
        "group_members": list(ref.group_members),
    }


def element_condition_logic(element: FlowElement) -> dict[str, Any]:
    if element.is_wire:
        return logic_true()

    if element.role in CONTACT_ROLES:
        if not element.devices:
            return {
                "op": "unknown",
                "kind": element.kind,
                "role": element.role,
                "position": f"{element.x},{element.y}",
            }
        ref = element.devices[0]
        constant_value = CONSTANT_DEVICE_VALUES.get(ref.device)
        if constant_value is not None:
            effective_value = constant_value if element.role == "a" else not constant_value
            if effective_value:
                return logic_true()
            return logic_false("constant_contact", ref.device)
        return {
            "op": "contact",
            "role": element.role,
            "state": "ON" if element.role == "a" else "OFF",
            "ct_code": element.ct_code,
            **device_ref_record(ref),
            "position": f"{element.x},{element.y}",
        }

    if element.element_kind == "ct":
        return {
            "op": "predicate",
            "opcode": element.opcode or element.role or "ct",
            "ct_code": element.ct_code,
            "devices": [device_ref_record(ref) for ref in element.devices],
            "constants": list(element.constants),
            "position": f"{element.x},{element.y}",
        }

    return {
        "op": "unknown",
        "kind": element.kind,
        "role": element.role,
        "opcode": element.opcode,
        "position": f"{element.x},{element.y}",
    }


def horizontal_edges(elements: list[FlowElement]) -> list[HorizontalEdge]:
    """Return horizontal edges that are explicitly carried by row elements.

    Earlier logic extended an element to the next element or vertical boundary
    on the same y row. That inferred a wire through any empty horizontal gap.
    The GX3 row data already has explicit ``wire`` elements for empty cells, so
    topology logic should advance only through contacts, inline predicates, and
    those explicit wires.
    """

    edges: list[HorizontalEdge] = []
    for element in elements:
        if element.is_sink:
            continue
        end_x = max(element.end_x, element.x + 1)
        edges.append(HorizontalEdge(element.x, element.y, end_x, element))
    return edges


def vertical_components(row: LadderRow, elements: list[FlowElement]) -> dict[int, list[set[int]]]:
    width, height = parse_dim(row.dim or extract_dim(row.data))
    max_y = max([height - 1, *[element.y for element in elements], 0])
    by_x: dict[int, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    for y in range(max_y + 1):
        for x_text, _ in VERTICAL_RE.findall(row.data):
            by_x[int(x_text)][y].add(y)

    verticals = {(int(x_text), int(y_text)) for x_text, y_text in VERTICAL_RE.findall(row.data)}
    xs = {x for x, _ in verticals}
    components: dict[int, list[set[int]]] = {}
    for x in xs:
        parent = {y: y for y in range(max_y + 1)}

        def find(value: int) -> int:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        def union(a: int, b: int) -> None:
            root_a = find(a)
            root_b = find(b)
            if root_a != root_b:
                parent[root_b] = root_a

        for vx, vy in verticals:
            if vx != x:
                continue
            if 0 <= vy - 1 <= max_y and 0 <= vy <= max_y:
                union(vy - 1, vy)
        grouped: dict[int, set[int]] = defaultdict(set)
        for y in range(max_y + 1):
            grouped[find(y)].add(y)
        components[x] = [ys for ys in grouped.values() if len(ys) > 1]
    return components


def topology_graph(row: LadderRow, elements: list[FlowElement], output_x: int | None = None) -> TopologyGraph:
    """Build the explicit coordinate graph used by enable-logic analysis."""

    width, height = parse_dim(row.dim or extract_dim(row.data))
    edges = horizontal_edges(elements)
    x_values = {0}
    if output_x is not None:
        x_values.add(output_x)
    for edge in edges:
        x_values.add(edge.x1)
        x_values.add(edge.x2)
    for element in elements:
        x_values.add(element.x)
    for x_text, _ in VERTICAL_RE.findall(row.data):
        x_values.add(int(x_text))
    if width:
        x_values.add(width)

    edges_by_x: dict[int, list[HorizontalEdge]] = defaultdict(list)
    for edge in edges:
        edges_by_x[edge.x1].append(edge)

    left_rail_rows = frozenset(edge.y for edge in edges if edge.x1 == 0)
    sink_nodes = frozenset((element.x, element.y) for element in elements if element.is_sink)
    components_by_x = {
        x: tuple(frozenset(component) for component in components)
        for x, components in vertical_components(row, elements).items()
    }
    return TopologyGraph(
        x_values=tuple(sorted(x_values)),
        horizontal_by_x={x: tuple(value) for x, value in edges_by_x.items()},
        vertical_by_x=components_by_x,
        left_rail_rows=left_rail_rows,
        sink_nodes=sink_nodes,
    )


def analyze_row_logic(row: LadderRow, labels: LabelResolver | None = None) -> RowLogicAnalysis:
    """Compute logic for every coordinate and driver output in one row.

    Driver elements are sink nodes: they can receive power from the left or
    from a vertical branch at the same coordinate, but they never source power
    to the right or back into the vertical component.
    """

    elements = positioned_elements(row, labels)
    width, height = parse_dim(row.dim or extract_dim(row.data))
    max_y = max([height - 1, *[element.y for element in elements], 0])
    formulas: dict[tuple[int, int], dict[str, Any]] = defaultdict(logic_false)
    graph = topology_graph(row, elements)

    for y in graph.left_rail_rows:
        if 0 <= y <= max_y:
            formulas[(0, y)] = logic_true()

    for x in graph.x_values:
        for component in graph.vertical_by_x.get(x, ()):
            source_ys = [
                y
                for y in sorted(component)
                if (x, y) not in graph.sink_nodes
            ]
            sink_ys = [y for y in sorted(component) if (x, y) in graph.sink_nodes]
            if not source_ys and len(sink_ys) > 1:
                # GX uses a vertical branch at the output coordinate to fan one
                # enable condition into several coils/instruction boxes. The
                # branch is on the input side of those sinks, not driven by the
                # first sink's output.
                merged = formulas[(x, min(sink_ys))]
                for y in sink_ys:
                    formulas[(x, y)] = or_logic([formulas[(x, y)], merged])
                continue
            if source_ys:
                merged = or_logic([formulas[(x, y)] for y in source_ys])
                for y in source_ys:
                    formulas[(x, y)] = merged
                for y in sink_ys:
                    formulas[(x, y)] = or_logic([formulas[(x, y)], merged])

        for edge in graph.horizontal_by_x.get(x, ()):
            if (edge.x1, edge.y) in graph.sink_nodes:
                continue
            incoming = formulas[(edge.x1, edge.y)]
            if is_false(incoming):
                continue
            condition = element_condition_logic(edge.element) if edge.element is not None else logic_true()
            if condition.get("op") == "unknown":
                condition = {
                    **condition,
                    "warning": "treated_as_pass_through_condition",
                }
            candidate = and_logic([incoming, condition])
            key = (edge.x2, edge.y)
            formulas[key] = or_logic([formulas[key], candidate])

    output_logic = {
        (element.x, element.y): formulas[(element.x, element.y)]
        for element in elements
        if element.is_sink
    }
    return RowLogicAnalysis(
        elements=tuple(elements),
        graph=graph,
        node_logic=dict(formulas),
        output_logic=output_logic,
    )


def row_logic_analysis(row: LadderRow, labels: LabelResolver | None = None) -> RowLogicAnalysis:
    cache_key = (row.data, row.dim, len(row.operations), id(labels))
    cached = getattr(row, "_gx3_logic_analysis_cache", None)
    if cached is not None and cached[0] == cache_key:
        return cached[1]
    analysis = analyze_row_logic(row, labels)
    setattr(row, "_gx3_logic_analysis_cache", (cache_key, analysis))
    return analysis


def enable_logic_for_output(
    row: LadderRow, output: FlowElement, labels: LabelResolver | None = None
) -> dict[str, Any]:
    analysis = row_logic_analysis(row, labels)
    coordinate = (output.x, output.y)
    return deepcopy(
        analysis.output_logic.get(
            coordinate,
            analysis.node_logic.get(coordinate, logic_false()),
        )
    )


def enable_logic_for_device(row: LadderRow, device: str) -> dict[str, Any]:
    outputs = output_elements_for(row, device)
    if not outputs:
        return logic_false()
    analysis = row_logic_analysis(row)
    return deepcopy(
        or_logic([analysis.output_logic.get((output.x, output.y), logic_false()) for output in outputs])
    )


def logic_to_text(node: dict[str, Any]) -> str:
    op = node.get("op")
    if op == "true":
        return "TRUE"
    if op == "false":
        return "FALSE"
    if op == "contact":
        device = str(node.get("device") or node.get("raw_device", ""))
        prefix = "/" if node.get("role") == "b" else ""
        edge = str(node.get("ct_code") or "")
        edge_prefix = "P " if edge == "p" else ""
        return f"[{prefix}{edge_prefix}{device}]"
    if op == "predicate":
        opcode = str(node.get("opcode", "predicate"))
        operands: list[str] = []
        operands.extend(str(value) for value in node.get("constants", []))
        operands.extend(str(ref.get("device", ref.get("raw_device", ""))) for ref in node.get("devices", []))
        return f"[{opcode} {' '.join(operands)}]".strip()
    if op == "unknown":
        label = str(node.get("opcode") or node.get("role") or node.get("kind") or "unknown")
        return f"[UNKNOWN {label}]"
    if op in {"and", "or"}:
        joiner = f" {op.upper()} "
        return "(" + joiner.join(logic_to_text(child) for child in node.get("args", [])) + ")"
    return f"[UNSUPPORTED {op}]"


def condition_refs_from_logic(node: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []

    def visit(current: dict[str, Any]) -> None:
        op = current.get("op")
        if op == "contact":
            refs.append(
                {
                    "device": current.get("raw_device") or current.get("device"),
                    "display_device": current.get("device") or current.get("raw_device"),
                    "device_type": current.get("device_type", ""),
                    "number": current.get("number", 0),
                    "role": current.get("role", ""),
                    "required_state": current.get("state", ""),
                    "position": current.get("position", ""),
                    "predicate": "",
                }
            )
        elif op == "predicate":
            for ref in current.get("devices", []):
                refs.append(
                    {
                        "device": ref.get("raw_device") or ref.get("device"),
                        "display_device": ref.get("device") or ref.get("raw_device"),
                        "device_type": ref.get("device_type", ""),
                        "number": ref.get("number", 0),
                        "role": "predicate",
                        "required_state": str(current.get("opcode", "predicate")),
                        "position": current.get("position", ""),
                        "predicate": logic_to_text(current),
                    }
                )
        elif op in {"and", "or"}:
            for child in current.get("args", []):
                visit(child)

    visit(node)

    seen: set[tuple[str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for ref in refs:
        key = (
            str(ref.get("device", "")),
            str(ref.get("role", "")),
            str(ref.get("required_state", "")),
            str(ref.get("predicate", "")),
        )
        if key in seen:
            continue
        unique.append(ref)
        seen.add(key)
    return unique


def logic_stats(node: dict[str, Any]) -> dict[str, int]:
    stats = {"contacts": 0, "predicates": 0, "and_nodes": 0, "or_nodes": 0, "unknowns": 0}

    def visit(current: dict[str, Any]) -> None:
        op = current.get("op")
        if op == "contact":
            stats["contacts"] += 1
        elif op == "predicate":
            stats["predicates"] += 1
        elif op == "unknown":
            stats["unknowns"] += 1
        elif op == "and":
            stats["and_nodes"] += 1
            for child in current.get("args", []):
                visit(child)
        elif op == "or":
            stats["or_nodes"] += 1
            for child in current.get("args", []):
                visit(child)

    visit(node)
    return stats
