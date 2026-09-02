from __future__ import annotations

"""used-devices reports which devices a project uses without a comment, so a
device it names has to be one the ladder actually contains, spelled the way
every other output spells it.

It used to pair the header's type tokens with the d{} numbers by position. A
modifier spends a token without spending a number, so the pairing shifted, and
on one real project the report named 127 devices that are not in the ladder --
including a whole run of ZR addresses reported as D. It also spelled the names
itself, which is decimal, so W132 appeared as "W306".
"""

from gx3cli.extract_used_devices_without_comments import Usage, row_devices
from gx3cli.gx3_arg_decode import parse_row_occurrences


# One rung: SM400 contact, FROM into a digit-designated K4M49000. The digit
# specification is the modifier that shifted the old positional pairing.
ROW = (
    "V1:8:1:2:4:1:3:1:2:3:a:SM:FROM:U:K_1:M:Ks:K_1:cb{fg=fg{dim=6x1:es=["
    "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=400:vt=nn}]}:pos=0,0}:"
    "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
    "d{s=#:a=1:vt=nn}:c{s=#:v=0}:M{b=d{s=#:a=49000:vt=nn}:m=c{s=#:v=4}}:c{s=#:v=1}]}:pos=1,0}]}}"
)


def test_devices_come_from_the_shared_decoder() -> None:
    operations, status = parse_row_occurrences(ROW)
    assert status == "exact", status
    devices = row_devices(operations)

    assert ("M", 49000) in devices, devices
    # The digit count K4 is a constant, not device M4, and the rung has no M80.
    assert not any(dev_type == "M" and number in (4, 80) for dev_type, number in devices), devices


def test_hexadecimal_types_are_spelled_the_way_gx_works3_spells_them() -> None:
    # W is one of the hexadecimal types, so W306 would be this report's own
    # spelling of a device the rest of the toolchain calls W132.
    assert Usage("W", 0x132).device == "W132"
    assert Usage("X", 0x1A).device == "X1A"
    assert Usage("D", 306).device == "D306"


def main() -> int:
    test_devices_come_from_the_shared_decoder()
    test_hexadecimal_types_are_spelled_the_way_gx_works3_spells_them()
    print("used-devices source checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
