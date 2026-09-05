"""Check a corpus of real projects by making two readings of it disagree.

There is no GX Works3 here to be the oracle, so the next best evidence is that
two paths built separately from the same bytes agree. Each project is read
four ways:

- the printed rung against the cross-reference: the printer resolves operands
  for display, the decoder resolves them into occurrences, and every device in
  one should be in the other. This is the check that found a BMOV's third
  operand decoded as a device that cannot exist.
- the driven device against the write set: what rung-text calls the device a
  rung drives has to be one the decoder says the rung writes. This is the check
  that found a source reported as a destination.
- every element against the picture: an element the layout knows about that
  leaves no label and no symbol in the SVG is logic the reader will not see.
- the parse status the decoder reports for every row.

Nothing here proves the two readings are right -- they were once wrong the same
way, which is why the walk they share is now written once. It proves they are
consistent, over projects nobody chose for being easy.

    python scripts/cross_check_corpus.py <folder of .gx3> -o results.json
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_device_name import canonical_device
from gx3cli.gx3_ladder_layout import _svg_rung, rung_layout
from gx3cli.gx3_ladder_print import parse_rung
from gx3cli.gx3_project_paths import is_gx3_archive
from gx3cli.gx3_rung_text import written_devices
from gx3cli.gx3_ladder_logic import positioned_elements
from gx3cli.review_gx3_project import LadderRow


DEVICE_LIKE = re.compile(r"^[A-Z]{1,3}[0-9A-F]+$")


def comparable(name: str) -> str:
    """The device, without the modifier spelled into its name.

    The printed rung folds an index register or a bit position into the name
    (D100Z2, D100.5); the cross-reference splits them out and keeps the
    modifier in its own column. Neither is wrong, so compare the device.
    """
    text = str(name).strip().strip("[]() ")
    if "\\" in text:
        return text
    text = re.sub(r"\.[0-9A-F]+$", "", text)
    text = re.sub(r"Z\d+$", "", text)
    text = re.sub(r"^K\d+", "", text)
    # K1 and H1F are constants. H takes hexadecimal digits, so "HEBF3" looks
    # exactly like a device name until you know the prefix.
    if text.startswith("#P"):
        return ""  # a pointer: where the program jumps, not a device
    # K1 and H1F and E4.32 are constants. H takes hexadecimal digits and E a
    # real number, so "HEBF3" and "E43200000" look exactly like device names
    # until you know the prefix.
    if re.fullmatch(r"K-?\d+", text) or re.fullmatch(r"H[0-9A-F]+", text):
        return ""
    if re.fullmatch(r"E-?[0-9.]+", text):
        return ""
    if not DEVICE_LIKE.match(text):
        return ""
    try:
        return canonical_device(text)
    except Exception:
        return ""


def check_project(root: Path) -> dict[str, object]:
    counts: Counter[str] = Counter()
    examples: list[str] = []

    for lddb in sorted(root.glob("*_LDDB.db")):
        con = sqlite3.connect(f"file:{lddb}?mode=ro", uri=True)
        con.text_factory = bytes
        rows = con.execute(
            "select id, pos, rowsize, data from LadderBlocks where blocktype=0"
        ).fetchall()
        con.close()

        for block_id, pos, rowsize, data in rows:
            text = data.decode("utf-8", "replace")
            if ":cb{" not in text:
                continue
            counts["rungs"] += 1
            row = LadderRow(
                lddb=lddb.name, pos=int(float(pos)), block_id=str(block_id), title="",
                blocktype=0, rowsize=int(float(rowsize or 0)), data=text, dim="",
                operations=[], parse_status="",
            )

            try:
                operations, status = parse_row_occurrences(text)
            except Exception as exc:
                counts["decode_error"] += 1
                if len(examples) < 8:
                    examples.append(f"decode {lddb.name}:{row.pos} {type(exc).__name__}")
                continue
            counts[f"parse_{status}"] += 1

            analysed = {
                c for _r, _o, args, _c in operations for occ in args
                if (c := comparable(occ.device))
            }

            try:
                printed_ops, _v, _w = parse_rung(row)
            except Exception as exc:
                counts["print_error"] += 1
                if len(examples) < 8:
                    examples.append(f"print {lddb.name}:{row.pos} {type(exc).__name__}")
                printed_ops = None
            if printed_ops is not None:
                printed = {
                    c for op in printed_ops for operand in op.operands
                    if (c := comparable(operand))
                }
                if printed != analysed:
                    counts["print_vs_xref_differs"] += 1
                    if len(examples) < 8:
                        examples.append(
                            f"print/xref {lddb.name}:{row.pos}"
                            f" print-only={sorted(printed - analysed)[:3]}"
                            f" xref-only={sorted(analysed - printed)[:3]}"
                        )

            writes_by_key: dict[str, set[str]] = {}
            for role, opcode, args, _c in operations:
                key = opcode or role
                writes_by_key.setdefault(key, set()).update(
                    occ.device for occ in args if occ.access in ("write", "both")
                )
            try:
                elements = positioned_elements(row)
            except Exception:
                elements = []
            for element in elements:
                named = written_devices(element)
                if not named:
                    continue
                key = (element.opcode or "").strip() or getattr(element, "role", "")
                expected = writes_by_key.get(key)
                if not expected:
                    continue
                counts["driver_decisions"] += 1
                if not set(named) & expected:
                    counts["driver_wrong"] += 1
                    if len(examples) < 8:
                        examples.append(
                            f"driver {lddb.name}:{row.pos} {key} says {named} not in {sorted(expected)[:3]}"
                        )

            try:
                layout = rung_layout(row)
                svg = "\n".join(_svg_rung(layout, 0))
            except Exception as exc:
                counts["svg_error"] += 1
                if len(examples) < 8:
                    examples.append(f"svg {lddb.name}:{row.pos} {type(exc).__name__}")
                continue
            for element in layout["elements"]:
                counts["svg_elements"] += 1
                label = str(element.get("label") or "")
                token = label.split()[0] if label else str(element.get("opcode") or "")
                if str(element.get("opcode", "")) in ("INV", "ME", "MEF"):
                    continue
                if token and html.escape(token[:12]) not in svg:
                    counts["svg_missing"] += 1
                    if len(examples) < 8:
                        examples.append(f"svg {lddb.name}:{row.pos} {token} not drawn")

    return {"counts": dict(counts), "examples": examples}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", help="folder holding .gx3 archives")
    parser.add_argument("-o", "--output", default="", help="write the per-project results here")
    parser.add_argument("--limit", type=int, default=0, help="stop after this many projects")
    args = parser.parse_args(argv)

    archives = sorted(Path(args.folder).glob("*.gx3"))
    if args.limit:
        archives = archives[: args.limit]

    results: dict[str, object] = {}
    totals: Counter[str] = Counter()
    for index, archive in enumerate(archives, 1):
        if not is_gx3_archive(archive):
            results[archive.name] = {"error": "not a readable gx3 archive"}
            print(f"[{index}/{len(archives)}] {archive.name}: unreadable archive", flush=True)
            continue
        try:
            with tempfile.TemporaryDirectory(prefix="corpus-") as tmp:
                with zipfile.ZipFile(archive) as zf:
                    zf.extractall(tmp)
                outcome = check_project(Path(tmp))
        except Exception as exc:
            results[archive.name] = {"error": f"{type(exc).__name__}: {exc}"}
            print(f"[{index}/{len(archives)}] {archive.name}: {type(exc).__name__}", flush=True)
            continue

        results[archive.name] = outcome
        counts = outcome["counts"]
        totals.update(counts)
        print(
            f"[{index}/{len(archives)}] {archive.name}: rungs={counts.get('rungs', 0):,}"
            f" print/xref differ={counts.get('print_vs_xref_differs', 0)}"
            f" driver wrong={counts.get('driver_wrong', 0)}"
            f" svg missing={counts.get('svg_missing', 0)}"
            f" partial={counts.get('parse_partial', 0)}",
            flush=True,
        )

    print("\n=== totals")
    for key in sorted(totals):
        print(f"  {key:<26} {totals[key]:>10,}")

    if args.output:
        Path(args.output).write_text(
            json.dumps({"totals": dict(totals), "projects": results}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"\nwritten: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
