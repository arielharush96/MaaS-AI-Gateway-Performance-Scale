#!/usr/bin/env python3
"""
07 — HPA Scaling Factor

Scales the BBR pod replicas (1 → 2 → 4 → 8) and runs the same benchmark
at each replica count to determine the scaling factor.

Questions answered:
  - Is scaling linear?
  - Where are the diminishing returns?
  - What replica count matches direct-provider throughput?

Requires: oc/kubectl access to scale the deployment.
This script prints the commands to run. If --apply is passed, it will
execute the scaling commands via subprocess (local kubectl/oc).

NOTE: Cluster commands should be run via iTerm MCP in practice.
This script generates the commands and runs the benchmarks.
"""

import argparse
import subprocess
import sys
from utils import (
    load_config, create_run_dir, run_guidellm,
    get_gateway_url, get_all_models,
    print_header, print_summary, save_metadata,
)

REPLICA_COUNTS = [1, 2, 4, 8]
DEFAULT_DEPLOYMENT = "payload-processing-body-based-routing"
DEFAULT_NAMESPACE = "openshift-ingress"


def scale_replicas(deployment: str, namespace: str, replicas: int, apply: bool):
    """Scale the BBR deployment to the given replica count."""
    cmd = f"oc scale deployment/{deployment} --replicas={replicas} -n {namespace}"

    if apply:
        print(f"\n  Scaling to {replicas} replicas: {cmd}")
        result = subprocess.run(cmd.split(), capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  ERROR: {result.stderr}", file=sys.stderr)
            return False

        # Wait for rollout
        wait_cmd = f"oc rollout status deployment/{deployment} -n {namespace} --timeout=120s"
        print(f"  Waiting for rollout: {wait_cmd}")
        result = subprocess.run(wait_cmd.split(), capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  WARNING: Rollout may not be complete: {result.stderr}", file=sys.stderr)
    else:
        print(f"\n  [DRY RUN] Would run: {cmd}")

    return True


def run_at_replica_count(config: dict, run_dir, model: dict, replicas: int, concurrency: int):
    """Run benchmark at a specific replica count."""
    gateway_url = get_gateway_url(config)
    medium_payload = config["payloads"]["medium"]

    output = run_dir / f"replicas_{replicas}_c{concurrency}"
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
    parser = argparse.ArgumentParser(description="07: HPA scaling factor evaluation")
    parser.add_argument("--model", type=str, default=None, help="Model to benchmark")
    parser.add_argument("--concurrency", type=int, default=100,
                        help="Fixed concurrency level for comparison across replica counts")
    parser.add_argument("--deployment", type=str, default=DEFAULT_DEPLOYMENT,
                        help="BBR deployment name")
    parser.add_argument("--namespace", type=str, default=DEFAULT_NAMESPACE)
    parser.add_argument("--replicas", type=str, default=None,
                        help="Comma-separated replica counts (default: 1,2,4,8)")
    parser.add_argument("--apply", action="store_true",
                        help="Actually scale the deployment (default: dry-run, just benchmarks)")
    parser.add_argument("--max-seconds", type=int, default=None)
    args = parser.parse_args()

    config = load_config()
    if args.max_seconds:
        config["benchmark"]["max_seconds"] = args.max_seconds

    replica_counts = [int(x) for x in args.replicas.split(",")] if args.replicas else REPLICA_COUNTS

    all_models = get_all_models(config)
    if args.model:
        models = [m for m in all_models if m["name"] == args.model]
        target = models[0] if models else all_models[0]
    else:
        target = all_models[0]

    run_dir = create_run_dir("07_hpa_scaling")
    save_metadata(run_dir, {
        "evaluation": "07_hpa_scaling",
        "model": target,
        "concurrency": args.concurrency,
        "replica_counts": replica_counts,
        "deployment": args.deployment,
        "namespace": args.namespace,
        "apply": args.apply,
    })

    print_header(f"07 — HPA Scaling: {target['name']} @ {args.concurrency} concurrent")

    for replicas in replica_counts:
        print(f"\n{'='*60}")
        print(f"  PHASE: {replicas} replica(s)")
        print(f"{'='*60}")

        ok = scale_replicas(args.deployment, args.namespace, replicas, args.apply)
        if not ok:
            print(f"  Skipping benchmark at {replicas} replicas due to scaling error.")
            continue

        run_at_replica_count(config, run_dir, target, replicas, args.concurrency)

    # Restore to 1 replica
    if args.apply:
        print("\nRestoring to 1 replica...")
        scale_replicas(args.deployment, args.namespace, 1, apply=True)

    print_summary(run_dir)
    print("Compare throughput across replica counts to determine scaling factor.")


if __name__ == "__main__":
    main()
