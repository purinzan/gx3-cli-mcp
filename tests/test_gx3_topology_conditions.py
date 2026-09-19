from __future__ import annotations

"""Two parallel contacts are an OR, and reporting them as an AND reverses it.

#86 and #87. `alarm-map` and `timing-chart` rebuilt a condition from the
cross-reference, which records the devices a rung mentions and not how they are
wired, then joined the contacts with " & " / " AND ".

    +--[ M100 ]--+
----|            |----( F0 )
    +--[ M101 ]--+

    reported as   M100 & M101
    actually      M100 OR M101

That is not an incomplete answer. It is the opposite of the rung, on the
commands that describe what raises an alarm and what gates a handshake.

The topology has been in `enable_logic_for_output` all along, with half a dozen
consumers. These two were rebuilding it from the wrong data.

Reading it out and then flattening it to a list would have printed the same
wrong sentence -- the first version of this fix did exactly that, and the
fixtures below are what showed it.
"""

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_alarm_map import RowIndex, row_conditions
from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.review_gx3_project import LadderRow
from test_gx3_ladder_logic import manual_row
from test_gx3_shared_reach import build_xref, write_program


def project(work: Path, cases: list[tuple[dict, str]]) -> tuple[Path, Path]:
    rungs = [
        (f"_guid/{index}", generate_rung(logic, {"type": "coil", "device": device})[0])
        for index, (logic, device) in enumerate(cases)
    ]
    write_program(work / "p", rungs)
    return work / "p", build_xref(work / "p", work / "x.sqlite")


def condition(con, root: Path, device: str, wired: bool = True) -> str:
    row = con.execute(
        "select lddb, pos from xref where device=? and access='write'", (device,)
    ).fetchone()
    assert row is not None, device
    _, _, text = row_conditions(
        con, row["lddb"], row["pos"], device, RowIndex(root) if wired else None
    )
    return text


def generated_row(logic: dict, device: str) -> LadderRow:
    data, rowsize, _ = generate_rung(logic, {"type": "coil", "device": device})
    return LadderRow("test", 0, "", "", 0, rowsize, data, "", [], "exact")


class StaticRows:
    def __init__(self, row: LadderRow) -> None:
        self.row = row

    def get(self, _lddb: str, _pos: int) -> LadderRow:
        return self.row


def commentless_xref() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("create table xref(device text, comment text)")
    return con


def test_two_parallel_contacts_are_an_or() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [({"or": [{"device": "M100"}, {"device": "M101"}]}, "F0")])
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = condition(con, root, "F0")
        finally:
            con.close()
        assert "OR" in text, text
        assert "&" not in text, text


def test_a_series_contact_before_a_branch_keeps_its_shape() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(
            work,
            [({"and": [{"device": "M200"}, {"or": [{"device": "M201"}, {"device": "M202"}]}]}, "F1")],
        )
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = condition(con, root, "F1")
        finally:
            con.close()
        assert "OR" in text and "AND" in text, text
        for device in ("M200", "M201", "M202"):
            assert device in text, (device, text)


def test_a_normally_closed_contact_stays_closed() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(
            work, [({"and": [{"device": "M300"}, {"not": {"device": "M301"}}]}, "F2")]
        )
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = condition(con, root, "F2")
        finally:
            con.close()
        assert "/M301" in text, text


def test_each_output_gets_its_own_condition() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(
            work,
            [
                ({"device": "M400"}, "F3"),
                ({"or": [{"device": "M401"}, {"device": "M402"}]}, "F4"),
            ],
        )
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            first = condition(con, root, "F3")
            second = condition(con, root, "F4")
        finally:
            con.close()
        assert "M400" in first and "M401" not in first, first
        assert "OR" in second and "M400" not in second, second


def test_alarm_self_contact_on_a_live_path_is_self_hold() -> None:
    row = generated_row({"or": [{"device": "M500"}, {"device": "F5"}]}, "F5")
    con = commentless_xref()
    try:
        conds, self_hold, text = row_conditions(con, "test", 0, "F5", StaticRows(row))
    finally:
        con.close()
    assert self_hold is True, (conds, text)
    assert "M500" in text and "F5" in text, text
    assert all("F5" not in item for item in conds), conds


def test_alarm_self_contact_on_a_dead_branch_is_not_self_hold() -> None:
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=500:vt=nn}]}:pos=0,0}"
    blocking_coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=900:vt=nn}]}:pos=1,0}"
    # The branch is after the coil, not at its shared input terminal.
    wire = "e{s=wire:pos=2,1}"
    self_contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=5:vt=nn}]}:pos=3,1}"
    alarm_coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=5:vt=nn}]}:pos=4,1}"
    row = manual_row(
        f"{contact}:{blocking_coil}:{wire}:{self_contact}:{alarm_coil}",
        dim="5x2",
        header="V1:10:1:1:1:1:1:1:1:1:a:M:c:M:a:F:c:F",
        verticals="v{pos=2,1}",
    )
    con = commentless_xref()
    try:
        conds, self_hold, text = row_conditions(con, "test", 0, "F5", StaticRows(row))
    finally:
        con.close()
    assert self_hold is False, (conds, text)
    assert text == "FALSE", text


def test_without_the_rung_the_answer_says_it_is_a_contact_list() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [({"or": [{"device": "M100"}, {"device": "M101"}]}, "F0")])
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            text = condition(con, root, "F0", wired=False)
        finally:
            con.close()
        assert "wiring not read" in text, text


def test_timing_chart_reads_the_same_way() -> None:
    from gx3cli.gx3_timing_chart import RowIndex as TimingRows, same_row_conditions

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root, db = project(work, [({"or": [{"device": "M100"}, {"device": "M101"}]}, "F0")])
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "select lddb, pos from xref where device='F0' and access='write'"
            ).fetchone()
            wired = same_row_conditions(
                con, row["lddb"], row["pos"], TimingRows(root), "F0"
            )
            flat = same_row_conditions(con, row["lddb"], row["pos"])
        finally:
            con.close()
        assert "OR" in wired, wired
        assert "wiring not read" in flat, flat


# #139: constants proved elsewhere in the project also simplify normal coil
# tracing. These regressions are about which upstream conditions still matter
# to the ON question, not about declaring live PLC state.
def _contact(device: str, role: str = "a", position: str = "0,0") -> dict:
    return {
        "op": "contact",
        "role": role,
        "state": "ON" if role == "a" else "OFF",
        "device": device,
        "raw_device": device,
        "device_type": device.rstrip("0123456789"),
        "position": position,
    }


def _fact(device: str, value: bool):
    from gx3cli.gx3_dead_logic import ConstantFact

    return ConstantFact(
        device=device,
        value=value,
        where="SYNTH st1",
        chain=(f"{device}={'ON' if value else 'OFF'}",),
        roots=("synthetic",),
        depth=1,
    )


def test_constant_false_in_and_prunes_the_whole_upstream_branch() -> None:
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic
    from gx3cli.gx3_topology_conditions import simplify_logic_for_trace

    logic = {
        "op": "and",
        "args": [_contact("X0", position="0,0"), _contact("M100", position="1,0"), _contact("M200", "b", "2,0")],
    }
    result = simplify_logic_for_trace(logic, {"M100": _fact("M100", False)})
    assert result.logic_text == "FALSE", result
    assert condition_refs_from_logic(result.logic) == []
    assert {ref["device"] for ref in result.pruned_conditions} == {"X0", "M100", "M200"}


def test_false_or_branch_is_removed_but_other_on_path_is_still_traced() -> None:
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic, logic_to_text
    from gx3cli.gx3_topology_conditions import simplify_logic_for_trace

    logic = {
        "op": "or",
        "args": [
            {"op": "and", "args": [_contact("M100", position="0,0"), _contact("X0", position="1,0")]},
            {"op": "and", "args": [_contact("X1", position="0,1"), _contact("M300", position="1,1")]},
        ],
    }
    result = simplify_logic_for_trace(logic, {"M100": _fact("M100", False)})
    refs = condition_refs_from_logic(result.logic)
    assert {ref["device"] for ref in refs} == {"X1", "M300"}, (logic_to_text(result.logic), refs)
    assert "X0" not in {ref["device"] for ref in refs}


def test_true_or_branch_short_circuits_the_other_upstream_conditions() -> None:
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic
    from gx3cli.gx3_topology_conditions import simplify_logic_for_trace

    logic = {"op": "or", "args": [_contact("M200"), _contact("M400", position="1,0")]}
    result = simplify_logic_for_trace(logic, {"M200": _fact("M200", True)})
    assert result.logic_text == "TRUE"
    assert condition_refs_from_logic(result.logic) == []


def test_b_contact_inverts_the_proven_coil_state_before_pruning() -> None:
    from gx3cli.gx3_topology_conditions import simplify_logic_for_trace

    assert simplify_logic_for_trace(_contact("M200", "b"), {"M200": _fact("M200", True)}).logic_text == "FALSE"
    assert simplify_logic_for_trace(_contact("M100", "b"), {"M100": _fact("M100", False)}).logic_text == "TRUE"


def test_public_trace_row_filter_keeps_only_conditions_left_after_simplification() -> None:
    from gx3cli.trace_gx3_device_dependencies import _filter_row_conditions

    logic = {
        "op": "or",
        "args": [
            {"op": "and", "args": [_contact("M100"), _contact("X0", position="1,0")]},
            _contact("X1", position="0,1"),
        ],
    }
    row = {
        "enable_logic": logic,
        "enable_logic_text": "raw",
        "conditions": [
            {"device": "M100", "role": "a", "required_state": "ON"},
            {"device": "X0", "role": "a", "required_state": "ON"},
            {"device": "X1", "role": "a", "required_state": "ON"},
        ],
    }
    removed = _filter_row_conditions(row, {"M100": _fact("M100", False)})
    assert removed == 2, row
    assert [condition["device"] for condition in row["conditions"]] == ["X1"], row
    assert row["enable_logic_text"] == "[X1]", row
    assert row["raw_enable_logic_text"] == "raw"


def test_public_trace_dispatch_prunes_refs_before_canonical_bfs_queue() -> None:
    from gx3cli import gx3_trace_state as base
    from gx3cli.trace_gx3_device_dependencies import (
        _condition_refs_provider,
    )
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic

    logic = {
        "op": "or",
        "args": [
            {"op": "and", "args": [_contact("M100"), _contact("X0", position="1,0")]},
            _contact("X1", position="0,1"),
        ],
    }
    assert base.condition_refs_from_logic is condition_refs_from_logic
    stats: dict[str, int] = {}
    refs = _condition_refs_provider({"M100": _fact("M100", False)}, stats)(logic)
    assert len(base.condition_refs_from_logic(logic)) == 3

    assert [ref["device"] for ref in refs] == ["X1"], refs
    assert stats["raw_refs"] == 3, stats
    assert stats["kept_refs"] == 1, stats
    assert stats["raw_refs"] - stats["kept_refs"] == 2, stats


def test_trace_inputs_are_loaded_once_and_calls_are_isolated() -> None:
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch
    from gx3cli import gx3_trace_state as base
    from gx3cli.trace_gx3_device_dependencies import build_trace

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        roots = []
        for index in range(2):
            root = work / f"p{index}"
            write_program(root, [("r", generate_rung({"device": f"X{index}"}, {"type": "coil", "device": "Y0"})[0])])
            roots.append(root)

        def trace(root):
            return build_trace(root, "Y0", 4, 100, True, True)

        with patch.object(base, "load_rows", wraps=base.load_rows) as rows, \
             patch.object(base, "load_comments_for_root", wraps=base.load_comments_for_root) as comments, \
             patch.object(base, "load_label_resolver", wraps=base.load_label_resolver) as labels:
            trace(roots[0])
            assert rows.call_count == comments.call_count == labels.call_count == 1

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(trace, roots))
        for index, result in enumerate(results):
            conditions = [c["device"] for r in result["driver_rows"] for c in r["conditions"]]
            assert f"X{index}" in conditions and f"X{1-index}" not in conditions, conditions

        inputs = base.load_trace_inputs(roots[0])
        try:
            base.build_trace(roots[1], "Y0", 4, 100, True, True, inputs=inputs)
        except ValueError:
            pass
        else:
            raise AssertionError("inputs from another root accepted")

        def nested_then_fail(node):
            trace(roots[1])
            raise RuntimeError("injected provider failure")

        try:
            base.build_trace(roots[0], "Y0", 4, 100, True, True, condition_refs_provider=nested_then_fail)
        except RuntimeError:
            pass
        else:
            raise AssertionError("provider was not called")
        assert trace(roots[0])["driver_rows"]


def test_trace_uses_one_communication_input_for_pruning_and_classification() -> None:
    import json
    import os
    import subprocess
    from unittest.mock import patch
    from gx3cli import gx3_trace_state as base
    from gx3cli import trace_gx3_device_dependencies as facade

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "p"
        write_program(root, [("r", generate_rung({"device": "M100"}, {"type": "coil", "device": "Y0"})[0])])
        (work / "outputs").mkdir()
        header = "device_start,device_end,network_label\n"
        # Conflicting real CSV inputs expose the old facade/base selection gap.
        (work / "outputs" / "proof_refresh_areas.csv").write_text(header + "M100,M100,selected-network\n", encoding="utf-8")
        (work / "proof_refresh_areas.csv").write_text(header + "M100,M100,legacy-network\n", encoding="utf-8")
        previous = Path.cwd()
        try:
            os.chdir(work)
            with patch.dict(os.environ, {"PROJECT_COMM_PREFIX": "proof"}), \
                 patch.object(base, "load_refresh_areas", wraps=base.load_refresh_areas) as refresh, \
                 patch.object(base, "load_unit_io_areas", wraps=base.load_unit_io_areas) as units, \
                 patch.object(facade, "load_trace_constant_context", wraps=facade.load_trace_constant_context) as context:
                result = facade.build_trace(root, "Y0", 4, 100, True, True)
                assert refresh.call_count == units.call_count == 1, (refresh.call_count, units.call_count)
                assert context.call_args.args[2][0].network_label == "selected-network"
                conditions = [c for row in result["driver_rows"] for c in row["conditions"] if c["device"] == "M100"]
                assert conditions and all(c["refresh_network_label"] == "selected-network" for c in conditions), conditions
            # Exercise the real command entry point, not only its API facade.
            environment = dict(os.environ, PROJECT_COMM_PREFIX="proof", PYTHONIOENCODING="utf-8",
                               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            command = [sys.executable, "-m", "gx3cli.trace_gx3_device_dependencies", "Y0",
                       "--root", str(root), "--strict-logic", "--no-link-map", "--format", "json"]
            for expected in ("selected-network", "legacy-network"):
                completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", env=environment)
                assert completed.returncode == 0, completed.stderr
                result = json.loads(completed.stdout)
                conditions = [c for row in result["driver_rows"] for c in row["conditions"] if c["device"] == "M100"]
                assert conditions and all(c["refresh_network_label"] == expected for c in conditions), conditions
                if expected == "selected-network":
                    (work / "outputs" / "proof_refresh_areas.csv").unlink()
        finally:
            os.chdir(previous)


def test_trace_finds_prepared_indexes_outside_the_current_directory() -> None:
    import json
    import os
    import subprocess
    from gx3cli.gx3_workspace import prepare
    from gx3cli.trace_gx3_device_dependencies import build_trace

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "owner" / "project"
        root.parent.mkdir()
        write_program(root, [
            ("writer", generate_rung({"device": "SM401"}, {"type": "coil", "device": "M100"})[0]),
            ("reader", generate_rung({"device": "M100"}, {"type": "coil", "device": "Y0"})[0]),
        ])
        built = prepare(root)
        assert built.ready
        unrelated = work / "unrelated"
        unrelated.mkdir()
        previous = Path.cwd()
        try:
            os.chdir(unrelated)
            result = build_trace(root, "Y0", 4, 100, True, True)
            assert result["constant_pruning"]["enabled"], result["constant_pruning"]
            assert result["constant_pruning"]["proven_constants"] >= 1, result
            environment = dict(os.environ, PYTHONIOENCODING="utf-8",
                               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            completed = subprocess.run(
                [sys.executable, "-m", "gx3cli.trace_gx3_device_dependencies", "Y0",
                 "--root", str(root), "--strict-logic", "--no-link-map", "--format", "json"],
                capture_output=True, text=True, encoding="utf-8", env=environment)
            assert completed.returncode == 0, completed.stderr
            cli_result = json.loads(completed.stdout)
            assert cli_result["constant_pruning"]["enabled"], cli_result["constant_pruning"]
            assert cli_result["constant_pruning"]["proven_constants"] >= 1, cli_result
            assert not list(unrelated.glob(".gx3_index/*")), "read path created an index"
        finally:
            os.chdir(previous)


def test_trace_rejects_source_change_during_input_loading() -> None:
    from unittest.mock import patch
    from gx3cli import gx3_trace_state as base
    from gx3cli.trace_gx3_device_dependencies import build_trace

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "project"
        write_program(root, [("r", generate_rung({"device": "X0"}, {"type": "coil", "device": "Y0"})[0])])
        original = base.load_rows
        def changed_rows(*args, **kwargs):
            rows = original(*args, **kwargs)
            # A real program edit after reading the old rows must not leave an
            # answer about X0 certified against a new X1 source version.
            from contextlib import closing
            with closing(sqlite3.connect(next(root.glob("*_LDDB.db")))) as con, con:
                con.execute("update LadderBlocks set data=?", (generate_rung({"device": "X1"}, {"type": "coil", "device": "Y0"})[0],))
            return rows
        with patch.object(base, "load_rows", side_effect=changed_rows):
            try:
                build_trace(root, "Y0", 4, 100, True, True)
            except (ValueError, SystemExit) as exc:
                assert "changed" in str(exc), exc
            else:
                raise AssertionError("trace accepted mixed project versions")
        recovered = build_trace(root, "Y0", 4, 100, True, True)
        conditions = [c["device"] for row in recovered["driver_rows"] for c in row["conditions"]]
        assert "X1" in conditions and "X0" not in conditions, conditions


def test_trace_rejects_existing_st_or_memory_content_change_during_traversal() -> None:
    import os
    from contextlib import closing
    from gx3cli import gx3_trace_state as base
    from gx3cli.gx3_input_identity import fingerprint
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic

    for filename in ("002_STDB.db", "003_DM.db"):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "project"
            write_program(root, [("r", generate_rung({"device": "X0"}, {"type": "coil", "device": "Y0"})[0])])
            source = root / filename
            # Exercise dependency lifetime, not the ST/DM semantic decoder:
            # valid SQLite contents exist before the input snapshot is taken.
            with closing(sqlite3.connect(source)) as con, con:
                con.execute("create table Source(Code text)")
                con.execute("insert into Source values ('D100 := D101;')")
            inputs = base.load_trace_inputs(root)
            original_stamp = source.stat()
            changed = False
            def change_during_traversal(node):
                nonlocal changed
                if not changed:
                    with closing(sqlite3.connect(source)) as con, con:
                        con.execute("update Source set Code='D100 := D102;'")
                    assert source.stat().st_size == original_stamp.st_size
                    os.utime(source, ns=(original_stamp.st_atime_ns, original_stamp.st_mtime_ns))
                    changed = True
                return condition_refs_from_logic(node)
            try:
                base.build_trace(root, "Y0", 4, 100, True, True, inputs=inputs,
                                 condition_refs_provider=change_during_traversal)
            except SystemExit as exc:
                assert "changed" in str(exc), exc
            else:
                raise AssertionError(f"trace accepted changed existing {filename}")
            assert changed
            result = base.build_trace(root, "Y0", 4, 100, True, True)
            assert result["input_sha256"] == fingerprint(root)
            # Close discipline: no connection from the failed read remains.
            moved = source.with_suffix(".moved")
            source.rename(moved)
            moved.rename(source)


def test_trace_uses_the_csv_paths_recorded_by_the_real_index_builder() -> None:
    import json
    import os
    import subprocess
    from gx3cli import gx3_index_lite as lite
    from gx3cli.gx3_workspace import index_paths

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "project"
        write_program(root, [("r", generate_rung({"device": "M100"}, {"type": "coil", "device": "Y0"})[0])])
        csv_path = work / "explicit-refresh.csv"
        csv_path.write_text("device_start,device_end,network_label\nM100,M100,recorded-network\n", encoding="utf-8")
        index, _ = index_paths(root)
        assert lite.main(["build", "--root", str(root), "--out", str(index),
                          "--refresh-csv", str(csv_path), "--unit-csv", str(work / "absent-units.csv")]) == 0
        unrelated = work / "elsewhere"
        (unrelated / "outputs").mkdir(parents=True)
        (unrelated / "outputs" / "proof_refresh_areas.csv").write_text(
            "device_start,device_end,network_label\nM100,M100,wrong-cwd-network\n", encoding="utf-8")
        environment = dict(os.environ, PROJECT_COMM_PREFIX="proof", PYTHONIOENCODING="utf-8",
                           PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        completed = subprocess.run(
            [sys.executable, "-m", "gx3cli.trace_gx3_device_dependencies", "Y0", "--root", str(root),
             "--strict-logic", "--no-link-map", "--format", "json"], cwd=unrelated,
            capture_output=True, text=True, encoding="utf-8", env=environment)
        assert completed.returncode == 0, completed.stderr
        result = json.loads(completed.stdout)
        assert result["communication_input_source"] == "validated_index", result
        assert result["communication_inputs"]["refresh_csv"]["path"] == str(csv_path.absolute())
        conditions = [c for row in result["driver_rows"] for c in row["conditions"] if c["device"] == "M100"]
        assert conditions and all(c["refresh_network_label"] == "recorded-network" for c in conditions), conditions


def test_trace_rejects_communication_csv_changes_and_recovers() -> None:
    import os
    from unittest.mock import patch
    from gx3cli import gx3_trace_state as base
    from gx3cli.gx3_ladder_logic import condition_refs_from_logic

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "project"
        write_program(root, [("r", generate_rung({"device": "M100"}, {"type": "coil", "device": "Y0"})[0])])
        csv_path = work / "proof_refresh_areas.csv"
        header = "device_start,device_end,network_label\n"
        csv_path.write_text(header + "M100,M100,old-network\n", encoding="utf-8")
        previous = Path.cwd()
        try:
            os.chdir(work)
            with patch.dict(os.environ, {"PROJECT_COMM_PREFIX": "proof"}):
                inputs = base.load_trace_inputs(root)
                stamp = csv_path.stat()
                changed = False
                def change(node):
                    nonlocal changed
                    if not changed:
                        csv_path.write_text(header + "M100,M100,new-network\n", encoding="utf-8")
                        assert csv_path.stat().st_size == stamp.st_size
                        os.utime(csv_path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                        changed = True
                    return condition_refs_from_logic(node)
                try:
                    base.build_trace(root, "Y0", 4, 100, True, True, inputs=inputs, condition_refs_provider=change)
                except SystemExit as exc:
                    assert "communication CSV inputs changed" in str(exc), exc
                else:
                    raise AssertionError("changed CSV accepted")
                assert changed
                result = base.build_trace(root, "Y0", 4, 100, True, True)
                conditions = [c for row in result["driver_rows"] for c in row["conditions"] if c["device"] == "M100"]
                assert conditions and all(c["refresh_network_label"] == "new-network" for c in conditions), conditions
        finally:
            os.chdir(previous)


def test_postfilter_row_key_includes_the_driven_device() -> None:
    from gx3cli.trace_gx3_device_dependencies import _row_device_key

    y0_row = {"row_id": "P1:10", "device": "Y0"}
    y1_row = {"row_id": "P1:10", "device": "Y1"}
    assert _row_device_key(y0_row) != _row_device_key(y1_row)
    assert _row_device_key({"row_id": "P1:10", "from_device": "Y0"}) == _row_device_key(y0_row)
    assert _row_device_key({"row_id": "P1:10", "from_device": "Y1"}) == _row_device_key(y1_row)


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} topology condition checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
