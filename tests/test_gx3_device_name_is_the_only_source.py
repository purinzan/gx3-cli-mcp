from __future__ import annotations

"""No module may keep its own copy of which device types are hexadecimal.

Issue #16 -- X, Y, B and W reported in decimal -- was fixed by giving the repo
one device table in gx3_device_name. Eight modules never adopted it and kept
their own `{"X", "Y", "B", "W", "SB", "SW"}`, every one of them missing DX and
DY. So after that issue was closed, gx3_dm_probe still printed DX and DY in
decimal, and gx3_external_inputs still read `DX10` as decimal ten rather than
hexadecimal sixteen -- a different device, reported without complaint.

The set is not a judgement call any module gets to make: it is a fact about how
GX Works3 numbers devices, recorded in DEVICE_TYPE_BASE (SH-081224 22.1). This
test fails if a copy of it reappears, whether as a named constant or inline in
a radix expression.
"""

import ast
import pathlib

from gx3cli.gx3_device_name import HEX_DEVICE_TYPES

ROOT = pathlib.Path(__file__).resolve().parents[1]
CANON = ROOT / "gx3cli" / "gx3_device_name.py"


def _modules() -> list[pathlib.Path]:
    return [p for p in sorted((ROOT / "gx3cli").glob("*.py")) if p != CANON]


def test_no_module_defines_its_own_hex_device_set() -> None:
    # Only the name is checked, not the contents. A set of device types that
    # happens to sit inside the hex set is not necessarily a copy of it -- link
    # devices are hexadecimal too, and gx3_link_map is entitled to say which
    # types are link devices. What no module is entitled to do is decide, on
    # its own, which types are written in hex.
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if not isinstance(target, ast.Name) or "HEX" not in target.id.upper():
                    continue
                if isinstance(node.value, (ast.Set, ast.List, ast.Tuple, ast.Dict)):
                    offenders.append(f"{path.name}:{node.lineno} {target.id}")
    assert not offenders, (
        "these define their own hexadecimal device set; import it from "
        "gx3_device_name, or call device_radix()/format_device():\n  "
        + "\n  ".join(offenders)
    )


def test_no_module_hardcodes_a_device_radix() -> None:
    # Catches `16 if prefix in {...} else 10` even when the set is named.
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.IfExp):
                continue
            ends = {
                n.value
                for n in (node.body, node.orelse)
                if isinstance(n, ast.Constant) and isinstance(n.value, int)
            }
            if ends == {16, 10}:
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "device radix decided locally; call device_radix() from gx3_device_name:\n  "
        + "\n  ".join(offenders)
    )


def test_no_module_spells_a_device_by_concatenation() -> None:
    """f"{dev_type}{number}" is decimal for every device type, silently.

    It needs no hexadecimal set and no radix expression, so the two checks
    above walk straight past it. used-devices built its report's device column
    this way and put W132 in it as "W306" -- a name no other output uses, and
    one an engineer searching the project will not find.
    """
    offenders: list[str] = []
    for path in _modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.JoinedStr):
                continue
            parts = node.values
            if len(parts) != 2 or not all(isinstance(v, ast.FormattedValue) for v in parts):
                continue
            if any(value.format_spec is not None for value in parts):
                # f"{dev_type}{number:X}" states its radix, and the modules
                # that do it check device_radix() first. It is the bare
                # {number} -- decimal, unstated -- that this is looking for.
                continue
            names = []
            for value in parts:
                target = value.value
                if isinstance(target, ast.Attribute):
                    names.append(target.attr.lower())
                elif isinstance(target, ast.Name):
                    names.append(target.id.lower())
                else:
                    names.append("")
            if names[1].endswith("text"):
                # Already spelled somewhere else; this is re-joining it, not
                # deciding the radix.
                continue
            if "type" in names[0] and ("num" in names[1] or names[1] == "n"):
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, (
        "these spell a device name by concatenation, which is decimal for X, Y, "
        "B and W; call format_device() from gx3_device_name:\n  "
        + "\n  ".join(offenders)
    )


def test_the_canonical_set_still_covers_the_direct_access_types() -> None:
    # DX/DY are the direct-access spellings of X/Y and FX/FY are the function
    # devices; all four are hexadecimal, and all four were what the copies
    # missed.
    for dev_type in ("X", "Y", "DX", "DY", "FX", "FY"):
        assert dev_type in HEX_DEVICE_TYPES, dev_type


def main() -> int:
    # run_tests.py runs each file as a plain script, so the tests have to be
    # called from here. Collected rather than listed, so a test added later
    # cannot be silently left out.
    tests = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]
    for name, test in tests:
        test()
    print(f"{len(tests)} checks passed in {__file__.rsplit('/', 1)[-1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
