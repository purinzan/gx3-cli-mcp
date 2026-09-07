from __future__ import annotations

"""Shared operand and instruction spelling for diagrams and logic trees."""
import re
from gx3cli.gx3_operand_parse import parse_operands
from gx3cli.gx3_device_name import format_device
from gx3cli.gx3_label_resolve import LabelResolver


def display_operands(
    raw_args: list[str], arg_tokens: list[str], labels: LabelResolver | None = None
) -> list[str]:
    """Decode every argument into its display text, in instruction order.

    The walk over the header tokens lives in gx3_operand_parse, shared with the
    cross-reference; this spells the result the way GX Works3 prints it, with
    the modifier folded into the name (K4M100, D100.5, D100Z2).
    """
    out: list[str] = []
    for operand in parse_operands(raw_args, arg_tokens):
        if operand.kind == "label":
            ref = labels.resolve_token(operand.label_token) if labels is not None and operand.label_token else None
            out.append(ref.name if ref is not None else "?")
            continue

        if operand.kind == "const":
            token = operand.const_token
            value = operand.const_value if token == "String" else operand.const_value or "?"
            if operand.raw.startswith("M{"):
                # A constant base with an index register: K2400Z2.
                index = f"Z{operand.index_reg}" if operand.index_reg else ""
                out.append(f"K{value}{index}")
                continue
            prefix = token.split("_", 1)[0] if token and token[0] in "KHE" else "K"
            if token == "String":
                out.append(f'"{value}"')
            elif prefix == "H":
                try:
                    out.append(f"H{int(value):X}")
                except ValueError:
                    out.append(f"H{value}")
            else:
                out.append(f"{prefix}{value}")
            continue

        if operand.kind == "pointer":
            out.append(f"#P{operand.number}")
            continue

        if operand.kind == "buffer":
            if operand.bit:
                modifier = f".{int(operand.bit):X}"
            elif operand.index_reg:
                modifier = f"Z{operand.index_reg}"
            else:
                modifier = ""
            out.append(f"U{operand.unit:X}\\G{operand.number}{modifier}")
            continue

        if operand.kind != "device" or operand.number is None:
            out.append("?")
            continue

        number = int(operand.number)
        dev_text = format_device(operand.device_type, number) if operand.device_type else f"?{number}"
        if operand.indirect:
            dev_text = "@" + dev_text
        if operand.index_reg:
            out.append(f"{dev_text}{operand.index_prefix}{operand.index_reg}")
        elif operand.digit:
            out.append(f"K{operand.digit}{dev_text}")
        elif operand.bit:
            out.append(f"{dev_text}.{int(operand.bit):X}")
        else:
            out.append(dev_text)

    return out


OPCODE_SUFFIX_RE = re.compile(r"__(\d+)$")


def display_opcode(op: str) -> str:
    m = OPCODE_SUFFIX_RE.search(op)
    if not m:
        return op
    base = op[: m.start()]
    if m.group(1) == "32":
        return f"D{base}"
    return base



def instruction_opcode(role: str, is_32bit: bool = False, is_string: bool = False) -> str:
    base = display_opcode(role)
    if is_string and base in {"=", "<>", "<", ">", "<=", ">="}:
        return "$" + base
    if is_32bit and base and not base[0].isalpha() and base[0] != "$":
        return "D" + base
    return base
