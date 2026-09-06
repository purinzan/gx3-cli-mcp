from __future__ import annotations

"""Tests for MC/CALL execution context, jump indexing, and interlock SAT."""

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


def call_row(contact_number: int, pointer: int, pos: int, device_type: str = "M") -> LadderRow:
    """A real-shaped CALL row, based on the corpus regression CALL #P240 row."""
    from test_gx3_operand_alignment import POINTER_ROW

    data = POINTER_ROW
    data = data.replace("a:M:CALL:P:D", f"a:{device_type}:CALL:P:D", 1)
    data = data.replace("a=100", f"a={contact_number}", 1)
    data = data.replace("a=240", f"a={pointer}", 1)
    return LadderRow("SYNTH_LDDB.db", pos, "{call}", "", 0, 1, data, "4x1", [], "exact")


def pointer_row(pointer: int, condition_device: str, output_device: str, pos: int) -> LadderRow:
    row = synthetic_row({"device": condition_device}, {"type": "coil", "device": output_device}, pos)
    # parse_pointers() reads the same p{...} record GX stores beside a rung.
    # Keeping it outside the generated element list avoids changing the rung
    # topology under test; the pointer is a label, not a conduction element.
    row.data += f":p{{s=d{{s=#:a={pointer}:vt=nn}}:pos=0,0}}"
    return row


def ret_row(pos: int) -> LadderRow:
    return synthetic_row({"device": "SM400"}, {"type": "coil", "device": "M999"}, pos, role="RET")


def combined_logic_text(rows: list[LadderRow], target_row: LadderRow, device: str) -> str:
    from gx3cli.gx3_ladder_logic import enable_logic_for_device, logic_to_text
    from gx3cli.gx3_mc_zones import active_zones, apply_zone_conditions, build_mc_zones

    zones = build_mc_zones(rows)
    local = enable_logic_for_device(target_row, device)
    return logic_to_text(apply_zone_conditions(local, active_zones(zones, target_row.lddb, target_row.pos)))


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


def test_one_conditional_call_is_part_of_the_subroutine_output_condition() -> None:
    sub = pointer_row(240, "M20", "M100", 1024)
    rows = [call_row(10, 240, 0), sub, ret_row(2048)]
    text = combined_logic_text(rows, sub, "M100")
    assert "[M10]" in text and "[M20]" in text and " AND " in text, text


def test_two_call_sites_are_or_alternatives() -> None:
    sub = pointer_row(240, "M20", "M100", 1024)
    rows = [call_row(10, 240, 0), call_row(11, 240, 512), sub, ret_row(2048)]
    text = combined_logic_text(rows, sub, "M100")
    assert "[M10]" in text and "[M11]" in text and " OR " in text, text
    assert "[M20]" in text, text


def test_nested_call_composes_parent_and_child_invocation() -> None:
    first = pointer_row(240, "M20", "M200", 1024)
    nested_call = call_row(30, 241, 1536)
    second = pointer_row(241, "M40", "M100", 3072)
    rows = [
        call_row(10, 240, 0),
        first,
        nested_call,
        ret_row(2048),
        second,
        ret_row(4096),
    ]
    text = combined_logic_text(rows, second, "M100")
    for device in ("M10", "M30", "M40"):
        assert f"[{device}]" in text, text
    assert text.count(" AND ") >= 2, text


def test_unconditional_call_does_not_add_a_false_condition() -> None:
    sub = pointer_row(240, "M20", "M100", 1024)
    rows = [call_row(400, 240, 0, device_type="SM"), sub, ret_row(2048)]
    text = combined_logic_text(rows, sub, "M100")
    assert text == "[M20]", text


def test_missing_ret_is_explicit_unresolved_execution_context() -> None:
    from gx3cli.gx3_mc_zones import build_jump_index, jumps_before

    sub = pointer_row(240, "M20", "M100", 1024)
    rows = [call_row(10, 240, 0), sub]
    sites = jumps_before(build_jump_index(rows), sub.lddb, sub.pos)
    assert any(site.opcode == "CALL_CONTEXT" for site in sites), sites
    assert any("no following RET" in site.condition_text for site in sites), sites


def test_recursive_call_keeps_known_entry_but_marks_context_unresolved() -> None:
    from gx3cli.gx3_mc_zones import build_jump_index, jumps_before

    sub = pointer_row(240, "M20", "M100", 1024)
    recursive = call_row(30, 240, 1536)
    rows = [call_row(10, 240, 0), sub, recursive, ret_row(2048)]

    # The known external entry is still useful evidence.
    text = combined_logic_text(rows, sub, "M100")
    assert "[M10]" in text and "[M20]" in text, text

    # But recursion means the project-level execution model is not complete.
    sites = jumps_before(build_jump_index(rows), sub.lddb, sub.pos)
    assert any(site.opcode == "CALL_CONTEXT" for site in sites), sites
    assert any("recursive/cyclic" in site.condition_text for site in sites), sites


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
