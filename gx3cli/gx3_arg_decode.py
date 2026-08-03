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

from gx3cli.extract_gx3_extended_instruction_knowledge import (
    DEVICE_TYPES,
    extract_args_text,
    extract_elements,
    header_tokens,
    top_level_items,
)
from gx3cli.gx3_intermediate_tool import parse_header_ops


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
COMPARE_RE = re.compile(r"^(?:(?:LD|AND|OR)?(?:D|E|\$)?|BKCMP)(?:=|<>|<=|>=|<|>)$")

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
        if argc <= 2:
            return {argc - 1}, True
        return {argc - 1}, False
    spec = WRITE_ARG_TABLE.get(op)
    if spec is None:
        return None, False
    if spec == "last":
        return {argc - 1}, False
    return set(spec), False  # type: ignore[arg-type]


def parse_row_occurrences(data: str) -> tuple[list[tuple[str, str, list[ArgOcc], str]], str]:
    """Return ([(role, opcode, args, const_summary)], parse_status).

    role is a/b/c for contacts/coils, otherwise the opcode.
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
            occ = decode_args(raw_args, arg_tokens or [hop.device_type], hop.op)
            role = hop.op
            access = "read" if hop.op in CONTACT_ROLES else "write"
            for a in occ:
                a.access = access
            results.append((role, "", occ, const_summary(raw_args)))
            continue

        occ = decode_args(raw_args, arg_tokens, hop.op)
        wset, rmw = write_indices(hop.op, len(raw_args))
        for a in occ:
            if wset is None:
                a.access = "ref"
            elif a.arg_index in wset:
                a.access = "both" if rmw else "write"
            else:
                a.access = "read"
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


def decode_args(raw_args: list[str], arg_tokens: list[str], role: str) -> list[ArgOcc]:
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
                    occs.append(make_occ("Z", int(extra), arg_index, detail="index register"))
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
                    take_if("Dots")
                    bit = const_mod.group(1) if const_mod else ""
                    suffix = f".{bit}" if bit else ""
                    detail = f"unit=0x{unit:X}" + (f" bit={bit}" if bit else "")
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
                    continue
            if const_base:
                # constant base with index register: K2400Z2 (header: K_n Zs)
                take_if_const()
                take_if("Zs", "Z")
                if index_dev:
                    detail = f"base=K{const_base.group(1)}+Z{index_dev.group(1)}"
                    occs.append(make_occ("Z", int(index_dev.group(1)), arg_index, detail=detail))
                continue
            dev_type = take_type()
            m = INNER_DEV_RE.search(arg)
            if not m:
                continue
            number = int(m.group(1))
            if index_dev and index_dev.group(1) != m.group(1):
                take_if("Zs", "Z")
                occs.append(make_occ(dev_type, number, arg_index, detail=f"Z{index_dev.group(1)} indexed"))
                occs.append(make_occ("Z", int(index_dev.group(1)), arg_index, detail="index register"))
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


def make_occ(dev_type: str, number: int, arg_index: int, detail: str = "") -> ArgOcc:
    dev_type = dev_type or "?"
    return ArgOcc(
        device=f"{dev_type}{number}" if dev_type != "?" else f"?{number}",
        device_type=dev_type,
        number=number,
        access="",
        arg_index=arg_index,
        detail=detail,
    )
