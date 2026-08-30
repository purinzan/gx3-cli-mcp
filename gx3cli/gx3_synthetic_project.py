from __future__ import annotations

"""Generate non-confidential GX3-like fixtures for tests, demos, and docs."""

import argparse
import shutil
import sqlite3
import zipfile
from pathlib import Path

from gx3cli.gx3_intermediate_tool import generate_rung, parse_device
from gx3cli.review_gx3_project import DEVICE_CODE_BY_TYPE


def _title_data(text: str) -> str:
    """Encode a section title the way extract_title_text() reads it back."""
    return f"V1:0:t:{text}:st{{m=0:dim=0}}"


def _create_ladder_db(path: Path) -> None:
    con = sqlite3.connect(path)
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
    rows = []
    title = _title_data("Emergency stop origin return demo")
    rows.append(("_guid/00000000-0000-0000-0000-000000000010", 0.0, 1, title, len(title), 0, 0))
    data, rowsize, _ = generate_rung(
        {"device": "X48"},
        {"type": "coil", "device": "M55"},
    )
    rows.append(("_guid/00000000-0000-0000-0000-000000000011", 5.0, 0, data, rowsize, 0, 0))
    data, rowsize, _ = generate_rung(
        {"and": [{"device": "X16"}, {"not": {"device": "M55"}}]},
        {"type": "coil", "device": "M100"},
    )
    rows.append(("_guid/00000000-0000-0000-0000-000000000012", 10.0, 0, data, rowsize, 0, 0))
    data, rowsize, _ = generate_rung(
        {"and": [{"device": "M100"}, {"device": "X32"}]},
        {"type": "coil", "device": "Y16"},
    )
    rows.append(("_guid/00000000-0000-0000-0000-000000000013", 20.0, 0, data, rowsize, 0, 0))
    con.executemany("insert into LadderBlocks values (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def _create_comment_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("create table DEVICE_DATA(SEQ integer, DevCode integer, ExtCode integer, ExtNo integer, DevNoLow integer, BitNo integer)")
    con.execute("create table COMMENT_DATA(DeviceSEQ integer, CmtNo integer, CmtData text, DelFlag integer)")
    devices = [
        (1, "X", 0x30, "Synthetic origin inhibit request input"),
        (2, "X", 0x10, "Synthetic emergency stop input"),
        (3, "M", 55, "Synthetic origin-return inhibit"),
        (4, "M", 100, "Synthetic origin-return request"),
        (5, "X", 0x20, "Synthetic servo ready input"),
        (6, "Y", 0x10, "Synthetic origin-return command"),
    ]
    con.executemany(
        "insert into DEVICE_DATA values (?, ?, 0, 0, ?, 0)",
        [(seq, DEVICE_CODE_BY_TYPE[dev_type], dev_no) for seq, dev_type, dev_no, _comment in devices],
    )
    con.executemany(
        "insert into COMMENT_DATA values (?, 5, ?, 0)",
        [(seq, comment) for seq, _dev_type, _dev_no, comment in devices],
    )
    con.commit()
    con.close()


def create_synthetic_project(root: Path, overwrite: bool = False) -> Path:
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"output already exists: {root}")
        if root.is_dir():
            shutil.rmtree(root)
        else:
            root.unlink()
    root.mkdir(parents=True)
    (root / "UnitConfig.dat").write_text("synthetic unit config\n", encoding="utf-8")
    (root / "CPU.PRM").write_text("synthetic cpu parameters\n", encoding="utf-8")
    (root / "LabelData.db").write_bytes(b"")
    _create_ladder_db(root / "001_LDDB.db")
    _create_comment_db(root / "001_DC.db")
    return root


def create_synthetic_gx3_archive(path: Path, overwrite: bool = False, profile: str = "basic") -> Path:
    work = path.with_suffix("")
    build = create_demo_line_project if profile == "demo-line" else create_synthetic_project
    build(work, overwrite=overwrite)
    if path.exists():
        if not overwrite:
            raise FileExistsError(f"output already exists: {path}")
        path.unlink()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file in sorted(work.rglob("*")):
            if file.is_file():
                zf.write(file, file.relative_to(work).as_posix())
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a synthetic, non-confidential GX3-like fixture.")
    parser.add_argument("output", type=Path, help="folder path or .gx3 archive path")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--profile",
        choices=["basic", "demo-line"],
        default="basic",
        help=(
            "basic (default): 3 rungs, enough to smoke-test a command. "
            "demo-line: a pick-and-place transfer station across 3 programs "
            "with ~60 commented devices, for demos and for reproducing a bug "
            "without sending a real project."
        ),
    )
    args = parser.parse_args(argv)
    if args.output.suffix.lower() == ".gx3":
        created = create_synthetic_gx3_archive(args.output, overwrite=args.overwrite, profile=args.profile)
    elif args.profile == "demo-line":
        created = create_demo_line_project(args.output, overwrite=args.overwrite)
    else:
        created = create_synthetic_project(args.output, overwrite=args.overwrite)
    print(f"synthetic project created: {created}")
    return 0



# ---------------------------------------------------------------------------
# "demo-line" profile
# ---------------------------------------------------------------------------
#
# A single-axis pick-and-place transfer station, split across three programs so
# the multi-POU paths (xref, program-map, dead-logic) have something real to
# work on. Everything here is invented and describes no actual equipment.
#
# The layout deliberately contains the faults `lint` and `dead-logic` look for,
# each marked PLANTED below, so that a first run on the demo project reports
# findings instead of an empty table.

DEMO_COMMENTS: list[tuple[str, str]] = [
    # safety and mode selection
    ("X0", "Emergency stop pushbutton (NC)"),
    ("X1", "Safety door closed"),
    ("X2", "Light curtain clear"),
    ("X3", "Air pressure normal"),
    ("X10", "Auto/manual selector: auto"),
    ("X11", "Cycle start pushbutton"),
    ("X12", "Cycle stop pushbutton"),
    ("X13", "Alarm reset pushbutton"),
    # axis and gripper feedback
    ("X20", "Origin position sensor"),
    ("X21", "Advance end sensor"),
    ("X22", "Retract end sensor"),
    ("X23", "Lift up end sensor"),
    ("X24", "Lift down end sensor"),
    ("X30", "Workpiece present at pickup"),
    ("X31", "Gripper open confirmation"),
    ("X32", "Gripper closed confirmation"),
    ("X40", "Servo ready"),
    ("X41", "Servo alarm contact (closed when healthy)"),
    ("X42", "Transfer overtravel limit"),
    # outputs
    ("Y0", "Advance solenoid"),
    ("Y1", "Retract solenoid"),
    ("Y2", "Lift up solenoid"),
    ("Y3", "Lift down solenoid"),
    ("Y4", "Gripper close solenoid"),
    ("Y5", "Gripper open solenoid"),
    ("Y10", "Running lamp"),
    ("Y11", "Alarm lamp"),
    ("Y12", "Origin position lamp"),
    ("Y13", "Alarm buzzer"),
    # internal relays
    ("M0", "Emergency stop relay"),
    ("M1", "Safety circuit established"),
    ("M2", "Machine ready to run"),
    ("M3", "Servo healthy"),
    ("M10", "Auto mode active"),
    ("M11", "Manual mode active"),
    ("M20", "Origin return request"),
    ("M21", "Origin return in progress"),
    ("M22", "Origin return complete"),
    ("M30", "Step 0: waiting at origin"),
    ("M31", "Step 1: advance to pickup"),
    ("M32", "Step 2: lower and grip"),
    ("M33", "Step 3: lift with workpiece"),
    ("M34", "Step 4: retract to place"),
    ("M35", "Step 5: release workpiece"),
    ("M36", "Step 6: cycle complete"),
    ("M50", "Cycle start latch"),
    ("M51", "Cycle stop request"),
    ("M60", "Workpiece held by gripper"),
    ("M100", "Any alarm active"),
    ("M101", "Alarm: emergency stop"),
    ("M102", "Alarm: air pressure low"),
    ("M103", "Alarm: servo fault"),
    ("M104", "Alarm: transfer overtravel"),
    ("M110", "Alarm reset accepted"),
    # PLANTED (comment-conflict): M120 and M121 carry identical comment text.
    ("M120", "Interface: upstream ready"),
    ("M121", "Interface: upstream ready"),
    ("M122", "Interface: downstream accept"),
    ("M123", "Interface: transfer request to host"),
    # PLANTED (unused-device): written by the alarm section, read by nobody.
    ("M900", "Spare: reserved for future alarm summary"),
    ("SM400", "Always ON special relay"),
]

# Each program is (file stem, section title, [(logic, output), ...]).
# Rungs are laid out in order; pos values are assigned automatically.
DEMO_PROGRAMS: list[tuple[str, list[tuple[str, list[tuple[dict, dict]]]]]] = [
    (
        "001",
        [
            (
                "Safety and mode selection",
                [
                    # Emergency stop is wired NC, so the relay follows the contact.
                    ({"device": "X0"}, {"type": "coil", "device": "M0"}),
                    (
                        {"and": [{"device": "M0"}, {"device": "X1"}, {"device": "X2"}]},
                        {"type": "coil", "device": "M1"},
                    ),
                    ({"device": "X41"}, {"type": "coil", "device": "M3"}),
                    (
                        {"and": [{"device": "M1"}, {"device": "M3"}, {"device": "X3"}]},
                        {"type": "coil", "device": "M2"},
                    ),
                    (
                        {"and": [{"device": "X10"}, {"device": "M2"}]},
                        {"type": "coil", "device": "M10"},
                    ),
                    (
                        {"and": [{"not": {"device": "X10"}}, {"device": "M2"}]},
                        {"type": "coil", "device": "M11"},
                    ),
                ],
            ),
            (
                "Cycle start and stop latch",
                [
                    (
                        {
                            "and": [
                                {"or": [{"device": "X11"}, {"device": "M50"}]},
                                {"device": "M10"},
                                {"device": "M22"},
                                {"not": {"device": "M51"}},
                                {"not": {"device": "M100"}},
                            ]
                        },
                        {"type": "coil", "device": "M50"},
                    ),
                    (
                        {"or": [{"device": "X12"}, {"device": "M100"}]},
                        {"type": "coil", "device": "M51"},
                    ),
                ],
            ),
            (
                "Indicator lamps",
                [
                    ({"device": "M50"}, {"type": "coil", "device": "Y10"}),
                    ({"device": "M100"}, {"type": "coil", "device": "Y11"}),
                    ({"device": "M22"}, {"type": "coil", "device": "Y12"}),
                ],
            ),
        ],
    ),
    (
        "002",
        [
            (
                "Origin return sequence",
                [
                    (
                        {
                            "and": [
                                {"device": "M2"},
                                {"not": {"device": "M22"}},
                                {"not": {"device": "M100"}},
                            ]
                        },
                        {"type": "coil", "device": "M20"},
                    ),
                    (
                        {"and": [{"device": "M20"}, {"not": {"device": "X20"}}]},
                        {"type": "coil", "device": "M21"},
                    ),
                    (
                        {"and": [{"device": "M21"}, {"device": "X40"}]},
                        {"type": "coil", "device": "Y1"},
                    ),
                    (
                        {"and": [{"device": "X20"}, {"device": "X22"}, {"device": "M2"}]},
                        {"type": "set", "device": "M22"},
                    ),
                    (
                        {"or": [{"not": {"device": "M0"}}, {"device": "M103"}]},
                        {"type": "rst", "device": "M22"},
                    ),
                ],
            ),
            (
                "Manual jog interlocks",
                [
                    (
                        {
                            "and": [
                                {"device": "M11"},
                                {"device": "M2"},
                                {"not": {"device": "X21"}},
                            ]
                        },
                        {"type": "coil", "device": "Y0"},
                    ),
                    # PLANTED (duplicate-coil): Y10 is also driven from the
                    # indicator lamp section in program 001. Both rungs run every
                    # scan, so the last one wins and the first is invisible.
                    ({"device": "M11"}, {"type": "coil", "device": "Y10"}),
                ],
            ),
        ],
    ),
    (
        "003",
        [
            (
                "Automatic cycle step sequence",
                [
                    (
                        {"and": [{"device": "M50"}, {"device": "X20"}, {"not": {"device": "M31"}}]},
                        {"type": "coil", "device": "M30"},
                    ),
                    (
                        {"and": [{"device": "M30"}, {"device": "X30"}]},
                        {"type": "set", "device": "M31"},
                    ),
                    (
                        {"and": [{"device": "M31"}, {"device": "X21"}]},
                        {"type": "set", "device": "M32"},
                    ),
                    (
                        {"and": [{"device": "M32"}, {"device": "X24"}, {"device": "X32"}]},
                        {"type": "set", "device": "M33"},
                    ),
                    (
                        {"and": [{"device": "M33"}, {"device": "X23"}]},
                        {"type": "set", "device": "M34"},
                    ),
                    (
                        {"and": [{"device": "M34"}, {"device": "X22"}]},
                        {"type": "set", "device": "M35"},
                    ),
                    (
                        {"and": [{"device": "M35"}, {"device": "X31"}]},
                        {"type": "set", "device": "M36"},
                    ),
                    # One reset clears the whole step chain at cycle end.
                    (
                        {"or": [{"device": "M36"}, {"device": "M51"}]},
                        {"type": "rst", "device": "M31"},
                    ),
                    (
                        {"or": [{"device": "M36"}, {"device": "M51"}]},
                        {"type": "rst", "device": "M32"},
                    ),
                    (
                        {"or": [{"device": "M36"}, {"device": "M51"}]},
                        {"type": "rst", "device": "M33"},
                    ),
                    (
                        {"or": [{"device": "M36"}, {"device": "M51"}]},
                        {"type": "rst", "device": "M34"},
                    ),
                    (
                        {"or": [{"device": "M36"}, {"device": "M51"}]},
                        {"type": "rst", "device": "M35"},
                    ),
                    ({"device": "M51"}, {"type": "rst", "device": "M36"}),
                ],
            ),
            (
                "Actuator outputs",
                [
                    (
                        {"and": [{"device": "M31"}, {"not": {"device": "X21"}}]},
                        {"type": "coil", "device": "Y0"},
                    ),
                    (
                        {"and": [{"device": "M34"}, {"not": {"device": "X22"}}]},
                        {"type": "coil", "device": "Y1"},
                    ),
                    (
                        {"and": [{"device": "M33"}, {"not": {"device": "X23"}}]},
                        {"type": "coil", "device": "Y2"},
                    ),
                    (
                        {"and": [{"device": "M32"}, {"not": {"device": "X24"}}]},
                        {"type": "coil", "device": "Y3"},
                    ),
                    (
                        {"and": [{"device": "M32"}, {"device": "X24"}]},
                        {"type": "coil", "device": "Y4"},
                    ),
                    (
                        {"and": [{"device": "M35"}, {"device": "X23"}]},
                        {"type": "coil", "device": "Y5"},
                    ),
                    ({"device": "X32"}, {"type": "coil", "device": "M60"}),
                ],
            ),
            (
                "Alarm detection and reset",
                [
                    ({"not": {"device": "M0"}}, {"type": "set", "device": "M101"}),
                    ({"not": {"device": "X3"}}, {"type": "set", "device": "M102"}),
                    ({"not": {"device": "X41"}}, {"type": "set", "device": "M103"}),
                    # PLANTED (dead-logic, SET without RST): M104 latches on
                    # overtravel and nothing ever clears it, so the machine can
                    # never be reset without a power cycle.
                    ({"device": "X42"}, {"type": "set", "device": "M104"}),
                    (
                        {
                            "or": [
                                {"device": "M101"},
                                {"device": "M102"},
                                {"device": "M103"},
                                {"device": "M104"},
                            ]
                        },
                        {"type": "coil", "device": "M100"},
                    ),
                    ({"and": [{"device": "X13"}, {"device": "M0"}]}, {"type": "coil", "device": "M110"}),
                    ({"device": "M110"}, {"type": "rst", "device": "M101"}),
                    ({"device": "M110"}, {"type": "rst", "device": "M102"}),
                    ({"device": "M110"}, {"type": "rst", "device": "M103"}),
                    ({"device": "M100"}, {"type": "coil", "device": "Y13"}),
                    # PLANTED (unused-device): nothing ever reads M900.
                    ({"device": "M100"}, {"type": "coil", "device": "M900"}),
                ],
            ),
            (
                "Upstream and downstream interface",
                [
                    ({"and": [{"device": "M2"}, {"device": "M22"}]}, {"type": "coil", "device": "M120"}),
                    ({"and": [{"device": "M2"}, {"device": "M22"}]}, {"type": "coil", "device": "M121"}),
                    ({"and": [{"device": "M36"}, {"device": "M60"}]}, {"type": "coil", "device": "M123"}),
                    # PLANTED (dead-logic, constant-OFF contact): SM400 is always
                    # ON, so its NC contact never closes and M122 can never be set.
                    (
                        {"and": [{"not": {"device": "SM400"}}, {"device": "M123"}]},
                        {"type": "coil", "device": "M122"},
                    ),
                ],
            ),
        ],
    ),
]


# The three hand-written programs above carry the interesting semantics and the
# planted faults. Real machines then repeat a station block many times over, and
# a demo that stops at 50 rungs never exercises the paths that only matter at
# scale -- section filtering, xref lookups that return dozens of hits, a lint
# summary long enough to need sorting. STATION_COUNT identical-in-shape,
# distinct-in-devices stations bring the fixture to roughly 500 rungs.

# 14 stations x 32 rungs, plus the 53 hand-written core rungs, is about 500.
STATION_COUNT = 14


def _station_devices(index: int) -> dict[str, str]:
    """Device names for station `index`, in the hexadecimal X/Y numbering GX uses."""
    x = 0x200 + index * 0x20
    y = 0x300 + index * 0x10
    m = 1000 + index * 100
    return {
        "in_ready": f"X{x:X}",
        "in_work": f"X{x + 1:X}",
        "in_clamp_open": f"X{x + 2:X}",
        "in_clamp_closed": f"X{x + 3:X}",
        "in_up": f"X{x + 4:X}",
        "in_down": f"X{x + 5:X}",
        "in_fwd": f"X{x + 6:X}",
        "in_rev": f"X{x + 7:X}",
        "in_pressure": f"X{x + 8:X}",
        "in_overload": f"X{x + 9:X}",
        "out_clamp": f"Y{y:X}",
        "out_unclamp": f"Y{y + 1:X}",
        "out_lift": f"Y{y + 2:X}",
        "out_lower": f"Y{y + 3:X}",
        "out_fwd": f"Y{y + 4:X}",
        "out_rev": f"Y{y + 5:X}",
        "out_lamp": f"Y{y + 6:X}",
        "out_alarm": f"Y{y + 7:X}",
        "enable": f"M{m}",
        "busy": f"M{m + 1}",
        "done": f"M{m + 2}",
        "step0": f"M{m + 10}",
        "step1": f"M{m + 11}",
        "step2": f"M{m + 12}",
        "step3": f"M{m + 13}",
        "step4": f"M{m + 14}",
        "step5": f"M{m + 15}",
        "reset": f"M{m + 20}",
        "alarm_any": f"M{m + 30}",
        "alarm_pressure": f"M{m + 31}",
        "alarm_overload": f"M{m + 32}",
        "alarm_timeout": f"M{m + 33}",
        "handoff_req": f"M{m + 40}",
        "handoff_ack": f"M{m + 41}",
    }


STATION_COMMENT_LABELS = [
    ("in_ready", "St{n}: station ready"),
    ("in_work", "St{n}: workpiece present"),
    ("in_clamp_open", "St{n}: clamp open confirmation"),
    ("in_clamp_closed", "St{n}: clamp closed confirmation"),
    ("in_up", "St{n}: lift up end"),
    ("in_down", "St{n}: lift down end"),
    ("in_fwd", "St{n}: advance end"),
    ("in_rev", "St{n}: retract end"),
    ("in_pressure", "St{n}: clamp pressure normal"),
    ("in_overload", "St{n}: drive overload contact"),
    ("out_clamp", "St{n}: clamp solenoid"),
    ("out_unclamp", "St{n}: unclamp solenoid"),
    ("out_lift", "St{n}: lift solenoid"),
    ("out_lower", "St{n}: lower solenoid"),
    ("out_fwd", "St{n}: advance solenoid"),
    ("out_rev", "St{n}: retract solenoid"),
    ("out_lamp", "St{n}: station running lamp"),
    ("out_alarm", "St{n}: station alarm lamp"),
    ("enable", "St{n}: station enabled"),
    ("busy", "St{n}: station busy"),
    ("done", "St{n}: station cycle complete"),
    ("step0", "St{n} step 0: idle"),
    ("step1", "St{n} step 1: clamp"),
    ("step2", "St{n} step 2: lift"),
    ("step3", "St{n} step 3: advance"),
    ("step4", "St{n} step 4: retract and lower"),
    ("step5", "St{n} step 5: unclamp"),
    ("reset", "St{n}: sequence reset"),
    ("alarm_any", "St{n}: any station alarm"),
    ("alarm_pressure", "St{n}: alarm clamp pressure low"),
    ("alarm_overload", "St{n}: alarm drive overload"),
    ("alarm_timeout", "St{n}: alarm cycle timeout"),
    ("handoff_req", "St{n}: transfer request to next station"),
    ("handoff_ack", "St{n}: transfer accepted by next station"),
]


def _station_sections(index: int) -> list[tuple[str, list[tuple[dict, dict]]]]:
    """One station's sections. Shape is identical per station; devices are not."""
    d = _station_devices(index)
    n = index + 1
    prev = _station_devices(index - 1) if index > 0 else None
    nxt = _station_devices(index + 1) if index + 1 < STATION_COUNT else None
    steps = ["step0", "step1", "step2", "step3", "step4", "step5"]
    advance_conditions = ["in_work", "in_clamp_closed", "in_up", "in_fwd", "in_rev", "in_clamp_open"]

    interlocks: list[tuple[dict, dict]] = [
        (
            {"and": [{"device": "M2"}, {"device": "M50"}, {"not": {"device": d["alarm_any"]}}]},
            {"type": "coil", "device": d["enable"]},
        ),
        # A station may only run once the station before it has handed a
        # workpiece over and the one after it has room to accept the result.
        # This is what makes the line a chain rather than 14 islands: tracing an
        # output on the last station walks back through every station upstream.
        (
            {
                "and": [
                    {"device": d["enable"]},
                    {"device": d["in_ready"]},
                    {"device": d["in_pressure"]},
                    {"device": d["handoff_ack"]},
                ]
                + ([{"device": prev["handoff_req"]}] if prev else [{"device": "M120"}])
            },
            {"type": "coil", "device": d["busy"]},
        ),
        ({"or": [{"device": "M51"}, {"device": d["alarm_any"]}]}, {"type": "coil", "device": d["reset"]}),
        ({"device": d["busy"]}, {"type": "coil", "device": d["out_lamp"]}),
        ({"device": d["alarm_any"]}, {"type": "coil", "device": d["out_alarm"]}),
    ]

    sequence: list[tuple[dict, dict]] = [
        (
            {"and": [{"device": d["busy"]}, {"not": {"device": d[steps[1]]}}]},
            {"type": "coil", "device": d[steps[0]]},
        )
    ]
    for step_index in range(1, len(steps)):
        sequence.append(
            (
                {
                    "and": [
                        {"device": d[steps[step_index - 1]]},
                        {"device": d[advance_conditions[step_index - 1]]},
                        {"device": d["busy"]},
                    ]
                },
                {"type": "set", "device": d[steps[step_index]]},
            )
        )
    sequence.append(
        ({"and": [{"device": d[steps[-1]]}, {"device": d["in_clamp_open"]}]}, {"type": "coil", "device": d["done"]})
    )
    for step_index in range(1, len(steps)):
        sequence.append(
            (
                {"or": [{"device": d["done"]}, {"device": d["reset"]}]},
                {"type": "rst", "device": d[steps[step_index]]},
            )
        )

    outputs: list[tuple[dict, dict]] = [
        ({"and": [{"device": d[steps[1]]}, {"not": {"device": d["in_clamp_closed"]}}]}, {"type": "coil", "device": d["out_clamp"]}),
        ({"and": [{"device": d[steps[5]]}, {"not": {"device": d["in_clamp_open"]}}]}, {"type": "coil", "device": d["out_unclamp"]}),
        ({"and": [{"device": d[steps[2]]}, {"not": {"device": d["in_up"]}}]}, {"type": "coil", "device": d["out_lift"]}),
        ({"and": [{"device": d[steps[4]]}, {"not": {"device": d["in_down"]}}]}, {"type": "coil", "device": d["out_lower"]}),
        ({"and": [{"device": d[steps[3]]}, {"not": {"device": d["in_fwd"]}}]}, {"type": "coil", "device": d["out_fwd"]}),
        ({"and": [{"device": d[steps[4]]}, {"not": {"device": d["in_rev"]}}]}, {"type": "coil", "device": d["out_rev"]}),
    ]

    alarms: list[tuple[dict, dict]] = [
        ({"and": [{"device": d["busy"]}, {"not": {"device": d["in_pressure"]}}]}, {"type": "set", "device": d["alarm_pressure"]}),
        ({"not": {"device": d["in_overload"]}}, {"type": "set", "device": d["alarm_overload"]}),
        ({"and": [{"device": d[steps[3]]}, {"device": "M51"}]}, {"type": "set", "device": d["alarm_timeout"]}),
        (
            {
                "or": [
                    {"device": d["alarm_pressure"]},
                    {"device": d["alarm_overload"]},
                    {"device": d["alarm_timeout"]},
                ]
            },
            {"type": "coil", "device": d["alarm_any"]},
        ),
        ({"device": "M110"}, {"type": "rst", "device": d["alarm_pressure"]}),
        ({"device": "M110"}, {"type": "rst", "device": d["alarm_overload"]}),
        ({"device": "M110"}, {"type": "rst", "device": d["alarm_timeout"]}),
    ]

    handoff: list[tuple[dict, dict]] = [
        ({"and": [{"device": d["done"]}, {"device": d["in_work"]}]}, {"type": "coil", "device": d["handoff_req"]}),
    ]
    if nxt:
        handoff.append(
            (
                {"and": [{"device": nxt["enable"]}, {"not": {"device": nxt["busy"]}}]},
                {"type": "coil", "device": d["handoff_ack"]},
            )
        )
    else:
        # The last station hands off to the line interface instead of a successor.
        handoff.append(({"device": "M122"}, {"type": "coil", "device": d["handoff_ack"]}))

    return [
        (f"Station {n:02d} - interlocks", interlocks),
        (f"Station {n:02d} - step sequence", sequence),
        (f"Station {n:02d} - actuator outputs", outputs),
        (f"Station {n:02d} - alarms", alarms),
        (f"Station {n:02d} - handoff", handoff),
    ]


def _station_comments(index: int) -> list[tuple[str, str]]:
    d = _station_devices(index)
    n = index + 1
    return [(d[key], label.format(n=f"{n:02d}")) for key, label in STATION_COMMENT_LABELS]


def _guid(index: int) -> str:
    return f"_guid/00000000-0000-0000-0000-{index:012d}"


def _create_demo_ladder_db(path: Path, sections: list[tuple[str, list[tuple[dict, dict]]]], seed: int) -> None:
    """Write one program's sections and rungs as LadderBlocks rows.

    Section titles are blocktype 1; rungs are blocktype 0. `pos` is the step
    position GX Works3 shows, so it advances by each rung's real width rather
    than by a fixed stride.
    """
    con = sqlite3.connect(path)
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
    rows = []
    pos = 0.0
    index = seed
    for section_title, rungs in sections:
        title = _title_data(section_title)
        rows.append((_guid(index), pos, 1, title, len(title), 0, 0))
        index += 1
        pos += 1.0
        for logic, output in rungs:
            data, rowsize, steps = generate_rung(logic, output)
            rows.append((_guid(index), pos, 0, data, rowsize, 0, 0))
            index += 1
            pos += max(1.0, float(steps))
    con.executemany("insert into LadderBlocks values (?, ?, ?, ?, ?, ?, ?)", rows)
    con.commit()
    con.close()


def _create_demo_comment_db(path: Path, comments: list[tuple[str, str]]) -> None:
    con = sqlite3.connect(path)
    con.execute("create table DEVICE_DATA(SEQ integer, DevCode integer, ExtCode integer, ExtNo integer, DevNoLow integer, BitNo integer)")
    con.execute("create table COMMENT_DATA(DeviceSEQ integer, CmtNo integer, CmtData text, DelFlag integer)")
    device_rows = []
    comment_rows = []
    for seq, (device, comment) in enumerate(comments, start=1):
        # Derive the stored number with the same parser the ladder uses, so a
        # comment can never drift away from the device it describes.
        dev_type, number = parse_device(device)
        device_rows.append((seq, DEVICE_CODE_BY_TYPE[dev_type], number))
        comment_rows.append((seq, comment))
    con.executemany("insert into DEVICE_DATA values (?, ?, 0, 0, ?, 0)", device_rows)
    con.executemany("insert into COMMENT_DATA values (?, 5, ?, 0)", comment_rows)
    con.commit()
    con.close()


def create_demo_line_project(root: Path, overwrite: bool = False) -> Path:
    """Build the larger 'demo-line' fixture: three programs, ~60 devices."""
    if root.exists():
        if not overwrite:
            raise FileExistsError(f"output already exists: {root}")
        if root.is_dir():
            shutil.rmtree(root)
        else:
            root.unlink()
    root.mkdir(parents=True)
    (root / "UnitConfig.dat").write_text("synthetic unit config\n", encoding="utf-8")
    (root / "CPU.PRM").write_text("synthetic cpu parameters\n", encoding="utf-8")
    (root / "LabelData.db").write_bytes(b"")
    programs = list(DEMO_PROGRAMS)
    comments = list(DEMO_COMMENTS)
    for index in range(STATION_COUNT):
        # Two stations per program file, so the fixture also has a realistic
        # number of POUs rather than one enormous one.
        stem = f"{10 + index // 2:03d}"
        sections = _station_sections(index)
        existing = next((entry for entry in programs if entry[0] == stem), None)
        if existing:
            existing[1].extend(sections)
        else:
            programs.append((stem, sections))
        comments.extend(_station_comments(index))
    for offset, (stem, sections) in enumerate(programs):
        _create_demo_ladder_db(root / f"{stem}_LDDB.db", sections, seed=(offset + 1) * 1000)
    _create_demo_comment_db(root / "001_DC.db", comments)
    return root

if __name__ == "__main__":
    raise SystemExit(main())
