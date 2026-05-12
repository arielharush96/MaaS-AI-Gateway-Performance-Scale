#!/bin/bash
#
# Experiment 2: Multi-Turn 100 Error Investigation
#
# turns=100, small payload (64/128), c=4 and c=8.
# Captures BBR logs before/after each run for error root-cause analysis.
# Preserves all error details from benchmarks.json for post-analysis.
#
# Timing: 30s warmup + 60s measurement = 90s per benchmark
# Matrix:  5 providers × 2 levels = 10 A/B pairs = 20 benchmarks
# Estimated runtime: ~30 minutes

SIMULATOR="http://llm-d-inference-sim.openshift-ingress.svc.cluster.local:8000"
GATEWAY="http://maas-default-gateway-data-science-gateway-class.openshift-ingress.svc.cluster.local:80"
METRICS_URL="http://payload-processing.openshift-ingress.svc.cluster.local:9090/metrics"
export OPENAI_API_KEY=$(cat /var/run/secrets/guidellm/token)
RESULTS="/results"
PROCESSOR="gpt2"
BACKEND_KWARGS='{"stream": false, "validate_backend": false}'
MAX_SEC=90
BENCH_TIMEOUT=150
WARMUP=30
COOLDOWN=0.0
CSV="$RESULTS/summary.csv"
PLUGIN_CSV="$RESULTS/plugin_latency.csv"
CSV_DUMP_EVERY=5

if [ ! -f "$CSV" ]; then
    echo "provider,benchmark,mode,prompt_tokens,output_tokens,turns,concurrency,successful,errored,rps,mean_ms,p50_ms,p95_ms,p99_ms,duration_s" > "$CSV"
fi

TOTAL=0; PASS=0; FAIL=0

echo "============================================"
echo " Experiment 2: Multi-Turn 100 Error Investigation"
echo "============================================"
echo " Payload:     64 prompt / 128 output tokens, turns=100"
echo " Concurrency: 4, 8"
echo " Providers:   all 5"
echo " Timing:      ${WARMUP}s warmup + $((MAX_SEC - WARMUP))s measurement"
echo " Total:       10 A/B pairs = 20 benchmarks"
echo ""
echo " Simulator: $SIMULATOR"
echo " Gateway:   $GATEWAY/<model>"
echo " Started:   $(date -Iseconds)"
echo "============================================"
echo ""

python3 /scripts/prom_monitor.py &
MONITOR_PID=$!
echo "[exp2] Prometheus monitor started (PID $MONITOR_PID)"

dump_csv() {
    echo ""
    echo "=== CSV_DUMP START ($(date -Iseconds)) ==="
    cat "$CSV"
    echo "=== CSV_DUMP END ==="
    echo ""
}

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
        python3 /scripts/parse.py "$dir" "${provider_label}/${name}" "$mode" "$pt" "$ot" "$concurrency" "${turns:-0}" "$CSV" || \
            echo "  WARN: parse failed"
        PASS=$((PASS + 1))
    else
        echo "  FAILED ($mode) - see $dir/guidellm.log"
        tail -3 "$dir/guidellm.log" 2>/dev/null || true
        FAIL=$((FAIL + 1))
    fi

    if (( TOTAL % CSV_DUMP_EVERY == 0 )); then
        dump_csv
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
    echo ">>> [${provider_label}/${name}] pt=$pt ot=$ot c=$concurrency${turns:+ turns=$turns}"

    run_bench "$provider_label" "$model" "$name" "$SIMULATOR" "baseline" "$pt" "$ot" "$concurrency" "$turns"

    scrape_metrics "$metrics_dir/before.prom"
    run_bench "$provider_label" "$model" "$name" "${GATEWAY}/${model}" "gateway" "$pt" "$ot" "$concurrency" "$turns"
    scrape_metrics "$metrics_dir/after.prom"
    if [ -s "$metrics_dir/before.prom" ] && [ -s "$metrics_dir/after.prom" ]; then
        echo "  --- Plugin latency (${provider_label}/${name}) ---"
        python3 /scripts/plugin_delta.py "$metrics_dir/before.prom" "$metrics_dir/after.prom" "$PLUGIN_CSV" "${provider_label}/${name}" || \
            echo "  WARN: plugin_delta.py failed"
        rm -f "$metrics_dir/before.prom" "$metrics_dir/after.prom"
    else
        echo "  WARN: metrics scrape empty"
    fi
}

capture_bbr_logs() {
    local label="$1" phase="$2"
    local log_dir="$RESULTS/bbr_logs"
    mkdir -p "$log_dir"
    local outfile="$log_dir/${label//\//_}_${phase}.log"
    python3 - "$outfile" <<'PYEOF'
import urllib.request, ssl, json, sys, os

sa_token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
namespace = "openshift-ingress"
api_base = "https://kubernetes.default.svc"
outfile = sys.argv[1]

if not os.path.exists(sa_token_path):
    print("  WARN: no SA token, skipping BBR log capture")
    sys.exit(0)

token = open(sa_token_path).read().strip()
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

try:
    url = f"{api_base}/api/v1/namespaces/{namespace}/pods?labelSelector=app=payload-processing"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    resp = urllib.request.urlopen(req, context=ctx, timeout=10)
    pods = json.loads(resp.read())
    pod_names = [p["metadata"]["name"] for p in pods.get("items", [])]
except Exception as e:
    print(f"  WARN: cannot list BBR pods: {e}")
    sys.exit(0)

with open(outfile, 'w') as f:
    for pod_name in pod_names:
        try:
            url = f"{api_base}/api/v1/namespaces/{namespace}/pods/{pod_name}/log?container=bbr&tailLines=2000"
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            resp = urllib.request.urlopen(req, context=ctx, timeout=15)
            f.write(f"=== {pod_name} ===\n")
            f.write(resp.read().decode('utf-8', errors='replace'))
            f.write("\n")
        except Exception as e:
            f.write(f"=== {pod_name} === ERROR: {e}\n")
print(f"  BBR logs ({phase}): {outfile}")
PYEOF
}

MT_LEVELS=(4 8)
ALL_PROVIDERS=("gpt-4o-openai" "gpt-4o-azure" "gpt-4o-bedrock" "claude-sonnet-anthropic" "claude-sonnet-vertex")

for model in "${ALL_PROVIDERS[@]}"; do
    echo ""
    echo "###################################################"
    echo " Provider: $model (turns=100)"
    echo "###################################################"
    for C in "${MT_LEVELS[@]}"; do
        capture_bbr_logs "${model}/multiturn-100-c${C}" "before"
        run_ab "$model" "$model" "multiturn-100-c${C}" 64 128 "$C" 100
        capture_bbr_logs "${model}/multiturn-100-c${C}" "after"
    done
    echo ">>> $model complete"
    dump_csv
done

echo "" > /results/.current_benchmark
touch /results/.monitor_stop
wait "$MONITOR_PID" 2>/dev/null || true

echo ""
echo "============================================"
echo " EXPERIMENT 2 COMPLETE"
echo " Total: $TOTAL  Passed: $PASS  Failed: $FAIL"
echo " Finished: $(date -Iseconds)"
echo "============================================"
echo ""
echo "=== FINAL CSV ==="
cat "$CSV"
echo "=== END CSV ==="
echo ""
echo "=== ERROR FILES ==="
find /results -name 'errors.json' -exec echo {} \; -exec cat {} \; 2>/dev/null || echo "(no error files)"
echo "=== END ERROR FILES ==="
echo ""
echo "=== BBR LOGS ==="
ls -la /results/bbr_logs/ 2>/dev/null || echo "(no bbr logs)"
echo "=== END BBR LOGS ==="
echo ""
echo "=== RESULTS_READY ==="
tail -f /dev/null
