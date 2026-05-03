import json, sys

d = json.load(sys.stdin)
b = d["benchmarks"][0]
m = b["metrics"]

rt = m["request_totals"]
rps = m["requests_per_second"]
lat = m["request_latency"]

total = rt["total"]
errored = rt["errored"]
successful = rt["successful"]

rps_mean = rps["successful"]["mean"]

lat_s = lat["successful"]
p50 = lat_s["percentiles"]["p50"] * 1000
p95 = lat_s["percentiles"]["p95"] * 1000
p99 = lat_s["percentiles"]["p99"] * 1000

print(f"Duration: {b['duration']:.1f}s  Warmup: {b['warmup_duration']:.1f}s")
print(f"Requests: {successful} successful, {errored} errors (total: {total})")
print(f"RPS:      {rps_mean:.2f} req/s")
print(f"Latency:  p50={p50:.1f}ms  p95={p95:.1f}ms  p99={p99:.1f}ms")
