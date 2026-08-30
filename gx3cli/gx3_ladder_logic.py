from __future__ import annotations

import json
import re
from collections import defaultdict
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
from gx3cli.review_gx3_project import LadderRow, operation_device_types


CONTACT_ROLES = {"a", "b"}
ON_DRIVER_ROLES = {"c", "SET", "PLS", "PLF", "OUT__16", "OUTH__16"}
OFF_DRIVER_ROLES = {"RST"}
DRIVER_ROLES = ON_DRIVER_ROLES | OFF_DRIVER_ROLES

DEVICE_RE = re.compile(r"^([A-Z]+)(-?\d+)$", re.IGNORECASE)
DEVICE_ARG_RE = re.compile(r"d\{s=#:a=(-?\d+):vt=nn\}|d\(a=(-?\d+)\)")
GROUP_ARG_RE = re.compile(
    r"([A-Z]+)\{b=d\{s=#:a=(-?\d+):vt=nn\}:m=c\{s=#:v=(\d+)\}\}"
)
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
    ct_code: str = ""
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


def parse_device(text: str) -> tuple[str, int]:
    value = text.strip().upper()
    match = DEVICE_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid device: {text!r}")
    return match.group(1), int(match.group(2))


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
                ct_code=str(meta.get("ct_code", "")),
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


def output_elements_for(row: LadderRow, device: str) -> list[FlowElement]:
    target = normalize_device(device)
    return [
        element
        for element in positioned_elements(row)
        if element.is_driver and any(ref.device == target for ref in element.devices)
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


def enable_logic_for_output(row: LadderRow, output: FlowElement) -> dict[str, Any]:
    elements = positioned_elements(row)
    width, height = parse_dim(row.dim or extract_dim(row.data))
    max_y = max([height - 1, output.y, *[element.y for element in elements], 0])
    formulas: dict[tuple[int, int], dict[str, Any]] = defaultdict(logic_false)

    for y in range(max_y + 1):
        formulas[(0, y)] = logic_true()

    x_values = {0, output.x}
    for element in elements:
        x_values.add(element.x)
        x_values.add(element.end_x)
    for x_text, _ in VERTICAL_RE.findall(row.data):
        x_values.add(int(x_text))
    if width:
        x_values.add(width)

    starts: dict[int, list[FlowElement]] = defaultdict(list)
    for element in elements:
        starts[element.x].append(element)

    components_by_x = vertical_components(row, elements)

    for x in sorted(x_values):
        for component in components_by_x.get(x, []):
            merged = or_logic([formulas[(x, y)] for y in sorted(component)])
            for y in component:
                formulas[(x, y)] = merged

        for element in starts.get(x, []):
            if element.is_driver:
                continue
            incoming = formulas[(element.x, element.y)]
            if is_false(incoming):
                continue
            condition = element_condition_logic(element)
            if condition.get("op") == "unknown":
                condition = {
                    **condition,
                    "warning": "treated_as_pass_through_condition",
                }
            candidate = and_logic([incoming, condition])
            key = (element.end_x, element.y)
            formulas[key] = or_logic([formulas[key], candidate])

    return formulas[(output.x, output.y)]


def enable_logic_for_device(row: LadderRow, device: str) -> dict[str, Any]:
    outputs = output_elements_for(row, device)
    if not outputs:
        return logic_false()
    return or_logic([enable_logic_for_output(row, output) for output in outputs])


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
