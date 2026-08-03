from __future__ import annotations

"""Run every test under tests/ with the product modules on the path.

Product modules live at the repo root; tests live in tests/. Each test is a
plain script (``python test_x.py``), so run it as a subprocess with the repo
root on PYTHONPATH. Data-dependent integration tests skip themselves when no
extracted project is present, so this passes on a clean checkout.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TESTS = sorted((ROOT / "tests").glob("test_*.py"))


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    failures: list[str] = []
    for test in TESTS:
        print(f"\n=== {test.name} ===")
        result = subprocess.run([sys.executable, str(test)], cwd=ROOT, env=env)
        if result.returncode != 0:
            failures.append(test.name)
    print("\n" + ("FAILED: " + ", ".join(failures) if failures else f"all {len(TESTS)} test files passed"))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
