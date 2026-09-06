from __future__ import annotations

"""Read current Mitsubishi PLC values and explain/replay captured data.

The network reader is intentionally read-only. Snapshot explanation and captured
log replay are separate offline paths: they consume already-captured JSON/CSV,
never open a PLC connection, and never claim ordinary imported/polled data is
scan-synchronized recording.
"""

import argparse
import json
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gx3cli.gx3_analysis_state import (
    AnalysisState,
    DECODE,
    NO_MEASUREMENT,
    PARTIAL,
    SEMANTICS,
    TOPOLOGY,
    TRUNCATED,
    UNSUPPORTED,
    checked,
    from_dict,
    no_measurement,
    not_evaluated,
    worst,
)
from gx3cli.gx3_device_name import device_radix, format_device
from gx3cli.gx3_input_identity import fingerprint, short
from gx3cli.gx3_ladder_print import load_live_values, live_overlay_for_devices
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.trace_gx3_device_dependencies import build_trace


DEVICE_CODES = {
    "X": 0x9C,
    "Y": 0x9D,
    "M": 0x90,
    "L": 0x92,
    "F": 0x93,
    "V": 0x94,
    "B": 0xA0,
    "D": 0xA8,
    "W": 0xB4,
    "TS": 0xC1,
    "TC": 0xC0,
    "TN": 0xC2,
    "SS": 0xC7,
    "SC": 0xC6,
    "SN": 0xC8,
    "CS": 0xC4,
    "CC": 0xC3,
    "CN": 0xC5,
    "SB": 0xA1,
    "SW": 0xB5,
    "S": 0x98,
    "DX": 0xA2,
    "DY": 0xA3,
    "SM": 0x91,
    "SD": 0xA9,
    "Z": 0xCC,
    "R": 0xAF,
    "ZR": 0xB0,
}

CURRENT_ONLY_NOTE = (
    "current snapshot only: these values explain the captured instant, not the "
    "historical cause of a past stop/trip"
)

LOG_REPLAY_SCOPE = (
    "offline imported/polled device log; timestamps are not assumed to be "
    "scan-synchronized PLC recording"
)


@dataclass(frozen=True)
class DeviceAddress:
    prefix: str
    number: int

    @property
    def display(self) -> str:
        return format_device(self.prefix, self.number)


def parse_device(text: str) -> DeviceAddress:
    value = text.strip().upper()
    for prefix in sorted(DEVICE_CODES, key=len, reverse=True):
        if not value.startswith(prefix):
            continue
        raw = value[len(prefix) :]
        if not raw:
            break
        base = device_radix(prefix)
        try:
            number = int(raw, base)
        except ValueError:
            break
        if number < 0 or number > 0xFFFFFF:
            raise ValueError(f"device address out of range: {text}")
        return DeviceAddress(prefix, number)
    raise ValueError(f"unsupported or invalid device: {text}")


def build_3e_binary_read_frame(
    device: DeviceAddress,
    count: int,
    *,
    bit_units: bool = False,
    network: int = 0,
    pc: int = 0xFF,
    io: int = 0x03FF,
    station: int = 0,
    timer: int = 0x0010,
) -> bytes:
    if count < 1 or count > 960:
        raise ValueError("count must be between 1 and 960")
    request_data = (
        timer.to_bytes(2, "little")
        + b"\x01\x04"
        + (0x0001 if bit_units else 0x0000).to_bytes(2, "little")
        + device.number.to_bytes(3, "little")
        + bytes([DEVICE_CODES[device.prefix]])
        + count.to_bytes(2, "little")
    )
    header = (
        b"\x50\x00"
        + bytes([network & 0xFF, pc & 0xFF])
        + io.to_bytes(2, "little")
        + bytes([station & 0xFF])
        + len(request_data).to_bytes(2, "little")
    )
    return header + request_data


def read_exact(sock: socket.socket, size: int) -> bytes:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("PLC closed the connection before the response was complete")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def parse_3e_binary_response(data: bytes) -> bytes:
    if len(data) < 11:
        raise ValueError(f"short MC response: {len(data)} bytes")
    if data[:2] != b"\xD0\x00":
        raise ValueError(f"unexpected MC response subheader: {data[:2].hex()}")
    length = int.from_bytes(data[7:9], "little")
    if len(data) != 9 + length:
        raise ValueError(f"MC response length mismatch: header={length} actual={len(data) - 9}")
    completion = int.from_bytes(data[9:11], "little")
    if completion:
        raise RuntimeError(f"PLC returned MC completion code 0x{completion:04X}")
    return data[11:]


def decode_bit_values(payload: bytes, count: int) -> list[bool]:
    values: list[bool] = []
    for byte in payload:
        values.append((byte >> 4) != 0)
        if len(values) >= count:
            break
        values.append((byte & 0x0F) != 0)
        if len(values) >= count:
            break
    if len(values) < count:
        raise ValueError(f"not enough bit data: got {len(values)} values, expected {count}")
    return values


def decode_word_values(payload: bytes, value_type: str, count: int) -> list[int | float]:
    if value_type in {"word", "signed-word"}:
        fmt = "<" + ("h" if value_type == "signed-word" else "H") * count
        size = 2 * count
        if len(payload) < size:
            raise ValueError(f"not enough word data: got {len(payload)} bytes, expected {size}")
        return list(struct.unpack(fmt, payload[:size]))
    if value_type in {"dword", "signed-dword", "float"}:
        if count % 2:
            raise ValueError(f"{value_type} reads require an even word count")
        item_count = count // 2
        fmt_char = {"dword": "I", "signed-dword": "i", "float": "f"}[value_type]
        size = 4 * item_count
        if len(payload) < size:
            raise ValueError(f"not enough dword data: got {len(payload)} bytes, expected {size}")
        return list(struct.unpack("<" + fmt_char * item_count, payload[:size]))
    raise ValueError(f"unsupported value type: {value_type}")


def explain_request(args: argparse.Namespace) -> dict[str, object]:
    device = parse_device(args.device)
    bit_units = args.type == "bit"
    read_count = args.count * 2 if args.type in {"dword", "signed-dword", "float"} else args.count
    frame = build_3e_binary_read_frame(
        device,
        read_count,
        bit_units=bit_units,
        network=args.network,
        pc=args.pc,
        io=args.io,
        station=args.station,
        timer=args.timer,
    )
    return {
        "ip": args.ip,
        "port": args.port,
        "frame": "3e-binary",
        "dry_run": True,
        "device": device.display,
        "device_prefix": device.prefix,
        "device_number": device.number,
        "device_code": f"0x{DEVICE_CODES[device.prefix]:02X}",
        "type": args.type,
        "count": args.count,
        "read_words": read_count if not bit_units else None,
        "bit_units": bit_units,
        "network": args.network,
        "pc": args.pc,
        "io": args.io,
        "station": args.station,
        "timer": args.timer,
        "request_hex": frame.hex(" "),
    }


def read_current_values(args: argparse.Namespace) -> dict[str, object]:
    plan = explain_request(args)
    frame = bytes.fromhex(str(plan["request_hex"]))
    if getattr(args, "dry_run", False):
        return plan
    device = parse_device(args.device)
    bit_units = args.type == "bit"
    read_count = int(plan["read_words"] or args.count)
    with socket.create_connection((args.ip, args.port), timeout=args.timeout) as sock:
        sock.settimeout(args.timeout)
        sock.sendall(frame)
        header = read_exact(sock, 9)
        length = int.from_bytes(header[7:9], "little")
        payload = read_exact(sock, length)
    raw = parse_3e_binary_response(header + payload)
    values = decode_bit_values(raw, args.count) if bit_units else decode_word_values(raw, args.type, read_count)
    result = dict(plan)
    result["values"] = values
    result["dry_run"] = False
    return result


def format_text(result: dict[str, object]) -> str:
    values = result.get("values", [])
    lines = [
        f"PLC: {result['ip']}:{result['port']} ({result['frame']})",
        f"Device: {result['device']} type={result['type']} count={result['count']}",
        f"Request: device_code={result.get('device_code')} bit_units={result.get('bit_units')} hex={result.get('request_hex')}",
        "Values:",
    ]
    if result.get("dry_run"):
        lines[-1] = "Values: (dry-run; no PLC connection opened)"
        return "\n".join(lines)
    if isinstance(values, list):
        for offset, value in enumerate(values):
            lines.append(f"  +{offset}: {value}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read current PLC device values over MC Protocol/SLMP 3E binary.",
        epilog=MODE_HELP,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ip", required=True, help="PLC IP address")
    parser.add_argument("--port", type=int, default=5000, help="PLC TCP port, often 5000 or project-specific")
    parser.add_argument("--device", required=True, help="start device, e.g. D1000, M200, X10")
    parser.add_argument("--count", type=int, default=1, help="number of values to read")
    parser.add_argument("--type", choices=["bit", "word", "signed-word", "dword", "signed-dword", "float"], default="word")
    parser.add_argument("--timeout", type=float, default=3.0)
    parser.add_argument("--network", type=int, default=0)
    parser.add_argument("--pc", type=lambda s: int(s, 0), default=0xFF)
    parser.add_argument("--io", type=lambda s: int(s, 0), default=0x03FF)
    parser.add_argument("--station", type=int, default=0)
    parser.add_argument("--timer", type=lambda s: int(s, 0), default=0x0010)
    parser.add_argument("--dry-run", action="store_true", help="print the planned request without opening a PLC connection")
    parser.add_argument("--explain-frame", action="store_true", help="alias for --dry-run with request frame details")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("-o", "--output", help="write output to file")
    return parser


MODE_HELP = """live-read has three modes:

  live-read --ip <addr> --device <device> [...]   read current values from a PLC
  live-read explain <device> --snapshot <file>    explain traced conditions against a capture
  live-read replay <normalize|series|changes|snapshot> <log>   read a captured log offline

The last two never open a network connection; they read files that were
captured earlier."""


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raw = list(sys.argv[1:] if argv is None else argv)
    # The offline modes were written with their own parsers and their own
    # main(), and nothing called either one: `live-read` reached the network
    # reader alone, so ~740 lines of shipped code had no way in. Dispatch on a
    # leading word, which leaves every existing flag-first invocation alone.
    if raw and raw[0] == "explain":
        return snapshot_main(raw[1:])
    if raw and raw[0] == "replay":
        return log_replay_main(raw[1:])
    if raw and raw[0] in {"modes", "help"}:
        print(MODE_HELP)
        return 0
    args = build_parser().parse_args(raw)
    if args.explain_frame:
        args.dry_run = True
    try:
        result = read_current_values(args)
    except (ConnectionError, OSError, RuntimeError, ValueError) as exc:
        print(f"gx3 live-read error: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else format_text(result)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


# ----- Offline captured-snapshot explanation ---------------------------------


def _snapshot_metadata(data: object) -> dict[str, object]:
    if not isinstance(data, dict):
        return {"timestamp": "", "source": "", "project_fingerprint": ""}
    nested = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return {
        "timestamp": data.get("timestamp", nested.get("timestamp", "")),
        "source": data.get("source", nested.get("source", "")),
        "project_fingerprint": data.get("project_fingerprint", nested.get("project_fingerprint", "")),
    }


def load_snapshot(path: Path) -> tuple[dict[str, object], dict[str, object]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return load_live_values(str(path)), _snapshot_metadata(raw)


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
        result["analysis_state"] = UNSUPPORTED
        result["analysis_stage"] = SEMANTICS
        result["next_step"] = "read this edge from a scan-synchronized recording, not a snapshot"
        return result
    if not device or role not in {"a", "b"}:
        result["reason"] = "contact identity/role is unresolved"
        result["analysis_state"] = PARTIAL
        result["analysis_stage"] = DECODE
        result["next_step"] = "gx3-cli parse-gaps --root <project> to see what the decoder could not read"
        return result
    overlay = live_overlay_for_devices([{"device": device, "role": role}], values)[0]
    result.update({key: value for key, value in overlay.items() if key != "role"})
    if "value" not in overlay:
        # The file was read through to the end. What is absent is a measured
        # value, which is a different thing from a decode gap and gets its own
        # state so a summary cannot average the two together.
        result["condition"] = "missing"
        result["reason"] = "snapshot has no value for this device"
        result["analysis_state"] = NO_MEASUREMENT
        result["next_step"] = f"capture {device} into the snapshot before reading this condition"
    return result


def _combine_snapshot_states(op: str, child_states: list[str]) -> str:
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


def evaluate_logic(node: object, values: dict[str, object]) -> tuple[str, list[dict[str, object]]]:
    """Conservatively evaluate a strict trace tree against one snapshot."""
    if not isinstance(node, dict):
        return "unknown", [{
            "kind": "unknown",
            "condition": "unknown",
            "reason": "logic node is not decoded",
            "analysis_state": PARTIAL,
            "analysis_stage": DECODE,
            "next_step": "gx3-cli parse-gaps --root <project> to see what the decoder could not read",
        }]
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
        return _combine_snapshot_states(op, states), leaves
    if op == "not":
        state, leaves = evaluate_logic(node.get("arg"), values)
        return {"pass": "block", "block": "pass"}.get(state, state), leaves
    if op == "predicate":
        return "unknown", [{
            "kind": "predicate",
            "condition": "unknown",
            "opcode": node.get("opcode", ""),
            "position": node.get("position", ""),
            "devices": node.get("devices", []),
            "constants": node.get("constants", []),
            "reason": "predicate value is not evaluated from a contact snapshot",
            "analysis_state": UNSUPPORTED,
            "analysis_stage": SEMANTICS,
            "next_step": "evaluate this comparison by hand from the captured word values",
        }]
    if op == "too_large":
        return "unknown", [{
            "kind": "too_large",
            "condition": "unknown",
            "reason": "static condition was capped before a complete logic tree was produced",
            "analysis_state": TRUNCATED,
            "analysis_stage": TOPOLOGY,
            "next_step": "narrow the target with --max-depth/--max-devices so the condition fits the budget",
        }]
    return "unknown", [{
        "kind": op or "unknown",
        "condition": "unknown",
        "opcode": node.get("opcode", ""),
        "position": node.get("position", ""),
        "reason": "unsupported/unknown logic is not guessed",
        "analysis_state": UNSUPPORTED,
        "analysis_stage": SEMANTICS,
        "next_step": "read this rung with gx3-cli rung-text and judge the condition by hand",
    }]


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


def leaf_states(leaves: list[dict[str, object]]) -> list[AnalysisState]:
    """The shared states the leaves of one row already declared.

    Each leaf names its own state where it is built, so this does not re-derive
    one from the leaf's prose. A leaf that says nothing was read cleanly and
    contributes nothing.
    """
    states: list[AnalysisState] = []
    for leaf in leaves:
        name = str(leaf.get("analysis_state") or "")
        if not name:
            continue
        reason = str(leaf.get("reason") or "")
        next_step = str(leaf.get("next_step") or "")
        device = str(leaf.get("device") or "")
        detail = {"device": device} if device else {}
        if name == NO_MEASUREMENT:
            states.append(no_measurement(reason, next_step, detail))
            continue
        states.append(AnalysisState(
            name,
            reason=reason,
            next_step=next_step,
            stage=str(leaf.get("analysis_stage") or SEMANTICS),
            detail=detail,
        ))
    return states


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
            meta = next((
                candidate
                for candidate in row.get("conditions", [])
                if str(candidate.get("device", "")) == key[0]
                and str(candidate.get("role", "")) == key[1]
            ), None)
        if meta:
            for field in (
                "comment", "classes", "has_driver", "self_reference", "source_kind",
                "semantic_group", "trace_boundary", "stop_reason", "refresh_area",
            ):
                if field in meta:
                    leaf[field] = meta[field]

    guards = list(row.get("execution_guards") or [])
    temporal = list(row.get("temporal_predicates") or [])
    warnings: list[str] = []
    states: list[AnalysisState] = leaf_states(leaves)
    if guards:
        warnings.append("unresolved execution guard means this row may not execute")
        states.append(AnalysisState(
            PARTIAL,
            reason="an execution guard (MC/jump) over this row is unresolved, so it may not execute",
            next_step="gx3-cli exec-config --root <project> to see what gates this program",
            stage=SEMANTICS,
        ))
        state = "unknown"
    if str(row.get("parse_status", "")) != "exact":
        warnings.append("row was not fully decoded")
        states.append(AnalysisState(
            PARTIAL,
            reason="the row was not fully decoded",
            next_step="gx3-cli parse-gaps --root <project> to see what the decoder could not read",
            stage=DECODE,
        ))
        state = "unknown"
    if int((row.get("logic_stats") or {}).get("too_large", 0) or 0) > 0:
        warnings.append("strict logic was capped as too large")
        states.append(AnalysisState(
            TRUNCATED,
            reason="the strict logic tree was capped before it was complete",
            next_step="narrow the target with --max-depth/--max-devices so the condition fits the budget",
            stage=TOPOLOGY,
        ))
        state = "unknown"
    if temporal:
        warnings.append("stateful/temporal semantics are present; snapshot evaluates the current write/enable condition only")
        states.append(AnalysisState(
            PARTIAL,
            reason="stateful/temporal semantics are present; one snapshot shows the current condition only",
            next_step="use a scan-synchronized recording to judge how this row got here",
            stage=SEMANTICS,
        ))

    analysis = worst(states) if states else checked()
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
        "analysis": analysis.as_dict(),
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

    # The whole answer is only as good as its weakest part, and the static
    # trace is part of it: a snapshot read against a trace that itself stopped
    # early is not a checked result, however many rows evaluated cleanly.
    states = [from_dict(row.get("analysis")) for row in rows]
    states.append(from_dict(trace.get("analysis")))
    if not rows:
        states.append(not_evaluated(
            "the trace produced no driver row for this device",
            next_step=f"gx3-cli xref where-used {target_device} --root <project> to see whether anything writes it",
        ))
    analysis = worst(states)
    # `warnings` keeps its own terse note for readers that only look there.
    # The sentence itself lives in `analysis` now, so it is not repeated here.
    if not from_dict(trace.get("analysis")).conclusive:
        warnings.append("static trace itself is not fully checked; see trace_analysis before using snapshot conclusions")

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
        "analysis": analysis.as_dict(),
        "trace_analysis": trace.get("analysis", {}),
        "trace_verification": trace.get("verification", {}),
        "driver_row_state_counts": counts,
        "driver_rows": rows,
        "warnings": warnings,
    }


def format_snapshot_text(result: dict[str, object]) -> str:
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
        lines.append("fingerprint: " + ("match" if match is True else "MISMATCH" if match is False else "not verified"))
    # First, in the same words every other command uses, whether this answer
    # can be read as the whole answer.
    lines.append(from_dict(result.get("analysis")).line("answer"))
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
        row_analysis = from_dict(row.get("analysis"))
        if not row_analysis.conclusive:
            lines.append(f"  {row_analysis.line()}")
        for leaf in row.get("leaf_conditions", []):
            device = leaf.get("device", leaf.get("opcode", leaf.get("kind", "?")))
            value = f" value={leaf['value']!r}" if "value" in leaf else ""
            reason = f" ({leaf['reason']})" if leaf.get("reason") else ""
            lines.append(f"  - {str(leaf.get('condition', 'unknown')):<7} {device}{value}{reason}")
        for warning in row.get("warnings", []):
            lines.append(f"  NOTE: {warning}")
    return "\n".join(lines)


def build_snapshot_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gx3-cli live-read explain",
        description="Explain strict traced ladder conditions against one captured device snapshot.",
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


def snapshot_main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_snapshot_parser().parse_args(argv)
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
    text = json.dumps(result, ensure_ascii=False, indent=2) if args.format == "json" else format_snapshot_text(result)
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(f"written: {path}")
    else:
        print(text)
    return 0


# ----- Offline captured device-log replay ------------------------------------


def _canonical_timestamp(value: object) -> tuple[str, object]:
    from datetime import datetime, timezone

    text = str(value or "").strip()
    if not text:
        raise ValueError("timestamp is required")
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        dt = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(f"timestamp must be ISO 8601: {text}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace("+00:00", "Z"), dt


def _infer_value_type(value: object) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int) and not isinstance(value, bool):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "string"
    return "unknown"


def _parse_logged_value(value: object, value_type: str) -> object:
    kind = value_type.strip().lower()
    if not isinstance(value, str):
        if kind in {"bit", "bool", "boolean"}:
            return bool(value)
        if kind in {"word", "signed-word", "dword", "signed-dword", "int", "integer"}:
            return int(value)
        if kind in {"float", "real", "double"}:
            return float(value)
        return value

    text = value.strip()
    if kind in {"bit", "bool", "boolean"}:
        lowered = text.lower()
        if lowered in {"1", "true", "on", "yes"}:
            return True
        if lowered in {"0", "false", "off", "no"}:
            return False
        raise ValueError(f"cannot parse boolean value: {value!r}")
    if kind in {"word", "signed-word", "dword", "signed-dword", "int", "integer"}:
        try:
            return int(text, 0)
        except ValueError:
            return int(text, 10)
    if kind in {"float", "real", "double"}:
        return float(text)
    if kind in {"string", "str", "text"}:
        return value
    if kind and kind != "unknown":
        # Unknown vendor-specific types are preserved verbatim rather than guessed.
        return value

    lowered = text.lower()
    if lowered in {"true", "on", "yes"}:
        return True
    if lowered in {"false", "off", "no"}:
        return False
    try:
        return int(text, 0)
    except ValueError:
        pass
    try:
        return float(text)
    except ValueError:
        return value


def _normalise_log_row(
    raw: dict[str, object],
    *,
    sequence: int,
    default_source: str,
    default_fingerprint: str,
) -> dict[str, object]:
    timestamp, _dt = _canonical_timestamp(raw.get("timestamp"))
    device = str(raw.get("device") or "").strip().upper()
    if not device:
        raise ValueError(f"record {sequence}: device is required")
    if "value" not in raw:
        raise ValueError(f"record {sequence}: value is required")
    declared_type = str(raw.get("value_type") or "").strip()
    parsed = _parse_logged_value(raw.get("value"), declared_type)
    value_type = declared_type or _infer_value_type(parsed)
    return {
        "timestamp": timestamp,
        "device": device,
        "value": parsed,
        "value_type": value_type or "unknown",
        "source": str(raw.get("source") or default_source or "").strip(),
        "project_fingerprint": str(raw.get("project_fingerprint") or default_fingerprint or "").strip(),
        "_sequence": sequence,
    }


def _read_json_log(path: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    metadata: dict[str, object] = {}
    if isinstance(data, list):
        rows = data
    elif isinstance(data, dict):
        nested = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        metadata = {
            "source": data.get("source", nested.get("source", "")),
            "project_fingerprint": data.get("project_fingerprint", nested.get("project_fingerprint", "")),
        }
        rows = data.get("records")
        if rows is None:
            rows = data.get("log")
        if rows is None:
            rows = data.get("rows")
    else:
        raise ValueError("JSON log must be a record list or an object containing records")
    if not isinstance(rows, list):
        raise ValueError("JSON log must contain a records/log/rows list")
    clean: list[dict[str, object]] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"record {index}: expected an object")
        clean.append(row)
    return clean, metadata


def _read_csv_log(path: Path) -> tuple[list[dict[str, object]], dict[str, object]]:
    import csv

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        required = {"timestamp", "device", "value"}
        fields = set(reader.fieldnames or [])
        missing = sorted(required - fields)
        if missing:
            raise ValueError("CSV is missing required columns: " + ", ".join(missing))
        return [dict(row) for row in reader], {}


def load_captured_log(path: Path) -> dict[str, object]:
    """Load CSV/JSON and return deterministic normalized records.

    Duplicate policy: same timestamp+device is last-record-wins in source order.
    No value is carried forward to another timestamp.
    """
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        raw_rows, metadata = _read_csv_log(path)
    elif suffix == ".json":
        raw_rows, metadata = _read_json_log(path)
    else:
        raise ValueError("captured log input must be .csv or .json")

    default_source = str(metadata.get("source") or path.name)
    default_fp = str(metadata.get("project_fingerprint") or "")
    deduped: dict[tuple[str, str], dict[str, object]] = {}
    duplicate_count = 0
    capture_identity: tuple[str, str] | None = None
    for sequence, raw in enumerate(raw_rows):
        # Validate BEFORE last-record-wins can erase evidence of a different
        # PLC/project. A filename is a display fallback, not a PLC identity.
        # Missing identity may inherit file metadata, never another record.
        identity = (
            str(raw.get("source") or metadata.get("source") or "").strip(),
            str(raw.get("project_fingerprint") or default_fp or "").strip(),
        )
        if capture_identity is None:
            capture_identity = identity
        elif identity != capture_identity:
            raise ValueError(
                f"record {sequence}: mixed capture identity (source/project_fingerprint); "
                "split the log by PLC and project version before replay; "
                "missing identity is not assumed to match a named capture"
            )
        row = _normalise_log_row(
            raw,
            sequence=sequence,
            default_source=default_source,
            default_fingerprint=default_fp,
        )
        key = (str(row["timestamp"]), str(row["device"]))
        if key in deduped:
            duplicate_count += 1
        deduped[key] = row

    records = sorted(
        deduped.values(),
        key=lambda row: (_canonical_timestamp(row["timestamp"])[1], str(row["device"]), int(row["_sequence"])),
    )
    for row in records:
        row.pop("_sequence", None)
    fingerprints = sorted({str(row["project_fingerprint"]) for row in records if row.get("project_fingerprint")})
    sources = sorted({str(row["source"]) for row in records if row.get("source")})
    return {
        "kind": "captured_device_log",
        "scope": LOG_REPLAY_SCOPE,
        "metadata": {
            "input": str(path),
            "input_records": len(raw_rows),
            "normalized_records": len(records),
            "duplicate_records_replaced": duplicate_count,
            "duplicate_policy": "same timestamp+device: last input record wins",
            "timestamp_policy": "ISO 8601 normalized to UTC; timezone-less timestamps are treated as UTC",
            "carry_forward": False,
            "sources": sources,
            "project_fingerprints": fingerprints,
        },
        "records": records,
    }


def build_device_series(normalized: dict[str, object], device: str | None = None) -> dict[str, object]:
    selected = device.strip().upper() if device else ""
    grouped: dict[str, list[dict[str, object]]] = {}
    for row in normalized.get("records", []):
        name = str(row.get("device") or "")
        if selected and name != selected:
            continue
        grouped.setdefault(name, []).append(dict(row))
    return {
        "kind": "device_time_series",
        "scope": normalized.get("scope", LOG_REPLAY_SCOPE),
        "devices": {name: grouped[name] for name in sorted(grouped)},
    }


def build_change_points(normalized: dict[str, object], device: str | None = None) -> dict[str, object]:
    series = build_device_series(normalized, device)
    changes: list[dict[str, object]] = []
    for name, rows in series["devices"].items():
        previous: dict[str, object] | None = None
        for row in rows:
            if previous is not None and (
                row.get("value") != previous.get("value")
                or row.get("value_type") != previous.get("value_type")
            ):
                changes.append({
                    "timestamp": row.get("timestamp"),
                    "device": name,
                    "previous_value": previous.get("value"),
                    "value": row.get("value"),
                    "previous_value_type": previous.get("value_type"),
                    "value_type": row.get("value_type"),
                    "source": row.get("source", ""),
                })
            previous = row
    changes.sort(key=lambda row: (_canonical_timestamp(row["timestamp"])[1], str(row["device"])))
    return {
        "kind": "device_change_points",
        "scope": normalized.get("scope", LOG_REPLAY_SCOPE),
        "changes": changes,
    }


def _select_snapshot_timestamp(records: list[dict[str, object]], requested: str, mode: str) -> tuple[str, float]:
    requested_text, requested_dt = _canonical_timestamp(requested)
    available = sorted({str(row["timestamp"]) for row in records}, key=lambda value: _canonical_timestamp(value)[1])
    if not available:
        raise ValueError("captured log has no records")
    if requested_text in available:
        return requested_text, 0.0
    if mode == "exact":
        raise ValueError(f"no records at requested timestamp: {requested_text}; use --mode nearest to select a nearby capture")
    if mode != "nearest":
        raise ValueError(f"unsupported snapshot mode: {mode}")
    # Tie-break toward the earlier timestamp for deterministic replay.
    selected = min(
        available,
        key=lambda value: (
            abs((_canonical_timestamp(value)[1] - requested_dt).total_seconds()),
            _canonical_timestamp(value)[1],
        ),
    )
    distance = abs((_canonical_timestamp(selected)[1] - requested_dt).total_seconds())
    return selected, distance


def build_log_snapshot(
    normalized: dict[str, object],
    requested_timestamp: str,
    *,
    mode: str = "exact",
    project_root: Path | None = None,
) -> dict[str, object]:
    records = list(normalized.get("records", []))
    selected_timestamp, distance = _select_snapshot_timestamp(records, requested_timestamp, mode)
    selected = [row for row in records if str(row.get("timestamp")) == selected_timestamp]
    values = {str(row["device"]): row.get("value") for row in selected}
    value_types = {str(row["device"]): str(row.get("value_type") or "unknown") for row in selected}
    sources = sorted({str(row.get("source") or "") for row in selected if row.get("source")})
    fingerprints = sorted({
        str(row.get("project_fingerprint") or "")
        for row in selected
        if row.get("project_fingerprint")
    })
    supplied_fp = fingerprints[0] if len(fingerprints) == 1 else ""
    warnings: list[str] = []
    if len(fingerprints) > 1:
        warnings.append("selected timestamp contains multiple project_fingerprints; project identity is ambiguous")

    actual_fp = ""
    fingerprint_match: bool | None = None
    if project_root is not None:
        actual_fp = fingerprint(Path(project_root))
        if supplied_fp:
            fingerprint_match = supplied_fp == actual_fp
            if not fingerprint_match:
                warnings.append(
                    "captured log project fingerprint does not match this project: "
                    f"log={short(supplied_fp)} project={short(actual_fp)}"
                )
        elif actual_fp:
            warnings.append("selected timestamp has no single project_fingerprint; project identity could not be verified")

    requested_text, _ = _canonical_timestamp(requested_timestamp)
    return {
        "timestamp": selected_timestamp,
        "source": sources[0] if len(sources) == 1 else "mixed" if sources else "",
        "project_fingerprint": supplied_fp,
        "values": values,
        "metadata": {
            "kind": "captured_log_snapshot",
            "scope": LOG_REPLAY_SCOPE,
            "requested_timestamp": requested_text,
            "selected_timestamp": selected_timestamp,
            "selection_mode": mode,
            "distance_seconds": distance,
            "value_count": len(values),
            "value_types": value_types,
            "sources": sources,
            "project_fingerprints": fingerprints,
            "actual_project_fingerprint": actual_fp,
            "fingerprint_match": fingerprint_match,
            "carry_forward": False,
            "scan_synchronized": False,
            "warnings": warnings,
        },
    }


def _write_replay_result(result: dict[str, object], output: str | None) -> int:
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if output:
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        print(f"written: {path}")
    else:
        print(text)
    return 0


def build_log_replay_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gx3-cli live-read replay",
        description=(
            "Import already-captured CSV/JSON device logs for offline replay. "
            "Ordinary imported/polled data is not treated as scan-synchronized recording."
        )
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("normalize", help="normalize and deterministically de-duplicate a captured log")
    p.add_argument("input", help="CSV or JSON captured log")
    p.add_argument("-o", "--output")

    p = sub.add_parser("series", help="emit per-device time series")
    p.add_argument("input", help="CSV or JSON captured log")
    p.add_argument("--device", default=None, help="optional one-device filter")
    p.add_argument("-o", "--output")

    p = sub.add_parser("changes", help="emit value/type change points")
    p.add_argument("input", help="CSV or JSON captured log")
    p.add_argument("--device", default=None, help="optional one-device filter")
    p.add_argument("-o", "--output")

    p = sub.add_parser("snapshot", help="export live-values-compatible JSON at or nearest a timestamp")
    p.add_argument("input", help="CSV or JSON captured log")
    p.add_argument("--at", required=True, help="requested ISO 8601 timestamp")
    p.add_argument("--mode", choices=("exact", "nearest"), default="exact")
    p.add_argument("--root", default=None, help="optional GX3 project used only to verify project_fingerprint")
    p.add_argument("-o", "--output")
    return parser


def log_replay_main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_log_replay_parser().parse_args(argv)
    try:
        normalized = load_captured_log(Path(args.input))
        if args.command == "normalize":
            result = normalized
        elif args.command == "series":
            result = build_device_series(normalized, args.device)
        elif args.command == "changes":
            result = build_change_points(normalized, args.device)
        elif args.command == "snapshot":
            root = resolve_project_root(args.root) if args.root else None
            result = build_log_snapshot(normalized, args.at, mode=args.mode, project_root=root)
        else:
            raise ValueError(f"unsupported log-replay command: {args.command}")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"gx3 log-replay error: {exc}", file=sys.stderr)
        return 2
    return _write_replay_result(result, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
