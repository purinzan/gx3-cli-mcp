from __future__ import annotations

"""One definition of how a device is spelled.

GX Works3 numbers some device types in hexadecimal (X, Y, B, W and friends) and
the rest in decimal. Every place that prints a device name to a user, or reads
one from a user, has to agree on that, or the tool reports devices that do not
exist in the customer's project. This module is that agreement; nothing else
should build a device name with an f-string.
"""

import re

# Every MELSEC device type, with the base its number is written in. One table,
# so the hex set and the parser's type list can never drift apart.
#
# The list has to be complete for parsing to work: `XA` can only be split into
# X + hex A by knowing that `XA` is not itself a type, and `ST10` is a retentive
# timer rather than step relay S followed by "T10" only because ST is listed.
DEVICE_TYPE_BASE: dict[str, int] = {
    # --- bit devices -------------------------------------------------------
    "X": 16,    # input
    "Y": 16,    # output
    "M": 10,    # internal relay
    "L": 10,    # latch relay
    "B": 16,    # link relay
    "F": 10,    # annunciator
    "V": 10,    # edge relay
    "S": 10,    # step relay
    "SB": 16,   # link special relay
    "SM": 10,   # special relay
    "DX": 16,   # direct input
    "DY": 16,   # direct output
    "FX": 16,   # function input  (SH-081224 22.1: 16 points, hexadecimal)
    "FY": 16,   # function output (SH-081224 22.1: 16 points, hexadecimal)
    # --- word devices ------------------------------------------------------
    "D": 10,    # data register
    "W": 16,    # link register
    "SW": 16,   # link special register
    "SD": 10,   # special register
    "R": 10,    # file register
    "ZR": 10,   # extended file register
    "Z": 10,    # index register
    "LZ": 10,   # long index register
    "RD": 10,   # refresh data register
    "FD": 10,   # function register (SH-081224 22.1: 5 points x 4 words)
    # --- timers and counters ----------------------------------------------
    "T": 10,    # timer
    "ST": 10,   # retentive timer
    "C": 10,    # counter
    "LT": 10,   # long timer
    "LST": 10,  # long retentive timer
    "LC": 10,   # long counter
    # contact / coil / current-value forms of the above
    "TS": 10, "TC": 10, "TN": 10,
    "STS": 10, "STC": 10, "STN": 10,
    "CS": 10, "CC": 10, "CN": 10,
    "LTS": 10, "LTC": 10, "LTN": 10,
    "LSTS": 10, "LSTC": 10, "LSTN": 10,
    "LCS": 10, "LCC": 10, "LCN": 10,
}

# Devices that can behave as ladder bit operands in contacts/coils. Timer and
# counter current-value forms (TN/CN/...) remain word-like and are intentionally
# excluded.
BIT_DEVICE_TYPES = frozenset(
    {
        "X", "Y", "DX", "DY", "FX", "FY",
        "M", "L", "B", "SM", "SB", "F", "V", "S",
        "T", "ST", "C", "LT", "LST", "LC",
        "TS", "TC", "STS", "STC", "CS", "CC",
        "LTS", "LTC", "LSTS", "LSTC", "LCS", "LCC",
    }
)

# Device types GX Works3 numbers in hexadecimal.
HEX_DEVICE_TYPES = frozenset(name for name, base in DEVICE_TYPE_BASE.items() if base == 16)

# Longest first, so a type that is a prefix of another never wins by accident.
_TYPES_LONGEST_FIRST = tuple(sorted(DEVICE_TYPE_BASE, key=len, reverse=True))

_DEVICE_RE = re.compile(r"^([A-Za-z]+)([0-9A-Fa-f]+)$")


def hex_number(number: int) -> str:
    """Format a hexadecimal device number the way GX Works3 writes it."""
    return f"{number:X}"


def device_radix(dev_type: str) -> int:
    """The base a device type's number is written in: 16 or 10.

    For callers that have to keep a caller-specific format (zero padding, an
    end-of-range calculation) and so cannot use format_device directly. They
    still must not carry their own copy of the hex set.
    """
    return DEVICE_TYPE_BASE.get(dev_type.upper(), 10)


def format_device(dev_type: str, number: int) -> str:
    """Spell a device the way it appears in GX Works3."""
    dev_type = dev_type.upper()
    if dev_type in HEX_DEVICE_TYPES:
        return f"{dev_type}{hex_number(number)}"
    return f"{dev_type}{number}"


def _read_number(dev_type: str, digits: str) -> int | None:
    if DEVICE_TYPE_BASE.get(dev_type, 10) == 16:
        try:
            return int(digits, 16)
        except ValueError:
            return None
    if not digits.isdigit():
        return None
    return int(digits)


def split_device(text: str) -> tuple[str, int] | None:
    """Parse a device as a user would type it. None if it is not a device.

    The number is read in the base its type uses, so `X1A` is 26 and `D1A` is
    not a device at all. Both `XA` and the older zero-padded `X0A` are accepted;
    format_device writes `XA`.
    """
    value = text.strip().upper()
    if not value:
        return None
    for dev_type in _TYPES_LONGEST_FIRST:
        if not value.startswith(dev_type):
            continue
        digits = value[len(dev_type):]
        if not digits:
            continue
        number = _read_number(dev_type, digits)
        if number is not None:
            return dev_type, number
    # Fall back to a permissive split for anything this table does not list,
    # rather than rejecting a device the tool used to accept.
    match = _DEVICE_RE.fullmatch(value)
    if not match:
        return None
    dev_type, digits = match.group(1), match.group(2)
    number = _read_number(dev_type, digits)
    return None if number is None else (dev_type, number)


def parse_device_name(text: str) -> tuple[str, int]:
    """split_device, raising instead of returning None."""
    parsed = split_device(text)
    if parsed is None:
        raise ValueError(f"invalid device: {text}")
    return parsed


def canonical_device(text: str) -> str:
    """Rewrite a user-typed device into its canonical spelling.

    `x1a`, `X1A` and `X01A` all name the same device and all come back as `X1A`.
    `X0A` comes back as `XA`, which is how GX Works3 writes it.
    """
    dev_type, number = parse_device_name(text)
    return format_device(dev_type, number)
