from gx3cli.gx3_intermediate_tool import generate_rung
from gx3cli.gx3_ladder_diagram import render_row_diagram
from gx3cli.gx3_ladder_logic import enable_logic_for_output, logic_to_text, output_elements_for
from gx3cli.gx3_matiec_export import StBuildContext, logic_to_st
from gx3cli.review_gx3_project import LadderRow


def logic_text_for_generated(logic):
    data, rowsize, _ = generate_rung(logic, {"type": "coil", "device": "M200"})
    row = LadderRow("test", 0, "", "", 0, rowsize, data, "", [], "exact")
    output = output_elements_for(row, "M200")[0]
    return logic_to_text(enable_logic_for_output(row, output))


def manual_row(
    elements: str,
    dim: str = "3x1",
    header: str = "V1:4:1:1:1:1:a:M:c:M",
    verticals: str = "",
) -> LadderRow:
    vs = f":vs=[{verticals}]" if verticals else ""
    data = f"{header}:cb{{fg=fg{{dim={dim}:es=[{elements}]{vs}}}}}"
    return LadderRow("test", 0, "", dim, 0, 1, data, "", [], "exact")


def test_blank_horizontal_gap_is_not_inferred_as_a_wire():
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}"
    coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=2,0}"
    row = manual_row(f"{contact}:{coil}")
    output = output_elements_for(row, "M200")[0]
    assert logic_to_text(enable_logic_for_output(row, output)) == "FALSE"


def test_explicit_wire_carries_a_horizontal_gap():
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}"
    wire = "e{s=wire:pos=1,0}"
    coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=2,0}"
    row = manual_row(f"{contact}:{wire}:{coil}")
    output = output_elements_for(row, "M200")[0]
    assert logic_to_text(enable_logic_for_output(row, output)) == "[M100]"


def test_left_rail_is_not_assumed_on_every_y_row():
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=1,1}"
    coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=2,1}"
    row = manual_row(f"{contact}:{coil}", dim="3x2")
    output = output_elements_for(row, "M200")[0]
    assert logic_to_text(enable_logic_for_output(row, output)) == "FALSE"


def test_driver_sink_does_not_feed_a_vertical_branch():
    contact_a = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}"
    coil_a = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=1,0}"
    wire = "e{s=wire:pos=1,1}"
    contact_b = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=101:vt=nn}]}:pos=2,1}"
    coil_b = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=201:vt=nn}]}:pos=3,1}"
    elements = f"{contact_a}:{coil_a}:{wire}:{contact_b}:{coil_b}"
    row = manual_row(
        elements,
        dim="4x2",
        header="V1:8:1:1:1:1:1:1:a:M:c:M:a:M:c:M",
        verticals="v{pos=1,1}",
    )
    output_a = output_elements_for(row, "M200")[0]
    output_b = output_elements_for(row, "M201")[0]
    assert logic_to_text(enable_logic_for_output(row, output_a)) == "[M100]"
    assert logic_to_text(enable_logic_for_output(row, output_b)) == "FALSE"


def main():
    test_blank_horizontal_gap_is_not_inferred_as_a_wire()
    test_explicit_wire_carries_a_horizontal_gap()
    test_left_rail_is_not_assumed_on_every_y_row()
    test_driver_sink_does_not_feed_a_vertical_branch()
    cases = [
        ("single", {"device": "M100"}, "[M100]"),
        (
            "and_series",
            {"and": [{"device": "M100"}, {"device": "M101"}]},
            "([M100] AND [M101])",
        ),
        (
            "or_parallel",
            {"or": [{"device": "M100"}, {"device": "M101"}]},
            "([M100] OR [M101])",
        ),
        (
            "nested_and_or",
            {"and": [{"device": "M100"}, {"or": [{"device": "M101"}, {"device": "M102"}]}]},
            "(([M100] AND [M101]) OR ([M100] AND [M102]))",
        ),
        (
            "cartesian_or_and",
            {
                "and": [
                    {"or": [{"device": "M100"}, {"device": "M101"}]},
                    {"or": [{"device": "M102"}, {"device": "M103"}]},
                ]
            },
            "(([M100] AND [M102]) OR ([M100] AND [M103]) OR ([M101] AND [M102]) OR ([M101] AND [M103]))",
        ),
        (
            "not_contact",
            {"not": {"device": "M100"}},
            "[/M100]",
        ),
        (
            "always_on_removed_from_and",
            {"and": [{"device": "SM400"}, {"device": "M100"}]},
            "[M100]",
        ),
        (
            "always_off_cuts_and_branch",
            {"and": [{"device": "SM401"}, {"device": "M100"}]},
            "FALSE",
        ),
        (
            "always_off_removed_from_or",
            {"or": [{"device": "SM401"}, {"device": "M100"}]},
            "[M100]",
        ),
        (
            "not_always_on_is_false",
            {"not": {"device": "SM400"}},
            "FALSE",
        ),
        (
            "not_always_off_is_true",
            {"not": {"device": "SM401"}},
            "TRUE",
        ),
        (
            "xor",
            {"xor": [{"device": "M100"}, {"device": "M101"}]},
            "(([M100] AND [/M101]) OR ([/M100] AND [M101]))",
        ),
    ]
    for name, logic, expected in cases:
        actual = logic_text_for_generated(logic)
        if actual != expected:
            raise AssertionError(f"{name}: {actual} != {expected}")
    data, rowsize, _ = generate_rung(
        {"or": [{"device": "M100"}, {"and": [{"device": "M101"}, {"not": {"device": "M102"}}]}]},
        {"type": "coil", "device": "M200"},
    )
    row = LadderRow("test", 0, "", "", 0, rowsize, data, "", [], "exact")
    diagram = "\n".join(render_row_diagram(row, cell_width=10))
    for token in ["[M100]", "[M101]", "[/M102]", "(M200)"]:
        if token not in diagram:
            raise AssertionError(f"ladder diagram missing {token}:\n{diagram}")
    ctx = StBuildContext()
    st_text = logic_to_st(
        {
            "op": "and",
            "args": [
                {"op": "contact", "raw_device": "M100", "role": "a"},
                {"op": "contact", "raw_device": "M101", "role": "b"},
            ],
        },
        ctx,
    )
    if st_text != "(M100 AND (NOT M101))":
        raise AssertionError(f"unexpected ST conversion: {st_text}")
print("all ladder logic checks passed")


if __name__ == "__main__":
    main()
