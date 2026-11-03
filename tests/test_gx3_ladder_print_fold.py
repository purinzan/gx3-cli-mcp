"""Synthetic LD bytes through the actual parser and both display renderers.

These fixtures carry explicit wires so an independent terminal graph can check
folds without treating the folding algorithm as its own oracle.
"""
from __future__ import annotations

import re
import sqlite3
import sys
import tempfile
import unicodedata
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from cross_check_corpus import check_project, svg_wiring_differs, wire_signature

from gx3cli.gx3_ladder_layout import CELL_H, CELL_W, RAIL_PAD, ROW_PAD_Y, layouts_to_svg, rung_layout
from gx3cli.gx3_ladder_print import N_CELLS, parse_rung, render_rung
from gx3cli.review_gx3_project import LadderRow


def make_row(ops, wires=(), verticals=(), height=None):
    """ops: (role, x, y, operands), where operands are (type, address)."""
    tokens, elements = [], []
    for role, x, y, operands in ops:
        tokens.extend([role, *["K_1" if kind == "K" else kind for kind, _ in operands]])
        kind = "ct" if role in ("a", "b", "=", "<>") else "cl" if role == "c" else "in"
        args = ":".join(f"c{{v={number}}}" if kind == "K" else f"d{{s=#:a={number}:vt=nn}}" for kind, number in operands)
        types = ":".join("as{vt=Abl}" for _ in operands)
        elements.append(f"e{{s=ce{{op={kind}{{op=#:ct=a:as=[{types}]}}:args=[{args}]}}:pos={x},{y}}}")
    elements.extend(f"e{{s=wire:pos={x},{y}}}" for x, y in wires)
    width = max([1, *[x + 1 + len(args) for _, x, _, args in ops], *[x + 1 for x, _ in wires]])
    height = height or max([0, *[y for _, _, y, _ in ops], *[y for _, y in wires], *[y for _, y in verticals]]) + 1
    vs = ":".join(f"v{{pos={x},{y}}}" for x, y in verticals)
    lengths = ":".join(str(len(t.encode("utf-16-le")) // 2) for t in tokens)
    data = f"V1:{len(tokens)}:{lengths}:" + ":".join(tokens) + f":cb{{fg=fg{{dim={width}x{height}:es=[{':'.join(elements)}]:vs=[{vs}]}}}}"
    return LadderRow("001_LDDB.db", 10, "synthetic", "", 0, 0, data, "", [], "")


def chain(count):
    return make_row([("a", x, 0, [("M", 100 + x)]) for x in range(count)] + [("c", count, 0, [("M", 900)])])


def cases():
    yield "short", chain(2)
    for count in (10, 11, 12, 20, 21, 22, 35, 90):
        yield f"chain-{count}", chain(count)
    yield "predicates-across-two-folds", make_row(
        [("a", x, 0, [("M", 100 + x)]) for x in [*range(9), *range(12, 19)]]
        + [("=", 9, 0, [("D", 0), ("D", 10)]), ("=", 19, 0, [("D", 20), ("D", 30)]),
           ("c", 22, 0, [("M", 900)])],
    )
    # The merge is exactly at the cut chosen by the overflowing MOV.
    yield "merge-at-cut", make_row(
        [("a", x, 0, [("M", 100 + x)]) for x in range(9)]
        + [("a", x, 1, [("M", 200 + x)]) for x in range(9)]
        + [("MOV", 9, 0, [("D", 0), ("D", 10)]), ("c", 12, 1, [("M", 900)])],
        wires=[(x, 1) for x in range(9, 12)], verticals=[(9, 1)],
    )
    yield "wire-only-branch", make_row(
        [("a", 0, 0, [("M", 100)]), ("c", 16, 0, [("M", 900)])],
        wires=[(x, y) for y in (0, 1) for x in range(1, 16)],
        verticals=[(1, 1), (16, 1)],
    )
    # A fitting contact beyond the first fold column must move with the box.
    yield "fitting-element-right-of-cut", make_row(
        [("a", x, 0, [("M", 100 + x)]) for x in range(9)]
        + [("MOV", 9, 0, [("D", 0), ("D", 10)]), ("a", 10, 1, [("M", 300)]), ("c", 12, 1, [("M", 900)])],
        wires=[(9, 1), (11, 1)], verticals=[(9, 1), (12, 1)],
    )
    yield "through-vertical-at-cut", make_row(
        [("a", x, y, [("M", 100 + y * 100 + x)]) for y in (0, 2) for x in range(9)]
        + [("MOV", 9, 0, [("D", 0), ("D", 10)]), ("c", 13, 2, [("M", 900)])],
        wires=[(x, 2) for x in range(9, 13)], verticals=[(9, 1), (9, 2)],
    )
    yield "right-vertical-intermediate-row", make_row(
        [("a", 0, 0, [("M", 100)]), ("c", 25, 2, [("M", 900)])],
        wires=[(x, 0) for x in range(1, 25)], verticals=[(25, 1), (25, 2)],
    )
    yield "separate-verticals", make_row(
        [("a", 0, 0, [("M", 100)]), ("c", 6, 2, [("M", 900)])],
        wires=[(1, 0), (4, 2), (5, 2)], verticals=[(2, 1), (4, 2)],
    )


def tree_for(row, comments=None, step=None):
    return ET.fromstring(layouts_to_svg({"rungs": [rung_layout(row, comments, step=step)]}))


def test_all_contacts_survive_multiple_print_folds():
    for count in (11, 12, 20, 21, 22, 35, 90):
        row = chain(count)
        printed = "\n".join(render_rung(row, {}, 0))
        names = re.findall(r"\bM\d+\b", printed)
        expected = [f"M{100 + x}" for x in range(count)] + ["M900"]
        assert sorted(names) == sorted(expected), (count, names)


def test_fold_markers_pair_and_continuations_do_not_start_at_the_rail():
    row = chain(35)
    layout = rung_layout(row)
    assert layout["dim"]["height"] == 4, layout["dim"]
    tree = tree_for(row)
    markers = [n.text for n in tree.iter() if n.get("class") == "fold-label"]
    assert sorted(markers) == ["K0 →", "K0 →", "K1 →", "K1 →", "K2 →", "K2 →"], markers
    assert all(e["x"] >= 1 for e in layout["elements"] if e["y"] > 0)


def test_last_svg_row_reaches_both_rails():
    row = make_row([("a", 0, 0, [("M", 100)]), ("c", 1, 2, [("M", 900)])], verticals=[(1, 1), (1, 2)])
    rails = [n for n in tree_for(row).iter() if n.get("class") == "rail"]
    last_center = ROW_PAD_Y + 2 * CELL_H + CELL_H // 2
    assert all(float(n.get("y2")) >= last_center for n in rails)


def test_contact_wires_reach_the_actual_symbol_bars():
    tree = tree_for(chain(2))
    endpoints = {(float(n.get(x)), float(n.get(y))) for n in tree.iter() if n.get("class") == "wire"
                 for x, y in (("x1", "y1"), ("x2", "y2"))}
    bars = [n for n in tree.iter() if n.get("class") == "symbol" and n.tag.endswith("line")]
    assert bars
    for bar in bars:
        center = (float(bar.get("x1")), (float(bar.get("y1")) + float(bar.get("y2"))) / 2)
        assert center in endpoints, center


def test_operand_comment_keeps_its_slot_and_width():
    row = make_row([("MOV", 0, 0, [("K", 0), ("D", 10)])])
    tree = tree_for(row, {("D", 10): "DestinationLong"}, step=0)
    labels = {n.text: float(n.get("x")) for n in tree.iter() if n.get("class") == "operand-label"}
    comments = [n for n in tree.iter() if n.get("class") == "comment"]
    assert comments and all(float(n.get("x")) == labels["D10"] for n in comments)
    assert comments[0].text == "DestinationL", comments[0].text
    assert any("step 0" in (n.text or "") for n in tree.iter() if n.get("class") == "pos")


def test_empty_stretch_between_verticals_is_not_wired():
    row = dict(cases())["separate-verticals"]
    cy = ROW_PAD_Y + CELL_H + CELL_H // 2
    left, right = RAIL_PAD + 2 * CELL_W, RAIL_PAD + 4 * CELL_W
    for n in tree_for(row).iter():
        if n.get("class") == "wire" and n.get("y1") == str(cy) and n.get("y2") == str(cy):
            a, b = sorted((float(n.get("x1")), float(n.get("x2"))))
            assert not (a < right and b > left), ET.tostring(n)


def test_rendered_svg_preserves_the_source_terminal_nets():
    from gx3cli.gx3_ladder_layout import _svg_rung
    for name, row in cases():
        layout = rung_layout(row)
        assert [e["operands"] for e in layout["elements"]] == [op.operands for op in parse_rung(row)[0]], name
        svg = "\n".join(_svg_rung(layout, 0))
        assert not svg_wiring_differs(row, layout, svg), name


def test_geometry_oracle_detects_an_invented_wire():
    from gx3cli.gx3_ladder_layout import _svg_rung
    row = dict(cases())["separate-verticals"]
    layout = rung_layout(row)
    svg = "\n".join(_svg_rung(layout, 0))
    cy = ROW_PAD_Y + CELL_H + CELL_H // 2
    bridge = f'<line class="wire" x1="{RAIL_PAD + 2 * CELL_W}" y1="{cy}" x2="{RAIL_PAD + 4 * CELL_W}" y2="{cy}" />'
    assert svg_wiring_differs(row, layout, svg + bridge)


def test_printed_wires_preserve_the_source_terminal_nets():
    # Locate the fixture's unique device labels in the actual printed text;
    # no fold coordinates or gap decisions are used to construct this graph.
    def display_width(text):
        return sum(2 if unicodedata.east_asian_width(c) in "WFA" else 1 for c in text)

    for name, row in cases():
        ops, vs, ws = parse_rung(row)
        lines = render_rung(row, {}, None)
        source = [((x, y), (end, y)) for x, y, end in ws]
        source += [((x, y - 1), (x, y)) for x, y in vs]
        source.append(((0, 0), (0, max([0, *[o.y for o in ops], *[y for _, y in vs]]))))
        source_terms, actual_terms = {}, {}
        for i, op in enumerate(ops):
            if op.is_contact or op.is_coil:
                found = [(j, m.start()) for j, line in enumerate(lines) if j % 8 == 0
                         for m in re.finditer(r"\b" + re.escape(op.operands[0]) + r"\b", line)]
                assert len(found) == 1, (name, op.operands, found)
                j, index = found[0]
                col = display_width(lines[j][:index])
                y = j + 2
                actual_terms[f"{i}:in"] = (col, y)
            else:
                found = [(j, m.start()) for j, line in enumerate(lines) if all(t in line for t in op.operands)
                         for m in re.finditer(r"\[" + re.escape(op.opcode_text()) + r"\s", line)]
                assert len(found) == 1, (name, op.role, found)
                y, index = found[0]
                col = display_width(lines[y][:index])
                actual_terms[f"{i}:in"] = (col, y)
            source_terms[f"{i}:in"] = (op.x, op.y)
            if op.is_contact:
                source_terms[f"{i}:out"] = (op.x + 1, op.y)
                actual_terms[f"{i}:out"] = (col + 6, y)
            elif op.is_inline_box:
                span = 1 + len(op.operands)
                source_terms[f"{i}:out"] = (op.x + span, op.y)
                actual_terms[f"{i}:out"] = (col + 14 * span - 2, y)
        segments, markers = [], {}
        for j, line in enumerate(lines):
            col = 0
            for ch in line:
                if col != 196:
                    if ch in "─┼┤├┴┬┘┐└┌":
                        segments.append(((col, j), (col + 2, j)))
                    if ch in "│┼┤├┴┬┘┐└┌":
                        segments.append(((col, j - 0.5), (col, j + 0.5)))
                col += display_width(ch)
            for m in re.finditer(r"(K\d+)\s+→", line):
                col = display_width(line[:m.start()])
                markers.setdefault(m.group(1), []).append((col + 12 if col == 30 else col, j))
        assert all(len(points) == 2 for points in markers.values()), name
        expected = wire_signature(source, source_terms)
        actual = wire_signature(segments, actual_terms, markers.values())
        assert expected == actual, (name, sorted(set(expected) - set(actual)), sorted(set(actual) - set(expected)))


def test_comment_width_boundaries_and_missing_step():
    from gx3cli.gx3_ladder_layout import _comment_lines
    assert _comment_lines("ABCDEFGHIJKL") == ["ABCDEFGHIJKL"]
    assert _comment_lines("ABCDEFGHIJKLM") == ["ABCDEFGHIJKL", "M"]
    assert _comment_lines("あいうえおかき") == ["あいうえおか", "き"]
    assert _comment_lines("A" * 25) == ["A" * 12, "A" * 9 + "..."]
    assert _comment_lines("A" * 11 + "あ" * 6 + "B")[-1].endswith("...")
    assert any("step ?" in (n.text or "") for n in tree_for(chain(2)).iter() if n.get("class") == "pos")


def test_empty_and_wire_only_rows_and_oversized_instruction():
    empty = make_row([])
    assert rung_layout(empty)["dim"] == {"width": 12, "height": 1}
    assert render_rung(empty, {}, 0)
    for count in (11, 12, 13):
        assert rung_layout(make_row([], wires=[(x, 0) for x in range(count)]))["dim"]["width"] == 12
    row = make_row([], wires=[(x, 0) for x in range(35)])
    layout = rung_layout(row)
    assert layout["dim"]["height"] == 4 and len(layout["folds"]) == 3
    assert "K2" in "\n".join(render_rung(row, {}, None))
    for width in (10, 11, 12):
        row = make_row([("MOV", 0, 0, [("D", i) for i in range(width - 1)])])
        assert len(rung_layout(row)["elements"][0]["operands"]) == width - 1
    row = make_row([("MOV", 0, 0, [("D", i) for i in range(12)])])
    for render in (rung_layout, lambda row: render_rung(row, {}, None)):
        try:
            render(row)
        except ValueError as exc:
            assert "instruction footprint" in str(exc)
        else:
            raise AssertionError("an unrepresentable instruction must not be silently clipped")


def test_cross_check_reports_a_render_failure_and_keeps_other_rows():
    from gx3cli.gx3_synthetic_project import create_synthetic_project
    with tempfile.TemporaryDirectory() as tmp:
        root = create_synthetic_project(Path(tmp) / "project")
        row = make_row([("MOV", 0, 0, [("D", i) for i in range(12)])])
        with sqlite3.connect(root / "001_LDDB.db") as con:
            con.execute("insert into LadderBlocks values ('oversized', 30, 0, ?, 1, 0, 0)", (row.data,))
        outcome = check_project(root)
        assert outcome["counts"]["rungs"] == 4
        assert outcome["counts"]["print_error"] == outcome["counts"]["svg_error"] == 1
        assert outcome["counts"]["print_missing"] == 0


def main():
    failed = []
    for name, func in sorted(globals().copy().items()):
        if name.startswith("test_") and callable(func):
            try:
                func()
                print("PASS", name)
            except AssertionError as exc:
                failed.append(name)
                print("FAIL", name, str(exc)[:500])
    assert not failed, failed


if __name__ == "__main__":
    main()
