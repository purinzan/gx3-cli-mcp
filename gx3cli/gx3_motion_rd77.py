from __future__ import annotations

"""RD77MS simple-motion analysis: buffer-memory access map with official labels.

Sources:
- ``UnitConfig.dat``      locate RD77 (and other) units and their head I/O
- unit parameter DB       ``_UnitLabel`` table = official G-address -> symbol map
                          (e.g. G2400 = RD77.stnAxMntr[0].dCommandPosition)
- xref DB                 every ladder U\\G access with POU / step / access kind

Outputs:
- console: per-unit U\\G access list annotated with symbol + axis number
- CSV:     <prefix>_motion_ug_access.csv and <prefix>_motion_labels.csv

The positioning-data tables (Da.*) live inside the ``*.iut`` container written
by the Simple Motion Setting function and are not decoded here; the tool lists
the container sections so their presence is visible.
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

from gx3cli.gx3_exec_config import read_units
from gx3cli.gx3_project_paths import default_output_prefix, default_project_root
from gx3cli.gx3_xref import default_db_path


AXIS_RE = re.compile(r"\[(\d+)\]")
UNIT_DETAIL_RE = re.compile(r"unit=0x([0-9A-Fa-f]+)")


def unit_label_map(db_path: Path) -> dict[int, str]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    labels: dict[int, str] = {}
    try:
        for g, label in con.execute("select Label, Data from _UnitLabel"):
            m = re.fullmatch(r"G(\d+)", str(g))
            if m and label:
                labels[int(m.group(1))] = str(label)
    except sqlite3.Error:
        pass
    con.close()
    return labels


def head_io_of_db(db_path: Path) -> int | None:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = con.execute("select Data from DeviceInfo where Label='_HeadIO'").fetchone()
    except sqlite3.Error:
        row = None
    con.close()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def find_unit_dbs(root: Path) -> dict[int, Path]:
    """Map ladder unit value (head_io >> 4) to the unit parameter DB path."""
    result: dict[int, Path] = {}
    for p in sorted(root.glob("*.db")):
        if p.name.endswith(("_LDDB.db", "_MilDB.db", "_StepInfo.db", "_DC.db")) or p.name == "LabelData.db":
            continue
        head = head_io_of_db(p)
        if head is not None:
            result[head >> 4] = p
    return result


def lookup_label(labels: dict[int, str], offset: int) -> tuple[str, str]:
    """Return (symbol, axis) for a G offset; falls back to nearest lower label."""
    if not labels:
        return "", ""
    best = None
    for g in (offset, offset - 1):
        if g in labels:
            best = (g, labels[g])
            break
    if best is None:
        lower = [g for g in labels if g <= offset and offset - g <= 16]
        if lower:
            g = max(lower)
            best = (g, f"{labels[g]} +{offset - g}")
    if best is None:
        return "", ""
    axis_m = AXIS_RE.search(best[1])
    axis = str(int(axis_m.group(1)) + 1) if axis_m else ""
    return best[1], axis


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--db", default=None, help="xref sqlite path")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    prefix = args.prefix or default_output_prefix("motion")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    units = read_units(root)
    unit_names: dict[int, str] = {}
    for u in units:
        try:
            head = int(u["head_io"])
        except (TypeError, ValueError):
            continue
        if u["head_io_hex"]:
            unit_names[head >> 4] = str(u["unit_name"])

    unit_dbs = find_unit_dbs(root)
    labels_by_unit = {uval: unit_label_map(p) for uval, p in unit_dbs.items()}

    xref_path = Path(args.db or default_db_path(root))
    if not xref_path.exists():
        raise SystemExit(f"xref db not found: {xref_path} (run: gx3_cli.py xref build)")
    con = sqlite3.connect(xref_path)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        select device, number as g, detail, access, opcode, role, const_args,
               pou, step, title, lddb, pos
        from xref where device_type='UG' order by device, pos
        """
    ).fetchall()

    access_rows: list[dict[str, object]] = []
    print(f"U\\G buffer accesses found in ladder: {len(rows)}")
    current_unit = None
    for r in rows:
        m = UNIT_DETAIL_RE.search(r["detail"] or "")
        uval = int(m.group(1), 16) if m else -1
        uname = unit_names.get(uval, "?")
        labels = labels_by_unit.get(uval, {})
        symbol, axis = lookup_label(labels, int(r["g"]))
        if uval != current_unit:
            current_unit = uval
            db_note = unit_dbs.get(uval)
            print(f"\n== U{uval:X} ({uname}) {'param db: ' + db_note.name if db_note else ''}")
        opcode = r["opcode"] or r["role"]
        const = f" k={r['const_args']}" if r["const_args"] else ""
        ax = f" axis{axis}" if axis else ""
        sym = f" {symbol}" if symbol else ""
        print(f"  {r['device']:<12} {r['access']:<5} {opcode:<8} {r['pou']:<6} st{r['step']}{const}{ax}{sym}")
        access_rows.append(
            {
                "device": r["device"],
                "unit": f"0x{uval:X}" if uval >= 0 else "",
                "unit_name": uname,
                "g_offset": r["g"],
                "access": r["access"],
                "opcode": opcode,
                "const_args": r["const_args"],
                "axis": axis,
                "symbol": symbol,
                "pou": r["pou"],
                "step": r["step"],
                "title": r["title"],
            }
        )

    # FROM/TO with dynamic unit (e.g. FROM D32717 K2400Z2 D9900 K2) --------
    ft_rows = con.execute(
        """
        select lddb, pos, opcode, device, device_type, access, arg_index, detail,
               const_args, comment, pou, step, title
        from xref
        where opcode in ('FROM','DFRO','DFROM','TO','DTO')
        order by lddb, pos, arg_index, id
        """
    ).fetchall()
    groups: dict[tuple[str, int], list[sqlite3.Row]] = {}
    for r in ft_rows:
        groups.setdefault((r["lddb"], r["pos"]), []).append(r)
    if groups:
        print(f"\nFROM/TO buffer accesses ({len(groups)} rows):")
    ft_csv_rows: list[dict[str, object]] = []
    base_re = re.compile(r"base=K(-?\d+)\+Z(\d+)")
    for (_, _), members in sorted(groups.items(), key=lambda kv: (kv[1][0]["pou"], kv[1][0]["step"] or 0)):
        opcode = members[0]["opcode"]
        unit_dev = next((m for m in members if m["arg_index"] == 0 and m["device_type"] != "Z"), None)
        offset_m = next((m for m in members if m["detail"] and "base=K" in m["detail"]), None)
        data_dev = next((m for m in members if m["access"] in ("write",) or (opcode in ("TO", "DTO") and m["arg_index"] == 2)), None)
        consts = (members[0]["const_args"] or "").split(",")

        unit_desc = f"{unit_dev['device']}({unit_dev['comment']})" if unit_dev else (f"K{consts[0]}" if consts and consts[0] else "?")
        base = None
        offset_desc = "?"
        if offset_m:
            bm = base_re.search(offset_m["detail"])
            if bm:
                base = int(bm.group(1))
                offset_desc = f"G{base}+Z{bm.group(2)}"
        symbol = axis_note = ""
        if base is not None:
            for uval, labels in labels_by_unit.items():
                if base in labels:
                    symbol = labels[base]
                    axis_note = f"axis=1+Z/100 ({unit_names.get(uval, '?')})"
                    break
        pou = members[0]["pou"]
        step = members[0]["step"]
        data_desc = f"{data_dev['device']}({data_dev['comment']})" if data_dev else ""
        print(f"  {pou:<6} st{step:<6} {opcode:<6} unit={unit_desc} offset={offset_desc} data={data_desc} {symbol} {axis_note}".rstrip())
        ft_csv_rows.append(
            {
                "pou": pou, "step": step, "opcode": opcode, "unit": unit_desc,
                "offset": offset_desc, "data": data_desc, "symbol": symbol,
                "axis_note": axis_note, "title": members[0]["title"],
            }
        )
    if ft_csv_rows:
        ft_csv = out_dir / f"{prefix}_fromto_access.csv"
        with ft_csv.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(
                f, fieldnames=["pou", "step", "opcode", "unit", "offset", "data", "symbol", "axis_note", "title"]
            )
            w.writeheader()
            for row in ft_csv_rows:
                w.writerow(row)
        print(f"csv: {ft_csv}")

    zp_rows = con.execute(
        """
        select opcode, count(*) as n from xref
        where opcode like 'ZP%' or opcode like 'GP%' or opcode like 'G.%' or opcode='SVST'
        group by opcode
        """
    ).fetchall()
    if zp_rows:
        print("\ndedicated motion instructions:")
        for r in zp_rows:
            print(f"  {r['opcode']}: {r['n']}")
    con.close()

    acc_csv = out_dir / f"{prefix}_ug_access.csv"
    with acc_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "device", "unit", "unit_name", "g_offset", "access", "opcode",
                "const_args", "axis", "symbol", "pou", "step", "title",
            ],
        )
        w.writeheader()
        for row in access_rows:
            w.writerow(row)
    print(f"\ncsv: {acc_csv}")

    label_csv = out_dir / f"{prefix}_labels.csv"
    with label_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["unit", "unit_name", "g_offset", "symbol", "axis"])
        for uval, labels in sorted(labels_by_unit.items()):
            uname = unit_names.get(uval, "?")
            for g, label in sorted(labels.items()):
                axis_m = AXIS_RE.search(label)
                axis = str(int(axis_m.group(1)) + 1) if axis_m else ""
                w.writerow([f"0x{uval:X}", uname, g, label, axis])
    print(f"csv: {label_csv}")

    for p in sorted(root.glob("*.iut")):
        data = p.read_bytes()
        sections = sorted(
            {
                m.group().decode("utf-16-le")
                for m in re.finditer(rb"(?:[\x20-\x7e][\x00]){10,}", data)
                if b"D\x00a\x00t\x00a\x00N\x00a\x00m\x00e" in m.group()
            }
        )
        print(f"\niut container: {p.name} ({len(data)} bytes, {len(sections)} named sections, not decoded)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
