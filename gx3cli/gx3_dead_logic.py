from __future__ import annotations

"""Dead / constant logic detection.

Uses the xref database plus project ladder topology to find both direct dead
logic and constants that propagate through ordinary coils and A/B contacts.
The propagation is deliberately conservative: stateful/external/multi-writer
cases remain unknown instead of being guessed.
"""

import argparse
import csv
import re
import sqlite3
import sys
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gx3cli.gx3_external_inputs import load_refresh_areas, refresh_area_for
from gx3cli.gx3_ladder_logic import (
    condition_refs_from_logic,
    enable_logic_for_output,
    logic_to_text,
    positioned_elements,
)
from gx3cli.gx3_mc_zones import (
    active_zones,
    apply_zone_conditions,
    build_jump_index,
    build_mc_zones,
    jumps_before,
)
from gx3cli.gx3_project_paths import default_comm_prefix, default_output_prefix, default_project_root
from gx3cli.gx3_device_name import split_device
from gx3cli.gx3_xref_read import device_match
from gx3cli.gx3_xref import default_db_path, open_xref_db
from gx3cli.review_gx3_project import LadderRow, load_comments_for_root, load_rows


INTERNAL_BIT_TYPES = ("M", "L", "B", "F", "V", "S")
PROPAGATED_BIT_TYPES = set(INTERNAL_BIT_TYPES) | {"Y"}
WORD_TYPES = ("D", "W", "ZR", "R")
SPECIAL_CONSTANTS = {"SM400": True, "SM401": False}


@dataclass(frozen=True)
class ConstantFact:
    device: str
    value: bool
    where: str
    chain: tuple[str, ...]
    roots: tuple[str, ...]
    depth: int = 0

    @property
    def state(self) -> str:
        return "ALWAYS_ON" if self.value else "ALWAYS_OFF"


@dataclass(frozen=True)
class LogicConstant:
    value: bool | None
    chain: tuple[str, ...] = ()
    roots: tuple[str, ...] = ()
    depth: int = 0


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


def runs_read_by_the_program(con) -> dict[str, list[tuple[int, int]]]:
    """The runs the program reads, by device type."""
    runs: dict[str, list[tuple[int, int]]] = {}
    try:
        rows = con.execute(
            "select device_type, number, range_len from xref"
            " where range_len > 1 and access in ('read', 'both')"
        ).fetchall()
    except Exception:
        return {}
    for row in rows:
        runs.setdefault(str(row["device_type"]), []).append((int(row["number"]), int(row["range_len"])))
    return runs


def read_by_a_run(runs: dict[str, list[tuple[int, int]]], device_type: str, device: str) -> bool:
    parsed = split_device(device)
    if parsed is None:
        return False
    number = parsed[1]
    return any(start <= number < start + length for start, length in runs.get(device_type, ()))


def _merge_evidence(results: list[LogicConstant], value: bool) -> LogicConstant:
    chain: list[str] = []
    roots: list[str] = []
    depth = 0
    for result in results:
        for item in result.chain:
            if item not in chain:
                chain.append(item)
        for item in result.roots:
            if item not in roots:
                roots.append(item)
        depth = max(depth, result.depth)
    return LogicConstant(value, tuple(chain), tuple(roots), depth)


def evaluate_constant_logic(node: dict[str, Any], facts: dict[str, ConstantFact]) -> LogicConstant:
    """Evaluate topology with known device constants using three-valued logic.

    UNKNOWN is represented by value=None.  AND needs one proven false to be
    false; OR needs one proven true to be true.  This lets a known constant
    collapse a branch without pretending the other, unknown branch was read.
    """
    op = node.get("op")
    if op == "true":
        return LogicConstant(True)
    if op == "false":
        source = str(node.get("source") or "")
        roots = (source,) if source else ()
        chain = (f"{source} -> FALSE",) if source else ()
        return LogicConstant(False, chain, roots)
    if op == "contact":
        device = str(node.get("raw_device") or node.get("device") or "")
        fact = facts.get(device)
        if fact is None:
            return LogicConstant(None)
        role = str(node.get("role") or "a")
        value = fact.value if role == "a" else not fact.value
        contact = f"{'/' if role == 'b' else ''}{device} -> {'TRUE' if value else 'FALSE'}"
        return LogicConstant(value, (*fact.chain, contact), fact.roots, fact.depth)
    if op == "and":
        results = [evaluate_constant_logic(child, facts) for child in node.get("args", [])]
        false_results = [result for result in results if result.value is False]
        if false_results:
            return _merge_evidence([false_results[0]], False)
        if results and all(result.value is True for result in results):
            return _merge_evidence(results, True)
        return LogicConstant(None)
    if op == "or":
        results = [evaluate_constant_logic(child, facts) for child in node.get("args", [])]
        true_results = [result for result in results if result.value is True]
        if true_results:
            return _merge_evidence([true_results[0]], True)
        if results and all(result.value is False for result in results):
            return _merge_evidence(results, False)
        return LogicConstant(None)
    # predicate / too_large / unknown and future node kinds are intentionally
    # not folded. Timers, counters and other stateful conditions land here.
    return LogicConstant(None)


def _writer_rows(con: sqlite3.Connection) -> dict[str, list[sqlite3.Row]]:
    rows = con.execute(
        """
        select device, device_type, access, role, opcode, lddb, pos, pou, step,
               coalesce(comment, '') as comment
        from xref where access in ('write', 'both')
        order by device, lddb, pos
        """
    ).fetchall()
    out: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        out[str(row["device"])].append(row)
    return out


def _special_constant_roots(con: sqlite3.Connection) -> dict[tuple[str, int], tuple[str, ...]]:
    roots: dict[tuple[str, int], list[str]] = defaultdict(list)
    try:
        rows = con.execute(
            """
            select lddb, pos, device, role from xref
            where device in ('SM400', 'SM401') and role in ('a', 'b')
            """
        ).fetchall()
    except sqlite3.Error:
        return {}
    for row in rows:
        device = str(row["device"])
        role = str(row["role"])
        value = SPECIAL_CONSTANTS[device] if role == "a" else not SPECIAL_CONSTANTS[device]
        roots[(str(row["lddb"]), int(row["pos"]))].append(
            f"{'/' if role == 'b' else ''}{device}={'TRUE' if value else 'FALSE'}"
        )
    return {key: tuple(value) for key, value in roots.items()}


def _in_refresh(device_type: str, device: str, refresh_areas: list) -> bool:
    parsed = split_device(device)
    if parsed is None or not refresh_areas:
        return False
    return refresh_area_for(device_type, parsed[1], refresh_areas) is not None


def propagate_constant_devices(
    rows: list[LadderRow],
    con: sqlite3.Connection,
    *,
    externals: dict[str, str] | None = None,
    refresh_areas: list | None = None,
) -> tuple[dict[str, ConstantFact], list[dict[str, object]]]:
    """Prove project-wide constants for ordinary single-writer bit coils.

    Only a normal OUT (`role == c`) with exactly one xref writer is eligible.
    SET/RST, pulse/edge writes, instruction destinations and multiple writers
    never enter the propagation graph. Rows under unresolved jump/CALL context
    are also excluded. Resolved MC/CALL enable conditions are folded into the
    rung condition through the existing execution-context model.
    """
    externals = externals or {}
    refresh_areas = refresh_areas or []
    writers = _writer_rows(con)
    special_roots = _special_constant_roots(con)
    zones = build_mc_zones(rows)
    jump_index = build_jump_index(rows)

    candidates: dict[str, dict[str, object]] = {}
    dependents: dict[str, set[str]] = defaultdict(set)

    for row in rows:
        if row.parse_status and row.parse_status != "exact":
            continue
        if jumps_before(jump_index, row.lddb, row.pos):
            continue
        for output in positioned_elements(row):
            if output.role != "c" or not output.devices:
                continue
            ref = output.devices[0]
            device = ref.device
            if ref.device_type not in PROPAGATED_BIT_TYPES:
                continue
            if device in externals or _in_refresh(ref.device_type, device, refresh_areas):
                continue
            device_writers = writers.get(device, [])
            # "Single writer" means one writer occurrence, not one ladder row.
            # Two OUT elements for the same device can share (lddb, pos) and
            # still have separate enable branches/order. Never prove a constant
            # from only the last output element encountered in that case.
            if len(device_writers) != 1:
                continue
            writer = device_writers[0]
            if (str(writer["lddb"]), int(writer["pos"])) != (row.lddb, row.pos):
                continue
            # Another write kind on the same rung still makes final ownership
            # stateful/order-dependent. Normal OUT is the only admitted kind.
            if str(writer["role"] or "") != "c":
                continue

            logic = enable_logic_for_output(row, output)
            logic = apply_zone_conditions(logic, active_zones(zones, row.lddb, row.pos))
            refs = condition_refs_from_logic(logic)
            for condition in refs:
                dependency = str(condition.get("device") or "")
                if dependency:
                    dependents[dependency].add(device)
            candidates[device] = {
                "logic": logic,
                "row": row,
                "pou": str(writer["pou"] or row.lddb),
                "step": writer["step"],
                "comment": str(writer["comment"] or ""),
            }

    facts: dict[str, ConstantFact] = {
        device: ConstantFact(
            device=device,
            value=value,
            where="special relay",
            chain=(f"{device}={'ON' if value else 'OFF'}",),
            roots=(device,),
            depth=0,
        )
        for device, value in SPECIAL_CONSTANTS.items()
    }

    queue = deque(candidates)
    queued = set(candidates)
    while queue:
        device = queue.popleft()
        queued.discard(device)
        if device in facts:
            continue
        candidate = candidates[device]
        logic = candidate["logic"]
        assert isinstance(logic, dict)
        result = evaluate_constant_logic(logic, facts)
        if result.value is None:
            continue
        row = candidate["row"]
        assert isinstance(row, LadderRow)
        step = candidate["step"]
        where = f"{candidate['pou']} st{step if step is not None else '?'}"
        direct_roots = special_roots.get((row.lddb, row.pos), ())
        roots = tuple(dict.fromkeys((*result.roots, *direct_roots)))
        chain = result.chain
        if direct_roots and not chain:
            chain = tuple(f"{root}" for root in direct_roots)
        chain = (*chain, f"{where}: {device}={'ON' if result.value else 'OFF'}")
        fact = ConstantFact(
            device=device,
            value=result.value,
            where=where,
            chain=chain,
            roots=roots or (f"{row.lddb}:{row.pos} static enable={logic_to_text(logic)}",),
            depth=result.depth + 1,
        )
        facts[device] = fact
        for downstream in dependents.get(device, ()):
            if downstream not in facts and downstream not in queued:
                queue.append(downstream)
                queued.add(downstream)

    findings: list[dict[str, object]] = []
    for device, fact in sorted(facts.items()):
        if device in SPECIAL_CONSTANTS:
            continue
        candidate = candidates.get(device, {})
        comment = str(candidate.get("comment") or "")
        dev_type = split_device(device)[0] if split_device(device) else ""
        category = "constant-output" if dev_type == "Y" else "constant-device"
        findings.append(
            {
                "category": category,
                "device": device,
                "constant_state": fact.state,
                "comment": comment,
                "count": 1,
                "where": fact.where,
                "note": f"proven {fact.state} through ordinary single-writer logic",
                "chain": " -> ".join(fact.chain),
                "chain_depth": fact.depth,
                "roots": " | ".join(fact.roots),
            }
        )

    # Once a coil is proven constant, every use of its A/B contact has a known
    # local truth value. This is the cross-reference bridge that catches the
    # user's important case: OFF coil -> A false / B true, ON coil -> reverse.
    try:
        contact_rows = con.execute(
            """
            select device, role, lddb, pos, pou, step, coalesce(comment, '') as comment
            from xref where role in ('a', 'b') order by device, lddb, pos
            """
        ).fetchall()
    except sqlite3.Error:
        contact_rows = []
    for row in contact_rows:
        device = str(row["device"])
        fact = facts.get(device)
        if fact is None or device in SPECIAL_CONSTANTS:
            continue
        role = str(row["role"])
        value = fact.value if role == "a" else not fact.value
        contact_text = f"{'/' if role == 'b' else ''}{device}"
        findings.append(
            {
                "category": "redundant-contact" if value else "dead-contact",
                "device": device,
                "constant_state": "ALWAYS_TRUE" if value else "ALWAYS_FALSE",
                "comment": str(row["comment"] or ""),
                "count": 1,
                "where": f"{row['pou'] or row['lddb']} st{row['step'] if row['step'] is not None else '?'}",
                "note": f"{contact_text} is always {'true' if value else 'false'} because {device} is {fact.state}",
                "chain": " -> ".join((*fact.chain, f"{contact_text}={'TRUE' if value else 'FALSE'}")),
                "chain_depth": fact.depth,
                "roots": " | ".join(fact.roots),
            }
        )

    return facts, findings


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
    parser.add_argument("--no-constant-propagation", action="store_true", help="skip project-wide constant ON/OFF propagation")
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
    covered_by_run = 0

    def in_refresh(device_type: str, device: str) -> bool:
        return _in_refresh(device_type, device, refresh_areas)

    con = open_xref_db(xref_path, root=root)

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

    read_runs = runs_read_by_the_program(con)
    findings: list[dict[str, object]] = []
    source, match = device_match(con)

    def usage_sites(device: str, access: tuple[str, ...], limit: int = 3) -> str:
        rows = con.execute(
            f"""
            select x.pou, x.step from {source}
            where {match} and x.access in ({','.join('?' * len(access))})
            order by x.pou, x.step limit ?
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
                f"select x.role, count(*) as n from {source} "
                f"where {match} and x.role in ('a','b') group by x.role",
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
        if read_by_a_run(read_runs, str(s["device_type"]), device):
            covered_by_run += 1
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

    # 5) project-wide constants ------------------------------------------------
    if not args.no_constant_propagation:
        comments = load_comments_for_root(root)
        rows = load_rows(root, comments)
        facts, propagated = propagate_constant_devices(
            rows,
            con,
            externals=externals,
            refresh_areas=refresh_areas,
        )
        findings.extend(propagated)
        proven = [fact for device, fact in facts.items() if device not in SPECIAL_CONSTANTS]
        print(f"project-wide proven constant coils: {len(proven)}")

    by_cat: dict[str, list[dict[str, object]]] = {}
    for finding in findings:
        by_cat.setdefault(str(finding["category"]), []).append(finding)
    print("")
    category_order = [
        "constant-output", "constant-device", "dead-contact", "redundant-contact",
        "const-off-contact", "always-on-contact", "sm-disabled-row",
        "unread-coil", "unread-word", "set-without-rst",
    ]
    for cat in category_order:
        items = by_cat.get(cat, [])
        print(f"== {cat}: {len(items)}")
        for finding in items[: args.limit]:
            extra = f" {finding.get('constant_state', '')}" if finding.get("constant_state") else ""
            print(
                f"  {str(finding['device']):<12} x{int(finding['count']):<3} "
                f"{str(finding['where']):<28}{extra} {finding.get('comment', '')}"
            )
            if finding.get("chain"):
                print(f"      chain: {finding['chain']}")
        if len(items) > args.limit:
            print(f"  ... {len(items) - args.limit} more (see CSV)")

    out = out_dir / f"{prefix}.csv"
    fieldnames = [
        "category", "device", "constant_state", "comment", "count", "where",
        "note", "chain", "chain_depth", "roots",
    ]
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in findings:
            writer.writerow(row)
    print(f"\ntotal findings: {len(findings)}")
    print(f"devices skipped as network-refreshed (visible to remote stations): {refreshed_skipped}")
    print(f"devices read only as part of a block or digit-specified run: {covered_by_run}")
    print(f"csv: {out}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())