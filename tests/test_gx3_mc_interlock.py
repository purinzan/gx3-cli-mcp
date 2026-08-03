from __future__ import annotations

"""Tests for MC zone reconstruction, jump indexing, and interlock SAT."""

import sys

from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_interlock import check_pair, collect_vars, eval_node, find_assignment, negate
from collections import Counter
from gx3cli.review_gx3_project import LadderRow


def contact(device: str, role: str = "a") -> dict:
    return {
        "op": "contact",
        "role": role,
        "state": "ON" if role == "a" else "OFF",
        "raw_device": device,
        "device": device,
    }


def test_negate_contact() -> None:
    node = contact("M1", "a")
    flipped = negate(node)
    assert flipped["role"] == "b" and flipped["state"] == "OFF"
    assert negate(flipped)["role"] == "a"


def test_eval_and_or() -> None:
    node = {"op": "and", "args": [contact("M1", "a"), contact("M2", "b")]}
    assert eval_node(node, {"dev:M1": True, "dev:M2": False}) is True
    assert eval_node(node, {"dev:M1": True, "dev:M2": True}) is False
    assert eval_node(node, {"dev:M1": True}) is None
    # short-circuit works on partial assignment
    assert eval_node(node, {"dev:M2": True}) is False


def test_check_pair_mutually_exclusive() -> None:
    logic_a = {"op": "and", "args": [contact("M1", "a"), contact("M9", "a")]}
    logic_b = {"op": "and", "args": [contact("M1", "b"), contact("M8", "a")]}
    result = check_pair(logic_a, logic_b, max_vars=10)
    assert result["verdict"] == "mutually-exclusive", result


def test_check_pair_simultaneous() -> None:
    logic_a = {"op": "or", "args": [contact("M1", "a"), contact("M2", "a")]}
    logic_b = contact("M2", "a")
    result = check_pair(logic_a, logic_b, max_vars=10)
    assert result["verdict"] == "simultaneous-possible", result
    assert result["witness"]["dev:M2"] is True


def test_check_pair_unknown_over_var_cap() -> None:
    logic_a = {"op": "or", "args": [contact(f"M{i}", "a") for i in range(6)]}
    logic_b = contact("M100", "a")
    result = check_pair(logic_a, logic_b, max_vars=3)
    assert result["verdict"] == "unknown"


def test_collect_vars_counts_predicates_once_per_form() -> None:
    predicate = {"op": "predicate", "opcode": ">=", "devices": [], "constants": ["K5"], "position": "1,0"}
    node = {"op": "and", "args": [predicate, contact("M1", "a"), negate(predicate)]}
    counter: Counter[str] = Counter()
    collect_vars(node, counter)
    keys = [k for k in counter if k.startswith("predicate:")]
    assert len(keys) == 1  # same predicate shares one variable through the not-wrapper


def synthetic_row(logic: dict, output: dict, pos: int, role: str | None = None) -> LadderRow:
    data, rowsize, _ = generate_rung(logic, output)
    if role:
        device_type = output["device"].rstrip("0123456789")
        data = data.replace(f"c:{device_type}", f"{role}:{device_type}", 1)
    return LadderRow("SYNTH_LDDB.db", pos, "{x}", "", 0, rowsize, data, "", [], "exact")


def test_mc_zone_reconstruction_with_synthetic_rows() -> None:
    from gx3cli.gx3_mc_zones import active_zones, build_mc_zones

    rows = [
        synthetic_row({"device": "M1"}, {"type": "coil", "device": "M900"}, 0, role="MC"),
        synthetic_row({"device": "M2"}, {"type": "coil", "device": "M100"}, 1024),
        synthetic_row({"device": "M3"}, {"type": "coil", "device": "M900"}, 2048, role="MCR"),
    ]
    zones_by_lddb = build_mc_zones(rows)
    zones = zones_by_lddb["SYNTH_LDDB.db"]
    assert len(zones) == 1
    assert zones[0].start_pos == 0
    assert zones[0].end_pos == 2048
    assert zones[0].condition_text == "[M1]"
    assert active_zones(zones_by_lddb, "SYNTH_LDDB.db", 1024) == zones
    assert active_zones(zones_by_lddb, "SYNTH_LDDB.db", 2048) == []


def test_jump_index_with_synthetic_row() -> None:
    from gx3cli.gx3_mc_zones import build_jump_index

    rows = [synthetic_row({"device": "M10"}, {"type": "coil", "device": "M999"}, 4096, role="CJ")]
    jump_index = build_jump_index(rows)
    sites = jump_index["SYNTH_LDDB.db"]
    assert len(sites) == 1
    assert sites[0].opcode == "CJ"
    assert sites[0].condition_text == "[M10]"


def main() -> int:
    for name, func in sorted(globals().items()):
        if name.startswith("test_"):
            func()
            print(f"pass: {name}")
    print("all tests passed")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
