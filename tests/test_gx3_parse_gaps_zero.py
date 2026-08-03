from __future__ import annotations

"""Regression test: a synthetic rung has zero intermediate parse-gap rows."""

import sqlite3
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_intermediate_tool import generate_rung


def write_synthetic_lddb(root: Path) -> None:
    data, rowsize, _ = generate_rung(
        {"and": [{"device": "M100"}, {"not": {"device": "M101"}}]},
        {"type": "coil", "device": "M200"},
    )
    con = sqlite3.connect(root / "001_LDDB.db")
    con.execute(
        """
        create table LadderBlocks (
            id text,
            pos real,
            blocktype integer,
            data text,
            rowsize integer,
            translated integer,
            ConvTarget integer
        )
        """
    )
    con.execute(
        "insert into LadderBlocks values (?, ?, ?, ?, ?, ?, ?)",
        ("_guid/00000000-0000-0000-0000-000000000001", 0.0, 0, data, rowsize, 0, 0),
    )
    con.commit()
    con.close()


def main() -> int:
    from gx3cli.analyze_gx3_intermediate_parse_gaps import ProjectInput, collect_project

    with tempfile.TemporaryDirectory(prefix="gx3_synth_") as tmp:
        root = Path(tmp)
        write_synthetic_lddb(root)
        rows = collect_project(ProjectInput("SYNTH", root))
    if rows:
        samples = [
            f"{row['lddb']}:{row['pos']} delta={row['delta']} reason={row['likely_reason']}"
            for row in rows[:10]
        ]
        raise AssertionError(f"expected 0 parse-gap rows for synthetic project: {'; '.join(samples)}")
    print("parse gap check passed (synthetic project, gap rows = 0)")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
