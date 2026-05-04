#!/bin/bash
#
# Experiment 3: HPA Scaling Test
# Runs inside the GuideLLM benchmark pod. Requires the pod's ServiceAccount
# to have permission to scale deployments (patched externally before running).
#
# The orchestrator (user or external script) sets REPLICA_COUNT env var before
# starting this script, or calls it with: run_hpa.sh <replica_count> <concurrency> <turns>

SIMULATOR="http://llm-d-inference-sim.openshift-ingress.svc.cluster.local:8000"
GATEWAY="http://maas-default-gateway-data-science-gateway-class.openshift-ingress.svc.cluster.local:80"
METRICS_URL="http://payload-processing.openshift-ingress.svc.cluster.local:9090/metrics"
export OPENAI_API_KEY=$(cat /var/run/secrets/guidellm/token)
RESULTS="/results"
PROCESSOR="gpt2"
BACKEND_KWARGS='{"stream": false, "validate_backend": false}'
MAX_SEC=180
BENCH_TIMEOUT=300
WARMUP=60
COOLDOWN=0.0

REPLICA_COUNT="${1:-1}"
TARGET_CONCURRENCY="${2:-128}"
TARGET_TURNS="${3:-50}"

RUN_TAG="hpa-r${REPLICA_COUNT}-c${TARGET_CONCURRENCY}-t${TARGET_TURNS}"
CSV="$RESULTS/hpa_summary.csv"
PLUGIN_CSV="$RESULTS/hpa_plugin_latency.csv"

if [ ! -f "$CSV" ]; then
    echo "replicas,provider,benchmark,mode,prompt_tokens,output_tokens,turns,concurrency,successful,errored,rps,mean_ms,p50_ms,p95_ms,p99_ms,duration_s" > "$CSV"
fi

TOTAL=0; PASS=0; FAIL=0

echo "============================================"
echo " Experiment 3: HPA Scaling Test"
echo " Replicas: $REPLICA_COUNT"
echo " Concurrency: $TARGET_CONCURRENCY"
echo " Turns: $TARGET_TURNS"
echo " Started: $(date -Iseconds)"
echo "============================================"
echo ""

python3 /scripts/prom_monitor.py &
MONITOR_PID=$!

run_bench() {
    local provider_label="$1" model="$2" name="$3" target="$4" mode="$5" pt="$6" ot="$7" concurrency="$8" turns="${9:-}"
    local dir="$RESULTS/${provider_label}/${name}/${mode}"
    mkdir -p "$dir"
    TOTAL=$((TOTAL + 1))

    local data_arg="prompt_tokens=${pt},output_tokens=${ot}"
    [ -n "$turns" ] && data_arg="${data_arg},turns=${turns}"

    timeout "${BENCH_TIMEOUT}" guidellm benchmark run \
        --target "$target" \
        --model "$model" \
        --processor "$PROCESSOR" \
        --data "$data_arg" \
        --profile concurrent \
        --rate "$concurrency" \
        --max-seconds "$MAX_SEC" \
        --warmup "$WARMUP" \
        --cooldown "$COOLDOWN" \
        --backend-kwargs "$BACKEND_KWARGS" \
        --output-dir "$dir" \
        --outputs json \
        --disable-progress \
        > "$dir/guidellm.log" 2>&1 || true

    if [ -f "$dir/benchmarks.json" ]; then
        python3 /scripts/parse_hpa.py "$dir" "${provider_label}/${name}" "$mode" "$pt" "$ot" "$concurrency" "${turns:-0}" "$CSV" "$REPLICA_COUNT" || \
            echo "  WARN: parse failed"
        PASS=$((PASS + 1))
    else
        echo "  FAILED ($mode) - see $dir/guidellm.log"
        tail -3 "$dir/guidellm.log" 2>/dev/null || true
        FAIL=$((FAIL + 1))
    fi
}

scrape_metrics() {
    local outfile="$1"
    python3 -c "import urllib.request,sys; urllib.request.urlretrieve(sys.argv[1], sys.argv[2])" "$METRICS_URL" "$outfile" 2>/dev/null || true
}

run_ab() {
    local provider_label="$1" model="$2" name="$3" pt="$4" ot="$5" concurrency="$6" turns="${7:-}"
    local metrics_dir="$RESULTS/${provider_label}/${name}/plugin_metrics"
    mkdir -p "$metrics_dir"

    echo "${provider_label}/${name}" > /results/.current_benchmark

    echo ""
    echo ">>> [${provider_label}/${name}] pt=$pt ot=$ot c=$concurrency${turns:+ turns=$turns} replicas=$REPLICA_COUNT"

    run_bench "$provider_label" "$model" "$name" "$SIMULATOR" "baseline" "$pt" "$ot" "$concurrency" "$turns"

    scrape_metrics "$metrics_dir/before.prom"
    run_bench "$provider_label" "$model" "$name" "${GATEWAY}/${model}" "gateway" "$pt" "$ot" "$concurrency" "$turns"
    scrape_metrics "$metrics_dir/after.prom"
    if [ -s "$metrics_dir/before.prom" ] && [ -s "$metrics_dir/after.prom" ]; then
        python3 /scripts/plugin_delta.py "$metrics_dir/before.prom" "$metrics_dir/after.prom" "$PLUGIN_CSV" "${provider_label}/${name}" || \
            echo "  WARN: plugin_delta.py failed"
        rm -f "$metrics_dir/before.prom" "$metrics_dir/after.prom"
    fi
}

HPA_PROVIDERS=("claude-sonnet-anthropic" "claude-sonnet-vertex")

echo "=== Multi-turn ${TARGET_TURNS} at c=${TARGET_CONCURRENCY}, replicas=${REPLICA_COUNT} ==="
for model in "${HPA_PROVIDERS[@]}"; do
    run_ab "$model" "$model" "${RUN_TAG}" 64 128 "$TARGET_CONCURRENCY" "$TARGET_TURNS"
done

echo ""
echo "=== Single-turn high-concurrency at replicas=${REPLICA_COUNT} ==="
for model in "${HPA_PROVIDERS[@]}"; do
    run_ab "$model" "$model" "hpa-r${REPLICA_COUNT}-single-c${TARGET_CONCURRENCY}" 32 64 "$TARGET_CONCURRENCY"
done

echo "" > /results/.current_benchmark
touch /results/.monitor_stop
wait "$MONITOR_PID" 2>/dev/null || true

echo ""
echo "============================================"
echo " HPA TEST COMPLETE (replicas=$REPLICA_COUNT)"
echo " Total: $TOTAL  Passed: $PASS  Failed: $FAIL"
echo " Finished: $(date -Iseconds)"
echo "============================================"
echo ""
echo "=== HPA CSV DUMP ==="
cat "$CSV"
echo "=== END HPA CSV DUMP ==="
echo ""
echo "=== RESULTS_READY ==="
tail -f /dev/null
