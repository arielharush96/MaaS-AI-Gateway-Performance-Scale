#!/usr/bin/env python3
"""
06 — Plugin Chain Latency Breakdown

Runs a load test through the gateway and simultaneously queries Prometheus
for per-plugin latency using the bbr_plugin_duration_seconds histogram
that the upstream framework already records.

The framework records a histogram per plugin name:
  bbr_plugin_duration_seconds{plugin="body-field-to-header"}
  bbr_plugin_duration_seconds{plugin="model-provider-resolver"}
  bbr_plugin_duration_seconds{plugin="api-translation"}
  bbr_plugin_duration_seconds{plugin="apikey-injection"}

This script:
  1. Records a Prometheus timestamp (start)
  2. Runs a GuideLLM benchmark
  3. Records a Prometheus timestamp (end)
  4. Queries the histogram for that time window
  5. Produces a per-plugin latency breakdown table

Requirements: Prometheus must be accessible and scraping the BBR pod.
"""

import argparse
import json
import time
from datetime import datetime
from utils import (
    load_config, create_run_dir, run_guidellm,
    get_gateway_url, get_all_models,
    query_prometheus, query_prometheus_range,
    print_header, print_summary, save_metadata,
)

PLUGINS = [
    "body-field-to-header",
    "model-provider-resolver",
    "api-translation",
    "apikey-injection",
]


def collect_plugin_metrics(prometheus_url: str, start_ts: float, end_ts: float, run_dir):
    """Query Prometheus for per-plugin latency during the benchmark window."""
    print(f"\n{'='*60}")
    print("Plugin Chain Latency Breakdown")
    print(f"{'='*60}\n")

    start_iso = datetime.utcfromtimestamp(start_ts).isoformat() + "Z"
    end_iso = datetime.utcfromtimestamp(end_ts).isoformat() + "Z"

    results = {}

    for plugin in PLUGINS:
        # p50
        q_p50 = f'histogram_quantile(0.50, rate(bbr_plugin_duration_seconds_bucket{{plugin="{plugin}"}}[1m]))'
        # p95
        q_p95 = f'histogram_quantile(0.95, rate(bbr_plugin_duration_seconds_bucket{{plugin="{plugin}"}}[1m]))'
        # p99
        q_p99 = f'histogram_quantile(0.99, rate(bbr_plugin_duration_seconds_bucket{{plugin="{plugin}"}}[1m]))'
        # request count
        q_count = f'increase(bbr_plugin_duration_seconds_count{{plugin="{plugin}"}}[{int(end_ts - start_ts)}s])'

        try:
            r_p50 = query_prometheus(prometheus_url, q_p50)
            r_p95 = query_prometheus(prometheus_url, q_p95)
            r_p99 = query_prometheus(prometheus_url, q_p99)
            r_count = query_prometheus(prometheus_url, q_count)

            p50_val = _extract_value(r_p50)
            p95_val = _extract_value(r_p95)
            p99_val = _extract_value(r_p99)
            count_val = _extract_value(r_count)

            results[plugin] = {
                "p50_ms": p50_val * 1000 if p50_val else None,
                "p95_ms": p95_val * 1000 if p95_val else None,
                "p99_ms": p99_val * 1000 if p99_val else None,
                "count": int(count_val) if count_val else None,
            }
        except Exception as e:
            print(f"  WARNING: Failed to query metrics for {plugin}: {e}")
            results[plugin] = {"error": str(e)}

    # Print table
    print(f"{'Plugin':<30} {'p50 (ms)':<12} {'p95 (ms)':<12} {'p99 (ms)':<12} {'Count':<10}")
    print("-" * 76)
    total_p50 = 0
    for plugin in PLUGINS:
        r = results.get(plugin, {})
        p50 = r.get("p50_ms")
        p95 = r.get("p95_ms")
        p99 = r.get("p99_ms")
        count = r.get("count")
        if p50 is not None:
            total_p50 += p50
        print(f"{plugin:<30} {_fmt(p50):<12} {_fmt(p95):<12} {_fmt(p99):<12} {count or 'N/A':<10}")

    print("-" * 76)
    print(f"{'TOTAL (cumulative p50)':<30} {total_p50:>8.2f}ms")
    print()

    # Save to file
    output_path = run_dir / "plugin_latency_breakdown.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Saved to {output_path}")

    return results


def _extract_value(prom_result: dict) -> float | None:
    try:
        data = prom_result.get("data", {}).get("result", [])
        if data:
            return float(data[0]["value"][1])
    except (IndexError, KeyError, ValueError):
        pass
    return None


def _fmt(val) -> str:
    return f"{val:.3f}" if val is not None else "N/A"


def main():
    parser = argparse.ArgumentParser(description="06: Plugin chain latency breakdown")
    parser.add_argument("--model", type=str, default=None,
                        help="Model to benchmark during collection (default: first)")
    parser.add_argument("--prometheus-url", type=str, default=None,
                        help="Prometheus URL (default: from config)")
    parser.add_argument("--max-seconds", type=int, default=60)
    parser.add_argument("--concurrency", type=int, default=50,
                        help="Concurrent users during collection")
    args = parser.parse_args()

    config = load_config()
    prometheus_url = args.prometheus_url or config.get("prometheus", {}).get("url", "http://localhost:9090")

    all_models = get_all_models(config)
    if args.model:
        models = [m for m in all_models if m["name"] == args.model]
        target = models[0] if models else all_models[0]
    else:
        target = all_models[0]

    run_dir = create_run_dir("06_plugin_chain_latency")
    save_metadata(run_dir, {
        "evaluation": "06_plugin_chain_latency",
        "model": target, "concurrency": args.concurrency,
        "prometheus_url": prometheus_url,
    })

    print_header(f"06 — Plugin Chain Latency: {target['name']} @ {args.concurrency} concurrent")

    gateway_url = get_gateway_url(config)
    medium_payload = config["payloads"]["medium"]

    start_ts = time.time()

    run_guidellm(
        target=gateway_url,
        model=target["name"],
        profile="concurrent",
        rate=str(args.concurrency),
        output_dir=run_dir / f"benchmark_{target['name']}",
        prompt_tokens=medium_payload["prompt_tokens"],
        output_tokens=medium_payload["output_tokens"],
        max_seconds=args.max_seconds,
    )

    end_ts = time.time()

    print("\nCollecting Prometheus metrics for the benchmark window...")
    collect_plugin_metrics(prometheus_url, start_ts, end_ts, run_dir)

    print_summary(run_dir)


if __name__ == "__main__":
    main()
