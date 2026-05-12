# MaaS AI Gateway - Phase 1 Performance Evaluation

Baseline performance and overhead benchmarks for the MaaS AI Gateway
(BBR payload-processing sidecar). Measures the latency and throughput cost
of routing LLM requests through the gateway vs sending them directly to the
inference backend.

## Deployment Guide

### Step 0 — Deploy the MaaS Platform

Before running benchmarks, the MaaS platform must be deployed on the cluster.
Follow the official quickstart:

> **https://opendatahub-io.github.io/models-as-a-service/v0.1.0/quickstart/**

This deploys:
- RHOAI / ODH operator with `modelsAsService: Managed`
- RHCL / Kuadrant operator (gateway, auth, rate limiting)
- The MaaS gateway (`maas-default-gateway` in `openshift-ingress`)
- Authorino (authentication)
- MaaS API

### Step 1 — Deploy PostgreSQL

The MaaS platform requires a PostgreSQL database. If not already deployed:

```bash
# Review and set your password in the secret (replace <CHANGE_ME>)
oc apply -f performance_and_scale_evaluation/phase1/manifests/infrastructure/postgres.yaml
```


### Step 2 — Deploy the Simulator

The simulator replaces a real LLM provider with a deterministic backend.
All providers return responses with fixed TTFT=1ms and ITL=1ms per token.

```bash
oc apply -f performance_and_scale_evaluation/phase1/manifests/llm-d-inference-sim.yaml
```

### Step 3 — Deploy Payload Processing (BBR)

Install the payload-processing sidecar via Helm. This deploys the 4-plugin
chain that processes every request through the gateway.
### Step 4 — Apply Models, Routes, Secrets, and RBAC

```bash
# ExternalModel CRs (model→provider mapping)
oc apply -f performance_and_scale_evaluation/phase1/manifests/external-models.yaml

# HTTPRoutes (path-based routing per model)
oc apply -f performance_and_scale_evaluation/phase1/manifests/httproutes.yaml

# Dummy API key secrets (simulator doesn't validate them)
oc apply -f performance_and_scale_evaluation/phase1/manifests/secrets.yaml

# Benchmark ServiceAccount + RBAC (Prometheus access + gateway auth)
oc apply -f performance_and_scale_evaluation/phase1/manifests/benchmark-sa.yaml
```

### Step 5 — Create Authentication Token

The MaaS gateway uses Kuadrant AuthPolicy with Kubernetes TokenReview.
A ServiceAccount token with the correct audience is required:

```bash
TOKEN=$(oc create token default \
  --audience=maas-default-gateway-sa \
  -n openshift-ingress \
  --duration=xm)

oc delete secret guidellm-token -n openshift-ingress --ignore-not-found
oc create secret generic guidellm-token \
  --from-literal=token=$TOKEN \
  -n openshift-ingress
```
**Main A/B benchmark** 

```bash
# Create ConfigMap from scripts
oc create configmap multi-provider-ab-script \
  --from-file=run.sh=performance_and_scale_evaluation/phase1/scripts/benchmark/run.sh \
  --from-file=parse.py=performance_and_scale_evaluation/phase1/scripts/benchmark/parse.py \
  --from-file=plugin_delta.py=performance_and_scale_evaluation/phase1/scripts/benchmark/plugin_delta.py \
  --from-file=prom_monitor.py=performance_and_scale_evaluation/phase1/scripts/benchmark/prom_monitor.py \
  -n openshift-ingress

# Launch the job
oc apply -f performance_and_scale_evaluation/phase1/manifests/GuideLLM-benchmark-job.yaml
```

**Per-plugin latency** (small payload, all concurrency levels):

```bash
oc create configmap exp1-benchmark-script \
  --from-file=run_exp1_plugin_latency.sh=performance_and_scale_evaluation/phase1/scripts/benchmark/run_exp1_plugin_latency.sh \
  --from-file=parse.py=performance_and_scale_evaluation/phase1/scripts/benchmark/parse.py \
  --from-file=prom_monitor.py=performance_and_scale_evaluation/phase1/scripts/benchmark/prom_monitor.py \
  --from-file=plugin_delta.py=performance_and_scale_evaluation/phase1/scripts/benchmark/plugin_delta.py \
  -n openshift-ingress

oc apply -f performance_and_scale_evaluation/phase1/manifests/exp1-benchmark-job.yaml
```

### Step 8 — Extract Results

When the benchmark completes, the pod prints `=== RESULTS_READY ===` and stays
alive. Results are stored on the PVC. Extract them with `oc cp`:

```bash
# Find the pod name
POD=$(oc get pods -n openshift-ingress -l job-name=multi-provider-ab-test \
  --no-headers -o custom-columns=":metadata.name")

# Clean up the job and PVC when done
oc delete job multi-provider-ab-test -n openshift-ingress
oc delete pvc multi-provider-ab-results -n openshift-ingress
```
## Simulator

`llm-d-inference-sim` is a multi-provider LLM simulator that responds to
OpenAI, Anthropic, Azure, Bedrock, and Vertex AI API formats. This version
([feat/multi-provider-support](https://github.com/arielharush96/llm-d-inference-sim/tree/feat/multi-provider-support))
is a fork that allows the simulator to handle requests for all 5 providers.

Key flags:
- `--deterministic-tokens` — every response returns exactly `max_tokens`
  output tokens, ensuring consistent A/B latency comparisons
- `--time-to-first-token 1` / `--inter-token-latency 1` — 1ms simulated
  token generation
- `--providers anthropic,azure,bedrock,vertexai` — enables non-OpenAI
  endpoint handlers

## Monitoring

The benchmark pod runs a background Prometheus monitor (`prom_monitor.py`)
that queries the cluster's built-in Prometheus via the Thanos API every 5 seconds:

- **CPU** — `container_cpu_usage_seconds_total` per pod
- **Memory** — `container_memory_working_set_bytes` per pod
- **Network** — `container_network_receive_bytes_total` / `transmit` per pod
- **Per-plugin latency** — scraped from the BBR `/metrics` endpoint before and
  after each gateway benchmark run via `plugin_delta.py`
- **Istio/Envoy metrics** — request duration histograms, upstream/downstream
  latency, Authorino auth duration

Results are written as CSV files to the PVC under `/results/prometheus/`.
