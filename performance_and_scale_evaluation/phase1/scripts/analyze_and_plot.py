#!/usr/bin/env python3
"""
Phase 1 Results Analyzer & Plot Generator

Reads GuideLLM benchmarks.json files from the results directory tree,
extracts key metrics, generates plots, and writes a CSV summary.

Usage:
    python analyze_and_plot.py <results_dir>
    python analyze_and_plot.py results/2026-04-28_15-30-00

Expects directory structure:
    <results_dir>/
        stage1-baseline/
            payload-small-c2/benchmarks.json
            payload-small-c4/benchmarks.json
            ...
        stage2-gateway-overhead/
            ...
        stage3-hpa-scaling/
            replicas-1/
            replicas-2/
            ...
        stage4-plugin-latency/
            plugin_latency_breakdown.json

Output:
    <results_dir>/report/
        summary.csv
        *.png plots
"""

import argparse
import csv
import json
import re
import sys
from pathlib import Path

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as ticker
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("WARNING: matplotlib not installed. Plots will be skipped.", file=sys.stderr)
    print("  Install: pip install matplotlib", file=sys.stderr)


CONCURRENCY_LEVELS = [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]

PAYLOAD_COLORS = {
    "small": "#2196F3",
    "medium": "#4CAF50",
    "large": "#FF9800",
    "very-large": "#F44336",
}

DEPTH_COLORS = {
    "1": "#2196F3",
    "10": "#4CAF50",
    "50": "#FF9800",
    "100": "#F44336",
}

REPLICA_COLORS = {
    1: "#2196F3",
    2: "#4CAF50",
    4: "#FF9800",
    8: "#F44336",
}


# ───────────────────────────────────────────────────────────────────
# Parsing
# ───────────────────────────────────────────────────────────────────

def parse_benchmark_dir(bench_dir: Path) -> dict | None:
    """Parse a single benchmark directory's benchmarks.json."""
    json_files = list(bench_dir.glob("benchmarks.json"))
    if not json_files:
        json_files = list(bench_dir.glob("*.json"))
    if not json_files:
        return None

    try:
        with open(json_files[0]) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return None

    benchmarks = data.get("benchmarks", [])
    if not benchmarks:
        return None

    b = benchmarks[0]
    metrics = b.get("metrics", {})
    req_latency = metrics.get("request_latency", {}).get("successful", {})
    rps = metrics.get("requests_per_second", {}).get("successful", {})
    totals = metrics.get("request_totals", {})
    output_tokens = metrics.get("output_token_count", {}).get("successful", {})

    percentiles = req_latency.get("percentiles", {})

    return {
        "rps_mean": rps.get("mean", 0),
        "rps_median": rps.get("median", 0),
        "latency_mean": req_latency.get("mean", 0),
        "latency_p50": percentiles.get("p50", req_latency.get("median", 0)),
        "latency_p95": percentiles.get("p95", 0),
        "latency_p99": percentiles.get("p99", 0),
        "latency_min": req_latency.get("min", 0),
        "latency_max": req_latency.get("max", 0),
        "requests_total": totals.get("total", 0),
        "requests_successful": totals.get("successful", 0),
        "requests_errored": totals.get("errored", 0),
        "output_tokens_mean": output_tokens.get("mean", 0),
        "duration": b.get("duration", 0),
    }


def parse_bench_name(name: str) -> dict:
    """Extract payload, depth, concurrency from benchmark directory name.

    Examples:
        payload-small-c16  → {"type": "payload", "payload": "small", "concurrency": 16}
        multiturn-10-c64   → {"type": "multiturn", "depth": "10", "concurrency": 64}
    """
    m = re.match(r"payload-(.+)-c(\d+)$", name)
    if m:
        return {"type": "payload", "payload": m.group(1), "concurrency": int(m.group(2))}

    m = re.match(r"multiturn-(\d+)-c(\d+)$", name)
    if m:
        return {"type": "multiturn", "depth": m.group(1), "concurrency": int(m.group(2))}

    return {"type": "unknown", "name": name}


def collect_stage_data(stage_dir: Path) -> list[dict]:
    """Collect all benchmark results from a stage directory."""
    rows = []
    if not stage_dir.exists():
        return rows

    for d in sorted(stage_dir.iterdir()):
        if not d.is_dir():
            continue
        parsed = parse_bench_name(d.name)
        metrics = parse_benchmark_dir(d)
        if metrics is None:
            continue
        row = {**parsed, **metrics, "bench_name": d.name}
        rows.append(row)

    return rows


def collect_hpa_data(hpa_dir: Path) -> dict[int, list[dict]]:
    """Collect benchmark results for each replica count."""
    result = {}
    if not hpa_dir.exists():
        return result

    for d in sorted(hpa_dir.iterdir()):
        if not d.is_dir():
            continue
        m = re.match(r"replicas-(\d+)", d.name)
        if not m:
            continue
        replicas = int(m.group(1))
        result[replicas] = collect_stage_data(d)

    return result


# ───────────────────────────────────────────────────────────────────
# CSV Export
# ───────────────────────────────────────────────────────────────────

CSV_COLUMNS = [
    "stage", "bench_name", "type", "payload", "depth", "concurrency",
    "replicas", "rps_mean", "latency_p50", "latency_p95", "latency_p99",
    "latency_mean", "latency_min", "latency_max",
    "requests_total", "requests_successful", "requests_errored",
    "output_tokens_mean", "duration",
]


def write_csv(rows: list[dict], path: Path):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  CSV: {path} ({len(rows)} rows)")


# ───────────────────────────────────────────────────────────────────
# Plotting helpers
# ───────────────────────────────────────────────────────────────────

def _setup_concurrency_axis(ax, title, ylabel):
    ax.set_xlabel("Concurrency")
    ax.set_ylabel(ylabel)
    ax.set_title(title)
    ax.set_xscale("log", base=2)
    ax.xaxis.set_major_formatter(ticker.ScalarFormatter())
    ax.xaxis.set_major_locator(ticker.FixedLocator(CONCURRENCY_LEVELS))
    ax.grid(True, alpha=0.3)
    ax.legend(fontsize=8)


def _group_by_key(rows: list[dict], key: str) -> dict[str, list[dict]]:
    groups = {}
    for r in rows:
        k = r.get(key, "unknown")
        groups.setdefault(k, []).append(r)
    return groups


def _sorted_by_concurrency(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: r.get("concurrency", 0))


# ───────────────────────────────────────────────────────────────────
# Plot functions
# ───────────────────────────────────────────────────────────────────

def plot_throughput_vs_concurrency(data: list[dict], title: str, path: Path,
                                   group_key: str, colors: dict):
    """Line plot: RPS vs concurrency, one line per group."""
    if not HAS_MATPLOTLIB:
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    groups = _group_by_key(data, group_key)

    for label in sorted(groups.keys()):
        rows = _sorted_by_concurrency(groups[label])
        xs = [r["concurrency"] for r in rows]
        ys = [r["rps_mean"] for r in rows]
        color = colors.get(label, None)
        ax.plot(xs, ys, "o-", label=label, color=color, markersize=4)

    _setup_concurrency_axis(ax, title, "Throughput (RPS)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot: {path.name}")


def plot_latency_vs_concurrency(data: list[dict], title: str, path: Path,
                                 group_key: str, colors: dict,
                                 percentile: str = "latency_p95"):
    """Line plot: latency percentile vs concurrency, one line per group."""
    if not HAS_MATPLOTLIB:
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    groups = _group_by_key(data, group_key)

    for label in sorted(groups.keys()):
        rows = _sorted_by_concurrency(groups[label])
        xs = [r["concurrency"] for r in rows]
        ys = [r[percentile] * 1000 for r in rows]  # seconds → ms
        color = colors.get(label, None)
        ax.plot(xs, ys, "o-", label=label, color=color, markersize=4)

    pname = percentile.replace("latency_", "").upper()
    _setup_concurrency_axis(ax, title, f"Latency {pname} (ms)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot: {path.name}")


def plot_error_rate_vs_concurrency(data: list[dict], title: str, path: Path,
                                    group_key: str, colors: dict):
    """Line plot: error rate (%) vs concurrency."""
    if not HAS_MATPLOTLIB:
        return
    fig, ax = plt.subplots(figsize=(10, 6))
    groups = _group_by_key(data, group_key)

    for label in sorted(groups.keys()):
        rows = _sorted_by_concurrency(groups[label])
        xs = [r["concurrency"] for r in rows]
        ys = []
        for r in rows:
            total = r.get("requests_total", 1) or 1
            errs = r.get("requests_errored", 0)
            ys.append(errs / total * 100)
        color = colors.get(label, None)
        ax.plot(xs, ys, "o-", label=label, color=color, markersize=4)

    _setup_concurrency_axis(ax, title, "Error Rate (%)")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot: {path.name}")


def plot_overhead_delta(baseline: list[dict], gateway: list[dict],
                        title: str, path: Path, metric: str = "latency_p95"):
    """Bar chart: overhead delta (gateway - baseline) at each (payload, concurrency)."""
    if not HAS_MATPLOTLIB:
        return

    base_lookup = {}
    for r in baseline:
        key = (r.get("payload", r.get("depth", "")), r.get("concurrency"))
        base_lookup[key] = r

    gw_lookup = {}
    for r in gateway:
        key = (r.get("payload", r.get("depth", "")), r.get("concurrency"))
        gw_lookup[key] = r

    common_keys = sorted(set(base_lookup.keys()) & set(gw_lookup.keys()),
                         key=lambda k: (k[0], k[1]))
    if not common_keys:
        return

    fig, ax = plt.subplots(figsize=(14, 6))
    labels = [f"{k[0]}\nc{k[1]}" for k in common_keys]
    deltas = []
    for k in common_keys:
        b_val = base_lookup[k].get(metric, 0)
        g_val = gw_lookup[k].get(metric, 0)
        deltas.append((g_val - b_val) * 1000)  # seconds → ms

    colors = ["#F44336" if d > 10 else "#4CAF50" for d in deltas]
    ax.bar(range(len(deltas)), deltas, color=colors, alpha=0.8)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=6, rotation=45, ha="right")
    ax.axhline(y=10, color="red", linestyle="--", alpha=0.5, label="10ms threshold")
    ax.set_ylabel(f"Overhead Delta — {metric.replace('latency_', '').upper()} (ms)")
    ax.set_title(title)
    ax.legend()
    ax.grid(True, alpha=0.3, axis="y")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot: {path.name}")


def plot_scaling_factor(hpa_data: dict[int, list[dict]], title: str, path: Path,
                        payload_filter: str = "medium"):
    """Line plot: scaling factor vs replica count for a given payload."""
    if not HAS_MATPLOTLIB:
        return

    base_rps = {}
    if 1 in hpa_data:
        for r in hpa_data[1]:
            if r.get("payload") == payload_filter:
                base_rps[r["concurrency"]] = r["rps_mean"]

    if not base_rps:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    replica_counts = sorted(hpa_data.keys())
    for C in CONCURRENCY_LEVELS:
        if C not in base_rps:
            continue
        xs, factors, rps_vals = [], [], []
        for rep in replica_counts:
            for r in hpa_data[rep]:
                if r.get("payload") == payload_filter and r.get("concurrency") == C:
                    xs.append(rep)
                    rps_vals.append(r["rps_mean"])
                    factors.append(r["rps_mean"] / base_rps[C] if base_rps[C] > 0 else 0)
        if len(xs) > 1:
            ax1.plot(xs, factors, "o-", label=f"c{C}", markersize=4)
            ax2.plot(xs, rps_vals, "o-", label=f"c{C}", markersize=4)

    # Ideal linear line
    ax1.plot(replica_counts, replica_counts, "k--", alpha=0.3, label="ideal linear")
    ax1.set_xlabel("Replicas")
    ax1.set_ylabel("Scaling Factor (RPS_N / RPS_1)")
    ax1.set_title(f"{title} — Scaling Factor ({payload_filter})")
    ax1.set_xticks(replica_counts)
    ax1.legend(fontsize=7)
    ax1.grid(True, alpha=0.3)

    ax2.set_xlabel("Replicas")
    ax2.set_ylabel("Throughput (RPS)")
    ax2.set_title(f"{title} — Absolute RPS ({payload_filter})")
    ax2.set_xticks(replica_counts)
    ax2.legend(fontsize=7)
    ax2.grid(True, alpha=0.3)

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot: {path.name}")


def plot_stage_comparison(baseline: list[dict], gateway: list[dict],
                          title: str, path: Path, payload_filter: str = "medium"):
    """Overlay: baseline vs gateway throughput and latency for one payload."""
    if not HAS_MATPLOTLIB:
        return

    b_rows = _sorted_by_concurrency([r for r in baseline if r.get("payload") == payload_filter])
    g_rows = _sorted_by_concurrency([r for r in gateway if r.get("payload") == payload_filter])

    if not b_rows or not g_rows:
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 6))

    # Throughput
    ax1.plot([r["concurrency"] for r in b_rows], [r["rps_mean"] for r in b_rows],
             "o-", label="Baseline (direct)", color="#2196F3", markersize=4)
    ax1.plot([r["concurrency"] for r in g_rows], [r["rps_mean"] for r in g_rows],
             "s-", label="Gateway", color="#F44336", markersize=4)
    _setup_concurrency_axis(ax1, f"{title} — Throughput ({payload_filter})", "RPS")

    # Latency p95
    ax2.plot([r["concurrency"] for r in b_rows],
             [r["latency_p95"] * 1000 for r in b_rows],
             "o-", label="Baseline p95", color="#2196F3", markersize=4)
    ax2.plot([r["concurrency"] for r in g_rows],
             [r["latency_p95"] * 1000 for r in g_rows],
             "s-", label="Gateway p95", color="#F44336", markersize=4)
    _setup_concurrency_axis(ax2, f"{title} — Latency P95 ({payload_filter})", "Latency (ms)")

    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  Plot: {path.name}")


# ───────────────────────────────────────────────────────────────────
# Main
# ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Phase 1 Results Analyzer & Plot Generator")
    parser.add_argument("results_dir", type=Path, help="Path to timestamped results directory")
    args = parser.parse_args()

    results_dir = args.results_dir
    if not results_dir.exists():
        print(f"ERROR: {results_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    report_dir = results_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)

    all_csv_rows = []

    # ── Stage 1 ──
    print("\n=== Stage 1: Baseline ===")
    s1 = collect_stage_data(results_dir / "stage1-baseline")
    s1_payload = [r for r in s1 if r["type"] == "payload"]
    s1_multi = [r for r in s1 if r["type"] == "multiturn"]

    for r in s1:
        r["stage"] = "stage1-baseline"
        r["replicas"] = 1
    all_csv_rows.extend(s1)
    print(f"  Collected {len(s1)} benchmarks ({len(s1_payload)} payload, {len(s1_multi)} multiturn)")

    if s1_payload:
        plot_throughput_vs_concurrency(s1_payload, "Stage 1 — Throughput vs Concurrency (Baseline)",
                                       report_dir / "stage1_payload_throughput.png", "payload", PAYLOAD_COLORS)
        plot_latency_vs_concurrency(s1_payload, "Stage 1 — Latency P95 (Baseline)",
                                    report_dir / "stage1_payload_latency_p95.png", "payload", PAYLOAD_COLORS)
        plot_latency_vs_concurrency(s1_payload, "Stage 1 — Latency P99 (Baseline)",
                                    report_dir / "stage1_payload_latency_p99.png", "payload", PAYLOAD_COLORS, "latency_p99")
        plot_error_rate_vs_concurrency(s1_payload, "Stage 1 — Error Rate (Baseline)",
                                       report_dir / "stage1_payload_errors.png", "payload", PAYLOAD_COLORS)

    if s1_multi:
        plot_throughput_vs_concurrency(s1_multi, "Stage 1 — Multi-Turn Throughput (Baseline)",
                                       report_dir / "stage1_multiturn_throughput.png", "depth", DEPTH_COLORS)
        plot_latency_vs_concurrency(s1_multi, "Stage 1 — Multi-Turn Latency P95 (Baseline)",
                                    report_dir / "stage1_multiturn_latency_p95.png", "depth", DEPTH_COLORS)

    # ── Stage 2 ──
    print("\n=== Stage 2: Gateway Overhead ===")
    s2 = collect_stage_data(results_dir / "stage2-gateway-overhead")
    s2_payload = [r for r in s2 if r["type"] == "payload"]
    s2_multi = [r for r in s2 if r["type"] == "multiturn"]

    for r in s2:
        r["stage"] = "stage2-gateway"
        r["replicas"] = 1
    all_csv_rows.extend(s2)
    print(f"  Collected {len(s2)} benchmarks ({len(s2_payload)} payload, {len(s2_multi)} multiturn)")

    if s2_payload:
        plot_throughput_vs_concurrency(s2_payload, "Stage 2 — Throughput vs Concurrency (Gateway)",
                                       report_dir / "stage2_payload_throughput.png", "payload", PAYLOAD_COLORS)
        plot_latency_vs_concurrency(s2_payload, "Stage 2 — Latency P95 (Gateway)",
                                    report_dir / "stage2_payload_latency_p95.png", "payload", PAYLOAD_COLORS)
        plot_latency_vs_concurrency(s2_payload, "Stage 2 — Latency P99 (Gateway)",
                                    report_dir / "stage2_payload_latency_p99.png", "payload", PAYLOAD_COLORS, "latency_p99")
        plot_error_rate_vs_concurrency(s2_payload, "Stage 2 — Error Rate (Gateway)",
                                       report_dir / "stage2_payload_errors.png", "payload", PAYLOAD_COLORS)

    if s2_multi:
        plot_throughput_vs_concurrency(s2_multi, "Stage 2 — Multi-Turn Throughput (Gateway)",
                                       report_dir / "stage2_multiturn_throughput.png", "depth", DEPTH_COLORS)
        plot_latency_vs_concurrency(s2_multi, "Stage 2 — Multi-Turn Latency P95 (Gateway)",
                                    report_dir / "stage2_multiturn_latency_p95.png", "depth", DEPTH_COLORS)

    # ── Stage 1 vs Stage 2 comparison ──
    if s1_payload and s2_payload:
        print("\n=== Overhead Delta (Stage 2 − Stage 1) ===")
        plot_overhead_delta(s1_payload, s2_payload,
                           "Gateway Overhead Delta — Payload × Concurrency (P95)",
                           report_dir / "overhead_delta_payload_p95.png", "latency_p95")
        plot_overhead_delta(s1_payload, s2_payload,
                           "Gateway Overhead Delta — Payload × Concurrency (P99)",
                           report_dir / "overhead_delta_payload_p99.png", "latency_p99")
        for payload in ["small", "medium", "large", "very-large"]:
            plot_stage_comparison(s1_payload, s2_payload,
                                 "Baseline vs Gateway",
                                 report_dir / f"comparison_{payload}.png", payload)

    if s1_multi and s2_multi:
        plot_overhead_delta(s1_multi, s2_multi,
                           "Gateway Overhead Delta — Multi-Turn × Concurrency (P95)",
                           report_dir / "overhead_delta_multiturn_p95.png", "latency_p95")

    # ── Stage 3: HPA ──
    print("\n=== Stage 3: HPA Scaling ===")
    hpa_data = collect_hpa_data(results_dir / "stage3-hpa-scaling")
    for replicas, rows in hpa_data.items():
        for r in rows:
            r["stage"] = "stage3-hpa"
            r["replicas"] = replicas
        all_csv_rows.extend(rows)
        print(f"  Replicas={replicas}: {len(rows)} benchmarks")

    if hpa_data:
        for payload in ["medium", "large", "very-large"]:
            plot_scaling_factor(hpa_data, "Stage 3 — HPA Scaling",
                               report_dir / f"stage3_scaling_{payload}.png", payload)

    # ── Stage 4: Plugin latency ──
    print("\n=== Stage 4: Plugin Latency ===")
    plugin_file = results_dir / "stage4-plugin-latency" / "plugin_latency_breakdown.json"
    if plugin_file.exists():
        with open(plugin_file) as f:
            plugin_data = json.load(f)
        print(f"  Plugins: {list(plugin_data.keys())}")
        for name, vals in plugin_data.items():
            print(f"    {name}: p50={vals.get('p50_ms', 'N/A')}ms  "
                  f"p95={vals.get('p95_ms', 'N/A')}ms  "
                  f"p99={vals.get('p99_ms', 'N/A')}ms")
    else:
        print("  No plugin latency data found.")

    # ── CSV ──
    print(f"\n=== Writing CSV ({len(all_csv_rows)} rows) ===")
    write_csv(all_csv_rows, report_dir / "summary.csv")

    # ── Summary ──
    print(f"\n{'='*60}")
    print(f"  Report directory: {report_dir}")
    print(f"  Total benchmarks: {len(all_csv_rows)}")
    print(f"  CSV:    summary.csv")
    if HAS_MATPLOTLIB:
        plots = list(report_dir.glob("*.png"))
        print(f"  Plots:  {len(plots)} files")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
