from __future__ import annotations

"""A condition that expands without bound is stopped, and said to be stopped.

One rung in a real project expanded to 33,554,427 nodes -- 2^25, the shape of a
combinatorial expansion rather than of a condition anyone wrote -- and took 146
seconds. The other 6,000 rungs of that project took 23 seconds together, so
reading the whole project was one rung's arithmetic and nothing else. `metrics`
never finished.

The fix is not a faster expansion. Past twenty thousand terms the expression is
not something a person will read, so this stops building it and puts a marker
in its place. What matters is that the marker is not silence: a trace over such
a rung lists fewer conditions than the rung has, and without the marker it
would present them as the whole condition.

Also pinned here: the identity used to spot duplicate branches is built from
the children's identities. Serialising the whole subtree at every level made
that one rung write 185MB of JSON, and was 73% of the time to read a program.
"""

from gx3cli.gx3_ladder_logic import (
    MAX_LOGIC_NODES,
    and_logic,
    is_too_large,
    logic_id,
    logic_size,
    logic_stats,
    logic_to_text,
    or_logic,
)


def contact(name: str) -> dict:
    return {"op": "contact", "device": name, "role": "a"}


def a_wide_tree(width: int) -> dict:
    """An OR of ANDs, the shape that expands."""
    return or_logic([and_logic([contact(f"M{i}"), contact(f"M{i + 1}")]) for i in range(width)])


def test_a_condition_within_the_budget_is_built_as_before() -> None:
    node = a_wide_tree(50)
    assert not is_too_large(node), node
    assert logic_size(node) < MAX_LOGIC_NODES
    assert "M1" in logic_to_text(node)


def test_a_condition_past_the_budget_stops_and_says_so() -> None:
    node = a_wide_tree(MAX_LOGIC_NODES)
    assert is_too_large(node), logic_size(node)
    assert str(MAX_LOGIC_NODES) in node["reason"], node
    assert node["next_step"], node
    assert logic_to_text(node) == "[TOO LARGE]"


def test_the_marker_survives_being_combined_further() -> None:
    # Otherwise a rung that was cut deep inside would look whole from above.
    node = and_logic([contact("M1"), a_wide_tree(MAX_LOGIC_NODES)])
    assert is_too_large(node), node


def test_a_cut_condition_is_counted_where_a_caller_will_see_it() -> None:
    stats = logic_stats(and_logic([contact("M1"), a_wide_tree(MAX_LOGIC_NODES)]))
    assert stats["too_large"] == 1, stats


def test_two_equal_branches_have_the_same_identity_and_collapse() -> None:
    assert logic_id(contact("M1")) == logic_id(contact("M1"))
    assert logic_id(contact("M1")) != logic_id(contact("M2"))
    assert and_logic([contact("M1"), contact("M1")]) == contact("M1")


def test_identity_distinguishes_shape_not_only_leaves() -> None:
    left = and_logic([contact("M1"), or_logic([contact("M2"), contact("M3")])])
    right = or_logic([contact("M1"), and_logic([contact("M2"), contact("M3")])])
    assert logic_id(left) != logic_id(right)


def test_size_is_the_node_count() -> None:
    assert logic_size(contact("M1")) == 1
    assert logic_size(and_logic([contact("M1"), contact("M2")])) == 3


def main() -> int:
    test_a_condition_within_the_budget_is_built_as_before()
    test_a_condition_past_the_budget_stops_and_says_so()
    test_the_marker_survives_being_combined_further()
    test_a_cut_condition_is_counted_where_a_caller_will_see_it()
    test_two_equal_branches_have_the_same_identity_and_collapse()
    test_identity_distinguishes_shape_not_only_leaves()
    test_size_is_the_node_count()
    print("logic budget checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
