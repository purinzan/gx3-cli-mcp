from __future__ import annotations

"""Static lint checks for GX Works3 projects.

Checks are registered in a small registry so new ones can be added without
touching the CLI plumbing. Each check receives a shared ``LintContext`` and
returns a list of finding dictionaries with a common schema:

    check, severity, device, comment, count, locations, detail, review_note

Phase 1 checks:
  duplicate-coil   same output/coil driven from multiple rungs (reuses
                   review_gx3_project.review_duplicate_coils)
  multi-writer     one word device (D/W/R/...) written from several POUs or
                   several rungs (uses the xref DB for POU name and real step)

Phase 3 checks (math / data-type):
  div-by-zero      division by a constant 0, or by a device that has no writer
  width-mismatch   32-bit destination high word (N+1) reused by another op
  signed-compare   signed compare against an out-of-range constant or a value
                   sourced from unsigned/buffer-memory style data

Output: one CSV per check (same shape as the static review CSVs), a summary
JSON, and a console summary. Uncertain math findings are severity=info.
"""

import argparse
import json
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gx3cli.extract_gx3_extended_instruction_knowledge import (
    extract_args_text,
    extract_elements,
    header_tokens,
    parse_header_ops,
    top_level_items,
)
from gx3cli.gx3_arg_decode import ArgOcc, base_opcode, decode_args
from gx3cli.gx3_xref import default_db_path as xref_db_path
from gx3cli.gx3_index_lite import default_db_path as lite_db_path
from gx3cli.gx3_project_paths import default_output_prefix, default_project_root
from gx3cli.gx3_cli import project_label_from_root
from gx3cli.gx3_alarm_map import ALARM_COMMENT_RE, collect_alarms
from gx3cli.review_gx3_project import (
    LadderRow,
    device_comment_text,
    load_comments_for_root,
    load_rows,
    review_duplicate_coils,
    write_csv,
)
from gx3cli.extract_hmi_build_info import CommentInfo


WORD_TYPES = {"D", "W", "R", "ZR", "SD", "SW", "G", "UG"}
CONST_VALUE_RE = re.compile(r"v=([^:}]+)")
CONST_SIGN_RE = re.compile(r"si=([^:}]+)")
RESET_WRITER_BASES = {"RST", "RST__16"}
INDEXED_DETAIL_RE = re.compile(r"\bindexed\b", re.IGNORECASE)
SCRATCH_TEXT_RE = re.compile(r"\b(calculation|calc|scratch|index|tmp|temp|working|register)\b", re.IGNORECASE)
HMI_DISPLAY_TEXT_RE = re.compile(r"\b(hmi|display|screen|page|manual|button|panel|jog|window)\b", re.IGNORECASE)
COUNT_HISTORY_TEXT_RE = re.compile(r"\b(count|counter|retry|time|seconds?|history|result|stats?)\b", re.IGNORECASE)
BUFFER_TEXT_RE = re.compile(r"\b(buffer|record|log|queue|message|payload)\b", re.IGNORECASE)
PROCESS_DATA_TEXT_RE = re.compile(
    r"\b(process|recipe|setting|quality|measurement|target|limit|position|value|product|payload)\b",
    re.IGNORECASE,
)
INTERFACE_TEXT_RE = re.compile(
    r"\b(interface|i/f|network|connection|input|output|communication|handoff|external|word input)\b",
    re.IGNORECASE,
)

DIV_BASES = {"/", "D/", "B/", "E/", "$/"}
# 32-bit (double word) instruction bases. E-float ops are also 32-bit wide.
WIDTH32_BASES = {
    "D+", "D-", "D*", "D/", "DMOV", "DCML", "DAND", "DOR", "DXOR", "DXNR",
    "DINC", "DDEC", "DBCD", "DBIN", "DFLT", "DINT", "DNEG", "DXCH", "DFMOV",
    "DWSUM", "DSUM", "DROL", "DROR", "DRCL", "DRCR", "DSFL", "DSFR",
    "E+", "E-", "E*", "E/", "EMOV", "EDMOV", "ENEG", "ECMP",
}
COMPARE_RE = re.compile(r"^(?:(?:LD|AND|OR)?(?:D|E|\$)?|BKCMP)(?:=|<>|<=|>=|<|>)$")


FINDING_FIELDS = ["check", "severity", "device", "comment", "count", "locations", "detail", "review_note"]

CheckFunc = Callable[["LintContext"], list[dict[str, object]]]
CHECKS: "dict[str, tuple[CheckFunc, str]]" = {}
CHECK_IDS = {
    "duplicate-coil": "GX0001",
    "multi-writer": "GX0002",
    "alarm-quality": "GX0003",
    "unused-device": "GX0004",
    "comment-conflict": "GX0005",
    "link-range": "GX0006",
    "compare-type": "GX0101",
    "div-by-zero": "GX0102",
    "width-mismatch": "GX0103",
    "signed-compare": "GX0104",
}


def register(name: str, description: str) -> Callable[[CheckFunc], CheckFunc]:
    def deco(func: CheckFunc) -> CheckFunc:
        CHECKS[name] = (func, description)
        return func

    return deco


@dataclass
class OpArg:
    index: int
    kind: str  # "device" | "const" | "other"
    device: str = ""
    device_type: str = ""
    number: int = 0
    access: str = ""
    detail: str = ""
    const: str = ""
    signed: str = ""


@dataclass
class RowOp:
    role: str
    opcode: str
    base: str
    args: list[OpArg]


@dataclass
class LintContext:
    root: Path
    rows: list[LadderRow]
    comments: dict[tuple[str, int], CommentInfo]
    xref: sqlite3.Connection | None = None
    lite: sqlite3.Connection | None = None
    link: sqlite3.Connection | None = None
    project_label: str = ""
    row_ops_cache: dict[str, list[RowOp]] = field(default_factory=dict)

    def comment(self, device_type: str, number: int) -> str:
        return device_comment_text(self.comments.get((device_type, number), CommentInfo()))

    def ops_for(self, row: LadderRow) -> list[RowOp]:
        key = f"{row.lddb}:{row.pos}"
        cached = self.row_ops_cache.get(key)
        if cached is None:
            cached = decode_row_ops(row.data)
            self.row_ops_cache[key] = cached
        return cached


def decode_row_ops(data: str) -> list[RowOp]:
    """Positional per-operation argument view including constants.

    Mirrors gx3_arg_decode.parse_row_occurrences but keeps constant operands
    at their argument index so math checks can inspect divisors etc.
    """
    tokens = header_tokens(data)
    hops = parse_header_ops(data)
    ce_elements = [e for e in extract_elements(data) if "s=ce{" in e]
    out: list[RowOp] = []
    for i, hop in enumerate(hops):
        element = ce_elements[i] if i < len(ce_elements) else ""
        args_text = extract_args_text(element)
        raw_args = top_level_items(args_text) if args_text else []
        next_tok = hops[i + 1].token_index if i + 1 < len(hops) else len(tokens)
        arg_tokens = tokens[hop.token_index + 1 : next_tok]
        occs: list[ArgOcc] = decode_args(raw_args, arg_tokens or [hop.device_type], hop.op)
        by_index: dict[int, ArgOcc] = {}
        for occ in occs:
            by_index.setdefault(occ.arg_index, occ)

        args: list[OpArg] = []
        for idx, raw in enumerate(raw_args):
            if raw.startswith("c{"):
                m = CONST_VALUE_RE.search(raw)
                s = CONST_SIGN_RE.search(raw)
                args.append(OpArg(index=idx, kind="const", const=m.group(1) if m else "", signed=s.group(1) if s else ""))
            else:
                occ = by_index.get(idx)
                if occ is not None:
                    args.append(
                        OpArg(
                            index=idx,
                            kind="device",
                            device=occ.device,
                            device_type=occ.device_type,
                            number=occ.number,
                            access=occ.access,
                            detail=occ.detail,
                        )
                    )
                else:
                    args.append(OpArg(index=idx, kind="other"))
        out.append(RowOp(role=hop.op, opcode=hop.op, base=base_opcode(hop.op), args=args))
    return out


def const_int(text: str):
    text = text.strip()
    try:
        if text.lower().startswith("0x") or re.fullmatch(r"[0-9A-Fa-f]+H", text):
            return int(text.rstrip("Hh"), 16)
        return int(text, 0)
    except (ValueError, TypeError):
        try:
            return int(float(text))
        except (ValueError, TypeError):
            return None


# --------------------------------------------------------------------------
# Phase 1 checks
# --------------------------------------------------------------------------


@register("duplicate-coil", "same output/coil driven from multiple rungs")
def check_duplicate_coil(ctx: LintContext) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for rec in review_duplicate_coils(ctx.rows, ctx.comments):
        out.append(
            {
                "check": "duplicate-coil",
                "severity": rec["severity"],
                "device": rec["device"],
                "comment": rec["comment"],
                "count": rec["driver_count"],
                "locations": rec["locations"],
                "detail": f"roles: {rec['roles']}",
                "review_note": rec["review_note"],
            }
        )
    return out


@register("multi-writer", "word device written from multiple POUs/rungs")
def check_multi_writer(ctx: LintContext) -> list[dict[str, object]]:
    if ctx.xref is None:
        print("  multi-writer: xref DB not found; run `gx3_cli.py xref build --root <root>` first (check skipped)")
        return []
    rows = ctx.xref.execute(
        """
        select device, device_type, pou, step, opcode, role, const_args, detail, lddb, pos, title, comment
        from xref
        where access in ('write', 'both')
        order by device_type, number, pou, pos
        """
    ).fetchall()
    by_device: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if str(row["device_type"]) not in WORD_TYPES:
            continue
        by_device[str(row["device"])].append(row)

    out: list[dict[str, object]] = []
    for device, writers in by_device.items():
        locs = {(w["lddb"], w["pos"]) for w in writers}
        pous = {str(w["pou"]) for w in writers if w["pou"]}
        if len(locs) <= 1:
            continue
        comment = next((str(w["comment"]) for w in writers if w["comment"]), "")
        severity, categories, review_note = classify_multi_writer(writers)
        nonreset_pous = {
            str(w["pou"])
            for w in writers
            if w["pou"] and base_opcode(str(w["opcode"] or w["role"] or "")) not in RESET_WRITER_BASES
        }
        loc_text = " | ".join(
            f"{w['pou'] or w['lddb']}:st{w['step'] if w['step'] is not None else '?'}:{w['opcode'] or w['role']}"
            for w in writers[:12]
        )
        out.append(
            {
                "check": "multi-writer",
                "severity": severity,
                "device": device,
                "comment": comment,
                "count": len(locs),
                "locations": loc_text,
                "detail": (
                    f"distinct_rungs={len(locs)}; distinct_POUs={len(pous)}; "
                    f"nonreset_POUs={len(nonreset_pous)}; categories={','.join(categories) or 'none'}"
                ),
                "review_note": review_note,
            }
        )
    out.sort(key=lambda item: (0 if item["severity"] == "high" else 1, -int(item["count"])))
    return out


def classify_multi_writer(writers: list[sqlite3.Row]) -> tuple[str, list[str], str]:
    """Classify a word multi-writer without treating every cross-POU write as high.

    GX Works projects often write the same word from many rows for scratch
    registers, screen numbers, counters, history buffers, and reset paths. A
    high finding is reserved for devices with more than one non-reset POU and
    no lower-confidence/low-risk classification.
    """
    text = " ".join(
        str(w[key] or "")
        for w in writers
        for key in ("device", "comment", "title", "detail", "pou", "opcode", "role")
    )
    categories: list[str] = []
    if INDEXED_DETAIL_RE.search(text):
        categories.append("indexed-address")
    if SCRATCH_TEXT_RE.search(text):
        categories.append("scratch")
    if HMI_DISPLAY_TEXT_RE.search(text):
        categories.append("hmi-display")
    if COUNT_HISTORY_TEXT_RE.search(text):
        categories.append("counter-history")
    if BUFFER_TEXT_RE.search(text):
        categories.append("buffer-record")
    if PROCESS_DATA_TEXT_RE.search(text):
        categories.append("process-data")
    if INTERFACE_TEXT_RE.search(text):
        categories.append("interface-data")
    device_types = {str(w["device_type"]) for w in writers}
    if device_types == {"ZR"}:
        categories.append("file-register-range")

    nonreset_pous = {
        str(w["pou"])
        for w in writers
        if w["pou"] and base_opcode(str(w["opcode"] or w["role"] or "")) not in RESET_WRITER_BASES
    }
    if len(nonreset_pous) <= 1:
        return (
            "medium",
            categories,
            "word has one non-reset owner plus reset/clear writers; verify reset timing but do not treat as owner conflict",
        )
    if categories:
        return (
            "medium",
            categories,
            "multi-writer is a likely scratch/HMI/counter/buffer or indexed data pattern; confirm ownership before logic changes",
        )
    return (
        "high",
        categories,
        "word device has multiple non-reset POU owners; confirm no scan-order overwrite or conflicting state ownership",
    )


# --------------------------------------------------------------------------
# Phase 3 checks (math / data type)
# --------------------------------------------------------------------------


def device_has_writer(ctx: LintContext, device: str) -> bool | None:
    """True/False if xref knows writers, None if xref unavailable."""
    if ctx.xref is None:
        return None
    row = ctx.xref.execute(
        "select count(*) from xref where device=? and access in ('write','both')", (device,)
    ).fetchone()
    return int(row[0]) > 0


def device_is_external(ctx: LintContext, device: str) -> bool:
    if ctx.lite is None:
        return False
    row = ctx.lite.execute("select 1 from external_sources where device=? limit 1", (device,)).fetchone()
    return row is not None


@register("alarm-quality", "alarm candidates missing reset/timer/latch clarity")
def check_alarm_quality(ctx: LintContext) -> list[dict[str, object]]:
    if ctx.xref is None:
        print("  alarm-quality: xref DB not found; run `gx3_cli.py xref build --root <root>` first (check skipped)")
        return []
    out: list[dict[str, object]] = []
    by_device: dict[str, list[dict[str, object]]] = defaultdict(list)
    for alarm in collect_alarms(ctx.xref, ALARM_COMMENT_RE):
        by_device[str(alarm["device"])].append(alarm)
    for device, rows in by_device.items():
        comment = str(rows[0].get("comment", ""))
        reset_count = sum(1 for r in rows if str(r.get("reset_at", "")).strip())
        timer_count = sum(1 for r in rows if str(r.get("monitor_timer", "")).strip())
        latch_count = sum(1 for r in rows if str(r.get("hold", "")) in {"SET-latch", "self-hold"})
        issues = []
        if reset_count == 0:
            issues.append("no reset row")
        if timer_count == 0 and re.search(r"time|timeout", comment, re.IGNORECASE):
            issues.append("time-related alarm without resolved timer")
        if latch_count == 0:
            issues.append("plain OUT alarm, not latched/self-held")
        if not issues:
            continue
        out.append(
            {
                "check": "alarm-quality",
                "severity": "medium" if "no reset row" in issues else "info",
                "device": device,
                "comment": comment,
                "count": len(issues),
                "locations": " | ".join(f"{r.get('pou')} st{r.get('step')}" for r in rows[:8]),
                "detail": "; ".join(issues),
                "review_note": "alarm behavior should have clear latch/trigger/reset evidence before field changes",
            }
        )
    return out


@register("unused-device", "devices written but never read, or comments on unused devices")
def check_unused_device(ctx: LintContext) -> list[dict[str, object]]:
    if ctx.lite is None:
        print("  unused-device: index DB not found; run `gx3_cli.py index-lite build --root <root>` first (check skipped)")
        return []
    out: list[dict[str, object]] = []
    rows = ctx.lite.execute(
        """
        select device, device_type, number, comment, occurrences, driver_rows, condition_uses, first_lddb, first_pos, first_title
        from devices
        where (driver_rows > 0 and condition_uses = 0)
           or (occurrences = 0 and coalesce(comment, '') <> '')
        order by driver_rows desc, device_type, number
        limit 1000
        """
    ).fetchall()
    for r in rows:
        comment = str(r["comment"] or "")
        if r["device_type"] in {"T", "C"}:
            continue
        if re.search(r"spare|unused|not used", comment, re.IGNORECASE):
            continue
        issue = "comment exists but no ladder use" if int(r["occurrences"]) == 0 else "written but never read as condition"
        out.append(
            {
                "check": "unused-device",
                "severity": "info",
                "device": r["device"],
                "comment": comment,
                "count": int(r["driver_rows"] or 0),
                "locations": f"{r['first_lddb']}:{r['first_pos']}:{r['first_title'] or ''}",
                "detail": issue,
                "review_note": "confirm this is intentional spare/output-only behavior before reusing the address",
            }
        )
    return out


@register("comment-conflict", "duplicate or contradictory device comments")
def check_comment_conflict(ctx: LintContext) -> list[dict[str, object]]:
    if ctx.lite is None:
        print("  comment-conflict: index DB not found; run `gx3_cli.py index-lite build --root <root>` first (check skipped)")
        return []
    groups: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for r in ctx.lite.execute(
        "select device, device_type, number, all_text from comments where coalesce(all_text, '') <> ''"
    ):
        text = re.sub(r"\s+", " ", str(r["all_text"] or "")).strip()
        if len(text) < 4 or re.search(r"spare|unused|not used", text, re.IGNORECASE):
            continue
        groups[text].append(r)
    out: list[dict[str, object]] = []
    for text, rows in groups.items():
        if len(rows) <= 1:
            continue
        device_types = {str(r["device_type"]) for r in rows}
        severity = "medium" if len(device_types) > 1 else "info"
        out.append(
            {
                "check": "comment-conflict",
                "severity": severity,
                "device": rows[0]["device"],
                "comment": text,
                "count": len(rows),
                "locations": ", ".join(str(r["device"]) for r in rows[:20]),
                "detail": "same comment assigned to multiple devices",
                "review_note": "duplicate comments can hide wrong-device edits or stale comment moves",
            }
        )
    out.sort(key=lambda x: (0 if x["severity"] == "medium" else 1, -int(x["count"])))
    return out[:1000]


@register("link-range", "project writes a linked inbound communication device")
def check_link_range(ctx: LintContext) -> list[dict[str, object]]:
    if ctx.xref is None or ctx.link is None:
        print("  link-range: xref/link DB not found; pass --xref-db and --link-db (check skipped)")
        return []
    project = ctx.project_label or project_label_from_root(ctx.root)
    rows = ctx.link.execute(
        """
        select project_a, device_a, project_b, device_b, direction, link_type, confidence, role, evidence
        from link_map
        where project_a=? or project_b=?
        """,
        (project, project),
    ).fetchall()
    inbound: dict[str, sqlite3.Row] = {}
    for r in rows:
        if str(r["project_b"]) == project and str(r["direction"]) == f"{r['project_a']} -> {r['project_b']}":
            inbound[str(r["device_b"])] = r
        elif str(r["project_a"]) == project and str(r["direction"]) == f"{r['project_b']} -> {r['project_a']}":
            inbound[str(r["device_a"])] = r
    out: list[dict[str, object]] = []
    for device, link in inbound.items():
        writers = ctx.xref.execute(
            "select * from xref where device=? and access in ('write','both') order by pou, pos limit 12",
            (device,),
        ).fetchall()
        if not writers:
            continue
        out.append(
            {
                "check": "link-range",
                "severity": "high" if str(link["confidence"]) == "high" else "medium",
                "device": device,
                "comment": str(writers[0]["comment"] or ""),
                "count": len(writers),
                "locations": " | ".join(f"{w['pou']} st{w['step']} {w['opcode'] or w['role']}" for w in writers),
                "detail": f"inbound link from {link['project_a']}:{link['device_a']} to {link['project_b']}:{link['device_b']} is also written locally",
                "review_note": "writing a receive/link-refresh address can mask or overwrite the partner PLC signal",
            }
        )
    return out


@register("compare-type", "compare operands with likely signed/unsigned or width mismatch")
def check_compare_type(ctx: LintContext) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in ctx.rows:
        loc = f"{row.lddb}:{row.pos}:{row.title}"
        for op in ctx.ops_for(row):
            if not COMPARE_RE.match(op.base):
                continue
            wide_op = op.base.startswith(("D", "E"))
            devices = [a for a in op.args if a.kind == "device"]
            consts = [a for a in op.args if a.kind == "const"]
            for arg in devices:
                comment = ctx.comment(arg.device_type, arg.number)
                if arg.device_type in {"UG"} or re.search(r"buffer|link", comment, re.IGNORECASE):
                    out.append(
                        {
                            "check": "compare-type",
                            "severity": "info",
                            "device": arg.device,
                            "comment": comment,
                            "count": 1,
                            "locations": loc,
                            "detail": f"{op.opcode} compares communication/module data; signedness may be unsigned",
                            "review_note": "verify signed/unsigned interpretation and scaling before changing thresholds",
                        }
                    )
            if not wide_op:
                for c in consts:
                    value = const_int(c.const)
                    if value is not None and value > 0xFFFF:
                        out.append(
                            {
                                "check": "compare-type",
                                "severity": "medium",
                                "device": f"const={c.const}",
                                "comment": "",
                                "count": 1,
                                "locations": loc,
                                "detail": f"{op.opcode} is a 16-bit compare against {value}, above 0xFFFF",
                                "review_note": "use/check a double-word compare when the threshold exceeds one word",
                            }
                        )
    return out


@register("div-by-zero", "division by constant 0 or by a device with no writer")
def check_div_by_zero(ctx: LintContext) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in ctx.rows:
        pou_loc = f"{row.lddb}:{row.pos}:{row.title}"
        for op in ctx.ops_for(row):
            if op.base not in DIV_BASES:
                continue
            if len(op.args) < 2:
                continue
            sources = op.args[:-1]  # last operand is the quotient destination
            if not sources:
                continue
            divisor = sources[-1]
            if divisor.kind == "const":
                value = const_int(divisor.const)
                if value == 0:
                    out.append(
                        {
                            "check": "div-by-zero",
                            "severity": "high",
                            "device": f"const={divisor.const}",
                            "comment": "",
                            "count": 1,
                            "locations": pou_loc,
                            "detail": f"{op.opcode} divisor is constant 0",
                            "review_note": "division by literal zero raises an operation error at runtime",
                        }
                    )
            elif divisor.kind == "device" and divisor.device_type in WORD_TYPES:
                has_writer = device_has_writer(ctx, divisor.device)
                if has_writer is False and not device_is_external(ctx, divisor.device):
                    out.append(
                        {
                            "check": "div-by-zero",
                            "severity": "info",
                            "device": divisor.device,
                            "comment": ctx.comment(divisor.device_type, divisor.number),
                            "count": 1,
                            "locations": pou_loc,
                            "detail": f"{op.opcode} divisor has no writer in project",
                            "review_note": "divisor may stay 0 unless set by parameter/HMI/comms; verify it is initialized non-zero",
                        }
                    )
    return out


@register("width-mismatch", "32-bit destination high word reused by another op")
def check_width_mismatch(ctx: LintContext) -> list[dict[str, object]]:
    # Registry of second (high) words written by 32-bit destinations.
    high_words: dict[tuple[str, int], dict[str, object]] = {}
    for row in ctx.rows:
        for op in ctx.ops_for(row):
            if op.base not in WIDTH32_BASES:
                continue
            for arg in op.args:
                if arg.kind != "device" or arg.device_type not in WORD_TYPES:
                    continue
                if arg.access not in ("write", "both"):
                    continue
                if arg.detail:  # indexed / buffer / digit form: pairing unclear
                    continue
                key = (arg.device_type, arg.number + 1)
                high_words.setdefault(
                    key,
                    {
                        "base": arg.device,
                        "opcode": op.opcode,
                        "loc": f"{row.lddb}:{row.pos}:{row.title}",
                    },
                )

    out: list[dict[str, object]] = []
    seen: set[tuple[str, int, str, int]] = set()
    for row in ctx.rows:
        loc = f"{row.lddb}:{row.pos}:{row.title}"
        for op in ctx.ops_for(row):
            is32 = op.base in WIDTH32_BASES
            for arg in op.args:
                if arg.kind != "device" or arg.device_type not in WORD_TYPES or arg.detail:
                    continue
                key = (arg.device_type, arg.number)
                src = high_words.get(key)
                if src is None or src["loc"] == loc:
                    continue
                dedup = (arg.device_type, arg.number, str(src["loc"]), row.pos)
                if dedup in seen:
                    continue
                seen.add(dedup)
                if arg.access in ("write", "both"):
                    severity = "medium"
                    detail = f"{op.opcode} writes {arg.device}, the high word of 32-bit {src['opcode']} {src['base']}"
                elif is32:
                    severity = "medium"
                    detail = f"32-bit {op.opcode} base {arg.device} overlaps high word of {src['opcode']} {src['base']}"
                else:
                    severity = "info"
                    detail = f"{op.opcode} reads {arg.device}, the high word of 32-bit {src['opcode']} {src['base']}"
                out.append(
                    {
                        "check": "width-mismatch",
                        "severity": severity,
                        "device": arg.device,
                        "comment": ctx.comment(arg.device_type, arg.number),
                        "count": 1,
                        "locations": f"{src['loc']}  <->  {loc}",
                        "detail": detail,
                        "review_note": "32-bit op occupies N and N+1; another op touching N+1 may corrupt or misread the value",
                    }
                )
    out.sort(key=lambda item: 0 if item["severity"] == "medium" else 1)
    return out


@register("signed-compare", "signed compare against out-of-range or unsigned source")
def check_signed_compare(ctx: LintContext) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in ctx.rows:
        loc = f"{row.lddb}:{row.pos}:{row.title}"
        for op in ctx.ops_for(row):
            if not COMPARE_RE.match(op.base):
                continue
            is32 = op.base.startswith("D") or op.base.startswith("E") or "D" in op.base[:2]
            wide = op.base.startswith("D") or op.base.startswith("E")
            for arg in op.args:
                if arg.kind == "const":
                    value = const_int(arg.const)
                    if value is None:
                        continue
                    if not wide and (value > 32767 or value < -32768):
                        out.append(
                            {
                                "check": "signed-compare",
                                "severity": "info",
                                "device": f"const={arg.const}",
                                "comment": "",
                                "count": 1,
                                "locations": loc,
                                "detail": f"16-bit signed compare {op.opcode} against constant {value} outside -32768..32767",
                                "review_note": "value does not fit a signed 16-bit word; compare result may be unexpected",
                            }
                        )
                elif arg.kind == "device" and arg.device_type in {"UG"}:
                    out.append(
                        {
                            "check": "signed-compare",
                            "severity": "info",
                            "device": arg.device,
                            "comment": "",
                            "count": 1,
                            "locations": loc,
                            "detail": f"signed compare {op.opcode} operand read from buffer memory (often unsigned)",
                            "review_note": "buffer-memory / module data is frequently unsigned; a signed compare can misorder high values",
                        }
                    )
    return out


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------


def open_optional(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def run_checks(ctx: LintContext, checks: list[str], prefix: str) -> dict[str, object]:
    summary: dict[str, object] = {"root": str(ctx.root), "checks": {}, "outputs": []}
    total = 0
    for name in checks:
        func, _desc = CHECKS[name]
        findings = func(ctx)
        sev_counts: dict[str, int] = defaultdict(int)
        for f in findings:
            sev_counts[str(f["severity"])] += 1
        out_path = Path(f"{prefix}_{name}.csv")
        write_csv(out_path, findings, FINDING_FIELDS)
        summary["checks"][name] = {"count": len(findings), "by_severity": dict(sev_counts)}
        summary["outputs"].append(str(out_path))
        total += len(findings)
        sev_text = ", ".join(f"{k}={v}" for k, v in sorted(sev_counts.items())) or "none"
        print(f"  {name:<15} findings={len(findings):<4} ({sev_text}) -> {out_path}")
    summary["total_findings"] = total
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Static lint checks for a GX Works3 project")
    parser.add_argument("root", nargs="?", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--checks", default="all", help="comma-separated check names or 'all'")
    parser.add_argument("--xref-db", default=None, help="xref sqlite path (default: .gx3_index/<project>_xref.sqlite)")
    parser.add_argument("--index-db", default=None, help="lite index sqlite path (default: .gx3_index/<project>.sqlite)")
    parser.add_argument("--link-db", default=".gx3_index/link_map.sqlite", help="link-map sqlite path for link-range check")
    parser.add_argument("--out-prefix", default=default_output_prefix("lint"), help="output CSV/JSON prefix")
    parser.add_argument("--list-checks", action="store_true", help="list available checks and exit")
    parser.add_argument("--fail-on", default="", help="exit non-zero if any finding has one of these severities, e.g. high")
    return parser


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)

    if args.list_checks:
        for name, (_func, desc) in CHECKS.items():
            print(f"{CHECK_IDS.get(name, 'GX9999'):<7} {name:<24} {desc}")
        return 0

    if args.checks.strip().lower() == "all":
        checks = list(CHECKS)
    else:
        checks = [c.strip() for c in args.checks.split(",") if c.strip()]
        unknown = [c for c in checks if c not in CHECKS]
        if unknown:
            raise SystemExit(f"unknown check(s): {', '.join(unknown)} (available: {', '.join(CHECKS)})")

    root = Path(args.root)
    print(f"lint root: {root}")
    print("loading ladder rows and comments ...")
    comments = load_comments_for_root(root)
    rows = load_rows(root, comments)

    xref_path = Path(args.xref_db) if args.xref_db else xref_db_path(root)
    lite_path = Path(args.index_db) if args.index_db else Path(lite_db_path(root))
    link_path = Path(args.link_db) if args.link_db else Path()
    ctx = LintContext(
        root=root,
        rows=rows,
        comments=comments,
        xref=open_optional(xref_path),
        lite=open_optional(lite_path),
        link=open_optional(link_path) if link_path else None,
        project_label=project_label_from_root(root),
    )

    print(f"rows={len(rows)} xref={'ok' if ctx.xref else 'missing'} index={'ok' if ctx.lite else 'missing'} link={'ok' if ctx.link else 'missing'}")
    print("running checks:")
    summary = run_checks(ctx, checks, args.out_prefix)

    summary_path = Path(f"{args.out_prefix}_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"total findings: {summary['total_findings']}")
    print(f"summary: {summary_path}")

    if args.fail_on:
        fail_sevs = {s.strip() for s in args.fail_on.split(",") if s.strip()}
        for name in checks:
            by_sev = summary["checks"][name]["by_severity"]
            if any(by_sev.get(s, 0) for s in fail_sevs):
                print(f"fail-on: found {args.fail_on} severity findings", file=sys.stderr)
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
