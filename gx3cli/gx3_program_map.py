from __future__ import annotations

"""Map LDDB files to POU names, program-file groups, and real step numbers.

Data sources inside an extracted GX Works3 project:
- ``*_LDDB.db``            ladder rows, block ids are ``_guid/<uuid>``
- ``*_StepInfo.db``        per-POU step sizes; ``T_Block.BlockID`` holds the same
                           GUIDs as the LDDB, which links decimal ids to hex ids
- ``ConvertData/<dec>/PouLinkOrder.info``  POU decimal ids in link order
- ``ConvertData/<dec>/Program.qpg``        POU names stored as length-prefixed
                           UTF-16 records just before the UTF-16 ``BackUp`` marker
- ``CPU.PRM``              program-file names in execution-setting order
"""

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

from gx3cli.gx3_project_paths import default_project_root


BACKUP_MARKER = "BackUp".encode("utf-16-le")
POU_ID_RE = re.compile(r"\d{15,20}")

# Characters allowed in POU / program-file names (ASCII plus Japanese:
# hiragana/katakana, CJK, halfwidth kana).
NAME_FULLMATCH_RE = re.compile(r"[0-9A-Za-z_$@!#&().+ \-぀-ヿ一-鿿｡-ﾟ]+")
# CPU.PRM name runs: no space/!, so adjacent binary noise does not merge names.
CPU_NAME_RUN_RE = re.compile(r"[0-9A-Za-z_$@#&().+\-぀-ヿ一-鿿｡-ﾟ]{2,64}")
CPU_PRM_SKIP_NAMES = {"BackUp", "EVENT", "EVEN2"}

# Some newer GX Works3 project generations have no UTF-16 "BackUp"
# marker in Program.qpg. Instead each POU name is stored as a fixed record:
#   <16-byte class marker> ... FF FF FF FF <WORD byte_len> <UTF-16 name> 00 00
QPG_NAME_MARKER = bytes.fromhex("489a0c79c2511f38275a466efabc2398")
FFFF_RE = re.compile(b"\xff\xff\xff\xff")


@dataclass
class PouInfo:
    pou_dir: str = ""            # decimal ConvertData id
    lddb_hex: str = ""           # hex prefix of *_LDDB.db
    name: str = ""               # POU name shown in GX Works3 (e.g. "601")
    program_file: str = ""       # CPU.PRM program-file name that owns this POU
    program_dir: str = ""        # decimal id of the owning program-file dir
    link_index: int = -1         # order inside the program file


@dataclass
class ProgramMap:
    root: Path = field(default_factory=Path)
    pous: dict[str, PouInfo] = field(default_factory=dict)          # by lddb hex
    pous_by_dir: dict[str, PouInfo] = field(default_factory=dict)   # by decimal id
    program_files: list[str] = field(default_factory=list)          # CPU.PRM order
    step_starts: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def label(self, lddb: str) -> str:
        hexid = lddb.split("_")[0]
        info = self.pous.get(hexid)
        if info is None:
            return hexid
        if info.name:
            return info.name
        # Fallbacks that stay meaningful for humans: the owning program file,
        # then an execution-order label, then the ConvertData dir id.
        # The raw LDDB hex hash is the last resort only.
        if info.program_file:
            return info.program_file
        if info.link_index >= 0 and info.program_dir:
            return f"pou{info.link_index}_dir{info.program_dir[:10]}"
        if info.program_dir or info.pou_dir:
            return f"dir{(info.program_dir or info.pou_dir)[:10]}"
        return hexid

    def step_of(self, lddb: str, pos: int) -> int | None:
        hexid = lddb.split("_")[0]
        starts = self.step_starts.get(hexid)
        if not starts:
            return None
        step = None
        for block_pos, start in starts:
            if block_pos <= pos:
                step = start
            else:
                break
        return step


def decode_utf16_records(data: bytes, end: int, count: int) -> list[str]:
    """Walk backwards from ``end`` and collect ``count`` length-prefixed UTF-16
    name records. Record layout: ... WORD byte_len, UTF-16 chars, NUL, padding.
    Records sit close together; scan a bounded window and keep the last matches."""
    window_start = max(0, end - 64 * (count + 4) - 512)
    window = data[window_start:end]
    names: list[tuple[int, str]] = []
    i = 0
    while i < len(window) - 4:
        blen = int.from_bytes(window[i : i + 2], "little")
        if 4 <= blen <= 42 and blen % 2 == 0:
            raw = window[i + 2 : i + 2 + blen]
            if len(raw) == blen and raw[-2:] == b"\x00\x00":
                try:
                    text = raw[:-2].decode("utf-16-le")
                except UnicodeDecodeError:
                    text = ""
                if text and re.fullmatch(r"[0-9A-Za-z_$@!#&().+ \-぀-ヿ一-鿿｡-ﾟ]+", text):
                    names.append((window_start + i, text))
                    i += 2 + blen
                    continue
        i += 2 if i % 2 == 0 else 1
    return [t for _, t in names[-count:]]


def qpg_pou_name_records(data: bytes) -> list[str]:
    """POU names from the fixed name records used by newer generations
    (projects whose Program.qpg has no UTF-16 ``BackUp`` marker).

    Record: 16-byte class marker, some variable bytes, ``FF FF FF FF``,
    WORD byte length (name incl. trailing NUL), UTF-16-LE name, NUL."""
    names: list[str] = []
    start = 0
    while True:
        i = data.find(QPG_NAME_MARKER, start)
        if i < 0:
            break
        window = data[i : i + 96]
        for m in FFFF_RE.finditer(window):
            k = i + m.end()
            blen = int.from_bytes(data[k : k + 2], "little")
            if not (4 <= blen <= 66 and blen % 2 == 0):
                continue
            raw = data[k + 2 : k + 2 + blen]
            if len(raw) != blen or raw[-2:] != b"\x00\x00":
                continue
            try:
                text = raw[:-2].decode("utf-16-le")
            except UnicodeDecodeError:
                continue
            if text and NAME_FULLMATCH_RE.fullmatch(text):
                names.append(text)
                break
        start = i + len(QPG_NAME_MARKER)
    return names


def match_program_file(pou_name: str, program_files: list[str]) -> str:
    """Associate a POU name with a CPU.PRM program-file name.

    Newer generations keep only a truncated/derived POU name in the qpg
    (e.g. POU ``100_03_`` in program file ``100_03_PROCESS_...``, or POU
    ``020_TP_2`` in program file ``020_TP``), so accept prefix matches in
    both directions and pick the most specific candidate."""
    if not pou_name:
        return ""
    if pou_name in program_files:
        return pou_name
    candidates = [n for n in program_files if n.startswith(pou_name) or pou_name.startswith(n)]
    if not candidates:
        return ""

    def common_prefix_len(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a, b):
            if x != y:
                break
            n += 1
        return n

    candidates.sort(key=lambda n: (-common_prefix_len(n, pou_name), len(n)))
    return candidates[0]


def load_program_map(root: Path) -> ProgramMap:
    pm = ProgramMap(root=root)
    cpu_prm = root / "CPU.PRM"
    if cpu_prm.exists():
        pm.program_files = parse_cpu_prm_program_names(cpu_prm.read_bytes())

    guid_to_hex: dict[str, str] = {}
    for p in sorted(root.glob("*_LDDB.db")):
        hexid = p.name.split("_")[0]
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.text_factory = bytes
        for (bid,) in con.execute("select id from LadderBlocks"):
            s = bid.decode("utf-8", errors="ignore") if isinstance(bid, bytes) else str(bid)
            if s.startswith("_guid/"):
                guid_to_hex[s[len("_guid/") :].lower()] = hexid
        con.close()

    dec_to_hex: dict[str, str] = {}
    for p in sorted(root.glob("*_StepInfo.db")):
        dec = p.name.split("_")[0]
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        hits: dict[str, int] = {}
        starts: list[tuple[int, int]] = []
        acc = 0
        for pos, bid, size in con.execute("select Pos, BlockID, StepSize from T_Block order by Pos"):
            g = str(bid).strip("{}").lower()
            hexid = guid_to_hex.get(g)
            if hexid:
                hits[hexid] = hits.get(hexid, 0) + 1
            starts.append((int(float(pos)), acc))
            acc += int(size or 0)
        con.close()
        if not hits:
            pm.warnings.append(f"stepinfo {dec}: no matching LDDB")
            continue
        hexid = max(hits, key=hits.get)
        if len(hits) > 1:
            pm.warnings.append(f"stepinfo {dec}: ambiguous LDDB match {hits}")
        dec_to_hex[dec] = hexid
        pm.step_starts[hexid] = starts
        info = PouInfo(pou_dir=dec, lddb_hex=hexid)
        pm.pous[hexid] = info
        pm.pous_by_dir[dec] = info

    convert = root / "ConvertData"
    if convert.is_dir():
        for d in sorted(convert.iterdir()):
            link = d / "PouLinkOrder.info"
            qpg = d / "Program.qpg"
            if not link.exists():
                continue
            pou_ids = POU_ID_RE.findall(link.read_text(encoding="ascii", errors="ignore"))
            names: list[str] = []
            program_file = ""
            if qpg.exists():
                data = qpg.read_bytes()
                idx = data.rfind(BACKUP_MARKER)
                if idx >= 0:
                    names = decode_utf16_records(data, idx, len(pou_ids))
                if len(names) != len(pou_ids):
                    # Newer generation: no BackUp marker. Use the fixed
                    # marker+length name records instead.
                    records = qpg_pou_name_records(data)
                    if len(records) == len(pou_ids):
                        names = records
                if names and names[0]:
                    program_file = match_program_file(names[0], pm.program_files)
                if not program_file:
                    program_file = qpg_program_file_name(data, pm.program_files)
            if len(names) != len(pou_ids):
                if not (len(pou_ids) == 1 and program_file):
                    pm.warnings.append(
                        f"program {d.name}: {len(pou_ids)} POUs but {len(names)} names decoded"
                    )
                names = [""] * len(pou_ids)
            for order, (pid, name) in enumerate(zip(pou_ids, names)):
                info = pm.pous_by_dir.get(pid)
                if info is None:
                    pm.warnings.append(f"program {d.name}: unknown POU id {pid}")
                    continue
                info.program_file = program_file
                info.name = name or (program_file if len(pou_ids) == 1 else "")
                info.program_dir = d.name
                info.link_index = order

        # Second pass for program dirs whose POU name is known but whose
        # program file could not be matched (underscore drift such as
        # POU ``202_MES2_`` vs program file ``202_MES_2``).
        used_files = {info.program_file for info in pm.pous_by_dir.values() if info.program_file}
        unused_files = [n for n in pm.program_files if n not in used_files]
        for info in pm.pous_by_dir.values():
            if not info.program_dir or info.program_file or not info.name:
                continue
            key = info.name.replace("_", "")
            candidates = [
                n
                for n in unused_files
                if n.replace("_", "") == key
                or n.replace("_", "").startswith(key)
                or key.startswith(n.replace("_", ""))
            ]
            if len(candidates) == 1:
                info.program_file = candidates[0]
                unused_files.remove(candidates[0])

        # Elimination fallback: if exactly one linked program dir is still
        # unresolved and exactly one CPU.PRM program file is unused, they
        # must correspond (execution-setting index correspondence).
        unresolved = [
            info
            for info in pm.pous_by_dir.values()
            if info.program_dir and not info.program_file
        ]
        if len(unresolved) == 1 and len(unused_files) == 1:
            info = unresolved[0]
            info.program_file = unused_files[0]
            if not info.name:
                info.name = unused_files[0]
            pm.warnings.append(
                f"program {info.program_dir}: program file assigned by elimination: {unused_files[0]}"
            )
    return pm


def parse_cpu_prm_program_names(data: bytes) -> list[str]:
    """Program-file names (execution-setting order) from CPU.PRM.

    Decodes the segment after the last ``$MELPRJ$`` marker as UTF-16-LE and
    collects name runs. Program names with non-ASCII text
    are kept whole; the old ASCII-only scan fragmented them (``100_01_`` +
    ``_1st_``)."""
    marker = "$MELPRJ$".encode("utf-16-le")
    start = data.rfind(marker)
    if start >= 0:
        data = data[start:]
    text = data.decode("utf-16-le", errors="replace")
    names: list[str] = []
    seen: set[str] = set()
    for run in CPU_NAME_RUN_RE.findall(text):
        if run in CPU_PRM_SKIP_NAMES or "$MELPRJ$" in run:
            continue
        if run not in seen:
            seen.add(run)
            names.append(run)
    return names


def utf16_ascii_strings(data: bytes) -> list[str]:
    strings: list[str] = []
    for m in re.finditer(rb"(?:[\x20-\x7e][\x00]){2,}", data):
        text = m.group().decode("utf-16-le", errors="ignore").strip()
        if text:
            strings.append(text)
    return strings


def qpg_program_file_name(data: bytes, program_files: list[str]) -> str:
    program_file_set = set(program_files)
    for text in utf16_ascii_strings(data):
        if text in program_file_set:
            return text
    return ""


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Show LDDB -> POU/program mapping.")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)

    pm = load_program_map(Path(args.root))
    if args.json:
        payload = {
            "program_files": pm.program_files,
            "pous": [vars(info) for info in sorted(pm.pous.values(), key=lambda i: (i.program_dir, i.link_index))],
            "warnings": pm.warnings,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0

    print(f"root: {pm.root}")
    print(f"program files (CPU.PRM execution-setting order): {', '.join(pm.program_files) or '-'}")
    print("")
    print(f"{'pou_name':<12} {'program_file':<14} {'lddb_hex':<18} {'program_dir':<22} {'order':<5} steps")
    current = None
    for info in sorted(pm.pous.values(), key=lambda i: (i.program_dir, i.link_index)):
        if info.program_dir != current:
            current = info.program_dir
            print(f"-- program dir {current or '(unlinked)'}")
        starts = pm.step_starts.get(info.lddb_hex, [])
        total = starts[-1][1] if starts else 0
        print(
            f"{info.name or '?':<12} {info.program_file or '-':<14} "
            f"{info.lddb_hex:<18} {info.program_dir or '-':<22} {info.link_index:<5} {total}"
        )
    for w in pm.warnings:
        print(f"warning: {w}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
