from __future__ import annotations

import argparse
import csv
import hashlib
import json
import lzma
import math
import re
import sqlite3
import struct
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from gx3cli.gx3_device_name import format_device as _format_device
from gx3cli.gx3_index_lite import default_db_path, default_project_label
from gx3cli.gx3_ladder_diagram import output_label, render_row_diagram
from gx3cli.gx3_ladder_logic import enable_logic_for_output, logic_to_text, normalize_device, output_elements_for, positioned_elements
from gx3cli.gx3_project_paths import default_project_root, find_comment_db
from gx3cli.review_gx3_project import LadderRow, comment_for_device, load_comments_for_root, load_rows


IP_RE = re.compile(r"(?<!\d)(?:25[0-5]|2[0-4]\d|1?\d?\d)(?:\.(?:25[0-5]|2[0-4]\d|1?\d?\d)){3}(?!\d)")


def is_sqlite(path: Path) -> bool:
    try:
        return path.read_bytes()[:16] == b"SQLite format 3\0"
    except OSError:
        return False


def open_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    return con


def entropy(data: bytes) -> float:
    if not data:
        return 0.0
    counts = Counter(data)
    n = len(data)
    return -sum((count / n) * math.log2(count / n) for count in counts.values())


def parse_sourceinfo_outer(path: Path) -> list[tuple[str, bytes]]:
    data = lzma.decompress(path.read_bytes(), format=lzma.FORMAT_ALONE)
    out: list[tuple[str, bytes]] = []
    off = 0
    while off < len(data):
        if data[off] != 1 or off + 6 > len(data):
            break
        char_count = data[off + 1]
        name_len = (char_count - 1) * 2
        name = data[off + 2 : off + 2 + name_len].decode("utf-16le", "replace")
        pos = off + 2 + name_len + 3
        if pos + 4 > len(data):
            break
        payload_len = int.from_bytes(data[pos : pos + 4], "little")
        payload = data[pos + 4 : pos + 4 + payload_len]
        out.append((name, payload))
        off = pos + 4 + payload_len
    return out


def parse_inner_container(payload: bytes) -> list[tuple[str, bytes]]:
    try:
        payload = lzma.decompress(payload, format=lzma.FORMAT_ALONE)
    except lzma.LZMAError:
        pass
    if not payload or payload[0] != 1:
        return [("", payload)]
    out: list[tuple[str, bytes]] = []
    off = 0
    while off < len(payload):
        if payload[off] != 1 or off + 6 > len(payload):
            break
        char_count = payload[off + 1]
        name_len = (char_count - 1) * 2
        name = payload[off + 2 : off + 2 + name_len].decode("utf-16le", "replace")
        pos = off + 2 + name_len + 3
        if pos + 4 > len(payload):
            break
        body_len = int.from_bytes(payload[pos : pos + 4], "little")
        body = payload[pos + 4 : pos + 4 + body_len]
        out.append((name, body))
        off = pos + 4 + body_len
    return out or [("", payload)]


def text_of_payload(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "utf-16le", "cp932"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            pass
    return payload.decode("utf-8", "replace")


def command_sourceinfo(args: argparse.Namespace) -> int:
    root = Path(args.root)
    cab = Path(args.cab) if args.cab else root / "SourceInfo.CAB"
    if not cab.exists():
        print(f"SourceInfo.CAB not found: {cab}", file=sys.stderr)
        return 2
    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
    for idx, (entry_name, payload) in enumerate(parse_sourceinfo_outer(cab)):
        inners = parse_inner_container(payload)
        print(f"[{idx}] {entry_name} payload={len(payload)} inner={len(inners)}")
        for inner_name, body in inners:
            label = inner_name or entry_name
            text = text_of_payload(body)
            if args.name and args.name.lower() not in label.lower() and args.name.lower() not in entry_name.lower():
                continue
            print(f"\n--- {label} ({len(body)} bytes) ---")
            print(text if args.full else "\n".join(text.splitlines()[: args.lines]))
            if out_dir:
                safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", f"{idx:02d}_{label}").strip("_")
                (out_dir / safe).write_bytes(body)
    return 0


def sourceinfo_values(root: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    cab = root / "SourceInfo.CAB"
    if not cab.exists():
        return values
    for entry_name, payload in parse_sourceinfo_outer(cab):
        for inner_name, body in parse_inner_container(payload):
            text = text_of_payload(body)
            if inner_name.endswith(".val") or entry_name.endswith("VerCompressed"):
                for line in text.splitlines():
                    if "\t" in line:
                        key, value = line.split("\t", 1)
                        values[key] = value
    return values


def command_version(args: argparse.Namespace) -> int:
    root = Path(args.root)
    vals = sourceinfo_values(root)
    fields = [
        ("Create", "CreateVer:Major", "CreateVer:Minor"),
        ("NewestSave", "NewestSaveVer:Major", "NewestSaveVer:Minor"),
        ("OldestSave", "OldestSaveVer:Major", "OldestSaveVer:Minor"),
        ("LastConvert", "LastConvertVer:Major", "LastConvertVer:Minor"),
        ("PCWrite", "PCWriteVer:Major", "PCWriteVer:Minor"),
    ]
    for label, major_key, minor_key in fields:
        major = vals.get(major_key, "")
        minor = vals.get(minor_key, "")
        if major or minor:
            print(f"{label}: {major}.{minor}" if major else f"{label}: {minor}")
    p = root / "!ProjectExInfo.dat"
    if p.exists():
        strings = re.findall(rb"(?:[\x20-\x7e]\x00){2,}", p.read_bytes())
        decoded = [s.decode("utf-16le", "replace") for s in strings]
        if decoded:
            print("ProjectExInfo:", ", ".join(decoded))
    return 0


def command_inspect(args: argparse.Namespace) -> int:
    root = Path(args.root)
    rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = str(path.relative_to(root))
        data = path.read_bytes()
        kind = "binary"
        detail = ""
        if path.name == "_Project.txc":
            kind = "encrypted/high-entropy project body"
            detail = f"entropy={entropy(data):.3f}"
        elif is_sqlite(path):
            kind = "sqlite"
            con = open_db(path)
            tables = [r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]
            con.close()
            detail = ",".join(tables[:6])
        elif path.suffix.lower() in {".xml", ".txt", ".version"}:
            kind = "plain-text"
        elif path.name == "SourceInfo.CAB":
            kind = "lzma SourceInfo container"
            try:
                detail = ",".join(name for name, _ in parse_sourceinfo_outer(path))
            except Exception as exc:
                detail = f"parse_error={exc}"
        elif path.suffix.lower() in {".prm", ".w3pa", ".dat"}:
            kind = "structured binary"
            detail = f"entropy={entropy(data):.3f}"
        rows.append({"file": rel, "size": len(data), "kind": kind, "detail": detail})
    print_table(rows, ["file", "size", "kind", "detail"], args.limit)
    return 0


def print_table(rows: list[dict] | list[sqlite3.Row], fields: list[str], limit: int | None = None) -> None:
    out = [dict(r) for r in (rows[:limit] if limit else rows)]
    if not out:
        print("no rows")
        return
    widths = {f: max(len(f), *(len(str(r.get(f, ""))) for r in out)) for f in fields}
    print("  ".join(f"{f:<{widths[f]}}" for f in fields))
    print("  ".join("-" * widths[f] for f in fields))
    for r in out:
        print("  ".join(f"{str(r.get(f, '')):<{widths[f]}}" for f in fields))


def command_query_instruction(args: argparse.Namespace) -> int:
    root = Path(args.root)
    rows = []
    needle = f"%{args.text}%"
    for db in sorted(root.glob("*_LDDB.db")):
        con = open_db(db)
        for r in con.execute(
            "select pos, blocktype, rowsize, substr(data,1,?) as data from LadderBlocks where data like ? order by pos",
            (args.data_chars, needle),
        ):
            rows.append({"lddb": db.name, "pos": r["pos"], "blocktype": r["blocktype"], "rowsize": r["rowsize"], "data": r["data"]})
        con.close()
    if args.json:
        print(json.dumps(rows[: args.limit], ensure_ascii=False, indent=2))
    else:
        print_table(rows, ["lddb", "pos", "blocktype", "rowsize", "data"], args.limit)
    return 0


DRIVER_ROLES = {"c", "SET", "RST", "PLS", "PLF", "OUT__16", "OUTH__16"}


def load_ladder_context(root_text: str) -> tuple[Path, dict, list[LadderRow]]:
    root = Path(root_text)
    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)
    rows.sort(key=lambda r: (r.lddb, int(r.pos)))
    return root, comments, rows


def row_outputs(row: LadderRow) -> list[str]:
    return [output_label(el) for el in positioned_elements(row) if el.is_driver]


def row_conditions(row: LadderRow) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for el in positioned_elements(row):
        if not el.is_condition:
            continue
        for ref in el.devices:
            label = ref.display
            if el.role == "b":
                label = "/" + label
            elif el.ct_code == "p":
                label = "P " + label
            if label not in seen:
                seen.add(label)
                out.append(label)
    return out


def compact(value: str, limit: int = 80) -> str:
    text = " ".join((value or "").split())
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def device_comment_text(device: str, comments: dict) -> str:
    try:
        dev_type, number = re.match(r"^([A-Z]+)(-?\d+)$", device, re.I).groups()
        return comment_for_device(dev_type.upper(), int(number), comments)
    except Exception:
        return ""


def command_block_context(args: argparse.Namespace) -> int:
    _, comments, rows = load_ladder_context(args.root)
    target = normalize_device(args.device)
    by_lddb: dict[str, list[LadderRow]] = defaultdict(list)
    for row in rows:
        by_lddb[row.lddb].append(row)
    matches = [(row.lddb, idx) for row in rows for idx, _ in []]
    matches = []
    for lddb, group in by_lddb.items():
        for i, row in enumerate(group):
            if any(occ.device == target for occ in row.occurrences):
                matches.append((lddb, i))
    if not matches:
        print(f"no rows found for {target}")
        return 1
    for lddb, idx in matches[: args.limit]:
        group = by_lddb[lddb]
        start = max(0, idx - args.around)
        end = min(len(group), idx + args.around + 1)
        print(f"\n## {target} context {lddb}:{group[idx].pos} {group[idx].title}")
        out_rows = []
        for j in range(start, end):
            row = group[j]
            out_rows.append(
                {
                    "mark": ">" if j == idx else "",
                    "pos": row.pos,
                    "title": compact(row.title, 34),
                    "outputs": compact(", ".join(row_outputs(row)), 48),
                    "conditions": compact(", ".join(row_conditions(row)), 64),
                }
            )
        print_table(out_rows, ["mark", "pos", "title", "outputs", "conditions"], None)
        if args.diagram:
            for line in render_row_diagram(group[idx]):
                print(line)
    return 0


def command_same_row(args: argparse.Namespace) -> int:
    _, comments, rows = load_ladder_context(args.root)
    target = normalize_device(args.device)
    matches = [row for row in rows if any(occ.device == target for occ in row.occurrences)]
    if not matches:
        print(f"no rows found for {target}")
        return 1
    for row in matches[: args.limit]:
        print(f"\n## {row.lddb}:{row.pos} {row.title}")
        outputs = row_outputs(row)
        conditions = row_conditions(row)
        print("outputs: " + (", ".join(outputs) if outputs else "-"))
        print("conditions: " + (", ".join(conditions) if conditions else "-"))
        if args.comments:
            for dev in sorted({occ.device for occ in row.occurrences}):
                comment = device_comment_text(dev, comments)
                if comment:
                    print(f"  {dev}: {comment}")
        if args.diagram:
            for line in render_row_diagram(row):
                print(line)
    return 0


def command_signal_classify(args: argparse.Namespace) -> int:
    _, comments, rows = load_ladder_context(args.root)
    target = normalize_device(args.device)
    driver_rows = [row for row in rows if output_elements_for(row, target)]
    condition_rows = [row for row in rows if any(occ.device == target and occ.role in {"a", "b"} for occ in row.occurrences)]
    text = " ".join([device_comment_text(target, comments), *[row.data for row in driver_rows]]).lower()
    traits = []
    if re.search(r"\bp\s+[a-z]+|p\{", text) or any(f"P {target}" in " ".join(row_conditions(row)) for row in driver_rows):
        traits.append("pulse/edge-driven")
    if any(target in row_conditions(row) for row in driver_rows):
        traits.append("self-hold")
    if re.search(r"mode|in payment|in .*mode|during|operation|運転中|モード", text):
        traits.append("state/mode")
    if re.search(r"completed|complete|完了|end condition|終了", text):
        traits.append("completion")
    if re.search(r"command|start|pls|button|pb|指令|起動", text):
        traits.append("command")
    if not traits:
        traits.append("unknown")
    rows_out = [
        {
            "device": target,
            "comment": device_comment_text(target, comments),
            "traits": ", ".join(traits),
            "driver_rows": len(driver_rows),
            "condition_rows": len(condition_rows),
        }
    ]
    print_table(rows_out, ["device", "comment", "traits", "driver_rows", "condition_rows"], None)
    for row in driver_rows[: args.limit]:
        outputs = output_elements_for(row, target)
        logic = " OR ".join(logic_to_text(enable_logic_for_output(row, out)) for out in outputs)
        print(f"- {row.lddb}:{row.pos} {row.title} logic={logic}")
    return 0


def command_impact_add_nc(args: argparse.Namespace) -> int:
    _, comments, rows = load_ladder_context(args.root)
    target = normalize_device(args.to)
    contact = normalize_device(args.contact)
    driver_rows = [row for row in rows if output_elements_for(row, target)]
    if not driver_rows:
        print(f"no driver rows found for {target}")
        return 1
    print(f"Proposed NC contact: /{contact} added to {target} driver condition")
    for row in driver_rows[: args.limit]:
        outputs = output_elements_for(row, target)
        print(f"\n## {row.lddb}:{row.pos} {row.title}")
        for out in outputs:
            logic = logic_to_text(enable_logic_for_output(row, out))
            print(f"original: {logic}")
            print(f"modified: ({logic}) AND [/{contact}]")
        print("same-row outputs: " + ", ".join(row_outputs(row)))
    affected = []
    for row in rows:
        if any(occ.device == target and occ.role in {"a", "b"} for occ in row.occurrences):
            affected.append(
                {
                    "lddb": row.lddb,
                    "pos": row.pos,
                    "title": compact(row.title, 32),
                    "outputs": compact(", ".join(row_outputs(row)), 60),
                }
            )
    print(f"\nDownstream rows that use {target} as a contact:")
    print_table(affected, ["lddb", "pos", "title", "outputs"], args.downstream_limit)
    comment = device_comment_text(contact, comments)
    if comment:
        print(f"\n/{contact}: {comment}")
    return 0


def command_state_chain(args: argparse.Namespace) -> int:
    root = Path(args.root)
    db = Path(args.db or default_db_path(root))
    if db.exists():
        con = open_db(db)
        rows = con.execute(
            """
            select device, comment, occurrences, driver_rows, condition_uses
            from devices
            where comment like ? or device like ?
            order by driver_rows desc, occurrences desc
            limit ?
            """,
            (f"%{args.text}%", f"%{args.text.upper()}%", args.limit),
        ).fetchall()
        print_table([dict(r) for r in rows], ["device", "comment", "occurrences", "driver_rows", "condition_uses"], None)
        con.close()
        return 0
    _, comments, _rows = load_ladder_context(args.root)
    found = []
    for (dev_type, number), info in comments.items():
        text = f"{info.japanese} {info.english} {info.all_text}"
        if args.text.lower() in text.lower():
            dev = _format_device(dev_type, number)
            found.append({"device": dev, "comment": compact(text, 120)})
    print_table(found, ["device", "comment"], args.limit)
    return 0


def load_unit_maps(root: Path) -> tuple[dict[int, dict], list[dict]]:
    con = open_db(root / "UnitConfig.dat")
    objs = {r["ObjectID"]: dict(r) for r in con.execute("select * from Object")}
    units = [dict(r) for r in con.execute("select * from Unit")]
    con.close()
    return objs, units


def command_ip_map(args: argparse.Namespace) -> int:
    root = Path(args.root)
    objs, units = load_unit_maps(root)
    rows = []
    for db in sorted(root.glob("*.db")):
        if db.name.endswith(("_LDDB.db", "_MilDB.db", "_StepInfo.db", "_DC.db", "_DM.db")) or db.name == "LabelData.db":
            continue
        try:
            con = open_db(db)
        except sqlite3.Error:
            continue
        deviceinfo: dict[str, str] = {}
        hits: list[tuple[str, str, str]] = []
        for trow in con.execute("select name from sqlite_master where type='table'"):
            table = trow[0]
            cols = [r[1] for r in con.execute(f'pragma table_info("{table}")')]
            if {"Label", "Data"}.issubset(cols):
                for r in con.execute(f'select Label, Data from "{table}" where Data is not null'):
                    value = str(r["Data"])
                    if table == "DeviceInfo":
                        deviceinfo[str(r["Label"])] = value
                    for ip in IP_RE.findall(value):
                        hits.append((ip, table, str(r["Label"])))
        con.close()
        if not hits:
            continue
        head = int(deviceinfo.get("_HeadIO", "-1")) if deviceinfo.get("_HeadIO", "").isdigit() else None
        matches = [u for u in units if head is not None and u.get("IONumber") == head]
        for ip, table, label in hits:
            for unit in matches or [None]:
                obj = objs.get(unit["ObjectID"], {}) if unit else {}
                rows.append(
                    {
                        "ip": ip,
                        "role": "subnet_mask" if ip.startswith("255.") else ("gateway" if ip.endswith(".254") else "node_address"),
                        "unit_object_id": "" if not unit else unit["ObjectID"],
                        "unit_name": obj.get("ObjectName", ""),
                        "base_no": deviceinfo.get("_BaseNo", ""),
                        "slot_no": deviceinfo.get("_SlotNo", ""),
                        "head_io_dec": "" if head is None else head,
                        "head_io_hex": "" if head is None else hex(head),
                        "parameter_db": db.name,
                        "table": table,
                        "label": label,
                    }
                )
    write_or_print(rows, args.out, ["ip", "role", "unit_object_id", "unit_name", "base_no", "slot_no", "head_io_dec", "head_io_hex", "parameter_db", "table", "label"])
    return 0


def load_index_comments(db: Path) -> sqlite3.Connection:
    if not db.exists():
        raise SystemExit(f"index db not found: {db}")
    return open_db(db)


def load_d_words(root: Path) -> dict[int, tuple[int, int, str]]:
    values: dict[int, tuple[int, int, str]] = {}
    for db in root.glob("*_DM.db"):
        con = open_db(db)
        for r in con.execute("select DevNo, MemData from MEMORY_DATA where DevCode=32"):
            base = r["DevNo"]
            data = r["MemData"] or b""
            for i in range(len(data) // 2):
                values[base + i] = (struct.unpack_from("<h", data, i * 2)[0], struct.unpack_from("<H", data, i * 2)[0], db.name)
        con.close()
    return values


def dword32(values: dict[int, tuple[int, int, str]], addr: int) -> tuple[int, str]:
    lo = values.get(addr, (0, 0, ""))[1]
    hi = values.get(addr + 1, (0, 0, ""))[1]
    value = lo | (hi << 16)
    if value >= 0x80000000:
        value -= 0x100000000
    return value, values.get(addr, values.get(addr + 1, (0, 0, "")))[2]


def command_scon_map(args: argparse.Namespace) -> int:
    root = Path(args.root)
    db = Path(args.db or default_db_path(root))
    con = load_index_comments(db)
    axes = []
    for prefix, m_start, d_start, pos_base in [("SCON1/RB01", 31600, 33200, 42560), ("SCON2/RB02", 31700, 33250, 42720)]:
        if con.execute("select 1 from comments where all_text like ? limit 1", (f"%{prefix}%",)).fetchone():
            axes.append((prefix, m_start, d_start, pos_base))
    map_rows = []
    for axis, m_start, d_start, _ in axes:
        for r in con.execute(
            'select device, all_text from comments where (device_type="M" and number between ? and ?) or (device_type="D" and number between ? and ?) order by device_type, number',
            (m_start, m_start + 99, d_start, d_start + 49),
        ):
            map_rows.append({"axis": axis, "device": r["device"], "comment": r["all_text"] or ""})
    if args.map_out:
        write_csv(Path(args.map_out), map_rows, ["axis", "device", "comment"])

    values = load_d_words(root)
    pos_rows = []
    for axis, _, _, base in axes:
        for pos in range(1, 21):
            i = pos - 1
            move, src = dword32(values, base + i * 2)
            speed, _ = dword32(values, base + 40 + i * 2)
            accel, _ = dword32(values, base + 80 + i * 2)
            decel, _ = dword32(values, base + 120 + i * 2)
            pos_rows.append({"axis": axis, "pos_no": pos, "move_amount": move, "speed": speed, "accel": accel, "decel": decel, "source_db": src})
    write_or_print(pos_rows, args.out, ["axis", "pos_no", "move_amount", "speed", "accel", "decel", "source_db"])
    con.close()
    return 0


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_or_print(rows: list[dict], out: str | None, fields: list[str]) -> None:
    if out:
        write_csv(Path(out), rows, fields)
        print(f"wrote {out} rows={len(rows)}")
    else:
        print_table(rows, fields, None)


def zip_manifest(path: Path) -> dict[str, tuple[int, int, str]]:
    with zipfile.ZipFile(path) as z:
        return {
            i.filename: (i.file_size, i.CRC, hashlib.sha256(z.read(i.filename)).hexdigest())
            for i in z.infolist()
            if not i.is_dir()
        }


def sqlite_table_hashes(path: Path) -> dict[str, tuple[int, str]]:
    con = open_db(path)
    out = {}
    for trow in con.execute("select name from sqlite_master where type='table' order by name"):
        table = trow[0]
        cols = [r[1] for r in con.execute(f'pragma table_info("{table}")')]
        count = con.execute(f'select count(*) from "{table}"').fetchone()[0]
        h = hashlib.sha256()
        if cols:
            q = ",".join(f'quote("{c}")' for c in cols)
            for row in con.execute(f'select {q} from "{table}" order by rowid'):
                h.update(("\t".join("" if v is None else str(v) for v in row) + "\n").encode("utf-8", "replace"))
        out[table] = (count, h.hexdigest()[:16])
    con.close()
    return out


def command_diff(args: argparse.Namespace) -> int:
    old = Path(args.old)
    new = Path(args.new)
    old_manifest = zip_manifest(old)
    new_manifest = zip_manifest(new)
    names = sorted(set(old_manifest) | set(new_manifest))
    changed = [n for n in names if n in old_manifest and n in new_manifest and old_manifest[n][2] != new_manifest[n][2]]
    rows = []
    for name in changed:
        rows.append({"file": name, "old_size": old_manifest[name][0], "new_size": new_manifest[name][0], "old_crc": hex(old_manifest[name][1]), "new_crc": hex(new_manifest[name][1])})
    print_table(rows, ["file", "old_size", "new_size", "old_crc", "new_crc"], args.limit)
    if args.sqlite and changed:
        with zipfile.ZipFile(old) as zo, zipfile.ZipFile(new) as zn:
            tmp = Path(args.tmp_dir)
            tmp.mkdir(parents=True, exist_ok=True)
            for name in changed:
                if not name.lower().endswith((".db", ".dat")):
                    continue
                a = tmp / ("old_" + Path(name).name)
                b = tmp / ("new_" + Path(name).name)
                a.write_bytes(zo.read(name))
                b.write_bytes(zn.read(name))
                if is_sqlite(a) and is_sqlite(b):
                    ah = sqlite_table_hashes(a)
                    bh = sqlite_table_hashes(b)
                    diffs = [t for t in sorted(set(ah) | set(bh)) if ah.get(t) != bh.get(t)]
                    if diffs:
                        print(f"\nSQLite diff: {name}")
                        for table in diffs[: args.table_limit]:
                            print(f"  {table}: {ah.get(table)} -> {bh.get(table)}")
    if args.txc and "_Project.txc" in changed:
        with zipfile.ZipFile(old) as zo, zipfile.ZipFile(new) as zn:
            a = zo.read("_Project.txc")
            b = zn.read("_Project.txc")
        pref = 0
        for x, y in zip(a, b):
            if x != y:
                break
            pref += 1
        print(f"\n_Project.txc: old={len(a)} new={len(b)} delta={len(b)-len(a)} common_prefix={pref}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extra GX3 inspection utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="classify readable/editable project files")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--limit", type=int, default=None)
    p.set_defaults(func=command_inspect)

    p = sub.add_parser("sourceinfo", help="dump SourceInfo.CAB entries")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--cab")
    p.add_argument("--name")
    p.add_argument("--out-dir")
    p.add_argument("--lines", type=int, default=80)
    p.add_argument("--full", action="store_true")
    p.set_defaults(func=command_sourceinfo)

    p = sub.add_parser("version", help="show GX Works3 save/convert/write versions")
    p.add_argument("--root", default=str(default_project_root()))
    p.set_defaults(func=command_version)

    p = sub.add_parser("ip-map", help="extract registered IP address map")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("-o", "--out")
    p.set_defaults(func=command_ip_map)

    p = sub.add_parser("scon-map", help="extract IAI/SCON axis maps and POS values")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--db")
    p.add_argument("-o", "--out")
    p.add_argument("--map-out")
    p.set_defaults(func=command_scon_map)

    p = sub.add_parser("query-instruction", help="search LadderBlocks.data by instruction/text")
    p.add_argument("text")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--data-chars", type=int, default=220)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=command_query_instruction)

    p = sub.add_parser("diff", help="compare two .gx3 ZIPs and optional SQLite/TXC details")
    p.add_argument("old")
    p.add_argument("new")
    p.add_argument("--limit", type=int, default=100)
    p.add_argument("--sqlite", action="store_true")
    p.add_argument("--txc", action="store_true")
    p.add_argument("--tmp-dir", default="outputs/gx3_diff_tmp")
    p.add_argument("--table-limit", type=int, default=40)
    p.set_defaults(func=command_diff)

    p = sub.add_parser("block-context", help="show nearby ladder rows around a device occurrence")
    p.add_argument("device")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--around", type=int, default=5)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--diagram", action="store_true")
    p.set_defaults(func=command_block_context)

    p = sub.add_parser("same-row", help="show outputs and conditions that share rows with a device")
    p.add_argument("device")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--diagram", action="store_true")
    p.add_argument("--comments", action="store_true")
    p.set_defaults(func=command_same_row)

    p = sub.add_parser("signal-classify", help="classify a device as pulse/hold/state/command from ladder usage")
    p.add_argument("device")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=command_signal_classify)

    p = sub.add_parser("impact-add-nc", help="show static impact of adding a normally-closed contact to a device driver")
    p.add_argument("contact")
    p.add_argument("--to", required=True, help="target output device to guard")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--downstream-limit", type=int, default=40)
    p.set_defaults(func=command_impact_add_nc)

    p = sub.add_parser("state-chain", help="search state/mode chain candidates by device or comment text")
    p.add_argument("text")
    p.add_argument("--root", default=str(default_project_root()))
    p.add_argument("--db")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=command_state_chain)
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
