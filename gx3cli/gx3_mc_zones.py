from __future__ import annotations

"""MC/MCR master-control zone reconstruction and conditional-jump indexing.

GX Works3 executes rungs between ``MC N M`` and ``MCR N`` only while the MC
input condition is true. ``enable_logic_for_output`` models a single rung, so
without this module every coil inside an MC zone reports an ON condition that
is missing the master condition entirely.

``build_mc_zones`` scans each LDDB in pos order, reconstructs the open/close
ranges per nesting level, and stores the MC instruction's own enable logic as
the zone condition. Rows inside a zone must AND that condition (all nesting
levels stack, so a row inside nested zones gets every active zone condition).

``build_jump_index`` records CJ/SCJ/GOEND sites. Their jump targets (pointer
P labels) are not resolved, so rows after a conditional jump only receive a
warning that execution is not guaranteed - the jump condition is NOT folded
into enable logic.

Known limits:
- The first argument of MC/MCR is the nesting number N; the intermediate
  decoder mis-types it with the row's default device type, so the nesting is
  read positionally from the raw argument and the relay is the last device.
- An MCR with an unreadable N closes every open zone (conservative for the
  common single-level case; nested projects should verify manually).
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from gx3cli.extract_gx3_extended_instruction_knowledge import element_meta, extract_elements, parse_header_ops
from gx3cli.gx3_ladder_logic import (
    DEVICE_ARG_RE,
    FlowElement,
    and_logic,
    enable_logic_for_output,
    logic_to_text,
    parse_pos,
    positioned_elements,
)
from gx3cli.review_gx3_project import LadderRow


MC_OPS = {"MC"}
MCR_OPS = {"MCR"}
JUMP_OPS = {"CJ", "SCJ", "GOEND"}
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

    def contains(self, pos: int) -> bool:
        return self.start_pos < pos and (self.end_pos is None or pos < self.end_pos)

    def summary(self) -> dict[str, Any]:
        return {
            "nesting": self.nesting,
            "relay": self.relay,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "condition_text": self.condition_text,
        }


@dataclass
class JumpSite:
    lddb: str
    pos: int
    opcode: str
    condition_text: str

    def summary(self) -> dict[str, Any]:
        return {"pos": self.pos, "opcode": self.opcode, "condition_text": self.condition_text}


def control_elements(row: LadderRow) -> list[tuple[FlowElement, str]]:
    """(FlowElement, raw element text) pairs for MC/MCR/jump ops in one row.

    Raw text is needed because the nesting argument N is not decodable as a
    device: it is read positionally with DEVICE_ARG_RE.
    """
    header_ops = parse_header_ops(row.data)
    if not any(hop.op in CONTROL_OPS for hop in header_ops):
        return []
    non_wire = [el for el in positioned_elements(row) if not el.is_wire]
    raws: list[str] = []
    op_index = 0
    for raw in extract_elements(row.data):
        meta = element_meta(raw)
        if parse_pos(str(meta.get("pos", ""))) is None:
            continue
        if str(meta.get("element_kind", "")) == "wire":
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
    # arg order is (N, relay); with a single decoded device it is ambiguous,
    # so only trust it when both were decoded. Relay is informational only.
    if len(element.devices) >= 2:
        return element.devices[-1].device
    return ""


def build_mc_zones(rows: list[LadderRow]) -> dict[str, list[McZone]]:
    by_lddb: dict[str, list[LadderRow]] = defaultdict(list)
    for row in rows:
        by_lddb[row.lddb].append(row)

    zones_by_lddb: dict[str, list[McZone]] = {}
    for lddb, ladder_rows in by_lddb.items():
        zones: list[McZone] = []
        open_zones: list[McZone] = []
        for row in sorted(ladder_rows, key=lambda r: r.pos):
            for element, raw in control_elements(row):
                if element.role in MC_OPS:
                    condition = enable_logic_for_output(row, element)
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


def build_jump_index(rows: list[LadderRow]) -> dict[str, list[JumpSite]]:
    by_lddb: dict[str, list[JumpSite]] = defaultdict(list)
    for row in rows:
        for element, _raw in control_elements(row):
            if element.role not in JUMP_OPS:
                continue
            condition = enable_logic_for_output(row, element)
            by_lddb[row.lddb].append(
                JumpSite(
                    lddb=row.lddb,
                    pos=row.pos,
                    opcode=element.role,
                    condition_text=logic_to_text(condition),
                )
            )
    for sites in by_lddb.values():
        sites.sort(key=lambda site: site.pos)
    return dict(by_lddb)


def jumps_before(jump_index: dict[str, list[JumpSite]], lddb: str, pos: int) -> list[JumpSite]:
    return [site for site in jump_index.get(lddb, []) if site.pos < pos]
