from __future__ import annotations

"""The offline live-read modes: reachable, and speaking the shared vocabulary.

#147. Two features shipped with parsers and a main() that nothing called --
`gx3-cli live-read` reached the network reader alone, so the snapshot
explanation and the captured-log replay had no way in from the CLI, from MCP,
or from a test.

Because nothing called them, nobody could see that they answered in their own
words. `current_enable_condition: "missing"` and a list of `warnings` strings
say the same things as the shared states, in a form no summary can fold in. The
clearest case is a device the snapshot has no value for: that is exactly what
`NO_MEASUREMENT` was defined for, and the constant had no construction site
anywhere in the package.

What is deliberately kept: `current_enable_condition` and `warnings`. They are
what a reader of one row looks at, and removing them would trade one gap for
another. The state is added beside them, as the thing a summary reads.
"""

import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_analysis_state import (
    CHECKED,
    NO_MEASUREMENT,
    NOT_EVALUATED,
    AnalysisState,
    from_dict,
    no_measurement,
)
from gx3cli.gx3_live_read import build_explanation, format_snapshot_text
from gx3cli.gx3_live_read import main as live_read_main
from test_gx3_shared_reach import coil, write_program


def a_project(work: Path) -> Path:
    # M1 (a-contact) drives M100.
    write_program(work / "p", [("_guid/a", coil("a", 1, 100))])
    return work / "p"


def a_snapshot(work: Path, values: dict[str, object], name: str = "snap.json") -> Path:
    path = work / name
    path.write_text(
        json.dumps({"timestamp": "2026-09-07T09:00:00", "source": "test", "values": values}),
        encoding="utf-8",
    )
    return path


def explain(work: Path, values: dict[str, object]) -> dict:
    root = a_project(work)
    with contextlib.redirect_stdout(io.StringIO()):
        return build_explanation(root, "M100", a_snapshot(work, values), max_depth=2)


# ----- the entry points exist ------------------------------------------------


def test_the_offline_modes_are_reachable_from_the_command() -> None:
    """The reproduction: `live-read` could only ever open a socket."""
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        snapshot = a_snapshot(work, {"M1": 1})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = live_read_main(
                ["explain", "M100", "--root", str(root), "--snapshot", str(snapshot)]
            )
        assert code == 0, out.getvalue()
        assert "target: M100" in out.getvalue(), out.getvalue()


def test_the_replay_mode_is_reachable_too() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        log = work / "log.json"
        log.write_text(
            json.dumps([
                {"timestamp": "2026-09-07T09:00:00", "device": "M1", "value": 1},
                {"timestamp": "2026-09-07T09:00:01", "device": "M1", "value": 0},
            ]),
            encoding="utf-8",
        )
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = live_read_main(["replay", "changes", str(log)])
        assert code == 0, out.getvalue()
        assert "M1" in out.getvalue(), out.getvalue()


def test_the_network_mode_still_takes_its_flags_first() -> None:
    # The dispatch reads a leading word, so an existing invocation that starts
    # with a flag has to be untouched.
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = live_read_main(["--ip", "192.0.2.10", "--device", "D100", "--dry-run"])
    assert code == 0, out.getvalue()
    assert "no PLC connection opened" in out.getvalue(), out.getvalue()


# ----- the shared vocabulary -------------------------------------------------


def test_a_device_the_snapshot_does_not_hold_is_no_measurement() -> None:
    """The state that had no construction site in the whole package.

    The file was read through to the end. What is missing is a value only a
    running PLC has, which is not a decode gap and must not be summarised as
    one.
    """
    with tempfile.TemporaryDirectory() as tmp:
        result = explain(Path(tmp), {})  # no values at all
        analysis = result["analysis"]
        assert analysis["state"] == NO_MEASUREMENT, analysis
        assert "M1" in analysis.get("next_step", ""), analysis
        row = result["driver_rows"][0]
        assert row["analysis"]["state"] == NO_MEASUREMENT, row["analysis"]
        # The old vocabulary is still there for a reader of the single row.
        assert row["current_enable_condition"] == "missing", row


def test_a_snapshot_that_holds_the_value_is_checked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = explain(Path(tmp), {"M1": 1})
        assert result["analysis"]["state"] == CHECKED, result["analysis"]
        row = result["driver_rows"][0]
        assert row["analysis"]["state"] == CHECKED, row["analysis"]
        assert row["current_enable_condition"] == "pass", row


def test_a_missing_value_does_not_read_as_a_clean_answer() -> None:
    # The point of the state: "pass" for the rows it could evaluate must not
    # be reported as the whole answer when one contact was never measured.
    with tempfile.TemporaryDirectory() as tmp:
        result = explain(Path(tmp), {})
        assert not from_dict(result["analysis"]).conclusive, result["analysis"]
        # And it is said once. The state is the carrier; `warnings` keeps its
        # own notes for what a state cannot express, such as an unverifiable
        # project fingerprint.
        body = format_snapshot_text(result)
        assert body.count("no measured value") == 2, body  # the answer, and the row


def test_the_text_output_says_it_in_the_shared_words() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        body = format_snapshot_text(explain(Path(tmp), {}))
        assert "answer: no measured value" in body, body
        assert "capture M1" in body, body


def test_a_device_nothing_drives_is_not_evaluated_rather_than_checked() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = a_project(work)
        with contextlib.redirect_stdout(io.StringIO()):
            result = build_explanation(root, "M9999", a_snapshot(work, {"M1": 1}), max_depth=2)
        assert result["driver_rows"] == [], result["driver_rows"]
        assert result["analysis"]["state"] == NOT_EVALUATED, result["analysis"]


# ----- the state itself ------------------------------------------------------


def test_no_measurement_says_how_to_get_the_value() -> None:
    """The stage requirement, kept as its purpose rather than its letter.

    The five stages describe reading the project file, and this state says the
    file was read completely. Requiring a stage would point the reader at the
    decoder when the answer is to capture a value, so the obligation to say
    what to do next is carried by next_step instead.
    """
    state = no_measurement("snapshot has no value for M1", "capture M1")
    assert state.stage == "", state
    assert not state.conclusive
    try:
        AnalysisState(NO_MEASUREMENT, reason="no value")
    except ValueError as error:
        assert "next_step" in str(error), str(error)
    else:
        raise AssertionError("no_measurement was allowed to say nothing about what to do")


def test_a_state_that_crossed_json_comes_back_as_itself() -> None:
    for state in (no_measurement("r", "n"), AnalysisState(CHECKED)):
        assert from_dict(json.loads(json.dumps(state.as_dict()))).state == state.state


def test_an_unrecognised_shape_does_not_pass_for_checked() -> None:
    for shape in (None, {}, {"state": "fine"}, "checked"):
        assert from_dict(shape).state == NOT_EVALUATED, shape


def main() -> int:
    test_the_offline_modes_are_reachable_from_the_command()
    test_the_replay_mode_is_reachable_too()
    test_the_network_mode_still_takes_its_flags_first()
    test_a_device_the_snapshot_does_not_hold_is_no_measurement()
    test_a_snapshot_that_holds_the_value_is_checked()
    test_a_missing_value_does_not_read_as_a_clean_answer()
    test_the_text_output_says_it_in_the_shared_words()
    test_a_device_nothing_drives_is_not_evaluated_rather_than_checked()
    test_no_measurement_says_how_to_get_the_value()
    test_a_state_that_crossed_json_comes_back_as_itself()
    test_an_unrecognised_shape_does_not_pass_for_checked()
    print("live-read state checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
