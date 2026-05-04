#!/bin/bash
#
# Supplementary experiments: per-plugin latency (Exp 1) + multi-turn 50 error investigation (Exp 2).
# Runs inside the GuideLLM benchmark pod, mounted via ConfigMap.

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
CSV="$RESULTS/summary.csv"
PLUGIN_CSV="$RESULTS/plugin_latency.csv"
CSV_DUMP_EVERY=5

echo "provider,benchmark,mode,prompt_tokens,output_tokens,turns,concurrency,successful,errored,rps,mean_ms,p50_ms,p95_ms,p99_ms,duration_s" > "$CSV"

TOTAL=0; PASS=0; FAIL=0

echo "============================================"
echo " Supplementary Experiments"
echo "============================================"
echo " Exp 1: Per-plugin latency (small payload, all concurrency, all providers)"
echo " Exp 2: Multi-turn 50 error investigation (all providers, c=4,32,128)"
echo ""
echo " Simulator: TTFT=1ms, ITL=1ms, --deterministic-tokens"
echo " Baseline:  $SIMULATOR/v1/chat/completions"
echo " Gateway:   $GATEWAY/v1/chat/completions"
echo " Metrics:   $METRICS_URL (plugin latency)"
echo " Duration:  ${MAX_SEC}s per benchmark (warmup: ${WARMUP}s)"
echo " Started:   $(date -Iseconds)"
echo "============================================"
echo ""

python3 /scripts/prom_monitor.py &
MONITOR_PID=$!
echo "[run_supplementary.sh] Prometheus monitor started (PID $MONITOR_PID)"

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
        echo "  WARN: metrics scrape empty — plugin latency not collected"
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

ALL_LEVELS=(2 4 8 16 32 64 128 256 512 1024)
MT_LEVELS=(4 32 128)
ALL_PROVIDERS=("gpt-4o-openai" "gpt-4o-azure" "gpt-4o-bedrock" "claude-sonnet-anthropic" "claude-sonnet-vertex")

echo ""
echo "###################################################"
echo " EXPERIMENT 1: Per-Plugin Latency"
echo " Small payload (32/64), all concurrency levels"
echo " All 5 providers × 10 levels = 50 A/B pairs"
echo "###################################################"
echo ""

for model in "${ALL_PROVIDERS[@]}"; do
    echo ""
    echo "=== Exp1: $model ==="
    for C in "${ALL_LEVELS[@]}"; do
        run_ab "$model" "$model" "exp1-small-c${C}" 32 64 "$C"
    done
    echo ">>> Exp1 $model complete"
    dump_csv
    echo "--- Plugin latency so far ---"
    cat "$PLUGIN_CSV" 2>/dev/null || echo "(no plugin data yet)"
    echo "---"
done

echo ""
echo "###################################################"
echo " EXPERIMENT 2: Multi-Turn 50 Error Investigation"
echo " turns=50, small payload (64/128)"
echo " All 5 providers × 3 concurrency levels = 15 A/B pairs"
echo "###################################################"
echo ""

for model in "${ALL_PROVIDERS[@]}"; do
    echo ""
    echo "=== Exp2: $model (turns=50) ==="
    for C in "${MT_LEVELS[@]}"; do
        capture_bbr_logs "${model}/multiturn-50-c${C}" "before"
        run_ab "$model" "$model" "multiturn-50-c${C}" 64 128 "$C" 50
        capture_bbr_logs "${model}/multiturn-50-c${C}" "after"
    done
    echo ">>> Exp2 $model complete"
    dump_csv
done

echo "" > /results/.current_benchmark
touch /results/.monitor_stop

wait "$MONITOR_PID" 2>/dev/null || true

echo ""
echo "============================================"
echo " SUPPLEMENTARY EXPERIMENTS COMPLETE"
echo " Total: $TOTAL  Passed: $PASS  Failed: $FAIL"
echo " Finished: $(date -Iseconds)"
echo "============================================"
echo ""
echo "=== FINAL CSV DUMP ==="
cat "$CSV"
echo "=== END FINAL CSV DUMP ==="
echo ""
echo "=== PLUGIN LATENCY CSV DUMP ==="
cat "$PLUGIN_CSV" 2>/dev/null || echo "(no plugin data)"
echo "=== END PLUGIN LATENCY CSV DUMP ==="
echo ""
echo "=== ERROR FILES ==="
find /results -name 'errors.json' -exec echo {} \; -exec cat {} \; 2>/dev/null || echo "(no error files)"
echo "=== END ERROR FILES ==="
echo ""
echo "=== BBR LOGS ==="
ls -la /results/bbr_logs/ 2>/dev/null || echo "(no bbr logs)"
echo "=== END BBR LOGS ==="
echo ""
echo "=== PROMETHEUS MONITORING SUMMARY ==="
for f in /results/prometheus/*.csv; do
    echo "  $(basename $f): $(wc -l < $f) lines"
done
echo "=== END PROMETHEUS SUMMARY ==="
echo ""
echo "=== DISK USAGE ==="
df -h /results
du -sh /results/*
echo "=== END DISK USAGE ==="
echo ""
echo "=== RESULTS_READY ==="
echo "Pod staying alive. Data on PVC: multi-provider-ab-results"
tail -f /dev/null
