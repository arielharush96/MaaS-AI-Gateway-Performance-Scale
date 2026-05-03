#!/bin/bash
# ============================================================================
# MaaS AI Gateway — Phase 1 Performance Pipeline (OpenAI)
# ============================================================================
#
# Runs all 4 test plan stages as K8s Jobs inside the cluster:
#
#   Stage 1 (AC1): Baseline — Direct to simulator (no gateway)
#   Stage 2 (AC2): Gateway overhead — Through gateway (single replica)
#   Stage 3 (AC3): HPA scaling — Gateway at 1/2/4/8 replicas
#   Stage 4 (AC4): Per-plugin latency — Prometheus queries
#
# Result extraction: 3-method fallback (oc cp → Python tar → individual files)
# Pod lifecycle: pods stay alive after benchmarks for safe extraction.
#
# Results structure:
#   results/<timestamp>/
#     ├── config/            Manifests + parameters snapshot
#     ├── stage1-baseline/   Per-benchmark dirs (benchmarks.json, metrics.json,
#     │                      summary.txt, guidellm.log) + summary.csv
#     ├── stage2-gateway-overhead/
#     ├── stage3-hpa-scaling/replicas-{1,2,4,8}/
#     ├── stage4-plugin-latency/
#     └── report/            CSV + plots
#
# Usage:
#   ./run_pipeline.sh                  # Run all stages
#   ./run_pipeline.sh stage1           # Baseline only
#   ./run_pipeline.sh stage2 stage3    # Multiple stages
#   ./run_pipeline.sh test             # Quick validation (1 benchmark, 15s)
#   ./run_pipeline.sh analyze-latest   # Re-run analysis on latest results
#
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MANIFESTS_DIR="${SCRIPT_DIR}/manifests"
SCRIPTS_DIR="${SCRIPT_DIR}/scripts"
TIMESTAMP="$(date +%Y-%m-%d_%H-%M-%S)"
RESULTS_DIR="${SCRIPT_DIR}/results/${TIMESTAMP}"

NAMESPACE="${NAMESPACE:-openshift-ingress}"
BBR_DEPLOYMENT="${BBR_DEPLOYMENT:-payload-processing}"
PROMETHEUS_URL="${PROMETHEUS_URL:-https://prometheus-k8s.apps.ariel-perf-xeon6.ibm.rhperfscale.org}"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log()  { echo -e "${GREEN}[$(date +%H:%M:%S)]${NC} $*"; }
warn() { echo -e "${YELLOW}[$(date +%H:%M:%S)] WARNING:${NC} $*"; }
err()  { echo -e "${RED}[$(date +%H:%M:%S)] ERROR:${NC} $*" >&2; }
hdr()  {
    echo ""
    echo -e "${CYAN}${BOLD}════════════════════════════════════════════════════════════${NC}"
    echo -e "${CYAN}${BOLD}  $*${NC}"
    echo -e "${CYAN}${BOLD}════════════════════════════════════════════════════════════${NC}"
    echo ""
}

# ============================================================================
# Results directory setup
# ============================================================================

setup_results_dir() {
    mkdir -p "$RESULTS_DIR/config"

    for f in "$MANIFESTS_DIR"/*.yaml; do
        cp "$f" "$RESULTS_DIR/config/" 2>/dev/null || true
    done

    cat > "$RESULTS_DIR/config/pipeline-params.json" <<PARAMS
{
    "timestamp": "$TIMESTAMP",
    "namespace": "$NAMESPACE",
    "bbr_deployment": "$BBR_DEPLOYMENT",
    "prometheus_url": "$PROMETHEUS_URL",
    "concurrency_levels": [2, 4, 8, 16, 32, 64, 128, 256, 512, 1024],
    "payloads": {
        "small": {"prompt_tokens": 32, "output_tokens": 64},
        "medium": {"prompt_tokens": 256, "output_tokens": 512},
        "large": {"prompt_tokens": 1024, "output_tokens": 1024},
        "very-large": {"prompt_tokens": 2048, "output_tokens": 2048}
    },
    "multi_turn_depths": [1, 10, 50, 100],
    "benchmark_duration_total_sec": 150,
    "warmup_fraction": 0.2,
    "warmup_sec": 30,
    "measurement_sec": 120,
    "mode": "non-streaming",
    "model": "gpt-4o-openai",
    "guidellm_image": "ghcr.io/vllm-project/guidellm:v0.6.0",
    "hpa_replica_counts": [2, 4, 8],
    "hpa_payloads": ["medium", "large", "very-large"]
}
PARAMS

    log "Results directory: $RESULTS_DIR"
    log "Manifests snapshot saved to $RESULTS_DIR/config/"
}

# ============================================================================
# Helpers
# ============================================================================

ensure_token() {
    log "Creating SA token (4h TTL)..."
    local token
    token=$(oc create token default \
        --audience=maas-default-gateway-sa \
        -n "$NAMESPACE" \
        --duration=240m)
    oc delete secret guidellm-token -n "$NAMESPACE" --ignore-not-found >/dev/null
    oc create secret generic guidellm-token \
        --from-literal=token="$token" \
        -n "$NAMESPACE" >/dev/null
    log "Token secret created."
}

cleanup_job() {
    local job_name="$1"
    oc delete job "$job_name" -n "$NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
}

cleanup_configmap() {
    local cm_name="$1"
    oc delete configmap "$cm_name" -n "$NAMESPACE" --ignore-not-found >/dev/null 2>&1 || true
}

apply_manifest() {
    local manifest="$1"
    log "Applying $(basename "$manifest")..."
    oc apply -f "$manifest"
}

# ============================================================================
# Wait for RESULTS_READY marker in pod logs (pod stays alive after benchmarks)
# ============================================================================

wait_for_results_ready() {
    local job_name="$1"

    log "Waiting for pod of job/$job_name to be ready..."
    local pod=""
    while [ -z "$pod" ]; do
        pod=$(oc get pods -n "$NAMESPACE" \
            -l "job-name=$job_name" \
            -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || true
        if [ -z "$pod" ]; then
            oc wait --for=condition=Ready pod -l "job-name=$job_name" \
                -n "$NAMESPACE" --timeout=60s 2>/dev/null || true
        fi
    done
    log "Pod: $pod"

    log "Polling logs for RESULTS_READY marker..."
    while true; do
        if oc logs "job/$job_name" -n "$NAMESPACE" 2>/dev/null | grep -q 'RESULTS_READY'; then
            log "RESULTS_READY signal received!"
            return 0
        fi

        # Check if pod failed
        local phase
        phase=$(oc get pods -n "$NAMESPACE" -l "job-name=$job_name" \
            -o jsonpath='{.items[0].status.phase}' 2>/dev/null) || true
        if [ "$phase" = "Failed" ]; then
            err "Job pod failed!"
            return 1
        fi

        log "  Benchmarks still running... (checking every 60s)"
        oc wait --for=condition=complete "job/$job_name" \
            -n "$NAMESPACE" --timeout=60s 2>/dev/null || true
    done
}

# ============================================================================
# 3-method result extraction (pod must be Running)
# ============================================================================

extract_results() {
    local job_name="$1"
    local dest_dir="$2"
    mkdir -p "$dest_dir"

    local pod
    pod=$(oc get pods -n "$NAMESPACE" \
        -l "job-name=$job_name" \
        -o jsonpath='{.items[0].metadata.name}' 2>/dev/null) || true

    if [ -z "$pod" ]; then
        err "No pod found for job/$job_name"
        oc logs "job/$job_name" -n "$NAMESPACE" > "$dest_dir/job-output.log" 2>/dev/null || true
        return 1
    fi

    log "Extracting from pod/$pod → $dest_dir ..."

    # --- Method 1: oc cp (needs tar in container) ---
    if oc cp "$NAMESPACE/$pod:/results/." "$dest_dir/" 2>/dev/null; then
        local count
        count=$(find "$dest_dir" -name 'benchmarks.json' 2>/dev/null | wc -l | tr -d ' ')
        if [ "$count" -gt 0 ]; then
            log "Method 1 (oc cp): $count benchmark(s) extracted."
            oc logs "job/$job_name" -n "$NAMESPACE" > "$dest_dir/job-output.log" 2>/dev/null || true
            return 0
        fi
    fi
    warn "Method 1 (oc cp) failed — trying Python tar..."

    # --- Method 2: Python tarfile via oc exec (no tar binary needed) ---
    local tar_tmp="$dest_dir/_results.tar.gz"
    if oc exec "$pod" -n "$NAMESPACE" -- python3 -c "
import tarfile, sys, os
with tarfile.open(fileobj=sys.stdout.buffer, mode='w|gz') as tar:
    tar.add('/results', arcname='.')
" > "$tar_tmp" 2>/dev/null; then
        if [ -s "$tar_tmp" ]; then
            tar xzf "$tar_tmp" -C "$dest_dir/" 2>/dev/null && {
                rm -f "$tar_tmp"
                local count
                count=$(find "$dest_dir" -name 'benchmarks.json' 2>/dev/null | wc -l | tr -d ' ')
                log "Method 2 (Python tar): $count benchmark(s) extracted."
                oc logs "job/$job_name" -n "$NAMESPACE" > "$dest_dir/job-output.log" 2>/dev/null || true
                return 0
            }
        fi
    fi
    rm -f "$tar_tmp" 2>/dev/null
    warn "Method 2 (Python tar) failed — extracting files individually..."

    # --- Method 3: cat each file via oc exec ---
    local files
    files=$(oc exec "$pod" -n "$NAMESPACE" -- find /results -type f 2>/dev/null) || true
    local extracted=0
    for f in $files; do
        local rel="${f#/results/}"
        local dest_file="$dest_dir/$rel"
        mkdir -p "$(dirname "$dest_file")"
        if oc exec "$pod" -n "$NAMESPACE" -- cat "$f" > "$dest_file" 2>/dev/null; then
            extracted=$((extracted + 1))
        fi
    done
    log "Method 3 (individual files): $extracted file(s) extracted."

    # Always save full logs as backup
    oc logs "job/$job_name" -n "$NAMESPACE" > "$dest_dir/job-output.log" 2>/dev/null || true
    log "Logs saved to $dest_dir/job-output.log"
}

# ============================================================================
# Combined: apply → wait → extract
# ============================================================================

run_job() {
    local manifest="$1"
    local job_name="$2"
    local configmap_name="$3"
    local dest_dir="$4"

    cleanup_job "$job_name"
    cleanup_configmap "$configmap_name"
    apply_manifest "$manifest"
    wait_for_results_ready "$job_name"
    extract_results "$job_name" "$dest_dir"

    local json_count csv_count txt_count
    json_count=$(find "$dest_dir" -name '*.json' 2>/dev/null | wc -l | tr -d ' ')
    csv_count=$(find "$dest_dir" -name '*.csv' 2>/dev/null | wc -l | tr -d ' ')
    txt_count=$(find "$dest_dir" -name '*.txt' 2>/dev/null | wc -l | tr -d ' ')
    log "Extraction complete: $json_count JSON, $csv_count CSV, $txt_count TXT"
    log "Pod remains alive. Clean up with: oc delete job $job_name -n $NAMESPACE"
}

# ============================================================================
# Stage 1 (AC1): Baseline — Direct to Simulator
# ============================================================================

run_stage1() {
    hdr "Stage 1 (AC1): Baseline — Direct to Simulator"
    echo "  Benchmarks: 80 (4 payloads × 10 levels + 4 depths × 10 levels)"
    echo "  Per-bench:  150s (30s warmup + 120s measurement)"
    echo "  Estimated:  ~3.5 hours"
    echo ""

    local dest="$RESULTS_DIR/stage1-baseline"

    run_job \
        "$MANIFESTS_DIR/stage1-baseline.yaml" \
        "guidellm-stage1-baseline" \
        "stage1-baseline-script" \
        "$dest"

    local count
    count=$(find "$dest" -name 'benchmarks.json' 2>/dev/null | wc -l | tr -d ' ')
    log "Stage 1 complete: $count benchmarks in $dest"
}

# ============================================================================
# Stage 2 (AC2): Gateway Overhead — Single Replica
# ============================================================================

run_stage2() {
    hdr "Stage 2 (AC2): Gateway Overhead (Single Replica)"
    echo "  Benchmarks: 80 (identical matrix to Stage 1)"
    echo "  Per-bench:  150s (30s warmup + 120s measurement)"
    echo "  Estimated:  ~3.5 hours"
    echo ""

    ensure_token
    local dest="$RESULTS_DIR/stage2-gateway-overhead"

    run_job \
        "$MANIFESTS_DIR/stage2-gateway-overhead.yaml" \
        "guidellm-stage2-gateway" \
        "stage2-gateway-script" \
        "$dest"

    local count
    count=$(find "$dest" -name 'benchmarks.json' 2>/dev/null | wc -l | tr -d ' ')
    log "Stage 2 complete: $count benchmarks in $dest"
}

# ============================================================================
# Stage 3 (AC3): HPA Scaling
# ============================================================================

run_stage3() {
    hdr "Stage 3 (AC3): HPA Scaling (2→4→8 replicas; replica=1 from Stage 2)"
    echo "  Per replica: 30 benchmarks (3 payloads × 10 levels)"
    echo "  Total:       90 benchmarks across 3 replica counts"
    echo "  Note:        replica=1 baseline reused from Stage 2"
    echo "  Per-bench:   150s (30s warmup + 120s measurement)"
    echo "  Estimated:   ~3.75 hours"
    echo ""

    ensure_token

    # Apply the ConfigMap once (shared across all replica runs)
    cleanup_configmap "stage3-hpa-script"
    oc apply -f <(awk '/^---/{found++} found<2{print}' "$MANIFESTS_DIR/stage3-hpa-benchmark.yaml")

    for REPLICAS in 2 4 8; do
        local job_name="guidellm-stage3-hpa-r${REPLICAS}"
        local dest="$RESULTS_DIR/stage3-hpa-scaling/replicas-${REPLICAS}"

        echo ""
        log "──── Scaling to $REPLICAS replica(s) ────"
        oc scale "deployment/$BBR_DEPLOYMENT" --replicas="$REPLICAS" -n "$NAMESPACE"
        oc rollout status "deployment/$BBR_DEPLOYMENT" -n "$NAMESPACE" --timeout=180s
        log "Rollout complete: $REPLICAS replica(s) ready."

        cleanup_job "$job_name"
        oc apply -f <(
            awk '/^---/{found++} found>=2{print}' "$MANIFESTS_DIR/stage3-hpa-benchmark.yaml" \
            | sed "s/REPLICA_COUNT/$REPLICAS/g"
        )

        wait_for_results_ready "$job_name"
        extract_results "$job_name" "$dest"

        local count
        count=$(find "$dest" -name 'benchmarks.json' 2>/dev/null | wc -l | tr -d ' ')
        log "Replicas=$REPLICAS complete: $count benchmarks"
    done

    log "Restoring to 1 replica..."
    oc scale "deployment/$BBR_DEPLOYMENT" --replicas=1 -n "$NAMESPACE"
    oc rollout status "deployment/$BBR_DEPLOYMENT" -n "$NAMESPACE" --timeout=120s
    log "Restored."
}

# ============================================================================
# Stage 4 (AC4): Per-Plugin Latency Breakdown
# ============================================================================

run_stage4() {
    hdr "Stage 4 (AC4): Per-Plugin Latency Breakdown"
    local dest="$RESULTS_DIR/stage4-plugin-latency"
    mkdir -p "$dest"

    local prom="$PROMETHEUS_URL"
    local token
    token=$(oc whoami -t 2>/dev/null || echo "")

    local auth_header=""
    [ -n "$token" ] && auth_header="Authorization: Bearer $token"

    PLUGINS=(
        "body-field-to-header"
        "model-provider-resolver"
        "api-translation"
        "apikey-injection"
    )

    log "Querying Prometheus at $prom..."
    echo ""
    printf "%-30s %-12s %-12s %-12s\n" "Plugin" "p50 (ms)" "p95 (ms)" "p99 (ms)"
    printf '%0.s─' {1..66}; echo

    local json_output="{"
    local first=true

    for plugin in "${PLUGINS[@]}"; do
        local p50="" p95="" p99=""

        for quantile in 0.50 0.95 0.99; do
            local query="histogram_quantile(${quantile}, rate(bbr_plugin_duration_seconds_bucket{plugin_name=\"${plugin}\"}[5m]))"
            local encoded_query
            encoded_query=$(python3 -c "import urllib.parse; print(urllib.parse.quote('$query'))" 2>/dev/null || echo "$query")

            local result
            if [ -n "$auth_header" ]; then
                result=$(curl -sk -H "$auth_header" \
                    "${prom}/api/v1/query?query=${encoded_query}" 2>/dev/null) || true
            else
                result=$(curl -sk \
                    "${prom}/api/v1/query?query=${encoded_query}" 2>/dev/null) || true
            fi

            local val
            val=$(echo "$result" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    v = float(d['data']['result'][0]['value'][1])
    print(f'{v*1000:.3f}')
except:
    print('N/A')
" 2>/dev/null || echo "N/A")

            case "$quantile" in
                0.50) p50="$val" ;;
                0.95) p95="$val" ;;
                0.99) p99="$val" ;;
            esac
        done

        printf "%-30s %-12s %-12s %-12s\n" "$plugin" "$p50" "$p95" "$p99"

        $first || json_output+=","
        first=false
        json_output+="\"$plugin\":{\"p50_ms\":\"$p50\",\"p95_ms\":\"$p95\",\"p99_ms\":\"$p99\"}"
    done

    json_output+="}"
    echo "$json_output" | python3 -m json.tool > "$dest/plugin_latency_breakdown.json" 2>/dev/null \
        || echo "$json_output" > "$dest/plugin_latency_breakdown.json"

    echo ""
    log "Plugin latency saved to $dest/plugin_latency_breakdown.json"
}

# ============================================================================
# Quick Test — 1 benchmark, 15s, validates full pipeline
# ============================================================================

run_test() {
    hdr "Quick Validation Test (1 benchmark, 15s)"

    local test_job="guidellm-test-quick"
    local test_cm="test-quick-script"
    local dest="$RESULTS_DIR/test-quick"

    cleanup_job "$test_job"
    cleanup_configmap "$test_cm"

    log "Applying test manifest..."
    oc apply -f "$MANIFESTS_DIR/stage-test-quick.yaml"

    wait_for_results_ready "$test_job"
    extract_results "$test_job" "$dest"

    # Validate outputs
    local errors=0
    echo ""
    log "Validating extracted files..."

    for expected in benchmarks.json metrics.json summary.txt guidellm.log; do
        local found
        found=$(find "$dest" -name "$expected" 2>/dev/null | head -1)
        if [ -n "$found" ]; then
            local size
            size=$(wc -c < "$found" | tr -d ' ')
            echo -e "  ${GREEN}✓${NC} $expected ($size bytes)"
        else
            echo -e "  ${RED}✗${NC} $expected — MISSING"
            errors=$((errors + 1))
        fi
    done

    local csv_found
    csv_found=$(find "$dest" -name "summary.csv" 2>/dev/null | head -1)
    if [ -n "$csv_found" ]; then
        local rows
        rows=$(wc -l < "$csv_found" | tr -d ' ')
        echo -e "  ${GREEN}✓${NC} summary.csv ($rows lines)"
    else
        echo -e "  ${RED}✗${NC} summary.csv — MISSING"
        errors=$((errors + 1))
    fi

    echo ""
    if [ "$errors" -eq 0 ]; then
        log "ALL CHECKS PASSED — pipeline is ready for full run."
    else
        err "$errors check(s) FAILED. Fix issues before running full benchmarks."
        return 1
    fi

    cleanup_job "$test_job"
    cleanup_configmap "$test_cm"
    log "Test resources cleaned up."
}

# ============================================================================
# Analysis — run locally after data extraction
# ============================================================================

run_analysis() {
    local target_dir="${1:-$RESULTS_DIR}"

    if [ "$target_dir" = "latest" ]; then
        target_dir=$(ls -td "$SCRIPT_DIR"/results/*/ 2>/dev/null | head -1)
        if [ -z "$target_dir" ]; then
            err "No results directories found."
            return 1
        fi
    fi

    hdr "Generating Analysis & Plots"
    echo "  Source: $target_dir"
    echo ""

    if ! python3 -c "import matplotlib" 2>/dev/null; then
        warn "matplotlib not installed. Install with: pip install matplotlib"
        warn "Generating CSV only..."
    fi

    python3 "$SCRIPTS_DIR/analyze_and_plot.py" "$target_dir"
}

# ============================================================================
# Main
# ============================================================================

main() {
    hdr "MaaS AI Gateway — Phase 1 Performance Pipeline"
    echo "  Cluster:    $(oc whoami --show-server 2>/dev/null || echo 'unknown')"
    echo "  Namespace:  $NAMESPACE"
    echo "  Results:    $RESULTS_DIR"
    echo "  Timestamp:  $TIMESTAMP"
    echo ""
    echo "  Test matrix:"
    echo "    Concurrency:  2, 4, 8, 16, 32, 64, 128, 256, 512, 1024"
    echo "    Payloads:     small (32/64), medium (256/512), large (1024/1024), very-large (2048/2048)"
    echo "    Multi-turn:   1, 10, 50, 100 turns"
    echo "    Per-bench:    150s (30s warmup + 120s measurement)"
    echo "    Mode:         NON-STREAMING"
    echo ""

    local stages=("$@")
    if [ ${#stages[@]} -eq 0 ]; then
        stages=(stage1 stage2 stage3 stage4 analyze)
    fi

    setup_results_dir

    local start_time=$SECONDS

    for stage in "${stages[@]}"; do
        case "$stage" in
            stage1|1|ac1|baseline)
                run_stage1 ;;
            stage2|2|ac2|gateway)
                run_stage2 ;;
            stage3|3|ac3|hpa)
                run_stage3 ;;
            stage4|4|ac4|plugin|prometheus)
                run_stage4 ;;
            test|validate|quick-test)
                run_test ;;
            analyze|report|plots)
                run_analysis "$RESULTS_DIR" ;;
            analyze-latest)
                run_analysis "latest" ;;
            *)
                err "Unknown stage: $stage"
                echo "Valid: stage1, stage2, stage3, stage4, test, analyze, analyze-latest"
                exit 1 ;;
        esac
    done

    local elapsed=$(( SECONDS - start_time ))
    local hours=$(( elapsed / 3600 ))
    local minutes=$(( (elapsed % 3600) / 60 ))
    local seconds=$(( elapsed % 60 ))

    hdr "Pipeline Complete"
    echo "  Duration:    ${hours}h ${minutes}m ${seconds}s"
    echo "  Results:     $RESULTS_DIR"
    echo ""
    echo "  Stage results:"
    for d in "$RESULTS_DIR"/stage*/; do
        [ -d "$d" ] || continue
        local count
        count=$(find "$d" -name 'benchmarks.json' 2>/dev/null | wc -l | tr -d ' ')
        printf "    %-35s %s benchmark(s)\n" "$(basename "$d")/" "$count"
    done
    if [ -d "$RESULTS_DIR/report" ]; then
        local plots
        plots=$(find "$RESULTS_DIR/report" -name '*.png' 2>/dev/null | wc -l | tr -d ' ')
        echo ""
        echo "  Report:  $RESULTS_DIR/report/"
        echo "  CSV:     $RESULTS_DIR/report/summary.csv"
        echo "  Plots:   $plots files"
    fi
    echo ""
}

main "$@"
