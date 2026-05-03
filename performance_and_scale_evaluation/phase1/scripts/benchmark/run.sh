#!/bin/bash

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
CSV_DUMP_EVERY=10

echo "provider,benchmark,mode,prompt_tokens,output_tokens,turns,concurrency,successful,errored,rps,mean_ms,p50_ms,p95_ms,p99_ms,duration_s" > "$CSV"

TOTAL=0; PASS=0; FAIL=0

echo "============================================"
echo " Multi-Provider A/B Test"
echo " All Providers: OpenAI, Azure, Bedrock, Anthropic, Vertex"
echo "============================================"
echo " Simulator: TTFT=1ms, ITL=1ms, --deterministic-tokens"
echo " Baseline:  $SIMULATOR/v1/chat/completions (direct, OpenAI format)"
echo " Gateway:   $GATEWAY/v1/chat/completions (body-based routing)"
echo "            BBR extracts model -> header-based route match"
echo "            BBR translates body + sets :path to native endpoint"
echo " Metrics:   $METRICS_URL (plugin latency)"
echo " Duration:  ${MAX_SEC}s per benchmark (warmup: ${WARMUP}s, effective: $((MAX_SEC - WARMUP))s)"
echo " Hard timeout: ${BENCH_TIMEOUT}s"
echo " Mode:      NON-STREAMING"
echo ""
echo " Nil-translator providers (reduced matrix, 22 A/B pairs each):"
echo "   - gpt-4o-openai    (passthrough)"
echo "   - gpt-4o-azure     (Azure OpenAI)"
echo "   - gpt-4o-bedrock   (Bedrock OpenAI)"
echo " Full-translator providers (full matrix, 49 A/B pairs each):"
echo "   - claude-sonnet-anthropic  (Anthropic Messages API)"
echo "   - claude-sonnet-vertex     (Vertex AI GenerateContent API)"
echo ""
echo " Total: 3×22 + 2×49 = 164 A/B pairs = 328 benchmarks"
echo " Estimated runtime: ~30 hours"
echo ""
echo " Prometheus: CPU, memory, network every 5s"
echo " Started:   $(date -Iseconds)"
echo "============================================"
echo ""

python3 /scripts/prom_monitor.py &
MONITOR_PID=$!
echo "[run.sh] Prometheus monitor started (PID $MONITOR_PID)"

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

    # Baseline: direct to simulator (OpenAI format)
    run_bench "$provider_label" "$model" "$name" "$SIMULATOR" "baseline" "$pt" "$ot" "$concurrency" "$turns"

    # Gateway: body-based routing (model in JSON body, BBR extracts it)
    scrape_metrics "$metrics_dir/before.prom"
    run_bench "$provider_label" "$model" "$name" "${GATEWAY}" "gateway" "$pt" "$ot" "$concurrency" "$turns"
    scrape_metrics "$metrics_dir/after.prom"
    if [ -s "$metrics_dir/before.prom" ] && [ -s "$metrics_dir/after.prom" ]; then
        echo "  --- Plugin latency (${provider_label}/${name}) ---"
        python3 /scripts/plugin_delta.py "$metrics_dir/before.prom" "$metrics_dir/after.prom" "$PLUGIN_CSV" "${provider_label}/${name}" || \
            echo "  WARN: plugin_delta.py failed"
        rm -f "$metrics_dir/before.prom" "$metrics_dir/after.prom"
    fi
}

ALL_LEVELS=(2 4 8 16 32 64 128 256 512 1024)
REDUCED_LEVELS=(8 64 512)
MT_LEVELS_FULL=(4 32 128)
MT_LEVELS_REDUCED=(32)

run_full_provider() {
    local model="$1"
    local pair_count=49

    echo ""
    echo "###################################################"
    echo " Provider: $model"
    echo " Mode: FULL-TRANSLATOR (all sizes × all levels)"
    echo " Routing: body-based"
    echo " $pair_count A/B pairs"
    echo "###################################################"
    echo ""

    echo "=== All payload sizes - FULL 10 levels each ==="
    for spec in "small:32:64" "medium:256:512" "large:1024:1024" "very-large:2048:2048"; do
        IFS=: read -r pname pt ot <<< "$spec"
        for C in "${ALL_LEVELS[@]}"; do
            run_ab "$model" "$model" "payload-${pname}-c${C}" "$pt" "$ot" "$C"
        done
    done

    echo ""
    echo "=== Multi-Turn - FULL (turns: 1,10,50 × c=4,32,128) ==="
    for turns in 1 10 50; do
        for C in "${MT_LEVELS_FULL[@]}"; do
            run_ab "$model" "$model" "multiturn-${turns}-c${C}" 64 128 "$C" "$turns"
        done
    done

    echo ""
    echo ">>> $model COMPLETE ($PASS passed, $FAIL failed so far)"
    dump_csv
    echo "--- Plugin latency so far ---"
    cat "$PLUGIN_CSV" 2>/dev/null || echo "(no plugin data yet)"
    echo "---"
}

run_nil_provider() {
    local model="$1"
    local pair_count=22

    echo ""
    echo "###################################################"
    echo " Provider: $model"
    echo " Mode: NIL-TRANSLATOR (reduced medium/large/xl)"
    echo " Routing: body-based"
    echo " $pair_count A/B pairs"
    echo "###################################################"
    echo ""

    echo "=== Small payload (32/64) - FULL 10 levels ==="
    for C in "${ALL_LEVELS[@]}"; do
        run_ab "$model" "$model" "payload-small-c${C}" 32 64 "$C"
    done

    echo ""
    echo "=== Medium payload (256/512) - REDUCED 3 levels ==="
    for C in "${REDUCED_LEVELS[@]}"; do
        run_ab "$model" "$model" "payload-medium-c${C}" 256 512 "$C"
    done

    echo ""
    echo "=== Large payload (1024/1024) - REDUCED 3 levels ==="
    for C in "${REDUCED_LEVELS[@]}"; do
        run_ab "$model" "$model" "payload-large-c${C}" 1024 1024 "$C"
    done

    echo ""
    echo "=== Very-large payload (2048/2048) - REDUCED 3 levels ==="
    for C in "${REDUCED_LEVELS[@]}"; do
        run_ab "$model" "$model" "payload-very-large-c${C}" 2048 2048 "$C"
    done

    echo ""
    echo "=== Multi-Turn - REDUCED (turns: 1,10,50 × c=32 only) ==="
    for turns in 1 10 50; do
        for C in "${MT_LEVELS_REDUCED[@]}"; do
            run_ab "$model" "$model" "multiturn-${turns}-c${C}" 64 128 "$C" "$turns"
        done
    done

    echo ""
    echo ">>> $model COMPLETE ($PASS passed, $FAIL failed so far)"
    dump_csv
    echo "--- Plugin latency so far ---"
    cat "$PLUGIN_CSV" 2>/dev/null || echo "(no plugin data yet)"
    echo "---"
}

# -------------------------------------------------------
# Run all providers
# -------------------------------------------------------

# Full-translator providers (full test matrix)
run_full_provider "claude-sonnet-anthropic"
run_full_provider "claude-sonnet-vertex"

# Nil-translator providers (reduced test matrix)
run_nil_provider "gpt-4o-openai"
run_nil_provider "gpt-4o-azure"
run_nil_provider "gpt-4o-bedrock"

# -------------------------------------------------------
# Done — signal completion and keep pod alive for extraction
# -------------------------------------------------------
echo "" > /results/.current_benchmark
touch /results/.monitor_stop

wait "$MONITOR_PID" 2>/dev/null || true

echo ""
echo "============================================"
echo " MULTI-PROVIDER A/B TEST COMPLETE"
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
