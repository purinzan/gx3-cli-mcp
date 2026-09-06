from __future__ import annotations

"""A trace that stopped at a limit or semantic boundary has not answered the question.

The important invariant here is not that every analysis is complete. It is that
anything the project-level model did not account for is named before the reader
sees the local rung condition as though it were the whole answer.
"""

from gx3cli.gx3_analysis_state import CHECKED, DECODE, PARTIAL, TOPOLOGY, TRUNCATED
from gx3cli.trace_gx3_device_dependencies import state_lines, trace_state


def test_a_complete_trace_says_nothing_extra() -> None:
    state = trace_state(truncated=False, reasons=[], partial_rows=[])
    assert state.state == CHECKED
    assert state.conclusive
    assert state_lines({"analysis": state.as_dict()}) == []


def test_a_trace_that_hit_a_limit_says_which_limit() -> None:
    state = trace_state(truncated=True, reasons=["max_depth"], partial_rows=[])
    assert state.state == TRUNCATED
    assert not state.conclusive
    assert "max_depth" in state.reason, state.reason
    assert "max-depth" in state.next_step, state.next_step

    lines = state_lines({"analysis": state.as_dict()})
    body = "\n".join(lines)
    assert "Result:" in body, body
    assert "max_depth" in body, body
    assert "next:" in body, body


def test_an_unread_driver_row_outranks_a_limit() -> None:
    state = trace_state(
        truncated=True, reasons=["max_depth"], partial_rows=[{"parse_status": "partial"}]
    )
    assert state.state == PARTIAL, state
    assert "not fully interpreted" in state.reason, state.reason
    assert "parse-gaps" in state.next_step, state.next_step


def test_the_japanese_output_says_the_same_thing() -> None:
    state = trace_state(truncated=True, reasons=["max_devices"], partial_rows=[])
    lines = state_lines({"analysis": state.as_dict()}, ja=True)
    body = "\n".join(lines)
    assert "結果:" in body, body
    assert "次の手順:" in body, body


def test_a_condition_too_large_to_expand_is_a_wiring_limit_not_a_decoding_one() -> None:
    state = trace_state(
        truncated=False, reasons=[], partial_rows=[], capped_rows=[{"logic_stats": {"too_large": 1}}]
    )
    assert state.state == PARTIAL, state
    assert state.stage == TOPOLOGY, state
    assert "too large" in state.reason, state.reason
    assert "parse-gaps" not in state.next_step, state.next_step
    assert "ladder-print" in state.next_step or "ladder-report" in state.next_step, state.next_step


def test_an_unread_row_still_outranks_a_capped_one() -> None:
    state = trace_state(
        truncated=False,
        reasons=[],
        partial_rows=[{"parse_status": "partial"}],
        capped_rows=[{"logic_stats": {"too_large": 1}}],
    )
    assert state.stage == DECODE, state
    assert "parse-gaps" in state.next_step, state.next_step


def test_the_stage_reaches_the_printed_line() -> None:
    state = trace_state(False, [], [], [{"logic_stats": {"too_large": 1}}])
    body = "\n".join(state_lines({"analysis": state.as_dict()}, ja=True))
    assert "配線と成立論理" in body, body


def test_a_stateful_driver_is_not_a_finished_answer() -> None:
    from gx3cli.gx3_analysis_state import SEMANTICS
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    gaps = semantic_gaps([{"driver_roles": ["SET"], "conditions": [], "cj_upstream": []}])
    assert gaps and "SET/RST" in gaps[0], gaps

    state = trace_state(False, [], [], [], gaps)
    assert state.state == PARTIAL, state
    assert state.stage == SEMANTICS, state
    assert not state.conclusive


def test_edge_triggered_value_write_survives_the_trace_output_boundary() -> None:
    """#95: MOVP is a writer and an edge event, not an ordinary level write."""
    from gx3cli.gx3_analysis_state import SEMANTICS
    from gx3cli.review_gx3_project import DeviceOcc, LadderRow
    from gx3cli.trace_gx3_device_dependencies import (
        driver_effect,
        row_write_occurrences,
        semantic_gaps,
        temporal_predicates,
    )

    occurrence = DeviceOcc(
        device="D200",
        device_type="D",
        number=200,
        role="MOVP",
        lddb="SYNTH_LDDB.db",
        pos=10,
        block_id="b",
        title="",
        parse_status="exact",
        access="write",
    )
    row = LadderRow("SYNTH_LDDB.db", 10, "b", "", 0, 1, "", "", [], "exact", [occurrence])
    assert row_write_occurrences(row) == [occurrence]

    predicates = temporal_predicates([occurrence], [])
    assert predicates == [{
        "kind": "edge_triggered_write",
        "device": "D200",
        "opcode": "MOVP",
        "execution_condition": "rising",
        "source": "instruction_execution_condition",
        "requires_runtime_state": True,
    }], predicates
    assert driver_effect("MOVP") == "write/edge-triggered"

    gaps = semantic_gaps([{
        "driver_roles": ["MOVP"],
        "conditions": [],
        "strict_logic": True,
        "mc_zones": [],
        "cj_upstream": [],
    }])
    assert any("edge-triggered value writes" in gap and "MOVP" in gap for gap in gaps), gaps
    state = trace_state(False, [], [], [], gaps)
    assert state.state == PARTIAL and state.stage == SEMANTICS, state


def test_level_value_write_does_not_gain_edge_semantics() -> None:
    """Ordinary MOV remains a level execution write; not every value write is partial."""
    from gx3cli.review_gx3_project import DeviceOcc
    from gx3cli.trace_gx3_device_dependencies import driver_effect, semantic_gaps, temporal_predicates

    occurrence = DeviceOcc(
        device="D200",
        device_type="D",
        number=200,
        role="MOV",
        lddb="SYNTH_LDDB.db",
        pos=10,
        block_id="b",
        title="",
        parse_status="exact",
        access="write",
    )
    assert temporal_predicates([occurrence], []) == []
    assert driver_effect("MOV") == "write/value"
    assert semantic_gaps([{
        "driver_roles": ["MOV"],
        "conditions": [],
        "strict_logic": True,
        "mc_zones": [],
        "cj_upstream": [],
    }]) == []


def test_unsigned_pulse_value_write_uses_manual_semantics_not_name_suffix() -> None:
    """+P_U ends in _U, so a suffix heuristic would miss its rising edge."""
    from gx3cli.review_gx3_project import DeviceOcc
    from gx3cli.trace_gx3_device_dependencies import temporal_predicates

    occurrence = DeviceOcc(
        device="D300",
        device_type="D",
        number=300,
        role="+P_U",
        lddb="SYNTH_LDDB.db",
        pos=20,
        block_id="b",
        title="",
        parse_status="exact",
        access="write",
    )
    predicates = temporal_predicates([occurrence], [])
    assert len(predicates) == 1, predicates
    assert predicates[0]["kind"] == "edge_triggered_write", predicates
    assert predicates[0]["execution_condition"] == "rising", predicates


def test_a_jump_above_a_driver_row_is_reported() -> None:
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    gaps = semantic_gaps([
        {
            "driver_roles": ["c"],
            "conditions": [],
            "strict_logic": True,
            "mc_zones": [],
            "cj_upstream": [{"pos": 10, "opcode": "CJ", "condition_text": "[M1]"}],
        }
    ])
    assert any("jump" in gap for gap in gaps), gaps
    assert not any("CALL/ECALL" in gap for gap in gaps), gaps


def test_an_unresolved_call_is_not_mislabeled_as_a_jump() -> None:
    """#91: the safety state and its explanation must name the same construct."""
    from gx3cli.gx3_analysis_state import SEMANTICS
    from gx3cli.trace_gx3_device_dependencies import execution_guards, semantic_gaps

    site = type(
        "CallGuard",
        (),
        {
            "opcode": "CALL_CONTEXT",
            "pos": 99,
            "condition_text": "CALL P240 target has no following RET",
            "reason": "CALL P240 target has no following RET",
            "start_pos": 100,
            "end_pos": 200,
        },
    )()
    row = {
        "driver_roles": ["c"],
        "conditions": [],
        "strict_logic": True,
        "mc_zones": [],
        "cj_upstream": [{
            "pos": 99,
            "opcode": "CALL_CONTEXT",
            "condition_text": site.condition_text,
            "reason": site.reason,
        }],
    }
    gaps = semantic_gaps([row])
    assert any("CALL/ECALL" in gap for gap in gaps), gaps
    assert not any("conditional jump" in gap for gap in gaps), gaps

    state = trace_state(False, [], [], [], gaps)
    assert state.state == PARTIAL and state.stage == SEMANTICS, state

    guards = execution_guards([site])
    assert guards == [{
        "kind": "call_context_unresolved",
        "opcode": "CALL_CONTEXT",
        "pos": 99,
        "condition": site.condition_text,
        "target_resolved": False,
        "reason": site.reason,
        "scope_start_pos": 100,
        "scope_end_pos": 200,
    }], guards


def test_flat_trace_with_project_execution_context_is_not_checked() -> None:
    """CALL/MC zones are not silently discarded by the default flat view."""
    from gx3cli.gx3_analysis_state import SEMANTICS
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    row = {
        "driver_roles": ["c"],
        "conditions": [{"device": "M20"}],
        "strict_logic": False,
        "mc_zones": [{
            "kind": "call_invocation",
            "pointer": 240,
            "condition_text": "[M10]",
        }],
        "cj_upstream": [],
    }
    gaps = semantic_gaps([row])
    assert any("--strict-logic" in gap for gap in gaps), gaps
    state = trace_state(False, [], [], [], gaps)
    assert state.state == PARTIAL and state.stage == SEMANTICS, state


def test_a_timer_contact_in_the_condition_is_named() -> None:
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    gaps = semantic_gaps([
        {
            "driver_roles": ["c"],
            "cj_upstream": [],
            "strict_logic": True,
            "mc_zones": [],
            "conditions": [{"device": "T580"}, {"device": "M100"}],
        }
    ])
    assert any("T580" in gap for gap in gaps), gaps
    assert not any("M100" in gap for gap in gaps), gaps


def test_a_plain_coil_says_nothing_extra() -> None:
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    assert semantic_gaps([
        {
            "driver_roles": ["c"],
            "conditions": [{"device": "M100"}],
            "strict_logic": True,
            "mc_zones": [],
            "cj_upstream": [],
        }
    ]) == []


def test_every_constraint_survives_the_one_that_names_the_state() -> None:
    state = trace_state(
        truncated=True,
        reasons=["max_depth"],
        partial_rows=[],
        capped_rows=[],
        gaps=["SET/RST: the condition shown is when it changes, not when it holds"],
    )
    also = state.as_dict().get("detail", {}).get("also", [])
    assert also, state.as_dict()
    assert any(item["stage"] == "reach" for item in also), also

    body = "\n".join(state_lines({"analysis": state.as_dict()}))
    assert "also:" in body, body
    assert "max_depth" in body, body


def test_the_order_is_what_to_do_next() -> None:
    state = trace_state(
        truncated=True,
        reasons=["max_depth"],
        partial_rows=[{"parse_status": "partial"}],
        capped_rows=[{"logic_stats": {"too_large": 1}}],
        gaps=["SET/RST: ..."],
    )
    assert state.stage == DECODE, state
    assert len(state.as_dict()["detail"]["also"]) == 3, state.as_dict()


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} trace state checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
