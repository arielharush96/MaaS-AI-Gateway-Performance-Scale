## MaaS AI Gateway — Performance & Scale Evaluation
The MaaS AI Gateway is an LLM request manager that sits between clients and inference backends. It accepts requests in OpenAI Chat Completions format and translates them on-the-fly to native provider APIs (Anthropic, Vertex AI, Azure OpenAI, Bedrock) via a BBR plugin chain. The gateway runs as an Envoy layer and handles model routing, API format translation, and credential injection all transparently to the client.

This repository contains the manifests and scripts used to measure the payload processing performance:
Baseline (A): GuideLLM → llm-d-inference-sim (direct, OpenAI format)
Gateway (B): GuideLLM → MaaS AI Gateway  → llm-d-inference-sim
GuideLLM (v0.6.0) is the load generator. It sends OpenAI-format requests at varying concurrency levels and payload sizes. llm-d-inference-sim is a multi-provider LLM simulator that responds to all five provider API formats with deterministic token counts (--deterministic-tokens), fixed TTFT (1ms), and fixed inter-token latency (1ms). By comparing latency and throughput between the two paths, we isolate the exact cost of routing through the gateway.

```
phase1/
├── run_benchmark.sh                          # Local orchestrator (your laptop)
├── README.md
│
├── manifests/
│   ├── llm-d-inference-sim.yaml              # Simulator deployment + service
│   ├── external-models.yaml                  # ExternalModel CRs
│   ├── httproutes.yaml                       # HTTPRoute per model
│   ├── GuideLLM-benchmark-job.yaml           # PVC + Job (clean, no embedded scripts)
│   ├── benchmark-sa.yaml                     # ServiceAccount + RBAC
│   ├── secrets-dummy.yaml                    # Dummy API keys
│   └── infrastructure/
│       ├── rhcl-kuadrant.yaml                # RHCL operator
│       ├── postgres.yaml                     # PostgreSQL (password sanitized)
│       └── payload-processing-values.yaml    # BBR Helm values
│
└── scripts/
    └── benchmark/                            # Run INSIDE the GuideLLM pod
        ├── run.sh                            # Main benchmark loop
        ├── parse.py                          # JSON→CSV parser
        ├── plugin_delta.py                   # Plugin latency calculator
        └── prom_monitor.py                   # Prometheus collector
```
## Pre-Requisites

The target cluster must have `ExternalModel` CRD deployed.  
## Install Payload Processing

1. If ExternalModel CRD is not deployed in your cluster, deploy it using the following:

    ```bash
    kubectl apply -f https://raw.githubusercontent.com/opendatahub-io/models-as-a-service/refs/heads/main/deployment/base/maas-controller/crd/bases/maas.opendatahub.io_externalmodels.yaml
    ```

1. Set `GATEWAY_NAME` and `GATEWAY_NAMESPACE` variables. The chart **must be
   installed in the same namespace as the Gateway** for the Istio EnvoyFilter
   `targetRefs` to work:

    ```bash
    export GATEWAY_NAME=maas-default-gateway
    export GATEWAY_NAMESPACE=openshift-ingress
    ```

1.  Clean local copy of upstream chart to avoid using stale version:

    ```bash
    rm ./deploy/payload-processing/charts/body-based-routing-v0.tgz
    ```

1.  Install `payload-processing` helm chart:

    ```bash
    helm install payload-processing ./deploy/payload-processing \
    --namespace ${GATEWAY_NAMESPACE} \
    --dependency-update \
    --set upstreamBbr.inferenceGateway.name=${GATEWAY_NAME} \
    --set upstreamBbr.provider.istio.envoyFilter.anchorSubFilter=extensions.istio.io/wasmplugin/${GATEWAY_NAMESPACE}.kuadrant-${GATEWAY_NAME}
    ```

    > **Important**: The payload processing ext proc is attached to a Gateway.
    > As a mandatory requirement, `--namespace` must match the namespace where the
    > Gateway resource lives.

## Cleanup

1.  Uninstall `payload-processing` helm chart:

    ```bash
    helm uninstall payload-processing --namespace ${GATEWAY_NAMESPACE}
    ```

1.  Delete the ExternalModel CRD (optionally):

    ```bash
    kubectl delete -f https://raw.githubusercontent.com/opendatahub-io/models-as-a-service/refs/heads/main/deployment/base/maas-controller/crd/bases/maas.opendatahub.io_externalmodels.yaml
    ```
