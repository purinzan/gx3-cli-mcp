from __future__ import annotations

"""A bundle meant for a public issue does not carry the customer's naming.

#93. The inventory listed every relative path under the project root and then
passed the JSON through the text redactor. That redactor knows addresses,
Japanese, upper-case project tokens and secrets already in the alias table --
and `CustomerAlpha/BatteryLine5/supplierModuleX.dat` is none of those.

The bundle is the thing the tool tells people to attach to a parser-gap issue,
so the names went out with it.

Checked the way the review asks: build a real bundle from a synthetic tree and
read the ZIP, entry names and every text payload, rather than checking the one
function that was changed.

What this does not claim: that no secret can be in a bundle. Sizes, suffixes
and nesting depth are still there, deliberately -- they are the diagnostic
content -- and a size can be identifying on its own. The claim is narrower:
path components are not passed through as text.
"""

import json
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from gx3cli.gx3_support_bundle import build_bundle, project_inventory
from test_gx3_shared_reach import coil, write_program


# Mixed-case ASCII, which is what the redactor does not recognise.
NAMES = ("CustomerAlpha", "BatteryLine5", "ProjectFalcon", "supplierModuleX")


def a_project(work: Path) -> Path:
    root = work / "CustomerAlpha"
    write_program(root, [("_guid/a", coil("a", 1, 100))])
    (root / "BatteryLine5").mkdir()
    (root / "BatteryLine5" / "supplierModuleX.dat").write_bytes(b"x")
    (root / "ProjectFalcon.w3pa").write_bytes(b"y")
    (root / "UnitConfig.dat").write_bytes(b"z")
    (root / "SourceInfo.CAB").write_bytes(b"cab")
    (root / "CPU.PRM").write_bytes(b"cpu")
    (root / "001_LDDB.db").write_bytes(b"db")
    return root


def bundle_text(work: Path, root: Path) -> tuple[list[str], str]:
    out = build_bundle(root, work / "bundle.zip")
    with zipfile.ZipFile(out) as archive:
        entries = archive.namelist()
        payload = "\n".join(
            archive.read(name).decode("utf-8", "replace") for name in entries
        )
    return entries, payload


def bundle_inventory(work: Path, root: Path) -> list[dict[str, object]]:
    out = build_bundle(root, work / "bundle.zip")
    with zipfile.ZipFile(out) as archive:
        return json.loads(archive.read("project_inventory_redacted.json").decode("utf-8"))


def test_no_project_name_reaches_the_bundle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        entries, payload = bundle_text(work, a_project(work))
        for name in NAMES:
            assert not any(name in entry for entry in entries), (name, entries)
            assert name not in payload, name


def test_a_suffix_is_not_a_licence_to_keep_the_name() -> None:
    # The first version of this fix matched `.*\\.w3pa` and kept
    # `ProjectFalcon.w3pa` whole, which put the project's name in the bundle
    # through the rule that was supposed to protect it.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        _, payload = bundle_text(work, a_project(work))
        assert ".w3pa" in payload, "the suffix is diagnostic and should survive"
        assert "ProjectFalcon" not in payload


def test_the_diagnostic_content_survives_the_built_archive() -> None:
    # The inventory is already structurally pseudonymized. Passing it through
    # the generic text redactor again used to rewrite CPU.PRM, 001_LDDB.db and
    # even DIR_0001/FILE_0001, contradicting the README and losing parser clues.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        rows = bundle_inventory(work, a_project(work))
        assert rows
        for row in rows:
            assert "suffix" in row and "size" in row and "depth" in row, row

        paths = {str(row["path"]) for row in rows}
        for fixed in ("UnitConfig.dat", "SourceInfo.CAB", "CPU.PRM", "001_LDDB.db"):
            assert fixed in paths, (fixed, sorted(paths))

        # The unknown nested path stays structurally anonymous, and its
        # stand-ins survive exactly as stand-ins rather than PROJECT aliases.
        assert any(
            path.startswith("DIR_") and "/FILE_" in path
            for path in paths
        ), sorted(paths)
        assert not any("PROJECT_" in path for path in paths), sorted(paths)


def test_two_files_in_one_folder_still_read_as_one_folder() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        root = work / "CustomerAlpha"
        write_program(root, [("_guid/a", coil("a", 1, 100))])
        (root / "BatteryLine5").mkdir()
        (root / "BatteryLine5" / "one.dat").write_bytes(b"1")
        (root / "BatteryLine5" / "two.dat").write_bytes(b"2")

        folders = {
            str(row["path"]).split("/")[0]
            for row in project_inventory(root)
            if "/" in str(row["path"])
        }
        assert len(folders) == 1, folders


def test_the_alias_table_is_not_in_the_bundle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        entries, payload = bundle_text(work, a_project(work))
        for entry in entries:
            assert "alias" not in entry.lower(), entries
        assert "real_to_alias" not in payload


def main() -> int:
    test_no_project_name_reaches_the_bundle()
    test_a_suffix_is_not_a_licence_to_keep_the_name()
    test_the_diagnostic_content_survives_the_built_archive()
    test_two_files_in_one_folder_still_read_as_one_folder()
    test_the_alias_table_is_not_in_the_bundle()
    print("bundle name checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
