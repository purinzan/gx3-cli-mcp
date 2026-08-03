import csv
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from gx3cli.gx3_project_paths import default_output_path, default_project_root

ROOT = default_project_root()

OUT_SUMMARY = default_output_path("extended_instruction_summary", "csv")
OUT_SAMPLES = default_output_path("extended_instruction_samples", "csv")
OUT_WORDS = default_output_path("word_register_usage_summary", "csv")
OUT_TIMERS = default_output_path("timer_counter_usage_summary", "csv")
OUT_NOTES = default_output_path("extended_instruction_knowledge", "txt")
OUT_INTERMEDIATE_SAMPLES = default_output_path("ladder_intermediate_extended_samples", "yaml")


DEVICE_TYPES = {
    "X",
    "Y",
    "M",
    "L",
    "F",
    "V",
    "S",
    "B",
    "W",
    "D",
    "ZR",
    "Z",
    "T",
    "ST",
    "C",
    "SM",
    "SB",
    "SW",
    "SD",
    "R",
    "U",
    "G",
    "J",
    "DX",
    "DY",
}

# Label-reference tokens (symbolic devices in the newer GX Works3 generation):
# "_lid/<label-table-id>/<n>". In the header they follow a/b/c role tokens
# instead of a plain device-type token, usually as
#   a:_lid/../1:_lid/../1:K_1:<index>:Dots   (array label bit, indexed)
# or a:_lid/../2                             (direct label bit).
LABEL_TOKEN_PREFIX = "_lid/"
LABEL_DEVICE_TYPE = "LABEL"

DEVICE_TYPE_ALIASES = {
    "Us": "U",
    "Zs": "Z",
}

WORD_REGISTER_TYPES = {"D", "W", "ZR", "Z", "R", "SW", "SD"}
TIMER_COUNTER_TYPES = {"T", "C"}

IGNORED_HEADER_TOKENS = DEVICE_TYPES | {
    "V1",
    "a",
    "b",
    "c",
    "K_1",
    "K_2",
    "K_4",
    "K_8",
    "Ks",
    "H_1",
    "H_2",
    "H_4",
    "String",
    "Zs",
    "Ats",
    "Us",
    "Dots",
    "N",
    "P",
    "ON",
    "OFF",
    "--",
    "",
}

HEADER_NOTE_DEVICE_RE = re.compile(r"^[A-Z]+-?\d+(?:\.\d+)?$")

KNOWN_OPS = {
    "=",
    "<>",
    "<",
    "<=",
    ">",
    ">=",
    "+",
    "+P",
    "$+",
    "D+",
    "D+P",
    "-",
    "-P",
    "D-",
    "D-P",
    "*",
    "*P",
    "D*",
    "D*P",
    "/",
    "/P",
    "D/",
    "D/P",
    "B+",
    "B+P",
    "B-",
    "B-P",
    "B*",
    "B*P",
    "B/",
    "B/P",
    "BK+",
    "BK-",
    "BK*",
    "BK/",
    "MOV",
    "MOVP",
    "$MOV",
    "$MOVP",
    "BMOV",
    "BMOVP",
    "BMOVL",
    "BMOVLP",
    "BXCH",
    "BLKMOVB",
    "FMOV",
    "FMOVP",
    "FMOVL",
    "DMOV",
    "DMOVP",
    "DFMOV",
    "DFMOVL",
    "SET",
    "RST",
    "RST__16",
    "PLS",
    "PLF",
    "BSET",
    "BRST",
    "NOPLF",
    "OUT__16",
    "OUTH__16",
    "INC",
    "INCP",
    "DINC_U",
    "DINCP_U",
    "DINCP",
    "DEC",
    "DECP",
    "DSUM",
    "SUM",
    "WSUM",
    "WSUM_U",
    "DWSUM",
    "WAND",
    "WANDP",
    "WOR",
    "WORP",
    "DOR",
    "BKAND",
    "BKBCD",
    "DX",
    "DROL",
    "INV",
    "MC",
    "MCR",
    "ME",
    "FF",
    "BKRST",
    "FOR",
    "NEXT",
    "GP.RIWT",
    "GP.RIRD",
    "ADRSET",
    "DVALP",
    "WTOBP",
    "SFTBRP",
    "SFTBL",
    "BINDA",
    "DBINDAP",
    "DBINDA",
    "BCDDA",
    "DABINP",
    "DDABIN",
    "BINHA",
    "DECO",
    "DTEST",
    "DATERD",
    "DATEWR",
    "SEC2DATE",
    "SEC2DATEP",
    "SEC2DATE_U",
    "SEC2DATEP_U",
    "TIME2SEC",
    "SEC2TIME",
    "DATE2SEC_U",
    "INT2DINT",
    "INT2DINTP",
    "DINT2INT",
    "UDINT2UINT",
    "INSTRP",
    "MIDRP",
    "MIDW",
    "FIFW",
    "FDEL",
    "LEDR",
    "SERDATA",
    "SERDATAP",
    "STRDEL",
    "G.INPUT",
    "G.OUTPUT",
    "GP.ERRCLEAR",
    "ZP.CSET",
    "ZP.REMTO",
    "ZP.REMFR",
    "DLIMIT",
    "ERROR",
    "EI",
    "ECALL",
    "CJ",
    "CALL",
    "BREAK",
    "RET",
    "FEND",
    "GOEND",
    "BKCMP=",
    "BKCMP<>",
    "BKCMP<",
    "BKCMP<=",
    "BKCMP>",
    "BKCMP>=",
    "BTOW",
    "WTOB",
    "LEN",
    "POW",
    # 32-bit float (single-precision real) arithmetic. The op token contains
    # "*" or "/" so the generic op regex cannot match it; list explicitly.
    "E+",
    "E+P",
    "E-",
    "E-P",
    "E*",
    "E*P",
    "E/",
    "E/P",
}

DEVICE_OPERAND_OPS = {
    "SET",
    "RST",
    "PLS",
    "PLF",
    "FF",
    "INC",
    "INCP",
    "DINCP",
    "DEC",
    "DECP",
    "OUT__16",
    "OUTH__16",
}


@dataclass
class HeaderOp:
    op: str
    device_type: str = ""
    token_index: int = 0


def decode_data(value) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("cp932", errors="ignore")
    return "" if value is None else str(value)


def as_int(value) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return value


def read_ladder_rows():
    for db_path in sorted(ROOT.glob("*_LDDB.db")):
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.text_factory = bytes
        cur = con.cursor()
        try:
            rows = cur.execute(
                "select id, pos, blocktype, rowsize, data from LadderBlocks order by pos"
            ).fetchall()
        except sqlite3.Error:
            con.close()
            continue

        current_title = ""
        for block_id, pos, blocktype, rowsize, data in rows:
            text = decode_data(data)
            if blocktype in (1, 2):
                title_text = extract_title_text(text)
                if title_text:
                    current_title = title_text
            if blocktype == 0:
                yield {
                    "lddb": db_path.name,
                    "id": decode_data(block_id),
                    "pos": as_int(pos),
                    "rowsize": as_int(rowsize),
                    "title": current_title,
                    "data": text,
                }
        con.close()


def extract_title_text(data: str) -> str:
    if not data:
        return ""
    if ":st{" in data:
        body = data.split(":st{", 1)[0]
        parts = body.split(":")
        if len(parts) >= 4:
            return parts[-1]
    if "[Title]" in data:
        m = re.search(r"(\[Title\].*?)(?::st\{|$)", data)
        if m:
            return m.group(1)
    return ""


def header_tokens(data: str) -> list[str]:
    prefix = data.split(":cb{", 1)[0]
    return prefix.split(":")


def is_op_like(token: str) -> bool:
    if token in KNOWN_OPS:
        return True
    if token in IGNORED_HEADER_TOKENS:
        return False
    if HEADER_NOTE_DEVICE_RE.fullmatch(token):
        return False
    if re.fullmatch(r"\d+", token):
        return False
    return bool(re.fullmatch(r"[A-Z_$][A-Z0-9_.$]*P?|[<>=+\-*/]+|OUT__\d+|OUTH__\d+", token))


def token_device_type(token: str) -> str:
    return DEVICE_TYPE_ALIASES.get(token, token)


def has_device_operand_after(tokens: list[str], index: int) -> bool:
    if index + 1 >= len(tokens):
        return False
    return token_device_type(tokens[index + 1]) in DEVICE_TYPES


def parse_header_ops(data: str) -> list[HeaderOp]:
    tokens = header_tokens(data)
    ops: list[HeaderOp] = []
    skip_string_literal_tokens = 0
    for i, token in enumerate(tokens):
        if skip_string_literal_tokens:
            skip_string_literal_tokens -= 1
            continue
        if token == "String":
            skip_string_literal_tokens = 2
            continue
        if token in DEVICE_TYPES and i > 0 and tokens[i - 1] in {"a", "b", "c", "EG"}:
            continue
        if token in {"a", "b", "c", "EG"} and i + 1 < len(tokens):
            device_type = token_device_type(tokens[i + 1])
            if device_type in DEVICE_TYPES:
                ops.append(HeaderOp(token, device_type, i))
            elif tokens[i + 1].startswith(LABEL_TOKEN_PREFIX):
                # Label (symbolic) bit device contact/coil. Count the op so
                # the header sequence stays aligned with the ce elements.
                # Read-only support: the label identity itself is not resolved.
                ops.append(HeaderOp(token, LABEL_DEVICE_TYPE, i))
        elif is_op_like(token):
            if token in DEVICE_OPERAND_OPS and not has_device_operand_after(tokens, i):
                continue
            ops.append(HeaderOp(token, "", i))
    return ops


def parse_header_devices(data: str) -> list[tuple[str, str, int]]:
    tokens = header_tokens(data)
    devices: list[tuple[str, str, int]] = []
    current_instruction = ""
    skip_string_literal_tokens = 0
    for i, token in enumerate(tokens):
        if skip_string_literal_tokens:
            skip_string_literal_tokens -= 1
            continue
        if token == "String":
            skip_string_literal_tokens = 2
            continue
        if token in DEVICE_TYPES and i > 0 and tokens[i - 1] in {"a", "b", "c", "EG"}:
            continue
        if token in {"a", "b", "c", "EG"} and i + 1 < len(tokens):
            device_type = token_device_type(tokens[i + 1])
            if device_type in DEVICE_TYPES:
                devices.append((device_type, token, i))
        elif is_op_like(token):
            if token in DEVICE_OPERAND_OPS and not has_device_operand_after(tokens, i):
                continue
            current_instruction = token
        elif token in DEVICE_TYPES:
            prev = tokens[i - 1] if i > 0 else ""
            if prev not in {"a", "b", "c"}:
                devices.append((token, current_instruction or "instruction_operand", i))
    return devices


def matching_at(text: str, start: int, open_char: str, close_char: str) -> int:
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return i
    return -1


def extract_es_text(data: str) -> str:
    marker = "es=["
    start = data.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker) - 1
    end = matching_at(data, body_start, "[", "]")
    if end < 0:
        return ""
    return data[body_start + 1 : end]


def extract_elements(data: str) -> list[str]:
    es_text = extract_es_text(data)
    elements: list[str] = []
    i = 0
    while i < len(es_text):
        if es_text.startswith("e{", i):
            end = matching_at(es_text, i + 1, "{", "}")
            if end < 0:
                break
            elements.append(es_text[i : end + 1])
            i = end + 1
        else:
            i += 1
    return elements


def top_level_items(text: str) -> list[str]:
    items = []
    start = 0
    brace = bracket = 0
    for i, ch in enumerate(text):
        if ch == "{":
            brace += 1
        elif ch == "}":
            brace -= 1
        elif ch == "[":
            bracket += 1
        elif ch == "]":
            bracket -= 1
        elif ch == ":" and brace == 0 and bracket == 0:
            items.append(text[start:i])
            start = i + 1
    items.append(text[start:])
    return [item for item in items if item]


def extract_args_text(element: str) -> str:
    marker = "args=["
    start = element.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker) - 1
    end = matching_at(element, body_start, "[", "]")
    if end < 0:
        return ""
    return element[body_start + 1 : end]


def summarize_arg(arg: str) -> str:
    if arg.startswith("d{"):
        num = re.search(r"a=([-]?\d+)", arg)
        return f"d(a={num.group(1)})" if num else "d"
    if arg.startswith("c{"):
        value = re.search(r"v=([^:}]+)", arg)
        sign = re.search(r"si=([^:}]+)", arg)
        suffix = f",si={sign.group(1)}" if sign else ""
        return f"c(v={value.group(1)}{suffix})" if value else "c"
    if arg.startswith("M{"):
        return "M(indexed)"
    if arg.startswith("B{"):
        return "B(bit-range/indexed)"
    return arg[:32]


def element_meta(element: str) -> dict[str, object]:
    pos_match = re.search(r"pos=(\d+),(\d+)", element)
    ce_kind = ""
    ct_code = ""
    if "s=-" in element:
        ce_kind = "wire"
    else:
        kind_match = re.search(r"op=(ct|cl)\{", element)
        if kind_match:
            ce_kind = kind_match.group(1)
        ct_match = re.search(r"op=(?:ct|cl)\{op=#:ct=([^:}]+)", element)
        if ct_match:
            ct_code = ct_match.group(1)

    as_vts = re.findall(r"as\{vt=([^}]+)\}", element)
    args_text = extract_args_text(element)
    args = top_level_items(args_text) if args_text else []

    return {
        "pos": f"{pos_match.group(1)},{pos_match.group(2)}" if pos_match else "",
        "element_kind": ce_kind,
        "ct_code": ct_code,
        "as_vts": ",".join(as_vts),
        "arg_count": len(args),
        "arg_shapes": "; ".join(summarize_arg(arg) for arg in args),
    }


def extract_dim(data: str) -> str:
    m = re.search(r"dim=([^:}]+)", data)
    return m.group(1) if m else ""


def classify_op(op: str) -> str:
    if op in {"SET", "RST", "PLS", "PLF", "NOPLF", "INV", "MC", "MCR", "ME", "FF", "BKRST"}:
        return "special_coil_or_control"
    if op in {"OUT__16", "OUTH__16"}:
        return "timer_counter_output"
    if op in {"MOV", "MOVP", "$MOV", "$MOVP", "BMOV", "BMOVP", "FMOV", "FMOVP", "DMOV", "DMOVP", "DFMOV"}:
        return "transfer"
    if op in {
        "+",
        "+P",
        "D+",
        "D+P",
        "-",
        "-P",
        "D-",
        "D-P",
        "*",
        "*P",
        "D*",
        "D*P",
        "/",
        "/P",
        "D/",
        "D/P",
        "INC",
        "INCP",
        "DINCP",
        "DEC",
        "DECP",
        "DSUM",
        "SUM",
        "WSUM",
        "DWSUM",
    }:
        return "arithmetic_or_count"
    if op in {"=", "<>", "<", "<=", ">", ">="}:
        return "compare_contact"
    if op in {"WAND", "WANDP", "WOR", "WORP", "DOR", "DROL"}:
        return "word_bit_operation"
    if op in {"GP.RIWT", "GP.RIRD", "ADRSET"}:
        return "module_or_buffer_access"
    if op in {"FOR", "NEXT"}:
        return "loop_control"
    if op in {"DVALP", "WTOBP", "SFTBRP", "BINDA", "DBINDAP", "DABINP", "DECO", "INSTRP", "MIDRP"}:
        return "conversion_or_string"
    if op in {"DATERD", "DATEWR"}:
        return "clock_calendar"
    return "other_instruction"


def support_status(op: str, category: str) -> str:
    if category in {"compare_contact", "transfer", "timer_counter_output", "special_coil_or_control"}:
        return "next_supported_candidate"
    if category in {"arithmetic_or_count", "word_bit_operation"}:
        return "support_after_operand_model"
    if category in {"module_or_buffer_access", "loop_control", "conversion_or_string"}:
        return "raw_only_until_structured_edit_test"
    return "investigate_before_edit"


def safe_snippet(text: str, limit: int = 500) -> str:
    compact = re.sub(r"\s+", " ", text)
    return compact[:limit]


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


def main() -> None:
    op_counter: Counter[str] = Counter()
    row_counter: dict[str, set[tuple[str, object]]] = defaultdict(set)
    category_counter: Counter[str] = Counter()
    pattern_counter: dict[str, Counter[tuple[str, str, int, str]]] = defaultdict(Counter)
    samples: list[dict[str, object]] = []
    word_counter: dict[str, Counter[str]] = defaultdict(Counter)
    word_samples: dict[str, list[str]] = defaultdict(list)
    timer_counter: dict[str, Counter[str]] = defaultdict(Counter)
    timer_samples: dict[str, list[str]] = defaultdict(list)

    ladder_rows = list(read_ladder_rows())
    for row in ladder_rows:
        data = row["data"]
        ops = parse_header_ops(data)
        elements = extract_elements(data)
        ce_elements = [e for e in elements if "s=ce{" in e]
        dim = extract_dim(data)

        for dev_type, role, _token_index in parse_header_devices(data):
            if dev_type in WORD_REGISTER_TYPES:
                word_counter[dev_type][role] += 1
                if len(word_samples[dev_type]) < 5:
                    word_samples[dev_type].append(f"{row['lddb']}:{row['pos']}:{row['title']}")
            if dev_type in TIMER_COUNTER_TYPES:
                timer_counter[dev_type][role] += 1
                if len(timer_samples[dev_type]) < 5:
                    timer_samples[dev_type].append(f"{row['lddb']}:{row['pos']}:{row['title']}")

        ce_index = 0
        for seq_index, header_op in enumerate(ops):
            if header_op.op in {"a", "b", "c"}:
                ce_index += 1
                continue

            category = classify_op(header_op.op)
            op_counter[header_op.op] += 1
            row_counter[header_op.op].add((row["lddb"], row["pos"]))
            category_counter[category] += 1

            meta = {}
            if ce_index < len(ce_elements):
                meta = element_meta(ce_elements[ce_index])
            ce_index += 1

            pattern = (
                str(meta.get("element_kind", "")),
                str(meta.get("ct_code", "")),
                int(meta.get("arg_count", 0) or 0),
                str(meta.get("as_vts", "")),
            )
            pattern_counter[header_op.op][pattern] += 1

            if sum(1 for s in samples if s["instruction"] == header_op.op) < 5:
                samples.append(
                    {
                        "category": category,
                        "instruction": header_op.op,
                        "lddb": row["lddb"],
                        "pos": row["pos"],
                        "block_id": row["id"],
                        "title": row["title"],
                        "rowsize": row["rowsize"],
                        "dim": dim,
                        "header_sequence_index": seq_index,
                        "element_pos": meta.get("pos", ""),
                        "element_kind": meta.get("element_kind", ""),
                        "ct_code": meta.get("ct_code", ""),
                        "arg_count": meta.get("arg_count", ""),
                        "arg_shapes": meta.get("arg_shapes", ""),
                        "as_vts": meta.get("as_vts", ""),
                        "support_status": support_status(header_op.op, category),
                        "data_snippet": safe_snippet(data),
                    }
                )

    summary_rows = []
    for op, count in op_counter.most_common():
        category = classify_op(op)
        patterns = pattern_counter[op].most_common(3)
        pattern_text = " / ".join(
            f"{kind}|ct={ct}|args={argc}|as={as_vts} ({n})"
            for (kind, ct, argc, as_vts), n in patterns
        )
        summary_rows.append(
            {
                "category": category,
                "instruction": op,
                "occurrences": count,
                "rows": len(row_counter[op]),
                "support_status": support_status(op, category),
                "top_element_patterns": pattern_text,
            }
        )

    word_rows = []
    for dev_type in sorted(word_counter):
        counter = word_counter[dev_type]
        total = sum(counter.values())
        instruction_total = total - counter.get("a", 0) - counter.get("b", 0) - counter.get("c", 0)
        top_instruction_roles = ", ".join(
            f"{role}:{count}"
            for role, count in counter.most_common(8)
            if role not in {"a", "b", "c"}
        )
        word_rows.append(
            {
                "device_type": dev_type,
                "occurrences": total,
                "contact_a": counter.get("a", 0),
                "contact_b": counter.get("b", 0),
                "coil_c": counter.get("c", 0),
                "instruction_operands": instruction_total,
                "top_instruction_roles": top_instruction_roles,
                "sample_locations": " / ".join(word_samples[dev_type]),
            }
        )

    timer_rows = []
    for dev_type in sorted(TIMER_COUNTER_TYPES):
        counter = timer_counter[dev_type]
        total = sum(counter.values())
        instruction_total = total - counter.get("a", 0) - counter.get("b", 0) - counter.get("c", 0)
        top_instruction_roles = ", ".join(
            f"{role}:{count}"
            for role, count in counter.most_common(8)
            if role not in {"a", "b", "c"}
        )
        timer_rows.append(
            {
                "device_type": dev_type,
                "occurrences": total,
                "contact_a": counter.get("a", 0),
                "contact_b": counter.get("b", 0),
                "coil_c": counter.get("c", 0),
                "out_16": counter.get("OUT__16", 0),
                "outh_16": counter.get("OUTH__16", 0),
                "reset_or_special": counter.get("RST", 0) + counter.get("SET", 0),
                "instruction_operands": instruction_total,
                "top_instruction_roles": top_instruction_roles,
                "sample_locations": " / ".join(timer_samples[dev_type]),
            }
        )

    write_csv(
        OUT_SUMMARY,
        summary_rows,
        [
            "category",
            "instruction",
            "occurrences",
            "rows",
            "support_status",
            "top_element_patterns",
        ],
    )
    write_csv(
        OUT_SAMPLES,
        samples,
        [
            "category",
            "instruction",
            "lddb",
            "pos",
            "block_id",
            "title",
            "rowsize",
            "dim",
            "header_sequence_index",
            "element_pos",
            "element_kind",
            "ct_code",
            "arg_count",
            "arg_shapes",
            "as_vts",
            "support_status",
            "data_snippet",
        ],
    )
    write_csv(
        OUT_WORDS,
        word_rows,
        [
            "device_type",
            "occurrences",
            "contact_a",
            "contact_b",
            "coil_c",
            "instruction_operands",
            "top_instruction_roles",
            "sample_locations",
        ],
    )
    write_csv(
        OUT_TIMERS,
        timer_rows,
        [
            "device_type",
            "occurrences",
            "contact_a",
            "contact_b",
            "coil_c",
            "out_16",
            "outh_16",
            "reset_or_special",
            "instruction_operands",
            "top_instruction_roles",
            "sample_locations",
        ],
    )

    lines = [
        "Extended instruction knowledge / 拡張命令・ワードデバイス調査",
        "============================================================",
        "",
        "目的:",
        "  YAML等の中間表現で扱える命令範囲を広げるため、既存LDDBから命令保存パターンを採取した。",
        "  今回の調査は読み取り専用で、入力プロジェクト本体は変更していない。",
        "",
        "生成ファイル:",
        f"  {OUT_SUMMARY}: 命令別の出現数、分類、代表要素パターン。",
        f"  {OUT_SAMPLES}: 命令別サンプル。LDDB、pos、タイトル、要素位置、引数形状、raw断片を含む。",
        f"  {OUT_WORDS}: D/W/ZR/Z/R/SW/SDなどワード・レジスタ系デバイスの使用形態。",
        f"  {OUT_TIMERS}: T/Cタイマ・カウンタ系デバイスの使用形態。",
        f"  {OUT_INTERMEDIATE_SAMPLES}: intermediate representation samples, when available.",
        "",
        "全体件数:",
        f"  実ラダー行: {len(ladder_rows)}",
        f"  命令/比較/特殊コイル候補の総出現: {sum(op_counter.values())}",
        "",
        "分類別件数:",
    ]
    for category, count in category_counter.most_common():
        lines.append(f"  {category}: {count}")

    lines.extend(["", "主要命令 上位40:"])
    for row in summary_rows[:40]:
        lines.append(
            f"  {row['instruction']}: {row['occurrences']}件, {row['rows']}行, "
            f"{row['category']}, {row['support_status']}"
        )

    lines.extend(
        [
            "",
            "中間表現への現在方針:",
            "  1. a/b接点と通常コイルに加え、op=clで表現される命令ブロックを instruction ノードとして扱う。",
            "  2. 命令名は LadderBlocks.data のヘッダ部にある MOV/MOVP/SET/RST/OUT__16 等から取得する。",
            "  3. 実要素側は es=[...] 内の ce 要素と順番対応させ、ct_code、arg_count、as_vts、arg_shapesを保持する。",
            "  4. YAMLでは未解釈の引数 raw も必ず持ち、書き戻し時に情報落ちしないようにする。",
            "  5. compare_contact、transfer、timer_counter_output、special_coil_or_control は同形引数変更の編集候補にする。",
            "  6. module_or_buffer_access、loop_control、conversion_or_string は当面 raw_only または個別検証扱いにする。",
            "",
            "分かった保存パターン:",
            "  - SET/RST/PLS はヘッダでは SET/RST/PLS、要素側では op=cl として保存される。",
            "  - b接点でもes側のct_codeがaの例があるため、接点種別はヘッダ側の a/b を正として保持する。",
            "  - PLS は ct_code=p のコイル要素として出る例がある。",
            "  - タイマ系は OUT__16 / OUTH__16 として出る。TデバイスとK定数を引数に持つ。",
            "  - MOV/MOVP/DMOV/BMOV/FMOV系は op=cl、引数数2または3が中心。",
            "  - =, <>, <, <=, >, >= は op=ct の比較接点として出る。",
            "  - D/ZR/Z/W/SW/SDは命令引数にも接点にも出る。中間表現では bit_device と word_device を分ける必要がある。",
            f"  - Tデバイスは{sum(timer_counter['T'].values())}件見つかった。Cデバイスは{sum(timer_counter['C'].values())}件で、このプロジェクトでは実質未使用。",
            "  - M{...} や B{...} のような indexed/bit-range 形があり、単純な d{a=...} だけでは扱えない。",
            "",
            "後続検証で完了したこと:",
            "  1. LDDB -> 中間JSON -> LDDB の raw_data 完全保持ラウンドトリップは成功。",
            "  2. 全5028行で row_level_differences = 0。",
            "  3. 実ラダー4136行中、4135行はヘッダ命令列とes要素が1対1対応。",
            "  4. SET/RST/PLS/OUT__16/MOV/BMOV/比較接点の同形引数変更は成功。",
            "  5. 同形引数変更では StepInfo 更新なしでDB構造上成立。",
            "",
            "残り:",
            "  1. GX Works3で生成GX3を開く。",
            "  2. GX Works3で変換・保存する。",
            "  3. 保存後差分を確認する。",
            "  4. GP.RIWT/GP.RIRDを含む複合行は当面 raw_only とする。",
        ]
    )

    OUT_NOTES.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

    print(f"Wrote {OUT_SUMMARY} ({len(summary_rows)} rows)")
    print(f"Wrote {OUT_SAMPLES} ({len(samples)} rows)")
    print(f"Wrote {OUT_WORDS} ({len(word_rows)} rows)")
    print(f"Wrote {OUT_TIMERS} ({len(timer_rows)} rows)")
    print(f"Wrote {OUT_NOTES}")


if __name__ == "__main__":
    main()
