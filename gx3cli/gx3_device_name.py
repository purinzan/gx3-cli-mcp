from __future__ import annotations

"""One definition of how a device is spelled.

GX Works3 numbers some device types in hexadecimal (X, Y, B, W and friends) and
the rest in decimal. Every place that prints a device name to a user, or reads
one from a user, has to agree on that, or the tool reports devices that do not
exist in the customer's project. This module is that agreement; nothing else
should build a device name with an f-string.
"""

import re

# Device types GX Works3 numbers in hexadecimal. D, R, ZR, M, T, C and the rest
# are decimal.
HEX_DEVICE_TYPES = {"X", "Y", "B", "W", "SB", "SW", "DX", "DY"}

_DEVICE_RE = re.compile(r"^([A-Za-z]+)([0-9A-Fa-f]+)$")


def hex_number(number: int) -> str:
    """Format a hexadecimal device number the way GX Works3 writes it."""
    text = f"{number:X}"
    # A leading letter would read as part of the device type, so GX pads it.
    return f"0{text}" if text[0] in "ABCDEF" else text


def format_device(dev_type: str, number: int) -> str:
    """Spell a device the way it appears in GX Works3."""
    dev_type = dev_type.upper()
    if dev_type in HEX_DEVICE_TYPES:
        return f"{dev_type}{hex_number(number)}"
    return f"{dev_type}{number}"


def split_device(text: str) -> tuple[str, int] | None:
    """Parse a device as a user would type it. None if it is not a device.

    The number is read in the base that type uses, so `X1A` is 26 and `D1A` is
    not a device at all.
    """
    match = _DEVICE_RE.fullmatch(text.strip())
    if not match:
        return None
    dev_type = match.group(1).upper()
    digits = match.group(2)
    if dev_type in HEX_DEVICE_TYPES:
        return dev_type, int(digits, 16)
    if not digits.isdigit():
        return None
    return dev_type, int(digits)


def parse_device_name(text: str) -> tuple[str, int]:
    """split_device, raising instead of returning None."""
    parsed = split_device(text)
    if parsed is None:
        raise ValueError(f"invalid device: {text}")
    return parsed


def canonical_device(text: str) -> str:
    """Rewrite a user-typed device into its canonical spelling.

    `x1a`, `X1A` and `X01A` all name the same device and all come back as `X1A`.
    """
    dev_type, number = parse_device_name(text)
    return format_device(dev_type, number)
