#!/usr/bin/env python3
"""
03 — Multi-Provider Through Gateway

Runs the same benchmark through the MaaS gateway for ALL providers
to measure overhead per provider (translation cost differences).

Each model uses the sweep profile to auto-find its saturation point.
The analysis step compares across providers to reveal which translation
path is cheapest (OpenAI passthrough) vs most expensive (Anthropic/Vertex).

Metrics: per-provider throughput, latency, overhead delta vs baseline.
"""

import argparse
from utils import (
    load_config, create_run_dir, run_guidellm,
    get_gateway_url, get_all_models,
    print_header, print_summary, save_metadata,
)


def run_multi_provider(config: dict, run_dir, models: list[dict]):
    """Sweep every model through the gateway with a medium payload."""
    gateway_url = get_gateway_url(config)
    medium_payload = config["payloads"]["medium"]

    for model in models:
        output = run_dir / f"gateway_{model['name']}"
        run_guidellm(
            target=gateway_url,
            model=model["name"],
            profile="sweep",
            output_dir=output,
            prompt_tokens=medium_payload["prompt_tokens"],
            output_tokens=medium_payload["output_tokens"],
            max_seconds=config["benchmark"]["max_seconds"],
        )


def main():
    parser = argparse.ArgumentParser(description="03: Multi-provider through gateway")
    parser.add_argument("--max-seconds", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    if args.max_seconds:
        config["benchmark"]["max_seconds"] = args.max_seconds

    models = get_all_models(config)
    run_dir = create_run_dir("03_multi_provider")
    save_metadata(run_dir, {"evaluation": "03_multi_provider", "models": models, "config": config})

    print_header(f"03 — Multi-Provider: {[m['name'] for m in models]}")
    run_multi_provider(config, run_dir, models)
    print_summary(run_dir)


if __name__ == "__main__":
    main()
