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
import contextlib
import json
import re
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from gx3cli.gx3_arg_decode import base_opcode, parse_row_operations
from gx3cli.gx3_analysis_state import AnalysisState, checked, not_evaluated, summarise
from gx3cli.gx3_xref import default_db_path as xref_db_path, open_xref_db
from gx3cli.gx3_index_lite import default_db_path as lite_db_path
from gx3cli.gx3_project_paths import (
    default_comm_prefix,
    default_output_prefix,
    default_project_root,
)
from gx3cli.gx3_device_name import format_device, split_device
from gx3cli.gx3_external_inputs import load_refresh_areas, refresh_area_for
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
from gx3cli.gx3_arg_decode import COMPARE_RE
from gx3cli.gx3_instruction_table import (
    manual_allowed_devices,
    manual_operand_names,
    manual_operand_types,
    operand_range,
    operand_words,
)


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

# Division bases, from the instruction list: /, D/, B/, DB/, E/, ED/ and their
# pulse forms. The set used to include "$/", which is not an instruction -- the
# manuals have no string division -- and to leave out DB/ and ED/.
DIV_BASES = {"/", "D/", "B/", "DB/", "E/", "ED/"}

# 32-bit instruction bases the manuals do not carry, so they cannot be typed
# from MANUAL_OPERAND_TYPES. These are the GX Works2-era names (iQ-R spells the
# conversions INT2FLT/FLT2INT), kept because real projects still contain them.
LEGACY_WIDTH32_BASES = {"DFLT", "DINT"}



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


# A sentinel for "not looked for yet", distinct from "looked for, not there".
_UNLOADED = object()


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
    # Why a check could not run, by check name. A check that records one here
    # is reported as not evaluated rather than as zero findings.
    states: dict[str, AnalysisState] = field(default_factory=dict)
    # Where the communication refresh areas were written, if they were.
    refresh_csv: str = ""
    _refresh_areas: object = _UNLOADED

    def cannot_evaluate(self, check: str, reason: str, next_step: str = "") -> list:
        self.states[check] = not_evaluated(reason, next_step)
        print(f"  {check}: {self.states[check].line()}")
        return []

    def refresh_areas(self) -> list | None:
        """The communication refresh areas, or None when they were not found.

        None is not an empty list. With no refresh information, a device a
        network writes every scan looks exactly like a device nothing writes,
        and a check that cannot tell them apart reports the difference as a
        finding. The callers treat None as "cannot evaluate".
        """
        if self._refresh_areas is _UNLOADED:
            path = Path(self.refresh_csv) if self.refresh_csv else None
            if path is None or not path.exists():
                self._refresh_areas = None
            else:
                self._refresh_areas = load_refresh_areas(path)
        return self._refresh_areas

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

    Uses gx3_arg_decode's canonical row walk, then adapts it into the lint
    view that keeps constants at their argument index for math checks.
    """
    operations, _status = parse_row_operations(data)
    out: list[RowOp] = []
    for operation in operations:
        by_index = {}
        for occ in operation.args:
            by_index.setdefault(occ.arg_index, occ)

        args: list[OpArg] = []
        for idx, raw in enumerate(operation.raw_args):
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
        out.append(RowOp(role=operation.role, opcode=operation.role, base=base_opcode(operation.role), args=args))
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
        return ctx.cannot_evaluate(
            "multi-writer", "no cross-reference database",
            "gx3-cli xref build --root <project>")
    rows = ctx.xref.execute(
        """
        select device, device_type, number, range_len, pou, step, opcode, role,
               const_args, detail, lddb, pos, title, comment
        from xref
        where access in ('write', 'both')
        order by device_type, number, pou, pos
        """
    ).fetchall()
    by_device: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        if str(row["device_type"]) not in WORD_TYPES:
            continue
        # A block instruction names the first device of the run it writes, so
        # grouping by the name alone missed the conflict this check exists to
        # find: `BMOV .. D400 K4` and a `MOV .. D401` both write D401, and the
        # answer was no finding at all.
        by_device[str(row["device"])].append(row)
        for member in run_members(row):
            by_device[member].append(row)

    # Where each device sits, so a run of them can be collapsed back into one
    # finding below.
    place: dict[str, tuple[str, int]] = {}
    for row in rows:
        if str(row["device_type"]) not in WORD_TYPES:
            continue
        dev_type, number = str(row["device_type"]), int(row["number"] or 0)
        place[str(row["device"])] = (dev_type, number)
        for offset, member in enumerate(run_members(row), start=1):
            place.setdefault(member, (dev_type, number + offset))

    # Where each of those writes got its value. Two rungs writing one word is
    # a different thing to judge when one of them is "MOV from the recipe
    # table" and the other is "MOV from the HMI": the finding said only that
    # both wrote it.
    sources = value_sources_by_write(ctx.xref)

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
            + source_note(sources, device, w)
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
                # The fact this finding is about: which rungs write here. The
                # printed locations carry a value-source note that only the
                # named device of a run gets, so grouping on the text split one
                # overlap into two findings.
                "_writers": frozenset(locs),
            }
        )
    out = collapse_runs(out, place)
    out.sort(key=lambda item: (0 if item["severity"] == "high" else 1, -int(item["count"])))
    return out


def collapse_runs(
    findings: list[dict[str, object]], place: dict[str, tuple[str, int]]
) -> list[dict[str, object]]:
    """One finding per fact, not one per device.

    Two block instructions overwriting the same run is a single thing to look
    at. Reported per device it came to 7,679 identical findings for one pair of
    BMOVs, and 38,408 findings on a real project that were 1,020 facts. A list
    that long is not read, which costs more than the findings were worth.
    """
    keyed: dict[tuple, list[dict[str, object]]] = defaultdict(list)
    for finding in findings:
        keyed[(finding.get("_writers"), str(finding["severity"]))].append(finding)

    out: list[dict[str, object]] = []
    for (locations, _), group in keyed.items():
        located = sorted(
            (place.get(str(item["device"]), ("", 0)), item) for item in group
        )
        run: list[dict[str, object]] = []
        previous: tuple[str, int] | None = None
        for (dev_type, number), item in located:
            if previous is not None and (dev_type, number) != (previous[0], previous[1] + 1):
                out.append(_merged(run))
                run = []
            run.append(item)
            previous = (dev_type, number)
        if run:
            out.append(_merged(run))
    return out


def _merged(run: list[dict[str, object]]) -> dict[str, object]:
    first = dict(run[0])
    first.pop("_writers", None)
    if len(run) == 1:
        return first
    first["device"] = f"{run[0]['device']}..{run[-1]['device']}"
    first["detail"] = (
        f"{str(first['detail'])}; one finding for {len(run)} devices written by the same rungs"
    )
    return first


def run_members(row: sqlite3.Row) -> list[str]:
    """The rest of the run a write covers, beyond the device it names."""
    try:
        length = int(row["range_len"] or 1)
        number = int(row["number"] or 0)
        dev_type = str(row["device_type"] or "")
    except (KeyError, IndexError, TypeError, ValueError):
        return []
    if length <= 1 or not dev_type:
        return []
    return [format_device(dev_type, number + offset) for offset in range(1, length)]


def value_sources_by_write(xref: sqlite3.Connection) -> dict[tuple[str, str, int], list[str]]:
    """The source device of every stored transfer, keyed by where it landed.

    Empty when the cross-reference predates value-flow edges. A finding then
    reads exactly as it did before rather than claiming a write had no source.
    """
    try:
        rows = xref.execute(
            "select destination_device, lddb, pos, source_device from data_flow"
        ).fetchall()
    except sqlite3.Error:
        return {}
    found: dict[tuple[str, str, int], list[str]] = {}
    for row in rows:
        key = (str(row["destination_device"]), str(row["lddb"]), int(row["pos"]))
        found.setdefault(key, []).append(str(row["source_device"]))
    return found


def source_note(
    sources: dict[tuple[str, str, int], list[str]], device: str, writer: sqlite3.Row
) -> str:
    names = sources.get((device, str(writer["lddb"]), int(writer["pos"] or 0)), [])
    return f"<-{'+'.join(sorted(set(names)))}" if names else ""


@register(
    "external-value-source",
    "a value is transferred from a word the ladder never writes",
)
def check_external_value_source(ctx: LintContext) -> list[dict[str, object]]:
    """Where a value enters the program from outside the ladder.

    Not a defect. A transfer whose source no rung ever writes is reading
    something put there by an HMI, a module, a network partner, a file register
    or a retained value -- and which of those it is decides where to look when
    the value is wrong. That boundary is the thing an engineer needs and the
    one the ladder alone does not show.

    Devices a communication refresh writes are excluded, because there the
    writer is known and is not a question. So is module buffer memory: a
    ladder that does not write module buffer memory is normal, not suspicious.
    What is left
    is the set worth naming.

    Without the communication CSVs this cannot tell a refreshed device from an
    unexplained one, so it does not run rather than reporting a longer list
    than the truth.
    """
    if ctx.xref is None:
        return ctx.cannot_evaluate(
            "external-value-source", "no cross-reference database",
            "gx3-cli xref build --root <project>")
    try:
        edges = ctx.xref.execute(
            "select source_device, destination_device, opcode, pou, step, lddb, pos, "
            "source_comment from data_flow order by source_device"
        ).fetchall()
    except sqlite3.Error:
        return ctx.cannot_evaluate(
            "external-value-source", "this cross-reference holds no value-flow edges",
            "gx3-cli xref build --root <project>")

    refresh_areas = ctx.refresh_areas()
    if refresh_areas is None:
        return ctx.cannot_evaluate(
            "external-value-source",
            "no communication refresh areas; a refreshed device would be reported as unexplained",
            "gx3-cli comm-refresh --root <project>")

    written = {
        str(row["device"])
        for row in ctx.xref.execute(
            "select distinct device from xref where access in ('write', 'both')"
        )
    }

    out: list[dict[str, object]] = []
    seen: set[str] = set()
    for edge in edges:
        source = str(edge["source_device"])
        if source in written or source in seen:
            continue
        parsed = split_device(source)
        if parsed is None:
            continue  # a label or a buffer reference, not a plain device
        dev_type, number = parsed
        if dev_type == "U" or source.startswith("U"):
            continue  # module buffer memory: the module writes it
        if refresh_area_for(dev_type, number, refresh_areas) is not None:
            continue
        seen.add(source)
        out.append(
            {
                "check": "external-value-source",
                "severity": "info",
                "device": source,
                "comment": str(edge["source_comment"] or ""),
                "count": 1,
                "locations": f"{edge['pou'] or edge['lddb']}:st{edge['step']}:{edge['opcode']}"
                f" -> {edge['destination_device']}",
                "detail": "no rung writes this word; its value comes from outside the ladder",
                "review_note": (
                    "confirm the writer: HMI, module, network partner, file register or a "
                    "retained value. This is a boundary, not a fault."
                ),
            }
        )
    out.sort(key=lambda item: str(item["device"]))
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
        return ctx.cannot_evaluate(
            "alarm-quality", "no cross-reference database",
            "gx3-cli xref build --root <project>")
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


def covered_device_ranges(lite: sqlite3.Connection) -> dict[str, list[tuple[int, int]]]:
    """The runs the ladder writes without naming every device in them."""
    try:
        rows = lite.execute("select device_type, start, length from covered_ranges").fetchall()
    except sqlite3.Error:
        # An index built before the runs were recorded. The check then reports
        # what it always did rather than failing.
        return {}
    out: dict[str, list[tuple[int, int]]] = {}
    for dev_type, start, length in rows:
        out.setdefault(str(dev_type), []).append((int(start), int(length)))
    return out


def is_covered(covered: dict[str, list[tuple[int, int]]], dev_type: str, number: int) -> bool:
    return any(start <= number < start + length for start, length in covered.get(dev_type, ()))


@register("unused-device", "devices written but never read, or comments on unused devices")
def check_unused_device(ctx: LintContext) -> list[dict[str, object]]:
    if ctx.lite is None:
        return ctx.cannot_evaluate(
            "unused-device", "no lite index",
            "gx3-cli index-lite build --root <project>")
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
    covered = covered_device_ranges(ctx.lite)
    for r in rows:
        comment = str(r["comment"] or "")
        if r["device_type"] in {"T", "C"}:
            continue
        if is_covered(covered, str(r["device_type"]), int(r["number"])):
            # A block instruction or a digit specification writes this device
            # without naming it, so it is neither unused nor unwritten. Before
            # the index recorded those runs, 149 of this check's 1000 findings
            # on one project were devices in that state.
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
    if not ctx.rows:
        return ctx.cannot_evaluate("comment-conflict", "the project has no ladder rows")
    if ctx.lite is None:
        return ctx.cannot_evaluate(
            "comment-conflict", "no lite index",
            "gx3-cli index-lite build --root <project>")
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
        return ctx.cannot_evaluate(
            "link-range", "no cross-reference or link-map database",
            "pass --xref-db and --link-db")
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


def _operand_names(op) -> tuple[str, ...] | None:
    """The manuals' name for each operand position, in ladder order."""
    argc = len(op.args)
    for name in (op.opcode, op.base):
        names = manual_operand_names(name, argc)
        if names is not None:
            return names
    return None


def _operand_types(op) -> tuple[str, ...] | None:
    """Operand type codes for this op, or None when the manuals do not say.

    The intermediate format writes a comparison contact as a bare "<" or "=",
    where the manuals document the family as "LD□，AND□，OR□" with one operand
    table shared by all three, so the bare form is looked up as the LD form.
    """
    argc = len(op.args)
    for name in (op.opcode, op.base):
        types = manual_operand_types(name, argc)
        if types is not None:
            return types
    if COMPARE_RE.match(op.base) and not op.base.startswith(("LD", "AND", "OR")):
        return manual_operand_types("LD" + op.base, argc)
    return None


def _operand_words(op, arg) -> int:
    """Device words this operand occupies, from the manual operand types.

    Falls back to the legacy 32-bit list for the handful of GX Works2-era
    opcodes the manuals do not carry, and to one word when nothing is known --
    an unknown operand should not invent an overlap.
    """
    types = _operand_types(op)
    if types is not None and arg.index < len(types):
        return operand_words(types[arg.index])
    return 2 if op.base in LEGACY_WIDTH32_BASES else 1


@register("operand-device", "operand uses a device type the manuals do not allow")
def check_operand_device(ctx: LintContext) -> list[dict[str, object]]:
    """Devices the manuals do not allow on that operand.

    GX Works3 rejects an invalid device at compile time, so a converted project
    should produce nothing here. What this catches is the decoder losing track
    of which argument is which -- when operands shift, the device types stop
    fitting, and a row that reads as a clean parse stops being plausible.
    """
    out: list[dict[str, object]] = []
    for row in ctx.rows:
        loc = f"{row.lddb}:{row.pos}:{row.title}"
        for op in ctx.ops_for(row):
            names = _operand_names(op)
            if names is None:
                continue
            for arg in op.args:
                if arg.kind != "device" or arg.detail or arg.index >= len(names):
                    continue
                allowed = manual_allowed_devices(op.opcode, names[arg.index]) or manual_allowed_devices(
                    op.base, names[arg.index]
                )
                if not allowed or arg.device_type in allowed:
                    continue
                out.append(
                    {
                        "check": "operand-device",
                        "severity": "medium",
                        "device": arg.device,
                        "comment": ctx.comment(arg.device_type, arg.number),
                        "count": 1,
                        "locations": loc,
                        "detail": f"{op.opcode} {names[arg.index]} is {arg.device_type}, which the manuals do not list for it",
                        "review_note": "either the program is invalid or this row decoded onto the wrong operand; check the rung in GX Works3",
                    }
                )
    return out


@register("width-mismatch", "multi-word destination overlapped by another op")
def check_width_mismatch(ctx: LintContext) -> list[dict[str, object]]:
    # Registry of the words above the first one, for every multi-word write.
    # A double-precision destination occupies four words, not two, so the
    # instruction-wide "is this a 32-bit op" question is not enough: the width
    # belongs to the operand. EDMOV is double precision and used to be listed
    # as 32-bit, which guarded D+1 while D+2 and D+3 were left unwatched.
    high_words: dict[tuple[str, int], dict[str, object]] = {}
    for row in ctx.rows:
        for op in ctx.ops_for(row):
            for arg in op.args:
                if arg.kind != "device" or arg.device_type not in WORD_TYPES:
                    continue
                if arg.access not in ("write", "both"):
                    continue
                if arg.detail:  # indexed / buffer / digit form: pairing unclear
                    continue
                words = _operand_words(op, arg)
                if words < 2:
                    continue
                for offset in range(1, words):
                    high_words.setdefault(
                        (arg.device_type, arg.number + offset),
                        {
                            "base": arg.device,
                            "opcode": op.opcode,
                            "words": words,
                            "loc": f"{row.lddb}:{row.pos}:{row.title}",
                        },
                    )

    out: list[dict[str, object]] = []
    seen: set[tuple[str, int, str, int]] = set()
    for row in ctx.rows:
        loc = f"{row.lddb}:{row.pos}:{row.title}"
        for op in ctx.ops_for(row):
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
                width = f"{int(src['words']) * 16}-bit"
                if arg.access in ("write", "both"):
                    severity = "medium"
                    detail = f"{op.opcode} writes {arg.device}, inside {width} {src['opcode']} {src['base']}"
                elif _operand_words(op, arg) >= 2:
                    severity = "medium"
                    detail = f"multi-word {op.opcode} base {arg.device} overlaps {width} {src['opcode']} {src['base']}"
                else:
                    severity = "info"
                    detail = f"{op.opcode} reads {arg.device}, inside {width} {src['opcode']} {src['base']}"
                out.append(
                    {
                        "check": "width-mismatch",
                        "severity": severity,
                        "device": arg.device,
                        "comment": ctx.comment(arg.device_type, arg.number),
                        "count": 1,
                        "locations": f"{src['loc']}  <->  {loc}",
                        "detail": detail,
                        "review_note": f"{width} operand occupies {src['words']} consecutive words; another op touching one of them may corrupt or misread the value",
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
            # The operand types say what the comparison actually is. Reading it
            # off the opcode name got both halves wrong: "D" is double word in
            # DMOV but date in ANDDT<, and the unsigned _U comparisons were not
            # matched at all -- so the check that exists to find signedness
            # mistakes could not see a single unsigned opcode.
            types = _operand_types(op)
            for arg in op.args:
                if arg.kind == "const":
                    value = const_int(arg.const)
                    if value is None:
                        continue
                    code = types[arg.index] if types and arg.index < len(types) else ""
                    span = operand_range(code)
                    if span is None or span[0] <= value <= span[1]:
                        continue
                    out.append(
                        {
                            "check": "signed-compare",
                            "severity": "info",
                            "device": f"const={arg.const}",
                            "comment": "",
                            "count": 1,
                            "locations": loc,
                            "detail": f"{op.opcode} compares a {code} operand against constant {value}, outside {span[0]}..{span[1]}",
                            "review_note": "value does not fit the operand type; compare result may be unexpected",
                        }
                    )
                elif arg.kind == "device" and arg.device_type in {"UG"}:
                    code = types[arg.index] if types and arg.index < len(types) else ""
                    if code.startswith("uint"):
                        continue  # already an unsigned compare; nothing to warn about
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


def open_checked_xref(path: Path, root: Path | None = None) -> sqlite3.Connection | None:
    """The xref db, refused when another decoder version wrote it.

    open_optional() serves the lite index and the link map too, and the lite
    index carries its own guard, so only this one goes through the xref check.
    """
    if not path.exists():
        return None
    return open_xref_db(path, read_only=True, root=root)


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
        state = ctx.states.get(name, checked())
        summary["checks"][name] = {
            "count": len(findings),
            "by_severity": dict(sev_counts),
            **state.as_dict(),
        }
        summary["outputs"].append(str(out_path))
        total += len(findings)
        if state.conclusive:
            sev_text = ", ".join(f"{k}={v}" for k, v in sorted(sev_counts.items())) or "none"
            print(f"  {name:<15} findings={len(findings):<4} ({sev_text}) -> {out_path}")
    summary["total_findings"] = total
    # A check that could not run reported zero findings, which read exactly
    # like a check that ran and found nothing. The summary says which is which,
    # and repeats the names so a reader of the total cannot miss them.
    states = {name: ctx.states.get(name, checked()) for name in checks}
    summary["analysis"] = summarise(states)
    inconclusive = summary["analysis"]["inconclusive"]
    if inconclusive:
        print(
            f"\n{len(inconclusive)} of {len(checks)} checks did not run: "
            + ", ".join(inconclusive)
        )
        print("total findings below counts only the checks that did.")
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Static lint checks for a GX Works3 project")
    parser.add_argument("root", nargs="?", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--checks", default="all", help="comma-separated check names or 'all'")
    parser.add_argument(
        "--require-evaluated",
        action="store_true",
        help="exit non-zero if any check could not run (its prerequisite was missing)",
    )
    parser.add_argument("--xref-db", default=None, help="xref sqlite path (default: .gx3_index/<project>_xref.sqlite)")
    parser.add_argument("--index-db", default=None, help="lite index sqlite path (default: .gx3_index/<project>.sqlite)")
    parser.add_argument("--link-db", default=".gx3_index/link_map.sqlite", help="link-map sqlite path for link-range check")
    parser.add_argument(
        "--refresh-csv", default="",
        help="communication refresh areas CSV; without it the external-value-source "
             "check cannot tell a refreshed device from an unexplained one and does not run",
    )
    parser.add_argument("--out-prefix", default=default_output_prefix("lint"), help="output CSV/JSON prefix")
    parser.add_argument("--list-checks", action="store_true", help="list available checks and exit")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="stdout format")
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
    status_out = sys.stderr if args.format == "json" else sys.stdout
    with contextlib.redirect_stdout(status_out):
        print(f"lint root: {root}")
        print("loading ladder rows and comments ...")
        comments = load_comments_for_root(root)
        rows = load_rows(root, comments)

        xref_path = Path(args.xref_db) if args.xref_db else xref_db_path(root)
        lite_path = Path(args.index_db) if args.index_db else Path(lite_db_path(root))
        link_path = Path(args.link_db) if args.link_db else Path()
        refresh_path = (
            Path(args.refresh_csv)
            if args.refresh_csv
            else Path("outputs") / f"{default_comm_prefix()}_refresh_areas.csv"
        )
        ctx = LintContext(
            root=root,
            rows=rows,
            comments=comments,
            xref=open_checked_xref(xref_path, root),
            lite=open_optional(lite_path),
            link=open_optional(link_path) if link_path else None,
            project_label=project_label_from_root(root),
            refresh_csv=str(refresh_path),
        )

        print(f"rows={len(rows)} xref={'ok' if ctx.xref else 'missing'} index={'ok' if ctx.lite else 'missing'} link={'ok' if ctx.link else 'missing'}")
        print("running checks:")
        summary = run_checks(ctx, checks, args.out_prefix)

    summary_path = Path(f"{args.out_prefix}_summary.json")
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.format == "json":
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(f"total findings: {summary['total_findings']}")
        print(f"summary: {summary_path}")

    inconclusive = summary.get("analysis", {}).get("inconclusive", [])
    if args.fail_on and inconclusive:
        # A check that could not run has no findings, so a severity gate passes
        # on it. Saying so is the difference between a gate that checked and a
        # gate that was not in a position to.
        print(
            f"fail-on: {len(inconclusive)} of {len(checks)} checks did not run "
            f"({', '.join(inconclusive)}); this gate did not cover them",
            file=sys.stderr,
        )
    if args.require_evaluated and inconclusive:
        print(
            "require-evaluated: " + ", ".join(inconclusive) + " did not run",
            file=sys.stderr,
        )
        return 2
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
