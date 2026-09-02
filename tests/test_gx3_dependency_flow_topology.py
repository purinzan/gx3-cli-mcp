from __future__ import annotations

from gx3cli.gx3_dependency_flow import dependency_refs_for_output, output_elements_for
from gx3cli.review_gx3_project import LadderRow


def manual_row(
    elements: str,
    dim: str = "3x1",
    header: str = "V1:4:1:1:1:1:a:M:c:M",
    verticals: str = "",
) -> LadderRow:
    vs = f":vs=[{verticals}]" if verticals else ""
    data = f"{header}:cb{{fg=fg{{dim={dim}:es=[{elements}]{vs}}}}}"
    return LadderRow("test", 0, "", dim, 0, 1, data, "", [], "exact")


def dependency_devices(row: LadderRow, device: str) -> list[str]:
    output = output_elements_for(row, device)[0]
    return [ref.device for _element, ref in dependency_refs_for_output(row, output)]


def test_dependency_flow_does_not_infer_blank_horizontal_wire() -> None:
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}"
    coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=2,0}"
    row = manual_row(f"{contact}:{coil}")
    assert dependency_devices(row, "M200") == []


def test_dependency_flow_uses_explicit_horizontal_wire() -> None:
    contact = "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=100:vt=nn}]}:pos=0,0}"
    wire = "e{s=wire:pos=1,0}"
    coil = "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=200:vt=nn}]}:pos=2,0}"
    row = manual_row(f"{contact}:{wire}:{coil}")
    assert dependency_devices(row, "M200") == ["M100"]


def test_dependency_flow_driver_sink_does_not_feed_vertical_branch() -> None:
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
    assert dependency_devices(row, "M200") == ["M100"]
    assert dependency_devices(row, "M201") == []


def main() -> int:
    test_dependency_flow_does_not_infer_blank_horizontal_wire()
    test_dependency_flow_uses_explicit_horizontal_wire()
    test_dependency_flow_driver_sink_does_not_feed_vertical_branch()
    print("dependency-flow topology checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
