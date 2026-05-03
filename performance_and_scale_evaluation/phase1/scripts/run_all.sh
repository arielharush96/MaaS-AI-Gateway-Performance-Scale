#!/bin/bash
# MaaS Performance & Scale Evaluation — Phase 1 Orchestrator
#
# Usage:
#   ./run_all.sh                # Run evaluations 01-05 (safe, no cluster changes)
#   ./run_all.sh 01             # Run 01 only (baseline direct)
#   ./run_all.sh 02             # Run 02 only (single provider ramp)
#   ./run_all.sh 03             # Run 03 only (multi-provider)
#   ./run_all.sh 04             # Run 04 only (payload size)
#   ./run_all.sh 05             # Run 05 only (translation overhead)
#   ./run_all.sh 06             # Run 06 only (plugin chain latency — needs Prometheus)
#   ./run_all.sh 07             # Run 07 only (HPA — scales replicas, needs --apply)
#   ./run_all.sh analyze        # Analyze all existing results
#
# Environment:
#   MAX_SECONDS=120     Override benchmark duration (default: 120s)
#   PROMETHEUS_URL=...  Override Prometheus endpoint for eval 06

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EVAL="${1:-safe}"
MAX_SECONDS="${MAX_SECONDS:-120}"

echo "============================================"
echo " MaaS Performance Evaluation — Phase 1"
echo "============================================"
echo " Mode:        ${EVAL}"
echo " Max seconds: ${MAX_SECONDS} per benchmark"
echo " Started:     $(date -Iseconds)"
echo "============================================"

run_eval() {
    local num="$1"
    local name="$2"
    local script="$3"
    shift 3
    echo ""
    echo ">>> [$num] $name"
    python3 "${SCRIPT_DIR}/${script}" --max-seconds "${MAX_SECONDS}" "$@"
}

case "$EVAL" in
    01) run_eval 01 "Baseline: Direct to Provider" "01_baseline_direct.py" ;;
    02) run_eval 02 "Single Provider Ramp Concurrency" "02_gateway_single_provider.py" ;;
    03) run_eval 03 "Multi-Provider Through Gateway" "03_gateway_multi_provider.py" ;;
    04) run_eval 04 "Payload Size Variations" "04_payload_size.py" ;;
    05) run_eval 05 "Translation Overhead Comparison" "05_translation_overhead.py" ;;
    06) run_eval 06 "Plugin Chain Latency Breakdown" "06_plugin_chain_latency.py" ;;
    07) run_eval 07 "HPA Scaling Factor" "07_hpa_scaling.py" ;;
    safe|all)
        run_eval 01 "Baseline: Direct to Provider" "01_baseline_direct.py"
        run_eval 02 "Single Provider Ramp Concurrency" "02_gateway_single_provider.py"
        run_eval 03 "Multi-Provider Through Gateway" "03_gateway_multi_provider.py"
        run_eval 04 "Payload Size Variations" "04_payload_size.py"
        run_eval 05 "Translation Overhead Comparison" "05_translation_overhead.py"
        if [[ "$EVAL" == "all" ]]; then
            run_eval 06 "Plugin Chain Latency Breakdown" "06_plugin_chain_latency.py"
            run_eval 07 "HPA Scaling Factor" "07_hpa_scaling.py"
        fi
        echo ""
        echo ">>> Analyzing Results"
        python3 "${SCRIPT_DIR}/analyze_results.py"
        ;;
    analyze)
        python3 "${SCRIPT_DIR}/analyze_results.py"
        ;;
    *)
        echo "Unknown evaluation: $EVAL"
        echo "Usage: $0 [01|02|03|04|05|06|07|safe|all|analyze]"
        exit 1
        ;;
esac

echo ""
echo "============================================"
echo " Done: $(date -Iseconds)"
echo " Results: ${SCRIPT_DIR}/../results/"
echo "============================================"
