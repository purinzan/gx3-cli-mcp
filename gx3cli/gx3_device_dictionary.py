from __future__ import annotations

"""Export a device dictionary from GX3 comments and optional xref data."""

import argparse
import csv
import json
import sqlite3
from pathlib import Path

from gx3cli.gx3_device_name import format_device
from gx3cli.gx3_project_paths import default_project_root, resolve_project_root
from gx3cli.gx3_xref import default_db_path
from gx3cli.review_gx3_project import load_comments_for_root


def collect_dictionary(root: Path, xref_db: Path | None = None) -> list[dict[str, object]]:
    comments = load_comments_for_root(root)
    by_device: dict[str, dict[str, object]] = {}
    for (device_type, number), info in comments.items():
        device = format_device(device_type, number)
        by_device[device] = {
            "address": device,
            "device": device,
            "device_prefix": device_type,
            "device_number": number,
            "comment": info.japanese or info.english or info.all_text,
            "comment_ja": info.japanese,
            "comment_en": info.english,
            "all_text": info.all_text,
            "source": "gx3-comment",
            "read_count": 0,
            "write_count": 0,
            "ref_count": 0,
            "occurrences": 0,
            "pous": [],
            "first_step": None,
            "confidence": "commented",
        }

    if xref_db and xref_db.exists():
        con = sqlite3.connect(xref_db)
        con.row_factory = sqlite3.Row
        for row in con.execute(
            """
            select device, device_type, number,
                   sum(case when access='read' then 1 else 0 end) as read_count,
                   sum(case when access in ('write','both') then 1 else 0 end) as write_count,
                   sum(case when access='ref' then 1 else 0 end) as ref_count,
                   count(*) as occurrences,
                   min(step) as first_step,
                   group_concat(distinct pou) as pous,
                   max(comment) as xref_comment
            from xref
            group by device, device_type, number
            order by device_type, number
            """
        ):
            device = str(row["device"])
            item = by_device.setdefault(
                device,
                {
                    "address": device,
                    "device": device,
                    "device_prefix": row["device_type"],
                    "device_number": row["number"],
                    "comment": row["xref_comment"] or "",
                    "comment_ja": "",
                    "comment_en": "",
                    "all_text": row["xref_comment"] or "",
                    "source": "xref",
                    "read_count": 0,
                    "write_count": 0,
                    "ref_count": 0,
                    "occurrences": 0,
                    "pous": [],
                    "first_step": None,
                    "confidence": "referenced",
                },
            )
            if not item.get("comment") and row["xref_comment"]:
                item["comment"] = row["xref_comment"]
                item["all_text"] = row["xref_comment"]
            item["read_count"] = int(row["read_count"] or 0)
            item["write_count"] = int(row["write_count"] or 0)
            item["ref_count"] = int(row["ref_count"] or 0)
            item["occurrences"] = int(row["occurrences"] or 0)
            item["first_step"] = row["first_step"]
            item["pous"] = sorted(p for p in str(row["pous"] or "").split(",") if p)
            item["source"] = "gx3-comment+xref" if item["source"] == "gx3-comment" else "xref"
        con.close()

    return sorted(by_device.values(), key=lambda r: (str(r["device_prefix"]), int(r["device_number"])))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fields = [
        "address",
        "device_prefix",
        "device_number",
        "comment",
        "comment_ja",
        "comment_en",
        "source",
        "read_count",
        "write_count",
        "ref_count",
        "occurrences",
        "pous",
        "first_step",
        "confidence",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["pous"] = ";".join(str(p) for p in row.get("pous", []))
            writer.writerow({field: out.get(field, "") for field in fields})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export GX3 device comments and optional xref usage as a device dictionary.")
    parser.add_argument("--root", default=str(default_project_root()), help="GX3 file or extracted project folder")
    parser.add_argument("--xref-db", help="optional xref sqlite DB; defaults to .gx3_index/<project>_xref.sqlite when present")
    parser.add_argument("--format", choices=["json", "csv"], default="json")
    parser.add_argument("-o", "--output", help="write output to file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = resolve_project_root(args.root)
    xref_db = Path(args.xref_db) if args.xref_db else default_db_path(root)
    rows = collect_dictionary(root, xref_db if xref_db.exists() else None)
    if args.format == "csv":
        if not args.output:
            raise SystemExit("--format csv requires --output")
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        write_csv(out, rows)
        print(f"written: {out} ({len(rows)} devices)")
        return 0
    text = json.dumps({"root": str(root), "devices": rows}, ensure_ascii=False, indent=2)
    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        print(f"written: {out} ({len(rows)} devices)")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
