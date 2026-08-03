from gx3cli.gx3_intermediate_tool import generate_rung, operation_model


def assert_contains(text, parts):
    missing = [part for part in parts if part not in text]
    if missing:
        raise AssertionError(f"missing fragments: {missing}\n{text}")


def check_case(name, logic, output, expected_rowsize, expected_ops, fragments):
    data, rowsize, op_count = generate_rung(logic, output)
    if rowsize != expected_rowsize:
        raise AssertionError(f"{name}: rowsize {rowsize} != {expected_rowsize}")
    if op_count != expected_ops:
        raise AssertionError(f"{name}: op_count {op_count} != {expected_ops}")
    ops = operation_model(data)
    if len(ops) != expected_ops:
        raise AssertionError(f"{name}: parsed ops {len(ops)} != {expected_ops}")
    assert_contains(data, fragments)
    return data


def main():
    check_case(
        "single_contact",
        {"device": "M100"},
        {"type": "coil", "device": "M200"},
        1,
        2,
        ["dim=2x1", "a:M:c:M", "a=100", "a=200"],
    )
    check_case(
        "and_series",
        {"and": [{"device": "M100"}, {"device": "M101"}]},
        {"type": "coil", "device": "M200"},
        1,
        3,
        ["dim=3x1", "a:M:a:M:c:M", "a=100", "a=101", "a=200"],
    )
    check_case(
        "or_parallel",
        {"or": [{"device": "M100"}, {"device": "M101"}]},
        {"type": "coil", "device": "M200"},
        2,
        3,
        ["dim=2x2", "vs=[v{pos=1,1}]", "a=100", "a=101", "a=200"],
    )
    check_case(
        "nested_and_or",
        {"and": [{"device": "M100"}, {"or": [{"device": "M101"}, {"device": "M102"}]}]},
        {"type": "coil", "device": "M200"},
        2,
        5,
        ["dim=3x2", "vs=[v{pos=2,1}]", "a=100", "a=101", "a=102"],
    )
    check_case(
        "cartesian_or_and",
        {"and": [{"or": [{"device": "M100"}, {"device": "M101"}]}, {"or": [{"device": "M102"}, {"device": "M103"}]}]},
        {"type": "coil", "device": "M200"},
        4,
        9,
        ["dim=3x4", "v{pos=2,1}", "v{pos=2,2}", "v{pos=2,3}"],
    )
    check_case(
        "not_contact",
        {"not": {"device": "M100"}},
        {"type": "coil", "device": "M200"},
        1,
        2,
        ["dim=2x1", "b:M:c:M", "a=100", "a=200"],
    )
    check_case(
        "xor",
        {"xor": [{"device": "M100"}, {"device": "M101"}]},
        {"type": "coil", "device": "M200"},
        2,
        5,
        ["dim=3x2", "a:M:b:M:b:M:a:M:c:M", "a=100", "a=101", "a=200"],
    )
    check_case(
        "set_output",
        {"device": "M100"},
        {"type": "set", "device": "L2612"},
        1,
        2,
        ["dim=2x1", "a:M:SET:L", "op=cl{op=#:ct=a", "a=2612"],
    )
    check_case(
        "rst_output",
        {"device": "M100"},
        {"type": "rst", "device": "L2613"},
        1,
        2,
        ["dim=2x1", "a:M:RST:L", "op=cl{op=#:ct=a", "a=2613"],
    )
    check_case(
        "pls_output",
        {"device": "M100"},
        {"type": "pls", "device": "M28"},
        1,
        2,
        ["dim=2x1", "a:M:PLS:M", "op=cl{op=#:ct=p", "a=28"],
    )
    danger04 = check_case(
        "known_and_or_display_pattern",
        {
            "or": [
                {"and": [{"device": "SM400"}, {"device": "M28"}]},
                {"and": [{"device": "SM401"}, {"device": "M29"}]},
            ]
        },
        {"type": "coil", "device": "M3"},
        2,
        5,
        ["V1:10:1:2:1:1:1:2:1:1:1:1:a:SM:a:M:a:SM:a:M:c:M", "dim=3x2", "v{pos=2,1}", "a=3"],
    )
    expected = (
        "V1:10:1:2:1:1:1:2:1:1:1:1:a:SM:a:M:a:SM:a:M:c:M:"
        "cb{fg=fg{dim=3x2:es=[e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=400:vt=nn}]}:pos=0,0}:"
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=28:vt=nn}]}:pos=1,0}:"
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=401:vt=nn}]}:pos=0,1}:"
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=29:vt=nn}]}:pos=1,1}:"
        "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=3:vt=nn}]}:pos=2,0}]:"
        "vs=[v{pos=2,1}]}}"
    )
    if danger04 != expected:
        raise AssertionError("known_and_or_display_pattern changed")
print("all intermediate regression checks passed")


if __name__ == "__main__":
    main()
