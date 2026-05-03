#!/usr/bin/env python3
"""Demo: llm-d-inference-sim supports multiple provider API formats."""
import urllib.request
import json
import sys

SIM = "http://llm-d-inference-sim.openshift-ingress.svc.cluster.local:8000"

print("=" * 65)
print(" llm-d-inference-sim: Multi-Provider API Support Demo")
print("=" * 65)
print(f" Simulator: {SIM}")
print()

# First show available models
print("--- Available models (/v1/models) ---")
resp = urllib.request.urlopen(f"{SIM}/v1/models", timeout=10)
models = json.loads(resp.read())
for m in models["data"]:
    print(f"    {m['id']}")
print()

tests = [
    {
        "name": "OpenAI (native)",
        "format": "OpenAI Chat Completions",
        "path": "/v1/chat/completions",
        "model": "gpt-4o-openai",
        "headers": {"Content-Type": "application/json"},
        "body": {
            "model": "gpt-4o-openai",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 10,
            "stream": False,
        },
    },
    {
        "name": "Azure OpenAI",
        "format": "Azure Deployments API",
        "path": "/openai/deployments/gpt-4o-azure/chat/completions",
        "model": "gpt-4o-azure",
        "headers": {"Content-Type": "application/json"},
        "body": {
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 10,
            "stream": False,
        },
    },
    {
        "name": "Anthropic (Claude)",
        "format": "Anthropic Messages API",
        "path": "/v1/messages",
        "model": "claude-sonnet-anthropic",
        "headers": {
            "Content-Type": "application/json",
            "x-api-key": "fake-key",
            "anthropic-version": "2023-06-01",
        },
        "body": {
            "model": "claude-sonnet-anthropic",
            "messages": [{"role": "user", "content": "Say hello"}],
            "max_tokens": 10,
            "stream": False,
        },
    },
    {
        "name": "AWS Bedrock",
        "format": "Bedrock Converse API",
        "path": "/model/gpt-4o-bedrock/converse",
        "model": "gpt-4o-bedrock",
        "headers": {"Content-Type": "application/json"},
        "body": {
            "messages": [
                {"role": "user", "content": [{"text": "Say hello"}]}
            ],
            "inferenceConfig": {"maxTokens": 10},
        },
    },
    {
        "name": "Google Vertex AI (Gemini)",
        "format": "Vertex generateContent API",
        "path": "/v1/models/gemini-pro-vertex:generateContent",
        "model": "gemini-pro-vertex",
        "headers": {"Content-Type": "application/json"},
        "body": {
            "contents": [
                {"role": "user", "parts": [{"text": "Say hello"}]}
            ],
            "generationConfig": {"maxOutputTokens": 10},
        },
    },
]

print(f" Formats to test: {len(tests)}")
print("=" * 65)
print()

passed = 0
failed = 0
for i, t in enumerate(tests, 1):
    print(f"--- {i}/{len(tests)}: {t['name']} ({t['format']}) ---")
    print(f"    Model:    {t['model']}")
    print(f"    Endpoint: POST {t['path']}")
    url = SIM + t["path"]
    try:
        data = json.dumps(t["body"]).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=t["headers"], method="POST")
        resp = urllib.request.urlopen(req, timeout=10)
        status = resp.status
        body = json.loads(resp.read())
        print(f"    Status:   {status} OK")
        pretty = json.dumps(body, indent=4)
        lines = pretty.split("\n")
        for line in lines[:12]:
            print(f"    {line}")
        if len(lines) > 12:
            print(f"    ... ({len(lines)} lines total)")
        print(f"    RESULT:   PASS")
        passed += 1
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:200]
        print(f"    Status:   {e.code} {e.reason}")
        print(f"    Body:     {body}")
        print(f"    RESULT:   FAIL")
        failed += 1
    except Exception as e:
        print(f"    Error:    {e}")
        print(f"    RESULT:   FAIL")
        failed += 1
    print()

print("=" * 65)
print(f" SUMMARY: {passed}/{len(tests)} provider formats PASSED, {failed} FAILED")
if passed == len(tests):
    print(" All provider API formats are supported by the simulator!")
print("=" * 65)
