from __future__ import annotations

"""Every device type the tool can name has to survive the header parser.

The header parser kept its own device-type set, separate from the device naming
table. The naming table knew about the long timers and counters (LT, LST, LC),
the long index register (LZ) and the refresh data register (RD); the parser set
did not. So for a row starting "a:LT:...", the contact failed the device check,
the "a" role token was skipped, and "LT" itself matched is_op_like() -- the row
parsed as a phantom "LT" instruction carrying no devices.

The damage was that the long-timer contact disappeared from xref, and because
the header op count still matched the element count, the row was still reported
as parse_status="exact". An interlock contact that vanishes from a clean parse
reads as a correct answer, so this could not be caught by the parse-gap report.
"""

from gx3cli.extract_gx3_extended_instruction_knowledge import (
    ACCESS_TOKENS,
    DEVICE_TYPES,
)
from gx3cli.extract_hmi_build_info import DEVICE_CODE_BY_TYPE
from gx3cli.gx3_arg_decode import parse_row_occurrences
from gx3cli.gx3_device_name import BIT_DEVICE_TYPES, DEVICE_TYPE_BASE
from gx3cli.gx3_intermediate_tool import generate_rung

# One contact on the device under test, driving one M coil. This is the header
# spelling generate_rung() produces, with the contact's device type templated.
_ROW = (
    "V1:4:1:1:1:1:a:{dev}:c:M:cb{{fg=fg{{dim=2x1:es=["
    "e{{s=ce{{op=ct{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[d{{s=#:a=5:vt=nn}}]}}:pos=0,0}}:"
    "e{{s=ce{{op=cl{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[d{{s=#:a=55:vt=nn}}]}}:pos=1,0}}"
    "]}}}}"
)


def _devices(dev_type: str) -> list[tuple[str, str]]:
    ops, _status = parse_row_occurrences(_ROW.format(dev=dev_type))
    return [(occ.device, occ.access) for _role, _op, occs, _consts in ops for occ in occs]


def test_long_timers_and_counters_keep_their_contact() -> None:
    # These are the types the parser used to drop. LT5 must read as a contact,
    # not disappear behind a phantom "LT" opcode.
    for dev_type in ("LT", "LST", "LC", "LZ", "RD"):
        assert _devices(dev_type) == [(f"{dev_type}5", "read"), ("M55", "write")], dev_type


def test_function_devices_keep_their_contact() -> None:
    # FX/FY/FD are the subroutine argument devices (SH-081224 22.1).
    for dev_type in ("FX", "FY", "FD"):
        assert _devices(dev_type) == [(f"{dev_type}5", "read"), ("M55", "write")], dev_type


def test_every_named_device_type_parses_as_a_contact() -> None:
    # The regression was the two tables drifting apart, so assert against the
    # naming table itself rather than a second hand-written list.
    for dev_type in DEVICE_TYPE_BASE:
        assert _devices(dev_type) == [(f"{dev_type}5", "read"), ("M55", "write")], dev_type


def test_parser_set_stays_derived_from_the_naming_table() -> None:
    assert DEVICE_TYPES == set(DEVICE_TYPE_BASE) | ACCESS_TOKENS


def test_generated_logic_accepts_long_timer_counter_bits() -> None:
    for dev_type in ("FX", "FY", "LT", "LST", "LC", "TS", "TC", "STS", "STC", "CS", "CC", "LTS", "LTC", "LSTS", "LSTC", "LCS", "LCC"):
        data, _rowsize, _written = generate_rung({"device": f"{dev_type}5"}, {"type": "coil", "device": "M55"})
        assert f"a:{dev_type}:" in data, dev_type


def test_generated_logic_rejects_word_only_device_types() -> None:
    for dev_type in sorted(set(DEVICE_TYPE_BASE) - BIT_DEVICE_TYPES):
        try:
            generate_rung({"device": f"{dev_type}5"}, {"type": "coil", "device": "M55"})
        except ValueError as exc:
            assert "device type not supported" in str(exc), dev_type
        else:
            raise AssertionError(f"{dev_type} should not generate as a ladder contact")


def test_observed_timer_counter_comment_codes_are_registered() -> None:
    # Observed from local GX Works3 comment DBs with non-empty comments:
    # C30 "AUTO DRAIN/WATER SUPPLY START COUNTER", ST170 "SELF CALIBRATION".
    assert DEVICE_CODE_BY_TYPE["C"] == 70
    assert DEVICE_CODE_BY_TYPE["ST"] == 74


def main() -> int:
    # run_tests.py runs each file as a plain script, so the tests have to be
    # called from here. Collected rather than listed, so a test added later
    # cannot be silently left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for name, test in tests:
        test()
    print(f"{len(tests)} checks passed in {__file__.rsplit('/', 1)[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
