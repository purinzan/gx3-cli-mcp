from __future__ import annotations

import hashlib
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
from gx3cli.gx3_operand_display import display_operands, instruction_opcode
from gx3cli.gx3_operand_parse import parse_operands
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
    # Canonical decoder span of written operands only. Read width must not
    # enlarge a narrower write when the same base occurs in several arguments.
    write_range_len: int = 1

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
    operands: list[str] = field(default_factory=list)

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
                    write_range_len=0 if "indexed" in arg.detail else arg.range_len,
                )
            )
            continue
        refs.append(DeviceRef(arg.device, arg.device_type, arg.number, access=arg.access, arg_index=arg.arg_index,
                              write_range_len=0 if "indexed" in arg.detail else arg.range_len))

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
        merged = first.access
        if ref.access and ref.access != first.access:
            merged = "both" if {first.access, ref.access} <= {"read", "write", "both"} else ref.access
        spans = [item.write_range_len for item in (first, ref) if item.is_written]
        # Unknown extent contributes no extra members, but must not erase a
        # separate, known written operand with the same base.
        write_span = max(spans, default=0)
        unique[at] = replace(first, access=merged, write_range_len=write_span)
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
        if "s=ce{" not in raw:
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
            opcode = instruction_opcode(operation.role, bool(re.search(r"as=\[as\{vt=A32", raw)),
                                        bool(re.search(r"as=\[as\{vt=Ass", raw)))
            category = str(row.operations[op_index].get("category", "")) if op_index < len(row.operations) else ""

        devices = device_refs_from_args(operation.args)
        operands = display_operands(operation.raw_args, operation.arg_tokens, labels, re.findall(r"as\{vt=([^}]+)", raw))
        operand_kinds = parse_operands(operation.raw_args, operation.arg_tokens)
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
                constants=[value for value, operand in zip(operands, operand_kinds) if operand.kind == "const"],
                operands=operands,
                argc=operation.argc,
            )
        )
        op_index += 1

    for element in elements:
        if element.is_sink:
            element.end_x = element.x
        elif element.kind == "instruction":
            operand_cells = element.argc
            element.end_x = element.x + max(1, 1 + operand_cells)
        else:
            element.end_x = element.x + 1
    return elements


def output_elements_for(row: LadderRow, device: str) -> list[FlowElement]:
    target = normalize_device(device)
    device_type, number = parse_device(target)
    return [
        element
        for element in row_logic_analysis(row).elements
        if element.is_sink and any(
            ref.is_written and (ref.device == target or (
                ref.device_type == device_type and ref.write_range_len > 1
                and ref.number <= number < ref.number + ref.write_range_len
            )) for ref in element.devices
        )
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


# A node's identity, for telling two branches apart. `logic_key` serialises the
# whole subtree, and and_logic/or_logic ask for it at every level while the
# tree is built from the bottom up, so the same leaves get serialised once per
# ancestor: on one real rung that came to 185MB of JSON and 12.7 seconds, and
# `logic_key` was 73% of the time taken to read a program.
#
# A digest built from the children's digests costs each node only its own
# fields, so the whole tree is one pass. Nodes are never changed after they are
# constructed, which is what makes an identity cache sound here; the node is
# kept in the cache beside its digest so its id cannot be reused while the
# entry stands.
_DIGEST_CACHE: dict[int, tuple[dict[str, Any], str]] = {}
_DIGEST_CACHE_LIMIT = 200_000


def reset_logic_ids() -> None:
    _DIGEST_CACHE.clear()
    _SIZE_CACHE.clear()


def logic_id(node: dict[str, Any]) -> str:
    """A short, stable identity for a logic node, equal for equal structures."""
    cached = _DIGEST_CACHE.get(id(node))
    if cached is not None and cached[0] is node:
        return cached[1]

    args = node.get("args")
    if isinstance(args, list):
        rest = json.dumps(
            {key: value for key, value in node.items() if key != "args"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        material = rest + "|" + "|".join(logic_id(child) for child in args)
    else:
        material = logic_key(node)
    digest = hashlib.blake2b(material.encode("utf-8"), digest_size=16).hexdigest()

    if len(_DIGEST_CACHE) >= _DIGEST_CACHE_LIMIT:
        # Bounded rather than unbounded: a wrong answer is impossible either
        # way, and a run over a whole project should not hold every node it
        # has ever seen.
        _DIGEST_CACHE.clear()
    _DIGEST_CACHE[id(node)] = (node, digest)
    return digest


# How many nodes one rung's condition may grow to before this stops building
# it. A rung in a real project produced 33,554,427 nodes -- 2^25, the shape of
# a combinatorial expansion, not of a condition anyone wrote -- and took 146
# seconds, while the other 6,000 rungs of that project took 23 seconds
# together. Past this point the expression is not something a person is going
# to read anyway; what they need is to be told that, and where to look.
MAX_LOGIC_NODES = 20_000

_SIZE_CACHE: dict[int, tuple[dict[str, Any], int]] = {}


def logic_size(node: dict[str, Any]) -> int:
    """How many nodes this subtree holds, counted once per node."""
    cached = _SIZE_CACHE.get(id(node))
    if cached is not None and cached[0] is node:
        return cached[1]
    args = node.get("args")
    size = 1 + sum(logic_size(child) for child in args) if isinstance(args, list) else 1
    if len(_SIZE_CACHE) >= _DIGEST_CACHE_LIMIT:
        _SIZE_CACHE.clear()
    _SIZE_CACHE[id(node)] = (node, size)
    return size


def logic_too_large(size: int) -> dict[str, Any]:
    """A condition this refused to keep expanding, and said so in its place."""
    return {
        "op": "too_large",
        "reason": f"the expanded condition passed {MAX_LOGIC_NODES} terms ({size})",
        "next_step": "read the rung itself: gx3-cli ladder-print / ladder-report",
    }


def is_too_large(node: dict[str, Any]) -> bool:
    return node.get("op") == "too_large"


def _within_budget(unique: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The marker node, if joining these would pass the budget."""
    total = 1
    for node in unique:
        if is_too_large(node):
            return node
        total += logic_size(node)
        if total > MAX_LOGIC_NODES:
            return logic_too_large(total)
    return None


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
        key = logic_id(node)
        if key in seen:
            continue
        unique.append(node)
        seen.add(key)
    if not unique:
        return logic_true()
    if len(unique) == 1:
        return unique[0]
    oversized = _within_budget(unique)
    if oversized is not None:
        return oversized
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
        key = logic_id(node)
        if key in seen:
            continue
        unique.append(node)
        seen.add(key)
    if not unique:
        return logic_false()
    if len(unique) == 1:
        return unique[0]
    oversized = _within_budget(unique)
    if oversized is not None:
        return oversized
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
        if constant_value is not None and element.ct_code not in {"p", "f"}:
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
            "device": element.operands[0] if element.operands else ref.display,
            "position": f"{element.x},{element.y}",
        }

    if element.element_kind == "ct":
        return {
            "op": "predicate",
            "opcode": element.opcode or element.role or "ct",
            "ct_code": element.ct_code,
            "devices": [device_ref_record(ref) for ref in element.devices],
            "constants": list(element.constants),
            "operands": list(element.operands),
            "position": f"{element.x},{element.y}",
        }

    return {
        "op": "unknown",
        "kind": element.kind,
        "role": element.role,
        "opcode": element.opcode,
        "position": f"{element.x},{element.y}",
    }


def input_expression_logic(element: FlowElement, incoming: dict[str, Any]) -> dict[str, Any] | None:
    """Inline INV/ME transform their entire input, not an independent contact.

    Keep the input subtree on a predicate node so existing conservative
    consumers do not mistake an edge for a steady-state contact. Its value
    cannot be obtained from one snapshot when a previous scan is required.
    """
    if element.element_kind != "ct":
        return None
    if element.role == "INV":
        opcode = "INV"
    elif element.role == "ME" and element.ct_code in {"p", "f"}:
        opcode = "MEP" if element.ct_code == "p" else "MEF"
    else:
        return None
    if is_too_large(incoming):
        return incoming
    oversized = _within_budget([incoming])
    if oversized is not None:
        return oversized
    refs = condition_refs_from_logic(incoming)
    return {
        "op": "predicate", "opcode": opcode, "expression_operator": True,
        "ct_code": element.ct_code, "position": f"{element.x},{element.y}",
        "args": [incoming], "constants": [],
        "devices": [
            {"device": ref["display_device"], "raw_device": ref["device"],
             "device_type": ref["device_type"], "number": ref["number"]}
            for ref in refs
        ],
        "requires_previous_scan": opcode in {"MEP", "MEF"},
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



def expression_branch_scopes(graph: TopologyGraph) -> dict[tuple[int, int], tuple[int, int]]:
    """Find the enclosing bypass branch of an inline expression operator.

    An operator inside one arm of a parallel branch applies to that arm's
    accumulator. The common input preceding the split is combined afterwards.
    Only a dominating split with a bypass that rejoins past the operator
    qualifies; a fan-out to an unrelated output does not reset its input.
    """
    aliases = {}
    for x, components in graph.vertical_by_x.items():
        for ys in components:
            for y in ys:
                aliases[(x, y)] = (x, min(ys))

    def node(x, y):
        return aliases.get((x, y), (x, y))

    successors: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    predecessors: dict[tuple[int, int], set[tuple[int, int]]] = defaultdict(set)
    operators = []
    for edges in graph.horizontal_by_x.values():
        for edge in edges:
            if (edge.x1, edge.y) in graph.sink_nodes:
                continue
            source, target = node(edge.x1, edge.y), node(edge.x2, edge.y)
            successors[source].add(target)
            predecessors[target].add(source)
            el = edge.element
            if el and el.element_kind == "ct" and (el.role == "INV" or (el.role == "ME" and el.ct_code in {"p", "f"})):
                operators.append((edge, source, target))

    if not operators:
        return {}

    rails = {node(0, y) for y in graph.left_rail_rows}
    dominators = {}
    for current in sorted(set(successors) | set(predecessors) | rails):
        parents = [dominators[p] for p in predecessors[current] if p in dominators]
        if current in rails:
            dominators[current] = {current}
        elif parents:
            dominators[current] = {current} | set.intersection(*parents)

    def reachable(start, excluded=None):
        seen, pending = {start}, [start]
        while pending:
            source = pending.pop()
            for target in successors.get(source, ()):
                if (source, target) == excluded or target in seen:
                    continue
                seen.add(target)
                pending.append(target)
        return seen

    scopes = {}
    for edge, source, target in operators:
        after = reachable(target)
        for origin in sorted(dominators.get(source, ()), reverse=True):
            if origin[0] >= source[0] or len(successors.get(origin, ())) < 2:
                continue
            if after & reachable(origin, (source, target)):
                scopes[(edge.x1, edge.y)] = origin
                break
    return scopes


def analyze_row_logic(row: LadderRow, labels: LabelResolver | None = None) -> RowLogicAnalysis:
    """Compute logic for every coordinate and driver output in one row.

    A driver's coordinate is its input terminal. Vertical branches join those
    inputs just like any other coordinate; the driver itself has no horizontal
    edge and therefore cannot feed through its output to the right.
    """

    elements = positioned_elements(row, labels)
    width, height = parse_dim(row.dim or extract_dim(row.data))
    max_y = max([height - 1, *[element.y for element in elements], 0])
    graph = topology_graph(row, elements)
    scopes = expression_branch_scopes(graph)
    slices = {}

    def evaluate(start=None, stop_x=None):
        cache_key = (start, stop_x)
        if cache_key in slices:
            return slices[cache_key]
        formulas: dict[tuple[int, int], dict[str, Any]] = defaultdict(logic_false)
        if start is None:
            for y in graph.left_rail_rows:
                if 0 <= y <= max_y:
                    formulas[(0, y)] = logic_true()
        else:
            formulas[start] = logic_true()

        for x in graph.x_values:
            if start is not None and x < start[0]:
                continue
            for component in graph.vertical_by_x.get(x, ()):
                # These are input terminals, including coordinates that also
                # host an output instruction. All incoming paths join here.
                ys = sorted(component)
                merged = or_logic([formulas[(x, y)] for y in ys])
                for y in ys:
                    formulas[(x, y)] = merged
            if stop_x is not None and x >= stop_x:
                break

            for edge in graph.horizontal_by_x.get(x, ()):
                coordinate = (edge.x1, edge.y)
                if coordinate in graph.sink_nodes:
                    continue
                incoming = formulas[coordinate]
                scope = scopes.get(coordinate)
                if scope is not None and (start is None or scope[0] >= start[0]):
                    local = evaluate(scope, x)[coordinate]
                    transformed = input_expression_logic(edge.element, local)
                    candidate = and_logic([formulas[scope], transformed])
                else:
                    transformed = input_expression_logic(edge.element, incoming) if edge.element else None
                    if transformed is not None:
                        candidate = transformed
                    else:
                        if is_false(incoming):
                            continue
                        condition = element_condition_logic(edge.element) if edge.element else logic_true()
                        candidate = and_logic([incoming, condition])
                key = (edge.x2, edge.y)
                formulas[key] = or_logic([formulas[key], candidate])
        slices[cache_key] = formulas
        return formulas

    formulas = evaluate()

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
        edge_prefix = {"p": "P ", "f": "F "}.get(edge, "")
        return f"[{prefix}{edge_prefix}{device}]"
    if op == "predicate":
        if node.get("expression_operator"):
            args = node.get("args", [])
            text = logic_to_text(args[0]) if len(args) == 1 else "[UNKNOWN INPUT]"
            return f"{node.get('opcode', 'UNKNOWN')}({text})"
        opcode = str(node.get("opcode", "predicate"))
        operands = node.get("operands")
        if operands is None:
            operands = [str(value) for value in node.get("constants", [])]
            operands.extend(str(ref.get("device", ref.get("raw_device", ""))) for ref in node.get("devices", []))
        return f"[{opcode} {' '.join(operands)}]".strip()
    if op == "too_large":
        return "[TOO LARGE]"
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
                    "ct_code": current.get("ct_code", ""),
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

    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[dict[str, Any]] = []
    for ref in refs:
        key = (
            str(ref.get("device", "")),
            str(ref.get("role", "")),
            str(ref.get("required_state", "")),
            str(ref.get("predicate", "")),
            str(ref.get("ct_code", "")),
        )
        if key in seen:
            continue
        unique.append(ref)
        seen.add(key)
    return unique


def logic_stats(node: dict[str, Any]) -> dict[str, int]:
    stats = {
        "contacts": 0, "predicates": 0, "and_nodes": 0, "or_nodes": 0,
        "unknowns": 0,
        # A condition this stopped expanding. Counted, because a caller that
        # reads the refs below it would otherwise see a shorter condition and
        # have no way to know it was cut.
        "too_large": 0,
    }

    def visit(current: dict[str, Any]) -> None:
        op = current.get("op")
        if op == "contact":
            stats["contacts"] += 1
        elif op == "predicate":
            stats["predicates"] += 1
            for child in current.get("args", []):
                visit(child)
        elif op == "unknown":
            stats["unknowns"] += 1
        elif op == "too_large":
            stats["too_large"] += 1
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
