#!/bin/bash
#
# Usage:
#   ./run_benchmark.sh                    # Full run (deploy + benchmark)
#   ./run_benchmark.sh --skip-deploy      # Only create ConfigMap and launch job
#   ./run_benchmark.sh --extract-only     # Only extract results from existing pod
#
# Prerequisites:
#   - oc CLI authenticated to the cluster
#   - MaaS Gateway stack deployed (RHCL operator, postgres, payload-processing)
#   - guidellm-token secret created:
#       oc create secret generic guidellm-token \
#         --from-literal=token=dummy-key -n openshift-ingress

set -euo pipefail

NAMESPACE="${NAMESPACE:-openshift-ingress}"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MANIFESTS="$SCRIPT_DIR/manifests"
BENCHMARK_SCRIPTS="$SCRIPT_DIR/scripts/benchmark"
JOB_NAME="multi-provider-ab-test"
CM_NAME="multi-provider-ab-script"

SKIP_DEPLOY=false
EXTRACT_ONLY=false

for arg in "$@"; do
    case "$arg" in
        --skip-deploy)  SKIP_DEPLOY=true ;;
        --extract-only) EXTRACT_ONLY=true ;;
    esac
done

echo "============================================"
echo " MaaS AI Gateway Benchmark"
echo " Namespace: $NAMESPACE"
echo "============================================"
echo ""

extract_results() {
    local pod
    pod=$(oc get pods -l app=$JOB_NAME -n "$NAMESPACE" \
          -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    if [ -z "$pod" ]; then
        echo "ERROR: No benchmark pod found."
        exit 1
    fi
    local results_dir="./results/$(date +%Y%m%d_%H%M%S)"
    mkdir -p "$results_dir"
    echo "Extracting results from $pod to $results_dir ..."
    oc cp "$NAMESPACE/$pod:/results" "$results_dir" || \
        echo "WARN: oc cp failed — pod may no longer be running."
    echo "Results saved to: $results_dir"
}

if $EXTRACT_ONLY; then
    extract_results
    exit 0
fi

# -------------------------------------------------------
# Step 1: Deploy infrastructure manifests
# -------------------------------------------------------
if ! $SKIP_DEPLOY; then
    echo "[1/4] Deploying infrastructure manifests..."

    echo "  Applying benchmark-sa (ServiceAccount + RBAC)..."
    oc apply -f "$MANIFESTS/benchmark-sa.yaml"

    echo "  Applying dummy API key secrets..."
    oc apply -f "$MANIFESTS/secrets-dummy.yaml"

    echo "  Applying llm-d-inference-sim..."
    oc apply -f "$MANIFESTS/llm-d-inference-sim.yaml"

    echo "  Waiting for simulator rollout..."
    oc rollout status deployment/llm-d-inference-sim -n "$NAMESPACE" --timeout=120s

    echo "  Applying ExternalModel CRs..."
    oc apply -f "$MANIFESTS/external-models.yaml"

    echo "  Applying HTTPRoutes..."
    oc apply -f "$MANIFESTS/httproutes.yaml"

    echo "  Infrastructure ready."
    echo ""
else
    echo "[1/4] Skipping infrastructure deployment (--skip-deploy)."
    echo ""
fi

# -------------------------------------------------------
# Step 2: Create ConfigMap from benchmark scripts
# -------------------------------------------------------
echo "[2/4] Creating ConfigMap '$CM_NAME' from scripts/benchmark/ ..."
oc create configmap "$CM_NAME" \
    --from-file=run.sh="$BENCHMARK_SCRIPTS/run.sh" \
    --from-file=parse.py="$BENCHMARK_SCRIPTS/parse.py" \
    --from-file=plugin_delta.py="$BENCHMARK_SCRIPTS/plugin_delta.py" \
    --from-file=prom_monitor.py="$BENCHMARK_SCRIPTS/prom_monitor.py" \
    -n "$NAMESPACE" \
    --dry-run=client -o yaml | oc apply -f -
echo ""

# -------------------------------------------------------
# Step 3: Launch benchmark job
# -------------------------------------------------------
echo "[3/4] Launching benchmark job..."
oc delete job "$JOB_NAME" -n "$NAMESPACE" --ignore-not-found 2>/dev/null || true
oc apply -f "$MANIFESTS/GuideLLM-benchmark-job.yaml"
echo ""

# -------------------------------------------------------
# Step 4: Stream logs
# -------------------------------------------------------
echo "[4/4] Waiting for benchmark pod to start..."
echo "      (Ctrl+C to detach — the job continues running in the cluster)"
echo ""

POD=""
for i in $(seq 1 60); do
    POD=$(oc get pods -l app=$JOB_NAME -n "$NAMESPACE" \
          -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    if [ -n "$POD" ]; then
        break
    fi
    printf "."
done
echo ""

if [ -z "$POD" ]; then
    echo "ERROR: Benchmark pod did not appear within 60 attempts."
    echo "Check: oc get pods -n $NAMESPACE -l app=$JOB_NAME"
    exit 1
fi

echo "Pod: $POD"
echo "Streaming logs..."
echo ""
oc logs -f "$POD" -n "$NAMESPACE" || true

echo ""
echo "============================================"
echo " Log stream ended."
echo " To extract results: ./run_benchmark.sh --extract-only"
echo "============================================"
