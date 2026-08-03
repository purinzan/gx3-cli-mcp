from __future__ import annotations

"""Regression test for timing-chart detection using synthetic SQLite fixtures."""

import sqlite3
import sys
import tempfile
from pathlib import Path


XREF_SCHEMA = """
create table xref (
    id integer primary key autoincrement,
    device text,
    device_type text,
    number integer,
    access text,
    role text,
    opcode text,
    arg_index integer,
    const_args text,
    detail text,
    lddb text,
    pos integer,
    pou text,
    step integer,
    title text,
    comment text,
    parse_status text
)
"""


def split_device(device: str) -> tuple[str, int]:
    prefix = device.rstrip("0123456789")
    return prefix, int(device[len(prefix) :])


def add_xref(con: sqlite3.Connection, device: str, access: str, role: str, *, opcode: str = "", pos: int = 0, comment: str = "", const_args: str = "", detail: str = "", arg_index: int = 0) -> None:
    device_type, number = split_device(device)
    con.execute(
        """
        insert into xref(
            device, device_type, number, access, role, opcode, arg_index,
            const_args, detail, lddb, pos, pou, step, title, comment, parse_status
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            device,
            device_type,
            number,
            access,
            role,
            opcode or role,
            arg_index,
            const_args,
            detail,
            "SYNTH_LDDB.db",
            pos,
            "SYNTH_POU",
            pos // 1024,
            "",
            comment,
            "exact",
        ),
    )


def create_xref(path: Path, rows: list[tuple[str, str, str, dict]]) -> None:
    con = sqlite3.connect(path)
    con.executescript(XREF_SCHEMA)
    for device, access, role, kwargs in rows:
        add_xref(con, device, access, role, **kwargs)
    con.commit()
    con.close()


def create_link_db(path: Path, xref_a: Path, xref_b: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        create table project(label text primary key, root text not null, xref_db text not null);
        create table link_map(
            id integer primary key autoincrement,
            project_a text not null,
            device_a text not null,
            project_b text not null,
            device_b text not null,
            link_type text not null,
            link_addr text,
            direction text,
            confidence text,
            role text,
            evidence text
        );
        """
    )
    con.executemany(
        "insert into project(label, root, xref_db) values (?, ?, ?)",
        [
            ("LINE_A", "synthetic-a", str(xref_a)),
            ("LINE_B", "synthetic-b", str(xref_b)),
        ],
    )
    rows = [
        ("LINE_B", "B200", "LINE_A", "M1000", "comment-role", "", "LINE_B_to_LINE_A", "high", "ready", "synthetic"),
        ("LINE_A", "M1100", "LINE_B", "B300", "comment-role", "", "LINE_A_to_LINE_B", "high", "auto", "synthetic"),
        ("LINE_A", "M9000", "LINE_B", "B300", "comment-role", "", "LINE_A_to_LINE_B", "high", "auto", "synthetic"),
        ("LINE_A", "M1101", "LINE_B", "B301", "comment-role", "", "LINE_A_to_LINE_B", "high", "request", "synthetic"),
        ("LINE_A", "M1104", "LINE_B", "B304", "comment-role", "", "LINE_A_to_LINE_B", "high", "running", "synthetic"),
        ("LINE_B", "B220", "LINE_A", "M1020", "comment-role", "", "LINE_B_to_LINE_A", "high", "complete", "synthetic"),
        ("LINE_B", "B221", "LINE_A", "M1021", "comment-role", "", "LINE_B_to_LINE_A", "high", "normal ok", "synthetic"),
        ("LINE_A", "W500", "LINE_B", "W800", "exact-device", "W", "LINE_A_to_LINE_B", "high", "", "synthetic"),
        ("LINE_A", "W501", "LINE_B", "W801", "exact-device", "W", "LINE_A_to_LINE_B", "high", "", "synthetic"),
    ]
    con.executemany(
        """
        insert into link_map(
            project_a, device_a, project_b, device_b, link_type, link_addr,
            direction, confidence, role, evidence
        ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    con.commit()
    con.close()


def main() -> int:
    from gx3cli.gx3_timing_chart import detect_signals, first_by_role

    with tempfile.TemporaryDirectory(prefix="gx3_timing_") as tmp:
        root = Path(tmp)
        xref_a = root / "line_a_xref.sqlite"
        xref_b = root / "line_b_xref.sqlite"
        link_db = root / "link_map.sqlite"
        create_xref(
            xref_a,
            [
                ("M1", "read", "a", {"pos": 1000, "comment": "auto condition"}),
                ("M1100", "write", "c", {"pos": 1000, "comment": "auto to LINE_B"}),
                ("M9000", "write", "c", {"pos": 1001, "comment": "auto unrelated"}),
                ("M2", "read", "a", {"pos": 1101, "comment": "request condition"}),
                ("M1101", "write", "c", {"pos": 1101, "comment": "request to LINE_B"}),
                ("M3", "read", "a", {"pos": 1104, "comment": "running condition"}),
                ("M1104", "write", "c", {"pos": 1104, "comment": "running to LINE_B"}),
                ("M1020", "read", "a", {"pos": 1200, "comment": "complete from LINE_B"}),
                ("M1021", "read", "a", {"pos": 1201, "comment": "normal from LINE_B"}),
                ("M50", "read", "a", {"pos": 2000, "comment": "data valid"}),
                ("W500", "write", "MOV", {"pos": 2000, "comment": "payload word"}),
                ("W501", "write", "MOV", {"pos": 2000, "comment": "payload word"}),
            ],
        )
        create_xref(
            xref_b,
            [
                ("B200", "write", "c", {"pos": 3000, "comment": "ready to LINE_A"}),
                ("B300", "read", "a", {"pos": 3100, "comment": "auto from LINE_A"}),
                ("B301", "read", "a", {"pos": 3101, "comment": "request from LINE_A"}),
                ("B304", "read", "a", {"pos": 3104, "comment": "running from LINE_A"}),
                ("B220", "write", "c", {"pos": 3200, "comment": "complete to LINE_A"}),
                ("B221", "write", "c", {"pos": 3201, "comment": "normal to LINE_A"}),
                ("L10", "read", "a", {"pos": 4000, "comment": "capture trigger"}),
                ("W800", "read", "BMOV", {"pos": 4000, "comment": "payload word", "const_args": "K2", "arg_index": 0}),
                ("ZR900", "write", "BMOV", {"pos": 4000, "arg_index": 1}),
                ("ZR100", "write", "SET", {"pos": 4000, "detail": "bit=K0", "arg_index": 2}),
            ],
        )
        create_link_db(link_db, xref_a, xref_b)
        signals, data_groups = detect_signals("LINE_A", "LINE_B", link_db)

    ready = first_by_role(signals, "ready", "LINE_B_to_LINE_A")
    if ready is None or ready.sender_device != "B200" or ready.receiver_device != "M1000":
        raise AssertionError(f"ready pair mismatch: {ready}")

    request = first_by_role(signals, "request", "LINE_A_to_LINE_B")
    if request is None or request.sender_device != "M1101" or request.receiver_device != "B301":
        raise AssertionError(f"request pair mismatch: {request}")

    autos = [s for s in signals if s.role == "auto" and s.direction == "LINE_A_to_LINE_B"]
    if any(s.sender_device == "M9000" for s in autos):
        raise AssertionError("off-band auto sender should have been filtered out")
    b300 = [s for s in autos if s.receiver_device == "B300"]
    if not b300 or b300[0].sender_device != "M1100":
        raise AssertionError(f"auto sender for B300 should be M1100, got {b300}")

    if not data_groups:
        raise AssertionError("no data capture groups detected")
    top = data_groups[0]
    if top.devices[:2] != ("W800", "W801"):
        raise AssertionError(f"top data group should expand W800..W801, got {top.devices}")
    if "L10" not in top.receiver_trigger:
        raise AssertionError(f"top data group trigger should include L10, got {top.receiver_trigger!r}")
    for expected in ["BMOV W800 K2 -> ZR900", "SET K0M100"]:
        if expected not in top.receiver_action:
            raise AssertionError(f"data action missing {expected!r}: {top.receiver_action!r}")

    print("all timing detect checks passed")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
