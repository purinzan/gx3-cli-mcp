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
from gx3cli.gx3_device_name import format_device
from gx3cli.gx3_reach import reach
from gx3cli.gx3_semantic_diff import ensure_root, load_side, logic_signature, summarize_change
from gx3cli.gx3_workspace import prepare
from gx3cli.gx3_xref import open_xref_db


# What a change can be. The first three can alter behaviour; the rest cannot,
# and saying so is the point of separating them.
LOGIC = "logic"
ADDED = "added"
REMOVED = "removed"
ORDER = "order"
LAYOUT_ONLY = "layout-only"
COMMENT_ONLY = "comment-only"

BEHAVIOURAL = (LOGIC, ADDED, REMOVED, ORDER)


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
    """The devices a rung writes, and whether part of it could not be read.

    A block instruction names the first device of the run it writes and no
    other: `BMOV D300 D400 K4` writes D400 through D403, and a walk that
    started only from D400 missed everything reading D401. The occurrence
    carries how many devices the run covers, so the span is spelled out here
    rather than left for each caller to remember.

    A run whose length is not a constant -- a count in a register, an
    index-modified destination -- has no span that can be written down from the
    file, and none is invented. The first device stands, and the rung is
    reported as one whose extent is not settled.
    """
    devices: list[str] = []
    uncertain = False
    try:
        operations, status = parse_row_occurrences(data)
    except Exception:
        return [], True
    for operation in operations:
        for occ in operation[2]:
            if occ.access not in {"write", "both"} or not occ.device:
                continue
            if occ.is_index_register:
                continue
            span = max(1, int(occ.range_len or 1))
            for offset in range(span):
                name = (
                    occ.device
                    if offset == 0
                    else format_device(occ.device_type, occ.number + offset)
                )
                if name and name not in devices:
                    devices.append(name)
    return devices, status != "exact" or uncertain


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


def order_changes(
    pou: str,
    old_pou: dict[str, tuple[int, str, str]],
    new_pou: dict[str, tuple[int, str, str]],
) -> list[Change]:
    """Rungs that kept their contents and changed places.

    Two rungs writing the same coil give a different result depending on which
    runs first, and the comparison before this one saw nothing at all: same
    GUIDs, same rung data, therefore no change. The scan order is part of the
    program.

    What counts is the relative order, not the numbers. Positions are rewritten
    whenever anything above them is edited, and calling that an execution
    change would put a finding on nearly every diff.
    """
    shared = sorted(set(old_pou) & set(new_pou))
    before = [guid for guid in sorted(shared, key=lambda g: old_pou[g][0])]
    after = [guid for guid in sorted(shared, key=lambda g: new_pou[g][0])]
    if before == after:
        return []

    place_before = {guid: index for index, guid in enumerate(before)}
    moved = [
        guid for index, guid in enumerate(after) if place_before[guid] != index
    ]

    out: list[Change] = []
    for guid in moved:
        pos, data, title = new_pou[guid]
        writes, unreadable = written_devices(data)
        was = place_before[guid]
        now = after.index(guid)
        out.append(
            Change(
                ORDER, pou, pos, title,
                f"runs {was - now} place(s) earlier" if now < was else f"runs {now - was} place(s) later",
                writes, [], unreadable,
            )
        )
    return out


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

    for hexid in sorted(set(old_rows) & set(new_rows)):
        pou = new_names.get(hexid) or old_names.get(hexid, hexid)
        changes.extend(order_changes(pou, old_rows[hexid], new_rows[hexid]))

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


def attach_reach(
    changes: list[Change],
    xref_db: Path,
    max_depth: int,
    max_nodes: int,
    root: Path | None = None,
) -> AnalysisState:
    """Follow what each change writes, through a cross-reference of *this* input.

    The database was opened here with a bare connect, so a cross-reference of
    another project answered every question with silence: no rows, no reach,
    and a run that exited zero saying the change reached nothing. `open_xref_db`
    already refuses that -- it checks the decoder version and the input
    fingerprint -- and the only reason this did not use it was that it did not
    ask.
    """
    con = open_xref_db(Path(xref_db), read_only=True, root=root)
    unreadable = 0
    stopped: set[str] = set()
    try:
        for change in changes:
            if change.kind not in BEHAVIOURAL:
                continue
            if change.unreadable:
                unreadable += 1
            # The whole run a block instruction writes, walked together, so a
            # device that only reads the middle of it is not lost.
            found = reach(con, change.writes, max_depth, max_nodes)
            stopped |= found.stopped
            change.truncated = change.truncated or found.truncated
            seen: set[str] = set()
            for item in found.steps:
                if item.device in seen:
                    continue
                seen.add(item.device)
                change.reaches.append(item.as_dict())
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
            if rest and not outputs and not alarms:
                # Nothing left an output and nothing raised an alarm, so the
                # count on its own says nothing at all. Name a few.
                for item in rest[:6]:
                    lines.append(
                        f"      {'経路' if ja else 'path':<8} {item['device']:<12} "
                        f"{item['basis']:<10} {item['pou']}:st{item['step']} "
                        f"{item['comment']}".rstrip()
                    )
                if len(rest) > 6:
                    lines.append(
                        f"      ... {len(rest) - 6} more" if not ja else f"      ほか {len(rest) - 6} 件"
                    )
            elif rest:
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
        # Checked against the new side: the reach is about what the change
        # leads to in the version that has it.
        state = attach_reach(changes, xref_db, args.max_depth, args.max_nodes, new_root)

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
