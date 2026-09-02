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
    # A file with no __main__ block runs as a script, does nothing and exits 0,
    # so it passes without executing an assertion. Five files were in that
    # state, one of them for as long as it had existed. Say so instead.
    silent = [t.name for t in TESTS if "__main__" not in t.read_text(encoding="utf-8")]
    if silent:
        print("these test files have no runner, so nothing in them would run:")
        for name in silent:
            print(f"  {name}")
        return 1

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
