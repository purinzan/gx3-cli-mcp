from __future__ import annotations

"""One reading of a ladder operation's operands, for everything that needs it.

The intermediate format states an operation's operands twice. The header lists
what kind each one is -- a device type, a constant, the "Us G" of buffer
memory, and the modifier tokens "Zs" (index register), "Ks" (digit
specification) and "Dots" (bit of a word). The element then carries the values:
d{a=N} for a device, c{v=N} for a constant, B{} for buffer memory and M{} for a
modified operand. Reading an operation means walking the two in step, because
only the header says which kind the next value is, and a modifier spends a
header token whether or not it spends a value.

That walk was written twice: once to print a rung, once to record its
occurrences. A token consumed on one side and not the other would be a bug in
one output only -- but it was the same bug in both, and each copy had to be
found and fixed separately. What the callers need from the operands genuinely
differs (the printed rung wants one string, "D100Z2"; the cross-reference wants
two occurrences, D100 and Z2, each with its own access), so what is shared here
is the walk, and each caller builds its own answer from the result.
"""

import re
from dataclasses import dataclass

from gx3cli.extract_gx3_extended_instruction_knowledge import DEVICE_TYPES, LABEL_TOKEN_PREFIX


TYPE_TOKEN_ALIASES = {"Us": "U", "Zs": "Z"}
SKIP_ARG_TOKENS = {"Ks", "Dots", "Digits"}

INNER_DEV_RE = re.compile(r"d\{[^{}]*?a=(-?\d+)[^{}]*?\}")
CONST_VALUE_RE = re.compile(r"v=([^:}]+)")
M_CONST_BASE_RE = re.compile(r"b=c\{[^}]*v=(-?\d+)")
M_INDEX_DEV_RE = re.compile(r"m=d\{[^}]*a=(-?\d+)")
M_CONST_MOD_RE = re.compile(r"m=c\{[^}]*v=(-?\d+)")


@dataclass
class Operand:
    """One operand of one operation, as the two halves of the row describe it.

    ``kind`` says which of the remaining fields carry the answer:

    - ``device``   device_type + number, with an optional modifier: index_reg
                   (D100Z2), digit (K4M100) or bit (D100.5)
    - ``buffer``   unit + number (the offset), with the same modifiers
    - ``const``    const_token ("K_1", "H_1", "String") + const_value, with
                   index_reg set for the K2400Z2 offset form
    - ``label``    label_token, the "_lid/..." reference
    - ``unknown``  the header and the element did not agree; nothing decoded
    """

    kind: str
    arg_index: int
    raw: str = ""
    device_type: str = ""
    number: int | None = None
    unit: int | None = None
    index_reg: str = ""
    digit: str = ""
    bit: str = ""
    const_token: str = ""
    const_value: str = ""
    label_token: str = ""
    extra_numbers: tuple[int, ...] = ()


def parse_operands(
    raw_args: list[str], arg_tokens: list[str], allow_pointer: bool = False
) -> list[Operand]:
    """Walk the element's values against the header's type tokens.

    ``allow_pointer`` accepts the "P" type, which a printed rung spells as a
    pointer (#P100) and which the cross-reference has no device for.
    """
    ti = 0
    n_tokens = len(arg_tokens)
    extra_types = ("U", "P") if allow_pointer else ("U",)

    def peek() -> str:
        return arg_tokens[ti] if ti < n_tokens else ""

    def advance() -> str:
        nonlocal ti
        tok = peek()
        if tok:
            ti += 1
        return tok

    def skip_to_meaningful() -> None:
        nonlocal ti
        while ti < n_tokens:
            tok = arg_tokens[ti]
            if (
                tok in DEVICE_TYPES
                or tok in TYPE_TOKEN_ALIASES
                or tok in SKIP_ARG_TOKENS
                or tok == "G"
                or tok == "String"
                or (allow_pointer and tok == "P")
                or re.match(r"^[KHE]_", tok)
            ):
                return
            ti += 1

    def take_type() -> str:
        skip_to_meaningful()
        while ti < n_tokens:
            tok = advance()
            if tok in SKIP_ARG_TOKENS:
                continue
            alias = TYPE_TOKEN_ALIASES.get(tok, tok)
            if alias in DEVICE_TYPES or alias in extra_types:
                return alias
            if re.match(r"^[KHE]_", tok) or tok == "String":
                skip_to_meaningful()
                continue
            return ""
        return ""

    def take_if(*names: str) -> str:
        skip_to_meaningful()
        if peek() in names:
            return advance()
        return ""

    def take_if_const() -> str:
        skip_to_meaningful()
        tok = peek()
        if re.match(r"^[KHE]_", tok) or tok == "String":
            return advance()
        return ""

    operands: list[Operand] = []
    for arg_index, arg in enumerate(raw_args):
        if arg.startswith("l{"):
            # A label reference. The arg is a placeholder; the identity is the
            # "_lid/<LabelID>/<row>" header token.
            token = next((t for t in arg_tokens[ti:] if t.startswith(LABEL_TOKEN_PREFIX)), "")
            if token:
                ti = arg_tokens.index(token, ti) + 1
            operands.append(Operand("label", arg_index, raw=arg, label_token=token))
            continue

        if arg.startswith("c{"):
            token = take_if_const()
            value = CONST_VALUE_RE.search(arg)
            operands.append(
                Operand(
                    "const",
                    arg_index,
                    raw=arg,
                    const_token=token,
                    const_value=value.group(1) if value else "",
                )
            )
            continue

        if arg.startswith("d{"):
            dev_type = take_type()
            m = INNER_DEV_RE.search(arg)
            operands.append(
                Operand(
                    "device" if m else "unknown",
                    arg_index,
                    raw=arg,
                    device_type=dev_type,
                    number=int(m.group(1)) if m else None,
                )
            )
            continue

        if arg.startswith("B{"):
            inner = [int(v) for v in INNER_DEV_RE.findall(arg)]
            dev_type = take_type()
            if dev_type == "U" and len(inner) >= 2:
                take_if("G")
                operands.append(
                    Operand(
                        "buffer",
                        arg_index,
                        raw=arg,
                        unit=inner[0],
                        number=inner[1],
                        extra_numbers=tuple(inner[2:]),
                    )
                )
            elif inner:
                # The range/indexed form of an ordinary device.
                operands.append(
                    Operand(
                        "device",
                        arg_index,
                        raw=arg,
                        device_type=dev_type,
                        number=inner[0],
                        extra_numbers=tuple(inner[1:]),
                    )
                )
            else:
                operands.append(Operand("unknown", arg_index, raw=arg))
            continue

        if arg.startswith("M{"):
            const_base = M_CONST_BASE_RE.search(arg)
            index_dev = M_INDEX_DEV_RE.search(arg)
            const_mod = M_CONST_MOD_RE.search(arg)
            inner = [int(v) for v in INNER_DEV_RE.findall(arg)] if "B{" in arg else []

            if inner:
                dev_type = take_type()
                if dev_type == "U" and len(inner) >= 2:
                    take_if("G")
                    # Buffer memory takes either a bit position ("Dots") or an
                    # index register ("Zs"); the index form spends a token too.
                    index_reg = ""
                    if const_mod is None and index_dev is not None:
                        if take_if("Zs", "Z"):
                            index_reg = index_dev.group(1)
                    else:
                        take_if("Dots")
                    operands.append(
                        Operand(
                            "buffer",
                            arg_index,
                            raw=arg,
                            unit=inner[0],
                            number=inner[1],
                            index_reg=index_reg,
                            bit=const_mod.group(1) if const_mod else "",
                        )
                    )
                    continue
                operands.append(Operand("unknown", arg_index, raw=arg))
                continue

            if const_base:
                # A constant base with an index register: K2400Z2, the offset
                # form FROM/TO instructions use.
                token = take_if_const()
                take_if("Zs", "Z")
                operands.append(
                    Operand(
                        "const",
                        arg_index,
                        raw=arg,
                        const_token=token,
                        const_value=const_base.group(1),
                        index_reg=index_dev.group(1) if index_dev else "",
                    )
                )
                continue

            dev_type = take_type()
            m = INNER_DEV_RE.search(arg)
            if not m:
                operands.append(Operand("unknown", arg_index, raw=arg))
                continue
            number = int(m.group(1))
            operand = Operand("device", arg_index, raw=arg, device_type=dev_type, number=number)
            if index_dev and index_dev.group(1) != m.group(1):
                take_if("Zs", "Z")
                operand.index_reg = index_dev.group(1)
            elif const_mod:
                token = take_if("Ks", "Dots")
                if token == "Ks":
                    operand.digit = const_mod.group(1)
                elif token == "Dots":
                    operand.bit = const_mod.group(1)
            else:
                take_if("Ks", "Dots", "Zs")
            operands.append(operand)
            continue

        operands.append(Operand("unknown", arg_index, raw=arg))

    return operands
