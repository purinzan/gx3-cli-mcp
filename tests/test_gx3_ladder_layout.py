from gx3cli.gx3_ladder_layout import GRID_CELLS, layouts_to_svg, rung_layout
from gx3cli.gx3_ladder_logic import logic_to_text, enable_logic_for_output, output_elements_for
from gx3cli.review_gx3_project import LadderRow


def manual_row(
    elements: str,
    dim: str = "3x1",
    header: str = "V1:4:1:1:1:1:a:M:c:M",
    verticals: str = "",
) -> LadderRow:
    vs = f":vs=[{verticals}]" if verticals else ""
    data = f"{header}:cb{{fg=fg{{dim={dim}:es=[{elements}]{vs}}}}}"
    return LadderRow("test", 10, "block", dim, 0, 1, data, "", [], "exact")


def test_layout_keeps_coordinates_and_operands() -> None:
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}"
    wire = "e{s=wire:pos=1,0}"
    coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=2,0}"
    row = manual_row(f"{contact}:{wire}:{coil}")

    layout = rung_layout(row)

    # Every rung lays out to the printed grid width whatever it holds, so a
    # page of them is not a ragged stack; the elements keep their own columns.
    assert layout["dim"] == {"width": GRID_CELLS, "height": 1}
    assert [(item["kind"], item["x"], item["y"], item["operands"]) for item in layout["elements"]] == [
        ("contact", 0, 0, ["M100"]),
        ("coil", 2, 0, ["M200"]),
    ]
    assert layout["wires"] == [{"x1": 1, "y": 0, "x2": 2}]


def test_svg_contains_ladder_symbols_and_device_labels() -> None:
    contact = "e{s=ce{op=ct{op=#:ct=b:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}"
    coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=1,0}"
    row = manual_row(f"{contact}:{coil}", dim="2x1", header="V1:4:1:1:1:1:b:M:c:M")

    payload = {"root": "test", "program": "test", "rungs": [rung_layout(row)]}
    svg = layouts_to_svg(payload)

    assert svg.startswith("<svg")
    assert "M100" in svg
    assert "M200" in svg
    assert "<ellipse" in svg
    assert 'class="mark"' in svg


def test_layout_does_not_change_logic_analysis() -> None:
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}"
    wire = "e{s=wire:pos=1,0}"
    coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=2,1}"
    row = manual_row(f"{contact}:{wire}:{coil}", dim="3x2", verticals="v{pos=2,1}")

    layout = rung_layout(row)
    output = output_elements_for(row, "M200")[0]

    assert layout["verticals"] == [{"x": 2, "y1": 0, "y2": 1}]
    assert logic_to_text(enable_logic_for_output(row, output)) == "[M100]"


def test_instruction_width_uses_cells_instead_of_remaining_rail() -> None:
    import xml.etree.ElementTree as ET
    from gx3cli.gx3_ladder_layout import CELL_W, RAIL_PAD
    for opcode, addresses in [("SET", [10]), ("MOV", [0, 10]), ("TO", [0, 10, 20, 30])]:
        for x in (1, 4):
            args = ":".join(f"d{{s=#:a={n}:vt=nn}}" for n in addresses)
            instruction = f"e{{s=ce{{op=in{{op=#:ct=a:as=[as{{vt=Abl}}]}}:args=[{args}]}}:pos={x},0}}"
            row = manual_row(instruction, dim="12x1", header="V1:8:1:1:1:1:1:1:" + opcode + ":D" * len(addresses))
            layout = rung_layout(row)
            assert layout["elements"][0]["opcode"] == opcode
            svg = layouts_to_svg({"root": "test", "program": "test", "rungs": [layout]})
            tree = ET.fromstring(svg)
            box = next(e for e in tree.iter() if e.get("class") == "box")
            assert float(box.get("width")) == (1 + len(addresses)) * CELL_W - 10
            start = RAIL_PAD + (12 - 1 - len(addresses)) * CELL_W
            assert float(box.get("x")) == start + 5
            assert float(box.get("x")) + float(box.get("width")) == RAIL_PAD + 12 * CELL_W - 5
            assert any(e.get("class") == "wire" and e.get("x1") == str(RAIL_PAD + x * CELL_W) and e.get("x2") == str(start) for e in tree.iter())
            assert layout["elements"][0]["x"] == x


def main() -> int:
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for _name, test in tests:
        test()
    print(f"{len(tests)} ladder-layout checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
