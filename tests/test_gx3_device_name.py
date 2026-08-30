from __future__ import annotations

"""Device spelling has to be identical everywhere it appears.

GX Works3 numbers X, Y, B and W in hexadecimal. When one command printed those
in decimal and another in hex, the tool reported devices that did not exist in
the customer's project -- and the numbers were plausible enough to read as a
wrong answer rather than a formatting bug.
"""

from gx3cli.gx3_device_name import (
    canonical_device,
    format_device,
    hex_number,
    parse_device_name,
    split_device,
)


def test_hex_types_are_formatted_in_hex() -> None:
    assert format_device("X", 36) == "X24"
    assert format_device("Y", 978) == "Y3D2"
    assert format_device("B", 31) == "B1F"
    assert format_device("W", 255) == "WFF"


def test_decimal_types_are_left_in_decimal() -> None:
    assert format_device("M", 100) == "M100"
    assert format_device("D", 200) == "D200"
    assert format_device("SM", 400) == "SM400"
    assert format_device("T", 10) == "T10"


def test_leading_letter_is_not_padded() -> None:
    # GX Works3 writes X0..XF, X10.., so the device is XA, not X0A. Splitting
    # that correctly is what the device type table is for.
    assert hex_number(10) == "A"
    assert format_device("X", 10) == "XA"
    assert canonical_device("XA") == "XA"
    # The older zero-padded spelling is still accepted on input.
    assert canonical_device("X0A") == "XA"


def test_hex_devices_with_letters_are_accepted() -> None:
    # These were rejected outright as "invalid device" before; real projects
    # are full of them.
    assert parse_device_name("Y3D2") == ("Y", 978)
    assert parse_device_name("X1A") == ("X", 26)


def test_spelling_round_trips() -> None:
    for name in ("X24", "Y3D2", "B1F", "WFF", "M100", "D200", "SM400", "XA"):
        assert canonical_device(name) == name
        assert format_device(*parse_device_name(name)) == name


def test_case_and_padding_are_normalized() -> None:
    assert canonical_device("x1a") == "X1A"
    assert canonical_device("X01A") == "X1A"
    assert canonical_device("m100") == "M100"


def test_types_that_prefix_other_types_split_correctly() -> None:
    # ST is a retentive timer, not step relay S followed by "T10"; the same
    # applies to SB/S, LST/LT/L, CN/C and RD/R.
    assert parse_device_name("ST10") == ("ST", 10)
    assert parse_device_name("S10") == ("S", 10)
    assert parse_device_name("SB1F") == ("SB", 31)
    assert parse_device_name("LST5") == ("LST", 5)
    assert parse_device_name("LT5") == ("LT", 5)
    assert parse_device_name("L5") == ("L", 5)
    assert parse_device_name("CN3") == ("CN", 3)
    assert parse_device_name("RD10") == ("RD", 10)
    assert parse_device_name("ZR100") == ("ZR", 100)


def test_hex_set_is_derived_from_the_one_table() -> None:
    from gx3cli.gx3_device_name import DEVICE_TYPE_BASE, HEX_DEVICE_TYPES

    assert HEX_DEVICE_TYPES == {n for n, b in DEVICE_TYPE_BASE.items() if b == 16}
    assert HEX_DEVICE_TYPES == {"X", "Y", "B", "W", "SB", "SW", "DX", "DY"}


def test_non_devices_are_rejected() -> None:
    # A-F are digits for X but not for D, so D1A is not a device.
    assert split_device("D1A") is None
    assert split_device("X") is None
    assert split_device("") is None
    assert split_device("not a device") is None


def main() -> int:
    test_hex_types_are_formatted_in_hex()
    test_decimal_types_are_left_in_decimal()
    test_leading_letter_is_not_padded()
    test_types_that_prefix_other_types_split_correctly()
    test_hex_set_is_derived_from_the_one_table()
    test_hex_devices_with_letters_are_accepted()
    test_spelling_round_trips()
    test_case_and_padding_are_normalized()
    test_non_devices_are_rejected()
    print("device name checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
