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


def main() -> int:
    test_a_complete_trace_says_nothing_extra()
    test_a_trace_that_hit_a_limit_says_which_limit()
    test_an_unread_driver_row_outranks_a_limit()
    test_the_japanese_output_says_the_same_thing()
    test_a_condition_too_large_to_expand_is_a_wiring_limit_not_a_decoding_one()
    test_an_unread_row_still_outranks_a_capped_one()
    test_the_stage_reaches_the_printed_line()
    print("trace state checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
