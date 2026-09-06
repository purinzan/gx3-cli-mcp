from __future__ import annotations

"""Alarm / fault inventory with trigger, hold, and reset conditions.

Scans the xref database (build it first: ``gx3_cli.py xref build``) for coils
whose comment looks like an alarm, plus every F (annunciator) device, and
reports for each:
- where it is driven (POU, real step)
- hold type: SET-latched / self-holding OUT / plain OUT
- trigger conditions of the driver row, with comments
- monitoring timer feeding the alarm and its setpoint when resolvable
- reset locations (RST rows)

Subcommands:
  list     write the full alarm inventory CSV and print a summary
  show     print one alarm's detail to the console
"""

import argparse
import csv
import re
import sqlite3
import sys
from pathlib import Path

from gx3cli.gx3_project_paths import default_output_prefix, default_project_root
from gx3cli.gx3_xref import default_db_path, normalize_device, open_xref_db
from gx3cli.gx3_xref_read import device_match


ALARM_COMMENT_RE = re.compile(
    r"異常|警報|故障|ｱﾗｰﾑ|アラーム|ｴﾗｰ|エラー|ﾀｲﾑｵｰﾊﾞ|タイムオーバ|時間超過|"
    r"abnormal|alarm|error|fault|time ?over|timeout|out of range",
    re.IGNORECASE,
)
ALARM_BIT_TYPES = {"M", "L", "B", "F", "V", "S"}
DRIVER_ROLES = ("c", "SET")
TIMER_UNITS_S = {"OUT__16": 0.1, "OUTH__16": 0.01}


def open_db(args: argparse.Namespace) -> sqlite3.Connection:
    """Open the cross-reference, checked against the project it is about.

    Opened raw, this reported alarms from one project beside comments from
    another and finished cleanly. The check has existed on `open_xref_db` all
    along; this command had a --root in hand and did not pass it.
    """
    path = Path(args.db or default_db_path(Path(args.root)))
    if not path.exists():
        raise SystemExit(f"xref db not found: {path} (run: gx3_cli.py xref build)")
    return open_xref_db(path, read_only=True, root=Path(args.root) if args.root else None)


def row_conditions(con: sqlite3.Connection, lddb: str, pos: int, self_device: str) -> tuple[list[str], bool]:
    rows = con.execute(
        """
        select device, role, comment from xref
        where lddb=? and pos=? and role in ('a','b')
        order by id
        """,
        (lddb, pos),
    ).fetchall()
    conds = []
    self_hold = False
    for r in rows:
        if r["device"] == self_device:
            self_hold = True
            continue
        mark = "" if r["role"] == "a" else "/"
        text = f"{mark}{r['device']}"
        if r["comment"]:
            text += f"={r['comment']}"
        conds.append(text)
    return conds, self_hold


def timer_setpoint(con: sqlite3.Connection, timer: str) -> str:
    source, match = device_match(con)
    rows = con.execute(
        f"""
        select x.opcode, x.const_args from {source}
        where {match} and x.access='write' and x.opcode in ('OUT__16','OUTH__16')
        """,
        (timer,),
    ).fetchall()
    values = []
    for r in rows:
        const = (r["const_args"] or "").split(",")[0]
        if const.isdigit():
            seconds = int(const) * TIMER_UNITS_S.get(r["opcode"], 0.01)
            values.append(f"K{const}(~{seconds:.2f}s)")
        elif const:
            values.append(const)
    return " / ".join(dict.fromkeys(values))


def collect_alarms(con: sqlite3.Connection, pattern: re.Pattern[str]) -> list[dict[str, object]]:
    candidates = con.execute(
        f"""
        select device, device_type, comment, lddb, pos, pou, step, role
        from xref
        where access='write' and role in {DRIVER_ROLES!r}
          and device_type in ({','.join('?' * len(ALARM_BIT_TYPES))})
        order by device_type, number, pos
        """,
        tuple(ALARM_BIT_TYPES),
    ).fetchall()

    out: list[dict[str, object]] = []
    seen_rows: set[tuple[str, str, int]] = set()
    for r in candidates:
        comment = r["comment"] or ""
        if r["device_type"] != "F" and not pattern.search(comment):
            continue
        key = (r["device"], r["lddb"], r["pos"])
        if key in seen_rows:
            continue
        seen_rows.add(key)

        conds, self_hold = row_conditions(con, r["lddb"], r["pos"], r["device"])
        hold = "SET-latch" if r["role"] == "SET" else ("self-hold" if self_hold else "OUT")

        timers = [c for c in conds if re.match(r"^/?T\d+", c)]
        timer_info = ""
        if timers:
            tdev = re.match(r"^/?(T\d+)", timers[0]).group(1)
            sp = timer_setpoint(con, tdev)
            timer_info = f"{tdev} {sp}".strip()

        resets = con.execute(
            f"select x.pou, x.step from {device_match(con)[0]} "
            f"where {device_match(con)[1]} and x.role='RST'", (r["device"],)
        ).fetchall()
        reset_info = "; ".join(f"{x['pou']} st{x['step']}" for x in resets[:6])

        out.append(
            {
                "device": r["device"],
                "comment": comment,
                "hold": hold,
                "pou": r["pou"],
                "step": r["step"],
                "trigger_conditions": " & ".join(conds[:12]),
                "monitor_timer": timer_info,
                "reset_at": reset_info,
            }
        )
    return out


def cmd_list(args: argparse.Namespace) -> int:
    con = open_db(args)
    pattern = re.compile(args.pattern, re.IGNORECASE) if args.pattern else ALARM_COMMENT_RE
    alarms = collect_alarms(con, pattern)

    out = Path(args.output or f"outputs/{default_output_prefix('alarms')}.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = ["device", "comment", "hold", "pou", "step", "trigger_conditions", "monitor_timer", "reset_at"]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in alarms:
            w.writerow(row)

    by_pou: dict[str, int] = {}
    by_hold: dict[str, int] = {}
    devices = {str(a["device"]) for a in alarms}
    for a in alarms:
        by_pou[str(a["pou"])] = by_pou.get(str(a["pou"]), 0) + 1
        by_hold[str(a["hold"])] = by_hold.get(str(a["hold"]), 0) + 1
    print(f"alarm driver rows: {len(alarms)} (devices: {len(devices)})")
    print(f"hold types: " + ", ".join(f"{k}={v}" for k, v in sorted(by_hold.items())))
    print("rows per POU: " + ", ".join(f"{k}={v}" for k, v in sorted(by_pou.items(), key=lambda x: -x[1])[:15]))
    print(f"csv: {out}")
    con.close()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    device = normalize_device(args.device)
    con = open_db(args)
    drivers = con.execute(
        f"select x.* from {device_match(con)[0]} "
        f"where {device_match(con)[1]} and x.access='write' order by x.pos", (device,)
    ).fetchall()
    if not drivers:
        print(f"no driver rows: {device}")
        return 1
    comment = next((d["comment"] for d in drivers if d["comment"]), "")
    print(f"{device} {comment}".rstrip())
    for d in drivers:
        conds, self_hold = row_conditions(con, d["lddb"], d["pos"], device)
        hold = "SET-latch" if d["role"] == "SET" else ("self-hold" if self_hold else d["role"])
        print(f"\n[{d['pou']} st{d['step']}] {hold}  {d['title'] or ''}".rstrip())
        for c in conds:
            print(f"  {'NC ' if c.startswith('/') else 'NO '}{c.lstrip('/')}")
        for c in conds:
            m = re.match(r"^/?(T\d+)", c)
            if m:
                sp = timer_setpoint(con, m.group(1))
                if sp:
                    print(f"  -> {m.group(1)} setpoint {sp}")
    resets = con.execute(
        f"select x.pou, x.step, x.title from {device_match(con)[0]} "
        f"where {device_match(con)[1]} and x.role='RST'", (device,)
    ).fetchall()
    if resets:
        print("\nReset (RST):")
        for r in resets:
            print(f"  {r['pou']} st{r['step']} {r['title'] or ''}".rstrip())
    con.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--db", default=None, help="xref sqlite path")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("list", help="write alarm inventory CSV")
    p.add_argument("--pattern", default=None, help="override alarm comment regex")
    p.add_argument("-o", "--output", default=None)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("show", help="show one alarm in detail")
    p.add_argument("device")
    p.set_defaults(func=cmd_show)
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
