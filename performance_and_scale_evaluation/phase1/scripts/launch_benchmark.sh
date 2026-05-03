#!/bin/bash
set -euo pipefail

MANIFEST="${1:?Usage: $0 <manifest.yaml> [results-label]}"
LABEL="${2:-$(basename "$MANIFEST" .yaml)}"
RESULTS_BASE="$(cd "$(dirname "$0")/.." && pwd)/results"
TSDIR="$RESULTS_BASE/$(date +%Y-%m-%d_%H-%M-%S)_${LABEL}"
NAMESPACE="openshift-ingress"

mkdir -p "$TSDIR/config"
cp "$MANIFEST" "$TSDIR/config/"

echo "=== Launch Benchmark ==="
echo "  Manifest: $MANIFEST"
echo "  Results:  $TSDIR"
echo "  Namespace: $NAMESPACE"
echo ""

echo ">>> Applying manifest..."
oc apply -f "$MANIFEST"
echo ""

JOB_NAME=$(grep -A1 'kind: Job' "$MANIFEST" | grep 'name:' | head -1 | awk '{print $2}')
echo ">>> Job: $JOB_NAME"
echo ">>> Waiting for pod to be Running..."

while true; do
    POD=$(oc get pods -n "$NAMESPACE" -l "job-name=$JOB_NAME" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    if [ -n "$POD" ]; then
        STATUS=$(oc get pod -n "$NAMESPACE" "$POD" -o jsonpath='{.status.phase}' 2>/dev/null || true)
        if [ "$STATUS" = "Running" ]; then
            break
        fi
        echo "  Pod $POD status: $STATUS"
    fi
done

echo ">>> Pod running: $POD"
echo ">>> Starting live log stream → $TSDIR/live.log"
echo ""
echo "  Press Ctrl+C to stop streaming (job continues in cluster)"
echo ""

oc logs -f -n "$NAMESPACE" "$POD" | tee "$TSDIR/live.log"
