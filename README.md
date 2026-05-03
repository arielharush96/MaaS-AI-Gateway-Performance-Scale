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
