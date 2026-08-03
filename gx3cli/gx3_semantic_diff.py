from __future__ import annotations

"""Semantic (rung-level) diff between two GX Works3 projects.

Rows are matched by their stable ``_guid/...`` block id, so moving a circuit
does not show as a change. A changed row is classified as:
- logic          op sequence or device/constant arguments differ
- layout-only    identical logic, only drawing data differs (hidden by default)

Also reports POUs (LDDB files) added/removed and device-comment changes.

Inputs may be extracted folders or ``.gx3`` files (extracted to a temp dir).
"""

import argparse
import csv
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from gx3cli.extract_gx3_extended_instruction_knowledge import extract_elements, header_tokens
from gx3cli.gx3_intermediate_tool import decode_data
from gx3cli.gx3_program_map import load_program_map
from gx3cli.review_gx3_project import extract_title, load_comments_for_root


ARG_VALUE_RE = re.compile(r"(?:a|v)=(-?[0-9.]+)")


def ensure_root(path_text: str, tmp: list[tempfile.TemporaryDirectory]) -> Path:
    path = Path(path_text)
    if path.is_dir():
        return path
    if path.suffix.lower() == ".gx3" and path.is_file():
        td = tempfile.TemporaryDirectory(prefix="gx3diff_")
        tmp.append(td)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(td.name)
        return Path(td.name)
    raise SystemExit(f"not a folder or .gx3 file: {path}")


def logic_signature(data: str) -> str:
    """Op sequence + ordered argument values; ignores drawing/layout data."""
    tokens = header_tokens(data)
    start = 0
    for i, tok in enumerate(tokens):
        if i >= 1 and not re.fullmatch(r"V1|\d+", tok):
            start = i
            break
    ops = ":".join(tokens[start:])
    args = []
    for element in extract_elements(data):
        if "s=ce{" not in element:
            continue
        args.extend(ARG_VALUE_RE.findall(element))
    return ops + "|" + ",".join(args)


def load_side(root: Path) -> tuple[dict[str, dict[str, tuple[int, str, str]]], dict[str, str]]:
    """Return ({lddb_hex: {guid: (pos, data, title)}}, {lddb_hex: pou_name})."""
    rows: dict[str, dict[str, tuple[int, str, str]]] = {}
    for db_path in sorted(root.glob("*_LDDB.db")):
        hexid = db_path.name.split("_")[0]
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.text_factory = bytes
        per: dict[str, tuple[int, str, str]] = {}
        current_title = ""
        for bid, pos, blocktype, data in con.execute(
            "select id, pos, blocktype, data from LadderBlocks order by pos, id"
        ):
            sid = decode_data(bid)
            text = decode_data(data)
            bt = int(blocktype)
            if bt in {1, 2}:
                t = extract_title(text)
                if t:
                    current_title = t
            if bt != 0 or not sid.startswith("_guid/"):
                continue
            per[sid] = (int(float(pos)), text, current_title)
        con.close()
        rows[hexid] = per
    pm = load_program_map(root)
    names = {hexid: (info.name or hexid) for hexid, info in pm.pous.items()}
    return rows, names


def summarize_change(old_data: str, new_data: str) -> str:
    def devset(data: str) -> list[str]:
        return ARG_VALUE_RE.findall("".join(e for e in extract_elements(data) if "s=ce{" in e))

    def opseq(data: str) -> list[str]:
        return [t for t in header_tokens(data) if re.fullmatch(r"[A-Za-z$][A-Za-z0-9_.$]*|[<>=+\-*/]+", t)]

    parts = []
    o_ops, n_ops = opseq(old_data), opseq(new_data)
    if o_ops != n_ops:
        parts.append(f"ops {' '.join(o_ops[:12])} -> {' '.join(n_ops[:12])}")
    o_args, n_args = devset(old_data), devset(new_data)
    if o_args != n_args:
        removed = [x for x in o_args if x not in n_args]
        added = [x for x in n_args if x not in o_args]
        if removed or added:
            parts.append(f"args -{removed[:8]} +{added[:8]}")
    return "; ".join(parts) or "argument order changed"


def comment_map(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for (dev_type, number), info in load_comments_for_root(root).items():
        text = info.japanese or info.english or info.all_text
        if text:
            out[f"{dev_type}{number}"] = text
    return out


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", help="old project: extracted folder or .gx3")
    parser.add_argument("new", help="new project: extracted folder or .gx3")
    parser.add_argument("-o", "--output", default=None, help="detail CSV path")
    parser.add_argument("--show-layout-only", action="store_true")
    parser.add_argument("--skip-comments", action="store_true")
    parser.add_argument("--limit", type=int, default=60, help="console lines per section")
    args = parser.parse_args(argv)

    tmp: list[tempfile.TemporaryDirectory] = []
    try:
        old_root = ensure_root(args.old, tmp)
        new_root = ensure_root(args.new, tmp)
        print(f"old: {args.old}\nnew: {args.new}\n")
        old_rows, old_names = load_side(old_root)
        new_rows, new_names = load_side(new_root)

        detail: list[dict[str, object]] = []
        only_old = sorted(set(old_rows) - set(new_rows))
        only_new = sorted(set(new_rows) - set(old_rows))
        if only_old:
            print("POUs removed: " + ", ".join(old_names.get(h, h) for h in only_old))
        if only_new:
            print("POUs added:   " + ", ".join(new_names.get(h, h) for h in only_new))

        counts = {"added": 0, "removed": 0, "logic": 0, "layout": 0}
        for hexid in sorted(set(old_rows) & set(new_rows)):
            o, n = old_rows[hexid], new_rows[hexid]
            pou = new_names.get(hexid) or old_names.get(hexid, hexid)
            for guid in o.keys() - n.keys():
                pos, data, title = o[guid]
                counts["removed"] += 1
                detail.append({"pou": pou, "kind": "removed", "pos": pos, "title": title, "summary": ""})
            for guid in n.keys() - o.keys():
                pos, data, title = n[guid]
                counts["added"] += 1
                detail.append({"pou": pou, "kind": "added", "pos": pos, "title": title, "summary": ""})
            for guid in o.keys() & n.keys():
                o_pos, o_data, o_title = o[guid]
                n_pos, n_data, n_title = n[guid]
                if o_data == n_data:
                    continue
                if logic_signature(o_data) == logic_signature(n_data):
                    counts["layout"] += 1
                    if args.show_layout_only:
                        detail.append(
                            {"pou": pou, "kind": "layout-only", "pos": n_pos, "title": n_title, "summary": ""}
                        )
                    continue
                counts["logic"] += 1
                detail.append(
                    {
                        "pou": pou,
                        "kind": "logic",
                        "pos": n_pos,
                        "title": n_title,
                        "summary": summarize_change(o_data, n_data),
                    }
                )

        print(
            f"\nrows: added={counts['added']} removed={counts['removed']} "
            f"logic-changed={counts['logic']} layout-only={counts['layout']}"
        )
        order = {"logic": 0, "added": 1, "removed": 2, "layout-only": 3}
        detail.sort(key=lambda d: (order.get(str(d["kind"]), 9), str(d["pou"]), int(d["pos"])))
        for d in detail[: args.limit]:
            print(f"  [{d['kind']:<11}] {d['pou']:<8} pos={d['pos']:<7} {d['title']}")
            if d["summary"]:
                print(f"      {d['summary']}")
        if len(detail) > args.limit:
            print(f"  ... {len(detail) - args.limit} more rows (see CSV)")

        comment_changes: list[dict[str, object]] = []
        if not args.skip_comments:
            oc, nc = comment_map(old_root), comment_map(new_root)
            for dev in sorted(oc.keys() | nc.keys()):
                if oc.get(dev, "") == nc.get(dev, ""):
                    continue
                kind = "comment-added" if dev not in oc else ("comment-removed" if dev not in nc else "comment-changed")
                comment_changes.append(
                    {"pou": "", "kind": kind, "pos": "", "title": dev,
                     "summary": f"{oc.get(dev, '')!r} -> {nc.get(dev, '')!r}"}
                )
            print(f"\ndevice comments changed: {len(comment_changes)}")
            for d in comment_changes[:20]:
                print(f"  [{d['kind']}] {d['title']}: {d['summary']}")
            if len(comment_changes) > 20:
                print(f"  ... {len(comment_changes) - 20} more (see CSV)")

        out = Path(args.output or "outputs/semantic_diff.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["kind", "pou", "pos", "title", "summary"])
            w.writeheader()
            for d in detail + comment_changes:
                w.writerow(d)
        print(f"\ncsv: {out}")
        return 0
    finally:
        for td in tmp:
            td.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
