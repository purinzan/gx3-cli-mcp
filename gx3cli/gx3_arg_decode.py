from __future__ import annotations

"""Shared full-argument decoder for ladder intermediate operations.

Decodes every argument of every operation (not just the first device),
including:
- plain devices              d{a=N}            e.g. MOV source and destination
- buffer memory access       B{b=..:e=..} + header ``Us:G``  ->  U70\\G123
- digit-specified bits       M{b=..:m=c{v=k}} + header ``M:Ks`` -> K2M35001
- bit-of-word / indexed      M{..} + header ``Dots``/``Zs``
- constant base + index      M{b=c{v=2400}:m=d{a=2}} -> K2400Z2 (FROM/TO offsets)

Each occurrence is classified as read / write / both / ref (unknown opcode).

Used by gx3_xref.py (cross-reference DB) and review_gx3_project.py (row
occurrence parsing for the lite index and static reviews).
"""

import re
from dataclasses import dataclass

from gx3cli.gx3_device_name import format_device as _format_device
from gx3cli.extract_gx3_extended_instruction_knowledge import (
    DEVICE_TYPES,
    extract_args_text,
    extract_elements,
    header_tokens,
    top_level_items,
)
from gx3cli.gx3_intermediate_tool import parse_header_ops
from gx3cli.gx3_operand_parse import CONST_VALUE_RE, M_CONST_MOD_RE, parse_operands

from gx3cli.extract_gx3_extended_instruction_knowledge import LABEL_DEVICE_TYPE, LABEL_TOKEN_PREFIX
from gx3cli.gx3_instruction_table import MANUAL_WRITE_ARGS, manual_operand_names, manual_write_indices
from gx3cli.gx3_label_resolve import LabelResolver, split_label_token



CONTACT_ROLES = {"a", "b", "EG"}
COIL_ROLES = {"c"}

WRITE_ARG_TABLE: dict[str, object] = {
    "SET": {0}, "RST": {0}, "PLS": {0}, "PLF": {0}, "FF": {0}, "DELTA": {0},
    "BSET": {0}, "BRST": {0},
    "OUT__16": {0}, "OUTH__16": {0}, "OUT": {0}, "RST__16": {0},
    "MC": {1}, "MCR": set(), "BKRST": {0, 1}, "ZRST": {0, 1},
    "MOV": "last", "DMOV": "last", "$MOV": "last", "EMOV": "last", "EDMOV": "last",
    "CML": "last", "DCML": "last", "CMLP": "last",
    "BMOV": {1}, "BMOVL": {1}, "FMOV": {1}, "DFMOV": {1}, "BLKMOVB": {1}, "FMOVL": {1}, "DFMOVL": {1},
    "BKBCD": {1},
    "XCH": {0, 1}, "DXCH": {0, 1}, "BXCH": {0, 1}, "SWAP": {0},
    "NEG": {0}, "DNEG": {0}, "ENEG": {0},
    "INC": {0}, "DEC": {0}, "DINC": {0}, "DINC_U": {0}, "DINCP_U": {0}, "DDEC": {0},
    "BCD": "last", "BIN": "last", "DBCD": "last", "DBIN": "last", "BCDDA": "last",
    "FLT": "last", "INT": "last", "DINT": "last", "FLT2INT": "last",
    "FLT2DINT": "last", "INT2FLT": "last", "DINT2FLT": "last", "INT2DINT": "last", "DINT2INT": "last", "UDINT2UINT": "last",
    "DATE2SEC": "last", "DATE2SEC_U": "last", "SEC2DATE": "last", "SEC2DATE_U": "last", "SEC2DATEP_U": "last", "TIME2SEC": "last", "SEC2TIME": "last",
    "DABIN": "last", "DDABIN": "last", "BINHA": "last", "GBIN": "last",
    "DFLT": "last", "DBL": "last", "WORD": "last",
    "ROR": {0}, "ROL": {0}, "RCR": {0}, "RCL": {0},
    "DROR": {0}, "DROL": {0}, "DRCR": {0}, "DRCL": {0},
    "SFR": {0}, "SFL": {0}, "BSFR": {0}, "BSFL": {0}, "DSFR": {0}, "DSFL": {0},
    "CMP": {2}, "DCMP": {2}, "ECMP": {2}, "ZCP": {3}, "DZCP": {3}, "DTEST": "last",
    "SEG": {1}, "DECO": {1}, "ENCO": {1}, "DIS": {1}, "UNI": {1},
    "SUM": "last", "DSUM": "last", "WSUM": "last", "DWSUM": "last", "WSUM_U": "last", "MEAN": "last",
    "MAX": "last", "MIN": "last", "SORT": {0}, "DLIMIT": "last", "POW": "last",
    "BKAND": "last", "DX": "last",
    "WTOB": "last", "BTOW": "last",
    "SFTBL": {0}, "SFTBR": {0}, "SFTBRP": {0},
    "FROM": {2}, "DFRO": {2}, "DFROM": {2}, "TO": set(), "DTO": set(),
    "CJ": set(), "CALL": set(), "ECALL": set(), "BREAK": set(), "ME": set(), "EI": set(), "LEDR": set(), "NOPLF": set(), "RET": set(), "FEND": set(), "GOEND": set(),
    # No device operands at all, so nothing to classify -- listed so the
    # coverage report does not call them unknown. The manuals give them no
    # operand table and no ST form ("対応していません"), which is why they are
    # here rather than in the generated table.
    "ANB": set(), "ORB": set(), "END": set(), "NOP": set(), "IRET": set(),
    # PHASEEND is the one instruction in its section with no "内容，範囲，デー
    # タ型" block, where PHASE and PHASECHG beside it have one.
    "PHASEEND": set(),
    "FOR": set(), "NEXT": set(), "INV": set(),
    # Intelligent function module random read/write.
    # Last device is the completion/status area; GP.RIRD also writes read data.
    "GP.RIWT": {3}, "GP.RIRD": {2, 3},
    "ZP.REMTO": {7}, "ZP.REMFR": {5, 7},
    "ANS": set(), "ANR": set(),
    "SCL": "last", "SCL2": "last", "LIMIT": "last",
    "ADRSET": "last", "BINDA": "last", "DBINDA": "last", "DBINDAP": "last",
    "DATERD": "last", "DATEWR": set(),
    "INSTR": "last", "INSTRP": "last", "MIDR": "last", "MIDRP": "last", "MIDW": "last",
    "LEN": "last", "STRDEL": "last",
    "FIFW": "last", "FDEL": {1}, "SERDATA": "last",
    "DVAL": "last", "DVALP": "last",
    "G.INPUT": "last", "G.OUTPUT": {0}, "GP.ERRCLEAR": set(), "ZP.CSET": {1},
}
ARITH_OPS = {"+", "-", "*", "/", "B+", "B-", "B*", "B/", "BK+", "BK-", "BK*", "BK/", "D+", "D-", "D*", "D/",
             "E+", "E-", "E*", "E/", "$+", "WAND", "WOR", "WXOR", "WXNR",
             "DAND", "DOR", "DXOR", "DXNR"}
# Contact comparisons: they produce a result on the rung and write nothing.
# BKCMP/DBKCMP used to be matched here and must not be -- a block compare
# stores its result into (d) ("比較演算結果を格納する先頭デバイス"), so treating
# it as a pure comparison hid every device a BKCMP writes. Those are in the
# manual table instead. The type infixes are the signed/unsigned (_U), double
# word (D), real (E), string ($) and date/time (DT/TM/ED) variants.
COMPARE_RE = re.compile(
    # The LD/AND/OR prefix is optional: the intermediate format writes a bare
    # "<" or "=" for the comparison contact, and KNOWN_OPS lists those.
    r"^(?:LD|AND|OR)?(?:D|E|\$|DT|TM|ED)?(?:=|<>|<=|>=|<|>)(?:_U)?$"
)

# The token tables and the operand regexes now live with the walk that uses
# them, in gx3_operand_parse.


@dataclass
class ArgOcc:
    device: str
    device_type: str
    number: int
    access: str
    arg_index: int
    detail: str = ""
    access_basis: str = ""
    # An index register named as a modifier (the Z2 of D100Z2). The
    # instruction reads it to work out an address; it never writes it, whatever
    # it does to the operand the modifier belongs to.
    is_index_register: bool = False
    # How many devices this operand covers. A block instruction names a count
    # operand, so BMOV ... D64061 K4 writes D64061 through D64064 while naming
    # only the first. 1 is an ordinary single device; 0 means the instruction
    # covers a run whose length is held in a device and cannot be known here.
    range_len: int = 1


@dataclass
class DecodedOperation:
    """One ladder operation decoded from a row.

    This is the shared operation-level view for callers that need more than
    just device occurrences: raw argument count, constant operands by position,
    and the original raw argument strings are kept with the classified
    occurrences.
    """

    role: str
    opcode: str
    args: list[ArgOcc]
    const_summary: str
    raw_args: list[str]
    arg_tokens: list[str]
    op_index: int

    @property
    def argc(self) -> int:
        return len(self.raw_args)

    @property
    def constant_values(self) -> dict[int, str]:
        return constant_values_by_index(self.raw_args)


def base_opcode(opcode: str) -> str:
    if opcode in WRITE_ARG_TABLE or opcode in ARITH_OPS:
        return opcode
    if opcode.endswith("P") and opcode[:-1] in WRITE_ARG_TABLE:
        return opcode[:-1]
    if opcode.endswith("P") and opcode[:-1] in ARITH_OPS:
        return opcode[:-1]
    return opcode


def write_indices(opcode: str, argc: int) -> tuple[set[int] | None, bool]:
    """Return (write index set, is_read_modify_write_dest). None => unknown."""
    op = base_opcode(opcode)
    if COMPARE_RE.match(op):
        return set(), False
    if op in ARITH_OPS:
        # Kept ahead of the manual table for the read-modify-write flag: the
        # two-operand form of "+" both reads and writes its destination, which
        # the operand table does not express.
        if argc <= 2:
            return {argc - 1}, True
        return {argc - 1}, False
    # The manuals name each operand, so they pin the destination down exactly.
    # Preferred over the table below, which was written by hand and put the
    # destination on the wrong operand for WTOB, BTOW, MIDR, MIDW, INSTR,
    # STRDEL, SERDATA, BKAND, BKRST, BREAK, G.INPUT and ZP.CSET -- for most of
    # those it named the count operand as the one being written.
    manual = manual_write_indices(opcode, argc)
    if manual is None and opcode != op:
        manual = manual_write_indices(op, argc)
    if manual is not None:
        return manual, False
    spec = WRITE_ARG_TABLE.get(op)
    if spec is None:
        return None, False
    if spec == "last":
        return {argc - 1}, False
    return set(spec), False  # type: ignore[arg-type]


def write_index_basis(opcode: str, argc: int) -> str:
    """Explain the source used to classify an instruction's write operands."""
    op = base_opcode(opcode)
    if COMPARE_RE.match(op):
        return "compare regex"
    if op in ARITH_OPS:
        return "arithmetic read/modify/write rule" if argc <= 2 else "arithmetic last-operand rule"
    if manual_write_indices(opcode, argc) is not None:
        return "manual operand table"
    if opcode != op and manual_write_indices(op, argc) is not None:
        return "manual operand table via base opcode"
    if op in WRITE_ARG_TABLE:
        return "legacy write-arg table"
    return "unknown"


def has_manual_write_schema(opcode: str) -> bool:
    """True when the generated manual operand table carries this opcode."""
    op = base_opcode(opcode)
    return opcode in MANUAL_WRITE_ARGS or op in MANUAL_WRITE_ARGS


def parse_row_occurrences(
    data: str, labels: LabelResolver | None = None
) -> tuple[list[tuple[str, str, list[ArgOcc], str]], str]:
    """Return ([(role, opcode, args, const_summary)], parse_status).

    role is a/b/c for contacts/coils, otherwise the opcode.

    Pass ``labels`` to give label references their names. Without it a label
    contact still parses, but arrives with no identity -- which is what left
    the cross-reference empty on label-based projects.
    """
    operations, status = parse_row_operations(data, labels)
    return [(op.role, op.opcode, op.args, op.const_summary) for op in operations], status


def parse_row_operations(data: str, labels: LabelResolver | None = None) -> tuple[list[DecodedOperation], str]:
    """Return decoded operations with raw argument metadata.

    This is the canonical row walk. Tools that need argument counts, constants,
    or device occurrences should use this instead of re-pairing header tokens
    and ``ce`` elements themselves.
    """

    tokens = header_tokens(data)
    header_ops = parse_header_ops(data)
    ce_elements = [e for e in extract_elements(data) if "s=ce{" in e]
    status = "exact" if len(ce_elements) == len(header_ops) else "partial"
    results: list[DecodedOperation] = []

    for op_index, hop in enumerate(header_ops):
        element = ce_elements[op_index] if op_index < len(ce_elements) else ""
        args_text = extract_args_text(element)
        raw_args = top_level_arg_items(args_text) if args_text else []
        next_op_token = header_ops[op_index + 1].token_index if op_index + 1 < len(header_ops) else len(tokens)
        arg_tokens = tokens[hop.token_index + 1 : next_op_token]

        if hop.op in CONTACT_ROLES or hop.op in COIL_ROLES:
            occ = decode_args(raw_args, arg_tokens or [hop.device_type], hop.op, labels)
            role = hop.op
            access = "read" if hop.op in CONTACT_ROLES else "write"
            for a in occ:
                a.access = "read" if a.is_index_register else access
                a.access_basis = "index register" if a.is_index_register else "ladder contact/coil"
            results.append(
                DecodedOperation(
                    role=role,
                    opcode="",
                    args=occ,
                    const_summary=const_summary(raw_args),
                    raw_args=raw_args,
                    arg_tokens=arg_tokens or [hop.device_type],
                    op_index=op_index,
                )
            )
            continue

        occ = decode_args(raw_args, arg_tokens, hop.op, labels)
        wset, rmw = write_indices(hop.op, len(raw_args))
        basis = write_index_basis(hop.op, len(raw_args))
        # Only the destination is given a span. Whether a source covers the
        # same run differs by instruction -- BMOV reads (n) words, FMOV repeats
        # one -- and the operand tables do not say which, so claiming a read
        # range here would be a guess. Missing a read is a smaller wrong answer
        # than inventing one.
        span, span_basis = block_span(hop.op, raw_args)
        for a in occ:
            if a.is_index_register:
                # It shares its arg_index with the operand it modifies, so the
                # write set would otherwise report the destination's Z as
                # written -- a device the instruction only ever reads.
                a.access = "read"
                a.access_basis = "index register"
                continue
            if wset is None:
                a.access = "ref"
            elif a.arg_index in wset:
                a.access = "both" if rmw else "write"
                if span != 1:
                    a.range_len = span
                    a.detail = (a.detail + "; " if a.detail else "") + (
                        f"covers {span} devices" if span else "covers a run of unknown length"
                    )
            else:
                a.access = "read"
            a.access_basis = basis
        results.append(
            DecodedOperation(
                role=hop.op,
                opcode=hop.op,
                args=occ,
                const_summary=const_summary(raw_args),
                raw_args=raw_args,
                arg_tokens=arg_tokens,
                op_index=op_index,
            )
        )
    return results, status


def top_level_arg_items(text: str) -> list[str]:
    """Split a GX3 argument list on either top-level ':' or ',' separators."""

    items = []
    start = 0
    brace = bracket = 0
    for index, char in enumerate(text):
        if char == "{":
            brace += 1
        elif char == "}":
            brace -= 1
        elif char == "[":
            bracket += 1
        elif char == "]":
            bracket -= 1
        elif char in {":", ","} and brace == 0 and bracket == 0:
            items.append(text[start:index])
            start = index + 1
    items.append(text[start:])
    return [item for item in items if item]


def block_span(opcode: str, raw_args: list[str]) -> tuple[int, str]:
    """How many devices a block instruction's destination covers.

    The manuals name a count operand "(n)" on the instructions that work on a
    run of devices: BMOV, FMOV, BKRST, BK+ and the rest. The ladder names only
    the first device of the run, so a cross-reference built from the operands
    alone records D64061 for a "BMOV .. D64061 K4" and nothing for the D64062,
    D64063 and D64064 it also writes. Searching for one of those answered "no
    occurrences", which reads as "nothing writes this device".

    Returns (length, basis): 1 for an ordinary single device, and 0 for a run
    whose count is held in a device, so its end is not knowable statically.
    """
    argc = len(raw_args)
    names = manual_operand_names(opcode, argc)
    if names is None:
        base = base_opcode(opcode)
        if base != opcode:
            names = manual_operand_names(base, argc)
    if names is None or "(n)" not in names:
        return 1, ""

    count_arg = raw_args[names.index("(n)")]
    if not count_arg.startswith("c{"):
        # The count is a device: the run is as long as that device says at
        # runtime, which no static reading can pin down.
        return 0, "manual count operand (n), length in a device"
    match = CONST_VALUE_RE.search(count_arg)
    try:
        length = int(match.group(1)) if match else 0
    except ValueError:
        length = 0
    if length < 1:
        return 0, "manual count operand (n), length unreadable"
    return length, "manual count operand (n)"


def const_summary(raw_args: list[str]) -> str:
    values = []
    for arg in raw_args:
        if arg.startswith("c{"):
            m = CONST_VALUE_RE.search(arg)
            if m:
                values.append(m.group(1))
    return ",".join(values[:6])


def constant_values_by_index(raw_args: list[str]) -> dict[int, str]:
    return {
        arg_index: match.group(1)
        for arg_index, raw in enumerate(raw_args)
        if raw.startswith("c{")
        for match in [CONST_VALUE_RE.search(raw)]
        if match is not None
    }


def decode_args(
    raw_args: list[str], arg_tokens: list[str], role: str, labels: LabelResolver | None = None
) -> list[ArgOcc]:
    """Turn one operation's operands into the occurrences it contains.

    The walk over the header tokens lives in gx3_operand_parse, shared with the
    printed rung; this turns its result into occurrences. A modified device
    yields two: the device, and the index register the instruction reads to
    reach it.
    """
    occs: list[ArgOcc] = []
    for operand in parse_operands(raw_args, arg_tokens):
        arg_index = operand.arg_index

        if operand.kind == "label":
            occs.append(make_label_occ(operand.label_token, arg_index, labels))
            continue

        if operand.kind == "const":
            if operand.index_reg:
                # K2400Z2: the constant is an offset, the index register is a
                # device the instruction reads.
                detail = f"base=K{operand.const_value}+Z{operand.index_reg}"
                occs.append(
                    make_occ("Z", int(operand.index_reg), arg_index, detail=detail, index_register=True)
                )
            continue

        if operand.kind == "buffer":
            suffix = ""
            detail = f"unit=0x{operand.unit:X}"
            if operand.bit:
                suffix = f".{operand.bit}"
                detail += f" bit={operand.bit}"
            elif operand.index_reg:
                suffix = f"Z{operand.index_reg}"
                detail += f" Z{operand.index_reg} indexed"
            occs.append(
                ArgOcc(
                    device=f"U{operand.unit:X}\\G{operand.number}{suffix}",
                    device_type="UG",
                    number=int(operand.number),
                    access="",
                    arg_index=arg_index,
                    detail=detail,
                )
            )
            if operand.index_reg:
                occs.append(
                    make_occ("Z", int(operand.index_reg), arg_index, detail="index register", index_register=True)
                )
            for extra in operand.extra_numbers:
                occs.append(make_occ("Z", int(extra), arg_index, detail="index register", index_register=True))
            continue

        if operand.kind != "device" or operand.number is None:
            continue

        number = int(operand.number)
        if operand.raw.startswith("d{"):
            # A plain device: an unrecognised type here means the header and
            # the element disagreed, and a guess would be worse than a gap.
            if operand.device_type in DEVICE_TYPES:
                occs.append(make_occ(operand.device_type, number, arg_index))
            continue

        if operand.raw.startswith("B{"):
            occs.append(make_occ(operand.device_type, number, arg_index, detail="range/indexed"))
            continue

        if operand.index_reg:
            occs.append(
                make_occ(operand.device_type, number, arg_index, detail=f"Z{operand.index_reg} indexed")
            )
            occs.append(
                make_occ("Z", int(operand.index_reg), arg_index, detail="index register", index_register=True)
            )
            continue

        if operand.digit:
            occs.append(make_occ(operand.device_type, number, arg_index, detail=f"digit=K{operand.digit}"))
            continue
        if operand.bit:
            occs.append(make_occ(operand.device_type, number, arg_index, detail=f"bit=K{operand.bit}"))
            continue

        const_mod = M_CONST_MOD_RE.search(operand.raw)
        if const_mod:
            # A modifier the header named no token for. It stays reported as a
            # modification rather than being spelled into the device name.
            occs.append(make_occ(operand.device_type, number, arg_index, detail=f"mod=K{const_mod.group(1)}"))
            continue
        occs.append(make_occ(operand.device_type, number, arg_index, detail="modified"))

    return occs


def make_label_occ(token: str, arg_index: int, labels: LabelResolver | None) -> ArgOcc:
    """An occurrence of a label, named if the label table could be read.

    Unresolved it keeps the raw reference rather than being dropped, so the
    row still shows that something is there and where it came from.
    """
    parsed = split_label_token(token)
    ref = labels.resolve_token(token) if labels is not None and token else None
    if ref is not None and parsed is not None:
        return ArgOcc(
            device=ref.name,
            device_type=LABEL_DEVICE_TYPE,
            number=parsed[1],
            access="",
            arg_index=arg_index,
            detail=ref.detail,
        )
    return ArgOcc(
        device=token or "?label",
        device_type=LABEL_DEVICE_TYPE,
        number=parsed[1] if parsed else 0,
        access="",
        arg_index=arg_index,
        detail="label (unresolved)",
    )


def make_occ(
    dev_type: str, number: int, arg_index: int, detail: str = "", index_register: bool = False
) -> ArgOcc:
    dev_type = dev_type or "?"
    return ArgOcc(
        device=_format_device(dev_type, number) if dev_type != "?" else f"?{number}",
        device_type=dev_type,
        number=number,
        access="",
        arg_index=arg_index,
        detail=detail,
        is_index_register=index_register,
    )
