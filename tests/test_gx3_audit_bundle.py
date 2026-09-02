from __future__ import annotations

"""audit is the one command that runs the others, so it is also the one that
can break on how their paths are resolved rather than on any project data."""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from gx3cli.gx3_synthetic_project import create_demo_line_project


ROOT = Path(__file__).resolve().parents[1]


def test_audit_writes_every_step_from_an_unrelated_cwd() -> None:
    # The bundle directory is created relative to the caller's cwd, but the
    # steps run with cwd=BASE_DIR. While the two happened to be the same the
    # bug was invisible; from anywhere else lint failed writing its CSVs, and
    # reported it as an unsupported GX Works3 format.
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        project = create_demo_line_project(work / "line", overwrite=True)
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        completed = subprocess.run(
            [sys.executable, "-m", "gx3cli.gx3_cli", "audit", "--root", str(project)],
            cwd=work,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout

        bundles = sorted((work / "outputs").glob("*_audit_*"))
        assert len(bundles) == 1, f"expected one audit bundle, got {bundles}"
        summary = json.loads((bundles[0] / "audit_summary.json").read_text(encoding="utf-8"))
        failed = [step["name"] for step in summary["steps"] if step["returncode"] != 0]
        assert not failed, f"audit steps failed: {failed}\n{completed.stdout}"

        # lint's CSVs are what went missing, so check they landed in the bundle.
        assert sorted(bundles[0].glob("lint_*.csv")), "lint wrote no findings CSV into the bundle"

        # The steps run with cwd=BASE_DIR, so a default output path resolves
        # inside the installed package: an audit used to leave one project's
        # dead-logic and network-map CSVs in gx3cli/outputs, where the release
        # gate then found them.
        for name in ("outputs", ".gx3_index"):
            written_into_package = ROOT / "gx3cli" / name
            strays = (
                sorted(p.name for p in written_into_package.glob("*"))
                if written_into_package.exists()
                else []
            )
            assert not strays, f"audit wrote into gx3cli/{name}: {strays}"

        # Everything a step produces belongs in the bundle.
        assert sorted(bundles[0].glob("*dead_logic*")), "dead-logic wrote nothing into the bundle"
        assert sorted(bundles[0].glob("*network_map*")), "network-map wrote nothing into the bundle"


def main() -> int:
    test_audit_writes_every_step_from_an_unrelated_cwd()
    print("audit bundle checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
