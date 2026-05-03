"""Shared utilities for MaaS performance evaluation scripts."""

import json
import os
import subprocess
import sys
import urllib.request
import urllib.parse
import yaml
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).parent
PHASE1_DIR = SCRIPTS_DIR.parent
RESULTS_DIR = PHASE1_DIR / "results"


def load_config() -> dict:
    config_path = SCRIPTS_DIR / "config.yaml"
    with open(config_path) as f:
        return yaml.safe_load(f)


def create_run_dir(evaluation_name: str) -> Path:
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir = RESULTS_DIR / evaluation_name / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def run_guidellm(
    target: str,
    model: str,
    profile: str,
    output_dir: Path,
    prompt_tokens: int = 256,
    output_tokens: int = 128,
    rate: str | None = None,
    max_seconds: int = 120,
    request_type: str = "chat_completions",
    warmup: float = 0.1,
    cooldown: float = 0.1,
    max_errors: int = 50,
    extra_args: list[str] | None = None,
    backend_kwargs: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run a single GuideLLM benchmark and return the result."""
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "guidellm", "benchmark", "run",
        "--target", target,
        "--model", model,
        "--request-type", request_type,
        "--profile", profile,
        "--data", f"prompt_tokens={prompt_tokens},output_tokens={output_tokens}",
        "--max-seconds", str(max_seconds),
        "--warmup", str(warmup),
        "--cooldown", str(cooldown),
        "--max-errors", str(max_errors),
        "--output-dir", str(output_dir),
        "--outputs", "json,csv",
        "--sample-requests", "20",
    ]

    if rate:
        cmd.extend(["--rate", str(rate)])

    if backend_kwargs:
        cmd.extend(["--backend-kwargs", json.dumps(backend_kwargs)])

    if extra_args:
        cmd.extend(extra_args)

    print(f"\n{'='*80}")
    print(f"  target:  {target}")
    print(f"  model:   {model}")
    print(f"  profile: {profile}" + (f" rate={rate}" if rate else ""))
    print(f"  payload: {prompt_tokens} prompt / {output_tokens} output tokens")
    print(f"  output:  {output_dir}")
    print(f"{'='*80}\n")

    result = subprocess.run(cmd, capture_output=False)

    if result.returncode != 0:
        print(f"WARNING: guidellm exited with code {result.returncode}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# Prometheus helpers (for plugin chain latency)
# ---------------------------------------------------------------------------

def query_prometheus(prometheus_url: str, query: str) -> dict:
    """Execute an instant PromQL query and return the parsed result."""
    url = f"{prometheus_url}/api/v1/query?query={urllib.parse.quote(query)}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def query_prometheus_range(
    prometheus_url: str, query: str,
    start: str, end: str, step: str = "15s",
) -> dict:
    """Execute a range PromQL query."""
    params = urllib.parse.urlencode({
        "query": query, "start": start, "end": end, "step": step,
    })
    url = f"{prometheus_url}/api/v1/query_range?{params}"
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Config accessors
# ---------------------------------------------------------------------------

def get_gateway_url(config: dict) -> str:
    return config["gateway"]["url"]


def get_provider_url(config: dict, provider_name: str) -> str:
    return config["providers"][provider_name]["url"]


def get_models_by_group(config: dict, group: str) -> list[dict]:
    return [m for m in config["models"] if m["group"] == group]


def get_all_models(config: dict) -> list[dict]:
    return config["models"]


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def print_header(title: str):
    width = max(len(title) + 4, 60)
    print(f"\n{'#'*width}")
    print(f"# {title}")
    print(f"# {datetime.now().isoformat()}")
    print(f"{'#'*width}\n")


def print_summary(run_dir: Path):
    print(f"\n{'='*60}")
    print(f"Results saved to: {run_dir}")
    csv_files = list(run_dir.rglob("*.csv"))
    json_files = list(run_dir.rglob("*.json"))
    print(f"  CSV files:  {len(csv_files)}")
    print(f"  JSON files: {len(json_files)}")
    print(f"{'='*60}\n")


def save_metadata(run_dir: Path, metadata: dict):
    """Save run metadata (config snapshot, timestamps, etc.) to the run dir."""
    meta_path = run_dir / "metadata.json"
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2, default=str)
