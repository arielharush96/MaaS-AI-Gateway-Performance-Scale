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

                        ┌─────────────────────────────────────────────────────────────────┐
  Gateway  (B):         │  GuideLLM ──► AI-Gateway(BBR+Envoy) ──► llm-d-inference-sim     │
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
├── README.md
│
├── manifests/
│   ├── llm-d-inference-sim.yaml              # Simulator deployment + service
│   ├── external-models.yaml                  # ExternalModel CRs (model→provider mapping)
│   ├── httproutes.yaml                       # HTTPRoute per model (path + header routing)
│   ├── secrets.yaml                          # Dummy API keys for simulator
│   ├── benchmark-sa.yaml                     # ServiceAccount + RBAC (monitoring + gateway access)
│   ├── smoke-test.yaml                       # Quick end-to-end validation pod
│   ├── supplementary-benchmark-job.yaml      # Job for supplementary experiments
│   └── infrastructure/                       # Platform-level prerequisites
│       ├── rhcl-kuadrant.yaml                # RHCL/Kuadrant operator (OLM subscription)
│       ├── postgres.yaml                     # PostgreSQL for MaaS DB
│       └── payload-processing-values.yaml    # BBR Helm values (production image)
│
└── scripts/
    └── benchmark/                            # Scripts that run INSIDE the GuideLLM pod
        ├── run.sh                            # Main A/B benchmark (all providers × sizes × concurrency)
        ├── run_supplementary.sh              # Exp 1: per-plugin latency + Exp 2: multi-turn errors
        ├── run_hpa.sh                        # Exp 3: HPA horizontal scaling test
        ├── parse.py                          # GuideLLM JSON → CSV parser (with error extraction)
        ├── parse_hpa.py                      # CSV parser for HPA tests (adds replica column)
        ├── plugin_delta.py                   # Per-plugin latency from Prometheus scrapes
        └── prom_monitor.py                   # Background CPU/memory/network collector
```

## Quick Start

### 1. Prerequisites

- OpenShift cluster with RHOAI (Red Hat OpenShift AI) installed
- `modelsAsService` component set to `Managed` in the DataScienceCluster
- RHCL/Kuadrant operator installed (`manifests/infrastructure/rhcl-kuadrant.yaml`)
- `oc` CLI logged in with cluster-admin

### 2. Deploy Infrastructure

```bash
# Install payload-processing (BBR) via Helm
helm upgrade --install payload-processing deploy/payload-processing \
  -f performance_and_scale_evaluation/phase1/manifests/infrastructure/payload-processing-values.yaml \
  -n openshift-ingress

# Deploy the simulator
oc apply -f performance_and_scale_evaluation/phase1/manifests/llm-d-inference-sim.yaml

# Apply ExternalModel CRs and HTTPRoutes
oc apply -f performance_and_scale_evaluation/phase1/manifests/external-models.yaml
oc apply -f performance_and_scale_evaluation/phase1/manifests/httproutes.yaml

# Apply dummy API key secrets
oc apply -f performance_and_scale_evaluation/phase1/manifests/secrets.yaml

# Create benchmark ServiceAccount and RBAC
oc apply -f performance_and_scale_evaluation/phase1/manifests/benchmark-sa.yaml
```

### 3. Create Authentication Token

The MaaS gateway uses Kuadrant AuthPolicy with Kubernetes TokenReview.
A ServiceAccount token with the correct audience is required:

```bash
# Create a 2-hour SA token for gateway authentication
TOKEN=$(oc create token default \
  --audience=maas-default-gateway-sa \
  -n openshift-ingress \
  --duration=120m)

# Store it as a secret (GuideLLM reads it as OPENAI_API_KEY)
oc delete secret guidellm-token -n openshift-ingress --ignore-not-found
oc create secret generic guidellm-token \
  --from-literal=token=$TOKEN \
  -n openshift-ingress
```

> **Important**: The token expires after 2 hours. For long-running benchmarks,
> increase `--duration` or recreate the token before each run.

### 4. Validate with Smoke Test

```bash
oc apply -f performance_and_scale_evaluation/phase1/manifests/smoke-test.yaml
oc logs smoke-test -n openshift-ingress -f

# Expected: all 6 endpoints return HTTP 200
# Clean up:
oc delete pod smoke-test -n openshift-ingress
```

### 5. Run Benchmarks

Create the benchmark scripts ConfigMap and launch the job:

```bash
# Create ConfigMap from benchmark scripts
oc create configmap multi-provider-ab-script \
  --from-file=run.sh=performance_and_scale_evaluation/phase1/scripts/benchmark/run.sh \
  --from-file=parse.py=performance_and_scale_evaluation/phase1/scripts/benchmark/parse.py \
  --from-file=plugin_delta.py=performance_and_scale_evaluation/phase1/scripts/benchmark/plugin_delta.py \
  --from-file=prom_monitor.py=performance_and_scale_evaluation/phase1/scripts/benchmark/prom_monitor.py \
  -n openshift-ingress

# Launch the A/B benchmark job (creates PVC + Job)
# Edit the job YAML to select which script to run
oc apply -f performance_and_scale_evaluation/phase1/manifests/supplementary-benchmark-job.yaml
```

## Gateway URL Pattern

The gateway expects requests in the format:

```
http://<gateway-svc>/<model-name>/v1/chat/completions
```

For example:
- `http://gateway:80/gpt-4o-openai/v1/chat/completions`
- `http://gateway:80/claude-sonnet-anthropic/v1/chat/completions`

The HTTPRoutes match by path prefix (`/<model>/`) and rewrite the path to `/`
before forwarding to the simulator. A second rule matches by the
`X-Gateway-Model-Name` header (set by the BBR `body-field-to-header` plugin).

## Kuadrant Authentication

The MaaS gateway enforces authentication via a Kuadrant AuthPolicy:

1. **Authentication**: `kubernetesTokenReview` with audience `maas-default-gateway-sa`
2. **Authorization**: `kubernetesSubjectAccessReview` checking `post` verb on
   `llminferenceservices` (group `serving.kserve.io`)

The `benchmark-gateway-access` ClusterRole (in `benchmark-sa.yaml`) grants the
`default` ServiceAccount the required `post` permission.

### Troubleshooting Auth

If you get **401**: Token is invalid or expired. Recreate with `oc create token`.

If you get **403**: Check that:
- AuthConfigs in `kuadrant-system` are READY (`oc get authconfig -n kuadrant-system`)
- The `benchmark-gateway-access` ClusterRoleBinding exists
- If AuthConfigs are stale, delete them: `oc delete authconfig -n kuadrant-system -l kuadrant.io/managed=true`
  (the Kuadrant operator will recreate them)

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

| Provider | Model Name | API Format | Translator |
|----------|-----------|------------|------------|
| OpenAI | `gpt-4o-openai` | OpenAI Chat Completions | nil (passthrough) |
| Azure | `gpt-4o-azure` | Azure OpenAI | nil |
| Bedrock | `gpt-4o-bedrock` | Bedrock OpenAI | nil |
| Anthropic | `claude-sonnet-anthropic` | Anthropic Messages API | full |
| Vertex AI | `claude-sonnet-vertex` | Vertex AI GenerateContent | full |

## Monitoring

The benchmark pod runs a background Prometheus monitor that collects every 5 seconds:
- **CPU** usage per pod (payload-processing, gateway, simulator)
- **Memory** working set per pod
- **Network** receive/transmit bytes per pod
- **Per-plugin latency** scraped from the BBR `/metrics` endpoint before and
  after each gateway benchmark run
