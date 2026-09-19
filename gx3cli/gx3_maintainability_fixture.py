from __future__ import annotations

"""Synthetic project pair for Doctor maintainability regression tests.

Both profiles expose the same two field-output behaviors:

* Y0 follows the run-permissive path M11 AND NOT X2.
* Y1 follows M11 as a status output.

The bad profile adds maintenance-only hazards around that core behavior:
missing physical-I/O comments, duplicate output ownership, a word device with
multiple writers, SET/RST split across programs, duplicated vague comments, and
dead legacy scratch devices. The fixture stays small so Doctor changes can be
tested without customer projects or the larger demo-line profile.
"""

import shutil
import sqlite3
from pathlib import Path

from gx3cli.gx3_synthetic_project import _create_demo_comment_db, _create_demo_ladder_db


GOOD_COMMENTS: list[tuple[str, str]] = [
    ("X0", "Machine safety permissive input"),
    ("X1", "Automatic operation request input"),
    ("X2", "Forward end limit input"),
    ("X3", "Maintenance latch set input"),
    ("X4", "Maintenance latch reset input"),
    ("M10", "Safety permissive established"),
    ("M11", "Automatic run permissive"),
    ("Y0", "Forward command output"),
    ("Y1", "Run status output"),
]

BAD_COMMENTS: list[tuple[str, str]] = [
    ("X0", "Permissive"),
    # X1 intentionally has no comment.
    ("X2", "Limit"),
    ("X3", "Flag"),
    ("X4", "Flag"),
    ("M10", "Flag"),
    ("M11", "Flag"),
    ("M90", "Temp"),
    ("M900", "Old flag"),
    ("M901", "Old flag"),
    # Y0 intentionally has no comment.
    ("Y1", "Lamp"),
]


def _core_sections() -> list[tuple[str, list[tuple[dict, dict]]]]:
    return [
        (
            "Run permissive",
            [
                ({"device": "X0"}, {"type": "coil", "device": "M10"}),
                (
                    {"and": [{"device": "M10"}, {"device": "X1"}]},
                    {"type": "coil", "device": "M11"},
                ),
                (
                    {"and": [{"device": "M11"}, {"not": {"device": "X2"}}]},
                    {"type": "coil", "device": "Y0"},
                ),
                ({"device": "M11"}, {"type": "coil", "device": "Y1"}),
            ],
        )
    ]


def _prepare_root(root: Path, overwrite: bool) -> Path:
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"output already exists: {root}")
        if root.is_dir():
            shutil.rmtree(root)
        else:
            root.unlink()
    root.mkdir(parents=True)
    (root / "UnitConfig.dat").write_text("synthetic maintainability fixture\n", encoding="utf-8")
    (root / "CPU.PRM").write_text("synthetic cpu parameters\n", encoding="utf-8")
    (root / "LabelData.db").write_bytes(b"")
    return root


def _mov_row(source: int = 100, destination: int = 120) -> str:
    return (
        "V1:9:1:1:1:1:1:1:a:SM:MOV:D:D:cb{fg=fg{dim=4x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=400:vt=nn}]}:pos=0,0}:"
        "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
        f"d{{s=#:a={source}:vt=nn}}:d{{s=#:a={destination}:vt=nn}}]}}:pos=1,0}}]}}}}"
    )


def _append_mov_writer(path: Path, guid: str, pos: float) -> None:
    con = sqlite3.connect(path)
    try:
        con.execute(
            "insert into LadderBlocks values (?, ?, ?, ?, ?, ?, ?)",
            (guid, pos, 0, _mov_row(), 1, 0, 0),
        )
        con.commit()
    finally:
        con.close()


def create_good_maintainability_project(root: Path, overwrite: bool = False) -> Path:
    """Create the organized baseline used to prove Doctor findings improve."""
    root = _prepare_root(root, overwrite)
    _create_demo_ladder_db(root / "001_LDDB.db", _core_sections(), seed=81000)
    _append_mov_writer(root / "001_LDDB.db", "_guid/maintainability/good/d120", 100.0)
    _create_demo_comment_db(root / "001_DC.db", GOOD_COMMENTS)
    return root


def create_bad_maintainability_project(root: Path, overwrite: bool = False) -> Path:
    """Create a field-output-equivalent project with handover debt."""
    root = _prepare_root(root, overwrite)

    first_program = _core_sections() + [
        (
            "TEMP latch added during modification",
            [
                ({"device": "X3"}, {"type": "set", "device": "M90"}),
                # Two legacy devices are written but never consumed. Their
                # duplicate vague comments also exercise comment-conflict.
                ({"device": "SM401"}, {"type": "coil", "device": "M900"}),
                ({"device": "SM401"}, {"type": "coil", "device": "M901"}),
            ],
        )
    ]
    second_program = [
        (
            "NEW patch appended elsewhere",
            [
                # Same condition as the original Y0 writer, so the field-output
                # truth table is intentionally unchanged while ownership is
                # duplicated across programs.
                (
                    {"and": [{"device": "M11"}, {"not": {"device": "X2"}}]},
                    {"type": "coil", "device": "Y0"},
                ),
                # Reset is deliberately separated from the SET above.
                ({"device": "X4"}, {"type": "rst", "device": "M90"}),
            ],
        )
    ]

    _create_demo_ladder_db(root / "001_LDDB.db", first_program, seed=82000)
    _create_demo_ladder_db(root / "002_LDDB.db", second_program, seed=83000)
    _append_mov_writer(root / "001_LDDB.db", "_guid/maintainability/bad/d120/one", 100.0)
    _append_mov_writer(root / "002_LDDB.db", "_guid/maintainability/bad/d120/two", 100.0)
    _create_demo_comment_db(root / "001_DC.db", BAD_COMMENTS)
    return root


__all__ = [
    "create_bad_maintainability_project",
    "create_good_maintainability_project",
]
