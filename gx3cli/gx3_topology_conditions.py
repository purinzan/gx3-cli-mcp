from __future__ import annotations

"""Constant-aware simplification for dependency tracing.

This module is the bridge between project-wide constant propagation and the
normal `trace-device` workflow. It does not decide PLC runtime state. It only
uses constants that the conservative dead-logic analysis could prove from the
saved project, then simplifies a rung's Boolean enable expression before the
trace queue expands upstream devices.

That gives the useful short-circuit behaviour a maintainer expects:

- FALSE AND X -> FALSE, so X does not need to be traced for this ON path.
- TRUE OR X -> TRUE, so X does not need to be traced for this ON path.
- FALSE OR X -> X, so only X remains in the trace.
- A/B contacts invert a proven coil state in the usual way.

If required xref/index-lite evidence is unavailable or stale, tracing simply
falls back to its historical behaviour with no constant pruning.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gx3cli.gx3_dead_logic import ConstantFact, ConstantProofUnavailable, lite_db_path, load_external_devices, propagate_constant_devices
from gx3cli.gx3_analysis_state import AnalysisState
from gx3cli.gx3_ladder_logic import (
    and_logic,
    condition_refs_from_logic,
    logic_false,
    logic_to_text,
    logic_true,
    or_logic,
)
from gx3cli.gx3_xref import default_db_path, open_xref_db
from gx3cli.review_gx3_project import LadderRow


@dataclass(frozen=True)
class TraceLogicPruning:
    logic: dict[str, Any]
    constant_value: bool | None
    constant_contacts: tuple[dict[str, Any], ...] = ()
    pruned_conditions: tuple[dict[str, Any], ...] = ()

    @property
    def logic_text(self) -> str:
        return logic_to_text(self.logic)


@dataclass(frozen=True)
class TraceConstantContext:
    facts: dict[str, ConstantFact]
    enabled: bool
    reason: str = ""
    analysis: AnalysisState | None = None

    def summary(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "proven_constants": len(self.facts),
            "reason": self.reason,
            "analysis": self.analysis.as_dict() if self.analysis is not None else None,
        }


def _load_external_boundaries(root: Path) -> tuple[dict[str, str] | None, str]:
    """Read external/HMI/communication ownership from a validated lite index.

    Constant pruning is destructive to the search graph, so an absent, stale,
    old-format, or malformed lite index must not be treated as "zero external
    writers". In those cases the caller disables pruning and keeps the normal
    trace instead.

    Use the same validated reader as dead-logic. It closes the handle on every
    path, and a failed table read cannot become an empty boundary set.
    """
    path = lite_db_path(root)
    if not path.exists():
        return None, f"index-lite database not found: {path}"

    try:
        return load_external_devices(path, root), ""
    except (Exception, SystemExit) as exc:
        return None, f"index-lite unavailable for constant pruning: {exc}"


def load_trace_constant_context(
    root: Path,
    rows: list[LadderRow],
    refresh_areas: list,
) -> TraceConstantContext:
    """Load only constants that project evidence can prove safely.

    The existing trace command does not require xref/index-lite databases.
    Constant-aware pruning therefore remains optional: missing, stale, or
    malformed prerequisites must never make tracing fail or silently turn an
    external/HMI/network-written value into a project constant.
    """
    externals, boundary_reason = _load_external_boundaries(root)
    if externals is None:
        return TraceConstantContext({}, False, boundary_reason)

    xref_path = default_db_path(root)
    if not xref_path.exists():
        return TraceConstantContext({}, False, f"xref database not found: {xref_path}")

    try:
        con = open_xref_db(xref_path, read_only=True, root=root, snapshot=True)
    except (Exception, SystemExit) as exc:
        return TraceConstantContext({}, False, f"xref unavailable for constant pruning: {exc}")

    try:
        facts, _findings = propagate_constant_devices(
            rows,
            con,
            externals=externals,
            refresh_areas=refresh_areas,
            root=root,
        )
    except ConstantProofUnavailable as exc:
        return TraceConstantContext({}, False, str(exc), exc.analysis)
    except Exception as exc:
        return TraceConstantContext({}, False, f"constant propagation unavailable: {exc}")
    finally:
        con.close()

    return TraceConstantContext(facts, True)


def _contact_key(ref: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(ref.get("device") or ref.get("raw_device") or ""),
        str(ref.get("role") or ""),
        str(ref.get("position") or ""),
    )


def _simplify(
    node: dict[str, Any],
    facts: dict[str, ConstantFact],
) -> tuple[dict[str, Any], bool | None, list[dict[str, Any]]]:
    op = node.get("op")
    if op == "true":
        return logic_true(), True, []
    if op == "false":
        return dict(node), False, []
    if op == "contact":
        device = str(node.get("raw_device") or node.get("device") or "")
        fact = facts.get(device)
        if fact is None:
            return dict(node), None, []
        role = str(node.get("role") or "a")
        value = fact.value if role == "a" else not fact.value
        evidence = {
            "device": device,
            "role": role,
            "device_state": fact.state,
            "contact_value": "TRUE" if value else "FALSE",
            "position": str(node.get("position") or ""),
            "where_proven": fact.where,
            "chain": list(fact.chain),
        }
        if value:
            return logic_true(), True, [evidence]
        return logic_false("propagated_constant_contact", device), False, [evidence]
    if op == "and":
        kept: list[dict[str, Any]] = []
        evidence: list[dict[str, Any]] = []
        unknown = False
        for child in node.get("args", []):
            simplified, value, child_evidence = _simplify(child, facts)
            evidence.extend(child_evidence)
            if value is False:
                return logic_false("constant_and_short_circuit"), False, evidence
            if value is True:
                continue
            unknown = True
            kept.append(simplified)
        if not unknown:
            return logic_true(), True, evidence
        return and_logic(kept), None, evidence
    if op == "or":
        kept = []
        evidence: list[dict[str, Any]] = []
        unknown = False
        for child in node.get("args", []):
            simplified, value, child_evidence = _simplify(child, facts)
            evidence.extend(child_evidence)
            if value is True:
                return logic_true(), True, evidence
            if value is False:
                continue
            unknown = True
            kept.append(simplified)
        if not unknown:
            return logic_false("constant_or_exhausted"), False, evidence
        return or_logic(kept), None, evidence

    # predicate / too_large / unknown / future nodes keep their original form.
    # In particular timers, counters and unresolved semantics are never folded
    # just because a nearby Boolean device happens to be constant.
    return dict(node), None, []


def simplify_logic_for_trace(
    node: dict[str, Any],
    facts: dict[str, ConstantFact],
) -> TraceLogicPruning:
    """Substitute proven constants and return only dependencies still relevant.

    `pruned_conditions` is evidence, not an instruction to delete ladder logic.
    It records contacts that were present in the original expression but no
    longer need upstream tracing for this particular ON-condition question.
    """
    original_refs = condition_refs_from_logic(node)
    simplified, value, evidence = _simplify(node, facts)
    remaining_refs = condition_refs_from_logic(simplified)
    remaining = {_contact_key(ref) for ref in remaining_refs}
    pruned = [ref for ref in original_refs if _contact_key(ref) not in remaining]
    return TraceLogicPruning(
        logic=simplified,
        constant_value=value,
        constant_contacts=tuple(evidence),
        pruned_conditions=tuple(pruned),
    )
