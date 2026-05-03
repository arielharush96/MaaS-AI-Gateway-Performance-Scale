#!/usr/bin/env python3
import urllib.request, json, time

SIM = "http://llm-d-inference-sim.openshift-ingress.svc.cluster.local:8000"
GW  = "http://maas-default-gateway-data-science-gateway-class.openshift-ingress.svc.cluster.local:80"

try:
    TOKEN = open("/var/run/secrets/guidellm/token").read().strip()
except Exception:
    TOKEN = "dummy"

body = json.dumps({
    "model": "claude-sonnet-anthropic",
    "messages": [{"role": "user", "content": "Tell me a story about a brave knight"}],
    "max_tokens": 64,
    "stream": False
}).encode()

def send(label, url, tok=None):
    hdrs = {"Content-Type": "application/json"}
    if tok:
        hdrs["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(url, data=body, headers=hdrs, method="POST")
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        data = json.loads(resp.read())
        ms = (time.time() - t0) * 1000
        ot = data.get("usage", {}).get("completion_tokens", "?")
        pt = data.get("usage", {}).get("prompt_tokens", "?")
        print(label)
        print("  Time: %.0fms  prompt_tokens=%s  output_tokens=%s" % (ms, pt, ot))
        print("  Response keys: %s" % list(data.keys()))
        c = data.get("choices", [{}])[0].get("message", {}).get("content", "")[:80]
        if not c and "content" in data:
            c = str(data.get("content", ""))[:80]
        print("  Content: %s" % c)
    except Exception as e:
        ms = (time.time() - t0) * 1000
        print(label)
        print("  ERROR (%.0fms): %s" % (ms, e))
        try:
            print("  Body: %s" % e.read().decode()[:200])
        except Exception:
            pass
    print()

print("=" * 70)
print("TEST: Body-based routing vs Path-prefix routing vs Baseline")
print("Model: claude-sonnet-anthropic, max_tokens=64")
print("=" * 70)
print()

send("1. BASELINE (direct to sim /v1/chat/completions)",
     SIM + "/v1/chat/completions")

send("2. GATEWAY path-prefix (current broken approach)",
     GW + "/claude-sonnet-anthropic/v1/chat/completions", TOKEN)

send("3. GATEWAY body-based (correct real-user flow)",
     GW + "/v1/chat/completions", TOKEN)

print("=" * 70)
print("If tests 1 and 3 show similar output_tokens => body-based routing works!")
print("If test 2 shows different output_tokens => confirms path-prefix bug.")
print("=" * 70)
