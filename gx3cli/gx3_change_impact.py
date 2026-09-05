from __future__ import annotations

"""What a change reaches, not just what changed.

`semantic-diff` says which rungs differ. That is the first half of the question
somebody actually has: they changed a contact, and they want to know which
outputs and which alarms can behave differently because of it. Answering that
by hand means taking each changed rung, reading which devices it writes, and
following each of those through the program -- which is what `downstream` does,
once, per device, if you remember to ask.

So this joins them. For every rung that changed, the devices it writes, and
what those reach.

Three things this is careful about.

The reach is a set of candidates, not a set of consequences. It is computed
from the saved file: a device is included because a rung that reads it writes
something else, not because that path runs. Scan order, execution conditions
and interlocks all decide whether it actually does, and none of them are
settled here.

A change that cannot alter behaviour is reported as such rather than dressed up
with an impact list. A comment, a moved element, a rung whose logic is
identical -- those get named as what they are, and the outputs they "affect"
are none.

A changed rung containing something the decoder could not read makes the reach
incomplete, and it says so. An impact list that is quietly missing a branch is
worse than no impact list, because it is read as a complete one.
"""

import argparse
import json
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gx3cli.gx3_alarm_map import ALARM_COMMENT_RE
from gx3cli.gx3_analysis_state import (
    CHECKED,
    DECODE,
    PARTIAL,
    REACH,
    TRUNCATED,
    AnalysisState,
    label_for,
)
from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_semantic_diff import ensure_root, load_side, logic_signature, summarize_change
from gx3cli.gx3_workspace import prepare


# What a change can be. The first three can alter behaviour; the rest cannot,
# and saying so is the point of separating them.
LOGIC = "logic"
ADDED = "added"
REMOVED = "removed"
LAYOUT_ONLY = "layout-only"
COMMENT_ONLY = "comment-only"

BEHAVIOURAL = (LOGIC, ADDED, REMOVED)


@dataclass
class Change:
    kind: str
    pou: str
    pos: int
    title: str
    summary: str = ""
    writes: list[str] = field(default_factory=list)
    reaches: list[dict[str, Any]] = field(default_factory=list)
    unreadable: bool = False
    # Whether the walk from this change hit a limit before it was exhausted.
    truncated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "pou": self.pou,
            "pos": self.pos,
            "title": self.title,
            "summary": self.summary,
            "writes": list(self.writes),
            "reaches": list(self.reaches),
            "unreadable": self.unreadable,
            "truncated": self.truncated,
        }


def written_devices(data: str) -> tuple[list[str], bool]:
    """The devices a rung writes, and whether part of it could not be read."""
    devices: list[str] = []
    try:
        operations, status = parse_row_occurrences(data)
    except Exception:
        return [], True
    for operation in operations:
        for occ in operation[2]:
            if occ.access in {"write", "both"} and occ.device and occ.device not in devices:
                devices.append(occ.device)
    return devices, status != "exact"


def reach_of(
    con: sqlite3.Connection, device: str, max_depth: int, max_nodes: int
) -> tuple[list[dict[str, Any]], set[str]]:
    """Devices the given one can reach, and the limits that stopped the walk.

    Static candidates. A device is here because something that reads the one
    before it writes this, which is what the saved file supports and no more.

    The limits are returned rather than swallowed. A walk that stopped at
    max-depth has not shown what the change reaches; it has shown what it
    reaches within that depth, and the two read identically unless the
    difference is stated. M100 -> M200 -> M300 at depth 1 loses M300, and
    reporting that as a complete answer is the failure this returns for.
    """
    seen = {device}
    out: list[dict[str, Any]] = []
    stopped: set[str] = set()
    frontier = [(device, 0)]
    has_flow = bool(
        con.execute(
            "select count(*) from sqlite_master where type='table' and name='data_flow'"
        ).fetchone()[0]
    )
    while frontier and len(out) < max_nodes:
        current, depth = frontier.pop(0)
        if depth >= max_depth:
            # There was somewhere further to go and the depth stopped it.
            stopped.add("max-depth")
            continue
        rows = con.execute(
            """
            select distinct w.device as device, w.comment as comment, w.pou as pou,
                   w.step as step, w.role as role
            from xref r join xref w on r.lddb = w.lddb and r.pos = w.pos
            where r.device = ? and r.access in ('read', 'ref', 'both')
              and w.access in ('write', 'both') and w.device <> r.device
            """,
            (current,),
        ).fetchall()
        candidates = [(row, "same-rung") for row in rows]
        if has_flow:
            for row in con.execute(
                """
                select f.destination_device as device, f.destination_comment as comment,
                       f.pou as pou, f.step as step, f.opcode as role
                from data_flow f where f.source_device = ?
                """,
                (current,),
            ):
                candidates.append((row, f"via {row['role']}"))

        for row, basis in candidates:
            name = str(row["device"])
            if name in seen:
                continue
            seen.add(name)
            out.append(
                {
                    "device": name,
                    "comment": str(row["comment"] or ""),
                    "from": current,
                    "basis": basis,
                    "pou": str(row["pou"] or ""),
                    "step": row["step"],
                    "depth": depth + 1,
                }
            )
            frontier.append((name, depth + 1))
            if len(out) >= max_nodes:
                stopped.add("max-nodes")
                break
    if frontier and len(out) >= max_nodes:
        stopped.add("max-nodes")
    return out, stopped


# Devices that leave the PLC, or that a person is paged about. A reach of 135
# devices does not answer "what does this change affect"; the outputs and the
# alarms inside it do, and the rest is the path between them.
OUTPUT_TYPES = ("Y",)


def is_output(device: str) -> bool:
    return device[:1] in OUTPUT_TYPES and device[1:2].isalnum()


def is_alarm(comment: str) -> bool:
    return bool(comment and ALARM_COMMENT_RE.search(comment))


def notable(reaches: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    outputs = [item for item in reaches if is_output(str(item["device"]))]
    alarms = [item for item in reaches if is_alarm(str(item["comment"]))]
    return outputs, alarms


def collect_changes(old_root: Path, new_root: Path, include_comments: bool = True) -> list[Change]:
    old_rows, old_names = load_side(old_root)
    new_rows, new_names = load_side(new_root)
    changes: list[Change] = []

    for hexid in sorted(set(old_rows) & set(new_rows)):
        old_pou, new_pou = old_rows[hexid], new_rows[hexid]
        pou = new_names.get(hexid) or old_names.get(hexid, hexid)

        for guid in sorted(old_pou.keys() - new_pou.keys()):
            pos, data, title = old_pou[guid]
            writes, unreadable = written_devices(data)
            changes.append(Change(REMOVED, pou, pos, title, "", writes, [], unreadable))

        for guid in sorted(new_pou.keys() - old_pou.keys()):
            pos, data, title = new_pou[guid]
            writes, unreadable = written_devices(data)
            changes.append(Change(ADDED, pou, pos, title, "", writes, [], unreadable))

        for guid in sorted(old_pou.keys() & new_pou.keys()):
            _, old_data, _ = old_pou[guid]
            new_pos, new_data, new_title = new_pou[guid]
            if old_data == new_data:
                continue
            if logic_signature(old_data) == logic_signature(new_data):
                changes.append(Change(LAYOUT_ONLY, pou, new_pos, new_title))
                continue
            writes, unreadable = written_devices(new_data)
            old_writes, old_unreadable = written_devices(old_data)
            for device in old_writes:
                if device not in writes:
                    writes.append(device)
            changes.append(
                Change(
                    LOGIC, pou, new_pos, new_title,
                    summarize_change(old_data, new_data),
                    writes, [], unreadable or old_unreadable,
                )
            )

    # A POU that exists on one side only. Every rung in it is a change.
    for hexid in sorted(set(new_rows) - set(old_rows)):
        pou = new_names.get(hexid, hexid)
        for _, (pos, data, title) in sorted(new_rows[hexid].items()):
            writes, unreadable = written_devices(data)
            changes.append(Change(ADDED, pou, pos, title, "whole POU is new", writes, [], unreadable))
    for hexid in sorted(set(old_rows) - set(new_rows)):
        pou = old_names.get(hexid, hexid)
        for _, (pos, data, title) in sorted(old_rows[hexid].items()):
            writes, unreadable = written_devices(data)
            changes.append(Change(REMOVED, pou, pos, title, "whole POU is gone", writes, [], unreadable))

    if include_comments:
        from gx3cli.gx3_semantic_diff import comment_map

        old_comments, new_comments = comment_map(old_root), comment_map(new_root)
        for device in sorted(old_comments.keys() | new_comments.keys()):
            before, after = old_comments.get(device, ""), new_comments.get(device, "")
            if before == after:
                continue
            changes.append(
                Change(COMMENT_ONLY, "", 0, device, f"{before!r} -> {after!r}")
            )
    return changes


def attach_reach(changes: list[Change], xref_db: Path, max_depth: int, max_nodes: int) -> AnalysisState:
    con = sqlite3.connect(f"file:{xref_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    unreadable = 0
    stopped: set[str] = set()
    try:
        for change in changes:
            if change.kind not in BEHAVIOURAL:
                continue
            if change.unreadable:
                unreadable += 1
            seen: set[str] = set()
            for device in change.writes:
                items, limits = reach_of(con, device, max_depth, max_nodes)
                stopped |= limits
                change.truncated = change.truncated or bool(limits)
                for item in items:
                    if item["device"] in seen:
                        continue
                    seen.add(item["device"])
                    change.reaches.append(item)
    finally:
        con.close()

    # A rung the decoder could not read outranks a limit: raising the limit
    # would not recover what was never read.
    if unreadable:
        return AnalysisState(
            PARTIAL,
            reason=f"{unreadable} changed rungs hold something the decoder could not read",
            next_step="gx3-cli parse-gaps --root <project>",
            stage=DECODE,
        )
    if stopped:
        limits = ", ".join(sorted(stopped))
        return AnalysisState(
            TRUNCATED,
            reason=f"the walk stopped at {limits}; what a change reaches beyond it is not listed",
            next_step="raise --max-depth or --max-nodes and run it again",
            stage=REACH,
        )
    return AnalysisState(CHECKED)


def render(changes: list[Change], state: AnalysisState, limit: int, ja: bool = False) -> list[str]:
    behavioural = [c for c in changes if c.kind in BEHAVIOURAL]
    others = [c for c in changes if c.kind not in BEHAVIOURAL]
    counts: dict[str, int] = {}
    for change in changes:
        counts[change.kind] = counts.get(change.kind, 0) + 1

    lines = [
        ("変更: " if ja else "changes: ")
        + ", ".join(f"{kind}={count}" for kind, count in sorted(counts.items()))
    ]
    if state.state != CHECKED:
        lines.append("")
        lines.append(f"{'結果' if ja else 'Result'}: {label_for(state.state, ja)} -- {state.reason}")
        if state.next_step:
            lines.append(f"  {'次の手順' if ja else 'next'}: {state.next_step}")

    if not behavioural:
        lines.append("")
        lines.append(
            "動作が変わりうる変更はありません。上記はコメントや配置の違いです。"
            if ja
            else "No change that can alter behaviour. What differs is comments and layout."
        )
        if others:
            lines.append("")
            for change in others[:limit]:
                lines.append(f"  [{change.kind:<12}] {change.title} {change.summary}"[:200])
            if len(others) > limit:
                lines.append(f"  ... {len(others) - limit} more")
        return lines

    lines.append("")
    lines.append(
        "以下は静的な影響候補です。保存されたファイルから辿れる範囲であって、"
        "実行順・実行条件・インタロックは判定していません。"
        if ja
        else "What follows are static candidates: reachable in the saved file. "
        "Scan order, execution conditions and interlocks are not decided here."
    )
    lines.append("")
    for change in behavioural[:limit]:
        head = f"[{change.kind:<7}] {change.pou}:pos={change.pos} {change.title}".rstrip()
        lines.append(head)
        if change.summary:
            lines.append(f"    {change.summary}")
        if change.unreadable:
            lines.append(
                "    ※ このラングに解釈できない部分があり、以下は不完全です"
                if ja
                else "    ! part of this rung could not be read; what follows is incomplete"
            )
        if change.truncated:
            lines.append(
                "    ※ 到達先の探索が上限で止まりました。この先はこの一覧にありません"
                if ja
                else "    ! the walk stopped at a limit; what lies beyond it is not listed"
            )
        if change.writes:
            lines.append(("    書き込み: " if ja else "    writes:  ") + ", ".join(change.writes))
        if change.reaches:
            outputs, alarms = notable(change.reaches)
            lines.append(
                (f"    到達候補: {len(change.reaches)} 件"
                 f"（うち出力 {len(outputs)}、警報 {len(alarms)}）"
                 if ja
                 else f"    reaches: {len(change.reaches)} "
                      f"({len(outputs)} outputs, {len(alarms)} alarms)")
            )
            for label, items in (
                ("出力" if ja else "outputs", outputs),
                ("警報" if ja else "alarms", alarms),
            ):
                for item in items[:6]:
                    lines.append(
                        f"      {label:<8} {item['device']:<12} {item['basis']:<10} "
                        f"{item['pou']}:st{item['step']} {item['comment']}".rstrip()
                    )
                if len(items) > 6:
                    lines.append(f"      ... {len(items) - 6} more {label}")
            rest = [
                item
                for item in change.reaches
                if item not in outputs and item not in alarms
            ]
            if rest:
                lines.append(
                    (f"      ほか {len(rest)} 件（経路上のデバイス）"
                     if ja
                     else f"      and {len(rest)} more devices along the way")
                )
        elif change.writes:
            lines.append(
                "    到達候補: なし（この書き込みを読む回路が見つかりません）"
                if ja
                else "    reaches: none (nothing reads what this writes)"
            )
        lines.append("")
    if len(behavioural) > limit:
        lines.append(f"... {len(behavioural) - limit} more changed rungs")
    return lines


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        description="What each change reaches: changed rungs, what they write, and what that reaches.",
    )
    parser.add_argument("old", help="old project: extracted folder or .gx3")
    parser.add_argument("new", help="new project: extracted folder or .gx3")
    parser.add_argument("--xref-db", default="", help="cross-reference of the new project")
    parser.add_argument("--max-depth", type=int, default=2, help="how far to follow what a change writes")
    parser.add_argument("--max-nodes", type=int, default=200, help="most devices to report per change")
    parser.add_argument("--limit", type=int, default=40, help="changes to print")
    parser.add_argument("--skip-comments", action="store_true")
    parser.add_argument("--ja", action="store_true")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    tmp: list[tempfile.TemporaryDirectory] = []
    try:
        old_root = ensure_root(args.old, tmp)
        new_root = ensure_root(args.new, tmp)
        changes = collect_changes(old_root, new_root, include_comments=not args.skip_comments)

        xref_db = Path(args.xref_db) if args.xref_db else prepare(new_root).xref.path
        state = attach_reach(changes, xref_db, args.max_depth, args.max_nodes)

        if args.format == "json":
            print(
                json.dumps(
                    {
                        "old": str(args.old),
                        "new": str(args.new),
                        "analysis": state.as_dict(),
                        "changes": [change.as_dict() for change in changes],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(f"old: {args.old}")
            print(f"new: {args.new}")
            print("")
            for line in render(changes, state, args.limit, args.ja):
                print(line)
        return 0
    finally:
        for handle in tmp:
            handle.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
