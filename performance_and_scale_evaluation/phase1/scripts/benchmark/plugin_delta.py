import sys, os, re
from collections import defaultdict

def parse_metrics(path):
    result = {}
    if not os.path.exists(path):
        return result
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('#') or not line:
                continue
            m = re.match(r'(bbr_plugin_duration_seconds_(sum|count))\{(.+?)\}\s+([\d.e\-+]+)', line)
            if m:
                metric, kind, labels, val = m.group(1), m.group(2), m.group(3), float(m.group(4))
                result[(labels, kind)] = val
    return result

before_file = sys.argv[1]
after_file = sys.argv[2]
csv_path = sys.argv[3]
label = sys.argv[4]

before = parse_metrics(before_file)
after = parse_metrics(after_file)

plugins = set()
for (labels, kind) in after:
    plugins.add(labels)

write_header = not os.path.exists(csv_path) or os.path.getsize(csv_path) == 0
with open(csv_path, 'a') as f:
    if write_header:
        f.write("benchmark,extension_point,plugin_type,plugin_name,requests,avg_latency_us\n")
    for labels in sorted(plugins):
        s_before = before.get((labels, 'sum'), 0)
        c_before = before.get((labels, 'count'), 0)
        s_after = after.get((labels, 'sum'), 0)
        c_after = after.get((labels, 'count'), 0)
        ds = s_after - s_before
        dc = c_after - c_before
        if dc > 0:
            avg_us = (ds / dc) * 1_000_000
            ldict = dict(kv.split('=', 1) for kv in labels.split(','))
            ep = ldict.get('extension_point', '').strip('"')
            pt = ldict.get('plugin_type', '').strip('"')
            pn = ldict.get('plugin_name', '').strip('"')
            f.write(f"{label},{ep},{pt},{pn},{int(dc)},{avg_us:.1f}\n")
            print(f"    {ep}/{pn}: {avg_us:.1f}µs ({int(dc)} reqs)")
