#!/usr/bin/env python3
"""Extract benchmark JSON files from job-output.log.

The GuideLLM job script appends all JSON results at the end:
    === RESULTS JSON ===
    --- /results/payload-small-c2/benchmarks.json ---
    { ... }
    --- /results/payload-medium-c4/benchmarks.json ---
    { ... }

This script parses them out and writes each to the correct subdirectory.
"""

import sys
import os
import json
import re

def main():
    if len(sys.argv) < 2:
        print("Usage: python extract_json_from_log.py <stage-dir>")
        print("  e.g.: python extract_json_from_log.py results/2026-04-28_15-45-25/stage1-baseline")
        sys.exit(1)

    stage_dir = sys.argv[1]
    log_path = os.path.join(stage_dir, "job-output.log")

    if not os.path.exists(log_path):
        print(f"ERROR: {log_path} not found")
        sys.exit(1)

    print(f"Reading {log_path} ({os.path.getsize(log_path) / 1e6:.0f} MB)...")

    in_json_section = False
    current_name = None
    current_json = []
    extracted = 0

    marker_re = re.compile(r'^--- /results/(.+)/benchmarks\.json ---$')

    with open(log_path, 'r', errors='replace') as f:
        for line in f:
            line = line.rstrip('\n')

            if line == "=== RESULTS JSON ===":
                in_json_section = True
                continue

            if not in_json_section:
                continue

            m = marker_re.match(line)
            if m:
                if current_name and current_json:
                    _write_json(stage_dir, current_name, current_json)
                    extracted += 1
                current_name = m.group(1)
                current_json = []
            elif current_name is not None:
                current_json.append(line)

    if current_name and current_json:
        _write_json(stage_dir, current_name, current_json)
        extracted += 1

    print(f"Extracted {extracted} benchmark JSON files to {stage_dir}/")


def _write_json(stage_dir, name, lines):
    raw = '\n'.join(lines).strip()
    if not raw:
        return

    out_dir = os.path.join(stage_dir, name)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "benchmarks.json")

    try:
        parsed = json.loads(raw)
        with open(out_path, 'w') as f:
            json.dump(parsed, f)
        print(f"  OK  {name}")
    except json.JSONDecodeError as e:
        with open(out_path, 'w') as f:
            f.write(raw)
        print(f"  WARN {name} (invalid JSON: {e})")


if __name__ == "__main__":
    main()
