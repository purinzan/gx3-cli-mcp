from __future__ import annotations

"""Explain traced ladder conditions against an already-captured device snapshot.

This command is deliberately about one captured instant. It does not monitor a
PLC and it does not claim that current values explain a past trip. Static ladder
topology comes from ``trace-device --strict-logic``; contact pass/block meaning
comes from the same live-value evaluator used by ``ladder-print --live-values``.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from gx3cli.gx3_input_identity import fingerprint, short
from gx3cli.gx3_ladder_print import load_live_values, live_overlay_for_devices
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.trace_gx3_device_dependencies import build_trace


CURRENT_ONLY_NOTE = (
    "current snapshot only: these values explain the captured instant, not the "
    "historical cause of a past stop/trip"
)


def _snapshot_metadata(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        return {"timestamp": "", "source": "", "project_fingerprint": ""}
    nested = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {
        "timestamp": data.get("timestamp", nested.get("timestamp", "")),
        "source": data.get("source", nested.get("source", "")),
        "project_fingerprint": data.get(
            "project_fingerprint", nested.get("project_fingerprint", "")
        ),
    }


def load_snapshot(path: Path) -> tuple[dict[str, object], dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    # Keep value parsing exactly aligned with ladder-print. This accepts both
    # {"values": {...}} and live-read's contiguous-device snapshot shape.
    values = load_live_values(str(path))
    return values, _snapshot_metadata(raw)


def _contact_result(node: dict[str, Any], values: dict[str, object]) -> dict[str, object]:
    device = str(node.get("device") or node.get("raw_device") or "")
    role = str(node.get("role") or "")
    result: dict[str, object] = {
        "kind": "contact",
        "device": device,
        "role": role,
        "required_state": "ON" if role == "a" else "OFF" if role == "b" else "",
        "position": node.get("position", ""),
        "condition": "unknown",
    }
    ct_code = str(node.get("ct_code") or "")
    if ct_code in {"p", "f"}:
        result["reason"] = "edge contact requires previous-scan state"
        return result
    if not device or role not in {"a", "b"}:
        result["reason"] = "contact identity/role is unresolved"
        return result

    # Reuse the ladder-print live evaluator instead of re-implementing ON/OFF,
    # NC inversion, hexadecimal device spelling, or string truthiness here.
    overlay = live_overlay_for_devices([{"device": device, "role": role}], values)[0]
    result.update({key: value for key, value in overlay.items() if key not in {"role"}})
    if "value" not in overlay:
        result["condition"] = "missing"
        result["reason"] = "snapshot has no value for this device"
    return result


def _combine(op: str, child_states: list[str]) -> str:
    """Conservative four-state Boolean reduction.

    ``missing`` is not false. A definite blocker still proves an AND false and
    a definite passer still proves an OR true; otherwise unknown outranks
    missing because an unsupported predicate cannot be fixed by reading one
    more device value.
    """
    if not child_states:
        return "unknown"
    if op == "and":
        if "block" in child_states:
            return "block"
        if all(state == "pass" for state in child_states):
            return "pass"
        if "unknown" in child_states:
            return "unknown"
        return "missing"
    if op == "or":
        if "pass" in child_states:
            return "pass"
        if all(state == "block" for state in child_states):
            return "block"
        if "unknown" in child_states:
            return "unknown"
        return "missing"
    return "unknown"


def evaluate_logic(
    node: object, values: dict[str, object]
) -> tuple[str, list[dict[str, object]]]:
    """Evaluate the strict trace logic without inventing unsupported semantics."""
    if not isinstance(node, dict):
        return "unknown", [{"kind": "unknown", "condition": "unknown", "reason": "logic node is not decoded"}]
    op = str(node.get("op") or "")
    if op == "true":
        return "pass", []
    if op == "false":
        return "block", []
    if op == "contact":
        leaf = _contact_result(node, values)
        return str(leaf["condition"]), [leaf]
    if op in {"and", "or"}:
        leaves: list[dict[str, object]] = []
        states: list[str] = []
        for child in node.get("args", []):
            state, child_leaves = evaluate_logic(child, values)
            states.append(state)
            leaves.extend(child_leaves)
        return _combine(op, states), leaves
    if op == "not":
        state, leaves = evaluate_logic(node.get("arg"), values)
        return ({"pass": "block", "block": "pass"}.get(state, state)), leaves
    if op == "predicate":
        return "unknown", [
            {
                "kind": "predicate",
                "condition": "unknown",
                "opcode": node.get("opcode", ""),
                "position": node.get("position", ""),
                "devices": node.get("devices", []),
                "constants": node.get("constants", []),
                "reason": "predicate value is not evaluated from a contact snapshot",
            }
        ]
    if op == "too_large":
        return "unknown", [
            {
                "kind": "too_large",
                "condition": "unknown",
                "reason": "static condition was capped before a complete logic tree was produced",
            }
        ]
    return "unknown", [
        {
            "kind": op or "unknown",
            "condition": "unknown",
            "opcode": node.get("opcode", ""),
            "position": node.get("position", ""),
            "reason": "unsupported/unknown logic is not guessed",
        }
    ]


def _leaf_metadata(row: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
    out: dict[tuple[str, str, str], dict[str, Any]] = {}
    for condition in row.get("conditions", []):
        key = (
            str(condition.get("device", "")),
            str(condition.get("role", "")),
            str(condition.get("position", "")),
        )
        out[key] = condition
    return out


def explain_driver_row(row: dict[str, Any], values: dict[str, object]) -> dict[str, object]:
    state, leaves = evaluate_logic(row.get("enable_logic"), values)
    metadata = _leaf_metadata(row)
    for leaf in leaves:
        if leaf.get("kind") != "contact":
            continue
        key = (
            str(leaf.get("device", "")),
            str(leaf.get("role", "")),
            str(leaf.get("position", "")),
        )
        meta = metadata.get(key)
        if meta is None:
            # Older traces may not carry position for every leaf.
            meta = next(
                (
                    candidate
                    for candidate in row.get("conditions", [])
                    if str(candidate.get("device", "")) == key[0]
                    and str(candidate.get("role", "")) == key[1]
                ),
                None,
            )
        if meta:
            for field in (
                "comment",
                "classes",
                "has_driver",
                "self_reference",
                "source_kind",
                "semantic_group",
                "trace_boundary",
                "stop_reason",
                "refresh_area",
            ):
                if field in meta:
                    leaf[field] = meta[field]

    guards = list(row.get("execution_guards") or [])
    temporal = list(row.get("temporal_predicates") or [])
    warnings: list[str] = []
    if guards:
        warnings.append("unresolved execution guard means this row may not execute")
        # Jump/CALL context can defeat a locally true/false rung result.
        state = "unknown"
    if str(row.get("parse_status", "")) != "exact":
        warnings.append("row was not fully decoded")
        state = "unknown"
    if int((row.get("logic_stats") or {}).get("too_large", 0) or 0) > 0:
        warnings.append("strict logic was capped as too large")
        state = "unknown"
    if temporal:
        warnings.append(
            "stateful/temporal semantics are present; snapshot evaluates the current write/enable condition only"
        )

    return {
        "row_id": row.get("row_id", ""),
        "device": row.get("device", ""),
        "lddb": row.get("lddb", ""),
        "pos": row.get("pos", ""),
        "block_id": row.get("block_id", ""),
        "title": row.get("title", ""),
        "driver_roles": row.get("driver_roles", []),
        "driver_effects": row.get("driver_effects", []),
        "enable_logic_text": row.get("enable_logic_text", ""),
        "current_enable_condition": state,
        "leaf_conditions": leaves,
        "execution_guards": guards,
        "temporal_predicates": temporal,
        "historical_root_cause_supported": False,
        "warnings": warnings,
    }


def explain_trace(trace: dict[str, Any], values: dict[str, object]) -> list[dict[str, object]]:
    return [explain_driver_row(row, values) for row in trace.get("driver_rows", [])]


def build_explanation(
    root: Path,
    target_device: str,
    snapshot_path: Path,
    *,
    max_depth: int = 4,
    max_devices: int = 300,
    include_reset: bool = True,
    allow_fingerprint_mismatch: bool = False,
) -> dict[str, object]:
    values, metadata = load_snapshot(snapshot_path)
    actual_fingerprint = fingerprint(root)
    supplied_fingerprint = str(metadata.get("project_fingerprint") or "")
    fingerprint_match: bool | None = None
    warnings: list[str] = []
    if supplied_fingerprint:
        fingerprint_match = supplied_fingerprint == actual_fingerprint
        if not fingerprint_match:
            message = (
                "snapshot project fingerprint does not match this project: "
                f"snapshot={short(supplied_fingerprint)} project={short(actual_fingerprint)}"
            )
            if not allow_fingerprint_mismatch:
                raise SystemExit(message + "; use --allow-fingerprint-mismatch only for an intentional comparison")
            warnings.append(message)
    elif actual_fingerprint:
        warnings.append("snapshot has no project_fingerprint; project identity could not be verified")

    trace = build_trace(
        root=root,
        target_device=target_device,
        max_depth=max_depth,
        max_devices=max_devices,
        include_reset=include_reset,
        strict_logic=True,
    )
    rows = explain_trace(trace, values)
    counts = {state: 0 for state in ("pass", "block", "missing", "unknown")}
    for row in rows:
        state = str(row.get("current_enable_condition", "unknown"))
        counts[state if state in counts else "unknown"] += 1

    analysis_state = str((trace.get("analysis") or {}).get("state", ""))
    if analysis_state and analysis_state != "checked":
        warnings.append(
            "static trace itself is not fully checked; see trace_analysis before using snapshot conclusions"
        )

    return {
        "kind": "current_snapshot_explanation",
        "target": trace.get("target", {"device": target_device}),
        "scope": CURRENT_ONLY_NOTE,
        "snapshot": {
            "path": str(snapshot_path),
            "timestamp": metadata.get("timestamp", ""),
            "source": metadata.get("source", ""),
            "project_fingerprint": supplied_fingerprint,
            "actual_project_fingerprint": actual_fingerprint,
            "fingerprint_match": fingerprint_match,
            "value_count": len(values),
        },
        "trace_analysis": trace.get("analysis", {}),
        "trace_verification": trace.get("verification", {}),
        "driver_row_state_counts": counts,
        "driver_rows": rows,
        "warnings": warnings,
    }


def format_text(result: dict[str, object]) -> str:
    target = result.get("target", {}) if isinstance(result.get("target"), dict) else {}
    snapshot = result.get("snapshot", {}) if isinstance(result.get("snapshot"), dict) else {}
    lines = [
        f"target: {target.get('device', '')}",
        f"snapshot: {snapshot.get('timestamp') or '(timestamp not supplied)'}",
        f"values: {snapshot.get('value_count', 0)}",
        f"scope: {result.get('scope', CURRENT_ONLY_NOTE)}",
        "",
    ]
    if snapshot.get("project_fingerprint"):
        match = snapshot.get("fingerprint_match")
        lines.append(
            "fingerprint: "
            + ("match" if match is True else "MISMATCH" if match is False else "not verified")
        )
    for warning in result.get("warnings", []):
        lines.append(f"WARNING: {warning}")
    if result.get("warnings"):
        lines.append("")

    rows = result.get("driver_rows", [])
    if not rows:
        lines.append("no driver rows were found for the target")
        return "\n".join(lines)
    for row in rows:
        lines.append(
            f"[{row.get('current_enable_condition', 'unknown').upper():<7}] "
            f"{row.get('lddb', '')} pos={row.get('pos', '')} {row.get('title', '')}"
        )
        if row.get("enable_logic_text"):
            lines.append(f"  logic: {row.get('enable_logic_text')}")
        for leaf in row.get("leaf_conditions", []):
            device = leaf.get("device", leaf.get("opcode", leaf.get("kind", "?")))
            value = f" value={leaf['value']!r}" if "value" in leaf else ""
            reason = f" ({leaf['reason']})" if leaf.get("reason") else ""
            lines.append(
                f"  - {str(leaf.get('condition', 'unknown')):<7} {device}{value}{reason}"
            )
        for warning in row.get("warnings", []):
            lines.append(f"  NOTE: {warning}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Explain strict traced ladder conditions against one captured device snapshot."
    )
    parser.add_argument("device", help="target device or resolved label")
    parser.add_argument("--root", default=str(default_project_root()), help="project folder or .gx3")
    parser.add_argument("--snapshot", required=True, help="captured live-values JSON")
    parser.add_argument("--max-depth", type=int, default=4)
    parser.add_argument("--max-devices", type=int, default=300)
    parser.add_argument("--exclude-reset", action="store_true")
    parser.add_argument("--allow-fingerprint-mismatch", action="store_true")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("-o", "--output", default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    root = resolve_project_root(args.root)
    result = build_explanation(
        root,
        args.device,
        Path(args.snapshot),
        max_depth=args.max_depth,
        max_devices=args.max_devices,
        include_reset=not args.exclude_reset,
        allow_fingerprint_mismatch=args.allow_fingerprint_mismatch,
    )
    text = (
        json.dumps(result, ensure_ascii=False, indent=2)
        if args.format == "json"
        else format_text(result)
    )
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(f"written: {path}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
