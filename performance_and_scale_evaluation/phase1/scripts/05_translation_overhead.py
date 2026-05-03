#!/usr/bin/env python3
"""
05 — Response Translation Overhead

Isolates the cost of translating request/response bodies per provider
by using cross-provider comparison groups:

  Group A: GPT-4o via OpenAI (passthrough) vs Azure vs Bedrock
  Group B: Claude Sonnet via Anthropic vs Bedrock vs Vertex

Same model, same prompt, different API format → the delta is pure
translation cost (marshal/unmarshal + field mapping).

Metrics: latency added by TranslateResponse per provider.
"""

import argparse
from utils import (
    load_config, create_run_dir, run_guidellm,
    get_gateway_url, get_models_by_group,
    print_header, print_summary, save_metadata,
)


def run_group(config: dict, run_dir, group: str):
    """Sweep all models in a comparison group with all payload sizes."""
    gateway_url = get_gateway_url(config)
    models = get_models_by_group(config, group)

    print(f"\n--- Group {group}: {[m['name'] for m in models]} ---\n")

    for model in models:
        for payload_name, payload in config["payloads"].items():
            output = run_dir / f"group{group}_{model['name']}_{payload_name}"
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
    parser = argparse.ArgumentParser(description="05: Translation overhead comparison")
    parser.add_argument("--group", choices=["A", "B", "C", "all"], default="all",
                        help="Which comparison group to run")
    parser.add_argument("--max-seconds", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    if args.max_seconds:
        config["benchmark"]["max_seconds"] = args.max_seconds

    run_dir = create_run_dir("05_translation_overhead")
    save_metadata(run_dir, {"evaluation": "05_translation_overhead", "group": args.group, "config": config})

    print_header("05 — Translation Overhead: Cross-Provider Comparison")
    groups = ["A", "B", "C"] if args.group == "all" else [args.group]
    for group in groups:
        run_group(config, run_dir, group)

    print_summary(run_dir)


if __name__ == "__main__":
    main()
