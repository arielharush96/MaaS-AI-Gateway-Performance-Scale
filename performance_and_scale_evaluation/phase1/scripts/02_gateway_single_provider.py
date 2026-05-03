#!/usr/bin/env python3
"""
02 — Single Provider Through Gateway, Ramp Concurrency

Sends all requests to ONE model through the MaaS gateway while ramping
concurrent users: 10 → 50 → 100 → 250 → 500 → 1000.

Purpose: find the throughput ceiling and saturation point for a single
provider channel without cross-provider interference.

Metrics: TTFT, throughput (RPS), error rate, p50/p95/p99 latency.
"""

import argparse
from utils import (
    load_config, create_run_dir, run_guidellm,
    get_gateway_url, get_all_models,
    print_header, print_summary, save_metadata,
)


def run_single_provider_ramp(config: dict, run_dir, model: dict):
    """Run fixed concurrency benchmarks at each level for a single model."""
    gateway_url = get_gateway_url(config)
    medium_payload = config["payloads"]["medium"]

    for concurrency in config["concurrency_levels"]:
        output = run_dir / f"{model['name']}_c{concurrency}"
        run_guidellm(
            target=gateway_url,
            model=model["name"],
            profile="concurrent",
            rate=str(concurrency),
            output_dir=output,
            prompt_tokens=medium_payload["prompt_tokens"],
            output_tokens=medium_payload["output_tokens"],
            max_seconds=config["benchmark"]["max_seconds"],
        )


def main():
    parser = argparse.ArgumentParser(description="02: Single provider — ramp concurrency through gateway")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name from config (e.g., gpt-4o-openai). Default: first model.")
    parser.add_argument("--max-seconds", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    if args.max_seconds:
        config["benchmark"]["max_seconds"] = args.max_seconds

    all_models = get_all_models(config)
    if args.model:
        models = [m for m in all_models if m["name"] == args.model]
        if not models:
            print(f"Model '{args.model}' not found. Available: {[m['name'] for m in all_models]}")
            return
        target_model = models[0]
    else:
        target_model = all_models[0]

    run_dir = create_run_dir("02_single_provider")
    save_metadata(run_dir, {"evaluation": "02_single_provider", "model": target_model, "config": config})

    print_header(f"02 — Single Provider Ramp: {target_model['name']}")
    run_single_provider_ramp(config, run_dir, target_model)
    print_summary(run_dir)


if __name__ == "__main__":
    main()
