"""Regression tests for ladder-print filtering and shared snapshot evaluation."""

import json
import tempfile
from pathlib import Path

from gx3cli.gx3_ladder_print import load_live_values, scan_sections, truthy_live_value, select_entries
from gx3cli.gx3_snapshot_explain import build_explanation, evaluate_logic, explain_driver_row, load_snapshot


def entry(blocktype, pos, title=None, devices=(), lines=None):
    return {
        "blocktype": blocktype,
        "pos": pos,
        "title": title,
        "devices": frozenset(devices),
        "lines": lines if lines is not None else ([f"L{pos}"] if pos is not None else []),
    }


def build_entries():
    # Section "A" has an EMPTY-title statement row in the middle (the bug trigger).
    return [
        entry(1, None, title="Sec A"),       # 0 section A title
        entry(0, 100, devices=["M10"]),      # 1 rung
        entry(1, None, title=""),            # 2 empty-title statement (invisible)
        entry(0, 110, devices=["D5330"]),    # 3 rung (still in Sec A)
        entry(0, 120, devices=["M10"]),      # 4 rung (still in Sec A)
        entry(1, None, title="Sec B"),       # 5 section B title
        entry(0, 200, devices=["Y5511"]),    # 6 rung
        entry(5, 210, lines=["END"]),        # 7 end block
    ]


def contact(device: str, role: str = "a", ct_code: str = "") -> dict:
    return {"op": "contact", "device": device, "role": role, "ct_code": ct_code, "position": "0,0"}


def test_snapshot_logic_uses_ladder_print_contact_semantics() -> None:
    values = {"X1": True, "X2": False}
    state, leaves = evaluate_logic(
        {"op": "and", "args": [contact("X1", "a"), contact("X2", "b")]}, values
    )
    assert state == "pass", (state, leaves)
    assert [leaf["condition"] for leaf in leaves] == ["pass", "pass"], leaves

    state, leaves = evaluate_logic(
        {"op": "or", "args": [contact("X2", "a"), contact("X1", "a")]}, values
    )
    assert state == "pass", (state, leaves)
    assert [leaf["condition"] for leaf in leaves] == ["block", "pass"], leaves


def test_snapshot_missing_unknown_and_edge_are_not_false() -> None:
    state, leaves = evaluate_logic(contact("M999", "a"), {})
    assert state == "missing"
    assert leaves[0]["condition"] == "missing"

    state, leaves = evaluate_logic({"op": "predicate", "opcode": ">"}, {"D0": 10})
    assert state == "unknown"
    assert leaves[0]["condition"] == "unknown"

    state, leaves = evaluate_logic(contact("M10", "a", "p"), {"M10": True})
    assert state == "unknown"
    assert "previous-scan" in str(leaves[0]["reason"])

    # A definite blocker proves an AND false even when another value is absent;
    # missing is never silently converted to False.
    state, _ = evaluate_logic(
        {"op": "and", "args": [contact("M10", "a"), contact("M11", "a")]},
        {"M10": False},
    )
    assert state == "block"


def test_stateful_trace_is_scoped_to_current_write_condition() -> None:
    row = {
        "row_id": "001:10",
        "device": "M100",
        "lddb": "001_LDDB.db",
        "pos": 10,
        "parse_status": "exact",
        "driver_roles": ["SET"],
        "driver_effects": ["ON/set"],
        "conditions": [{"device": "X1", "role": "a", "position": "0,0"}],
        "enable_logic": contact("X1", "a"),
        "enable_logic_text": "[X1]",
        "logic_stats": {},
        "execution_guards": [],
        "temporal_predicates": [{"kind": "latch_set", "requires_runtime_state": True}],
    }
    explained = explain_driver_row(row, {"X1": True})
    assert explained["current_enable_condition"] == "pass"
    assert explained["historical_root_cause_supported"] is False
    assert any("stateful/temporal" in warning for warning in explained["warnings"])

    row["execution_guards"] = [{"kind": "conditional_jump_unresolved"}]
    explained = explain_driver_row(row, {"X1": True})
    assert explained["current_enable_condition"] == "unknown"


def test_snapshot_metadata_and_fingerprint_mismatch() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        snapshot = root / "snapshot.json"
        snapshot.write_text(
            json.dumps(
                {
                    "timestamp": "2026-09-06T10:00:00-04:00",
                    "source": "captured-test",
                    "project_fingerprint": "deadbeef",
                    "values": {"X1": True},
                }
            ),
            encoding="utf-8",
        )
        values, metadata = load_snapshot(snapshot)
        assert values == {"X1": True}
        assert metadata["timestamp"] == "2026-09-06T10:00:00-04:00"
        assert metadata["source"] == "captured-test"

        # Give the folder a real analysis identity. Fingerprint validation runs
        # before trace construction, so an intentional mismatch is rejected
        # even if the rest of this tiny folder is not a valid GX3 project.
        (root / "CPU.PRM").write_bytes(b"synthetic CPU parameters")
        try:
            build_explanation(root, "M100", snapshot)
        except SystemExit as exc:
            assert "does not match" in str(exc)
        else:
            raise AssertionError("fingerprint mismatch was not rejected")


def main():
    entries = build_entries()

    # scan_sections: two named sections; empty-title row is not its own section.
    secs = scan_sections(entries)
    assert [s["title"] for s in secs] == ["Sec A", "Sec B"], secs
    a = secs[0]
    assert (a["start_pos"], a["end_pos"], a["rungs"]) == (100, 120, 3), a

    # --section "Sec A": regression — empty-title row must not truncate the block.
    sel = select_entries(entries, sections=["Sec A"])
    kept_pos = [e["pos"] for e in sel if e["blocktype"] == 0]
    assert kept_pos == [100, 110, 120], kept_pos
    assert any(e.get("title") == "Sec A" for e in sel)
    assert all(e.get("title") != "Sec B" for e in sel)

    # --pos-range: inclusive bounds on rung pos.
    sel = select_entries(entries, pos_range=(110, 200))
    assert [e["pos"] for e in sel if e["blocktype"] == 0] == [110, 120, 200]

    # --device: rung with the device plus its preceding section title.
    sel = select_entries(entries, device="D5330")
    assert [e["pos"] for e in sel if e["blocktype"] == 0] == [110]
    assert sel[0]["title"] == "Sec A"  # preceding title pulled in for context

    # --device case-insensitive.
    assert select_entries(entries, device="d5330")

    assert truthy_live_value(True) is True
    assert truthy_live_value(0) is False
    assert truthy_live_value("ON") is True

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "live.json"
        path.write_text(
            json.dumps({"device": "D100", "values": [12, 34]}),
            encoding="utf-8",
        )
        assert load_live_values(str(path)) == {"D100": 12, "D101": 34}

        path.write_text(
            json.dumps({"values": {"x1a": True, "M10": False}}),
            encoding="utf-8",
        )
        assert load_live_values(str(path)) == {"X1A": True, "M10": False}

    test_snapshot_logic_uses_ladder_print_contact_semantics()
    test_snapshot_missing_unknown_and_edge_are_not_false()
    test_stateful_trace_is_scoped_to_current_write_condition()
    test_snapshot_metadata_and_fingerprint_mismatch()

    print("all ladder-print filter and snapshot explanation checks passed")


if __name__ == "__main__":
    main()
