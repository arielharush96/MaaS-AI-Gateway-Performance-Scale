
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

echo "provider,benchmark,mode,prompt_tokens,output_tokens,turns,concurrency,successful,errored,rps,mean_ms,p50_ms,p95_ms,p99_ms,duration_s" > "$CSV"

TOTAL=0; PASS=0; FAIL=0

echo "============================================"
echo " Experiment 1: Full-Stack Latency Breakdown"
echo "============================================"
echo " Payload:     small (32 prompt / 64 output tokens)"
echo " Concurrency: 2,4,8,16,32,64,128,256,512,1024"
echo " Providers:   all 5"
echo " Timing:      ${WARMUP}s warmup + $((MAX_SEC - WARMUP))s measurement"
echo " Total:       50 A/B pairs = 100 benchmarks"
echo ""
echo " Simulator:  $SIMULATOR"
echo " Gateway:    $GATEWAY/<model>"
echo " BBR metrics: $METRICS_URL"
echo " Thanos:     istio_request_duration, envoy_*_rq_time, authorino_*_duration"
echo " Started:    $(date -Iseconds)"
echo "============================================"
echo ""

python3 /scripts/prom_monitor.py &
MONITOR_PID=$!
echo "[exp1] Prometheus monitor started (PID $MONITOR_PID)"

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
    local provider_label="$1" model="$2" name="$3" pt="$4" ot="$5" concurrency="$6"
    local metrics_dir="$RESULTS/${provider_label}/${name}/plugin_metrics"
    mkdir -p "$metrics_dir"

    echo "${provider_label}/${name}" > /results/.current_benchmark

    echo ""
    echo ">>> [${provider_label}/${name}] pt=$pt ot=$ot c=$concurrency"

    run_bench "$provider_label" "$model" "$name" "$SIMULATOR" "baseline" "$pt" "$ot" "$concurrency"

    scrape_metrics "$metrics_dir/before.prom"
    run_bench "$provider_label" "$model" "$name" "${GATEWAY}/${model}" "gateway" "$pt" "$ot" "$concurrency"
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

ALL_LEVELS=(2 4 8 16 32 64 128 256 512 1024)
ALL_PROVIDERS=("gpt-4o-openai" "gpt-4o-azure" "gpt-4o-bedrock" "claude-sonnet-anthropic" "claude-sonnet-vertex")

for model in "${ALL_PROVIDERS[@]}"; do
    echo ""
    echo "###################################################"
    echo " Provider: $model"
    echo "###################################################"
    for C in "${ALL_LEVELS[@]}"; do
        run_ab "$model" "$model" "exp1-small-c${C}" 32 64 "$C"
    done
    echo ">>> $model complete"
    dump_csv
    echo "--- Plugin latency so far ---"
    cat "$PLUGIN_CSV" 2>/dev/null || echo "(no plugin data yet)"
    echo "---"
done

echo "" > /results/.current_benchmark
touch /results/.monitor_stop
wait "$MONITOR_PID" 2>/dev/null || true

echo ""
echo "============================================"
echo " EXPERIMENT 1 COMPLETE"
echo " Total: $TOTAL  Passed: $PASS  Failed: $FAIL"
echo " Finished: $(date -Iseconds)"
echo "============================================"
echo ""
echo "=== FINAL CSV ==="
cat "$CSV"
echo "=== END CSV ==="
echo ""
echo "=== PLUGIN LATENCY CSV ==="
cat "$PLUGIN_CSV" 2>/dev/null || echo "(no plugin data)"
echo "=== END PLUGIN LATENCY ==="
echo ""
echo "=== PROMETHEUS LATENCY METRICS ==="
for f in /results/prometheus/istio_*.csv /results/prometheus/envoy_*.csv /results/prometheus/authorino_*.csv; do
    if [ -f "$f" ] && [ -s "$f" ]; then
        echo "--- $(basename $f): $(wc -l < $f) lines ---"
        head -5 "$f"
        echo "..."
    fi
done
echo "=== END PROMETHEUS LATENCY ==="
echo ""
echo "=== PROMETHEUS MONITORING SUMMARY ==="
for f in /results/prometheus/*.csv; do
    echo "  $(basename $f): $(wc -l < $f) lines"
done
echo "=== END PROMETHEUS SUMMARY ==="
echo ""
echo "=== RESULTS_READY ==="
tail -f /dev/null
