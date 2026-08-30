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
    # FF starts with a letter, so it is padded: WFF would read as type "WFF".
    assert format_device("W", 255) == "W0FF"


def test_decimal_types_are_left_in_decimal() -> None:
    assert format_device("M", 100) == "M100"
    assert format_device("D", 200) == "D200"
    assert format_device("SM", 400) == "SM400"
    assert format_device("T", 10) == "T10"


def test_leading_letter_is_padded() -> None:
    # XA would read as device type "XA", so GX writes X0A.
    assert hex_number(10) == "0A"
    assert format_device("X", 10) == "X0A"
    assert canonical_device("X0A") == "X0A"


def test_hex_devices_with_letters_are_accepted() -> None:
    # These were rejected outright as "invalid device" before; real projects
    # are full of them.
    assert parse_device_name("Y3D2") == ("Y", 978)
    assert parse_device_name("X1A") == ("X", 26)


def test_spelling_round_trips() -> None:
    for name in ("X24", "Y3D2", "B1F", "W0FF", "M100", "D200", "SM400", "X0A"):
        assert canonical_device(name) == name
        assert format_device(*parse_device_name(name)) == name


def test_case_and_padding_are_normalized() -> None:
    assert canonical_device("x1a") == "X1A"
    assert canonical_device("X01A") == "X1A"
    assert canonical_device("m100") == "M100"


def test_non_devices_are_rejected() -> None:
    # A-F are digits for X but not for D, so D1A is not a device.
    assert split_device("D1A") is None
    assert split_device("X") is None
    assert split_device("") is None
    assert split_device("not a device") is None


def main() -> int:
    test_hex_types_are_formatted_in_hex()
    test_decimal_types_are_left_in_decimal()
    test_leading_letter_is_padded()
    test_hex_devices_with_letters_are_accepted()
    test_spelling_round_trips()
    test_case_and_padding_are_normalized()
    test_non_devices_are_rejected()
    print("device name checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
