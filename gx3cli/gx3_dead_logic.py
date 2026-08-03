from __future__ import annotations

"""Dead / constant logic detection.

Uses the xref database (full read/write classification) plus, when available,
the lite index ``external_sources`` table (HMI / communication / refresh
boundaries) to avoid flagging devices legitimately written from outside.

Categories:
  const-off-contact   NO contact on a bit device nothing ever writes
                      -> branch can never conduct (dead branch candidate)
  always-on-contact   NC contact on a bit device nothing ever writes
                      -> contact is always closed (redundant condition)
  sm-disabled-row     rows gated by SM401 NO / SM400 NC (intentional disable)
  unread-coil         bit device written but never read in the PLC
                      (may still be monitored by HMI - listed, not judged)
  set-without-rst     SET-latched devices with no RST anywhere
  unread-word         word device written but never read in the PLC

Every finding lists POU and step so it can be checked in GX Works3 directly.
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

from gx3cli.gx3_external_inputs import load_refresh_areas, refresh_area_for
from gx3cli.gx3_project_paths import default_comm_prefix, default_output_prefix, default_project_root
from gx3cli.gx3_xref import default_db_path


INTERNAL_BIT_TYPES = ("M", "L", "B", "F", "V", "S")
WORD_TYPES = ("D", "W", "ZR", "R")


def lite_db_path(root: Path) -> Path:
    name = root.name
    if name.startswith("_extracted_"):
        name = name[len("_extracted_") :]
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "project"
    return Path(".gx3_index") / f"{label}.sqlite"


def load_external_devices(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    out: dict[str, str] = {}
    try:
        for device, kind, group in con.execute(
            "select device, source_kind, semantic_group from external_sources"
        ):
            out[str(device)] = f"{kind}/{group}"
    except sqlite3.Error:
        pass
    con.close()
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--db", default=None, help="xref sqlite path")
    parser.add_argument("--lite-db", default=None, help="lite index sqlite (for external boundaries)")
    parser.add_argument("--refresh-csv", default=None, help="comm refresh areas CSV (network-visible ranges)")
    parser.add_argument("--output-dir", default="outputs")
    parser.add_argument("--prefix", default=None)
    parser.add_argument("--limit", type=int, default=25, help="console lines per category")
    args = parser.parse_args(argv)

    root = Path(args.root)
    xref_path = Path(args.db or default_db_path(root))
    if not xref_path.exists():
        raise SystemExit(f"xref db not found: {xref_path} (run: gx3_cli.py xref build)")
    prefix = args.prefix or default_output_prefix("dead_logic")
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    externals = load_external_devices(Path(args.lite_db) if args.lite_db else lite_db_path(root))
    print(f"external/HMI/comm boundary devices known: {len(externals)}")

    refresh_csv = Path(args.refresh_csv) if args.refresh_csv else Path("outputs") / f"{default_comm_prefix()}_refresh_areas.csv"
    refresh_areas = load_refresh_areas(refresh_csv)
    print(f"network refresh areas loaded: {len(refresh_areas)} ({refresh_csv})")
    refreshed_skipped = 0

    def in_refresh(device_type: str, device: str) -> bool:
        m = re.match(r"^[A-Z]+(\d+)$", device)
        if not m or not refresh_areas:
            return False
        return refresh_area_for(device_type, int(m.group(1)), refresh_areas) is not None

    con = sqlite3.connect(xref_path)
    con.row_factory = sqlite3.Row

    stats: dict[str, dict[str, int]] = {}
    for r in con.execute(
        """
        select device, device_type,
               sum(case when access in ('write','both') then 1 else 0 end) as writes,
               sum(case when access='read' then 1 else 0 end) as reads,
               sum(case when access='ref' then 1 else 0 end) as refs,
               sum(case when role='SET' then 1 else 0 end) as sets,
               sum(case when role='RST' then 1 else 0 end) as rsts,
               max(comment) as comment
        from xref group by device
        """
    ):
        stats[r["device"]] = dict(r)

    findings: list[dict[str, object]] = []

    def usage_sites(device: str, access: tuple[str, ...], limit: int = 3) -> str:
        rows = con.execute(
            f"""
            select pou, step from xref where device=? and access in ({','.join('?' * len(access))})
            order by pou, step limit ?
            """,
            (device, *access, limit),
        ).fetchall()
        return "; ".join(f"{r['pou']} st{r['step']}" for r in rows)

    # 1) contacts on never-written internal bit devices ------------------------
    for device, s in sorted(stats.items()):
        if s["device_type"] not in INTERNAL_BIT_TYPES:
            continue
        if s["writes"] or s["refs"]:
            continue
        if device in externals:
            continue
        if not s["reads"]:
            continue
        if in_refresh(s["device_type"], device):
            refreshed_skipped += 1
            continue
        roles = {
            r["role"]: r["n"]
            for r in con.execute(
                "select role, count(*) as n from xref where device=? and role in ('a','b') group by role",
                (device,),
            )
        }
        if roles.get("a"):
            findings.append(
                {
                    "category": "const-off-contact",
                    "device": device,
                    "comment": s["comment"] or "",
                    "count": roles["a"],
                    "where": usage_sites(device, ("read",)),
                    "note": "NO contact, no writer found -> branch never conducts",
                }
            )
        if roles.get("b"):
            findings.append(
                {
                    "category": "always-on-contact",
                    "device": device,
                    "comment": s["comment"] or "",
                    "count": roles["b"],
                    "where": usage_sites(device, ("read",)),
                    "note": "NC contact, no writer found -> always closed",
                }
            )

    # 2) rows intentionally disabled via SM400/SM401 ---------------------------
    sm_rows = con.execute(
        """
        select distinct lddb, pos, pou, step, title from xref
        where (device='SM401' and role='a') or (device='SM400' and role='b')
        order by pou, step
        """
    ).fetchall()
    for r in sm_rows:
        findings.append(
            {
                "category": "sm-disabled-row",
                "device": "SM401/SM400",
                "comment": r["title"] or "",
                "count": 1,
                "where": f"{r['pou']} st{r['step']}",
                "note": "row branch disabled by always-OFF pattern",
            }
        )

    # 3) written but never read ------------------------------------------------
    for device, s in sorted(stats.items()):
        if s["reads"] or s["refs"]:
            continue
        if device in externals:
            continue
        if s["writes"] and in_refresh(s["device_type"], device):
            refreshed_skipped += 1
            continue
        if s["device_type"] in INTERNAL_BIT_TYPES and s["writes"]:
            findings.append(
                {
                    "category": "unread-coil",
                    "device": device,
                    "comment": s["comment"] or "",
                    "count": s["writes"],
                    "where": usage_sites(device, ("write", "both")),
                    "note": "never read in PLC (HMI monitoring possible)",
                }
            )
        elif s["device_type"] in WORD_TYPES and s["writes"]:
            findings.append(
                {
                    "category": "unread-word",
                    "device": device,
                    "comment": s["comment"] or "",
                    "count": s["writes"],
                    "where": usage_sites(device, ("write", "both")),
                    "note": "never read in PLC (HMI/logging possible)",
                }
            )

    # 4) SET without RST ------------------------------------------------------
    for device, s in sorted(stats.items()):
        if s["sets"] and not s["rsts"] and s["device_type"] in INTERNAL_BIT_TYPES:
            if device in externals:
                continue
            findings.append(
                {
                    "category": "set-without-rst",
                    "device": device,
                    "comment": s["comment"] or "",
                    "count": s["sets"],
                    "where": usage_sites(device, ("write", "both")),
                    "note": "latched but never reset in ladder",
                }
            )

    by_cat: dict[str, list[dict[str, object]]] = {}
    for f in findings:
        by_cat.setdefault(str(f["category"]), []).append(f)
    print("")
    for cat in ["const-off-contact", "always-on-contact", "sm-disabled-row",
                "unread-coil", "unread-word", "set-without-rst"]:
        items = by_cat.get(cat, [])
        print(f"== {cat}: {len(items)}")
        for f in items[: args.limit]:
            print(f"  {f['device']:<12} x{f['count']:<3} {f['where']:<28} {f['comment']}")
        if len(items) > args.limit:
            print(f"  ... {len(items) - args.limit} more (see CSV)")

    out = out_dir / f"{prefix}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["category", "device", "comment", "count", "where", "note"])
        w.writeheader()
        for row in findings:
            w.writerow(row)
    print(f"\ntotal findings: {len(findings)}")
    print(f"devices skipped as network-refreshed (visible to remote stations): {refreshed_skipped}")
    print(f"csv: {out}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
