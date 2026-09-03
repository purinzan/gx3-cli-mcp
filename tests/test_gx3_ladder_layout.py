from gx3cli.gx3_ladder_layout import layouts_to_svg, rung_layout
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

    assert layout["dim"] == {"width": 3, "height": 1}
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
