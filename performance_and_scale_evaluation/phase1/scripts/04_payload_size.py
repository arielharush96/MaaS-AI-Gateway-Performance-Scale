#!/usr/bin/env python3
"""
04 — Payload Size Variations

Tests the gateway with increasing request body sizes:
  small  (10 tokens,   ~50 bytes)
  medium (256 tokens,  ~1KB)
  large  (1024 tokens, ~4KB)
  very_large (5120 tokens, ~20KB)

Purpose: determine whether JSON marshal/unmarshal cost grows linearly
with body size or spikes at some threshold. Uses a single provider
(OpenAI passthrough) to isolate the payload size effect from translation cost.

Metrics: throughput, latency, CPU/memory of gateway pod.
"""

import argparse
from utils import (
    load_config, create_run_dir, run_guidellm,
    get_gateway_url, get_all_models,
    print_header, print_summary, save_metadata,
)


def run_payload_sweep(config: dict, run_dir, model: dict):
    """Run sweep for each payload size through the gateway."""
    gateway_url = get_gateway_url(config)

    for payload_name, payload in config["payloads"].items():
        output = run_dir / f"{model['name']}_{payload_name}"
        run_guidellm(
            target=gateway_url,
            model=model["name"],
            profile="sweep",
            output_dir=output,
            prompt_tokens=payload["prompt_tokens"],
            output_tokens=payload["output_tokens"],
            max_seconds=config["benchmark"]["max_seconds"],
        )


def main():
    parser = argparse.ArgumentParser(description="04: Payload size variations")
    parser.add_argument("--model", type=str, default=None,
                        help="Model name (default: first model, ideally OpenAI passthrough)")
    parser.add_argument("--max-seconds", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    if args.max_seconds:
        config["benchmark"]["max_seconds"] = args.max_seconds

    all_models = get_all_models(config)
    if args.model:
        models = [m for m in all_models if m["name"] == args.model]
        target_model = models[0] if models else all_models[0]
    else:
        # Default to OpenAI passthrough to isolate payload effect
        openai = [m for m in all_models if m["provider"] == "openai"]
        target_model = openai[0] if openai else all_models[0]

    run_dir = create_run_dir("04_payload_size")
    save_metadata(run_dir, {"evaluation": "04_payload_size", "model": target_model, "config": config})

    print_header(f"04 — Payload Size: {target_model['name']}")
    run_payload_sweep(config, run_dir, target_model)
    print_summary(run_dir)


if __name__ == "__main__":
    main()
