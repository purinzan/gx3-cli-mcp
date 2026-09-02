from __future__ import annotations

"""Probe GX Works3 ``*.w3pa`` binary parameter files.

Existing communication tools decode the high-value RJ61BT11 refresh areas.
This helper keeps the lower-level evidence visible: UTF-16 strings, likely
device starts, the size word used by known refresh records, module names, IPs,
and section markers.  It is intentionally conservative and read-only.
"""

import argparse
import csv
import json
import re
import struct
import sys
from collections import Counter
from pathlib import Path

from gx3cli.extract_comm_refresh_areas import iter_utf16_strings_any_alignment, read_size_after_device_string
from gx3cli.gx3_project_paths import default_output_prefix, default_project_root
from gx3cli.gx3_device_name import device_radix


DEVICE_RE = re.compile(r"^(?:SB|SW|SM|SD|ZR|X|Y|W|B|D|M|L|R|F|V|TC|TS|TN|CN)[0-9A-F]+$")
MODULE_RE = re.compile(
    r"^(?:R\d|RJ\d|RD\d|RX\d|RY\d|AJ\d|GT\d|ENCPU|RCPU|MemoryCard|DEVSTORE|EVENT|EthernetPort|CommIfSection\d|SystemParam\w*|RemotePassword\w*)"
)
IP_RE = re.compile(r"(?<!\d)(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?!\d)")
SECTION_WORDS = (
    "Ethernet",
    "SLMP",
    "CCIEF",
    "CC-Link",
    "通信",
    "リモート",
    "自局",
    "SystemParam",
)


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def u32_at(data: bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0] if offset + 4 <= len(data) else 0


def looks_useful(text: str) -> bool:
    if not text or len(text) > 120:
        return False
    if DEVICE_RE.fullmatch(text) or MODULE_RE.match(text) or IP_RE.search(text):
        return True
    if any(word in text for word in SECTION_WORDS):
        return True
    if re.fullmatch(r"[0-9A-Za-z_./()+-]{2,64}", text):
        return True
    return False


def classify_text(text: str) -> str:
    if IP_RE.search(text):
        return "ip"
    if MODULE_RE.match(text):
        return "module_or_section"
    if DEVICE_RE.fullmatch(text):
        return "device"
    if any(word in text for word in SECTION_WORDS):
        return "section"
    if re.fullmatch(r"[0-9A-Za-z_./()+-]{2,64}", text):
        return "ascii_identifier"
    return "other"


def end_device(start: str, count: int) -> str:
    match = re.fullmatch(r"([A-Z]+)([0-9A-F]+)", start)
    if not match or count <= 0:
        return ""
    prefix, raw = match.groups()
    base = device_radix(prefix)
    try:
        value = int(raw, base)
    except ValueError:
        return ""
    end_value = value + count - 1
    if base == 16:
        return f"{prefix}{end_value:0{len(raw)}X}"
    return f"{prefix}{end_value:0{len(raw)}d}"


def probe_file(path: Path, root: Path) -> tuple[dict[str, object], list[dict[str, object]], list[dict[str, object]]]:
    data = path.read_bytes()
    all_strings = iter_utf16_strings_any_alignment(path)
    useful: list[dict[str, object]] = []
    seen: set[tuple[int, str]] = set()
    for offset, text in all_strings:
        if (offset, text) in seen or not looks_useful(text):
            continue
        seen.add((offset, text))
        category = classify_text(text)
        useful.append(
            {
                "w3pa": rel(path, root),
                "offset_hex": f"0x{offset:X}",
                "offset": offset,
                "category": category,
                "text": text,
            }
        )

    devices: list[dict[str, object]] = []
    for row in useful:
        if row["category"] != "device":
            continue
        device = str(row["text"])
        offset = int(row["offset"])
        size = read_size_after_device_string(path, offset, device)
        devices.append(
            {
                "w3pa": row["w3pa"],
                "offset_hex": row["offset_hex"],
                "device_start": device,
                "points_or_words_after_string": size,
                "device_end_guess": end_device(device, size),
                "confidence": "high_for_known_refresh_record" if size else "string_only",
            }
        )

    categories = Counter(str(row["category"]) for row in useful)
    modules = sorted({str(row["text"]) for row in useful if row["category"] in {"module_or_section", "section"}})
    ips = sorted({m.group(0) for _, text in all_strings for m in IP_RE.finditer(text)})
    device_prefixes = Counter(re.match(r"[A-Z]+", str(d["device_start"])).group(0) for d in devices if re.match(r"[A-Z]+", str(d["device_start"])))
    summary = {
        "w3pa": rel(path, root),
        "size": len(data),
        "header_u32_0_hex": f"0x{u32_at(data, 0):08X}" if len(data) >= 4 else "",
        "header_u32_1_hex": f"0x{u32_at(data, 4):08X}" if len(data) >= 8 else "",
        "raw_utf16_strings": len(all_strings),
        "useful_strings": len(useful),
        "device_strings": categories.get("device", 0),
        "module_or_section_strings": categories.get("module_or_section", 0) + categories.get("section", 0),
        "ip_strings": len(ips),
        "device_prefix_counts": "; ".join(f"{k}:{v}" for k, v in sorted(device_prefixes.items())),
        "modules_or_sections": " / ".join(modules[:20]),
        "ip_values": " / ".join(ips),
    }
    return summary, useful, devices


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                fields.append(key)
                seen.add(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Probe *.w3pa parameter files for strings and device evidence.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    paths = sorted(root.glob("*.w3pa"))
    if not paths:
        raise SystemExit(f"no *.w3pa files found under: {root}")

    summaries: list[dict[str, object]] = []
    strings: list[dict[str, object]] = []
    devices: list[dict[str, object]] = []
    for path in paths:
        summary, useful, devs = probe_file(path, root)
        summaries.append(summary)
        strings.extend(useful)
        devices.extend(devs)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    prefix = args.prefix or default_output_prefix("w3pa")
    base = out_dir / prefix
    write_csv(base.with_name(base.name + "_summary.csv"), summaries)
    write_csv(base.with_name(base.name + "_strings.csv"), strings)
    write_csv(base.with_name(base.name + "_devices.csv"), devices)

    top = {
        "root": str(root),
        "w3pa_files": len(paths),
        "useful_strings": len(strings),
        "device_strings": len(devices),
        "files_with_devices": sum(1 for row in summaries if int(row["device_strings"]) > 0),
        "files_with_ip_strings": sum(1 for row in summaries if int(row["ip_strings"]) > 0),
    }
    (base.with_name(base.name + "_summary.json")).write_text(
        json.dumps(top, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(top, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
