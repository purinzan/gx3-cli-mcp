from __future__ import annotations

"""Program execution configuration and system (unit) configuration report.

Sources:
- ``CPU.PRM``            program-file names in execution-setting order
- ``ConvertData``        program-file groups and POU link order (gx3_program_map)
- ``*_StepInfo.db``      step size per POU
- ``UnitConfig.dat``     full module list: base, slot, head I/O, network info
- xref DB (optional)     ladder row counts per POU

Program-file name <-> POU group association uses a naming heuristic
(``PF107`` <-> group whose first POU is ``107``); unmatched groups keep an
empty program_file column instead of a guessed one.
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

from gx3cli.gx3_program_map import load_program_map, parse_cpu_prm_program_names
from gx3cli.gx3_project_paths import default_output_prefix, default_project_root
from gx3cli.gx3_xref import default_db_path


def read_units(root: Path) -> list[dict[str, object]]:
    path = root / "UnitConfig.dat"
    if not path.exists():
        return []
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    units: dict[int, dict[str, object]] = {}
    for row in con.execute("select * from Object order by ObjectID"):
        units[row["ObjectID"]] = {
            "object_id": row["ObjectID"],
            "unit_name": row["ObjectName"],
            "base": "",
            "slot": "",
            "head_io": "",
            "head_io_hex": "",
            "station": "",
        }
    try:
        for row in con.execute("select * from Unit"):
            u = units.get(row["ObjectID"])
            if not u:
                continue
            u["base"] = row["ObjectIDOfBaseUnit"]
            u["slot"] = row["SlotNumber"]
            io = row["IONumber"]
            if io is not None:
                u["head_io"] = io
                try:
                    u["head_io_hex"] = f"0x{int(io):X}"
                except (TypeError, ValueError):
                    pass
    except sqlite3.Error:
        pass
    try:
        for row in con.execute("select * from NetworkUnit"):
            u = units.get(row["ObjectID"])
            if u:
                u["station"] = row["StationNumber"]
    except sqlite3.Error:
        pass
    con.close()
    return [u for u in units.values()]


def program_file_names(root: Path) -> list[str]:
    """Program-file names from CPU.PRM in execution-setting order.

    Delegates to gx3_program_map so Japanese program names stay whole
    (the old ASCII-only scan fragmented them)."""
    data = (root / "CPU.PRM").read_bytes() if (root / "CPU.PRM").exists() else b""
    if not data:
        return []
    return parse_cpu_prm_program_names(data)


def pou_row_counts(args: argparse.Namespace) -> dict[str, int]:
    path = Path(args.db or default_db_path(Path(args.root)))
    if not path.exists():
        return {}
    con = sqlite3.connect(path)
    counts = {
        pou: n
        for pou, n in con.execute(
            "select pou, count(distinct lddb || ':' || pos) from xref group by pou"
        )
    }
    con.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--db", default=None, help="xref sqlite path (for row counts)")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    args = parser.parse_args(argv)

    root = Path(args.root)
    prefix = args.prefix or default_output_prefix("exec")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    pm = load_program_map(root)
    names = program_file_names(root)
    rows_by_pou = pou_row_counts(args)

    # group POUs by program dir, keep link order
    groups: dict[str, list] = {}
    for info in pm.pous.values():
        groups.setdefault(info.program_dir or "?", []).append(info)
    for members in groups.values():
        members.sort(key=lambda i: i.link_index)

    # name association: trust the program map's program_file when it matches
    # a CPU.PRM name; otherwise fall back to the naming heuristic
    # (PF107 <-> first POU "107", exact match, underscore-stripped match).
    name_by_dir: dict[str, str] = {}
    for d, members in groups.items():
        pf = members[0].program_file if members else ""
        if pf and pf in names:
            name_by_dir[d] = pf
            continue
        first = members[0].name if members else ""
        for n in names:
            if (
                n == first
                or (n.startswith("PF") and n[2:] == first)
                or n.rstrip("_") == first.rstrip("_")
                or n.split("_")[0] == first
            ):
                name_by_dir[d] = n
                break

    print(f"root: {root}")
    print(f"program files in CPU.PRM execution-setting order ({len(names)}):")
    print("  " + ", ".join(names))
    print("")
    print(f"program groups ({len(groups)}), POUs ({len(pm.pous)}):")
    program_rows: list[dict[str, object]] = []
    total_steps = 0
    order_of = {n: i for i, n in enumerate(names)}
    for d, members in sorted(groups.items(), key=lambda kv: order_of.get(name_by_dir.get(kv[0], ""), 999)):
        label = name_by_dir.get(d, "")
        exec_order = order_of.get(label, "")
        header = f"[{exec_order}] {label}" if label else "[?] (name unresolved)"
        steps = sum(pm.step_starts.get(m.lddb_hex, [(0, 0)])[-1][1] for m in members)
        total_steps += steps
        print(f"  {header:<24} dir={d} pous={len(members)} steps~{steps}")
        for m in members:
            s = pm.step_starts.get(m.lddb_hex, [])
            total = s[-1][1] if s else 0
            rows = rows_by_pou.get(m.name or m.lddb_hex, "")
            print(f"      {m.link_index}: {m.name:<8} lddb={m.lddb_hex} steps~{total} rows={rows}")
            program_rows.append(
                {
                    "program_file": label,
                    "exec_order": exec_order,
                    "program_dir": d,
                    "link_index": m.link_index,
                    "pou": m.name,
                    "lddb_hex": m.lddb_hex,
                    "steps_approx": total,
                    "ladder_rows": rows,
                }
            )
    print(f"\ntotal steps (approx): {total_steps}")

    units = read_units(root)
    print(f"\nunits from UnitConfig.dat ({len(units)}):")
    for u in sorted(units, key=lambda x: (str(x["base"]), str(x["slot"]))):
        print(
            f"  base={u['base']!s:<3} slot={u['slot']!s:<3} head_io={u['head_io_hex']!s:<7} "
            f"station={u['station']!s:<4} {u['unit_name']}"
        )

    prog_csv = out_dir / f"{prefix}_programs.csv"
    with prog_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "program_file", "exec_order", "program_dir", "link_index",
                "pou", "lddb_hex", "steps_approx", "ladder_rows",
            ],
        )
        w.writeheader()
        for row in program_rows:
            w.writerow(row)
    unit_csv = out_dir / f"{prefix}_units.csv"
    with unit_csv.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(
            f, fieldnames=["object_id", "unit_name", "base", "slot", "head_io", "head_io_hex", "station"]
        )
        w.writeheader()
        for u in units:
            w.writerow(u)
    print(f"\ncsv: {prog_csv}")
    print(f"csv: {unit_csv}")
    for w_ in pm.warnings:
        print(f"warning: {w_}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
