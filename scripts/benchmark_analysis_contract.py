"""Reproducible synthetic #153 measurements, not a GX Works3 correctness oracle.

Each sample runs in a fresh process with fresh indexes. OS filesystem caches are
not flushed. Tracemalloc measures Python allocations. Where available, process
peak RSS is cumulative since worker startup, not an isolated phase allocation.
Instrumentation overhead is included and identical for compared checkouts.
"""
from __future__ import annotations

import argparse
from contextlib import closing, redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time
import tracemalloc

try:
    import resource
except ImportError:  # Windows: do not report missing process memory as zero.
    resource = None


CASES = ("small", "wide", "deep", "large-span", "multi-pou", "ld-st")
WIDTH = 24
SPAN = 4096


def process_peak_rss_bytes() -> int | None:
    if resource is None or sys.platform not in {"darwin", "linux"}:
        return None
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak) if sys.platform == "darwin" else int(peak * 1024)


def coil(source: str, number: int, destination: str, target: int) -> str:
    # Frozen stored syntax, independent of the checkout being measured.
    return (
        f"V1:4:1:1:1:1:a:{source}:c:{destination}:cb{{fg=fg{{dim=2x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=["
        f"d{{s=#:a={number}:vt=nn}}]}}:pos=0,0}}:"
        "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=Abl}]}:args=["
        f"d{{s=#:a={target}:vt=nn}}]}}:pos=1,0}}]}}}}"
    )


def operation(header: str, operands: str) -> str:
    return (
        f"V1:9:1:1:1:1:1:1:a:M:{header}:cb{{fg=fg{{dim=4x1:es=["
        "e{s=ce{op=ct{op=#:ct=a:as=[as{vt=Abl}]}:args=[d{s=#:a=1:vt=nn}]}:pos=0,0}:"
        "e{s=ce{op=cl{op=#:ct=a:as=[as{vt=A16}:as{vt=A16}:as{vt=A16}]}:args=["
        f"{operands}]}}:pos=1,0}}]}}}}"
    )


def fixture(root: Path, case: str) -> tuple[str, str, str]:
    root.mkdir()
    programs: dict[int, list[str]] = {1: []}
    query, target = "M0", "Y0"
    if case == "small":
        programs[1] = [coil("SM", 401, "M", 0), coil("M", 0, "Y", 0)]
    elif case == "wide":
        programs[1] = [coil("X", n, "Y", 0) for n in range(WIDTH)]
        query = "Y0"
    elif case == "large-span":
        programs[1] = [
            operation("BMOV:D:D:K_1", f"d{{s=#:a=100:vt=nn}}:d{{s=#:a=10000:vt=nn}}:c{{s=#:v={SPAN}}}"),
            operation("MOV:D:D", "d{s=#:a=10001:vt=nn}:d{s=#:a=20000:vt=nn}"),
        ]
        query, target = "D10001", "D20000"
    else:
        chain = [coil("X", 0, "M", 0)]
        chain.extend(coil("M", n - 1, "M", n) for n in range(1, WIDTH))
        chain.append(coil("M", WIDTH - 1, "Y", 0))
        if case == "multi-pou":
            programs = {}
            for n, data in enumerate(chain):
                programs.setdefault(1 + n // 7, []).append(data)
        else:
            programs[1] = chain
    for program, rungs in programs.items():
        with closing(sqlite3.connect(root / f"{program:03d}_LDDB.db")) as con, con:
            con.execute("create table LadderBlocks(id text, pos real, blocktype integer, data text, rowsize integer, translated integer, ConvTarget integer)")
            con.executemany("insert into LadderBlocks values (?, ?, 0, ?, 1, 0, 0)",
                            [(f"_guid/synthetic-{n}", n * 16, data) for n, data in enumerate(rungs)])
    if case == "ld-st":
        with closing(sqlite3.connect(root / "100_STDB.db")) as con, con:
            con.execute("create table Source(Pou text, Code text)")
            con.execute("insert into Source values ('MainST', 'D900 := D901; local := X0;')")
    digest = hashlib.sha256()
    for path in sorted(root.iterdir()):
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return query, target, digest.hexdigest()


def measure(action, loader_codes: dict) -> tuple[object, dict]:
    stats = {"sql_statements": 0, "sqlite_opens": 0,
             "load_rows": 0, "load_comments": 0, "load_labels": 0, "fingerprint_file_reads": 0}
    original_connect, previous_profile = sqlite3.connect, sys.getprofile()

    def sql(_statement):
        stats["sql_statements"] += 1

    def connect(*args, **kwargs):
        stats["sqlite_opens"] += 1
        con = original_connect(*args, **kwargs)
        con.set_trace_callback(sql)
        return con

    def profile(frame, event, _arg):
        if event == "call" and frame.f_code in loader_codes:
            stats[loader_codes[frame.f_code]] += 1

    sqlite3.connect = connect
    tracemalloc.start()
    sys.setprofile(profile)
    started = time.perf_counter()
    try:
        with redirect_stdout(io.StringIO()):
            result = action()
        stats["wall_seconds"] = time.perf_counter() - started
        stats["python_peak_bytes"] = tracemalloc.get_traced_memory()[1]
        stats["process_peak_rss_bytes_after_phase"] = process_peak_rss_bytes()
    finally:
        sys.setprofile(previous_profile)
        tracemalloc.stop()
        sqlite3.connect = original_connect
    return result, stats


def sample(checkout: Path, case: str, warm_queries: int) -> dict:
    sys.path.insert(0, str(checkout))
    from gx3cli import gx3_input_identity, gx3_trace_state, gx3_xref
    from gx3cli.gx3_workspace import prepare
    from gx3cli.trace_gx3_device_dependencies import build_trace

    codes = {
        gx3_trace_state.load_rows.__code__: "load_rows",
        gx3_trace_state.load_comments_for_root.__code__: "load_comments",
        gx3_trace_state.load_label_resolver.__code__: "load_labels",
        gx3_input_identity.file_digest.__code__: "fingerprint_file_reads",
    }
    with tempfile.TemporaryDirectory(prefix="gx3_bench_") as tmp:
        previous = Path.cwd()
        try:
            os.chdir(tmp)
            root = Path(tmp) / "project"
            query, target, fixture_sha = fixture(root, case)
            workspace, cold = measure(lambda: prepare(root), codes)

            def warm():
                for _ in range(warm_queries):
                    captured = io.StringIO()
                    with redirect_stdout(captured):
                        code = gx3_xref.main(["--root", str(root), "--db", str(workspace.xref.path),
                                              "where-used", query, "--json", "--limit", "-1"])
                    if code != 0:
                        raise RuntimeError("benchmark query unexpectedly failed")
                    report = json.loads(captured.getvalue())["results"][0]
                    expected = WIDTH if case == "wide" else 1
                    if report["total_counts"]["writers"] != expected or report["truncated"]:
                        raise RuntimeError("benchmark fixture writer count/coverage changed")

            _, query_stats = measure(warm, codes)
            trace, trace_stats = measure(lambda: build_trace(
                root, target, max_depth=WIDTH + 4, max_devices=WIDTH + 8,
                include_reset=True, strict_logic=True), codes)
            if trace.get("target", {}).get("device") != target:
                raise RuntimeError("benchmark trace returned a different target")
            return {"case": case, "fixture_sha256": fixture_sha, "warm_queries": warm_queries,
                    "trace_truncated": trace.get("truncated"),
                    "cold_build": cold, "warm_query": query_stats, "trace": trace_stats}
        finally:
            os.chdir(previous)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--case", choices=("all", *CASES), default="all")
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--warm-queries", type=int, default=10)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.repeat < 1 or args.warm_queries < 1:
        parser.error("repeat and warm-queries must be positive")
    checkout = args.checkout.resolve()
    if args.worker:
        if args.case == "all":
            parser.error("worker needs one case")
        print(json.dumps(sample(checkout, args.case, args.warm_queries)))
        return 0
    samples = []
    for case in CASES if args.case == "all" else (args.case,):
        for _ in range(args.repeat):
            process = subprocess.run([
                sys.executable, str(Path(__file__).resolve()), "--checkout", str(checkout),
                "--case", case, "--warm-queries", str(args.warm_queries), "--worker",
            ], text=True, encoding="utf-8", capture_output=True, check=True)
            samples.append(json.loads(process.stdout))
    revision = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    print(json.dumps({"schema": "gx3-analysis-benchmark-v1", "revision": revision,
                      "python": platform.python_version(), "platform": platform.platform(),
                      "fixture_version": 1, "width": WIDTH, "span": SPAN,
                      "memory_scope": "Python peak per phase; process peak RSS cumulative since worker start on macOS/Linux, null elsewhere",
                      "cache_scope": "fresh process and indexes per sample; OS page cache not flushed",
                      "samples": samples}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
