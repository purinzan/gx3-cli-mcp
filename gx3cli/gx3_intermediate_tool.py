import argparse
import hashlib
import json
import shutil
import sqlite3
import time
import uuid
import zipfile
from itertools import product
from pathlib import Path

from gx3cli.gx3_device_name import format_device as _format_device, parse_device_name as _parse_device_name
from gx3cli.extract_gx3_extended_instruction_knowledge import (
    classify_op,
    element_meta,
    extract_dim,
    extract_elements,
    parse_header_ops,
    support_status,
)
from gx3cli.gx3_project_paths import convertdata_path, default_output_path


SCHEMA_VERSION = "0.3"
BIT_DEVICE_TYPES = {"X", "Y", "DX", "DY", "M", "L", "B", "SM", "SB", "T", "ST", "C", "F", "V", "S"}
SPECIAL_CODE_TYPES = {"SM", "SB"}


def decode_data(value) -> str:
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("cp932", errors="ignore")
    return "" if value is None else str(value)


def safe_int(value):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return value


def unique_path(prefix: str, suffix: str = "") -> Path:
    base = Path(f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}{suffix}")
    candidate = base
    index = 1
    while candidate.exists():
        candidate = Path(f"{prefix}_{time.strftime('%Y%m%d_%H%M%S')}_{index}{suffix}")
        index += 1
    return candidate


def ensure_source_root(input_path: Path, extract_dir: Path | None = None) -> Path:
    if input_path.is_dir():
        return input_path
    if not input_path.is_file():
        raise SystemExit(f"input not found: {input_path}")
    if extract_dir is None:
        extract_dir = unique_path(f"_project_export_{input_path.stem}")
    extract_dir.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(input_path, "r") as zf:
        zf.extractall(extract_dir)
    return extract_dir


def guid_to_blockid(guid: str) -> str:
    value = guid
    if value.startswith("_guid/"):
        value = value.split("/", 1)[1]
    return "{" + value.upper() + "}"


def normalize_guid(value: object) -> str | None:
    text = decode_data(value).strip()
    if text.startswith("_guid/"):
        text = text.split("/", 1)[1]
    text = text.strip("{}")
    try:
        return str(uuid.UUID(text))
    except (ValueError, AttributeError):
        return None


def guid_bytes_le(guid: str) -> bytes:
    return uuid.UUID(guid).bytes_le


def read_ladder_rows(root: Path) -> dict[str, list[dict[str, object]]]:
    result: dict[str, list[dict[str, object]]] = {}
    for db_path in sorted(root.glob("*_LDDB.db")):
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.text_factory = bytes
        rows = []
        for row in con.execute(
            "select id, pos, blocktype, data, rowsize, translated, ConvTarget from LadderBlocks order by pos, id"
        ):
            block_id, pos, blocktype, data, rowsize, translated, conv_target = row
            rows.append(
                {
                    "id": decode_data(block_id),
                    "pos": pos,
                    "blocktype": safe_int(blocktype),
                    "data": decode_data(data),
                    "rowsize": safe_int(rowsize),
                    "translated": safe_int(translated),
                    "ConvTarget": safe_int(conv_target),
                }
            )
        con.close()
        result[db_path.name] = rows
    return result


def operation_model(data: str) -> list[dict[str, object]]:
    header_ops = parse_header_ops(data)
    ce_elements = [e for e in extract_elements(data) if "s=ce{" in e]
    operations = []
    ce_index = 0
    for header_index, op in enumerate(header_ops):
        raw_element = ce_elements[ce_index] if ce_index < len(ce_elements) else ""
        meta = element_meta(raw_element) if raw_element else {}
        if op.op in {"a", "b", "c"}:
            node = {
                "kind": "contact" if op.op in {"a", "b"} else "coil",
                "role": op.op,
                "device_type": op.device_type,
            }
        else:
            category = classify_op(op.op)
            node = {
                "kind": "instruction",
                "opcode": op.op,
                "category": category,
                "support_status": support_status(op.op, category),
            }
        node.update(
            {
                "header_index": header_index,
                "element_pos": meta.get("pos", ""),
                "element_kind": meta.get("element_kind", ""),
                "ct_code": meta.get("ct_code", ""),
                "arg_count": meta.get("arg_count", 0),
                "as_vts": meta.get("as_vts", ""),
                "arg_shapes": meta.get("arg_shapes", ""),
            }
        )
        operations.append(node)
        ce_index += 1
    return operations


def find_stepinfo_map(root: Path, lddb_rows: dict[str, list[dict[str, object]]]) -> dict[str, str]:
    block_to_lddb = {}
    for lddb, rows in lddb_rows.items():
        for row in rows:
            if str(row["id"]).startswith("_guid/"):
                block_to_lddb[guid_to_blockid(str(row["id"]))] = lddb

    result: dict[str, str] = {}
    for step_path in sorted(root.glob("*_StepInfo.db")):
        con = sqlite3.connect(f"file:{step_path}?mode=ro", uri=True)
        try:
            for (block_id,) in con.execute("select BlockID from T_Block"):
                lddb = block_to_lddb.get(block_id)
                if lddb and lddb not in result:
                    result[lddb] = step_path.name
        except sqlite3.Error:
            pass
        con.close()
    return result


def export_payload(input_path: Path, output: Path, extract_dir: Path | None = None) -> dict[str, object]:
    root = ensure_source_root(input_path, extract_dir)
    ladder_rows = read_ladder_rows(root)
    stepinfo_map = find_stepinfo_map(root, ladder_rows)

    dbs = []
    stats = {"rows": 0, "parse_status": {}, "blocktypes": {}, "categories": {}}
    for lddb, rows in ladder_rows.items():
        out_rows = []
        for row in rows:
            data = str(row["data"])
            operations = operation_model(data) if row["blocktype"] == 0 else []
            if row["blocktype"] == 0:
                ce_count = len([e for e in extract_elements(data) if "s=ce{" in e])
                parse_status = "exact" if ce_count == len(operations) else "needs_raw_review"
            else:
                parse_status = "raw_non_ladder"

            for op in operations:
                if op.get("kind") == "instruction":
                    category = str(op.get("category", ""))
                    stats["categories"][category] = stats["categories"].get(category, 0) + 1
            stats["rows"] += 1
            stats["parse_status"][parse_status] = stats["parse_status"].get(parse_status, 0) + 1
            key = str(row["blocktype"])
            stats["blocktypes"][key] = stats["blocktypes"].get(key, 0) + 1

            out_rows.append(
                {
                    "id": row["id"],
                    "pos": row["pos"],
                    "blocktype": row["blocktype"],
                    "rowsize": row["rowsize"],
                    "translated": row["translated"],
                    "ConvTarget": row["ConvTarget"],
                    "dim": extract_dim(data) if row["blocktype"] == 0 else "",
                    "parse_status": parse_status,
                    "operations": operations,
                    "raw_data": data,
                    "modified": False,
                    "requires_stepinfo_update": False,
                    "generated_by": "",
                }
            )
        dbs.append({"lddb": lddb, "stepinfo": stepinfo_map.get(lddb, ""), "rows": out_rows})

    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": {"input": str(input_path), "source_root": str(root)},
        "writeback_policy": "raw_data_preserved",
        "dbs": dbs,
        "stats": stats,
    }
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def find_row(payload: dict[str, object], lddb: str, pos: float) -> dict[str, object]:
    for db in payload["dbs"]:
        if db["lddb"] != lddb:
            continue
        for row in db["rows"]:
            if float(row["pos"]) == float(pos):
                return row
    raise KeyError(f"row not found: {lddb} pos={pos}")


def find_db(payload: dict[str, object], lddb: str) -> dict[str, object]:
    for db in payload["dbs"]:
        if db["lddb"] == lddb:
            return db
    raise KeyError(f"LDDB not found: {lddb}")


def op_sequence(data: str) -> list[str]:
    return [op.op if op.op not in {"a", "b", "c"} else f"{op.op}:{op.device_type}" for op in parse_header_ops(data)]


def parse_device(device: str) -> tuple[str, int]:
    index = 0
    while index < len(device) and not device[index].isdigit() and device[index] != "-":
        index += 1
    dev_type = device[:index].upper()
    number_text = device[index:]
    if not dev_type or not number_text:
        raise ValueError(f"invalid device: {device}")
    if dev_type not in BIT_DEVICE_TYPES:
        raise ValueError(f"device type not supported for generated logic yet: {dev_type}")
    # X, Y and B are numbered in hex, so the base depends on the type.
    return _parse_device_name(f"{dev_type}{number_text}")


def toggle_contact(role: str) -> str:
    if role == "a":
        return "b"
    if role == "b":
        return "a"
    raise ValueError(f"invalid contact role: {role}")


def literal_from_expr(expr: dict[str, object], negate: bool = False) -> dict[str, object]:
    device = str(expr["device"])
    role = str(expr.get("contact", "a"))
    if role not in {"a", "b"}:
        raise ValueError(f"contact must be a or b: {expr}")
    if negate:
        role = toggle_contact(role)
    dev_type, number = parse_device(device)
    return {"role": role, "device": _format_device(dev_type, number), "device_type": dev_type, "number": number}


def to_nnf(expr, negate: bool = False):
    if isinstance(expr, str):
        return literal_from_expr({"device": expr}, negate)
    if not isinstance(expr, dict):
        raise ValueError(f"logic node must be object or device string: {expr}")
    if "device" in expr:
        return literal_from_expr(expr, negate)
    if "contact" in expr and "device" not in expr:
        raise ValueError(f"contact node missing device: {expr}")
    if "not" in expr:
        return to_nnf(expr["not"], not negate)
    if "xor" in expr:
        items = list(expr["xor"])
        if len(items) != 2:
            raise ValueError("xor currently supports exactly two operands")
        a, b = items
        expanded = {"or": [{"and": [a, {"not": b}]}, {"and": [{"not": a}, b]}]}
        return to_nnf(expanded, negate)
    if "and" in expr:
        key = "or" if negate else "and"
        return {key: [to_nnf(item, negate) for item in expr["and"]]}
    if "or" in expr:
        key = "and" if negate else "or"
        return {key: [to_nnf(item, negate) for item in expr["or"]]}
    raise ValueError(f"unsupported logic node: {expr}")


def dnf(expr) -> list[list[dict[str, object]]]:
    if isinstance(expr, dict) and "role" in expr and "device_type" in expr:
        return [[expr]]
    if isinstance(expr, dict) and "and" in expr:
        terms = [[]]
        for child in expr["and"]:
            child_terms = dnf(child)
            terms = [left + right for left, right in product(terms, child_terms)]
        return terms
    if isinstance(expr, dict) and "or" in expr:
        terms = []
        for child in expr["or"]:
            terms.extend(dnf(child))
        return terms
    raise ValueError(f"cannot convert to DNF: {expr}")


def code_for_contact(dev_type: str) -> list[str]:
    return ["1", "2"] if dev_type in SPECIAL_CODE_TYPES else ["1", "1"]


def code_for_output(output_type: str, dev_type: str) -> list[str]:
    if output_type == "coil":
        return code_for_contact(dev_type)
    if output_type in {"set", "rst", "pls"}:
        return ["3", "1"]
    raise ValueError(f"unsupported output type: {output_type}")


def contact_element(role: str, dev_type: str, number: int, x: int, y: int) -> str:
    # GX stores ordinary a/b contacts with ct=a in es; the header role carries a/b.
    return (
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}"
        f":args=[d{{s=#:a={number}:vt=nn}}]}}:pos={x},{y}}}"
    )


def output_element(output_type: str, number: int, x: int, y: int) -> str:
    ct = "p" if output_type == "pls" else "a"
    return (
        f"e{{s=ce{{op=cl{{op=#:ct={ct}:as=[as{{vt=Abl}}]}}"
        f":args=[d{{s=#:a={number}:vt=nn}}]}}:pos={x},{y}}}"
    )


def wire_element(x: int, y: int) -> str:
    return f"e{{s=-:pos={x},{y}}}"


def output_header(output_type: str, dev_type: str) -> str:
    if output_type == "coil":
        return f"c:{dev_type}"
    return f"{output_type.upper()}:{dev_type}"


def generate_rung(logic: dict[str, object], output: dict[str, object], max_terms: int = 64, max_contacts: int = 256) -> tuple[str, int, int]:
    output_type = str(output.get("type", "coil")).lower()
    out_dev_type, out_number = parse_device(str(output["device"]))
    nnf = to_nnf(logic)
    terms = dnf(nnf)
    if not terms:
        raise ValueError("logic produced no terms")
    if len(terms) > max_terms:
        raise ValueError(f"DNF term count too large: {len(terms)} > {max_terms}")
    contact_count = sum(len(term) for term in terms)
    if contact_count > max_contacts:
        raise ValueError(f"contact count too large: {contact_count} > {max_contacts}")

    rowsize = len(terms)
    max_len = max(1, max(len(term) for term in terms))
    coil_x = max_len
    width = coil_x + 1

    type_codes: list[str] = []
    op_tokens: list[str] = []
    elements: list[str] = []
    for y, term in enumerate(terms):
        for x, literal in enumerate(term):
            dev_type = str(literal["device_type"])
            number = int(literal["number"])
            role = str(literal["role"])
            type_codes.extend(code_for_contact(dev_type))
            op_tokens.append(f"{role}:{dev_type}")
            elements.append(contact_element(role, dev_type, number, x, y))
        for x in range(len(term), coil_x):
            elements.append(wire_element(x, y))

    type_codes.extend(code_for_output(output_type, out_dev_type))
    op_tokens.append(output_header(output_type, out_dev_type))
    elements.append(output_element(output_type, out_number, coil_x, 0))

    verticals = [f"v{{pos={coil_x},{y}}}" for y in range(1, rowsize)]
    data = f"V1:{len(type_codes)}:{':'.join(type_codes)}:{':'.join(op_tokens)}:cb{{fg=fg{{dim={width}x{rowsize}:es=[{':'.join(elements)}]"
    if verticals:
        data += f":vs=[{':'.join(verticals)}]"
    data += "}}"
    operation_count = contact_count + 1
    return data, rowsize, operation_count


def apply_edits(intermediate: Path, edits_path: Path, output: Path) -> dict[str, object]:
    payload = load_json(intermediate)
    edits = load_json(edits_path).get("edits", [])
    if not isinstance(edits, list):
        raise SystemExit("edits file must contain an 'edits' list")

    report = []
    for edit in edits:
        edit_type = edit["type"]
        if edit_type == "edit_operand":
            row = find_row(payload, edit["lddb"], float(edit["pos"]))
            before = row["raw_data"]
            before_seq = op_sequence(before)
            old = edit["old"]
            new = edit["new"]
            old_count = before.count(old)
            if old_count != 1:
                raise SystemExit(f"edit_operand requires exactly one old fragment, found {old_count}: {old}")
            after = before.replace(old, new, 1)
            after_seq = op_sequence(after)
            if before_seq != after_seq:
                raise SystemExit("edit_operand changed operation sequence; use replace_rung instead")
            row["raw_data"] = after
            row["operations"] = operation_model(after)
            row["dim"] = extract_dim(after)
            row["modified"] = True
            row["requires_stepinfo_update"] = False
            row["generated_by"] = "edit_operand"
            report.append({"type": edit_type, "lddb": edit["lddb"], "pos": edit["pos"], "stepinfo": "not_required"})
        elif edit_type == "replace_rung":
            row = find_row(payload, edit["lddb"], float(edit["pos"]))
            after, rowsize, operation_count = generate_rung(edit["logic"], edit["output"])
            row["raw_data"] = after
            row["rowsize"] = rowsize
            row["dim"] = extract_dim(after)
            row["operations"] = operation_model(after)
            row["parse_status"] = "exact"
            row["modified"] = True
            row["requires_stepinfo_update"] = True
            row["generated_by"] = "replace_rung"
            row["generated_operation_count"] = operation_count
            report.append(
                {
                    "type": edit_type,
                    "lddb": edit["lddb"],
                    "pos": edit["pos"],
                    "rowsize": rowsize,
                    "operation_count": operation_count,
                    "stepinfo": "required",
                }
            )
        elif edit_type == "insert_rung_before_end":
            db_entry = find_db(payload, edit["lddb"])
            end_rows = [candidate for candidate in db_entry["rows"] if int(candidate.get("blocktype", -1)) == 5]
            if not end_rows:
                raise SystemExit(f"END row not found in {edit['lddb']}")
            end_row = max(end_rows, key=lambda candidate: float(candidate["pos"]))
            insert_pos = float(end_row["pos"])
            pos_step = float(edit.get("pos_step", 1024))
            new_end_pos = insert_pos + pos_step
            after, rowsize, operation_count = generate_rung(edit["logic"], edit["output"])
            row_id = str(edit.get("id") or f"_guid/{uuid.uuid4()}")
            if not row_id.startswith("_guid/"):
                row_id = "_guid/" + row_id.strip("{}")
            new_row = {
                "id": row_id.lower(),
                "pos": insert_pos,
                "blocktype": 0,
                "rowsize": rowsize,
                "translated": int(edit.get("translated", 1)),
                "ConvTarget": int(edit.get("ConvTarget", 0)),
                "dim": extract_dim(after),
                "parse_status": "exact",
                "operations": operation_model(after),
                "raw_data": after,
                "modified": True,
                "inserted": True,
                "requires_stepinfo_update": True,
                "generated_by": "insert_rung_before_end",
                "generated_operation_count": operation_count,
            }
            end_row["original_pos"] = end_row["pos"]
            end_row["pos"] = new_end_pos
            end_row["modified"] = True
            end_row["moved_existing"] = True
            end_row["requires_stepinfo_update"] = True
            end_row["generated_by"] = "insert_rung_before_end_move_end"
            db_entry["rows"].append(new_row)
            db_entry["rows"].sort(key=lambda item: (float(item["pos"]), str(item["id"])))
            report.append(
                {
                    "type": edit_type,
                    "lddb": edit["lddb"],
                    "insert_pos": insert_pos,
                    "end_new_pos": new_end_pos,
                    "new_id": new_row["id"],
                    "rowsize": rowsize,
                    "operation_count": operation_count,
                    "stepinfo": "required",
                }
            )
        else:
            raise SystemExit(f"unsupported edit type: {edit_type}")

    payload.setdefault("edit_report", []).extend(report)
    write_json(output, payload)
    return payload


def row_lookup(rows: dict[str, list[dict[str, object]]]) -> dict[tuple[str, str, float], dict[str, object]]:
    return {(db, str(row["id"]), float(row["pos"])): row for db, db_rows in rows.items() for row in db_rows}


def write_ladderblocks(target_root: Path, payload: dict[str, object]) -> set[str]:
    changed_lddbs = set()
    for db in payload["dbs"]:
        modified_rows = [row for row in db["rows"] if row.get("modified")]
        if not modified_rows:
            continue
        db_path = target_root / db["lddb"]
        con = sqlite3.connect(db_path)
        con.execute("pragma journal_mode=OFF")
        con.execute("pragma synchronous=OFF")
        ordered_rows = sorted(modified_rows, key=lambda item: (1 if item.get("inserted") else 0, float(item.get("pos", 0))))
        for row in ordered_rows:
            if row.get("inserted"):
                con.execute(
                    """
                    insert into LadderBlocks(id, pos, blocktype, data, rowsize, translated, ConvTarget)
                    values (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["id"],
                        row["pos"],
                        row["blocktype"],
                        row["raw_data"],
                        row["rowsize"],
                        row["translated"],
                        row["ConvTarget"],
                    ),
                )
            else:
                con.execute(
                    """
                    update LadderBlocks
                       set pos=?,
                           blocktype=?,
                           data=?,
                           rowsize=?,
                           translated=?,
                           ConvTarget=?
                     where id=? and pos=?
                    """,
                    (
                        row["pos"],
                        row["blocktype"],
                        row["raw_data"],
                        row["rowsize"],
                        row["translated"],
                        row["ConvTarget"],
                        row["id"],
                        row.get("original_pos", row["pos"]),
                    ),
                )
            changed_lddbs.add(db["lddb"])
        con.commit()
        con.close()
    return changed_lddbs


def compare_intermediate_to_source(payload: dict[str, object]) -> dict[str, object]:
    source_root = Path(payload["source"]["source_root"])
    if not source_root.exists():
        return {"skipped": f"source_root not found: {source_root}"}
    source_rows = read_ladder_rows(source_root)
    missing = []
    mismatches = []
    checked = 0
    skipped_modified = 0
    for db in payload["dbs"]:
        lddb = db["lddb"]
        source_map = {(str(row["id"]), float(row["pos"])): row for row in source_rows.get(lddb, [])}
        for row in db["rows"]:
            if row.get("modified"):
                skipped_modified += 1
                continue
            key = (str(row["id"]), float(row["pos"]))
            source_row = source_map.get(key)
            if source_row is None:
                missing.append({"lddb": lddb, "id": row["id"], "pos": row["pos"]})
                continue
            checked += 1
            if str(source_row["data"]) != str(row["raw_data"]):
                mismatches.append({"lddb": lddb, "id": row["id"], "pos": row["pos"]})
    return {
        "checked_unmodified_rows": checked,
        "skipped_modified_rows": skipped_modified,
        "missing_rows": missing,
        "raw_data_mismatches": mismatches,
    }


def update_stepinfo_for_row(target_root: Path, db_entry: dict[str, object], row: dict[str, object]) -> None:
    stepinfo = db_entry.get("stepinfo")
    if not stepinfo:
        raise SystemExit(f"StepInfo DB not known for {db_entry['lddb']}")
    step_path = target_root / str(stepinfo)
    block_id = guid_to_blockid(str(row["id"]))
    new_count = int(row.get("generated_operation_count") or len(operation_model(str(row["raw_data"]))))
    con = sqlite3.connect(step_path)
    con.execute("pragma journal_mode=OFF")
    con.execute("pragma synchronous=OFF")
    block = con.execute("select Pos, BlockID, StepSize, BodyID from T_Block where BlockID=?", (block_id,)).fetchone()
    if not block:
        con.close()
        raise SystemExit(f"T_Block not found: {stepinfo} {block_id}")
    _pos, _block_id, old_step_size, body_id = block
    old_count = con.execute("select count(*) from T_Step where BlockID=?", (block_id,)).fetchone()[0]
    new_step_size = int(old_step_size) - int(old_count) + new_count
    con.execute("update T_Block set Pos=?, StepSize=?, BodyID=? where BlockID=?", (row["pos"], new_step_size, body_id, block_id))
    con.execute("delete from T_Step where BlockID=?", (block_id,))
    for mil_id in range(new_count):
        con.execute(
            "insert into T_Step(Pos, ElementIDMacroFB, BlockID, MilID, StepSize) values (?, 0, ?, ?, 1)",
            (row["pos"], block_id, mil_id),
        )
    con.commit()
    con.close()


def insert_stepinfo_for_row(target_root: Path, db_entry: dict[str, object], row: dict[str, object], end_row: dict[str, object]) -> None:
    stepinfo = db_entry.get("stepinfo")
    if not stepinfo:
        raise SystemExit(f"StepInfo DB not known for {db_entry['lddb']}")
    step_path = target_root / str(stepinfo)
    new_block_id = guid_to_blockid(str(row["id"]))
    end_block_id = guid_to_blockid(str(end_row["id"]))
    new_count = int(row.get("generated_operation_count") or len(operation_model(str(row["raw_data"]))))
    con = sqlite3.connect(step_path)
    con.execute("pragma journal_mode=OFF")
    con.execute("pragma synchronous=OFF")
    if con.execute("select 1 from T_Block where BlockID=?", (new_block_id,)).fetchone():
        con.close()
        raise SystemExit(f"T_Block already exists: {new_block_id}")
    end_block = con.execute("select Pos, BlockID, StepSize, BodyID from T_Block where BlockID=?", (end_block_id,)).fetchone()
    if not end_block:
        con.close()
        raise SystemExit(f"END T_Block not found: {end_block_id}")
    _end_pos, _end_block_id, old_end_step_size, body_id = end_block
    new_step_size = int(row.get("stepinfo_step_size") or int(old_end_step_size) + 1)
    con.execute("insert into T_Block(Pos, BlockID, StepSize, BodyID) values (?, ?, ?, ?)", (row["pos"], new_block_id, new_step_size, body_id))
    for mil_id in range(new_count):
        con.execute(
            "insert into T_Step(Pos, ElementIDMacroFB, BlockID, MilID, StepSize) values (?, 0, ?, ?, 1)",
            (row["pos"], new_block_id, mil_id),
        )
    con.commit()
    con.close()


def move_stepinfo_block(target_root: Path, db_entry: dict[str, object], row: dict[str, object]) -> None:
    stepinfo = db_entry.get("stepinfo")
    if not stepinfo:
        raise SystemExit(f"StepInfo DB not known for {db_entry['lddb']}")
    step_path = target_root / str(stepinfo)
    block_id = guid_to_blockid(str(row["id"]))
    con = sqlite3.connect(step_path)
    con.execute("pragma journal_mode=OFF")
    con.execute("pragma synchronous=OFF")
    first_step = con.execute("select StepSize from T_Step where BlockID=? order by MilID limit 1", (block_id,)).fetchone()
    step_size = int(first_step[0]) if first_step else int(row.get("rowsize") or 0)
    con.execute("update T_Block set Pos=?, StepSize=? where BlockID=?", (row["pos"], step_size, block_id))
    con.execute("update T_Step set Pos=? where BlockID=?", (row["pos"], block_id))
    con.commit()
    con.close()


def integrity_check(path: Path) -> str:
    con = sqlite3.connect(path)
    value = con.execute("pragma integrity_check").fetchone()[0]
    con.close()
    return value


def changed_files_by_hash(source_root: Path, target_root: Path) -> list[str]:
    changed = []
    for source_path in sorted(source_root.rglob("*")):
        if not source_path.is_file():
            continue
        rel = source_path.relative_to(source_root)
        target_path = target_root / rel
        if not target_path.exists():
            changed.append(f"{rel}:missing_in_target")
            continue
        if hashlib.sha256(source_path.read_bytes()).hexdigest() != hashlib.sha256(target_path.read_bytes()).hexdigest():
            changed.append(str(rel))
    for target_path in sorted(target_root.rglob("*")):
        if not target_path.is_file():
            continue
        rel = target_path.relative_to(target_root)
        if not (source_root / rel).exists():
            changed.append(f"{rel}:new_in_target")
    return changed


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_diff_input(path: Path, prefix: str) -> Path:
    if path.is_dir():
        return path
    if not path.is_file():
        raise SystemExit(f"diff input not found: {path}")
    target = unique_path(prefix)
    target.mkdir(parents=True, exist_ok=False)
    with zipfile.ZipFile(path, "r") as zf:
        zf.extractall(target)
    return target


def file_set_diff(left_root: Path, right_root: Path) -> dict[str, object]:
    left_files = {str(path.relative_to(left_root)): path for path in left_root.rglob("*") if path.is_file()}
    right_files = {str(path.relative_to(right_root)): path for path in right_root.rglob("*") if path.is_file()}
    only_left = sorted(set(left_files) - set(right_files))
    only_right = sorted(set(right_files) - set(left_files))
    changed = []
    for rel in sorted(set(left_files) & set(right_files)):
        if file_sha256(left_files[rel]) != file_sha256(right_files[rel]):
            changed.append(rel)
    return {"only_left": only_left, "only_right": only_right, "changed": changed}


def compare_lddb_rows(left_root: Path, right_root: Path, changed_files: list[str]) -> list[dict[str, object]]:
    diffs = []
    lddb_files = sorted({name for name in changed_files if name.endswith("_LDDB.db")})
    for rel in lddb_files:
        left_rows = read_ladder_rows(left_root).get(Path(rel).name, [])
        right_rows = read_ladder_rows(right_root).get(Path(rel).name, [])
        left_map = {(str(row["id"]), float(row["pos"])): row for row in left_rows}
        right_map = {(str(row["id"]), float(row["pos"])): row for row in right_rows}
        for key in sorted(set(left_map) | set(right_map), key=lambda item: (item[1], item[0])):
            left_row = left_map.get(key)
            right_row = right_map.get(key)
            if left_row is None:
                diffs.append({"lddb": rel, "id": key[0], "pos": key[1], "change": "added"})
                continue
            if right_row is None:
                diffs.append({"lddb": rel, "id": key[0], "pos": key[1], "change": "removed"})
                continue
            changed_fields = []
            for field in ["blocktype", "rowsize", "translated", "ConvTarget", "data"]:
                if left_row.get(field) != right_row.get(field):
                    changed_fields.append(field)
            if changed_fields:
                entry = {
                    "lddb": rel,
                    "id": key[0],
                    "pos": key[1],
                    "change": "modified",
                    "fields": changed_fields,
                    "left_rowsize": left_row.get("rowsize"),
                    "right_rowsize": right_row.get("rowsize"),
                    "left_dim": extract_dim(str(left_row.get("data", ""))) if left_row.get("blocktype") == 0 else "",
                    "right_dim": extract_dim(str(right_row.get("data", ""))) if right_row.get("blocktype") == 0 else "",
                    "left_ops": op_sequence(str(left_row.get("data", ""))) if left_row.get("blocktype") == 0 else [],
                    "right_ops": op_sequence(str(right_row.get("data", ""))) if right_row.get("blocktype") == 0 else [],
                }
                diffs.append(entry)
    return diffs


def read_table_rows(db_path: Path, table: str) -> list[tuple]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        columns = [row[1] for row in con.execute(f"pragma table_info({table})")]
        if not columns:
            return []
        order = ", ".join(columns)
        rows = con.execute(f"select * from {table} order by {order}").fetchall()
        return rows
    finally:
        con.close()


def compare_stepinfo(left_root: Path, right_root: Path, changed_files: list[str]) -> list[dict[str, object]]:
    diffs = []
    step_files = sorted({name for name in changed_files if name.endswith("_StepInfo.db")})
    for rel in step_files:
        for table in ["T_Block", "T_Step", "T_SourcePos"]:
            left_rows = read_table_rows(left_root / rel, table)
            right_rows = read_table_rows(right_root / rel, table)
            if left_rows == right_rows:
                continue
            diffs.append(
                {
                    "stepinfo": rel,
                    "table": table,
                    "left_count": len(left_rows),
                    "right_count": len(right_rows),
                    "changed": True,
                }
            )
    return diffs


def read_stepinfo_blocks(root: Path, stepinfo: str) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    step_path = root / stepinfo
    con = sqlite3.connect(f"file:{step_path}?mode=ro", uri=True)
    con.text_factory = bytes
    try:
        blocks = []
        for pos, block_id, step_size, body_id in con.execute("select Pos, BlockID, StepSize, BodyID from T_Block order by Pos, BlockID"):
            blocks.append(
                {
                    "pos": pos,
                    "block_id": decode_data(block_id),
                    "guid": normalize_guid(block_id),
                    "step_size": safe_int(step_size),
                    "body_id": safe_int(body_id),
                }
            )
        steps = []
        for pos, _element_id, block_id, mil_id, step_size in con.execute(
            "select Pos, ElementIDMacroFB, BlockID, MilID, StepSize from T_Step order by Pos, BlockID, MilID"
        ):
            steps.append(
                {
                    "pos": pos,
                    "block_id": decode_data(block_id),
                    "guid": normalize_guid(block_id),
                    "mil_id": safe_int(mil_id),
                    "step_size": safe_int(step_size),
                }
            )
    finally:
        con.close()
    return blocks, steps


def pcode_path_for_stepinfo(root: Path, stepinfo: str) -> Path:
    step_id = stepinfo.removesuffix("_StepInfo.db")
    return convertdata_path(root, step_id, "PouPCode.pcode")


def pcode_record_start(data: bytes, guid: str) -> int | None:
    guid_offset = data.find(guid_bytes_le(guid))
    if guid_offset < 5:
        return None
    start = guid_offset - 5
    length = int.from_bytes(data[start : start + 4], "little")
    if data[start + 4] != 0x11:
        return None
    if length < 21 or start + length > len(data):
        return None
    return start


def pcode_record_for_guid(data: bytes, guid: str) -> bytes | None:
    start = pcode_record_start(data, guid)
    if start is None:
        return None
    length = int.from_bytes(data[start : start + 4], "little")
    return data[start : start + length]


def replace_pcode_record_guid(record: bytes, new_guid: str) -> bytes:
    if len(record) < 21 or record[4] != 0x11:
        raise ValueError("invalid PouPCode record")
    return record[:5] + guid_bytes_le(new_guid) + record[21:]


def sync_pcode_guid_records(root: Path) -> list[dict[str, object]]:
    ladder_rows = read_ladder_rows(root)
    stepinfo_map = find_stepinfo_map(root, ladder_rows)
    actions: list[dict[str, object]] = []
    for lddb, rows in sorted(ladder_rows.items()):
        stepinfo = stepinfo_map.get(lddb, "")
        if not stepinfo:
            continue
        pcode_path = pcode_path_for_stepinfo(root, stepinfo)
        if not pcode_path.exists():
            continue
        data = pcode_path.read_bytes()
        sorted_rows = sorted(rows, key=lambda row: (float(row["pos"]), str(row["id"])))
        changed = False

        for index, row in enumerate(sorted_rows):
            guid = normalize_guid(row.get("id"))
            if guid is None or pcode_record_start(data, guid) is not None:
                continue

            insert_before = None
            insert_start = None
            for candidate in sorted_rows[index + 1 :]:
                candidate_guid = normalize_guid(candidate.get("id"))
                if candidate_guid is None:
                    continue
                candidate_start = pcode_record_start(data, candidate_guid)
                if candidate_start is not None:
                    insert_before = candidate_guid
                    insert_start = candidate_start
                    break
            if insert_start is None:
                insert_start = len(data)

            template_guid = None
            template_record = None
            preferred = list(reversed(sorted_rows[:index])) + sorted_rows[index + 1 :]
            for candidate in preferred:
                if safe_int(candidate.get("blocktype")) != safe_int(row.get("blocktype")):
                    continue
                candidate_guid = normalize_guid(candidate.get("id"))
                if candidate_guid is None:
                    continue
                candidate_record = pcode_record_for_guid(data, candidate_guid)
                if candidate_record is not None:
                    template_guid = candidate_guid
                    template_record = candidate_record
                    break
            if template_record is None:
                for candidate in preferred:
                    candidate_guid = normalize_guid(candidate.get("id"))
                    if candidate_guid is None:
                        continue
                    candidate_record = pcode_record_for_guid(data, candidate_guid)
                    if candidate_record is not None:
                        template_guid = candidate_guid
                        template_record = candidate_record
                        break
            if template_record is None:
                actions.append(
                    {
                        "lddb": lddb,
                        "stepinfo": stepinfo,
                        "pcode": str(pcode_path.relative_to(root)),
                        "guid": guid,
                        "action": "failed",
                        "reason": "no_template_record",
                    }
                )
                continue

            new_record = replace_pcode_record_guid(template_record, guid)
            data = data[:insert_start] + new_record + data[insert_start:]
            changed = True
            actions.append(
                {
                    "lddb": lddb,
                    "stepinfo": stepinfo,
                    "pcode": str(pcode_path.relative_to(root)),
                    "guid": guid,
                    "pos": row.get("pos"),
                    "blocktype": row.get("blocktype"),
                    "action": "inserted_pcode_record",
                    "insert_before_guid": insert_before,
                    "template_guid": template_guid,
                    "record_length": len(new_record),
                }
            )

        if changed:
            pcode_path.write_bytes(data)
    return actions


def check_one_lddb_guid_consistency(root: Path, lddb: str, rows: list[dict[str, object]], stepinfo: str) -> dict[str, object]:
    row_entries = []
    invalid_lddb_ids = []
    duplicate_lddb_ids = []
    seen = set()
    for row in rows:
        guid = normalize_guid(row.get("id"))
        if guid is None:
            invalid_lddb_ids.append({"id": row.get("id"), "pos": row.get("pos")})
            continue
        if guid in seen:
            duplicate_lddb_ids.append(guid)
        seen.add(guid)
        row_entries.append(
            {
                "guid": guid,
                "id": row.get("id"),
                "pos": float(row["pos"]),
                "blocktype": row.get("blocktype"),
            }
        )

    step_blocks, step_steps = read_stepinfo_blocks(root, stepinfo)
    step_map = {str(block["guid"]): block for block in step_blocks if block.get("guid")}
    step_guids = set(step_map)
    lddb_guids = {entry["guid"] for entry in row_entries}
    missing_in_stepinfo = []
    pos_mismatches = []
    for entry in row_entries:
        block = step_map.get(str(entry["guid"]))
        if block is None:
            missing_in_stepinfo.append(entry)
            continue
        if float(block["pos"]) != float(entry["pos"]):
            pos_mismatches.append(
                {
                    "guid": entry["guid"],
                    "lddb_pos": entry["pos"],
                    "stepinfo_pos": block["pos"],
                    "blocktype": entry["blocktype"],
                }
            )

    extra_in_stepinfo = [block for block in step_blocks if block.get("guid") not in lddb_guids]
    step_block_guids = {block["guid"] for block in step_blocks if block.get("guid")}
    orphan_steps = [step for step in step_steps if step.get("guid") not in step_block_guids]

    pcode_path = pcode_path_for_stepinfo(root, stepinfo)
    pcode_missing = []
    pcode_offsets = []
    pcode_order_violations = []
    if pcode_path.exists():
        pcode = pcode_path.read_bytes()
        previous_offset = -1
        for entry in row_entries:
            offset = pcode.find(guid_bytes_le(str(entry["guid"])))
            if offset < 0:
                pcode_missing.append(entry)
                continue
            pcode_offsets.append({"guid": entry["guid"], "pos": entry["pos"], "blocktype": entry["blocktype"], "offset": offset})
            if offset < previous_offset:
                pcode_order_violations.append(
                    {
                        "guid": entry["guid"],
                        "pos": entry["pos"],
                        "blocktype": entry["blocktype"],
                        "offset": offset,
                        "previous_offset": previous_offset,
                    }
                )
            previous_offset = offset
    else:
        pcode = b""

    issue_count = (
        len(invalid_lddb_ids)
        + len(duplicate_lddb_ids)
        + len(missing_in_stepinfo)
        + len(extra_in_stepinfo)
        + len(pos_mismatches)
        + len(orphan_steps)
        + len(pcode_missing)
        + len(pcode_order_violations)
        + (0 if pcode_path.exists() else 1)
    )
    return {
        "lddb": lddb,
        "stepinfo": stepinfo,
        "pcode": str(pcode_path.relative_to(root)) if pcode_path.exists() else str(pcode_path),
        "status": "ok" if issue_count == 0 else "ng",
        "counts": {
            "lddb_blocks": len(row_entries),
            "stepinfo_blocks": len(step_blocks),
            "stepinfo_steps": len(step_steps),
            "pcode_size": len(pcode),
            "pcode_guid_hits": len(pcode_offsets),
        },
        "issues": {
            "invalid_lddb_ids": invalid_lddb_ids,
            "duplicate_lddb_ids": sorted(set(duplicate_lddb_ids)),
            "missing_in_stepinfo": missing_in_stepinfo,
            "extra_in_stepinfo": extra_in_stepinfo,
            "pos_mismatches": pos_mismatches,
            "orphan_step_rows": orphan_steps,
            "missing_in_pcode": pcode_missing,
            "pcode_order_violations": pcode_order_violations,
            "missing_pcode_file": [] if pcode_path.exists() else [str(pcode_path)],
        },
    }


def check_guid_consistency(input_path: Path, extract_dir: Path | None = None) -> dict[str, object]:
    root = ensure_source_root(input_path, extract_dir)
    ladder_rows = read_ladder_rows(root)
    stepinfo_map = find_stepinfo_map(root, ladder_rows)
    reports = []
    missing_stepinfo = []
    for lddb, rows in sorted(ladder_rows.items()):
        stepinfo = stepinfo_map.get(lddb, "")
        if not stepinfo:
            missing_stepinfo.append({"lddb": lddb, "lddb_blocks": len(rows)})
            continue
        reports.append(check_one_lddb_guid_consistency(root, lddb, rows, stepinfo))

    ng_reports = [report for report in reports if report["status"] != "ok"]
    total_issues = len(missing_stepinfo)
    for report in reports:
        for values in report["issues"].values():
            total_issues += len(values)

    return {
        "input": str(input_path),
        "project_root": str(root),
        "status": "ok" if total_issues == 0 else "ng",
        "summary": {
            "lddb_files": len(ladder_rows),
            "checked_lddb_files": len(reports),
            "missing_stepinfo_files": len(missing_stepinfo),
            "ng_lddb_files": len(ng_reports),
            "total_issues": total_issues,
        },
        "missing_stepinfo": missing_stepinfo,
        "reports": reports,
    }


def diff_projects(left: Path, right: Path) -> dict[str, object]:
    left_root = resolve_diff_input(left, f"_project_diff_left_{left.stem}")
    right_root = resolve_diff_input(right, f"_project_diff_right_{right.stem}")
    files = file_set_diff(left_root, right_root)
    changed = list(files["changed"])
    return {
        "left_root": str(left_root),
        "right_root": str(right_root),
        "file_diff": files,
        "lddb_diffs": compare_lddb_rows(left_root, right_root, changed),
        "stepinfo_diffs": compare_stepinfo(left_root, right_root, changed),
    }


def make_zip(target_root: Path, output: Path) -> str | None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(target_root.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(target_root).as_posix())
    with zipfile.ZipFile(output, "r") as zf:
        return zf.testzip()


def build_gx3(intermediate: Path, output: Path | None, target_dir: Path | None, sync_pcode_guids: bool = False) -> dict[str, object]:
    payload = load_json(intermediate)
    source_root = Path(payload["source"]["source_root"])
    if not source_root.exists():
        raise SystemExit(f"source_root not found: {source_root}")
    if target_dir is None:
        target_dir = unique_path(f"_project_build_{intermediate.stem}")
    shutil.copytree(source_root, target_dir)

    changed_lddbs = write_ladderblocks(target_dir, payload)
    changed_stepinfos = set()
    for db in payload["dbs"]:
        moved_end_rows = [row for row in db["rows"] if row.get("moved_existing") and int(row.get("blocktype", -1)) == 5]
        for row in db["rows"]:
            if row.get("inserted") and row.get("requires_stepinfo_update"):
                if not moved_end_rows:
                    raise SystemExit(f"inserted row has no moved END row: {db['lddb']}")
                insert_stepinfo_for_row(target_dir, db, row, moved_end_rows[0])
                if db.get("stepinfo"):
                    changed_stepinfos.add(str(db["stepinfo"]))
            elif row.get("moved_existing") and row.get("requires_stepinfo_update"):
                move_stepinfo_block(target_dir, db, row)
                if db.get("stepinfo"):
                    changed_stepinfos.add(str(db["stepinfo"]))
            elif row.get("modified") and row.get("requires_stepinfo_update"):
                update_stepinfo_for_row(target_dir, db, row)
                if db.get("stepinfo"):
                    changed_stepinfos.add(str(db["stepinfo"]))

    pcode_guid_sync = sync_pcode_guid_records(target_dir) if sync_pcode_guids else []

    checks = {}
    for name in sorted(changed_lddbs | changed_stepinfos):
        checks[name] = integrity_check(target_dir / name)

    if output is None:
        output = Path(f"{intermediate.stem}_built_{time.strftime('%Y%m%d_%H%M%S')}.gx3")
    bad_member = make_zip(target_dir, output)
    changed_files = changed_files_by_hash(source_root, target_dir)
    report = {
        "target_dir": str(target_dir),
        "output": str(output),
        "zip_test_bad_member": bad_member,
        "changed_lddbs": sorted(changed_lddbs),
        "changed_stepinfos": sorted(changed_stepinfos),
        "pcode_guid_sync": pcode_guid_sync,
        "changed_files": changed_files,
        "integrity": checks,
    }
    report_path = output.with_suffix(output.suffix + ".build_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def sync_pcode_guids_project(input_path: Path, output: Path | None, target_dir: Path | None) -> dict[str, object]:
    if target_dir is None:
        target_dir = unique_path(f"_project_sync_pcode_{input_path.stem}")
    if target_dir.exists():
        raise SystemExit(f"target_dir already exists: {target_dir}")
    if input_path.is_dir():
        shutil.copytree(input_path, target_dir)
    elif input_path.is_file():
        target_dir.mkdir(parents=True, exist_ok=False)
        with zipfile.ZipFile(input_path, "r") as zf:
            zf.extractall(target_dir)
    else:
        raise SystemExit(f"input not found: {input_path}")

    actions = sync_pcode_guid_records(target_dir)
    if output is None:
        output = Path(f"{input_path.stem}_pcode_guid_sync.gx3")
    bad_member = make_zip(target_dir, output)
    check = check_guid_consistency(target_dir)
    report = {
        "target_dir": str(target_dir),
        "output": str(output),
        "zip_test_bad_member": bad_member,
        "pcode_guid_sync": actions,
        "guid_check_status": check["status"],
        "guid_check_summary": check["summary"],
    }
    report_path = output.with_suffix(output.suffix + ".sync_pcode_report.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def validate_intermediate(intermediate: Path, roundtrip: bool = True) -> dict[str, object]:
    payload = load_json(intermediate)
    rows = 0
    modified = 0
    parse_status = {}
    for db in payload["dbs"]:
        for row in db["rows"]:
            rows += 1
            if row.get("modified"):
                modified += 1
            status = str(row.get("parse_status", ""))
            parse_status[status] = parse_status.get(status, 0) + 1

    source_compare = compare_intermediate_to_source(payload)
    result = {"rows": rows, "modified_rows": modified, "parse_status": parse_status, "source_compare": source_compare}
    if roundtrip:
        source_root = Path(payload["source"]["source_root"])
        if source_root.exists():
            target = unique_path(f"_project_validate_roundtrip_{intermediate.stem}")
            report = build_gx3(intermediate, None, target)
            result["roundtrip_build"] = report
        else:
            result["roundtrip_build"] = {"skipped": f"source_root not found: {source_root}"}
    return result


def inspect_row(intermediate: Path, lddb: str, pos: float) -> dict[str, object]:
    payload = load_json(intermediate)
    row = find_row(payload, lddb, pos)
    return {
        "lddb": lddb,
        "pos": pos,
        "id": row["id"],
        "rowsize": row["rowsize"],
        "dim": row.get("dim", ""),
        "parse_status": row.get("parse_status", ""),
        "modified": row.get("modified", False),
        "operations": row.get("operations", []),
        "raw_data": row.get("raw_data", ""),
    }


def cmd_export(args) -> None:
    payload = export_payload(Path(args.input), Path(args.output), Path(args.extract_dir) if args.extract_dir else None)
    print(f"Wrote {args.output}")
    print(json.dumps(payload["stats"], ensure_ascii=False, indent=2))


def cmd_validate(args) -> None:
    result = validate_intermediate(Path(args.intermediate), roundtrip=not args.no_roundtrip)
    out = Path(args.output) if args.output else Path(str(args.intermediate) + ".validate_report.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_apply(args) -> None:
    apply_edits(Path(args.intermediate), Path(args.edits), Path(args.output))
    print(f"Wrote {args.output}")


def cmd_build(args) -> None:
    report = build_gx3(
        Path(args.intermediate),
        Path(args.output) if args.output else None,
        Path(args.target_dir) if args.target_dir else None,
        sync_pcode_guids=args.sync_pcode_guids,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def cmd_inspect(args) -> None:
    result = inspect_row(Path(args.intermediate), args.lddb, float(args.pos))
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_diff(args) -> None:
    result = diff_projects(Path(args.left), Path(args.right))
    out = Path(args.output) if args.output else default_output_path("diff_report", "json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(json.dumps(result, ensure_ascii=False, indent=2))


def cmd_check_guids(args) -> None:
    result = check_guid_consistency(Path(args.input), Path(args.extract_dir) if args.extract_dir else None)
    out = Path(args.output) if args.output else default_output_path("guid_consistency_report", "json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {out}")
    print(json.dumps({"status": result["status"], "summary": result["summary"]}, ensure_ascii=False, indent=2))
    if args.fail_on_issues and result["status"] != "ok":
        raise SystemExit(1)


def cmd_sync_pcode_guids(args) -> None:
    report = sync_pcode_guids_project(
        Path(args.input),
        Path(args.output) if args.output else None,
        Path(args.target_dir) if args.target_dir else None,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ladder intermediate export/edit/build tool")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("export", help="export a project to intermediate JSON")
    p.add_argument("input")
    p.add_argument("-o", "--output", default=str(default_output_path("ladder_intermediate", "json")))
    p.add_argument("--extract-dir")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("validate", help="validate intermediate JSON and optionally build a roundtrip project")
    p.add_argument("intermediate")
    p.add_argument("-o", "--output")
    p.add_argument("--no-roundtrip", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("apply", help="apply edit JSON to intermediate JSON")
    p.add_argument("intermediate")
    p.add_argument("edits")
    p.add_argument("-o", "--output", required=True)
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("build", help="build a project from intermediate JSON")
    p.add_argument("intermediate")
    p.add_argument("-o", "--output")
    p.add_argument("--target-dir")
    p.add_argument("--sync-pcode-guids", action="store_true", help="experimental: insert missing PouPCode GUID records")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("inspect", help="inspect one ladder row from intermediate JSON")
    p.add_argument("intermediate")
    p.add_argument("--lddb", required=True)
    p.add_argument("--pos", required=True)
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("diff", help="diff two project files or extracted project folders")
    p.add_argument("left")
    p.add_argument("right")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_diff)

    p = sub.add_parser("check-guids", help="check LDDB / StepInfo / PouPCode block GUID consistency")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--extract-dir")
    p.add_argument("--fail-on-issues", action="store_true")
    p.set_defaults(func=cmd_check_guids)

    p = sub.add_parser("sync-pcode-guids", help="experimental: copy a project and insert missing PouPCode GUID records")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.add_argument("--target-dir")
    p.set_defaults(func=cmd_sync_pcode_guids)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
