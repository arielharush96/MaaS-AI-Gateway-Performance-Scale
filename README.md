## MaaS AI Gateway — Performance & Scale Evaluation
The MaaS AI Gateway is an LLM request manager that sits between clients and inference backends. It accepts requests in OpenAI Chat Completions format and translates them on-the-fly to native provider APIs (Anthropic, Vertex AI, Azure OpenAI, Bedrock) via a BBR plugin chain. The gateway runs as an Envoy layer and handles model routing, API format translation, and credential injection all transparently to the client.

This repository contains the manifests and scripts used to measure the payload processing performance:
Baseline (A): GuideLLM → llm-d-inference-sim (direct, OpenAI format)
Gateway (B): GuideLLM → MaaS AI Gateway  → llm-d-inference-sim
GuideLLM (v0.6.0) is the load generator. It sends OpenAI-format requests at varying concurrency levels and payload sizes. llm-d-inference-sim is a multi-provider LLM simulator that responds to all five provider API formats with deterministic token counts (--deterministic-tokens), fixed TTFT (1ms), and fixed inter-token latency (1ms). By comparing latency and throughput between the two paths, we isolate the exact cost of routing through the gateway.
