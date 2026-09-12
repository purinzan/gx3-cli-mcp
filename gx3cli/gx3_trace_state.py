from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict, deque
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from gx3cli.gx3_input_identity import dependency_snapshot, input_stamp, input_version

from gx3cli.gx3_device_name import format_device as _format_device, parse_device_name as _parse_device_name
from gx3cli.extract_hmi_build_info import CommentInfo
from gx3cli.review_gx3_project import (
    DeviceOcc,
    LadderRow,
    classify_condition,
    comment_for_device,
    load_comments_for_root,
    load_rows,
)
from gx3cli.gx3_ladder_logic import (
    condition_refs_from_logic,
    enable_logic_for_output,
    logic_stats,
    logic_to_text,
    or_logic,
    row_logic_analysis,
)
from gx3cli.gx3_label_resolve import LabelResolver, load_label_resolver
from gx3cli.gx3_mc_zones import active_zones, apply_zone_conditions, build_jump_index, build_mc_zones, jumps_before
from gx3cli.gx3_external_inputs import (
    classify_external_contact,
    load_refresh_areas,
    load_unit_io_areas,
    RefreshArea,
    UnitIoArea,
)
from gx3cli.gx3_analysis_state import (
    DECODE,
    DISCOVERY,
    NOT_EVALUATED,
    SEMANTICS,
    PARTIAL,
    REACH,
    TOPOLOGY,
    TRUNCATED,
    AnalysisState,
    checked,
)
from gx3cli.gx3_instruction_table import is_edge_triggered, manual_exec_condition
from gx3cli.gx3_project_paths import default_comm_prefix, default_project_root


DEVICE_RE = re.compile(r"^([A-Z]+)(-?\d+)$", re.IGNORECASE)
CONTACT_ROLES = {"a", "b"}
ON_DRIVER_ROLES = {"c", "SET", "PLS", "PLF", "OUT__16", "OUTH__16"}
OFF_DRIVER_ROLES = {"RST"}
DRIVER_ROLES = ON_DRIVER_ROLES | OFF_DRIVER_ROLES
MOJIBAKE_MARKERS = ("â", "ã", "Ã", "ï")
MOJIBAKE_REPLACEMENTS = {
    "\u00e2\u2013\u00bd": "\u25bd",
    "\u00e2\u2020\u2018": "\u2191",
    "\u00e2\u02dc\u2020": "\u2606",
    "\u00e2\u02dc\u2026": "\u2605",
}


def display_text(value: object) -> str:
    text = ("" if value is None else str(value)).replace("\r", " ").replace("\n", " ")
    for bad, good in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(bad, good)
    if not any(marker in text for marker in MOJIBAKE_MARKERS):
        return text
    for encoding in ("cp1252", "latin1"):
        try:
            repaired = text.encode(encoding).decode("utf-8")
        except UnicodeError:
            continue
        if repaired and sum(repaired.count(marker) for marker in MOJIBAKE_MARKERS) < sum(
            text.count(marker) for marker in MOJIBAKE_MARKERS
        ):
            return repaired
    return text


def parse_device(text: str) -> tuple[str, int]:
    return _parse_device_name(text)


def normalize_device(text: str) -> str:
    dev_type, number = parse_device(text)
    return _format_device(dev_type, number)


def normalize_trace_device(text: str) -> str:
    """Normalize a physical device and preserve a symbolic label name."""
    try:
        return normalize_device(text)
    except ValueError:
        return text.strip()


def project_label_from_root(root: Path) -> str:
    name = root.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"


def device_key(device: str) -> tuple[str, int]:
    return parse_device(device)


TEMPORAL_DEVICE_TYPES = {"T", "ST", "C", "LT", "LC", "LST"}
TIMER_DEVICE_TYPES = {"T", "ST", "LT", "LST"}
COUNTER_DEVICE_TYPES = {"C", "LC"}


def semantic_gaps(driver_rows: list[dict]) -> list[str]:
    """What the listed conditions do not account for.

    Local Boolean topology is only one layer. Stateful instructions, unresolved
    control flow, and project-level execution zones are kept distinct so a
    structurally exact rung cannot silently become a semantically complete
    project answer.
    """
    gaps: list[str] = []

    call_guards = [
        site
        for row in driver_rows
        for site in (row.get("cj_upstream") or [])
        if str(site.get("opcode", "")) == "CALL_CONTEXT"
    ]
    jump_guards = [
        site
        for row in driver_rows
        for site in (row.get("cj_upstream") or [])
        if str(site.get("opcode", "")) != "CALL_CONTEXT"
    ]
    if call_guards:
        reasons = []
        for site in call_guards:
            reason = str(site.get("reason") or site.get("condition_text") or "call target unresolved")
            if reason not in reasons:
                reasons.append(reason)
        sample = "; ".join(reasons[:3])
        if len(reasons) > 3:
            sample += f" (+{len(reasons) - 3} more)"
        gaps.append(f"CALL/ECALL invocation context is unresolved: {sample}")
    if jump_guards:
        gaps.append(
            "a conditional jump above a driver row may bypass it; jump targets are "
            "not resolved"
        )

    # Flat mode intentionally lists occurrences rather than deriving topology.
    # If project-level execution zones exist, saying `checked` would imply those
    # omitted CALL/MC constraints were part of the answer. They are not.
    if any(not row.get("strict_logic") and row.get("mc_zones") for row in driver_rows):
        gaps.append(
            "project execution context (MC/CALL) exists but flat trace does not fold it; "
            "rerun with --strict-logic for the project-level write-execution predicate"
        )

    roles = {role for row in driver_rows for role in (row.get("driver_roles") or [])}
    if {"SET", "RST"} & roles:
        gaps.append("SET/RST: the condition shown is when it changes, not when it holds")
    if {"PLS", "PLF"} & roles:
        gaps.append("PLS/PLF: the write happens on an edge, not while the condition holds")
    edge_write_roles = sorted(
        role
        for role in roles
        if role not in {"PLS", "PLF"} and is_edge_triggered(role) is True
    )
    if edge_write_roles:
        gaps.append(
            "edge-triggered value writes ("
            + ", ".join(edge_write_roles[:6])
            + (f" +{len(edge_write_roles) - 6} more" if len(edge_write_roles) > 6 else "")
            + "): the write occurs on an execution edge, not while the rung level holds"
        )
    if {"OUT__16", "OUTH__16"} & roles:
        gaps.append("a timer or counter is driven here; its contact means done, not enabled")
    temporal = {
        str(condition.get("device", ""))
        for row in driver_rows
        for condition in (row.get("conditions") or [])
        if str(condition.get("device", ""))[:2].rstrip("0123456789") in TEMPORAL_DEVICE_TYPES
        or str(condition.get("device", ""))[:1] in TEMPORAL_DEVICE_TYPES
    }
    if temporal:
        listed = ", ".join(sorted(temporal)[:4])
        gaps.append(f"timer/counter contacts in the condition ({listed}): elapsed time is not modelled")
    if any(
        item.get("kind") == "expression_edge"
        for row in driver_rows for item in row.get("temporal_predicates", [])
    ):
        gaps.append("MEP/MEF: the expression edge requires previous-scan state")
    if any(
        item.get("kind") == "edge_contact"
        for row in driver_rows for item in row.get("temporal_predicates", [])
    ):
        gaps.append("pulse contacts require previous-scan state, not just the current device level")
    return gaps


def trace_state(
    truncated: bool,
    reasons: list[str],
    partial_rows: list,
    capped_rows: list | None = None,
    gaps: list[str] | None = None,
    label_gaps: list[str] | None = None,
) -> AnalysisState:
    """What this trace is worth, in the common analysis-state vocabulary."""
    found: list[tuple[str, str, str, str]] = []
    if partial_rows:
        found.append((
            PARTIAL,
            DECODE,
            f"{len(partial_rows)} driver rows were not fully interpreted",
            "gx3-cli parse-gaps --root <project>",
        ))
    for gap in label_gaps or []:
        found.append((
            PARTIAL,
            DECODE,
            gap,
            "gx3-cli label-probe --root <project>",
        ))
    if capped_rows:
        found.append((
            PARTIAL,
            TOPOLOGY,
            f"{len(capped_rows)} driver rows have a condition too large to expand; "
            "the conditions listed are fewer than the rung has",
            "read the rung itself: gx3-cli ladder-print / ladder-report",
        ))
    for gap in gaps or []:
        found.append((
            PARTIAL,
            SEMANTICS,
            gap,
            "read the rung and check these on the machine: gx3-cli ladder-print / ladder-report",
        ))
    if truncated:
        found.append((
            TRUNCATED,
            REACH,
            f"the search stopped at {', '.join(reasons) or 'a limit'}",
            "raise --max-depth or --max-devices and run it again",
        ))

    if not found:
        return checked()

    state, stage, reason, next_step = found[0]
    others = [
        {"stage": other_stage, "reason": other_reason, "next_step": other_next}
        for _, other_stage, other_reason, other_next in found[1:]
    ]
    return AnalysisState(
        state,
        reason=reason,
        next_step=next_step,
        stage=stage,
        detail={"also": others} if others else {},
    )


def device_comment(device: str, comments: dict[tuple[str, int], CommentInfo]) -> str:
    try:
        dev_type, number = device_key(device)
    except ValueError:
        return ""
    return comment_for_device(dev_type, number, comments)


def resolve_label_occurrences(rows: list[LadderRow], labels: LabelResolver) -> None:
    """Give row-level occurrences the same label identity as canonical decode."""
    for row in rows:
        for occ in row.occurrences:
            if occ.device_type != "LABEL" or not str(occ.device).startswith("_lid/"):
                continue
            ref = labels.resolve_token(str(occ.device))
            if ref is not None:
                occ.device = ref.name


def trace_output_elements_for(
    row: LadderRow, device: str, labels: LabelResolver
) -> list[Any]:
    """Output elements for a physical device or resolved symbolic label."""
    target = normalize_trace_device(device)
    return [
        element
        for element in row_logic_analysis(row, labels).elements
        if element.is_sink and any(ref.device == target and ref.is_written for ref in element.devices)
    ]


def relevant_unresolved_labels(driver_rows: list[dict[str, Any]]) -> list[str]:
    """Unresolved label tokens that actually lie on this trace, not elsewhere."""
    tokens: set[str] = set()
    for row in driver_rows:
        values: list[object] = [row.get("device", "")]
        values.extend(item.get("device", "") for item in row.get("conditions", []))
        values.extend(item.get("device", "") for item in row.get("same_row_outputs", []))
        values.extend(item.get("device", "") for item in row.get("instruction_refs", []))
        for value in values:
            text = str(value)
            if text.startswith("_lid/"):
                tokens.add(text)
    return sorted(tokens)


def label_resolution_gaps(labels: LabelResolver, unresolved: list[str]) -> list[str]:
    if not unresolved:
        return []
    sample = ", ".join(unresolved[:3])
    more = f" (+{len(unresolved) - 3} more)" if len(unresolved) > 3 else ""
    reason = f"; {labels.reason}" if labels.reason else ""
    return [
        f"{len(unresolved)} label references on this trace could not be resolved "
        f"(LabelData status={labels.status}{reason}): {sample}{more}"
    ]


def temporal_predicates(
    output_occs: list[DeviceOcc], condition_records: list[dict[str, Any]],
    enable_logic: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Known temporal/state predicates, without inventing their runtime value."""
    out: list[dict[str, Any]] = []
    for occ in output_occs:
        if occ.role == "SET":
            out.append({
                "kind": "latch_set",
                "device": occ.device,
                "source": "enable_logic",
                "requires_runtime_state": True,
            })
        elif occ.role == "RST":
            out.append({
                "kind": "latch_reset",
                "device": occ.device,
                "source": "enable_logic",
                "requires_runtime_state": True,
            })
        elif occ.role == "PLS":
            out.append({
                "kind": "rising_edge",
                "device": occ.device,
                "source": "enable_logic",
                "requires_runtime_state": True,
            })
        elif occ.role == "PLF":
            out.append({
                "kind": "falling_edge",
                "device": occ.device,
                "source": "enable_logic",
                "requires_runtime_state": True,
            })
        elif occ.role in {"OUT__16", "OUTH__16"}:
            kind = "timer_or_counter_state"
            if occ.device_type in TIMER_DEVICE_TYPES:
                kind = "timer_done"
            elif occ.device_type in COUNTER_DEVICE_TYPES:
                kind = "counter_done"
            out.append({
                "kind": kind,
                "device": occ.device,
                "source": "enable_logic",
                "requires_runtime_state": True,
            })
        elif is_edge_triggered(occ.role) is True:
            execution_condition = manual_exec_condition(occ.role) or "edge"
            out.append({
                "kind": "edge_triggered_write",
                "device": occ.device,
                "opcode": occ.role,
                "execution_condition": execution_condition,
                "source": "instruction_execution_condition",
                "requires_runtime_state": True,
            })

    for condition in condition_records:
        device = str(condition.get("device", ""))
        if condition.get("ct_code") in {"p", "f"}:
            out.append({
                "kind": "edge_contact", "device": device,
                "edge": condition["ct_code"], "requires_runtime_state": True,
            })
        try:
            dev_type, _number = parse_device(device)
        except ValueError:
            continue
        if dev_type in TIMER_DEVICE_TYPES:
            out.append({
                "kind": "timer_done_contact",
                "device": device,
                "required_state": condition.get("required_state", ""),
                "requires_runtime_state": True,
            })
        elif dev_type in COUNTER_DEVICE_TYPES:
            out.append({
                "kind": "counter_done_contact",
                "device": device,
                "required_state": condition.get("required_state", ""),
                "requires_runtime_state": True,
            })
    def visit(node):
        if node.get("expression_operator") and node.get("requires_previous_scan"):
            record = {
                "kind": "expression_edge", "opcode": node.get("opcode"),
                "position": node.get("position"), "source": "enable_logic",
                "requires_runtime_state": True,
            }
            if record not in out:
                out.append(record)
        for child in node.get("args", []):
            visit(child)

    if enable_logic:
        visit(enable_logic)
    return out


def execution_guards(row_jumps: list[Any]) -> list[dict[str, Any]]:
    """Machine-readable unresolved execution context for this row."""
    out: list[dict[str, Any]] = []
    for site in row_jumps:
        is_call = site.opcode == "CALL_CONTEXT"
        record: dict[str, Any] = {
            "kind": "call_context_unresolved" if is_call else "conditional_jump_unresolved",
            "opcode": site.opcode,
            "pos": site.pos,
            "condition": site.condition_text,
            "target_resolved": False,
        }
        if getattr(site, "reason", ""):
            record["reason"] = site.reason
        if getattr(site, "start_pos", None) is not None:
            record["scope_start_pos"] = site.start_pos
            record["scope_end_pos"] = site.end_pos
        out.append(record)
    return out


def driver_index(rows: list[LadderRow], include_reset: bool = True) -> dict[str, list[LadderRow]]:
    """Rows that decide a device's value, by device."""
    roles = DRIVER_ROLES if include_reset else ON_DRIVER_ROLES
    index: dict[str, list[LadderRow]] = defaultdict(list)
    for row in rows:
        seen: set[str] = set()
        for occ in row.occurrences:
            if occ.device in seen:
                continue
            writes = occ.role in roles or occ.access in {"write", "both"}
            if not writes:
                continue
            index[occ.device].append(row)
            seen.add(occ.device)
    return index


def occurrence_counts(rows: list[LadderRow]) -> dict[str, Counter[str]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        for occ in row.occurrences:
            counts[occ.device][occ.role] += 1
    return counts


def row_key(row: LadderRow) -> str:
    return f"{row.lddb}:{row.pos}"


def row_conditions(row: LadderRow) -> list[DeviceOcc]:
    return [occ for occ in row.occurrences if occ.role in CONTACT_ROLES]


def row_driver_occurrences(row: LadderRow) -> list[DeviceOcc]:
    return [occ for occ in row.occurrences if occ.role in DRIVER_ROLES]


def row_write_occurrences(row: LadderRow) -> list[DeviceOcc]:
    """Occurrences that actually write a value, including instruction targets."""
    return [
        occ
        for occ in row.occurrences
        if occ.role in DRIVER_ROLES or occ.access in {"write", "both"}
    ]


def row_instruction_refs(row: LadderRow) -> list[DeviceOcc]:
    return [occ for occ in row.occurrences if occ.role not in CONTACT_ROLES and occ.role not in DRIVER_ROLES]


def required_state(role: str) -> str:
    return "ON" if role == "a" else "OFF"


def driver_effect(role: str) -> str:
    if role == "RST":
        return "OFF/reset"
    if role == "SET":
        return "ON/set"
    if role in {"PLS", "PLF"}:
        return "ON/pulse"
    if role in {"OUT__16", "OUTH__16"}:
        return "ON/timer_or_counter"
    if is_edge_triggered(role) is True:
        return "write/edge-triggered"
    if role not in DRIVER_ROLES:
        return "write/value"
    return "ON/coil"


def condition_record(
    occ: DeviceOcc,
    comments: dict[tuple[str, int], CommentInfo],
    drivers: dict[str, list[LadderRow]],
    active_device: str,
    refresh_areas: list[RefreshArea],
    unit_io_areas: list[UnitIoArea],
) -> dict[str, Any]:
    classes = sorted(classify_condition(occ, comments))
    comment = comment_for_device(occ.device_type, occ.number, comments)
    has_driver = bool(drivers.get(occ.device))
    external = classify_external_contact(
        occ.device_type,
        occ.number,
        occ.device,
        comment,
        has_driver=has_driver,
        refresh_areas=refresh_areas,
        unit_io_areas=unit_io_areas,
    )
    return {
        "role": occ.role,
        "device": occ.device,
        "required_state": required_state(occ.role),
        "comment": comment,
        "classes": classes,
        "has_driver": has_driver,
        "self_reference": occ.device == active_device,
        **external,
    }


def logic_condition_record(
    ref: dict[str, Any],
    comments: dict[tuple[str, int], CommentInfo],
    drivers: dict[str, list[LadderRow]],
    active_device: str,
    row: LadderRow,
    refresh_areas: list[RefreshArea],
    unit_io_areas: list[UnitIoArea],
) -> dict[str, Any]:
    device = str(ref.get("device", ""))
    role = str(ref.get("role", ""))
    required = str(ref.get("required_state", ""))
    predicate = str(ref.get("predicate", ""))
    classes: list[str] = []
    comment = ""
    try:
        dev_type, number = parse_device(device)
        comment = comment_for_device(dev_type, number, comments)
        if role in CONTACT_ROLES:
            fake = DeviceOcc(
                device=device,
                device_type=dev_type,
                number=number,
                role=role,
                lddb=row.lddb,
                pos=row.pos,
                block_id=row.block_id,
                title=row.title,
                parse_status=row.parse_status,
            )
            classes = sorted(classify_condition(fake, comments))
    except ValueError:
        dev_type = ""
        number = 0
    has_driver = bool(drivers.get(device))
    external = classify_external_contact(
        dev_type,
        number,
        device,
        comment,
        has_driver=has_driver,
        refresh_areas=refresh_areas,
        unit_io_areas=unit_io_areas,
    )

    return {
        "role": role,
        "device": device,
        "required_state": required,
        "comment": comment,
        "classes": classes,
        "has_driver": has_driver,
        "self_reference": device == active_device,
        "position": ref.get("position", ""),
        "ct_code": ref.get("ct_code", ""),
        "predicate": predicate,
        **external,
    }


def simple_occ_record(occ: DeviceOcc, comments: dict[tuple[str, int], CommentInfo]) -> dict[str, Any]:
    return {
        "role": occ.role,
        "device": occ.device,
        "comment": comment_for_device(occ.device_type, occ.number, comments),
    }


@dataclass
class TraceInputs:
    root: Path
    comments: Any
    labels: LabelResolver
    rows: list[LadderRow]
    # Call-owned projections: both constant pruning and classification consume
    # these exact lists. The shared CSV resolver prefers outputs/ over CWD.
    refresh_areas: list[RefreshArea] = field(default_factory=lambda: load_refresh_areas())
    unit_io_areas: list[UnitIoArea] = field(default_factory=lambda: load_unit_io_areas())
    source_version: tuple | None = None
    communication_paths: dict[str, Path] = field(default_factory=dict)
    communication_version: tuple | None = None
    external_boundaries: tuple[dict[str, str] | None, str] | None = None
    xref_path: Path | None = None

    def validate_source(self, *, content: bool = True) -> None:
        if self.source_version is None:
            raise ValueError("trace inputs have no verified source version; use load_trace_inputs")
        current = input_version(self.root) if content else input_stamp(self.root)
        expected = self.source_version if content else self.source_version[1]
        if current != expected:
            raise SystemExit("project inputs changed during trace analysis; discard this result and retry")
        if self.communication_version is not None and dependency_snapshot(self.communication_paths) != self.communication_version:
            raise SystemExit("communication CSV inputs changed during trace analysis; discard this result and retry")


def _communication_inputs(root: Path) -> dict[str, Any]:
    from gx3cli.gx3_external_inputs import find_comm_csv
    from gx3cli.gx3_index_lite import open_existing, external_sources_from
    from gx3cli.gx3_workspace import index_paths

    lite_path, xref_path = index_paths(root)
    paths = None
    expected_files = None
    externals = None
    reason = ""
    try:
        with closing(open_existing(lite_path, root=root, required_tables=("external_sources",))) as con:
            meta = dict(con.execute("select key, value from meta"))
            selected = {name: Path(meta[name]) for name in ("refresh_csv", "unit_csv")}
            expected_files = json.loads(meta["external_dependencies"])["files"]
            externals = external_sources_from(con)
            paths = selected
    except (Exception, SystemExit) as exc:
        reason = f"index-lite unavailable for constant pruning: {exc}"
    if paths is None:
        # A missing/invalid index disables only optional pruning. Legacy trace
        # still reads the explicit environment/CWD communication inputs.
        paths = {"refresh_csv": find_comm_csv("refresh_areas.csv").absolute(),
                 "unit_csv": find_comm_csv("units.csv").absolute()}
        expected_files = None
        externals = None
    version = dependency_snapshot(paths)
    if expected_files is not None and version[0] != expected_files:
        raise SystemExit("communication CSV inputs changed after index validation; retry trace")
    return {
        "refresh_areas": load_refresh_areas(paths["refresh_csv"]),
        "unit_io_areas": load_unit_io_areas(paths["unit_csv"]),
        "communication_paths": paths, "communication_version": version,
        "external_boundaries": (externals, reason), "xref_path": xref_path,
    }


def load_trace_inputs(root: Path) -> TraceInputs:
    version = input_version(root)
    comments = load_comments_for_root(root)
    labels = load_label_resolver(root)
    rows = load_rows(root, comments)
    resolve_label_occurrences(rows, labels)
    return TraceInputs(Path(root).resolve(), comments, labels, rows, source_version=version,
                       **_communication_inputs(root))


def build_trace(
    root: Path,
    target_device: str,
    max_depth: int,
    max_devices: int,
    include_reset: bool,
    strict_logic: bool,
    *,
    inputs: TraceInputs | None = None,
    condition_refs_provider: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    inputs = inputs if inputs is not None else load_trace_inputs(root)
    if inputs.root != Path(root).resolve():
        raise ValueError("trace inputs belong to a different project root")
    inputs.validate_source(content=False)
    comments, labels, rows = inputs.comments, inputs.labels, inputs.rows
    refs_for_logic = condition_refs_provider or condition_refs_from_logic
    drivers = driver_index(rows, include_reset=include_reset)
    counts = occurrence_counts(rows)
    # Pass the same per-project resolver into the control-flow layer explicitly.
    # Its row-evidence fallback remains for older callers, but trace never needs
    # hidden process-global state or a second LabelData read.
    mc_zones = build_mc_zones(rows, labels)
    jump_index = build_jump_index(rows, labels)
    refresh_areas, unit_io_areas = inputs.refresh_areas, inputs.unit_io_areas

    target = normalize_trace_device(target_device)
    queue: deque[tuple[str, int, str]] = deque([(target, 0, "")])
    visited: set[str] = set()
    devices: list[dict[str, Any]] = []
    driver_rows: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    truncated = False
    truncated_reasons: set[str] = set()

    while queue:
        device, depth, parent = queue.popleft()
        if device in visited:
            continue
        if len(visited) >= max_devices:
            truncated = True
            truncated_reasons.add("max_devices")
            break
        visited.add(device)

        dev_counts = counts.get(device, Counter())
        rows_for_device = drivers.get(device, [])
        on_cause_logic: dict[str, Any] | None = None
        off_cause_logic: dict[str, Any] | None = None
        if strict_logic:
            on_terms: list[dict[str, Any]] = []
            off_terms: list[dict[str, Any]] = []
            for driver_row in rows_for_device:
                zones = active_zones(mc_zones, driver_row.lddb, driver_row.pos)
                for output in trace_output_elements_for(driver_row, device, labels):
                    output_logic = apply_zone_conditions(
                        enable_logic_for_output(driver_row, output, labels), zones
                    )
                    if output.role in OFF_DRIVER_ROLES:
                        off_terms.append(output_logic)
                    else:
                        on_terms.append(output_logic)
            on_cause_logic = or_logic(on_terms)
            off_cause_logic = or_logic(off_terms)
        out_coil_rows = {
            row_key(driver_row)
            for driver_row in rows_for_device
            if any(occ.device == device and occ.role == "c" for occ in driver_row.occurrences)
        }
        set_rows = {
            row_key(driver_row)
            for driver_row in rows_for_device
            if any(occ.device == device and occ.role == "SET" for occ in driver_row.occurrences)
        }
        device_warnings: list[str] = []
        if len(out_coil_rows) > 1:
            device_warnings.append(
                f"multi-coil: {len(out_coil_rows)} OUT rows drive {device}; "
                "last write per scan wins, so the OR of row conditions is an over-approximation"
            )
        if out_coil_rows and set_rows:
            device_warnings.append(
                f"mixed drivers: {device} has both OUT and SET rows; "
                "OUT overwrites the latch every scan it executes"
            )
        devices.append(
            {
                "device": device,
                "comment": device_comment(device, comments),
                "depth": depth,
                "parent": parent,
                "driver_row_count": len(rows_for_device),
                "occurrences_by_role": dict(dev_counts),
                "terminal": not bool(rows_for_device),
                "on_cause_logic": on_cause_logic,
                "on_cause_logic_text": logic_to_text(on_cause_logic) if on_cause_logic else "",
                "off_cause_logic": off_cause_logic,
                "off_cause_logic_text": logic_to_text(off_cause_logic) if off_cause_logic else "",
                "warnings": device_warnings,
            }
        )

        if depth >= max_depth:
            if rows_for_device:
                truncated = True
                truncated_reasons.add("max_depth")
            continue

        for row in rows_for_device:
            output_occs = [occ for occ in row_write_occurrences(row) if occ.device == device]
            output_roles = [occ.role for occ in output_occs]
            row_zones = active_zones(mc_zones, row.lddb, row.pos)
            row_jumps = jumps_before(jump_index, row.lddb, row.pos)
            enable_logic: dict[str, Any] | None = None
            enable_logic_text = ""
            enable_logic_stats: dict[str, int] = {}
            if strict_logic:
                outputs = trace_output_elements_for(row, device, labels)
                enable_logic = apply_zone_conditions(
                    or_logic([enable_logic_for_output(row, output, labels) for output in outputs]),
                    row_zones,
                )
                enable_logic_text = logic_to_text(enable_logic)
                enable_logic_stats = logic_stats(enable_logic)
                condition_records = [
                    logic_condition_record(ref, comments, drivers, device, row, refresh_areas, unit_io_areas)
                    for ref in refs_for_logic(enable_logic)
                ]
            else:
                conditions = row_conditions(row)
                condition_records = [
                    condition_record(occ, comments, drivers, device, refresh_areas, unit_io_areas) for occ in conditions
                ]
            row_id = row_key(row)
            row_rec = {
                "row_id": row_id,
                "device": device,
                "depth": depth,
                "lddb": row.lddb,
                "pos": row.pos,
                "block_id": row.block_id,
                "title": row.title,
                "parse_status": row.parse_status,
                "rowsize": row.rowsize,
                "dim": row.dim,
                "driver_roles": output_roles,
                "driver_effects": [driver_effect(role) for role in output_roles],
                "conditions": condition_records,
                "strict_logic": strict_logic,
                "enable_logic": enable_logic,
                "enable_logic_text": enable_logic_text,
                "logic_stats": enable_logic_stats,
                "mc_zones": [zone.summary() for zone in row_zones],
                "cj_upstream": [site.summary() for site in row_jumps],
                "execution_guards": execution_guards(row_jumps),
                "temporal_predicates": temporal_predicates(output_occs, condition_records, enable_logic),
                "same_row_outputs": [simple_occ_record(occ, comments) for occ in row_driver_occurrences(row)],
                "instruction_refs": [simple_occ_record(occ, comments) for occ in row_instruction_refs(row)],
            }
            driver_rows.append(row_rec)

            if strict_logic:
                edge_conditions = condition_records
            else:
                edge_conditions = [
                    condition_record(occ, comments, drivers, device, refresh_areas, unit_io_areas) for occ in row_conditions(row)
                ]

            for cond in edge_conditions:
                edge = {
                    "from_device": device,
                    "condition_device": cond["device"],
                    "row_id": row_id,
                    "role": cond["role"],
                    "required_state": cond["required_state"],
                    "comment": cond.get("comment", ""),
                    "classes": cond.get("classes", []),
                    "has_driver": bool(drivers.get(cond["device"])),
                    "self_reference": cond["device"] == device,
                    "cycle": cond["device"] in visited,
                    "predicate": cond.get("predicate", ""),
                    "source_kind": cond.get("source_kind", ""),
                    "semantic_group": cond.get("semantic_group", ""),
                    "trace_boundary": cond.get("trace_boundary", False),
                    "stop_reason": cond.get("stop_reason", ""),
                    "refresh_area": cond.get("refresh_area", ""),
                    "source_unit_kind": cond.get("source_unit_kind", ""),
                    "source_unit_name": cond.get("source_unit_name", ""),
                    "source_unit_connection": cond.get("source_unit_connection", ""),
                    "source_unit_slot_number": cond.get("source_unit_slot_number", ""),
                    "source_unit_area": cond.get("source_unit_area", ""),
                }
                edges.append(edge)
                if not edge["has_driver"] or edge["self_reference"] or edge["cycle"]:
                    continue
                queue.append((cond["device"], depth + 1, device))

    edge_counter = Counter(edge["condition_device"] for edge in edges)
    partial_driver_rows = [row for row in driver_rows if row.get("parse_status") != "exact"]
    absent = not counts.get(target)
    capped_driver_rows = [
        row
        for row in driver_rows
        if row.get("parse_status") == "exact"
        and int((row.get("logic_stats") or {}).get("too_large", 0)) > 0
    ]
    unresolved_labels = relevant_unresolved_labels(driver_rows)
    label_gaps = label_resolution_gaps(labels, unresolved_labels)
    if target in labels.ambiguous_names:
        analysis_state = AnalysisState(
            NOT_EVALUATED,
            reason=f"{target} names labels in multiple scopes",
            next_step="use a scoped label name: " + ", ".join(labels.ambiguous_names[target]),
            stage=DISCOVERY,
        )
    elif absent and not label_gaps:
        analysis_state = AnalysisState(
            NOT_EVALUATED,
            reason=f"{target} does not appear anywhere in this project",
            next_step=(
                "check the device name and the project; "
                f"gx3-cli xref where-used {target} lists occurrences"
            ),
            stage=DISCOVERY,
        )
    else:
        analysis_state = trace_state(
            truncated,
            sorted(truncated_reasons),
            partial_driver_rows,
            capped_driver_rows,
            semantic_gaps(driver_rows),
            label_gaps=label_gaps,
        )
    analysis = analysis_state.as_dict()
    structural_shape = "partial" if partial_driver_rows else "exact"
    inputs.validate_source()

    return {
        "target": {
            "device": target,
            "comment": device_comment(target, comments),
        },
        "source_root": str(root),
        "input_sha256": inputs.source_version[0],
        "communication_inputs": inputs.communication_version[0] if inputs.communication_version is not None else {},
        "communication_input_source": (
            "validated_index" if inputs.external_boundaries is not None and inputs.external_boundaries[0] is not None
            else "legacy_fallback"
        ),
        "max_depth": max_depth,
        "max_devices": max_devices,
        "include_reset": include_reset,
        "strict_logic": strict_logic,
        "truncated": truncated,
        "truncated_reasons": sorted(truncated_reasons),
        "analysis": analysis,
        "label_resolution": {
            "status": labels.status,
            "reason": labels.reason,
            "unresolved_tokens": unresolved_labels,
            "unresolved_count": len(unresolved_labels),
        },
        "verification": {
            "structural_shape": structural_shape,
            "semantic_scope_state": analysis.get("state", ""),
            "independent_gx_works3_validation": False,
            "note": (
                "structural_shape=exact means operation/element shape matched; it is not "
                "proof that label identity, execution semantics, timing, state or control flow "
                "was independently validated against GX Works3"
            ),
        },
        "stats": {
            "devices_traced": len(devices),
            "driver_rows": len(driver_rows),
            "partial_driver_rows": len(partial_driver_rows),
            "capped_driver_rows": len(capped_driver_rows),
            "dependency_edges": len(edges),
            "terminal_conditions": sum(1 for edge in edges if not edge["has_driver"]),
            "self_references": sum(1 for edge in edges if edge["self_reference"]),
        },
        "parse_warnings": [
            {
                "row_id": row["row_id"],
                "device": row["device"],
                "lddb": row["lddb"],
                "pos": row["pos"],
                "parse_status": row.get("parse_status", ""),
                "title": row.get("title", ""),
            }
            for row in partial_driver_rows[:30]
        ],
        "devices": devices,
        "driver_rows": driver_rows,
        "edges": edges,
        "top_condition_devices": [
            {"device": device, "count": count, "comment": device_comment(device, comments)}
            for device, count in edge_counter.most_common(30)
        ],
    }


def load_cross_link_index(project: str, link_db: Path) -> dict[str, list[dict[str, str]]]:
    if not link_db.exists():
        return {}
    con = sqlite3.connect(link_db)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        select device_a as device, project_b as other_project, device_b as other_device,
               link_type, direction, confidence, role
        from link_map
        where project_a=?
        union all
        select device_b as device, project_a as other_project, device_a as other_device,
               link_type, direction, confidence, role
        from link_map
        where project_b=?
        order by device, confidence desc, other_project, other_device
        """,
        (project, project),
    ).fetchall()
    con.close()
    out: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        out[str(row["device"])].append(
            {
                "other_project": str(row["other_project"]),
                "other_device": str(row["other_device"]),
                "link_type": str(row["link_type"]),
                "direction": str(row["direction"]),
                "confidence": str(row["confidence"]),
                "role": str(row["role"] or ""),
            }
        )
    return out


def attach_cross_links(trace: dict[str, Any], project: str, link_db: Path) -> None:
    links = load_cross_link_index(project, link_db)
    if not links:
        return
    trace["project"] = project
    trace["link_db"] = str(link_db)
    for dev in trace.get("devices", []):
        device = str(dev.get("device", ""))
        if device in links:
            dev["cross_links"] = links[device]
    for edge in trace.get("edges", []):
        device = str(edge.get("condition_device", ""))
        if device in links:
            edge["cross_links"] = links[device]
    for row in trace.get("driver_rows", []):
        for cond in row.get("conditions", []):
            device = str(cond.get("device", ""))
            if device in links:
                cond["cross_links"] = links[device]


def format_condition(cond: dict[str, Any]) -> str:
    comment = f" {cond['comment']}" if cond.get("comment") else ""
    predicate = f" {cond['predicate']}" if cond.get("predicate") else ""
    flags: list[str] = []
    if cond.get("self_reference"):
        flags.append("self")
    if cond.get("cycle"):
        flags.append("cycle")
    if cond.get("has_driver"):
        flags.append("has-driver")
    if cond.get("trace_boundary"):
        source = str(cond.get("source_kind", "terminal"))
        semantic = str(cond.get("semantic_group", "external_unknown"))
        flags.append(f"boundary:{source}/{semantic}")
    if cond.get("source_unit_kind"):
        unit = str(cond.get("source_unit_name", "unit"))
        slot = str(cond.get("source_unit_slot_number", ""))
        area = str(cond.get("source_unit_area", ""))
        slot_text = f" slot={slot}" if slot else ""
        area_text = f" {area}" if area else ""
        flags.append(f"unit:{unit}{slot_text}{area_text}")
    if cond.get("cross_links"):
        links = ", ".join(
            f"{link['other_project']}:{link['other_device']}"
            for link in cond.get("cross_links", [])[:3]
        )
        more = len(cond.get("cross_links", [])) - 3
        flags.append(f"cross:{links}" + (f"+{more}" if more > 0 else ""))
    suffix = f" [{', '.join(flags)}]" if flags else ""
    return f"{cond['role']}:{cond['device']}={cond['required_state']}{predicate}{comment}{suffix}"


def format_cross_links(links: list[dict[str, str]], limit: int = 3) -> str:
    if not links:
        return ""
    shown = ", ".join(f"{link['other_project']}:{link['other_device']}" for link in links[:limit])
    more = len(links) - limit
    return shown + (f" +{more}" if more > 0 else "")


def format_execution_zone(zone: dict[str, Any], indent: str = "") -> str:
    end = zone.get("end_pos")
    if zone.get("kind") == "call_invocation":
        pointer = zone.get("pointer")
        return (
            f"{indent}call_invocation: P{pointer} "
            f"pos {zone.get('start_pos')}..{end if end is not None else 'END'} "
            f"condition={zone.get('condition_text')}"
        )
    return (
        f"{indent}mc_zone: N{zone.get('nesting')} relay={zone.get('relay') or '?'} "
        f"pos {zone.get('start_pos')}..{end if end is not None else 'END'} "
        f"condition={zone.get('condition_text')}"
    )


def format_execution_guard(site: dict[str, Any], indent: str = "") -> str:
    if site.get("opcode") == "CALL_CONTEXT":
        reason = site.get("reason") or site.get("condition_text") or "unresolved call context"
        return f"{indent}call_context: {reason} (row execution not fully resolved)"
    return (
        f"{indent}cj_upstream: {site.get('opcode')}@{site.get('pos')} "
        f"condition={site.get('condition_text')} "
        "(jump target not resolved; row execution not guaranteed)"
    )


def format_text(trace: dict[str, Any]) -> str:
    lines: list[str] = []
    target = trace["target"]
    lines.append(f"Target: {target['device']} {target.get('comment', '')}".rstrip())
    lines.append(f"Source: {trace['source_root']}")
    lines.append(
        "Stats: "
        f"devices={trace['stats']['devices_traced']}, "
        f"driver_rows={trace['stats']['driver_rows']}, "
        f"partial_driver_rows={trace['stats'].get('partial_driver_rows', 0)}, "
        f"capped_driver_rows={trace['stats'].get('capped_driver_rows', 0)}, "
        f"edges={trace['stats']['dependency_edges']}, "
        f"terminal={trace['stats']['terminal_conditions']}, "
        f"self_refs={trace['stats']['self_references']}, "
        f"truncated={trace['truncated']}, "
        f"truncated_reasons={','.join(trace.get('truncated_reasons', [])) or 'none'}, "
        f"strict_logic={trace.get('strict_logic', False)}"
    )
    analysis = trace["analysis"]
    if analysis["state"] != "checked":
        lines.append("")
        lines.append(f"Result: {analysis['label']} -- {analysis.get('reason', '')}")
        if analysis.get("next_step"):
            lines.append(f"  next: {analysis['next_step']}")
        lines.append("")
    lines.append("Legend: a=ON required, b=OFF required, self=self-hold/self-reference, has-driver=upstream driver exists")
    if trace.get("strict_logic"):
        lines.append("Note: enable_logic is the topology-derived condition from the left rail to the target output.")
    else:
        lines.append("Note: conditions is a flat device list. Use --strict-logic for topology-derived AND/OR and execution-zone folding.")
    if trace["stats"].get("partial_driver_rows", 0):
        lines.append(
            "Warning: trace includes partial-parse driver rows; conditions/instruction refs may be incomplete. "
            "Run parse-gaps for row-level reasons."
        )
    lines.append("")

    by_device: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trace["driver_rows"]:
        by_device[row["device"]].append(row)

    for device_rec in trace["devices"]:
        device = device_rec["device"]
        indent = "  " * int(device_rec["depth"])
        comment = f" {device_rec['comment']}" if device_rec.get("comment") else ""
        terminal = " terminal" if device_rec.get("terminal") else ""
        lines.append(
            f"{indent}- D{device_rec['depth']} {device}{comment}"
            f" drivers={device_rec['driver_row_count']}{terminal}"
        )
        if device_rec.get("cross_links"):
            lines.append(f"{indent}  cross_links: {format_cross_links(device_rec['cross_links'])}")
        if device_rec.get("on_cause_logic_text"):
            lines.append(f"{indent}  ON_CAUSE: {device_rec['on_cause_logic_text']}")
        if device_rec.get("off_cause_logic_text") and device_rec.get("off_cause_logic_text") != "FALSE":
            lines.append(f"{indent}  OFF_CAUSE: {device_rec['off_cause_logic_text']}")
        for warning in device_rec.get("warnings", []):
            lines.append(f"{indent}  warning: {warning}")
        for row in by_device.get(device, []):
            effects = ", ".join(row["driver_effects"])
            parse = "" if row.get("parse_status") == "exact" else f" [parse={row.get('parse_status')}]"
            lines.append(f"{indent}  row {row['lddb']}:{row['pos']} {row['title']}{parse}")
            lines.append(f"{indent}    effect: {effects}; roles={','.join(row['driver_roles'])}")
            if row.get("enable_logic_text"):
                lines.append(f"{indent}    enable_logic: {row['enable_logic_text']}")
            for predicate in row.get("temporal_predicates", []):
                lines.append(
                    f"{indent}    temporal: {predicate.get('kind')} "
                    f"device={predicate.get('device', '')} runtime_state_required"
                )
            for zone in row.get("mc_zones", []):
                lines.append(format_execution_zone(zone, f"{indent}    "))
            for site in row.get("cj_upstream", []):
                lines.append(format_execution_guard(site, f"{indent}    "))
            if row["conditions"]:
                lines.append(f"{indent}    conditions:")
                for cond in row["conditions"]:
                    lines.append(f"{indent}      - {format_condition(cond)}")
            if row["same_row_outputs"]:
                outputs = "; ".join(
                    f"{out['role']}:{out['device']} {out.get('comment', '')}".rstrip()
                    for out in row["same_row_outputs"]
                )
                lines.append(f"{indent}    same_row_outputs: {outputs}")
            if row["instruction_refs"]:
                refs = "; ".join(
                    f"{ref['role']}:{ref['device']} {ref.get('comment', '')}".rstrip()
                    for ref in row["instruction_refs"]
                )
                lines.append(f"{indent}    instruction_refs: {refs}")
        lines.append("")

    if trace["top_condition_devices"]:
        lines.append("Top condition devices:")
        for rec in trace["top_condition_devices"][:15]:
            comment = f" {rec['comment']}" if rec.get("comment") else ""
            lines.append(f"  - {rec['device']} count={rec['count']}{comment}")
    return "\n".join(lines)


def compact_condition_key(cond: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(cond.get("device") or cond.get("condition_device", "")),
        str(cond.get("required_state", "")),
        str(cond.get("predicate", "")),
    )


def format_compact_condition(cond: dict[str, Any]) -> str:
    device = cond.get("device") or cond.get("condition_device", "")
    state = cond.get("required_state", "")
    predicate = f" {cond['predicate']}" if cond.get("predicate") else ""
    comment = f" {display_text(cond['comment'])}" if cond.get("comment") else ""
    boundary = ""
    if cond.get("trace_boundary"):
        source = cond.get("source_kind", "boundary")
        semantic = cond.get("semantic_group", "unknown")
        boundary = f" [{source}/{semantic}]"
    elif not cond.get("has_driver"):
        boundary = " [terminal]"
    cross = ""
    if cond.get("cross_links"):
        links = ", ".join(
            f"{link['other_project']}:{link['other_device']}"
            for link in cond.get("cross_links", [])[:3]
        )
        more = len(cond.get("cross_links", [])) - 3
        cross = f" => {links}" + (f" +{more}" if more > 0 else "")
    return f"{device}={state}{predicate}{comment}{boundary}{cross}".rstrip()


def compact_labels(ja: bool) -> dict[str, str]:
    if ja:
        return {
            "target": "対象",
            "source": "解析元",
            "stats": "統計",
            "truncated": "打ち切り理由",
            "on_logic": "ON条件",
            "off_logic": "OFF/リセット条件",
            "active_rows": "有効な駆動行",
            "driver_rows": "駆動行",
            "disabled_target": "対象デバイスの無効/常時FALSE行",
            "disabled_all": "展開中に見つかった無効/常時FALSE枝",
            "disabled_devices": "ON条件が常時FALSEの条件デバイス",
            "boundaries": "外部/HMI/通信/終端条件",
            "internal": "次に掘る内部条件",
            "top": "頻出条件デバイス",
            "more_active": "件の有効行を省略",
            "more_rows": "件の行を省略",
            "more_disabled": "件の無効行を省略",
            "more_boundaries": "件の境界条件を省略",
            "more_internal": "件の内部条件を省略",
        }
    return {
        "target": "Target",
        "source": "Source",
        "stats": "Stats",
        "truncated": "Truncated reasons",
        "on_logic": "ON logic",
        "off_logic": "OFF/reset logic",
        "active_rows": "Active driver rows",
        "driver_rows": "Driver rows",
        "disabled_target": "Disabled / constant-FALSE target driver rows",
        "disabled_all": "Disabled / constant-FALSE branches found while tracing",
        "disabled_devices": "Condition devices whose ON cause is constant FALSE",
        "boundaries": "External/HMI/communication/terminal boundaries",
        "internal": "Upstream internal conditions to inspect next",
        "top": "Top repeated condition devices",
        "more_active": "more active rows omitted",
        "more_rows": "more rows omitted",
        "more_disabled": "more disabled rows omitted",
        "more_boundaries": "more boundaries omitted",
        "more_internal": "more internal conditions omitted",
    }


def format_row_summary(row: dict[str, Any]) -> str:
    effects = ",".join(row.get("driver_roles", []))
    title = display_text(row.get("title", ""))
    parse = "" if row.get("parse_status") == "exact" else f" parse={row.get('parse_status')}"
    return f"{row['lddb']}:{row['pos']} roles={effects}{parse} {title}".rstrip()


def state_lines(trace: dict[str, Any], ja: bool = False) -> list[str]:
    """Say what the trace is worth, before the conditions are read."""
    analysis = trace.get("analysis") or {}
    if not analysis or analysis.get("state") == "checked":
        return []
    head = "結果" if ja else "Result"
    nxt = "次の手順" if ja else "next"
    label = analysis.get("label_ja" if ja else "label", "") or analysis.get("label", "")
    stage = analysis.get("stage_label_ja" if ja else "stage_label", "")
    if stage:
        label = f"{label}（{stage}）" if ja else f"{label} [{stage}]"
    out = ["", f"{head}: {label} -- {analysis.get('reason', '')}".rstrip(" -")]
    if analysis.get("next_step"):
        out.append(f"  {nxt}: {analysis['next_step']}")
    also = (analysis.get("detail") or {}).get("also") or []
    if also:
        out.append("  " + ("ほかにも:" if ja else "also:"))
        for item in also:
            out.append(f"    - [{item.get('stage', '')}] {item.get('reason', '')}")
    out.append("")
    return out


def format_compact(trace: dict[str, Any], row_limit: int = 8, condition_limit: int = 30, ja: bool = False) -> str:
    label = compact_labels(ja)
    lines: list[str] = []
    target = trace["target"]
    devices = trace.get("devices", [])
    target_device = target["device"]
    target_rec = next((dev for dev in devices if dev.get("device") == target_device), devices[0] if devices else {})

    lines.append(f"{label['target']}: {target_device} {display_text(target.get('comment', ''))}".rstrip())
    if target_rec.get("cross_links"):
        lines.append(f"Cross-links: {format_cross_links(target_rec['cross_links'])}")
    lines.append(f"{label['source']}: {trace['source_root']}")
    lines.append(
        f"{label['stats']}: "
        f"devices={trace['stats']['devices_traced']}, "
        f"driver_rows={trace['stats']['driver_rows']}, "
        f"partial_driver_rows={trace['stats'].get('partial_driver_rows', 0)}, "
        f"capped_driver_rows={trace['stats'].get('capped_driver_rows', 0)}, "
        f"edges={trace['stats']['dependency_edges']}, "
        f"truncated={trace['truncated']}"
    )
    if trace.get("truncated_reasons"):
        lines.append(f"{label['truncated']}: {', '.join(trace['truncated_reasons'])}")
    lines.extend(state_lines(trace, ja))
    if trace["stats"].get("partial_driver_rows", 0):
        if ja:
            lines.append("Parse warning: 駆動行の一部が構造として読み切れていません（条件/命令参照が不足する可能性）。"
                         "なお構造が揃っていること自体は、意味が正しいことの証明ではありません。")
        else:
            lines.append("Parse warning: some driver rows did not decode into the expected shape, so "
                         "conditions or instruction refs may be missing. A matching shape is "
                         "not proof that the meaning is right.")
    lines.append("")

    for warning in target_rec.get("warnings", []):
        if ja:
            lines.append(f"警告: {warning}")
        else:
            lines.append(f"Warning: {warning}")

    on_logic = target_rec.get("on_cause_logic_text", "")
    off_logic = target_rec.get("off_cause_logic_text", "")
    if on_logic:
        lines.append(f"{label['on_logic']}:")
        lines.append(f"  {on_logic}")
        lines.append("")
    if off_logic and off_logic != "FALSE":
        lines.append(f"{label['off_logic']}:")
        lines.append(f"  {off_logic}")
        lines.append("")

    target_rows = [row for row in trace.get("driver_rows", []) if row.get("device") == target_device]
    active_rows = [row for row in target_rows if row.get("enable_logic_text") and row.get("enable_logic_text") != "FALSE"]
    disabled_rows = [row for row in target_rows if row.get("enable_logic_text") == "FALSE"]
    fallback_rows = [row for row in target_rows if not row.get("enable_logic_text")]
    all_disabled_rows = [
        row
        for row in trace.get("driver_rows", [])
        if row.get("enable_logic_text") == "FALSE" and row.get("device") != target_device
    ]

    if active_rows:
        lines.append(f"{label['active_rows']}:")
        for row in active_rows[:row_limit]:
            lines.append(f"  - {format_row_summary(row)}")
            row_logic = row.get("enable_logic_text")
            if row_logic and on_logic and row_logic == on_logic:
                same_msg = "(= 上記ON条件と同一)" if ja else "(= same as ON logic above)"
                lines.append(f"    logic: {same_msg}")
            else:
                lines.append(f"    logic: {row_logic}")
            for predicate in row.get("temporal_predicates", []):
                lines.append(
                    f"    temporal: {predicate.get('kind')} device={predicate.get('device', '')}"
                )
            for zone in row.get("mc_zones", []):
                if zone.get("kind") == "call_invocation":
                    call_label = "CALL呼出条件" if ja else "CALL invocation"
                    lines.append(
                        f"    {call_label}: P{zone.get('pointer')} condition={zone.get('condition_text')}"
                    )
                else:
                    label_mc = "MCゾーン内" if ja else "inside MC zone"
                    lines.append(
                        f"    {label_mc}: N{zone.get('nesting')} relay={zone.get('relay') or '?'} "
                        f"condition={zone.get('condition_text')}"
                    )
            for site in row.get("cj_upstream", []):
                if site.get("opcode") == "CALL_CONTEXT":
                    call_gap = "CALL呼出文脈未解決" if ja else "CALL invocation unresolved"
                    reason = site.get("reason") or site.get("condition_text") or ""
                    lines.append(f"    {call_gap}: {reason}")
                else:
                    label_cj = "上流に条件ジャンプあり(実行保証なし)" if ja else "conditional jump upstream (execution not guaranteed)"
                    lines.append(f"    {label_cj}: {site.get('opcode')}@{site.get('pos')}")
        if len(active_rows) > row_limit:
            lines.append(f"  ... {len(active_rows) - row_limit} {label['more_active']}")
        lines.append("")
    elif fallback_rows:
        lines.append(f"{label['driver_rows']}:")
        for row in fallback_rows[:row_limit]:
            lines.append(f"  - {format_row_summary(row)}")
            for site in row.get("cj_upstream", []):
                if site.get("opcode") == "CALL_CONTEXT":
                    reason = site.get("reason") or site.get("condition_text") or ""
                    lines.append(f"    CALL invocation unresolved: {reason}")
        if len(fallback_rows) > row_limit:
            lines.append(f"  ... {len(fallback_rows) - row_limit} {label['more_rows']}")
        lines.append("")

    if disabled_rows:
        lines.append(f"{label['disabled_target']}:")
        for row in disabled_rows[:row_limit]:
            lines.append(f"  - {format_row_summary(row)}")
            lines.append("    logic: FALSE")
        if len(disabled_rows) > row_limit:
            lines.append(f"  ... {len(disabled_rows) - row_limit} {label['more_disabled']}")
        lines.append("")

    if all_disabled_rows:
        lines.append(f"{label['disabled_all']}:")
        for row in all_disabled_rows[:row_limit]:
            lines.append(f"  - {row.get('device')} {format_row_summary(row)}")
            lines.append("    logic: FALSE")
        if len(all_disabled_rows) > row_limit:
            lines.append(f"  ... {len(all_disabled_rows) - row_limit} {label['more_disabled']}")
        lines.append("")

    disabled_devices = [
        dev
        for dev in devices
        if dev.get("device") != target_device
        and dev.get("on_cause_logic_text") == "FALSE"
        and dev.get("driver_row_count", 0) > 0
    ]
    if disabled_devices:
        lines.append(f"{label['disabled_devices']}:")
        for dev in disabled_devices[:condition_limit]:
            lines.append(f"  - {dev.get('device')} {display_text(dev.get('comment', ''))}".rstrip())
            lines.append("    ON_CAUSE: FALSE")
        if len(disabled_devices) > condition_limit:
            lines.append(f"  ... {len(disabled_devices) - condition_limit} {label['more_disabled']}")
        lines.append("")

    seen: set[tuple[str, str, str]] = set()
    boundary_conditions: list[dict[str, Any]] = []
    internal_conditions: list[dict[str, Any]] = []
    for edge in trace.get("edges", []):
        key = compact_condition_key(edge)
        if key in seen:
            continue
        seen.add(key)
        if edge.get("trace_boundary") or not edge.get("has_driver"):
            boundary_conditions.append(edge)
        elif not edge.get("self_reference"):
            internal_conditions.append(edge)

    if boundary_conditions:
        lines.append(f"{label['boundaries']}:")
        for cond in boundary_conditions[:condition_limit]:
            lines.append(f"  - {format_compact_condition(cond)}")
        if len(boundary_conditions) > condition_limit:
            lines.append(f"  ... {len(boundary_conditions) - condition_limit} {label['more_boundaries']}")
        lines.append("")

    if internal_conditions:
        lines.append(f"{label['internal']}:")
        for cond in internal_conditions[:condition_limit]:
            lines.append(f"  - {format_compact_condition(cond)}")
        if len(internal_conditions) > condition_limit:
            lines.append(f"  ... {len(internal_conditions) - condition_limit} {label['more_internal']}")
        lines.append("")

    if trace.get("top_condition_devices"):
        lines.append(f"{label['top']}:")
        for rec in trace["top_condition_devices"][:10]:
            comment = f" {display_text(rec['comment'])}" if rec.get("comment") else ""
            lines.append(f"  - {rec['device']} count={rec['count']}{comment}")

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Trace ladder dependencies from one device.")
    parser.add_argument("device", help="target device or resolved symbolic label name")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--max-depth", type=int, default=4, help="maximum upstream device depth")
    parser.add_argument("--max-devices", type=int, default=300, help="maximum devices to trace before truncating")
    parser.add_argument(
        "--exclude-reset",
        action="store_true",
        help="ignore RST rows when building driver index",
    )
    parser.add_argument(
        "--strict-logic",
        action="store_true",
        help="derive conditions from exact ladder topology instead of a flat row contact list",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="print a compact evidence-focused summary instead of the full dependency tree",
    )
    parser.add_argument("--ja", action="store_true", help="use Japanese headings for compact text output")
    parser.add_argument("--compact-row-limit", type=int, default=8, help="driver rows shown in compact output")
    parser.add_argument("--compact-condition-limit", type=int, default=30, help="conditions shown in compact output")
    parser.add_argument("--project", default=None, help="current project label for link-map annotations")
    parser.add_argument("--link-db", default=".gx3_index/link_map.sqlite", help="link-map sqlite path for boundary hints")
    parser.add_argument("--no-link-map", action="store_true", help="do not annotate boundary devices with link-map targets")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("-o", "--output", help="write output to file")
    return parser


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args()
    trace = build_trace(
        root=Path(args.root),
        target_device=args.device,
        max_depth=args.max_depth,
        max_devices=args.max_devices,
        include_reset=not args.exclude_reset,
        strict_logic=args.strict_logic,
    )
    if not args.no_link_map:
        project = args.project or project_label_from_root(Path(args.root))
        attach_cross_links(trace, project, Path(args.link_db))
    if args.format == "json":
        output = json.dumps(trace, ensure_ascii=False, indent=2)
    elif args.compact:
        output = format_compact(
            trace,
            row_limit=args.compact_row_limit,
            condition_limit=args.compact_condition_limit,
            ja=args.ja,
        )
    else:
        output = format_text(trace)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
