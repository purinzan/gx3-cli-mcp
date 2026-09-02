from __future__ import annotations

import argparse
import csv
import math
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from gx3cli.gx3_device_name import device_radix


MARKERS: tuple[tuple[str, bytes], ...] = (
    ("pk_local_like", b"PK\x03\x04"),
    ("pk_dir_like", b"PK\x02\x06"),
    ("pj_like", b"PJ\x01\x01"),
    ("base_screen", b"BAS"),
    ("got_model", b"GOT"),
    ("gte_marker", b"GTE"),
    ("png_marker", b"PNG"),
    ("ccv_marker", b"CCV"),
    ("hfd_marker", b"HFD__"),
    ("epb_marker", b"EPB"),
    ("put_marker", b"PUT"),
    ("gtstv_marker", b"GTSTV"),
    ("ginf_marker", b"GINF"),
)

DEVICE_RE = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"(?P<device>(?:SM|SD|SB|SW|ZR|X|Y|M|L|B|D|W|R|T|C)[0-9A-Fa-f]{2,7})"
    r"(?![A-Za-z0-9_])"
)
DEVICE_SPLIT_RE = re.compile(r"^(SM|SD|SB|SW|ZR|X|Y|M|L|B|D|W|R|T|C)([0-9A-Fa-f]+)$")
SCREEN_ID_RE = re.compile(r"\b(?:BAS|GOT|GTE|GTSTV|CCV|HFD__|EPB|PUT)[ -~]{0,40}")
HMI_KEYWORD_RE = re.compile(
    r"PLC|MELSEC|MITSUBISHI|QCPU|RCPU|Recipe|Alarm|Error|Manual|Auto|Start|Stop|"
    r"Ethernet|Screen|Window|Base|Device|Comment|Script|Logging|Recipe|GOT",
    re.IGNORECASE,
)
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]")
CJK_RUN_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff]{3,}")


@dataclass(frozen=True)
class StringHit:
    file: str
    offset: int
    encoding: str
    kind: str
    text: str


@dataclass(frozen=True)
class MarkerHit:
    file: str
    offset: int
    marker: str
    snippet: str


@dataclass(frozen=True)
class DeviceHit:
    file: str
    offset: int
    encoding: str
    device: str
    normalized_device: str
    matched_device: str
    plc_projects: str
    plc_comment: str
    text: str


@dataclass(frozen=True)
class RecordHit:
    file: str
    offset: int
    marker: str
    label_offset: int
    label_kind: str
    label: str
    next_offset: int
    span: int


@dataclass
class FileStats:
    file: str
    size: int
    gt_magic: bool
    header_hex: str
    entropy_head64k: float
    entropy_sample: float
    zero_count: int
    top_bytes: str
    ascii_strings: int
    utf16le_strings: int
    cp932_strings: int
    interesting_strings: int
    device_candidates: int
    plc_matched_devices: int
    record_labels: int
    marker_counts: dict[str, int]


def csv_text(value: object) -> str:
    return "" if value is None else str(value).replace("\r", " ").replace("\n", " ")


def clean_snippet(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text[:limit]


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    total = len(data)
    return -sum((count / total) * math.log2(count / total) for count in counts.values())


def entropy_sample(data: bytes, max_bytes: int = 262_144) -> float:
    if len(data) <= max_bytes:
        return entropy(data)
    step = max(1, len(data) // max_bytes)
    return entropy(data[::step])


def printable_ascii(data: bytes) -> str:
    return "".join(chr(b) if 32 <= b < 127 else " " for b in data)


def iter_ascii_strings(data: bytes, min_len: int) -> list[tuple[int, str]]:
    pattern = re.compile(rb"[\x20-\x7e]{%d,}" % min_len)
    return [(m.start(), m.group().decode("ascii", "replace")) for m in pattern.finditer(data)]


def is_useful_codepoint(code: int) -> bool:
    return (
        code in (9, 10, 13, 0x3000)
        or 0x20 <= code <= 0x7E
        or 0x3040 <= code <= 0x30FF
        or 0x3400 <= code <= 0x9FFF
    )


def iter_utf16le_strings(data: bytes, min_chars: int, max_hits: int | None = None) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    for alignment in (0, 1):
        start: int | None = None
        chars: list[str] = []
        i = alignment
        while i + 1 < len(data):
            code = data[i] | (data[i + 1] << 8)
            if is_useful_codepoint(code):
                if start is None:
                    start = i
                    chars = []
                chars.append(chr(code))
            else:
                if start is not None and len(chars) >= min_chars:
                    text = "".join(chars)
                    if looks_interesting(text):
                        hits.append((start, text))
                        if max_hits and len(hits) >= max_hits:
                            return hits
                start = None
                chars = []
            i += 2
        if start is not None and len(chars) >= min_chars:
            text = "".join(chars)
            if looks_interesting(text):
                hits.append((start, text))
                if max_hits and len(hits) >= max_hits:
                    return hits
    return sorted(hits)


def cp932_char_len(data: bytes, i: int) -> int:
    b = data[i]
    if b in (9, 10, 13) or 0x20 <= b <= 0x7E or 0xA1 <= b <= 0xDF:
        return 1
    if (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFC) and i + 1 < len(data):
        b2 = data[i + 1]
        if 0x40 <= b2 <= 0x7E or 0x80 <= b2 <= 0xFC:
            return 2
    return 0


def iter_cp932_strings(data: bytes, min_chars: int, max_hits: int | None = None) -> list[tuple[int, str]]:
    hits: list[tuple[int, str]] = []
    start: int | None = None
    chunk = bytearray()
    i = 0
    while i < len(data):
        width = cp932_char_len(data, i)
        if width:
            if start is None:
                start = i
                chunk = bytearray()
            chunk.extend(data[i : i + width])
            i += width
            continue
        if start is not None:
            add_cp932_hit(hits, start, bytes(chunk), min_chars)
            if max_hits and len(hits) >= max_hits:
                return hits
        start = None
        chunk = bytearray()
        i += 1
    if start is not None:
        add_cp932_hit(hits, start, bytes(chunk), min_chars)
    return hits[:max_hits] if max_hits else hits


def add_cp932_hit(hits: list[tuple[int, str]], offset: int, data: bytes, min_chars: int) -> None:
    try:
        text = data.decode("cp932")
    except UnicodeDecodeError:
        return
    if len(text) < min_chars:
        return
    if looks_interesting(text):
        hits.append((offset, text))


def looks_interesting(text: str) -> bool:
    if CJK_RUN_RE.search(text):
        return True
    if SCREEN_ID_RE.search(text):
        return True
    if HMI_KEYWORD_RE.search(text):
        return True
    if natural_device_text(text):
        return True
    return False


def natural_device_text(text: str) -> bool:
    matches = list(DEVICE_RE.finditer(text))
    if len(matches) >= 2:
        return True
    if not matches:
        return False
    match = matches[0]
    split = DEVICE_SPLIT_RE.fullmatch(match.group("device").upper())
    if not split:
        return False
    _, number_text = split.groups()
    if len(number_text) < 3:
        return False
    alnum = sum(ch.isalnum() for ch in text)
    symbol = sum(not ch.isalnum() and not ch.isspace() for ch in text)
    return alnum >= symbol and len(text) <= 80


def classify_string(text: str) -> str:
    if CJK_RE.search(text):
        return "jp_text"
    if SCREEN_ID_RE.search(text):
        return "screen_or_gt_marker"
    if HMI_KEYWORD_RE.search(text):
        return "hmi_keyword"
    if DEVICE_RE.search(text):
        return "device_candidate"
    return "text"


def count_marker(data: bytes, marker: bytes) -> int:
    count = 0
    start = 0
    while True:
        pos = data.find(marker, start)
        if pos < 0:
            return count
        count += 1
        start = pos + 1


def iter_marker_hits(data: bytes, file_label: str, max_per_marker: int) -> tuple[dict[str, int], list[MarkerHit]]:
    counts: dict[str, int] = {}
    hits: list[MarkerHit] = []
    for marker_name, marker in MARKERS:
        total = 0
        emitted = 0
        start = 0
        while True:
            pos = data.find(marker, start)
            if pos < 0:
                break
            total += 1
            if emitted < max_per_marker:
                window = data[max(0, pos - 32) : pos + 120]
                hits.append(MarkerHit(file_label, pos, marker_name, clean_snippet(printable_ascii(window))))
                emitted += 1
            start = pos + 1
        counts[marker_name] = total
    return counts, hits


LABEL_PATTERNS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("base_screen", re.compile(rb"BAS[0-9:;<=>?]{2,8}(?:on)?")),
    ("screen_source", re.compile(rb"S[0-9]{5}")),
    ("trigger", re.compile(rb"TGR[0-9]{3}")),
    ("comment_group", re.compile(rb"CMT[A-Z0-9:;<=>?_-]{2,16}")),
    ("image", re.compile(rb"IMG-[A-Z0-9]/[0-9:;<=>?]{1,8}")),
    ("source", re.compile(rb"SRC-[A-Z0-9]/[0-9:;<=>?]{1,8}")),
    ("control", re.compile(rb"C-[A-Z0-9]-[0-9:;<=>?]{1,8}")),
    ("numeric", re.compile(rb"N[0-9]{5}")),
    ("got_model", re.compile(rb"GOT[0-9:;<=>?A-Za-z_-]{2,16}")),
    ("gte_marker", re.compile(rb"GTE[0-9A-Za-z:;<=>?_-]{2,16}")),
    ("gtstv", re.compile(rb"GTSTV")),
)


def label_offset_for_marker(marker_name: str, offset: int) -> int | None:
    if marker_name == "pk_local_like":
        return offset + 32
    if marker_name in {"pk_dir_like", "pj_like"}:
        return offset + 30
    return None


def extract_record_label(data: bytes, marker_name: str, offset: int) -> tuple[int, str, str]:
    label_offset = label_offset_for_marker(marker_name, offset)
    if label_offset is None or label_offset >= len(data):
        return -1, "", ""
    window = data[label_offset : label_offset + 64]
    for kind, pattern in LABEL_PATTERNS:
        match = pattern.match(window)
        if match:
            return label_offset, kind, match.group().decode("ascii", "replace")
    match = re.match(rb"[A-Z0-9][A-Z0-9_+\-/:;<=>?]{2,24}", window)
    if match:
        return label_offset, "unknown_ascii", match.group().decode("ascii", "replace")
    return label_offset, "", ""


def iter_record_hits(data: bytes, file_label: str) -> list[RecordHit]:
    positions: list[tuple[int, str]] = []
    for marker_name, marker in MARKERS:
        if marker_name not in {"pk_local_like", "pk_dir_like", "pj_like"}:
            continue
        start = 0
        while True:
            pos = data.find(marker, start)
            if pos < 0:
                break
            positions.append((pos, marker_name))
            start = pos + 1
    positions.sort()

    rows: list[RecordHit] = []
    for idx, (offset, marker_name) in enumerate(positions):
        label_offset, label_kind, label = extract_record_label(data, marker_name, offset)
        next_offset = positions[idx + 1][0] if idx + 1 < len(positions) else len(data)
        if label:
            rows.append(
                RecordHit(
                    file=file_label,
                    offset=offset,
                    marker=marker_name,
                    label_offset=label_offset,
                    label_kind=label_kind,
                    label=label,
                    next_offset=next_offset,
                    span=max(0, next_offset - offset),
                )
            )
    return rows


def normalize_device(raw: str) -> str:
    match = DEVICE_SPLIT_RE.fullmatch(raw.upper())
    if not match:
        return raw.upper()
    dev_type, number = match.groups()
    return f"{dev_type}{number.upper()}"


def alternate_devices(device: str) -> list[str]:
    match = DEVICE_SPLIT_RE.fullmatch(device.upper())
    if not match:
        return [device.upper()]
    dev_type, number_text = match.groups()
    out = [f"{dev_type}{number_text.upper()}"]
    if device_radix(dev_type) == 16 and re.search(r"[A-Fa-f]", number_text):
        try:
            out.append(f"{dev_type}{int(number_text, 16)}")
        except ValueError:
            pass
    elif device_radix(dev_type) == 16:
        try:
            out.append(f"{dev_type}{int(number_text):X}")
        except ValueError:
            pass
    return list(dict.fromkeys(out))


def load_plc_comment_lookup(index_dir: Path) -> dict[str, list[tuple[str, str]]]:
    lookup: dict[str, list[tuple[str, str]]] = {}
    if not index_dir.exists():
        return lookup
    for db in sorted(index_dir.glob("*.sqlite")):
        try:
            con = sqlite3.connect(str(db))
            rows = con.execute(
                "select device, coalesce(all_text, japanese, english, '') from comments where trim(coalesce(all_text, japanese, english, ''))<>''"
            ).fetchall()
            con.close()
        except sqlite3.Error:
            continue
        project = db.stem
        for device, comment in rows:
            lookup.setdefault(str(device).upper(), []).append((project, str(comment)))
    return lookup


def plc_match(device: str, lookup: dict[str, list[tuple[str, str]]]) -> tuple[str, str, str]:
    for candidate in alternate_devices(device):
        rows = lookup.get(candidate.upper())
        if rows:
            projects = ";".join(dict.fromkeys(project for project, _ in rows[:8]))
            comments = " / ".join(dict.fromkeys(comment for _, comment in rows[:4] if comment))
            return candidate.upper(), projects, comments
    return "", "", ""


def device_hits_from_strings(
    strings: list[StringHit],
    lookup: dict[str, list[tuple[str, str]]],
) -> list[DeviceHit]:
    hits: list[DeviceHit] = []
    seen: set[tuple[str, int, str, str]] = set()
    for row in strings:
        for match in DEVICE_RE.finditer(row.text):
            raw = match.group("device").upper()
            normalized = normalize_device(raw)
            key = (row.file, row.offset + match.start(), row.encoding, normalized)
            if key in seen:
                continue
            seen.add(key)
            matched_device, projects, comment = plc_match(normalized, lookup)
            hits.append(
                DeviceHit(
                    file=row.file,
                    offset=row.offset + match.start(),
                    encoding=row.encoding,
                    device=raw,
                    normalized_device=normalized,
                    matched_device=matched_device,
                    plc_projects=projects,
                    plc_comment=comment,
                    text=clean_snippet(row.text),
                )
            )
    return hits


def collect_strings(
    data: bytes,
    file_label: str,
    min_string: int,
    max_strings: int,
    unicode_strings: bool,
    deep_strings: bool,
    all_strings: bool,
) -> tuple[list[StringHit], dict[str, int]]:
    rows: list[StringHit] = []
    counts = {"ascii": 0, "utf16le": 0, "cp932": 0}

    ascii_rows = iter_ascii_strings(data, min_string)
    counts["ascii"] = len(ascii_rows)
    for offset, text in ascii_rows:
        if all_strings or looks_interesting(text):
            rows.append(StringHit(file_label, offset, "ascii", classify_string(text), clean_snippet(text)))
            if len(rows) >= max_strings:
                return rows, counts

    if unicode_strings:
        utf16_rows = iter_utf16le_strings(data, max(3, min_string // 2), max_hits=max_strings)
        counts["utf16le"] = len(utf16_rows)
        for offset, text in utf16_rows:
            rows.append(StringHit(file_label, offset, "utf16le", classify_string(text), clean_snippet(text)))
            if len(rows) >= max_strings:
                return rows, counts

    if deep_strings:
        cp932_rows = iter_cp932_strings(data, max(3, min_string // 2), max_hits=max_strings)
        counts["cp932"] = len(cp932_rows)
        for offset, text in cp932_rows:
            rows.append(StringHit(file_label, offset, "cp932", classify_string(text), clean_snippet(text)))
            if len(rows) >= max_strings:
                return rows, counts

    return rows, counts


def file_label(path: Path) -> str:
    return path.name


def top_bytes(data: bytes, limit: int = 10) -> str:
    return " ".join(f"{byte:02X}:{count}" for byte, count in Counter(data).most_common(limit))


def analyze_file(
    path: Path,
    args: argparse.Namespace,
    lookup: dict[str, list[tuple[str, str]]],
) -> tuple[FileStats, list[MarkerHit], list[StringHit], list[DeviceHit], list[RecordHit]]:
    data = path.read_bytes()
    label = file_label(path)
    marker_counts, marker_hits = iter_marker_hits(data, label, args.max_marker_hits)
    record_hits = iter_record_hits(data, label)
    string_hits, string_counts = collect_strings(
        data,
        label,
        args.min_string,
        args.max_strings_per_file,
        args.unicode_strings,
        args.deep_strings,
        args.all_strings,
    )
    device_hits = device_hits_from_strings(string_hits, lookup)
    stats = FileStats(
        file=label,
        size=len(data),
        gt_magic=data.startswith(b"GT"),
        header_hex=data[:32].hex(" "),
        entropy_head64k=entropy(data[:65536]),
        entropy_sample=entropy_sample(data),
        zero_count=data.count(0),
        top_bytes=top_bytes(data),
        ascii_strings=string_counts["ascii"],
        utf16le_strings=string_counts["utf16le"],
        cp932_strings=string_counts["cp932"],
        interesting_strings=len(string_hits),
        device_candidates=len(device_hits),
        plc_matched_devices=sum(1 for row in device_hits if row.matched_device),
        record_labels=len(record_hits),
        marker_counts=marker_counts,
    )
    return stats, marker_hits, string_hits, device_hits, record_hits


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_text(row.get(key, "")) for key in fieldnames})


def write_summary(
    path: Path,
    stats_rows: list[FileStats],
    marker_rows: list[MarkerHit],
    string_rows: list[StringHit],
    device_rows: list[DeviceHit],
    record_rows: list[RecordHit],
) -> None:
    total_size = sum(row.size for row in stats_rows)
    visible = [row for row in stats_rows if row.marker_counts.get("base_screen", 0) or row.marker_counts.get("pk_local_like", 0)]
    plc_matches = [row for row in device_rows if row.matched_device]
    lines = [
        "# GTX probe summary",
        "",
        f"Files: {len(stats_rows)}",
        f"Total bytes: {total_size}",
        f"GT magic files: {sum(1 for row in stats_rows if row.gt_magic)}",
        f"Visible marker files: {len(visible)}",
        f"Interesting strings: {len(string_rows)}",
        f"Device candidates: {len(device_rows)}",
        f"PLC comment matches: {len(plc_matches)}",
        f"Record labels: {len(record_rows)}",
        "",
        "## Per-file signal",
        "",
        "| file | size | entropy | BAS | PK-like | records | strings | devices | PLC matches |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in stats_rows:
        lines.append(
            f"| {row.file} | {row.size} | {row.entropy_sample:.3f} | "
            f"{row.marker_counts.get('base_screen', 0)} | {row.marker_counts.get('pk_local_like', 0)} | "
            f"{row.record_labels} | {row.interesting_strings} | {row.device_candidates} | {row.plc_matched_devices} |"
        )
    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- GTX files in this workspace start with `GT`, not the ZIP `PK` header used by GX3 files.",
            "- `PK-like` and `BAS` markers are treated as GT Designer3 internal record signals, not as valid ZIP archives.",
            "- Use the CSV files next to this summary for offsets and context snippets.",
        ]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def summarize_matched_devices(device_rows: list[DeviceHit]) -> list[dict[str, object]]:
    grouped: dict[str, list[DeviceHit]] = {}
    for row in device_rows:
        if row.matched_device:
            grouped.setdefault(row.matched_device, []).append(row)

    out: list[dict[str, object]] = []
    for device, rows in sorted(grouped.items()):
        projects: list[str] = []
        comments: list[str] = []
        for row in rows:
            projects.extend(part for part in row.plc_projects.split(";") if part)
            if row.plc_comment:
                comments.append(row.plc_comment)
        out.append(
            {
                "matched_device": device,
                "occurrences": len(rows),
                "gtx_files": ";".join(dict.fromkeys(row.file for row in rows)),
                "offsets": ";".join(str(row.offset) for row in rows[:12]),
                "raw_devices": ";".join(dict.fromkeys(row.device for row in rows)),
                "plc_projects": ";".join(dict.fromkeys(projects)),
                "plc_comment": " / ".join(dict.fromkeys(comments[:3])),
                "confidence": "medium",
                "note": "ASCII GTX fragment matched to GX3 comment DB; verify in GT Designer3 before field changes",
            }
        )
    return out


def resolve_paths(values: list[str]) -> list[Path]:
    if not values:
        return sorted(Path(".").glob("*.GTX"))
    out: list[Path] = []
    for value in values:
        path = Path(value)
        if path.is_dir():
            out.extend(sorted(path.glob("*.GTX")))
        else:
            matches = sorted(Path(".").glob(value)) if any(ch in value for ch in "*?[]") else [path]
            out.extend(matches)
    return list(dict.fromkeys(p for p in out if p.exists()))


def scan(args: argparse.Namespace) -> int:
    paths = resolve_paths(args.paths)
    if not paths:
        print("no GTX files found", file=sys.stderr)
        return 2

    lookup = load_plc_comment_lookup(Path(args.gx3_index_dir)) if args.gx3_index_dir else {}
    stats_rows: list[FileStats] = []
    marker_rows: list[MarkerHit] = []
    string_rows: list[StringHit] = []
    device_rows: list[DeviceHit] = []
    record_rows: list[RecordHit] = []

    for path in paths:
        stats, markers, strings, devices, records = analyze_file(path, args, lookup)
        stats_rows.append(stats)
        marker_rows.extend(markers)
        string_rows.extend(strings)
        device_rows.extend(devices)
        record_rows.extend(records)
        print(
            f"{path.name}: size={stats.size} entropy={stats.entropy_sample:.3f} "
            f"BAS={stats.marker_counts.get('base_screen', 0)} PK-like={stats.marker_counts.get('pk_local_like', 0)} "
            f"records={stats.record_labels} strings={stats.interesting_strings} "
            f"devices={stats.device_candidates} plc_matches={stats.plc_matched_devices}"
        )

    out_dir = Path(args.output_dir)
    prefix = args.prefix

    inventory_fields = [
        "file",
        "size",
        "gt_magic",
        "header_hex",
        "entropy_head64k",
        "entropy_sample",
        "zero_count",
        "top_bytes",
        "ascii_strings",
        "utf16le_strings",
        "cp932_strings",
        "interesting_strings",
        "device_candidates",
        "plc_matched_devices",
        "record_labels",
        *[name for name, _ in MARKERS],
    ]
    write_csv(
        out_dir / f"{prefix}_inventory.csv",
        inventory_fields,
        [
            {
                "file": row.file,
                "size": row.size,
                "gt_magic": int(row.gt_magic),
                "header_hex": row.header_hex,
                "entropy_head64k": f"{row.entropy_head64k:.6f}",
                "entropy_sample": f"{row.entropy_sample:.6f}",
                "zero_count": row.zero_count,
                "top_bytes": row.top_bytes,
                "ascii_strings": row.ascii_strings,
                "utf16le_strings": row.utf16le_strings,
                "cp932_strings": row.cp932_strings,
                "interesting_strings": row.interesting_strings,
                "device_candidates": row.device_candidates,
                "plc_matched_devices": row.plc_matched_devices,
                "record_labels": row.record_labels,
                **row.marker_counts,
            }
            for row in stats_rows
        ],
    )
    write_csv(
        out_dir / f"{prefix}_markers.csv",
        ["file", "offset", "marker", "snippet"],
        [row.__dict__ for row in marker_rows],
    )
    write_csv(
        out_dir / f"{prefix}_records.csv",
        ["file", "offset", "marker", "label_offset", "label_kind", "label", "next_offset", "span"],
        [row.__dict__ for row in record_rows],
    )
    write_csv(
        out_dir / f"{prefix}_strings.csv",
        ["file", "offset", "encoding", "kind", "text"],
        [row.__dict__ for row in string_rows],
    )
    write_csv(
        out_dir / f"{prefix}_device_candidates.csv",
        [
            "file",
            "offset",
            "encoding",
            "device",
            "normalized_device",
            "matched_device",
            "plc_projects",
            "plc_comment",
            "text",
        ],
        [row.__dict__ for row in device_rows],
    )
    write_csv(
        out_dir / f"{prefix}_matched_devices.csv",
        [
            "matched_device",
            "occurrences",
            "gtx_files",
            "offsets",
            "raw_devices",
            "plc_projects",
            "plc_comment",
            "confidence",
            "note",
        ],
        summarize_matched_devices(device_rows),
    )
    write_summary(out_dir / f"{prefix}_summary.md", stats_rows, marker_rows, string_rows, device_rows, record_rows)
    print(f"wrote {out_dir / f'{prefix}_summary.md'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Probe GT Designer3 GTX single-file HMI projects")
    sub = parser.add_subparsers(dest="command")
    scan_parser = sub.add_parser("scan", help="scan GTX files and emit CSV summaries")
    scan_parser.add_argument("paths", nargs="*", help="GTX files, folders, or globs. Defaults to *.GTX")
    scan_parser.add_argument("--output-dir", default="outputs", help="output directory")
    scan_parser.add_argument("--prefix", default="gtx_probe", help="output filename prefix")
    scan_parser.add_argument("--gx3-index-dir", default=".gx3_index", help="GX3 index directory for comment matching")
    scan_parser.add_argument("--min-string", type=int, default=6, help="minimum ASCII string length")
    scan_parser.add_argument("--max-strings-per-file", type=int, default=5000, help="cap interesting strings per file")
    scan_parser.add_argument("--max-marker-hits", type=int, default=500, help="cap marker context rows per marker per file")
    scan_parser.add_argument("--unicode-strings", action="store_true", help="also scan UTF-16LE strings (can produce false positives on encoded GTX data)")
    scan_parser.add_argument("--deep-strings", action="store_true", help="also scan CP932/Japanese byte runs")
    scan_parser.add_argument("--all-strings", action="store_true", help="write all ASCII strings, including low-signal runs")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in (None, "scan"):
        if args.command is None:
            args = parser.parse_args(["scan", *(argv or [])])
        return scan(args)
    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
