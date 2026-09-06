from __future__ import annotations

"""Semantic diff between two GX Works3 projects.

Ladder rows are matched by their stable ``_guid/...`` block id, so moving a
circuit does not show as a change. A changed row is classified as:
- logic          operands, wiring, execution metadata, or other row data differ
- layout-only    only the outer canvas size differs (hidden by default)

Comparison is conservative: moving elements or rerouting wires is reported
even when it might preserve the Boolean function. Coordinates encode wiring,
so they cannot be discarded as cosmetic without proving equivalence.

The command also compares configuration through the existing project readers:
- project-config CPU and unit extraction
- exec-config CPU.PRM execution order extraction
- module-params intelligent-function-module setting decoder
- dm-probe initial/retained device-memory decoder

Configuration availability is explicit. ``missing``, ``unreadable`` and
``unsupported`` are never collapsed into ``same``.

Also reports POUs (LDDB files) added/removed and device-comment changes.

Inputs may be extracted folders or ``.gx3`` files (extracted to a temp dir).
"""

import argparse
import csv
import json
import re
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

from gx3cli.gx3_device_name import format_device as _format_device
from gx3cli.gx3_arg_decode import DecodedOperation, parse_row_operations
from gx3cli.gx3_analysis_state import AnalysisState, DECODE, PARTIAL
from gx3cli.gx3_operand_parse import CONST_VALUE_RE
from gx3cli.gx3_intermediate_tool import decode_data
from gx3cli.gx3_program_map import load_program_map
from gx3cli.review_gx3_project import extract_title, load_comments_for_root
from gx3cli.gx3_project_config import cpu_section, units_section
from gx3cli.gx3_exec_config import program_file_names
from gx3cli.gx3_module_params import MODULE_DB_RE, read_module
from gx3cli.gx3_dm_probe import decode_memory_db, load_comment_map


CONFIG_SECTION_ORDER = (
    "project-config.cpu",
    "project-config.units",
    "project-config.execution",
    "module-params",
    "device-memory",
)
CONFIG_UNAVAILABLE_PRIORITY = {"ok": 0, "missing": 1, "unsupported": 2, "unreadable": 3}


def ensure_root(path_text: str, tmp: list[tempfile.TemporaryDirectory]) -> Path:
    path = Path(path_text)
    if path.is_dir():
        return path
    if path.suffix.lower() == ".gx3" and path.is_file():
        td = tempfile.TemporaryDirectory(prefix="gx3diff_")
        tmp.append(td)
        with zipfile.ZipFile(path) as zf:
            zf.extractall(td.name)
        return Path(td.name)
    raise SystemExit(f"not a folder or .gx3 file: {path}")


def logic_signature(data: str) -> str:
    """Preserve everything except the known outer drawing extent.

    Decoded operations alone lose vertical/horizontal wires, element positions,
    ct execution flags, and unresolved operands. Equal decoded lists therefore
    do not prove equivalent circuits. Keep raw data, including unknown fields,
    and normalize only the canvas dimensions (not nested operand metadata).
    """
    return re.sub(r"(:cb\{fg=fg\{dim=)\d+x\d+(?=:)", r"\1<canvas>", data, count=1)


def load_side(root: Path) -> tuple[dict[str, dict[str, tuple[int, str, str]]], dict[str, str]]:
    """Return ({lddb_hex: {guid: (pos, data, title)}}, {lddb_hex: pou_name})."""
    rows: dict[str, dict[str, tuple[int, str, str]]] = {}
    for db_path in sorted(root.glob("*_LDDB.db")):
        hexid = db_path.name.split("_")[0]
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        con.text_factory = bytes
        per: dict[str, tuple[int, str, str]] = {}
        current_title = ""
        for bid, pos, blocktype, data in con.execute(
            "select id, pos, blocktype, data from LadderBlocks order by pos, id"
        ):
            sid = decode_data(bid)
            text = decode_data(data)
            bt = int(blocktype)
            if bt in {1, 2}:
                t = extract_title(text)
                if t:
                    current_title = t
            if bt != 0 or not sid.startswith("_guid/"):
                continue
            per[sid] = (int(float(pos)), text, current_title)
        con.close()
        rows[hexid] = per
    pm = load_program_map(root)
    names = {hexid: (info.name or hexid) for hexid, info in pm.pous.items()}
    return rows, names


def summarize_change(old_data: str, new_data: str) -> str:
    old_operations, old_status = parse_row_operations(old_data)
    new_operations, new_status = parse_row_operations(new_data)

    def decoded_args(operations: list[DecodedOperation]) -> list[str]:
        args: list[str] = []
        for operation in operations:
            for index, raw in enumerate(operation.raw_args):
                if raw.startswith("c{"):
                    match = CONST_VALUE_RE.search(raw)
                    args.append(f"K{match.group(1) if match else '?'}")
                    continue
                devices = sorted(occ.device for occ in operation.args if occ.arg_index == index)
                args.extend(devices or ["?"])
        return args

    parts = []
    o_ops = [operation.role for operation in old_operations]
    n_ops = [operation.role for operation in new_operations]
    if o_ops != n_ops:
        parts.append(f"ops {' '.join(o_ops[:12])} -> {' '.join(n_ops[:12])}")
    o_args, n_args = decoded_args(old_operations), decoded_args(new_operations)
    if o_args != n_args:
        removed = [x for x in o_args if x not in n_args]
        added = [x for x in n_args if x not in o_args]
        if removed or added:
            parts.append(f"args -{removed[:8]} +{added[:8]}")
    summary = "; ".join(parts) or "wiring, execution metadata, or raw operand data changed; review rung"
    if old_status != "exact" or new_status != "exact":
        state = AnalysisState(
            PARTIAL, stage=DECODE,
            reason=f"decoded summary only (old={old_status}, new={new_status}); unparsed changes may be omitted from this summary",
            next_step="inspect the raw rung and parse-gaps in both project versions",
        )
        summary += "; " + state.line("summary-scope")
    return summary


def comment_map(root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for (dev_type, number), info in load_comments_for_root(root).items():
        text = info.japanese or info.english or info.all_text
        if text:
            out[_format_device(dev_type, number)] = text
    return out


def _config_payload(state: str, data: object | None = None, detail: str = "") -> dict[str, object]:
    return {"state": state, "data": {} if data is None else data, "detail": detail}


def _normalize_units(units: object) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for unit in units if isinstance(units, list) else []:
        if not isinstance(unit, dict):
            continue
        rows.append(
            {
                "unit_name": unit.get("unit_name", ""),
                "base": unit.get("base", ""),
                "slot": unit.get("slot", ""),
                "head_io": unit.get("head_io", ""),
                "head_io_hex": unit.get("head_io_hex", ""),
                "station": unit.get("station", ""),
            }
        )
    return sorted(
        rows,
        key=lambda row: tuple(
            str(row[key]) for key in ("base", "slot", "unit_name", "head_io_hex", "station")
        ),
    )


def _collect_project_config(root: Path) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}

    config_xml = root / "Config.xml"
    if not config_xml.exists():
        out["project-config.cpu"] = _config_payload("missing", detail="Config.xml is absent")
    else:
        try:
            section = cpu_section(root)
            model = str(section.data.get("cpu_model", ""))
            if model:
                out["project-config.cpu"] = _config_payload("ok", {"cpu_model": model})
            else:
                out["project-config.cpu"] = _config_payload(
                    "unsupported",
                    {"cpu_model": ""},
                    "Config.xml exists but its CPU Unit field was not decoded",
                )
        except (OSError, sqlite3.Error, UnicodeError, ValueError) as exc:
            out["project-config.cpu"] = _config_payload("unreadable", detail=str(exc))

    unit_config = root / "UnitConfig.dat"
    if not unit_config.exists():
        out["project-config.units"] = _config_payload("missing", detail="UnitConfig.dat is absent")
    else:
        try:
            section = units_section(root)
            out["project-config.units"] = _config_payload(
                "ok", {"units": _normalize_units(section.data.get("units", []))}
            )
        except (OSError, sqlite3.Error, UnicodeError, ValueError) as exc:
            out["project-config.units"] = _config_payload("unreadable", detail=str(exc))

    cpu_prm = root / "CPU.PRM"
    if not cpu_prm.exists():
        out["project-config.execution"] = _config_payload("missing", detail="CPU.PRM is absent")
    else:
        try:
            # Order is semantic here: CPU.PRM stores execution-setting order.
            out["project-config.execution"] = _config_payload(
                "ok", {"program_files": program_file_names(root)}
            )
        except (OSError, UnicodeError, ValueError) as exc:
            out["project-config.execution"] = _config_payload("unreadable", detail=str(exc))

    return out


def _collect_module_params(root: Path) -> dict[str, object]:
    candidates = [path for path in sorted(root.glob("*.db")) if MODULE_DB_RE.match(path.name)]
    if not candidates:
        return _config_payload("missing", detail="no numeric module parameter database is present")

    modules = []
    try:
        for path in candidates:
            module = read_module(path)
            if module.model or module.settings or module.note:
                modules.append(module)
    except (OSError, sqlite3.Error, UnicodeError, ValueError) as exc:
        return _config_payload("unreadable", detail=str(exc))

    if not modules:
        return _config_payload(
            "unsupported",
            detail="numeric databases exist but none matched the module-parameter schema",
        )
    notes = [f"{module.path.name}: {module.note}" for module in modules if module.note]
    if notes:
        return _config_payload("unreadable", detail="; ".join(notes))

    values: dict[str, object] = {}
    for module in modules:
        base = module.identity.get("_BaseNo", "")
        slot = module.identity.get("_SlotNo", "")
        module_key = f"base={base}|slot={slot}|model={module.model}|head_io={module.head_io}"
        # Preserve a module with no non-default settings as semantic presence.
        values[f"{module_key}|@module"] = "present"
        for setting in module.settings:
            key = (
                f"{module_key}|table={setting.table}|label={setting.label}|index={setting.index}"
            )
            values[key] = setting.value
    return _config_payload("ok", dict(sorted(values.items())))


def _collect_device_memory(root: Path) -> dict[str, object]:
    dbs = sorted(root.glob("*_DM.db"))
    if not dbs:
        return _config_payload("missing", detail="no *_DM.db device-memory file is present")

    try:
        comments = load_comment_map(root)
        topology_buckets: dict[str, list[dict[str, object]]] = {}
        value_buckets: dict[str, list[object]] = {}
        unsupported_codes: set[int] = set()
        for db in dbs:
            summaries, rows = decode_memory_db(db, root, comments, sys.maxsize)
            for summary in summaries:
                dev_code = int(summary.get("dev_code", -1))
                device_type = str(summary.get("device_type", ""))
                if device_type.startswith("DEV"):
                    unsupported_codes.add(dev_code)
                key = (
                    f"code={dev_code}|type={device_type}|mode={summary.get('decode_mode', '')}"
                    f"|first={summary.get('first_device', '')}|last={summary.get('last_device', '')}"
                )
                topology_buckets.setdefault(key, []).append(
                    {
                        "blocks": summary.get("blocks", 0),
                        "storage_words": summary.get("storage_words", 0),
                    }
                )
            for row in rows:
                key = f"code={row.get('dev_code', '')}|device={row.get('device', '')}"
                value_buckets.setdefault(key, []).append(row.get("value_unsigned", 0))
    except (OSError, sqlite3.Error, UnicodeError, ValueError, struct_error()) as exc:
        return _config_payload("unreadable", detail=str(exc))

    topology = {
        key: sorted(values, key=lambda value: json.dumps(value, sort_keys=True, ensure_ascii=False))
        for key, values in sorted(topology_buckets.items())
    }
    values: dict[str, object] = {}
    for key, bucket in sorted(value_buckets.items()):
        ordered = sorted(bucket, key=lambda value: str(value))
        values[key] = ordered[0] if len(ordered) == 1 else ordered
    data = {"topology": topology, "nonzero_values": values}
    if unsupported_codes:
        return _config_payload(
            "unsupported",
            data,
            "unknown device code(s): " + ", ".join(str(code) for code in sorted(unsupported_codes)),
        )
    return _config_payload("ok", data)


def struct_error() -> type[Exception]:
    """Avoid importing struct only for its exception type at module import time."""
    import struct

    return struct.error


def collect_configuration(root: Path) -> dict[str, dict[str, object]]:
    out = _collect_project_config(root)
    out["module-params"] = _collect_module_params(root)
    out["device-memory"] = _collect_device_memory(root)
    return out


def _flatten(value: object, prefix: str = "") -> dict[str, object]:
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for key in sorted(value, key=lambda item: str(item)):
            child = f"{prefix}.{key}" if prefix else str(key)
            out.update(_flatten(value[key], child))
        return out
    if isinstance(value, list):
        out = {}
        for index, item in enumerate(value):
            child = f"{prefix}[{index}]" if prefix else f"[{index}]"
            out.update(_flatten(item, child))
        return out
    return {prefix or "value": value}


def _format_config_value(value: object) -> str:
    if value is _CONFIG_MISSING_VALUE:
        return "<missing>"
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


_CONFIG_MISSING_VALUE = object()


def _unavailable_state(old_state: str, new_state: str) -> str:
    return max(
        (old_state, new_state),
        key=lambda state: CONFIG_UNAVAILABLE_PRIORITY.get(state, 99),
    )


def compare_configuration(
    old_root: Path, new_root: Path
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    old = collect_configuration(old_root)
    new = collect_configuration(new_root)
    results: list[dict[str, object]] = []
    detail: list[dict[str, object]] = []

    for name in CONFIG_SECTION_ORDER:
        old_payload = old[name]
        new_payload = new[name]
        old_state = str(old_payload.get("state", "unsupported"))
        new_state = str(new_payload.get("state", "unsupported"))
        old_data = old_payload.get("data", {})
        new_data = new_payload.get("data", {})
        availability_changed = old_state != new_state

        if old_state != "ok" or new_state != "ok":
            state = old_state if old_state == new_state else _unavailable_state(old_state, new_state)
            changed = availability_changed or old_data != new_data
            if changed:
                detail.append(
                    {
                        "pou": name,
                        "kind": f"config-{state}",
                        "pos": "",
                        "title": name,
                        "summary": (
                            f"old={old_state} ({old_payload.get('detail', '')}) -> "
                            f"new={new_state} ({new_payload.get('detail', '')})"
                        ),
                    }
                )
            results.append(
                {
                    "name": name,
                    "state": state,
                    "old_state": old_state,
                    "new_state": new_state,
                    "changes": 1 if changed else 0,
                }
            )
            continue

        old_flat = _flatten(old_data)
        new_flat = _flatten(new_data)
        keys = sorted(old_flat.keys() | new_flat.keys())
        changed_keys = [key for key in keys if old_flat.get(key, _CONFIG_MISSING_VALUE) != new_flat.get(key, _CONFIG_MISSING_VALUE)]
        state = "changed" if changed_keys else "same"
        for key in changed_keys:
            before = old_flat.get(key, _CONFIG_MISSING_VALUE)
            after = new_flat.get(key, _CONFIG_MISSING_VALUE)
            detail.append(
                {
                    "pou": name,
                    "kind": "config-changed",
                    "pos": "",
                    "title": key,
                    "summary": f"{_format_config_value(before)} -> {_format_config_value(after)}",
                }
            )
        results.append(
            {
                "name": name,
                "state": state,
                "old_state": old_state,
                "new_state": new_state,
                "changes": len(changed_keys),
            }
        )
    return results, detail


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", help="old project: extracted folder or .gx3")
    parser.add_argument("new", help="new project: extracted folder or .gx3")
    parser.add_argument("-o", "--output", default=None, help="detail CSV path")
    parser.add_argument("--show-layout-only", action="store_true")
    parser.add_argument("--skip-comments", action="store_true")
    parser.add_argument("--skip-config", action="store_true", help="skip project/module/device-memory configuration diff")
    parser.add_argument("--limit", type=int, default=60, help="console lines per section")
    args = parser.parse_args(argv)

    tmp: list[tempfile.TemporaryDirectory] = []
    try:
        old_root = ensure_root(args.old, tmp)
        new_root = ensure_root(args.new, tmp)
        print(f"old: {args.old}\nnew: {args.new}\n")
        old_rows, old_names = load_side(old_root)
        new_rows, new_names = load_side(new_root)

        detail: list[dict[str, object]] = []
        only_old = sorted(set(old_rows) - set(new_rows))
        only_new = sorted(set(new_rows) - set(old_rows))
        if only_old:
            print("POUs removed: " + ", ".join(old_names.get(h, h) for h in only_old))
        if only_new:
            print("POUs added:   " + ", ".join(new_names.get(h, h) for h in only_new))

        counts = {"added": 0, "removed": 0, "logic": 0, "layout": 0}
        for hexid in sorted(set(old_rows) & set(new_rows)):
            o, n = old_rows[hexid], new_rows[hexid]
            pou = new_names.get(hexid) or old_names.get(hexid, hexid)
            for guid in o.keys() - n.keys():
                pos, data, title = o[guid]
                counts["removed"] += 1
                detail.append({"pou": pou, "kind": "removed", "pos": pos, "title": title, "summary": ""})
            for guid in n.keys() - o.keys():
                pos, data, title = n[guid]
                counts["added"] += 1
                detail.append({"pou": pou, "kind": "added", "pos": pos, "title": title, "summary": ""})
            for guid in o.keys() & n.keys():
                o_pos, o_data, o_title = o[guid]
                n_pos, n_data, n_title = n[guid]
                if o_data == n_data:
                    continue
                if logic_signature(o_data) == logic_signature(n_data):
                    counts["layout"] += 1
                    if args.show_layout_only:
                        detail.append(
                            {"pou": pou, "kind": "layout-only", "pos": n_pos, "title": n_title, "summary": ""}
                        )
                    continue
                counts["logic"] += 1
                detail.append(
                    {
                        "pou": pou,
                        "kind": "logic",
                        "pos": n_pos,
                        "title": n_title,
                        "summary": summarize_change(o_data, n_data),
                    }
                )

        print(
            f"\nrows: added={counts['added']} removed={counts['removed']} "
            f"logic-changed={counts['logic']} layout-only={counts['layout']}"
        )
        order = {"logic": 0, "added": 1, "removed": 2, "layout-only": 3}
        detail.sort(key=lambda d: (order.get(str(d["kind"]), 9), str(d["pou"]), int(d["pos"])))
        for d in detail[: args.limit]:
            print(f"  [{d['kind']:<11}] {d['pou']:<8} pos={d['pos']:<7} {d['title']}")
            if d["summary"]:
                print(f"      {d['summary']}")
        if len(detail) > args.limit:
            print(f"  ... {len(detail) - args.limit} more rows (see CSV)")

        comment_changes: list[dict[str, object]] = []
        if not args.skip_comments:
            oc, nc = comment_map(old_root), comment_map(new_root)
            for dev in sorted(oc.keys() | nc.keys()):
                if oc.get(dev, "") == nc.get(dev, ""):
                    continue
                kind = "comment-added" if dev not in oc else ("comment-removed" if dev not in nc else "comment-changed")
                comment_changes.append(
                    {"pou": "", "kind": kind, "pos": "", "title": dev,
                     "summary": f"{oc.get(dev, '')!r} -> {nc.get(dev, '')!r}"}
                )
            print(f"\ndevice comments changed: {len(comment_changes)}")
            for d in comment_changes[:20]:
                print(f"  [{d['kind']}] {d['title']}: {d['summary']}")
            if len(comment_changes) > 20:
                print(f"  ... {len(comment_changes) - 20} more (see CSV)")

        config_changes: list[dict[str, object]] = []
        if not args.skip_config:
            config_results, config_changes = compare_configuration(old_root, new_root)
            print("\nconfiguration:")
            for result in config_results:
                suffix = f" changes={result['changes']}" if int(result["changes"]) else ""
                print(
                    f"  {str(result['name']):<28} {str(result['state']):<11}"
                    f" old={result['old_state']} new={result['new_state']}{suffix}"
                )
            print(f"configuration changed items: {len(config_changes)}")
            for d in config_changes[: args.limit]:
                print(f"  [{d['kind']}] {d['pou']} {d['title']}")
                if d["summary"]:
                    print(f"      {d['summary']}")
            if len(config_changes) > args.limit:
                print(f"  ... {len(config_changes) - args.limit} more configuration items (see CSV)")

        out = Path(args.output or "outputs/semantic_diff.csv")
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=["kind", "pou", "pos", "title", "summary"])
            w.writeheader()
            for d in detail + comment_changes + config_changes:
                w.writerow(d)
        print(f"\ncsv: {out}")
        return 0
    finally:
        for td in tmp:
            td.cleanup()


if __name__ == "__main__":
    raise SystemExit(main())
