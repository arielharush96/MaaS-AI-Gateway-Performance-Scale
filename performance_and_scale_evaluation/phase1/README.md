# MaaS AI Gateway — Phase 1 Performance Evaluation

Baseline performance and overhead benchmarks for the MaaS AI Gateway
(BBR payload-processing sidecar). Measures the latency and throughput cost
of routing LLM requests through the gateway vs sending them directly to the
inference backend.

## Architecture

```
                        ┌─────────────────────────────────────────────────┐
  Baseline (A):         │  GuideLLM ──► llm-d-inference-sim              │
                        │               (direct, OpenAI format)           │
                        └─────────────────────────────────────────────────┘

                        ┌─────────────────────────────────────────────────┐
  Gateway  (B):         │  GuideLLM ──► Envoy + BBR ──► sim              │
                        │               (body-based routing,              │
                        │                API translation,                 │
                        │                plugin chain)                    │
                        └─────────────────────────────────────────────────┘
```

GuideLLM sends OpenAI-format requests to both targets. The gateway's BBR
plugin chain extracts the model name from the request body, resolves the
provider, translates the request to the native API format, and forwards it
to the simulator. The difference in latency between A and B is the gateway
overhead.

## Prerequisites

- OpenShift cluster with the MaaS AI Gateway (data-science-gateway + BBR) installed
- `oc` CLI authenticated to the cluster
- A `guidellm-token` secret and `benchmark-sa` ServiceAccount in the target namespace

## Deployment Order

All manifests target the `openshift-ingress` namespace.

### 1. Simulator

```bash
oc apply -f manifests/llm-d-inference-sim.yaml
```

Deploys `llm-d-inference-sim` — a multi-provider LLM simulator that responds
to OpenAI, Anthropic, Azure, Bedrock, and Vertex AI API formats.

Key flags:
- `--deterministic-tokens` — every response returns exactly `max_tokens`
  output tokens, eliminating randomness in A/B latency comparisons
- `--time-to-first-token 1` / `--inter-token-latency 1` — 1ms simulated
  token generation (latency = TTFT + ITL × output_tokens)
- `--providers anthropic,azure,bedrock,vertexai` — enables non-OpenAI
  endpoint handlers

Served model names: `gpt-4o-openai`, `gpt-4o-azure`, `gpt-4o-bedrock`,
`claude-sonnet-anthropic`, `claude-sonnet-vertex`, `gemini-pro-vertex`

### 2. External Models

```bash
oc apply -f manifests/external-models.yaml
```

Creates `ExternalModel` CRs that tell the gateway which provider format
to use for each model name:

| Model                    | Provider       | Translator     |
|--------------------------|----------------|----------------|
| `gpt-4o-openai`         | `openai`       | passthrough     |
| `gpt-4o-azure`          | `azure-openai` | nil (path only) |
| `gpt-4o-bedrock`        | `bedrock-openai`| nil (path only)|
| `claude-sonnet-anthropic`| `anthropic`   | full            |
| `claude-sonnet-vertex`  | `vertex`       | full            |
| `gemini-pro-vertex`     | `vertex`       | full            |

### 3. HTTP Routes

```bash
oc apply -f manifests/httproutes.yaml
```

Creates `HTTPRoute` resources with two match rules per model:
1. **Path-prefix match** — `/<model-name>/` with URL rewrite (legacy)
2. **Header match** — `X-Gateway-Model-Name: <model-name>` (used by benchmarks)

The A/B test uses header-based routing (rule 2): GuideLLM sends requests to
`GATEWAY/v1/chat/completions` with the model name in the JSON body. The BBR
`body-field-to-header` plugin extracts it into the `X-Gateway-Model-Name`
header, which the HTTPRoute matches on.

### 4. A/B Benchmark Job

```bash
oc apply -f manifests/multi-provider-ab-test.yaml
```

Creates a PVC, ConfigMap (with embedded scripts), and a Job that runs
GuideLLM benchmarks for all providers.

## Test Matrix

### Payload sizes

| Name       | Prompt tokens | Output tokens | Expected latency |
|------------|---------------|---------------|------------------|
| small      | 32            | 64            | ~65ms            |
| medium     | 256           | 512           | ~513ms           |
| large      | 1024          | 1024          | ~1025ms          |
| very-large | 2048          | 2048          | ~2049ms          |

### Concurrency levels

`2, 4, 8, 16, 32, 64, 128, 256, 512, 1024`

### Multi-turn depths

`1, 10, 50` conversation turns

### Provider test coverage

**Full-translator** providers (body is translated to native format):
- `claude-sonnet-anthropic`, `claude-sonnet-vertex`
- All 4 payload sizes × 10 concurrency levels = 40 A/B pairs
- 3 turn depths × 3 concurrency levels = 9 A/B pairs
- **49 A/B pairs per provider**

**Nil-translator** providers (body passes through, only path changes):
- `gpt-4o-openai`, `gpt-4o-azure`, `gpt-4o-bedrock`
- Small payload × 10 concurrency levels = 10 A/B pairs
- Medium/large/very-large × 3 reduced levels = 9 A/B pairs
- 3 turn depths × 1 concurrency level = 3 A/B pairs
- **22 A/B pairs per provider**

**Total: 2×49 + 3×22 = 164 A/B pairs = 328 benchmarks**

### Benchmark parameters

| Parameter        | Value  |
|------------------|--------|
| Duration         | 180s per benchmark (60s warmup + 120s measurement) |
| Hard timeout     | 300s   |
| Streaming        | disabled |
| GuideLLM version | v0.6.0 |
| Tokenizer        | gpt2   |

## Monitoring

The job runs a background Prometheus monitor that collects every 5 seconds:
- **CPU** usage per pod (payload-processing, gateway, simulator)
- **Memory** working set per pod
- **Network** receive/transmit bytes per pod

Plugin-level latency is scraped from the BBR `/metrics` endpoint before
and after each gateway benchmark run.

## Output

Results are written to the PVC and dumped to job logs:

```
/results/
├── summary.csv                    # Main results (one row per benchmark)
├── plugin_latency.csv             # Per-plugin latency deltas
├── prometheus/
│   ├── cpu.csv
│   ├── memory.csv
│   ├── net_rx.csv
│   ├── net_tx.csv
│   └── benchmark_markers.csv      # Timestamps for each benchmark start
└── <provider>/<benchmark>/<mode>/
    └── guidellm.log               # Raw GuideLLM output
```

### Extracting results

While the pod is alive after completion (it runs `tail -f /dev/null`):

```bash
# Copy all results to local machine
oc cp openshift-ingress/<pod-name>:/results ./results

# Or use the pipeline orchestrator
./run_pipeline.sh  # handles extraction automatically
```

## Pipeline Orchestrator

`run_pipeline.sh` automates the full benchmark lifecycle:

```bash
./run_pipeline.sh                  # Run all stages (1-4)
./run_pipeline.sh stage1           # Baseline only
./run_pipeline.sh stage2           # Gateway overhead only
./run_pipeline.sh stage3           # HPA scaling (2/4/8 replicas)
./run_pipeline.sh stage4           # Per-plugin latency from Prometheus
./run_pipeline.sh test             # Quick validation (1 benchmark, 15s)
./run_pipeline.sh analyze-latest   # Re-run analysis on latest results
```

Each stage: applies the manifest, waits for `RESULTS_READY`, extracts
results from the pod (3-method fallback: `oc cp` → Python tar → individual
file cat), and saves a config snapshot.

## File Index

```
manifests/
├── llm-d-inference-sim.yaml       # Simulator deployment + service
├── external-models.yaml           # ExternalModel CRs (provider mapping)
├── httproutes.yaml                # HTTPRoute per model (path + header match)
└── multi-provider-ab-test.yaml    # A/B benchmark job (all providers)

run_pipeline.sh                    # Pipeline orchestrator (stages 1-4)

scripts/
├── launch_benchmark.sh            # Simple manifest launcher with log streaming
└── ...                            # Analysis and utility scripts
```
