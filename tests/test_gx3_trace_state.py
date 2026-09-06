from __future__ import annotations

"""A trace that stopped at a limit has not answered the question.

It has shown where looking stopped. The flag for it was inside a stats line --
"truncated=True", between an edge count and a device count -- and the
conditions printed underneath read as the whole condition.

Issue #49, P0: the states are to be the same across commands, and a conclusion
resting on something not fully read is to say so. trace-device now reports
through the same vocabulary lint uses, above the answer rather than inside a
statistics line.
"""

from gx3cli.gx3_analysis_state import CHECKED, DECODE, PARTIAL, TOPOLOGY, TRUNCATED
from gx3cli.trace_gx3_device_dependencies import state_lines, trace_state


def test_a_complete_trace_says_nothing_extra() -> None:
    state = trace_state(truncated=False, reasons=[], partial_rows=[])
    assert state.state == CHECKED
    assert state.conclusive
    # Nothing above the answer when there is nothing to warn about.
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
    # Both are true, and the one that matters is that part of the condition
    # could not be read: raising the limit would not fix it.
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
    """The instructions were read. What could not be finished was the folding.

    Reporting it as "instructions and operands" sends the reader to parse-gaps,
    which has nothing to say about it: there is no gap in the decoding. The
    rung is one whose expanded condition passed the size budget, and the way
    to see it is to look at the rung.
    """
    state = trace_state(
        truncated=False, reasons=[], partial_rows=[], capped_rows=[{"logic_stats": {"too_large": 1}}]
    )
    assert state.state == PARTIAL, state
    assert state.stage == TOPOLOGY, state
    assert "too large" in state.reason, state.reason
    assert "parse-gaps" not in state.next_step, state.next_step
    assert "ladder-print" in state.next_step or "ladder-report" in state.next_step, state.next_step


def test_an_unread_row_still_outranks_a_capped_one() -> None:
    # Both incomplete; the unreadable one is the worse problem and the one
    # whose remedy differs from doing nothing.
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
    """#95: SET, PLS and timers carry state a Boolean condition does not.

    The condition folded from the contacts is right, and it answers "when does
    this change", not "when does this hold". Returning `checked` said there was
    nothing else to know.
    """
    from gx3cli.gx3_analysis_state import SEMANTICS
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    gaps = semantic_gaps([{"driver_roles": ["SET"], "conditions": [], "cj_upstream": []}])
    assert gaps and "SET/RST" in gaps[0], gaps

    state = trace_state(False, [], [], [], gaps)
    assert state.state == PARTIAL, state
    assert state.stage == SEMANTICS, state
    assert not state.conclusive


def test_a_jump_above_a_driver_row_is_reported() -> None:
    # #98: the targets are not resolved, so which rungs a jump bypasses is
    # unknown. A trace that crosses one cannot say the rung ran.
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    gaps = semantic_gaps([
        {"driver_roles": ["c"], "conditions": [], "cj_upstream": [{"pos": 10}]}
    ])
    assert any("jump" in gap for gap in gaps), gaps


def test_a_timer_contact_in_the_condition_is_named() -> None:
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    gaps = semantic_gaps([
        {
            "driver_roles": ["c"],
            "cj_upstream": [],
            "conditions": [{"device": "T580"}, {"device": "M100"}],
        }
    ])
    assert any("T580" in gap for gap in gaps), gaps
    assert not any("M100" in gap for gap in gaps), gaps


def test_a_plain_coil_says_nothing_extra() -> None:
    # The opposite error: if everything is partial, the word stops being read.
    from gx3cli.trace_gx3_device_dependencies import semantic_gaps

    assert semantic_gaps([
        {"driver_roles": ["c"], "conditions": [{"device": "M100"}], "cj_upstream": []}
    ]) == []


def test_every_constraint_survives_the_one_that_names_the_state() -> None:
    """#76's addendum: choosing an overall state must not erase the others.

    A trace can be semantically incomplete and truncated at once. Raising the
    depth limit does not make the timer modelled, and a reader who only sees
    the winner fixes one and believes the answer.
    """
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
    # An unread row outranks everything: nothing else can be trusted over it.
    from gx3cli.gx3_analysis_state import DECODE

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
    test_a_complete_trace_says_nothing_extra()
    test_a_trace_that_hit_a_limit_says_which_limit()
    test_an_unread_driver_row_outranks_a_limit()
    test_the_japanese_output_says_the_same_thing()
    test_a_condition_too_large_to_expand_is_a_wiring_limit_not_a_decoding_one()
    test_an_unread_row_still_outranks_a_capped_one()
    test_the_stage_reaches_the_printed_line()
    test_a_stateful_driver_is_not_a_finished_answer()
    test_a_jump_above_a_driver_row_is_reported()
    test_a_timer_contact_in_the_condition_is_named()
    test_a_plain_coil_says_nothing_extra()
    test_every_constraint_survives_the_one_that_names_the_state()
    test_the_order_is_what_to_do_next()
    print("trace state checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
