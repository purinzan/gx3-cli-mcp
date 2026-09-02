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

from gx3cli.extract_gx3_extended_instruction_knowledge import LABEL_DEVICE_TYPE, LABEL_TOKEN_PREFIX
from gx3cli.gx3_instruction_table import MANUAL_WRITE_ARGS, manual_write_indices
from gx3cli.gx3_label_resolve import LabelResolver, split_label_token


INNER_DEV_RE = re.compile(r"d\{[^{}]*?a=(-?\d+)[^{}]*?\}")
CONST_VALUE_RE = re.compile(r"v=([^:}]+)")

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

TYPE_TOKEN_ALIASES = {"Us": "U", "Zs": "Z"}
SKIP_ARG_TOKENS = {"Ks", "Dots", "Digits"}
M_CONST_BASE_RE = re.compile(r"b=c\{[^}]*v=(-?\d+)")
M_INDEX_DEV_RE = re.compile(r"m=d\{[^}]*a=(-?\d+)")
M_CONST_MOD_RE = re.compile(r"m=c\{[^}]*v=(-?\d+)")


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
    tokens = header_tokens(data)
    header_ops = parse_header_ops(data)
    ce_elements = [e for e in extract_elements(data) if "s=ce{" in e]
    status = "exact" if len(ce_elements) == len(header_ops) else "partial"
    results: list[tuple[str, str, list[ArgOcc], str]] = []

    for op_index, hop in enumerate(header_ops):
        element = ce_elements[op_index] if op_index < len(ce_elements) else ""
        args_text = extract_args_text(element)
        raw_args = top_level_items(args_text) if args_text else []
        next_op_token = header_ops[op_index + 1].token_index if op_index + 1 < len(header_ops) else len(tokens)
        arg_tokens = tokens[hop.token_index + 1 : next_op_token]

        if hop.op in CONTACT_ROLES or hop.op in COIL_ROLES:
            occ = decode_args(raw_args, arg_tokens or [hop.device_type], hop.op, labels)
            role = hop.op
            access = "read" if hop.op in CONTACT_ROLES else "write"
            for a in occ:
                a.access = "read" if a.is_index_register else access
                a.access_basis = "index register" if a.is_index_register else "ladder contact/coil"
            results.append((role, "", occ, const_summary(raw_args)))
            continue

        occ = decode_args(raw_args, arg_tokens, hop.op, labels)
        wset, rmw = write_indices(hop.op, len(raw_args))
        basis = write_index_basis(hop.op, len(raw_args))
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
            else:
                a.access = "read"
            a.access_basis = basis
        results.append((hop.op, hop.op, occ, const_summary(raw_args)))
    return results, status


def const_summary(raw_args: list[str]) -> str:
    values = []
    for arg in raw_args:
        if arg.startswith("c{"):
            m = CONST_VALUE_RE.search(arg)
            if m:
                values.append(m.group(1))
    return ",".join(values[:6])


def decode_args(
    raw_args: list[str], arg_tokens: list[str], role: str, labels: LabelResolver | None = None
) -> list[ArgOcc]:
    """Pair raw args with header type tokens and decode every device.

    Header token kinds per argument:
      d{}                -> TYPE
      c{}                -> K_/H_/E_ constant token
      B{} buffer memory  -> Us G
      M{} digit spec     -> TYPE Ks       (K2M35001)
      M{} bit of word    -> TYPE Dots     (D100.5)
      M{} const + index  -> K_n Zs        (K2400Z2 in FROM/TO offsets)
      M{} device + index -> TYPE Zs       (D100Z2)
    """
    occs: list[ArgOcc] = []
    ti = 0
    n_tokens = len(arg_tokens)

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
                or re.match(r"^[KHE]_", tok)
                or tok == "String"
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
            if alias in DEVICE_TYPES or alias == "U":
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

    for arg_index, arg in enumerate(raw_args):
        if arg.startswith("l{"):
            # A label reference. The arg itself is a placeholder ("l{id=#}");
            # the identity is the "_lid/<LabelID>/<row>" header token.
            token = next((t for t in arg_tokens[ti:] if t.startswith(LABEL_TOKEN_PREFIX)), "")
            if token:
                ti = arg_tokens.index(token, ti) + 1
            occs.append(make_label_occ(token, arg_index, labels))
            continue
        if arg.startswith("c{"):
            take_if_const()
            continue
        if arg.startswith("d{"):
            dev_type = take_type()
            m = INNER_DEV_RE.search(arg)
            if not m or dev_type not in DEVICE_TYPES:
                continue
            occs.append(make_occ(dev_type, int(m.group(1)), arg_index))
        elif arg.startswith("B{"):
            inner = INNER_DEV_RE.findall(arg)
            dev_type = take_type()
            if dev_type == "U" and len(inner) >= 2:
                unit = int(inner[0])
                offset = int(inner[1])
                take_if("G")
                occs.append(
                    ArgOcc(
                        device=f"U{unit:X}\\G{offset}",
                        device_type="UG",
                        number=offset,
                        access="",
                        arg_index=arg_index,
                        detail=f"unit=0x{unit:X}",
                    )
                )
                for extra in inner[2:]:
                    occs.append(make_occ("Z", int(extra), arg_index, detail="index register", index_register=True))
            elif inner:
                occs.append(make_occ(dev_type, int(inner[0]), arg_index, detail="range/indexed"))
        elif arg.startswith("M{"):
            const_base = M_CONST_BASE_RE.search(arg)
            index_dev = M_INDEX_DEV_RE.search(arg)
            const_mod = M_CONST_MOD_RE.search(arg)
            buffer_inner = INNER_DEV_RE.findall(arg) if "B{" in arg else []
            if buffer_inner:
                dev_type = take_type()
                if dev_type == "U" and len(buffer_inner) >= 2:
                    unit = int(buffer_inner[0])
                    offset = int(buffer_inner[1])
                    take_if("G")
                    # Buffer memory carries either a bit position (header "Dots") or an
                    # index register (header "Zs"). The index form consumed no token, so
                    # its "Zs" was left for the next operand to read as its device type:
                    # a BMOV's D48200Z2 came back as an index register Z48200, and the D
                    # occurrence went missing from the cross-reference.
                    index_reg = ""
                    if const_mod is None and index_dev is not None:
                        if take_if("Zs", "Z"):
                            index_reg = index_dev.group(1)
                    else:
                        take_if("Dots")
                    bit = const_mod.group(1) if const_mod else ""
                    suffix = f".{bit}" if bit else (f"Z{index_reg}" if index_reg else "")
                    detail = f"unit=0x{unit:X}" + (f" bit={bit}" if bit else "")
                    if index_reg:
                        detail += f" Z{index_reg} indexed"
                    occs.append(
                        ArgOcc(
                            device=f"U{unit:X}\\G{offset}{suffix}",
                            device_type="UG",
                            number=offset,
                            access="",
                            arg_index=arg_index,
                            detail=detail,
                        )
                    )
                    if index_reg:
                        occs.append(make_occ("Z", int(index_reg), arg_index, detail="index register", index_register=True))
                    continue
            if const_base:
                # constant base with index register: K2400Z2 (header: K_n Zs)
                take_if_const()
                take_if("Zs", "Z")
                if index_dev:
                    detail = f"base=K{const_base.group(1)}+Z{index_dev.group(1)}"
                    occs.append(make_occ("Z", int(index_dev.group(1)), arg_index, detail=detail, index_register=True))
                continue
            dev_type = take_type()
            m = INNER_DEV_RE.search(arg)
            if not m:
                continue
            number = int(m.group(1))
            if index_dev and index_dev.group(1) != m.group(1):
                take_if("Zs", "Z")
                occs.append(make_occ(dev_type, number, arg_index, detail=f"Z{index_dev.group(1)} indexed"))
                occs.append(make_occ("Z", int(index_dev.group(1)), arg_index, detail="index register", index_register=True))
            elif const_mod:
                mod_tok = take_if("Ks", "Dots")
                kind = "digit" if mod_tok == "Ks" else ("bit" if mod_tok == "Dots" else "mod")
                occs.append(make_occ(dev_type, number, arg_index, detail=f"{kind}=K{const_mod.group(1)}"))
            else:
                take_if("Ks", "Dots", "Zs")
                occs.append(make_occ(dev_type, number, arg_index, detail="modified"))
        else:
            continue

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
