from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

from scripts.benchmark_analysis_contract import CASES, fixture


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        for case in CASES:
            first = fixture(Path(tmp) / f"{case}-one", case)
            second = fixture(Path(tmp) / f"{case}-two", case)
            assert first == second, (case, first, second)
    root = Path(__file__).resolve().parents[1]
    completed = subprocess.run([
        sys.executable, str(root / "scripts" / "benchmark_analysis_contract.py"),
        "--case", "all", "--repeat", "1", "--warm-queries", "2",
    ], cwd=root, text=True, encoding="utf-8", capture_output=True, check=True)
    report = json.loads(completed.stdout)
    assert len(report["samples"]) == len(CASES), report
    for sample in report["samples"]:
        assert sample["trace_truncated"] is False, sample
        for phase in ("cold_build", "warm_query", "trace"):
            values = sample[phase]
            assert values["sql_statements"] > 0 and values["sqlite_opens"] > 0, values
            assert values["wall_seconds"] > 0 and values["python_peak_bytes"] > 0, values
            rss = values["process_peak_rss_bytes_after_phase"]
            assert rss is None or rss > 0, values
        # Two queries: 9 existing statements + one schema query and four
        # SQLite-internal table_info statements each. No data scan added.
        assert sample["warm_query"]["sql_statements"] == 28, sample
        for loader in ("load_rows", "load_comments", "load_labels"):
            assert sample["warm_query"][loader] == 0, sample
            assert sample["trace"][loader] == 1, sample
    print("synthetic benchmark determinism and instrumentation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
