from __future__ import annotations

"""DevCode is the number a comment is filed under, and it is not documented.

Issue #29: a device type missing from the table comes back with a blank
comment, which cannot be told apart from a device nobody commented. It also
warned that guessing a code attaches comments to the wrong device, which is
worse than the blank.

C=70 and ST=74 are not guesses. They were read out of sample projects that use
counters and retentive timers in the ladder and comment them:

    EPE2.gx3   ladder uses 22 C devices, highest C52
               DevCode 70 holds 23 comments, range 0..52   -- same set
               ladder uses 22 ST devices, highest ST35
               DevCode 74 holds 22 comments, range 1..35   -- same set

Two more projects agree (GNC-A, GNC-B: C52 under 70, ST0 under 74). A code that
merely contains the used numbers proves nothing -- M's 10878 comments span
0..20479 and contain almost anything -- so what identifies a code is holding
the same set, not a superset.

LT, LC, LST and LZ stay out of the table: no sample project uses one, so there
is nothing to read their codes from.
"""

import ast
import pathlib

from gx3cli.extract_hmi_build_info import DEVICE_CODE_BY_TYPE
from gx3cli.extract_used_devices_without_comments import (
    DEVICE_CODE_BY_TYPE as USED_DEVICES_TABLE,
)
from gx3cli.review_gx3_project import DEVICE_CODE_BY_TYPE as REVIEW_TABLE


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_the_counter_and_retentive_timer_codes_are_recorded() -> None:
    assert DEVICE_CODE_BY_TYPE["C"] == 70
    assert DEVICE_CODE_BY_TYPE["ST"] == 74
    # The timer code was already known; it anchors the others.
    assert DEVICE_CODE_BY_TYPE["T"] == 66


def test_the_unconfirmed_types_are_left_out() -> None:
    # A wrong code puts one device's comment on another. None of these appears
    # in any sample project, so none of them can be read from data yet.
    for dev_type in ("LT", "LC", "LST", "LZ", "FX", "FY", "FD"):
        assert dev_type not in DEVICE_CODE_BY_TYPE, dev_type


def test_every_command_reads_the_same_table() -> None:
    # Two copies drift the moment a code is confirmed for one command and not
    # the other, and the symptom is a blank comment rather than an error.
    assert USED_DEVICES_TABLE is DEVICE_CODE_BY_TYPE
    assert REVIEW_TABLE is DEVICE_CODE_BY_TYPE


def test_no_module_keeps_its_own_copy() -> None:
    offenders: list[str] = []
    for path in sorted((ROOT / "gx3cli").glob("*.py")):
        if path.name == "extract_hmi_build_info.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "DEVICE_CODE_BY_TYPE":
                    offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "these define their own device code table; import it from "
        "extract_hmi_build_info:\n  " + "\n  ".join(offenders)
    )


def main() -> int:
    test_the_counter_and_retentive_timer_codes_are_recorded()
    test_the_unconfirmed_types_are_left_out()
    test_every_command_reads_the_same_table()
    test_no_module_keeps_its_own_copy()
    print("device code table checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
