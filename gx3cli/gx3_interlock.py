from __future__ import annotations

"""Static interlock check: can two coils' enable conditions be true at once?

Builds the strict topology-derived enable logic for each device (including MC
master-control zone conditions), optionally substitutes internal OUT-driven
bit conditions upstream to --max-depth, and checks boolean satisfiability of
``A AND B`` by exhaustive assignment over the contact variables.

Verdicts:
  mutually-exclusive  no assignment enables both (sound: word predicates and
                      boundary devices are treated as independent free
                      booleans, which can only ADD satisfying assignments, so
                      an exclusive verdict cannot be produced by that
                      approximation)
  simultaneous-possible  a witness assignment is printed; this does NOT prove
                      reachability (scan dynamics, timers, and word values
                      are ignored)
  unknown             more variables than --max-vars

Devices with SET/RST drivers are kept as free variables (a latch's held state
cannot be expressed as a static formula). If a checked target itself is
latched, the verdict covers simultaneity of the SET causes only.
"""

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from gx3cli.gx3_ladder_logic import (
    enable_logic_for_output,
    logic_key,
    logic_to_text,
    normalize_device,
    or_logic,
    output_elements_for,
    parse_device,
)
from gx3cli.gx3_mc_zones import active_zones, apply_zone_conditions, build_mc_zones
from gx3cli.gx3_project_paths import default_project_root
from gx3cli.review_gx3_project import LadderRow, load_comments_for_root, load_rows
from gx3cli.trace_gx3_device_dependencies import (
    OFF_DRIVER_ROLES,
    device_comment,
    driver_index,
)


INTERNAL_BIT_TYPES = {"M", "L", "B", "F", "V", "S"}


# --------------------------------------------------------------------------
# Logic-tree operations (negation, evaluation, variables)
# --------------------------------------------------------------------------


def negate(node: dict[str, Any]) -> dict[str, Any]:
    op = node.get("op")
    if op == "true":
        return {"op": "false"}
    if op == "false":
        return {"op": "true"}
    if op == "contact":
        flipped = dict(node)
        flipped["role"] = "b" if node.get("role") == "a" else "a"
        flipped["state"] = "OFF" if node.get("state") == "ON" else "ON"
        return flipped
    if op == "and":
        return {"op": "or", "args": [negate(child) for child in node.get("args", [])]}
    if op == "or":
        return {"op": "and", "args": [negate(child) for child in node.get("args", [])]}
    if op == "not":
        return dict(node.get("arg", {"op": "true"}))
    return {"op": "not", "arg": node}


def var_key(node: dict[str, Any]) -> str:
    op = node.get("op")
    if op == "contact":
        return f"dev:{node.get('raw_device') or node.get('device')}"
    return f"{op}:{logic_key(node)}"


def collect_vars(node: dict[str, Any], counter: Counter[str]) -> None:
    op = node.get("op")
    if op in {"true", "false"}:
        return
    if op == "contact":
        counter[var_key(node)] += 1
        return
    if op in {"and", "or"}:
        for child in node.get("args", []):
            collect_vars(child, counter)
        return
    if op == "not":
        collect_vars(node.get("arg", {}), counter)
        return
    counter[var_key(node)] += 1


def eval_node(node: dict[str, Any], assign: dict[str, bool]) -> bool | None:
    op = node.get("op")
    if op == "true":
        return True
    if op == "false":
        return False
    if op == "contact":
        value = assign.get(var_key(node))
        if value is None:
            return None
        return value if node.get("role") == "a" else not value
    if op == "and":
        pending = False
        for child in node.get("args", []):
            result = eval_node(child, assign)
            if result is False:
                return False
            if result is None:
                pending = True
        return None if pending else True
    if op == "or":
        pending = False
        for child in node.get("args", []):
            result = eval_node(child, assign)
            if result is True:
                return True
            if result is None:
                pending = True
        return None if pending else False
    if op == "not":
        result = eval_node(node.get("arg", {}), assign)
        return None if result is None else not result
    return assign.get(var_key(node))


def find_assignment(node: dict[str, Any], variables: list[str]) -> dict[str, bool] | None:
    assign: dict[str, bool] = {}

    def solve(index: int) -> bool:
        result = eval_node(node, assign)
        if result is True:
            return True
        if result is False:
            return False
        if index >= len(variables):
            return False
        var = variables[index]
        for value in (True, False):
            assign[var] = value
            if solve(index + 1):
                return True
            del assign[var]
        return False

    return dict(assign) if solve(0) else None


# --------------------------------------------------------------------------
# Enable logic per device and upstream expansion
# --------------------------------------------------------------------------


class InterlockModel:
    def __init__(self, root: Path) -> None:
        self.comments = load_comments_for_root(root)
        self.rows = load_rows(root, self.comments)
        self.drivers = driver_index(self.rows, include_reset=True)
        self.mc_zones = build_mc_zones(self.rows)
        self._on_logic_cache: dict[str, dict[str, Any]] = {}

    def driver_roles(self, device: str) -> set[str]:
        roles: set[str] = set()
        for row in self.drivers.get(device, []):
            for occ in row.occurrences:
                if occ.device == device:
                    roles.add(occ.role)
        return roles

    def is_latched(self, device: str) -> bool:
        return bool(self.driver_roles(device) & {"SET", "RST"})

    def on_logic(self, device: str) -> dict[str, Any]:
        cached = self._on_logic_cache.get(device)
        if cached is not None:
            return cached
        terms: list[dict[str, Any]] = []
        for row in self.drivers.get(device, []):
            zones = active_zones(self.mc_zones, row.lddb, row.pos)
            for output in output_elements_for(row, device):
                if output.role in OFF_DRIVER_ROLES:
                    continue
                terms.append(apply_zone_conditions(enable_logic_for_output(row, output), zones))
        logic = or_logic(terms)
        self._on_logic_cache[device] = logic
        return logic

    def expandable(self, device: str) -> bool:
        if not self.drivers.get(device):
            return False
        try:
            device_type, _ = parse_device(device)
        except ValueError:
            return False
        if device_type not in INTERNAL_BIT_TYPES:
            return False
        return not self.is_latched(device)

    def expand(self, node: dict[str, Any], depth: int, stack: frozenset[str]) -> dict[str, Any]:
        op = node.get("op")
        if op == "contact" and depth > 0:
            device = str(node.get("raw_device") or node.get("device") or "")
            if device and device not in stack and self.expandable(device):
                definition = self.expand(self.on_logic(device), depth - 1, stack | {device})
                return definition if node.get("role") == "a" else negate(definition)
            return node
        if op in {"and", "or"}:
            expanded = [self.expand(child, depth, stack) for child in node.get("args", [])]
            return {"op": op, "args": expanded}
        if op == "not":
            return {"op": "not", "arg": self.expand(node.get("arg", {}), depth, stack)}
        return node


def witness_lines(assign: dict[str, bool], model: InterlockModel) -> list[str]:
    lines: list[str] = []
    for key, value in sorted(assign.items()):
        if key.startswith("dev:"):
            device = key[len("dev:"):]
            comment = device_comment(device, model.comments)
            comment_text = f" {comment}" if comment else ""
            lines.append(f"{device}={'ON' if value else 'OFF'}{comment_text}")
        else:
            label = key.split(":", 1)[0]
            lines.append(f"[{label}] {key.split(':', 1)[1][:90]} = {value}")
    return lines


def check_pair(logic_a: dict[str, Any], logic_b: dict[str, Any], max_vars: int) -> dict[str, Any]:
    combined = {"op": "and", "args": [logic_a, logic_b]}
    counter: Counter[str] = Counter()
    collect_vars(combined, counter)
    variables = [var for var, _count in counter.most_common()]
    if len(variables) > max_vars:
        return {
            "verdict": "unknown",
            "variables": len(variables),
            "reason": f"{len(variables)} variables exceed --max-vars {max_vars}; lower --max-depth or raise --max-vars",
        }
    assign = find_assignment(combined, variables)
    if assign is None:
        return {"verdict": "mutually-exclusive", "variables": len(variables)}
    return {"verdict": "simultaneous-possible", "variables": len(variables), "witness": assign}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Check whether two coils' ON/enable conditions can be true simultaneously (static)."
    )
    parser.add_argument("device_a", help="first coil/device")
    parser.add_argument("device_b", help="second coil/device")
    parser.add_argument("--root", default=str(default_project_root()), help="extracted project folder")
    parser.add_argument("--max-depth", type=int, default=2, help="upstream substitution depth for internal OUT-driven bits")
    parser.add_argument("--max-vars", type=int, default=24, help="variable limit for the satisfiability search")
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("-o", "--output", help="write output to file")
    return parser


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    args = build_parser().parse_args(argv)

    device_a = normalize_device(args.device_a)
    device_b = normalize_device(args.device_b)
    model = InterlockModel(Path(args.root))

    report: dict[str, Any] = {
        "root": str(args.root),
        "device_a": device_a,
        "device_b": device_b,
        "max_depth": args.max_depth,
        "notes": [
            "word predicates / boundary devices are free booleans (over-approximation: cannot cause a false exclusive verdict)",
            "scan order, timers, and data values are not modeled",
        ],
        "devices": {},
    }

    logics: dict[str, dict[str, Any]] = {}
    for device in (device_a, device_b):
        base = model.on_logic(device)
        info: dict[str, Any] = {
            "comment": device_comment(device, model.comments),
            "driver_rows": len(model.drivers.get(device, [])),
            "on_logic_text": logic_to_text(base),
            "latched": model.is_latched(device),
        }
        if not model.drivers.get(device):
            info["warning"] = "no driver rows found; device treated as free input"
            logics[device] = {"op": "contact", "role": "a", "state": "ON", "raw_device": device, "device": device}
        else:
            if info["latched"]:
                info["latch_note"] = "SET/RST driven: verdict covers simultaneity of set causes, not held state overlap"
            logics[device] = model.expand(base, args.max_depth, frozenset({device}))
        report["devices"][device] = info

    result = check_pair(logics[device_a], logics[device_b], args.max_vars)
    report["result"] = result
    if result.get("witness"):
        report["witness_lines"] = witness_lines(result["witness"], model)

    if args.format == "json":
        output = json.dumps(report, ensure_ascii=False, indent=2)
    else:
        lines = [
            f"Interlock check: {device_a} vs {device_b}",
            f"Root: {args.root}",
        ]
        for device in (device_a, device_b):
            info = report["devices"][device]
            lines.append("")
            lines.append(f"{device} {info['comment']}".rstrip())
            lines.append(f"  driver_rows={info['driver_rows']} latched={info['latched']}")
            lines.append(f"  ON logic: {info['on_logic_text']}")
            for key in ("warning", "latch_note"):
                if info.get(key):
                    lines.append(f"  note: {info[key]}")
        lines.append("")
        verdict = result["verdict"]
        if verdict == "mutually-exclusive":
            lines.append(f"VERDICT: MUTUALLY EXCLUSIVE ({result['variables']} variables, no enabling assignment exists)")
        elif verdict == "simultaneous-possible":
            lines.append(f"VERDICT: SIMULTANEOUS ON POSSIBLE ({result['variables']} variables)")
            lines.append("Witness assignment:")
            for line in report.get("witness_lines", []):
                lines.append(f"  - {line}")
        else:
            lines.append(f"VERDICT: UNKNOWN ({result.get('reason', '')})")
        lines.append("")
        for note in report["notes"]:
            lines.append(f"note: {note}")
        output = "\n".join(lines)

    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")
    else:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
