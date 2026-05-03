#!/usr/bin/env python3
"""Ingest benchmark CSV results into MLflow experiments.

Usage (from inside the cluster, or with port-forward active):
  python3 ingest_to_mlflow.py --tracking-uri http://mlflow:5000 --csv /results/openai/summary.csv --experiment "OpenAI A/B Test"
  python3 ingest_to_mlflow.py --tracking-uri http://localhost:5000 --csv summary.csv --experiment "OpenAI A/B Test"
"""
import argparse
import csv
import os
import mlflow


def parse_benchmark_name(name):
    """Extract structured info from benchmark name."""
    info = {}
    if "multiturn" in name:
        parts = name.split("-")
        info["test_type"] = "multi-turn"
        info["turns"] = int(parts[1])
        info["concurrency"] = int(parts[2].replace("c", ""))
        info["payload_label"] = "64/128"
    else:
        parts = name.rsplit("-c", 1)
        info["test_type"] = "payload"
        info["concurrency"] = int(parts[1])
        info["payload_label"] = parts[0].replace("payload-", "")
        info["turns"] = 0
    return info


def ingest_csv(csv_path, experiment_name, tracking_uri):
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)

    has_provider = False
    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        has_provider = "provider" in fieldnames
        for row in reader:
            rows.append(row)

    print(f"Loaded {len(rows)} rows from {csv_path}")
    print(f"Experiment: {experiment_name}")
    print(f"Has provider column: {has_provider}")
    print()

    benchmarks = {}
    for row in rows:
        key = row["benchmark"]
        if has_provider:
            provider = row.get("provider", key.split("/")[0] if "/" in key else "unknown")
            key = f"{provider}/{row['benchmark']}"
        if key not in benchmarks:
            benchmarks[key] = {}
        benchmarks[key][row["mode"]] = row

    logged = 0
    for bench_key, modes in sorted(benchmarks.items()):
        for mode_name, row in modes.items():
            info = parse_benchmark_name(row["benchmark"])
            provider = row.get("provider", "openai") if has_provider else "openai"

            run_name = f"{bench_key}/{mode_name}"

            with mlflow.start_run(run_name=run_name):
                mlflow.set_tag("benchmark", row["benchmark"])
                mlflow.set_tag("mode", mode_name)
                mlflow.set_tag("test_type", info["test_type"])
                mlflow.set_tag("payload_label", info["payload_label"])
                if has_provider:
                    mlflow.set_tag("provider", provider)

                mlflow.log_param("prompt_tokens", int(row["prompt_tokens"]))
                mlflow.log_param("output_tokens", int(row["output_tokens"]))
                mlflow.log_param("concurrency", info["concurrency"])
                mlflow.log_param("turns", info["turns"])
                mlflow.log_param("mode", mode_name)
                if has_provider:
                    mlflow.log_param("provider", provider)

                mlflow.log_metric("p95_ms", float(row["p95_ms"]))
                mlflow.log_metric("p99_ms", float(row["p99_ms"]))
                mlflow.log_metric("mean_ms", float(row["mean_ms"]))
                mlflow.log_metric("p50_ms", float(row["p50_ms"]))
                mlflow.log_metric("rps", float(row["rps"]))
                mlflow.log_metric("successful", int(row["successful"]))
                mlflow.log_metric("errored", int(row["errored"]))
                mlflow.log_metric("duration_s", float(row["duration_s"]))

                logged += 1

        if "baseline" in modes and "gateway" in modes:
            b = modes["baseline"]
            g = modes["gateway"]
            with mlflow.start_run(run_name=f"{bench_key}/overhead"):
                info = parse_benchmark_name(b["benchmark"])
                provider = b.get("provider", "openai") if has_provider else "openai"

                mlflow.set_tag("benchmark", b["benchmark"])
                mlflow.set_tag("mode", "overhead")
                mlflow.set_tag("test_type", info["test_type"])
                mlflow.set_tag("payload_label", info["payload_label"])
                if has_provider:
                    mlflow.set_tag("provider", provider)

                mlflow.log_param("prompt_tokens", int(b["prompt_tokens"]))
                mlflow.log_param("output_tokens", int(b["output_tokens"]))
                mlflow.log_param("concurrency", info["concurrency"])
                mlflow.log_param("turns", info["turns"])
                mlflow.log_param("mode", "overhead")
                if has_provider:
                    mlflow.log_param("provider", provider)

                bp95 = float(b["p95_ms"])
                gp95 = float(g["p95_ms"])
                bp99 = float(b["p99_ms"])
                gp99 = float(g["p99_ms"])

                mlflow.log_metric("overhead_p95_ms", gp95 - bp95)
                mlflow.log_metric("overhead_p99_ms", gp99 - bp99)
                mlflow.log_metric("overhead_p95_pct", ((gp95 - bp95) / bp95) * 100 if bp95 > 0 else 0)
                mlflow.log_metric("overhead_p99_pct", ((gp99 - bp99) / bp99) * 100 if bp99 > 0 else 0)
                mlflow.log_metric("overhead_mean_ms", float(g["mean_ms"]) - float(b["mean_ms"]))
                mlflow.log_metric("rps_baseline", float(b["rps"]))
                mlflow.log_metric("rps_gateway", float(g["rps"]))
                mlflow.log_metric("rps_reduction_pct", ((float(b["rps"]) - float(g["rps"])) / float(b["rps"])) * 100 if float(b["rps"]) > 0 else 0)

                logged += 1

    print(f"Logged {logged} runs to MLflow")


def ingest_plugin_csv(csv_path, experiment_name, tracking_uri):
    """Ingest plugin latency CSV into a separate experiment."""
    if not os.path.exists(csv_path):
        print(f"Plugin CSV not found: {csv_path}")
        return

    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(f"{experiment_name} - Plugin Latency")

    rows = []
    with open(csv_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    print(f"Loaded {len(rows)} plugin latency rows from {csv_path}")

    logged = 0
    for row in rows:
        run_name = f"{row['benchmark']}/{row['extension_point']}/{row['plugin_name']}"
        with mlflow.start_run(run_name=run_name):
            mlflow.set_tag("benchmark", row["benchmark"])
            mlflow.set_tag("extension_point", row["extension_point"])
            mlflow.set_tag("plugin_type", row["plugin_type"])
            mlflow.set_tag("plugin_name", row["plugin_name"])
            mlflow.log_metric("avg_latency_us", float(row["avg_latency_us"]))
            mlflow.log_metric("requests", int(row["requests"]))
            logged += 1

    print(f"Logged {logged} plugin latency runs")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest benchmark results into MLflow")
    parser.add_argument("--tracking-uri", default="http://localhost:5000")
    parser.add_argument("--csv", required=True, help="Path to summary.csv")
    parser.add_argument("--plugin-csv", default=None, help="Path to plugin_latency.csv")
    parser.add_argument("--experiment", required=True, help="Experiment name")
    args = parser.parse_args()

    ingest_csv(args.csv, args.experiment, args.tracking_uri)

    if args.plugin_csv:
        ingest_plugin_csv(args.plugin_csv, args.experiment, args.tracking_uri)
    else:
        base_dir = os.path.dirname(args.csv)
        plugin_path = os.path.join(base_dir, "plugin_latency.csv")
        if os.path.exists(plugin_path):
            print(f"\nFound plugin CSV at {plugin_path}")
            ingest_plugin_csv(plugin_path, args.experiment, args.tracking_uri)
