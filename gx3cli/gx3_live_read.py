from __future__ import annotations

"""Read current Mitsubishi PLC values and explain captured snapshots.

The network reader is intentionally read-only. Snapshot explanation is a
separate offline path: it consumes an already-captured JSON file, combines it
with ``trace-device --strict-logic``, and never opens a PLC connection.
"""

import argparse
import json
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
    parser = argparse.ArgumentParser(description="Read current PLC device values over MC Protocol/SLMP 3E binary.")
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


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
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
        return result
    if not device or role not in {"a", "b"}:
        result["reason"] = "contact identity/role is unresolved"
        return result
    overlay = live_overlay_for_devices([{"device": device, "role": role}], values)[0]
    result.update({key: value for key, value in overlay.items() if key != "role"})
    if "value" not in overlay:
        result["condition"] = "missing"
        result["reason"] = "snapshot has no value for this device"
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
        }]
    if op == "too_large":
        return "unknown", [{
            "kind": "too_large",
            "condition": "unknown",
            "reason": "static condition was capped before a complete logic tree was produced",
        }]
    return "unknown", [{
        "kind": op or "unknown",
        "condition": "unknown",
        "opcode": node.get("opcode", ""),
        "position": node.get("position", ""),
        "reason": "unsupported/unknown logic is not guessed",
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
    if guards:
        warnings.append("unresolved execution guard means this row may not execute")
        state = "unknown"
    if str(row.get("parse_status", "")) != "exact":
        warnings.append("row was not fully decoded")
        state = "unknown"
    if int((row.get("logic_stats") or {}).get("too_large", 0) or 0) > 0:
        warnings.append("strict logic was capped as too large")
        state = "unknown"
    if temporal:
        warnings.append("stateful/temporal semantics are present; snapshot evaluates the current write/enable condition only")

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
            lines.append(f"  - {str(leaf.get('condition', 'unknown')):<7} {device}{value}{reason}")
        for warning in row.get("warnings", []):
            lines.append(f"  NOTE: {warning}")
    return "\n".join(lines)


def build_snapshot_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Explain strict traced ladder conditions against one captured device snapshot.")
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


if __name__ == "__main__":
    raise SystemExit(main())
