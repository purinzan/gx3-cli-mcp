from __future__ import annotations

"""How big a project is, and which parts of it are worth reading first.

Sixty-odd commands is a lot to face with an unfamiliar project, and every one
of them wants to be told where to look. This answers that: the size of each
program, and the rungs that carry the most logic.

The complexity figure is the number of independent paths through a rung, which
for ladder is one plus the number of parallel branches -- the same count as
cyclomatic complexity, arrived at the way ladder is actually drawn. A rung with
no branch is 1. Every OR adds a path.

Ladder does not have "lines of code": a rung is the unit an engineer reads, and
a wide rung is not the same as several narrow ones. So rungs, conditions per
rung and branches per rung are counted, rather than a line total that would
mean nothing.

Nothing here is re-derived from the project files. The rung expressions come
from rung-text, and the read/write facts from the cross-reference, so a number
in this report and a line in another command cannot disagree.
"""

import argparse
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gx3cli.gx3_project_paths import default_project_root
from gx3cli.gx3_rung_text import RungText, collect

# A device or label term in a rendered condition: X10, /M100, IN_Start.
_TERM = re.compile(r"/?[A-Za-z_][A-Za-z0-9_]*")
_KEYWORDS = {"AND", "OR", "TRUE", "FALSE", "UNKNOWN", "UNSUPPORTED", "P"}


def condition_terms(condition: str) -> int:
    """How many device or label terms the condition tests."""
    if not condition or condition in ("TRUE", "FALSE", "?"):
        return 0
    return sum(1 for term in _TERM.findall(condition) if term.lstrip("/") not in _KEYWORDS)


def branch_count(condition: str) -> int:
    """Parallel branches in the rung: one OR is one extra path."""
    return condition.count(" OR ")


def complexity(condition: str) -> int:
    """Independent paths through the rung. A rung with no branch is 1."""
    return 1 + branch_count(condition)


@dataclass
class ProgramMetrics:
    program: str
    rungs: int = 0
    outputs: int = 0
    conditions: int = 0
    branches: int = 0
    max_complexity: int = 1
    unresolved: int = 0
    devices_read: set[str] = field(default_factory=set)
    devices_written: set[str] = field(default_factory=set)

    @property
    def avg_conditions(self) -> float:
        return round(self.conditions / self.outputs, 2) if self.outputs else 0.0


def program_metrics(items: list[RungText]) -> list[ProgramMetrics]:
    by_program: dict[str, ProgramMetrics] = {}
    seen_rungs: set[tuple[str, int]] = set()
    for item in items:
        metrics = by_program.setdefault(item.lddb, ProgramMetrics(program=item.lddb))
        if (item.lddb, item.pos) not in seen_rungs:
            seen_rungs.add((item.lddb, item.pos))
            metrics.rungs += 1
        metrics.outputs += 1
        metrics.conditions += condition_terms(item.condition)
        metrics.branches += branch_count(item.condition)
        metrics.max_complexity = max(metrics.max_complexity, complexity(item.condition))
        if item.condition == "?" or "UNKNOWN" in item.condition:
            metrics.unresolved += 1
        metrics.devices_written.add(item.device)
    return sorted(by_program.values(), key=lambda m: -m.rungs)


def hotspots(items: list[RungText], limit: int) -> list[RungText]:
    """The rungs carrying the most logic, hardest first."""
    return sorted(
        items,
        key=lambda item: (-complexity(item.condition), -condition_terms(item.condition), item.lddb, item.pos),
    )[:limit]


def xref_totals(db: Path) -> dict[str, int]:
    """Device counts from the cross-reference, when one has been built."""
    if not db.exists():
        return {}
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "select count(distinct device),"
            " sum(case when access in ('write','both') then 1 else 0 end),"
            " sum(case when access='read' then 1 else 0 end) from xref"
        ).fetchone()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return {"devices": rows[0] or 0, "writes": rows[1] or 0, "reads": rows[2] or 0}


def render(
    programs: list[ProgramMetrics], top: list[RungText], totals: dict[str, int]
) -> list[str]:
    lines: list[str] = []
    rungs = sum(m.rungs for m in programs)
    outputs = sum(m.outputs for m in programs)
    unresolved = sum(m.unresolved for m in programs)

    lines.append("project")
    lines.append(f"  programs   {len(programs)}")
    lines.append(f"  rungs      {rungs}")
    lines.append(f"  outputs    {outputs}")
    if totals:
        lines.append(f"  devices    {totals.get('devices', 0)}")
        lines.append(f"  reads      {totals.get('reads', 0)}")
        lines.append(f"  writes     {totals.get('writes', 0)}")
    if unresolved:
        # Said plainly rather than folded into the totals: these are rungs the
        # reader could not turn into an expression, and a complexity figure
        # that quietly excluded them would overstate how well the project is
        # understood.
        lines.append(f"  unresolved {unresolved}  (rungs whose condition could not be read)")

    lines.append("")
    lines.append(f"{'program':<24} {'rungs':>6} {'outputs':>8} {'cond/out':>9} {'branches':>9} {'worst':>6}")
    for metrics in programs:
        lines.append(
            f"{metrics.program:<24} {metrics.rungs:>6} {metrics.outputs:>8} "
            f"{metrics.avg_conditions:>9} {metrics.branches:>9} {metrics.max_complexity:>6}"
        )

    if top:
        lines.append("")
        lines.append("most involved rungs")
        width = max(len(item.location) for item in top)
        for item in top:
            paths = complexity(item.condition)
            terms = condition_terms(item.condition)
            lines.append(f"  {item.location:<{width}}  paths={paths} terms={terms}  -> {item.device}")
    return lines


def to_json(
    programs: list[ProgramMetrics], top: list[RungText], totals: dict[str, int]
) -> dict[str, Any]:
    return {
        "project": {
            "programs": len(programs),
            "rungs": sum(m.rungs for m in programs),
            "outputs": sum(m.outputs for m in programs),
            "unresolved": sum(m.unresolved for m in programs),
            **totals,
        },
        "programs": [
            {
                "program": m.program,
                "rungs": m.rungs,
                "outputs": m.outputs,
                "conditions": m.conditions,
                "conditions_per_output": m.avg_conditions,
                "branches": m.branches,
                "max_complexity": m.max_complexity,
                "unresolved": m.unresolved,
                "devices_written": len(m.devices_written),
            }
            for m in programs
        ],
        "hotspots": [
            {
                "lddb": item.lddb,
                "pos": item.pos,
                "device": item.device,
                "complexity": complexity(item.condition),
                "terms": condition_terms(item.condition),
                "condition": item.condition,
            }
            for item in top
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Project size and where the logic is concentrated."
    )
    parser.add_argument("--root", default=str(default_project_root()))
    parser.add_argument("--xref-db", default="", help="cross-reference DB, for device totals")
    parser.add_argument("--top", type=int, default=10, help="how many rungs to list (0 for none)")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("-o", "--output", default="")
    args = parser.parse_args(argv)

    root = Path(args.root)
    items = collect(root)
    if not items:
        print("no rungs found")
        return 0

    programs = program_metrics(items)
    top = hotspots(items, args.top) if args.top > 0 else []
    totals = xref_totals(Path(args.xref_db)) if args.xref_db else {}

    if args.format == "json":
        body = json.dumps(to_json(programs, top, totals), ensure_ascii=False, indent=2)
    else:
        body = "\n".join(render(programs, top, totals))

    if args.output:
        Path(args.output).write_text(body + "\n", encoding="utf-8")
        print(f"wrote: {args.output}")
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
