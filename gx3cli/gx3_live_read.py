from __future__ import annotations

"""Read current Mitsubishi PLC device values over MC Protocol/SLMP.

This module is intentionally read-only. It implements the common 3E binary
batch-read frame used by many MELSEC Ethernet configurations.
"""

import argparse
import json
import socket
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from gx3cli.gx3_device_name import device_radix, format_device


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


if __name__ == "__main__":
    raise SystemExit(main())
