#!/usr/bin/env python3
"""Probe Vertex AI path formats on the simulator."""
import urllib.request
import json

SIM = "http://llm-d-inference-sim.openshift-ingress.svc.cluster.local:8000"
body = json.dumps({
    "contents": [{"role": "user", "parts": [{"text": "hello"}]}],
    "generationConfig": {"maxOutputTokens": 5},
}).encode()

paths = [
    "/v1/projects/test/locations/us/publishers/google/models/gemini-pro-vertex:generateContent",
    "/v1/projects/p/locations/l/publishers/google/models/gemini-pro-vertex:generateContent",
    "/models/gemini-pro-vertex:generateContent",
    "/v1/models/gemini-pro-vertex:generateContent",
    "/vertex/gemini-pro-vertex",
    "/v1beta1/projects/p/locations/l/publishers/google/models/gemini-pro-vertex:generateContent",
    "/v1/chat/completions",
]

print("=== Probing Vertex AI paths on simulator ===")
print(f"Simulator: {SIM}")
print()

for p in paths:
    try:
        req = urllib.request.Request(SIM + p, data=body, headers={"Content-Type": "application/json"}, method="POST")
        resp = urllib.request.urlopen(req, timeout=5)
        data = resp.read().decode()[:200]
        print(f"  200 OK   {p}")
        print(f"           {data}")
    except urllib.error.HTTPError as e:
        print(f"  {e.code}      {p}")
    except Exception as e:
        print(f"  ERR      {p}: {e}")

# Also try with OpenAI format body to the /v1/chat/completions for gemini-pro-vertex model
print()
print("=== Trying OpenAI format with gemini-pro-vertex model name ===")
oai_body = json.dumps({
    "model": "gemini-pro-vertex",
    "messages": [{"role": "user", "content": "hello"}],
    "max_tokens": 5,
    "stream": False,
}).encode()
try:
    req = urllib.request.Request(SIM + "/v1/chat/completions", data=oai_body, headers={"Content-Type": "application/json"}, method="POST")
    resp = urllib.request.urlopen(req, timeout=5)
    data = json.loads(resp.read())
    print(f"  200 OK - gemini-pro-vertex responds to OpenAI format!")
    print(f"  Model in response: {data.get('model')}")
    print(f"  Response: {json.dumps(data, indent=2)[:300]}")
except urllib.error.HTTPError as e:
    print(f"  {e.code} - {e.read().decode()[:100]}")
except Exception as e:
    print(f"  ERR: {e}")
