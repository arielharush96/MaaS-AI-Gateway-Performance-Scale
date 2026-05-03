#!/usr/bin/env python3
"""
Prometheus monitor — collects CPU, memory, and network metrics for all
MaaS stack pods every INTERVAL seconds. Writes per-metric CSV files and
tracks which benchmark is active via /results/.current_benchmark.
"""
import urllib.request, urllib.parse, ssl, json, os, sys, time, signal

PROM_URL = "https://thanos-querier.openshift-monitoring.svc.cluster.local:9091/api/v1/query"
TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
RESULTS_DIR = "/results/prometheus"
INTERVAL = 5
STOP_FILE = "/results/.monitor_stop"
NAMESPACE = "openshift-ingress"
POD_REGEX = "payload-processing.*|maas-default.*|llm-d-inference-sim.*|kube-auth-proxy.*|data-science-gateway.*"

QUERIES = {
    "cpu": f'sum(rate(container_cpu_usage_seconds_total{{namespace="{NAMESPACE}",pod=~"{POD_REGEX}",container!="POD",container!=""}}[30s])) by (pod)',
    "memory": f'container_memory_working_set_bytes{{namespace="{NAMESPACE}",pod=~"{POD_REGEX}",container!="POD",container!=""}}',
    "net_rx": f'sum(rate(container_network_receive_bytes_total{{namespace="{NAMESPACE}",pod=~"{POD_REGEX}"}}[30s])) by (pod)',
    "net_tx": f'sum(rate(container_network_transmit_bytes_total{{namespace="{NAMESPACE}",pod=~"{POD_REGEX}"}}[30s])) by (pod)',
}

shutdown = False
def handle_signal(signum, frame):
    global shutdown
    shutdown = True
signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

os.makedirs(RESULTS_DIR, exist_ok=True)

token = open(TOKEN_PATH).read().strip()
ctx = ssl.create_default_context()
ctx.check_hostname = False
ctx.verify_mode = ssl.CERT_NONE

csv_files = {}
for metric_name in QUERIES:
    path = os.path.join(RESULTS_DIR, f"{metric_name}.csv")
    f = open(path, "w")
    f.write("timestamp,pod,value\n")
    f.flush()
    csv_files[metric_name] = f

benchmark_label_path = "/results/.current_benchmark"
bench_csv = open(os.path.join(RESULTS_DIR, "benchmark_markers.csv"), "w")
bench_csv.write("timestamp,benchmark\n")
bench_csv.flush()

print(f"[prom_monitor] Started. Interval={INTERVAL}s, writing to {RESULTS_DIR}")
print(f"[prom_monitor] Monitoring pods matching: {POD_REGEX}")
sys.stdout.flush()

cycle = 0
last_benchmark = ""
while not shutdown and not os.path.exists(STOP_FILE):
    ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    cur_bench = ""
    if os.path.exists(benchmark_label_path):
        try:
            cur_bench = open(benchmark_label_path).read().strip()
        except:
            pass
    if cur_bench != last_benchmark:
        bench_csv.write(f"{ts},{cur_bench}\n")
        bench_csv.flush()
        last_benchmark = cur_bench
        if cur_bench:
            print(f"[prom_monitor] Benchmark: {cur_bench}")
            sys.stdout.flush()

    for metric_name, query in QUERIES.items():
        try:
            url = PROM_URL + "?" + urllib.parse.urlencode({"query": query})
            req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
            resp = urllib.request.urlopen(req, context=ctx, timeout=10)
            data = json.loads(resp.read())
            if data.get("status") == "success":
                for result in data["data"]["result"]:
                    pod = result["metric"].get("pod", "unknown")
                    val = result["value"][1]
                    csv_files[metric_name].write(f"{ts},{pod},{val}\n")
                csv_files[metric_name].flush()
        except Exception as e:
            if cycle == 0:
                print(f"[prom_monitor] WARN: {metric_name} query failed: {e}")
                sys.stdout.flush()

    cycle += 1
    if cycle % 60 == 0:
        print(f"[prom_monitor] {cycle} samples collected ({cycle * INTERVAL}s elapsed)")
        sys.stdout.flush()

    for _ in range(INTERVAL * 10):
        if shutdown or os.path.exists(STOP_FILE):
            break
        time.sleep(0.1)

for f in csv_files.values():
    f.close()
bench_csv.close()
print(f"[prom_monitor] Stopped after {cycle} samples.")
sys.stdout.flush()
