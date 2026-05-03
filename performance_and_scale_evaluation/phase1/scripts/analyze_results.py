#!/usr/bin/env python3
"""
Results analyzer for all Phase 1 evaluations.

Reads GuideLLM CSV outputs and plugin latency JSON to produce:
  - Baseline vs gateway overhead tables
  - Per-provider translation cost ranking
  - Scalability / concurrency throughput tables
  - Plugin chain latency breakdown
  - HPA scaling factor report
"""

import argparse
import csv
import json
from pathlib import Path
from utils import RESULTS_DIR


def load_csv_results(csv_path: Path) -> list[dict]:
    with open(csv_path) as f:
        return list(csv.DictReader(f))


def latest_run(eval_name: str) -> Path | None:
    eval_dir = RESULTS_DIR / eval_name
    if not eval_dir.exists():
        return None
    runs = sorted(eval_dir.iterdir(), reverse=True)
    return runs[0] if runs else None


def _safe_float(row: dict, key: str, default=0.0) -> float:
    try:
        return float(row.get(key, default))
    except (ValueError, TypeError):
        return default


# ---------------------------------------------------------------------------
# 01: Baseline
# ---------------------------------------------------------------------------
def analyze_01():
    run_dir = latest_run("01_baseline_direct")
    if not run_dir:
        print("  No results for 01_baseline_direct.")
        return

    print(f"\n{'='*70}")
    print("01 — Baseline: Direct to Provider (no gateway)")
    print(f"{'='*70}")
    print(f"{'Provider':<15} {'Model':<25} {'Payload':<10} {'p50 (ms)':<12} {'p99 (ms)':<12} {'RPS':<10}")
    print("-" * 84)

    for d in sorted(run_dir.iterdir()):
        if not d.is_dir() or d.name == "metadata.json":
            continue
        parts = d.name.replace("direct_", "").split("_")
        if len(parts) < 3:
            continue
        provider = parts[0]
        payload = parts[-1]
        model = "_".join(parts[1:-1])

        for csv_file in d.glob("*.csv"):
            rows = load_csv_results(csv_file)
            for row in rows:
                p50 = _safe_float(row, "request_latency_p50") * 1000
                p99 = _safe_float(row, "request_latency_p99") * 1000
                rps = _safe_float(row, "completed_request_rate")
                print(f"{provider:<15} {model:<25} {payload:<10} {p50:>8.1f}    {p99:>8.1f}    {rps:>7.1f}")


# ---------------------------------------------------------------------------
# 02: Single provider ramp
# ---------------------------------------------------------------------------
def analyze_02():
    run_dir = latest_run("02_single_provider")
    if not run_dir:
        print("  No results for 02_single_provider.")
        return

    print(f"\n{'='*70}")
    print("02 — Single Provider: Concurrency Ramp")
    print(f"{'='*70}")
    print(f"{'Model':<25} {'Concurrency':<15} {'RPS':<10} {'p50 (ms)':<12} {'p99 (ms)':<12} {'Errors':<8}")
    print("-" * 82)

    for d in sorted(run_dir.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        parts = name.rsplit("_c", 1)
        if len(parts) != 2:
            continue
        model, concurrency = parts

        for csv_file in d.glob("*.csv"):
            rows = load_csv_results(csv_file)
            for row in rows:
                p50 = _safe_float(row, "request_latency_p50") * 1000
                p99 = _safe_float(row, "request_latency_p99") * 1000
                rps = _safe_float(row, "completed_request_rate")
                errors = row.get("error_count", "0")
                print(f"{model:<25} {concurrency:<15} {rps:>7.1f}   {p50:>8.1f}    {p99:>8.1f}    {errors:>6}")


# ---------------------------------------------------------------------------
# 03: Multi-provider
# ---------------------------------------------------------------------------
def analyze_03():
    run_dir = latest_run("03_multi_provider")
    if not run_dir:
        print("  No results for 03_multi_provider.")
        return

    print(f"\n{'='*70}")
    print("03 — Multi-Provider Through Gateway")
    print(f"{'='*70}")
    print(f"{'Model':<30} {'RPS':<10} {'p50 (ms)':<12} {'p99 (ms)':<12}")
    print("-" * 64)

    for d in sorted(run_dir.iterdir()):
        if not d.is_dir():
            continue
        model = d.name.replace("gateway_", "")
        for csv_file in d.glob("*.csv"):
            rows = load_csv_results(csv_file)
            for row in rows:
                p50 = _safe_float(row, "request_latency_p50") * 1000
                p99 = _safe_float(row, "request_latency_p99") * 1000
                rps = _safe_float(row, "completed_request_rate")
                print(f"{model:<30} {rps:>7.1f}   {p50:>8.1f}    {p99:>8.1f}")


# ---------------------------------------------------------------------------
# 04: Payload size
# ---------------------------------------------------------------------------
def analyze_04():
    run_dir = latest_run("04_payload_size")
    if not run_dir:
        print("  No results for 04_payload_size.")
        return

    print(f"\n{'='*70}")
    print("04 — Payload Size Impact")
    print(f"{'='*70}")
    print(f"{'Model':<25} {'Payload':<12} {'RPS':<10} {'p50 (ms)':<12} {'p99 (ms)':<12}")
    print("-" * 71)

    for d in sorted(run_dir.iterdir()):
        if not d.is_dir():
            continue
        parts = d.name.rsplit("_", 1)
        if len(parts) != 2:
            continue
        model, payload = parts

        for csv_file in d.glob("*.csv"):
            rows = load_csv_results(csv_file)
            for row in rows:
                p50 = _safe_float(row, "request_latency_p50") * 1000
                p99 = _safe_float(row, "request_latency_p99") * 1000
                rps = _safe_float(row, "completed_request_rate")
                print(f"{model:<25} {payload:<12} {rps:>7.1f}   {p50:>8.1f}    {p99:>8.1f}")


# ---------------------------------------------------------------------------
# 05: Translation overhead
# ---------------------------------------------------------------------------
def analyze_05():
    run_dir = latest_run("05_translation_overhead")
    if not run_dir:
        print("  No results for 05_translation_overhead.")
        return

    print(f"\n{'='*70}")
    print("05 — Translation Overhead: Cross-Provider Comparison")
    print(f"{'='*70}")
    print(f"{'Group':<8} {'Model':<30} {'Payload':<10} {'p50 (ms)':<12} {'RPS':<10}")
    print("-" * 70)

    for d in sorted(run_dir.iterdir()):
        if not d.is_dir():
            continue
        name = d.name
        if not name.startswith("group"):
            continue
        group = name[5]
        rest = name[7:]
        parts = rest.rsplit("_", 1)
        if len(parts) != 2:
            continue
        model, payload = parts

        for csv_file in d.glob("*.csv"):
            rows = load_csv_results(csv_file)
            for row in rows:
                p50 = _safe_float(row, "request_latency_p50") * 1000
                rps = _safe_float(row, "completed_request_rate")
                print(f"{group:<8} {model:<30} {payload:<10} {p50:>8.1f}    {rps:>7.1f}")


# ---------------------------------------------------------------------------
# 06: Plugin chain latency
# ---------------------------------------------------------------------------
def analyze_06():
    run_dir = latest_run("06_plugin_chain_latency")
    if not run_dir:
        print("  No results for 06_plugin_chain_latency.")
        return

    breakdown_file = run_dir / "plugin_latency_breakdown.json"
    if not breakdown_file.exists():
        print("  No plugin_latency_breakdown.json found.")
        return

    print(f"\n{'='*70}")
    print("06 — Plugin Chain Latency Breakdown")
    print(f"{'='*70}")

    with open(breakdown_file) as f:
        data = json.load(f)

    print(f"{'Plugin':<30} {'p50 (ms)':<12} {'p95 (ms)':<12} {'p99 (ms)':<12}")
    print("-" * 66)
    total_p50 = 0
    for plugin, vals in data.items():
        if "error" in vals:
            print(f"{plugin:<30} ERROR: {vals['error']}")
            continue
        p50 = vals.get("p50_ms")
        p95 = vals.get("p95_ms")
        p99 = vals.get("p99_ms")
        if p50:
            total_p50 += p50
        fmt = lambda v: f"{v:.3f}" if v else "N/A"
        print(f"{plugin:<30} {fmt(p50):<12} {fmt(p95):<12} {fmt(p99):<12}")

    print("-" * 66)
    print(f"{'CUMULATIVE p50':<30} {total_p50:.3f}ms")


# ---------------------------------------------------------------------------
# 07: HPA scaling
# ---------------------------------------------------------------------------
def analyze_07():
    run_dir = latest_run("07_hpa_scaling")
    if not run_dir:
        print("  No results for 07_hpa_scaling.")
        return

    print(f"\n{'='*70}")
    print("07 — HPA Scaling Factor")
    print(f"{'='*70}")
    print(f"{'Replicas':<12} {'Concurrency':<15} {'RPS':<10} {'p50 (ms)':<12} {'p99 (ms)':<12} {'Factor':<8}")
    print("-" * 69)

    results = []
    for d in sorted(run_dir.iterdir()):
        if not d.is_dir():
            continue
        parts = d.name.split("_")
        if len(parts) < 2 or parts[0] != "replicas":
            continue
        replicas = int(parts[1])
        concurrency = parts[-1] if parts[-1].startswith("c") else ""

        for csv_file in d.glob("*.csv"):
            rows = load_csv_results(csv_file)
            for row in rows:
                rps = _safe_float(row, "completed_request_rate")
                p50 = _safe_float(row, "request_latency_p50") * 1000
                p99 = _safe_float(row, "request_latency_p99") * 1000
                results.append((replicas, concurrency, rps, p50, p99))

    if not results:
        print("  No HPA benchmark data found.")
        return

    base_rps = results[0][2] if results else 1
    for replicas, concurrency, rps, p50, p99 in results:
        factor = rps / base_rps if base_rps > 0 else 0
        print(f"{replicas:<12} {concurrency:<15} {rps:>7.1f}   {p50:>8.1f}    {p99:>8.1f}    {factor:>5.2f}x")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Analyze Phase 1 results")
    parser.add_argument("--eval", type=str, default="all",
                        help="Which evaluation to analyze (01-07 or all)")
    args = parser.parse_args()

    analyzers = {
        "01": analyze_01,
        "02": analyze_02,
        "03": analyze_03,
        "04": analyze_04,
        "05": analyze_05,
        "06": analyze_06,
        "07": analyze_07,
    }

    if args.eval == "all":
        for num, fn in analyzers.items():
            fn()
    elif args.eval in analyzers:
        analyzers[args.eval]()
    else:
        print(f"Unknown eval: {args.eval}. Choose 01-07 or all.")


if __name__ == "__main__":
    main()
