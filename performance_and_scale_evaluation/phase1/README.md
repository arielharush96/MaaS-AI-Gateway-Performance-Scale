# MaaS AI Gateway - Phase 1 Performance Evaluation

Baseline performance and overhead benchmarks for the MaaS AI Gateway
(BBR payload-processing sidecar). Measures the latency and throughput cost
of routing LLM requests through the gateway vs sending them directly to the
inference backend.

## Architecture

```
                        ┌─────────────────────────────────────────────────┐
  Baseline (A):         │  GuideLLM ──► llm-d-inference-sim               │
                        │               (direct, OpenAI format)           │
                        └─────────────────────────────────────────────────┘

                        ┌────────────────────────────────────────────────----------------─┐
  Gateway  (B):         │  GuideLLM ──► AI-Gatway(BBR+Envoy) ──► llm-d-inference- sim     │
                        │               (body-based routing,                              │
                        │                API translation,                                 │
                        │                plugin chain)                                    │
                        └─────────────────────────────────────────────────────────────────┘
```

GuideLLM sends OpenAI-format requests to both targets. The gateway's BBR
plugin chain extracts the model name from the request body, resolves the
provider, translates the request to the native API format, and forwards it
to the simulator. The difference in latency between A and B is the gateway
overhead.

## File Structure

```
phase1/
├── run_benchmark.sh                          # Local orchestrator (runs from your laptop)
├── README.md
│
├── manifests/
│   ├── llm-d-inference-sim.yaml              # Simulator deployment + service
│   ├── external-models.yaml                  # ExternalModel CRs (model→provider mapping)
│   ├── httproutes.yaml                       # HTTPRoute per model (header-based routing)
│   ├── GuideLLM-benchmark-job.yaml           # PVC + Job (GuideLLM load generator)
│   ├── benchmark-sa.yaml                     # ServiceAccount + RBAC (Prometheus access)
│   ├── secrets-dummy.yaml                    # Dummy API keys for simulator
│   └── infrastructure/                       # Platform-level prerequisites
│       ├── rhcl-kuadrant.yaml                # RHCL/Kuadrant operator (OLM subscription)
│       ├── postgres.yaml                     # PostgreSQL for MaaS DB
│       └── payload-processing-values.yaml    # BBR Helm values (plugin chain config)
│
└── scripts/
    └── benchmark/                            # Scripts that run INSIDE the GuideLLM pod
        ├── run.sh                            # Main benchmark orchestrator
        ├── parse.py                          # GuideLLM JSON → CSV parser
        ├── plugin_delta.py                   # Per-plugin latency from Prometheus scrapes
        └── prom_monitor.py                   # Background CPU/memory/network collector
```

## Prerequisites

- OpenShift cluster with the MaaS AI Gateway stack installed:
  1. RHCL/Kuadrant operator (`manifests/infrastructure/rhcl-kuadrant.yaml`)
  2. PostgreSQL (`manifests/infrastructure/postgres.yaml`)
  3. BBR payload-processing sidecar deployed via Helm


## Simulator

`llm-d-inference-sim` is a multi-provider LLM simulator that responds to
OpenAI, Anthropic, Azure, Bedrock, and Vertex AI API formats. this version ( https://github.com/arielharush96/llm-d-inference-sim/tree/feat/multi-provider-support ) is a fork which allows the simulator to handle requests (from the ai gateway) for all 5 different providors.

Modified simulator key flags:
- `--deterministic-tokens` — every response returns exactly `max_tokens`
  output tokens, ensuring consistent A/B latency comparisons
- `--time-to-first-token 1` / `--inter-token-latency 1` — 1ms simulated
  token generation
- `--providers anthropic,azure,bedrock,vertexai` — enables non-OpenAI
  endpoint handlers

## Plugin Chain

The payload-processing (BBR) sidecar runs these plugins in order:

| # | Plugin                   | Purpose                                              |
|---|--------------------------|------------------------------------------------------|
| 1 | `body-field-to-header`   | Extracts `model` from JSON body → `X-Gateway-Model-Name` header |
| 2 | `model-provider-resolver`| Looks up ExternalModel CR, resolves provider + credentials |
| 3 | `api-translation`        | Translates OpenAI format → native provider format    |
| 4 | `apikey-injection`       | Injects API key from credentialRef secret             |

## Test Matrix

### Payload sizes

| Name       | Prompt tokens | Output tokens |
|------------|---------------|---------------|
| small      | 32            | 64            |
| medium     | 256           | 512           |
| large      | 1024          | 1024          |
| very-large | 2048          | 2048          |

### Concurrency levels

`2, 4, 8, 16, 32, 64, 128, 256, 512, 1024`

### Multi-turn depths

`1, 10, 50` conversation turns

### Provider coverage
- `claude-sonnet-anthropic` (Anthropic Messages API)
- `claude-sonnet-vertex` (Vertex AI GenerateContent API)
- `gpt-4o-openai` (OpenAI)
- `gpt-4o-azure` (Azure OpenAI)
- `gpt-4o-bedrock` (Bedrock OpenAI)
  

## Monitoring

The benchmark pod runs a background Prometheus monitor that collects every 5 seconds:
- **CPU** usage per pod (payload-processing, gateway, simulator)
- **Memory** working set per pod
- **Network** receive/transmit bytes per pod
- **PerPlugin Latency** Per-plugin latency is scraped from the BBR `/metrics` endpoint before and
after each gateway benchmark run.
