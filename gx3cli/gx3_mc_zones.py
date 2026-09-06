from __future__ import annotations

"""Project-level ladder execution context: MC/MCR, jumps, and CALL scopes.

``enable_logic_for_output`` answers one rung's local topology. This module adds
execution context that lives outside that rung:

- MC/MCR zones: rows execute only while every active master condition is true.
- CJ/SCJ/GOEND: targets are still unresolved, so affected rows are surfaced as
  execution-uncertain instead of pretending the local rung is the whole answer.
- CALL: a statically resolved same-program P pointer runs from P through RET.
  The call-site enable predicate is therefore part of every write in that
  subroutine. Multiple call sites are OR alternatives; nested CALLs compose the
  caller invocation with the nested call-site condition.

ECALL deliberately stays conservative until its program-file operand can be
mapped to the exact program/LDDB from project evidence. Duplicate pointers,
missing RET, missing pointer definitions, unreadable call-site topology, ECALL
targets and recursive/cyclic invocation are kept as explicit unresolved
execution context rather than guessed.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from gx3cli.extract_gx3_extended_instruction_knowledge import (
    LABEL_TOKEN_PREFIX,
    element_meta,
    extract_elements,
    header_tokens,
    parse_header_ops,
)
from gx3cli.gx3_label_resolve import LabelRef, LabelResolver, split_label_token
from gx3cli.gx3_ladder_logic import (
    DEVICE_ARG_RE,
    FlowElement,
    and_logic,
    enable_logic_for_output,
    logic_to_text,
    or_logic,
    parse_pos,
    positioned_elements,
)
from gx3cli.gx3_ladder_print import parse_pointers, parse_rung
from gx3cli.review_gx3_project import LadderRow


MC_OPS = {"MC"}
MCR_OPS = {"MCR"}
JUMP_OPS = {"CJ", "SCJ", "GOEND"}
CALL_OPS = {"CALL", "ECALL"}
CONTROL_OPS = MC_OPS | MCR_OPS | JUMP_OPS


@dataclass
class McZone:
    lddb: str
    start_pos: int
    nesting: int
    relay: str
    condition: dict[str, Any]
    condition_text: str
    end_pos: int | None = None
    kind: str = "mc"
    pointer: int | None = None

    def contains(self, pos: int) -> bool:
        return self.start_pos < pos and (self.end_pos is None or pos < self.end_pos)

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "nesting": self.nesting,
            "relay": self.relay,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "condition_text": self.condition_text,
        }
        if self.kind != "mc":
            out["kind"] = self.kind
        if self.pointer is not None:
            out["pointer"] = self.pointer
        return out


@dataclass
class JumpSite:
    lddb: str
    pos: int
    opcode: str
    condition_text: str
    start_pos: int | None = None
    end_pos: int | None = None
    reason: str = ""

    def applies(self, pos: int) -> bool:
        if self.start_pos is not None:
            return self.start_pos <= pos and (self.end_pos is None or pos < self.end_pos)
        return self.pos < pos

    def summary(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "pos": self.pos,
            "opcode": self.opcode,
            "condition_text": self.condition_text,
        }
        if self.reason:
            out["reason"] = self.reason
        if self.start_pos is not None:
            out["scope_start_pos"] = self.start_pos
            out["scope_end_pos"] = self.end_pos
        return out


@dataclass(frozen=True)
class SubroutineScope:
    lddb: str
    pointer: int
    start_pos: int
    end_pos: int
    complete: bool = True
    reason: str = ""

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.lddb, self.pointer, self.start_pos)

    def contains(self, pos: int) -> bool:
        return self.start_pos <= pos < self.end_pos


@dataclass
class CallSite:
    lddb: str
    pos: int
    opcode: str
    pointer: int | None
    condition: dict[str, Any]
    condition_text: str
    target_keys: list[tuple[str, int, int]] = field(default_factory=list)


@dataclass
class CallContext:
    zones: dict[str, list[McZone]] = field(default_factory=dict)
    unresolved: dict[str, list[JumpSite]] = field(default_factory=dict)


def inferred_label_resolver(rows: list[LadderRow]) -> LabelResolver | None:
    """Recover label names already resolved on the row occurrence boundary."""
    entries: dict[tuple[str, int], LabelRef] = {}
    for row in rows:
        tokens = [token for token in header_tokens(row.data) if token.startswith(LABEL_TOKEN_PREFIX)]
        occurrences = [occ for occ in row.occurrences if occ.device_type == "LABEL"]
        for token, occ in zip(tokens, occurrences):
            parsed = split_label_token(token)
            name = str(occ.device)
            if parsed is None or not name or name.startswith(LABEL_TOKEN_PREFIX):
                continue
            entries.setdefault(parsed, LabelRef(name=name))
    return LabelResolver(entries) if entries else None


def control_elements(
    row: LadderRow, labels: LabelResolver | None = None
) -> list[tuple[FlowElement, str]]:
    """(FlowElement, raw element text) pairs for MC/MCR/jump ops in one row."""
    header_ops = parse_header_ops(row.data)
    if not any(hop.op in CONTROL_OPS for hop in header_ops):
        return []
    non_wire = [el for el in positioned_elements(row, labels) if not el.is_wire]
    raws: list[str] = []
    op_index = 0
    for raw in extract_elements(row.data):
        meta = element_meta(raw)
        if parse_pos(str(meta.get("pos", ""))) is None:
            continue
        if str(meta.get("element_kind", "")) == "wire" or raw.startswith("e{s=wire"):
            continue
        if op_index >= len(header_ops):
            continue
        raws.append(raw)
        op_index += 1
    return [(el, raw) for el, raw in zip(non_wire, raws) if el.role in CONTROL_OPS]


def first_arg_number(raw: str) -> int | None:
    match = DEVICE_ARG_RE.search(raw)
    if not match:
        return None
    return int(match.group(1) or match.group(2))


def mc_relay_device(element: FlowElement) -> str:
    if len(element.devices) >= 2:
        return element.devices[-1].device
    return ""


def _build_master_zones(
    rows: list[LadderRow], labels: LabelResolver | None
) -> dict[str, list[McZone]]:
    by_lddb: dict[str, list[LadderRow]] = defaultdict(list)
    for row in rows:
        by_lddb[row.lddb].append(row)

    zones_by_lddb: dict[str, list[McZone]] = {}
    for lddb, ladder_rows in by_lddb.items():
        zones: list[McZone] = []
        open_zones: list[McZone] = []
        for row in sorted(ladder_rows, key=lambda r: r.pos):
            for element, raw in control_elements(row, labels):
                if element.role in MC_OPS:
                    condition = enable_logic_for_output(row, element, labels)
                    zone = McZone(
                        lddb=lddb,
                        start_pos=row.pos,
                        nesting=first_arg_number(raw) or 0,
                        relay=mc_relay_device(element),
                        condition=condition,
                        condition_text=logic_to_text(condition),
                    )
                    zones.append(zone)
                    open_zones.append(zone)
                elif element.role in MCR_OPS:
                    nesting = first_arg_number(raw)
                    still_open: list[McZone] = []
                    for zone in open_zones:
                        if nesting is None or zone.nesting >= nesting:
                            zone.end_pos = row.pos
                        else:
                            still_open.append(zone)
                    open_zones = still_open
        if zones:
            zones_by_lddb[lddb] = zones
    return zones_by_lddb


def active_zones(zones_by_lddb: dict[str, list[McZone]], lddb: str, pos: int) -> list[McZone]:
    return [zone for zone in zones_by_lddb.get(lddb, []) if zone.contains(pos)]


def zone_condition_terms(zones: list[McZone]) -> list[dict[str, Any]]:
    return [zone.condition for zone in zones]


def apply_zone_conditions(logic: dict[str, Any], zones: list[McZone]) -> dict[str, Any]:
    if not zones:
        return logic
    return and_logic([*zone_condition_terms(zones), logic])


def _pointer_definitions(rows: list[LadderRow]) -> dict[tuple[str, int], list[int]]:
    found: dict[tuple[str, int], list[int]] = defaultdict(list)
    for row in rows:
        for pointer, _grid_y in parse_pointers(row.data):
            found[(row.lddb, pointer)].append(row.pos)
    for positions in found.values():
        positions.sort()
    return dict(found)


def _ret_positions(rows: list[LadderRow]) -> dict[str, list[int]]:
    found: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        if any(hop.op == "RET" for hop in parse_header_ops(row.data)):
            found[row.lddb].append(row.pos)
    for positions in found.values():
        positions.sort()
    return dict(found)


def _scope_for_start(
    lddb: str,
    pointer: int,
    start_pos: int,
    ret_positions: dict[str, list[int]],
    max_pos: dict[str, int],
) -> SubroutineScope:
    end = next((pos for pos in ret_positions.get(lddb, []) if pos > start_pos), None)
    if end is None:
        return SubroutineScope(
            lddb,
            pointer,
            start_pos,
            max_pos.get(lddb, start_pos) + 1,
            complete=False,
            reason=f"CALL P{pointer} target has no following RET in {lddb}",
        )
    return SubroutineScope(lddb, pointer, start_pos, end)


def _call_sites(
    rows: list[LadderRow],
    labels: LabelResolver | None,
    master_zones: dict[str, list[McZone]],
) -> list[CallSite]:
    sites: list[CallSite] = []
    for row in rows:
        if not any(hop.op in CALL_OPS for hop in parse_header_ops(row.data)):
            continue
        printed, _verticals, _wires = parse_rung(row, labels)
        flows = [element for element in positioned_elements(row, labels) if not element.is_wire]
        for op in printed:
            if op.role not in CALL_OPS:
                continue
            pointer_values: list[int] = []
            for operand in op.operands:
                if not operand.startswith("#P"):
                    continue
                try:
                    pointer_values.append(int(operand[2:]))
                except ValueError:
                    pass
            pointer = pointer_values[0] if len(pointer_values) == 1 else None
            element = next(
                (
                    candidate
                    for candidate in flows
                    if candidate.x == op.x and candidate.y == op.y and candidate.role == op.role
                ),
                None,
            )
            if element is None:
                condition: dict[str, Any] = {
                    "op": "unknown",
                    "kind": "call_site_topology",
                    "opcode": op.role,
                }
            else:
                condition = enable_logic_for_output(row, element, labels)
                condition = apply_zone_conditions(
                    condition, active_zones(master_zones, row.lddb, row.pos)
                )
            sites.append(
                CallSite(
                    lddb=row.lddb,
                    pos=row.pos,
                    opcode=op.role,
                    pointer=pointer,
                    condition=condition,
                    condition_text=logic_to_text(condition),
                )
            )
    return sites


def _call_context(
    rows: list[LadderRow],
    labels: LabelResolver | None,
    master_zones: dict[str, list[McZone]],
) -> CallContext:
    sites = _call_sites(rows, labels, master_zones)
    if not sites:
        return CallContext()

    pointer_defs = _pointer_definitions(rows)
    ret_positions = _ret_positions(rows)
    min_pos: dict[str, int] = {}
    max_pos: dict[str, int] = {}
    for row in rows:
        min_pos[row.lddb] = min(min_pos.get(row.lddb, row.pos), row.pos)
        max_pos[row.lddb] = max(max_pos.get(row.lddb, row.pos), row.pos)
    all_lddbs = sorted(max_pos)

    wanted: set[tuple[str, int]] = set()
    for site in sites:
        if site.pointer is None:
            continue
        if site.opcode == "CALL":
            wanted.add((site.lddb, site.pointer))
        else:
            for lddb, pointer in pointer_defs:
                if pointer == site.pointer:
                    wanted.add((lddb, pointer))

    scopes: list[SubroutineScope] = []
    for target in sorted(wanted):
        lddb, pointer = target
        for start in pointer_defs.get(target, []):
            scopes.append(_scope_for_start(lddb, pointer, start, ret_positions, max_pos))

    scopes_by_target: dict[tuple[str, int], list[SubroutineScope]] = defaultdict(list)
    scopes_by_pointer: dict[int, list[SubroutineScope]] = defaultdict(list)
    for scope in scopes:
        scopes_by_target[(scope.lddb, scope.pointer)].append(scope)
        scopes_by_pointer[scope.pointer].append(scope)

    unresolved_reason: dict[tuple[str, int, int], set[str]] = defaultdict(set)
    global_unresolved: dict[str, set[str]] = defaultdict(set)
    incoming: dict[tuple[str, int, int], list[CallSite]] = defaultdict(list)

    def mark_global(reason: str, lddbs: list[str]) -> None:
        for lddb in lddbs:
            global_unresolved[lddb].add(reason)

    for site in sites:
        if site.pointer is None:
            targets = all_lddbs if site.opcode == "ECALL" else [site.lddb]
            mark_global(
                f"{site.opcode} at {site.lddb}:{site.pos} pointer target could not be decoded",
                targets,
            )
            continue

        if site.opcode == "ECALL":
            candidates = scopes_by_pointer.get(site.pointer, [])
            if not candidates:
                mark_global(
                    f"ECALL P{site.pointer} has no matching P definition and its program target is unresolved",
                    all_lddbs,
                )
            for scope in candidates:
                unresolved_reason[scope.key].add(
                    f"ECALL P{site.pointer} program target is not resolved to one LDDB"
                )
            site.target_keys = [scope.key for scope in candidates]
            continue

        candidates = scopes_by_target.get((site.lddb, site.pointer), [])
        site.target_keys = [scope.key for scope in candidates]
        if not candidates:
            mark_global(
                f"CALL P{site.pointer} has no pointer definition in {site.lddb}",
                [site.lddb],
            )
            continue
        if len(candidates) == 1:
            incoming[candidates[0].key].append(site)
            if site.condition.get("op") == "unknown":
                unresolved_reason[candidates[0].key].add(
                    f"CALL P{site.pointer} call-site topology could not be resolved"
                )
        else:
            for scope in candidates:
                unresolved_reason[scope.key].add(
                    f"CALL P{site.pointer} has {len(candidates)} pointer definitions in {site.lddb}"
                )

    for scope in scopes:
        if not scope.complete:
            unresolved_reason[scope.key].add(scope.reason)

    def containing_scope(lddb: str, pos: int) -> SubroutineScope | None:
        candidates = [scope for scope in scopes if scope.lddb == lddb and scope.contains(pos)]
        if not candidates:
            return None
        return max(candidates, key=lambda scope: scope.start_pos)

    cache: dict[tuple[str, int, int], dict[str, Any] | None] = {}
    visiting: list[tuple[str, int, int]] = []

    def invocation(scope: SubroutineScope) -> dict[str, Any] | None:
        if scope.key in cache:
            return cache[scope.key]
        if scope.key in visiting:
            cycle = visiting[visiting.index(scope.key) :] + [scope.key]
            text = " -> ".join(f"{key[0]}:P{key[1]}" for key in cycle)
            for key in cycle:
                unresolved_reason[key].add(f"recursive/cyclic CALL invocation: {text}")
            return None

        visiting.append(scope.key)
        terms: list[dict[str, Any]] = []
        for site in incoming.get(scope.key, []):
            parent = containing_scope(site.lddb, site.pos)
            if parent is None:
                terms.append(site.condition)
                continue
            parent_logic = invocation(parent)
            if parent_logic is None:
                unresolved_reason[scope.key].add(
                    f"caller execution context for CALL P{scope.pointer} is unresolved"
                )
                continue
            terms.append(and_logic([parent_logic, site.condition]))
        visiting.pop()

        if not terms:
            unresolved_reason[scope.key].add(
                f"P{scope.pointer} has no statically resolved CALL entry condition"
            )
            cache[scope.key] = None
            return None
        cache[scope.key] = or_logic(terms)
        return cache[scope.key]

    context = CallContext()
    for scope in scopes:
        logic = invocation(scope)
        if logic is not None:
            context.zones.setdefault(scope.lddb, []).append(
                McZone(
                    lddb=scope.lddb,
                    start_pos=scope.start_pos - 1,
                    end_pos=scope.end_pos,
                    nesting=0,
                    relay=f"CALL P{scope.pointer}",
                    condition=logic,
                    condition_text=logic_to_text(logic),
                    kind="call_invocation",
                    pointer=scope.pointer,
                )
            )

        reasons = sorted(unresolved_reason.get(scope.key, set()))
        if reasons:
            reason = "; ".join(reasons)
            context.unresolved.setdefault(scope.lddb, []).append(
                JumpSite(
                    lddb=scope.lddb,
                    pos=scope.start_pos - 1,
                    opcode="CALL_CONTEXT",
                    condition_text=reason,
                    start_pos=scope.start_pos,
                    end_pos=scope.end_pos,
                    reason=reason,
                )
            )

    # If the target scope itself cannot be located, there is no honest bounded
    # subroutine range to annotate. Fail closed over the smallest defensible
    # domain: the current LDDB for CALL, or every LDDB for unresolved ECALL.
    for lddb, reasons in global_unresolved.items():
        if lddb not in min_pos:
            continue
        reason = "; ".join(sorted(reasons))
        start = min_pos[lddb]
        context.unresolved.setdefault(lddb, []).append(
            JumpSite(
                lddb=lddb,
                pos=start - 1,
                opcode="CALL_CONTEXT",
                condition_text=reason,
                start_pos=start,
                end_pos=max_pos[lddb] + 1,
                reason=reason,
            )
        )

    for zones in context.zones.values():
        zones.sort(key=lambda zone: (zone.start_pos, zone.end_pos or 2**63))
    for sites_for_lddb in context.unresolved.values():
        sites_for_lddb.sort(key=lambda site: (site.start_pos or site.pos, site.pos))
    return context


def build_mc_zones(
    rows: list[LadderRow], labels: LabelResolver | None = None
) -> dict[str, list[McZone]]:
    labels = labels or inferred_label_resolver(rows)
    master = _build_master_zones(rows, labels)
    calls = _call_context(rows, labels, master)
    merged: dict[str, list[McZone]] = {lddb: list(zones) for lddb, zones in master.items()}
    for lddb, zones in calls.zones.items():
        merged.setdefault(lddb, []).extend(zones)
    for zones in merged.values():
        zones.sort(key=lambda zone: (zone.start_pos, zone.end_pos or 2**63, zone.kind))
    return merged


def build_jump_index(
    rows: list[LadderRow], labels: LabelResolver | None = None
) -> dict[str, list[JumpSite]]:
    labels = labels or inferred_label_resolver(rows)
    by_lddb: dict[str, list[JumpSite]] = defaultdict(list)
    for row in rows:
        for element, _raw in control_elements(row, labels):
            if element.role not in JUMP_OPS:
                continue
            condition = enable_logic_for_output(row, element, labels)
            by_lddb[row.lddb].append(
                JumpSite(
                    lddb=row.lddb,
                    pos=row.pos,
                    opcode=element.role,
                    condition_text=logic_to_text(condition),
                )
            )

    master = _build_master_zones(rows, labels)
    call_context = _call_context(rows, labels, master)
    for lddb, sites in call_context.unresolved.items():
        by_lddb[lddb].extend(sites)

    for sites in by_lddb.values():
        sites.sort(key=lambda site: site.pos)
    return dict(by_lddb)


def jumps_before(jump_index: dict[str, list[JumpSite]], lddb: str, pos: int) -> list[JumpSite]:
    return [site for site in jump_index.get(lddb, []) if site.applies(pos)]
