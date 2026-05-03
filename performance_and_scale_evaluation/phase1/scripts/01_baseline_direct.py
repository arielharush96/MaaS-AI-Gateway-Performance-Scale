#!/usr/bin/env python3
"""
01 — Baseline: Direct to Provider (No Gateway)

The control group. Sends requests directly to each provider's API,
bypassing the MaaS gateway entirely. This establishes the "floor"
against which all gateway measurements are compared.

Metrics collected: TTFT, throughput (RPS), error rate, p50/p95/p99 latency.
"""

import argparse
from utils import (
    load_config, create_run_dir, run_guidellm,
    get_provider_url, get_all_models,
    print_header, print_summary, save_metadata,
)


def run_baseline(config: dict, run_dir, models: list[dict]):
    """Sweep profile direct to each provider — no gateway in the path."""
    for model in models:
        provider_url = get_provider_url(config, model["provider"])

        for payload_name, payload in config["payloads"].items():
            output = run_dir / f"direct_{model['provider']}_{model['target_model']}_{payload_name}"
            run_guidellm(
                target=provider_url,
                model=model["target_model"],
                profile="sweep",
                output_dir=output,
                prompt_tokens=payload["prompt_tokens"],
                output_tokens=payload["output_tokens"],
                max_seconds=config["benchmark"]["max_seconds"],
            )


def main():
    parser = argparse.ArgumentParser(description="01: Baseline — direct to provider")
    parser.add_argument("--provider", type=str, default=None,
                        help="Run for a specific provider only (openai, anthropic, azure, bedrock, vertex)")
    parser.add_argument("--payload", type=str, default=None,
                        help="Run for a specific payload size only (small, medium, large, very_large)")
    parser.add_argument("--max-seconds", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    if args.max_seconds:
        config["benchmark"]["max_seconds"] = args.max_seconds
    if args.payload:
        config["payloads"] = {args.payload: config["payloads"][args.payload]}

    models = get_all_models(config)
    if args.provider:
        models = [m for m in models if m["provider"] == args.provider]

    # Deduplicate: if the same target_model appears for multiple channels,
    # only test it once per provider for the baseline.
    seen = set()
    unique_models = []
    for m in models:
        key = (m["provider"], m["target_model"])
        if key not in seen:
            seen.add(key)
            unique_models.append(m)

    run_dir = create_run_dir("01_baseline_direct")
    save_metadata(run_dir, {"evaluation": "01_baseline_direct", "models": unique_models, "config": config})
    print_header("01 — Baseline: Direct to Provider (no gateway)")
    run_baseline(config, run_dir, unique_models)
    print_summary(run_dir)


if __name__ == "__main__":
    main()
