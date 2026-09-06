from __future__ import annotations

"""Doctor must rate an intentionally tangled handover fixture worse than its
organized field-output equivalent.

This is the acceptance fixture for issue #135.  It exercises the actual project
loader, index/xref builders and project-health orchestration rather than only
the score arithmetic.
"""

import tempfile
from pathlib import Path

from gx3cli.gx3_audit import collect_project_health
from gx3cli.gx3_maintainability_fixture import (
    create_bad_maintainability_project,
    create_good_maintainability_project,
)
from gx3cli.gx3_workspace import prepare


def _report(root: Path) -> dict[str, object]:
    workspace = prepare(root, rebuild=True)
    return collect_project_health(
        root,
        index_dir=workspace.directory,
        link_db=workspace.directory / "missing_link_map.sqlite",
        top=20,
    )


def test_bad_profile_is_worse_than_good_profile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        good_root = create_good_maintainability_project(base / "good-maintainability")
        bad_root = create_bad_maintainability_project(base / "bad-maintainability")

        good = _report(good_root)
        bad = _report(bad_root)

        # The optional cross-project link map may be absent, but every core
        # Doctor check must have been evaluated for this comparison to mean
        # anything.
        assert not good["analysis"]["core_inconclusive"], good["analysis"]
        assert not bad["analysis"]["core_inconclusive"], bad["analysis"]

        good_scores = good["scores"]
        bad_scores = bad["scores"]
        assert bad_scores["Maintainability"] < good_scores["Maintainability"], (good_scores, bad_scores)
        assert bad_scores["Traceability"] < good_scores["Traceability"], (good_scores, bad_scores)
        assert bad_scores["Change safety"] < good_scores["Change safety"], (good_scores, bad_scores)
        assert bad_scores["Documentation"] < good_scores["Documentation"], (good_scores, bad_scores)

        checks = bad["checks"]
        assert checks["duplicate-coil"]["count"] > 0, checks["duplicate-coil"]
        assert checks["multi-writer"]["count"] > 0, checks["multi-writer"]
        assert checks["io-comment-gap"]["count"] > 0, checks["io-comment-gap"]
        assert checks["comment-conflict"]["count"] > 0, checks["comment-conflict"]
        assert checks["unused-device"]["count"] > 0, checks["unused-device"]

        good_checks = good["checks"]
        assert good_checks["duplicate-coil"]["count"] == 0, good_checks["duplicate-coil"]
        assert good_checks["multi-writer"]["count"] == 0, good_checks["multi-writer"]
        assert good_checks["io-comment-gap"]["count"] == 0, good_checks["io-comment-gap"]
        assert good_checks["comment-conflict"]["count"] == 0, good_checks["comment-conflict"]


def main() -> int:
    test_bad_profile_is_worse_than_good_profile()
    print("doctor maintainability profile checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
