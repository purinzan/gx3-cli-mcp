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
        # Two builders now check input before/after population, in addition
        # to their saved identity and workspace validation. No source reload.
        expected_hash_reads = 32 if sample["case"] == "multi-pou" else (16 if sample["case"] == "ld-st" else 8)
        assert sample["cold_build"]["fingerprint_file_reads"] == expected_hash_reads, sample
        assert sample["dependency_flow_truncated"] is False, sample
        for phase in ("cold_build", "warm_query", "trace", "dependency_flow"):
            values = sample[phase]
            assert values["sql_statements"] > 0 and values["sqlite_opens"] > 0, values
            assert values["wall_seconds"] > 0 and values["python_peak_bytes"] > 0, values
            rss = values["process_peak_rss_bytes_after_phase"]
            assert rss is None or rss > 0, values
        # Two queries: 9 existing statements + one schema query and four
        # SQLite-internal table_info statements and one build-contract read
        # each. No project data scan added.
        assert sample["warm_query"]["sql_statements"] == 30, sample
        # Trace pins the validation snapshot (BEGIN) and checks stored ST gaps
        # before constant proofs. Both artifact opens verify the build contract.
        assert sample["trace"]["sql_statements"] == (27 if sample["case"] == "multi-pou" else 24), sample
        for loader in ("load_rows", "load_comments", "load_labels"):
            assert sample["warm_query"][loader] == 0, sample
            assert sample["trace"][loader] == 1, sample
        assert sample["dependency_flow"]["load_rows"] == 1, sample
        assert sample["dependency_flow"]["load_comments"] == 1, sample
        # One checked connection replaces probe + reopen. BEGIN and ST scope
        # query add two SQL statements, plus one build-contract read;
        # multi-pou reads three extra source DBs.
        multi = sample["case"] == "multi-pou"
        assert sample["dependency_flow"]["sqlite_opens"] == (5 if multi else 2), sample
        assert sample["dependency_flow"]["sql_statements"] == (15 if multi else 12), sample
    print("synthetic benchmark determinism and instrumentation checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
