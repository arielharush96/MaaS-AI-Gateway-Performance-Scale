#!/usr/bin/env python3
"""Test: compare direct simulator vs gateway for Anthropic to find the anomaly root cause."""
import urllib.request, json, time, sys

SIM = "http://llm-d-inference-sim.openshift-ingress.svc.cluster.local:8000"
GW = "http://maas-default-gateway-data-science-gateway-class.openshift-ingress.svc.cluster.local:80"

try:
    TOKEN = open("/var/run/secrets/guidellm/token").read().strip()
except:
    TOKEN = "test-token"

BODY = json.dumps({
    "model": "claude-sonnet-anthropic",
    "messages": [{"role": "user", "content": "hello world"}],
    "max_tokens": 64,
    "stream": False
}).encode()

def timed_request(url, headers, label):
    req = urllib.request.Request(url, data=BODY, headers=headers, method="POST")
    start = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=30)
        elapsed = (time.time() - start) * 1000
        data = resp.read().decode()
        print(f"\n=== {label} ===")
        print(f"  URL: {url}")
        print(f"  Status: {resp.status}")
        print(f"  Time: {elapsed:.0f}ms")
        try:
            j = json.loads(data)
            print(f"  Response keys: {list(j.keys())}")
            if "usage" in j:
                print(f"  Usage: {j['usage']}")
            if "choices" in j:
                c = j["choices"][0]
                msg = c.get("message", {}).get("content", "")[:80]
                print(f"  Content (first 80 chars): {msg}")
            elif "content" in j:
                c = j["content"][0] if j["content"] else {}
                print(f"  Content: {c.get('text', '')[:80]}")
            print(f"  Full response (first 500 chars): {data[:500]}")
        except:
            print(f"  Raw response: {data[:500]}")
    except Exception as e:
        elapsed = (time.time() - start) * 1000
        print(f"\n=== {label} ===")
        print(f"  URL: {url}")
        print(f"  Time: {elapsed:.0f}ms")
        print(f"  ERROR: {e}")
        if hasattr(e, 'read'):
            print(f"  Body: {e.read().decode()[:500]}")

print("=" * 60)
print("Testing Anthropic anomaly: direct vs gateway")
print("=" * 60)

timed_request(
    f"{SIM}/v1/chat/completions",
    {"Content-Type": "application/json"},
    "1. DIRECT to sim (OpenAI path /v1/chat/completions)"
)

timed_request(
    f"{SIM}/v1/messages",
    {"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
    "2. DIRECT to sim (Anthropic path /v1/messages, OpenAI body)"
)

ANTHROPIC_BODY = json.dumps({
    "model": "claude-sonnet-anthropic",
    "messages": [{"role": "user", "content": "hello world"}],
    "max_tokens": 64,
}).encode()

req3 = urllib.request.Request(
    f"{SIM}/v1/messages",
    data=ANTHROPIC_BODY,
    headers={"Content-Type": "application/json", "anthropic-version": "2023-06-01"},
    method="POST"
)
start = time.time()
try:
    resp = urllib.request.urlopen(req3, timeout=30)
    elapsed = (time.time() - start) * 1000
    data = resp.read().decode()
    print(f"\n=== 3. DIRECT to sim (Anthropic path + Anthropic body) ===")
    print(f"  URL: {SIM}/v1/messages")
    print(f"  Time: {elapsed:.0f}ms")
    print(f"  Response (500 chars): {data[:500]}")
except Exception as e:
    elapsed = (time.time() - start) * 1000
    print(f"\n=== 3. DIRECT to sim (Anthropic path + Anthropic body) ===")
    print(f"  Time: {elapsed:.0f}ms")
    print(f"  ERROR: {e}")
    if hasattr(e, 'read'):
        print(f"  Body: {e.read().decode()[:500]}")

timed_request(
    f"{GW}/claude-sonnet-anthropic/v1/chat/completions",
    {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"},
    "4. GATEWAY (OpenAI path, BBR translates to Anthropic)"
)

print("\n" + "=" * 60)
print("If test 1 takes ~65ms and test 4 takes ~5ms,")
print("the simulator doesn't understand the translated format.")
print("=" * 60)
