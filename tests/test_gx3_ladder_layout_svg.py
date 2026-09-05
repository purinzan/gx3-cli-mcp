from __future__ import annotations

"""The picture has to say what the ladder says, at one width.

Two things were wrong with it.

A rung was laid out to its own contents, so a page of rungs was a ragged stack
of different widths with the right rail moving from one to the next. It now
lays out to the printed twelve-cell grid, and a rung wider than that folds onto
continuation rows -- on the same rule the printed rung folds by, so the two
break in the same place.

And a contact carried a mark only when it was normally closed. A rising-edge
contact was drawn exactly like a level one, so the picture said "while this is
on" where the ladder says "when it turns on". The mark now comes from
contact_mark(), shared with the printed rung, and INV, ME and MEF are drawn on
the wire as symbols rather than as boxes with letters in them.
"""

from gx3cli.gx3_ladder_layout import GRID_CELLS, _svg_rung, rung_layout
from gx3cli.gx3_ladder_print import contact_mark
from gx3cli.review_gx3_project import LadderRow


def rung(data: str) -> LadderRow:
    return LadderRow(
        lddb="test_LDDB.db", pos=1, block_id="1", title="", blocktype=0,
        rowsize=0, data=data, dim="", operations=[], parse_status="",
    )


def element(pos_x: int, kind: str, ct: str, number: int) -> str:
    op = "ct" if kind in ("a", "b") else "cl"
    return (
        f"e{{s=ce{{op={op}{{op=#:ct={ct}:as=[as{{vt=Abl}}]}}:"
        f"args=[d{{s=#:a={number}:vt=nn}}]}}:pos={pos_x},0}}"
    )


# One rising-edge contact, one normally closed contact, one coil.
PULSE_ROW = (
    "V1:7:1:1:1:1:a:M:b:M:c:M:cb{fg=fg{dim=3x1:es=["
    + element(0, "a", "p", 100)
    + ":"
    + element(1, "b", "a", 200)
    + ":"
    + element(2, "c", "a", 300)
    + "]}}"
)

# A short rung: three cells of content, nowhere near the grid width.
SHORT_ROW = (
    "V1:5:1:1:1:a:M:c:M:cb{fg=fg{dim=2x1:es=["
    + element(0, "a", "a", 100)
    + ":"
    + element(1, "c", "a", 200)
    + "]}}"
)


def test_a_short_rung_still_lays_out_to_the_grid_width() -> None:
    layout = rung_layout(rung(SHORT_ROW))
    assert layout["dim"]["width"] == GRID_CELLS, layout["dim"]
    svg = "\n".join(_svg_rung(layout, 0))
    # Both rails are drawn, and the right one sits at the grid edge.
    assert svg.count('class="rail"') == 2, svg


def test_two_rungs_of_different_content_are_the_same_width() -> None:
    short = rung_layout(rung(SHORT_ROW))["dim"]["width"]
    longer = rung_layout(rung(PULSE_ROW))["dim"]["width"]
    assert short == longer == GRID_CELLS, (short, longer)


def test_a_rising_edge_contact_is_not_drawn_as_a_level_one() -> None:
    layout = rung_layout(rung(PULSE_ROW))
    kinds = [(e["kind"], e["role"], e["ct_code"]) for e in layout["elements"]]
    assert ("contact", "a", "p") in kinds, kinds

    svg = "\n".join(_svg_rung(layout, 0))
    # The edge arrow: a shaft and a head. Without it this rung drew nothing to
    # tell the pulse contact from the plain one beside it.
    assert 'class="mark-head"' in svg, svg
    # And the normally closed contact keeps its slash.
    assert svg.count('class="mark"') >= 2, svg


def test_the_mark_is_the_one_the_printed_rung_uses() -> None:
    # The decision is shared, so the two cannot drift apart.
    assert contact_mark("a", "p") == "rising"
    assert contact_mark("a", "f") == "falling"
    assert contact_mark("b", "a") == "closed"
    assert contact_mark("a", "a") == ""


def test_nothing_in_the_layout_is_left_out_of_the_picture() -> None:
    layout = rung_layout(rung(PULSE_ROW))
    svg = "\n".join(_svg_rung(layout, 0))
    for item in layout["elements"]:
        label = str(item["label"])
        assert label in svg, (label, svg)


def main() -> int:
    test_a_short_rung_still_lays_out_to_the_grid_width()
    test_two_rungs_of_different_content_are_the_same_width()
    test_a_rising_edge_contact_is_not_drawn_as_a_level_one()
    test_the_mark_is_the_one_the_printed_rung_uses()
    test_nothing_in_the_layout_is_left_out_of_the_picture()
    print("ladder layout svg checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
