"""Regression tests for ladder-print section/pos/device filtering.

These test the pure filter functions on synthetic entries, so they need no
extracted project. Key regression: an empty-title statement row (invisible in
the printed output) must NOT end a --section selection early.
"""

from gx3cli.gx3_ladder_print import scan_sections, select_entries


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

    print("all ladder-print filter checks passed")


if __name__ == "__main__":
    main()
