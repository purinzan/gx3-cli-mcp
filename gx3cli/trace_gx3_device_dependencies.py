from __future__ import annotations

"""Constant-aware public trace-device facade.

The original trace engine lives in ``gx3_trace_state``.  This module keeps the
historical import/CLI path while adding one extra pass for the maintenance
question "what can make this coil turn on?": project constants proven by
``dead-logic`` are substituted into strict Boolean enable expressions, branches
that collapse to FALSE/TRUE are removed, and only still-relevant upstream
conditions remain in the returned trace.

The public trace path also installs a context-local condition-ref dispatcher
around the canonical engine.  Strict enable logic is simplified before the BFS
adds upstream devices to its queue, so branches proven irrelevant are never
expanded.  A post-pass still rewrites the returned Boolean expressions and
attaches the constant evidence; the ladder decoder itself is not forked.
"""

import json
import sys
from collections import Counter, deque
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from gx3cli import gx3_trace_state as base
from gx3cli.gx3_ladder_logic import condition_refs_from_logic, logic_to_text
from gx3cli.gx3_topology_conditions import load_trace_constant_context, simplify_logic_for_trace

# Keep the established module API for callers that import helpers or constants
# directly. Several sibling analyzers intentionally share these definitions.
DEVICE_RE = base.DEVICE_RE
CONTACT_ROLES = base.CONTACT_ROLES
ON_DRIVER_ROLES = base.ON_DRIVER_ROLES
OFF_DRIVER_ROLES = base.OFF_DRIVER_ROLES
DRIVER_ROLES = base.DRIVER_ROLES
TEMPORAL_DEVICE_TYPES = base.TEMPORAL_DEVICE_TYPES
TIMER_DEVICE_TYPES = base.TIMER_DEVICE_TYPES
COUNTER_DEVICE_TYPES = base.COUNTER_DEVICE_TYPES

display_text = base.display_text
parse_device = base.parse_device
normalize_device = base.normalize_device
normalize_trace_device = base.normalize_trace_device
project_label_from_root = base.project_label_from_root
device_key = base.device_key
semantic_gaps = base.semantic_gaps
trace_state = base.trace_state
device_comment = base.device_comment
resolve_label_occurrences = base.resolve_label_occurrences
trace_output_elements_for = base.trace_output_elements_for
relevant_unresolved_labels = base.relevant_unresolved_labels
label_resolution_gaps = base.label_resolution_gaps
temporal_predicates = base.temporal_predicates
execution_guards = base.execution_guards
driver_index = base.driver_index
occurrence_counts = base.occurrence_counts
row_key = base.row_key
row_conditions = base.row_conditions
row_driver_occurrences = base.row_driver_occurrences
row_write_occurrences = base.row_write_occurrences
row_instruction_refs = base.row_instruction_refs
required_state = base.required_state
driver_effect = base.driver_effect
condition_record = base.condition_record
logic_condition_record = base.logic_condition_record
simple_occ_record = base.simple_occ_record
load_cross_link_index = base.load_cross_link_index
attach_cross_links = base.attach_cross_links
format_condition = base.format_condition
format_cross_links = base.format_cross_links
format_execution_zone = base.format_execution_zone
format_execution_guard = base.format_execution_guard
format_text = base.format_text
compact_condition_key = base.compact_condition_key
format_compact_condition = base.format_compact_condition
compact_labels = base.compact_labels
format_row_summary = base.format_row_summary
state_lines = base.state_lines
format_compact = base.format_compact
build_parser = base.build_parser


# ``gx3_trace_state`` binds condition_refs_from_logic as a module global.  Keep
# one dispatcher installed there and use ContextVar rather than temporary
# monkeypatching: concurrent MCP trace calls then carry independent pruning
# facts without changing each other's behaviour. Calls outside this facade see
# the original function because the context variable is empty.
_ORIGINAL_CONDITION_REFS = base.condition_refs_from_logic
_ACTIVE_CONSTANT_FACTS: ContextVar[dict | None] = ContextVar(
    "gx3_trace_constant_facts", default=None
)
_ACTIVE_PRUNE_STATS: ContextVar[dict[str, int] | None] = ContextVar(
    "gx3_trace_prune_stats", default=None
)


def _condition_refs_dispatch(node: dict[str, Any]) -> list[dict[str, Any]]:
    facts = _ACTIVE_CONSTANT_FACTS.get()
    if not facts:
        return _ORIGINAL_CONDITION_REFS(node)
    raw = _ORIGINAL_CONDITION_REFS(node)
    result = simplify_logic_for_trace(node, facts)
    kept = _ORIGINAL_CONDITION_REFS(result.logic)
    stats = _ACTIVE_PRUNE_STATS.get()
    if stats is not None:
        stats["calls"] = stats.get("calls", 0) + 1
        stats["raw_refs"] = stats.get("raw_refs", 0) + len(raw)
        stats["kept_refs"] = stats.get("kept_refs", 0) + len(kept)
        if result.constant_value is not None:
            stats["constant_rows"] = stats.get("constant_rows", 0) + 1
    return kept


if base.condition_refs_from_logic is not _condition_refs_dispatch:
    base.condition_refs_from_logic = _condition_refs_dispatch


def _load_constant_context(root: Path):
    comments = base.load_comments_for_root(root)
    labels = base.load_label_resolver(root)
    rows = base.load_rows(root, comments)
    base.resolve_label_occurrences(rows, labels)
    comm_prefix = base.default_comm_prefix()
    refresh_areas = base.load_refresh_areas(Path(f"{comm_prefix}_refresh_areas.csv"))
    return load_trace_constant_context(root, rows, refresh_areas)


def __getattr__(name: str) -> Any:
    """Delegate legacy/internal attributes to the canonical trace engine.

    The former public module accumulated a few shared helpers over time.  The
    facade must not break an internal consumer merely because that helper was
    not explicitly re-exported above.
    """
    return getattr(base, name)


def _ref_key(value: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(value.get("device") or value.get("condition_device") or value.get("raw_device") or ""),
        str(value.get("role") or ""),
        str(value.get("required_state") or value.get("state") or ""),
    )


def _condition_key(value: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(value.get("device") or value.get("condition_device") or ""),
        str(value.get("role") or ""),
        str(value.get("required_state") or ""),
    )


def _simplify_device_logic(record: dict[str, Any], key: str, facts: dict) -> None:
    logic = record.get(key)
    if not isinstance(logic, dict):
        return
    result = simplify_logic_for_trace(logic, facts)
    text_key = f"{key}_text"
    record[f"raw_{key}"] = logic
    record[f"raw_{text_key}"] = str(record.get(text_key) or logic_to_text(logic))
    record[key] = result.logic
    record[text_key] = result.logic_text


def _filter_row_conditions(row: dict[str, Any], facts: dict) -> int:
    logic = row.get("enable_logic")
    if not isinstance(logic, dict):
        return 0

    result = simplify_logic_for_trace(logic, facts)
    original_conditions = list(row.get("conditions") or [])
    row["raw_enable_logic"] = logic
    row["raw_enable_logic_text"] = str(row.get("enable_logic_text") or logic_to_text(logic))
    row["enable_logic"] = result.logic
    row["enable_logic_text"] = result.logic_text
    row["constant_enable_value"] = result.constant_value
    row["constant_contacts"] = [dict(item) for item in result.constant_contacts]
    row["pruned_conditions"] = [dict(item) for item in result.pruned_conditions]

    remaining = {_ref_key(ref) for ref in condition_refs_from_logic(result.logic)}
    kept: list[dict[str, Any]] = []
    for cond in original_conditions:
        if _condition_key(cond) in remaining:
            kept.append(cond)
    row["conditions"] = kept
    # Conditions may already have been removed before the BFS queue expanded.
    # The simplified expression still knows every raw contact it eliminated,
    # so use that evidence count rather than only comparing returned records.
    return len(result.pruned_conditions)


def _reachable_devices(trace: dict[str, Any], edges: list[dict[str, Any]]) -> set[str]:
    target = str((trace.get("target") or {}).get("device") or "")
    if not target:
        return set()
    outgoing: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        outgoing.setdefault(str(edge.get("from_device") or ""), []).append(edge)

    reachable = {target}
    queue = deque([target])
    while queue:
        device = queue.popleft()
        for edge in outgoing.get(device, []):
            if not edge.get("has_driver") or edge.get("self_reference"):
                continue
            upstream = str(edge.get("condition_device") or "")
            if upstream and upstream not in reachable:
                reachable.add(upstream)
                queue.append(upstream)
    return reachable


def prune_trace_result(
    trace: dict[str, Any],
    root: Path,
    *,
    context=None,
    prequeue_stats: dict[str, int] | None = None,
) -> dict[str, Any]:
    if not trace.get("strict_logic"):
        trace["constant_pruning"] = {
            "enabled": False,
            "reason": "strict topology is required before constant branch pruning",
        }
        return trace

    comments = base.load_comments_for_root(root)
    context = context or _load_constant_context(root)
    trace["constant_pruning"] = context.summary()
    if not context.enabled:
        return trace

    facts = context.facts
    old_edges = list(trace.get("edges") or [])
    pruned_conditions = 0

    for device in trace.get("devices", []):
        _simplify_device_logic(device, "on_cause_logic", facts)
        _simplify_device_logic(device, "off_cause_logic", facts)

    allowed_by_row: dict[str, set[tuple[str, str, str]]] = {}
    for row in trace.get("driver_rows", []):
        pruned_conditions += _filter_row_conditions(row, facts)
        allowed_by_row[str(row.get("row_id") or "")] = {
            _condition_key(cond) for cond in row.get("conditions", [])
        }

    filtered_edges = [
        edge
        for edge in old_edges
        if _condition_key(edge) in allowed_by_row.get(str(edge.get("row_id") or ""), set())
    ]
    reachable = _reachable_devices(trace, filtered_edges)

    trace["devices"] = [
        record for record in trace.get("devices", []) if str(record.get("device") or "") in reachable
    ]
    trace["driver_rows"] = [
        row for row in trace.get("driver_rows", []) if str(row.get("device") or "") in reachable
    ]
    trace["edges"] = [
        edge
        for edge in filtered_edges
        if str(edge.get("from_device") or "") in reachable
        and (not edge.get("has_driver") or str(edge.get("condition_device") or "") in reachable)
    ]

    edge_counter = Counter(str(edge.get("condition_device") or "") for edge in trace["edges"])
    trace["top_condition_devices"] = [
        {"device": device, "count": count, "comment": base.device_comment(device, comments)}
        for device, count in edge_counter.most_common(30)
        if device
    ]

    stats = trace.setdefault("stats", {})
    stats["devices_traced"] = len(trace["devices"])
    stats["driver_rows"] = len(trace["driver_rows"])
    stats["dependency_edges"] = len(trace["edges"])
    stats["terminal_conditions"] = sum(1 for edge in trace["edges"] if not edge.get("has_driver"))
    stats["self_references"] = sum(1 for edge in trace["edges"] if edge.get("self_reference"))
    stats["constant_facts"] = len(facts)
    stats["pruned_dependency_conditions"] = pruned_conditions
    stats["pruned_dependency_edges"] = len(old_edges) - len(trace["edges"])

    prequeue_stats = prequeue_stats or {}
    prequeue_pruned = max(
        0,
        int(prequeue_stats.get("raw_refs", 0)) - int(prequeue_stats.get("kept_refs", 0)),
    )
    stats["prequeue_pruned_dependency_refs"] = prequeue_pruned
    stats["prequeue_constant_rows"] = int(prequeue_stats.get("constant_rows", 0))
    trace["constant_pruning"].update(
        {
            "stage": "pre-queue-prune",
            "pruned_conditions": pruned_conditions,
            "prequeue_pruned_refs": prequeue_pruned,
            "prequeue_condition_calls": int(prequeue_stats.get("calls", 0)),
            "postfilter_pruned_edges": len(old_edges) - len(trace["edges"]),
            "note": (
                "strict enable logic was simplified before upstream conditions entered the "
                "BFS queue; the post-pass only rewrites returned expressions and attaches evidence"
            ),
        }
    )
    return trace


def build_trace(
    root: Path,
    target_device: str,
    max_depth: int,
    max_devices: int,
    include_reset: bool,
    strict_logic: bool,
) -> dict[str, Any]:
    context = _load_constant_context(root) if strict_logic else None
    prequeue_stats: dict[str, int] = {}
    facts_token = _ACTIVE_CONSTANT_FACTS.set(
        context.facts if context is not None and context.enabled else None
    )
    stats_token = _ACTIVE_PRUNE_STATS.set(prequeue_stats if strict_logic else None)
    try:
        trace = base.build_trace(
            root=root,
            target_device=target_device,
            max_depth=max_depth,
            max_devices=max_devices,
            include_reset=include_reset,
            strict_logic=strict_logic,
        )
    finally:
        _ACTIVE_PRUNE_STATS.reset(stats_token)
        _ACTIVE_CONSTANT_FACTS.reset(facts_token)
    return prune_trace_result(
        trace,
        root,
        context=context,
        prequeue_stats=prequeue_stats,
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    args = base.build_parser().parse_args()
    root = Path(args.root)
    trace = build_trace(
        root=root,
        target_device=args.device,
        max_depth=args.max_depth,
        max_devices=args.max_devices,
        include_reset=not args.exclude_reset,
        strict_logic=args.strict_logic,
    )
    if not args.no_link_map:
        project = args.project or base.project_label_from_root(root)
        base.attach_cross_links(trace, project, Path(args.link_db))
    if args.format == "json":
        output = json.dumps(trace, ensure_ascii=False, indent=2)
    elif args.compact:
        output = base.format_compact(
            trace,
            row_limit=args.compact_row_limit,
            condition_limit=args.compact_condition_limit,
            ja=args.ja,
        )
    else:
        output = base.format_text(trace)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
