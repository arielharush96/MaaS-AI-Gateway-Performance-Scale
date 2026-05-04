import json, sys, os

bench_dir = sys.argv[1]
name = sys.argv[2]
mode = sys.argv[3]
pt = sys.argv[4]
ot = sys.argv[5]
conc = sys.argv[6]
turns = sys.argv[7]
csv_path = sys.argv[8]
replicas = sys.argv[9]

json_file = os.path.join(bench_dir, 'benchmarks.json')
if not os.path.exists(json_file):
    print(f"  ERROR: {json_file} not found")
    sys.exit(1)

with open(json_file) as f:
    data = json.load(f)

b = data['benchmarks'][0]
m = b['metrics']
rt = m['request_totals']
rps = m['requests_per_second']['successful']['mean']
lat = m['request_latency']['successful']
p50 = lat['percentiles']['p50'] * 1000
p95 = lat['percentiles']['p95'] * 1000
p99 = lat['percentiles']['p99'] * 1000
mean_lat = lat['mean'] * 1000
dur = b['duration']

row = f"{replicas},{name},{mode},{pt},{ot},{turns},{conc},{rt['successful']},{rt['errored']},{rps:.2f},{mean_lat:.1f},{p50:.1f},{p95:.1f},{p99:.1f},{dur:.1f}"
with open(csv_path, 'a') as f:
    f.write(row + '\n')

print(f"  {mode} (r={replicas}): RPS={rps:.1f} mean={mean_lat:.1f}ms p50={p50:.1f}ms p95={p95:.1f}ms p99={p99:.1f}ms")

errored = rt.get('errored', 0)
if errored > 0:
    errors_out = os.path.join(bench_dir, 'errors.json')
    error_data = []
    for req in b.get('requests', []):
        if req.get('errored'):
            error_data.append({
                'start': req.get('start'),
                'end': req.get('end'),
                'error': req.get('info', {}).get('error'),
                'traceback': req.get('info', {}).get('traceback'),
                'status': req.get('info', {}).get('status'),
            })
    if error_data:
        with open(errors_out, 'w') as f:
            json.dump({'benchmark': name, 'mode': mode, 'replicas': replicas,
                       'total_errored': errored, 'errors': error_data}, f, indent=2)
        print(f"  Saved {len(error_data)} error details to {errors_out}")

os.remove(json_file)
