import {
  BarChart,
  LineChart,
  Divider,
  Grid,
  H1,
  H2,
  H3,
  Row,
  Stack,
  Stat,
  Table,
  Text,
  Card,
  CardBody,
  CardHeader,
  useHostTheme,
} from 'cursor/canvas';

const CONC = ["2", "4", "8", "16", "32", "64", "128", "256", "512", "1024"];
const CONC_REDUCED = ["8", "64", "512"];

// ── p95 latency data (small payload, 32/64 tokens) ──
const smallP95 = {
  openai:    { base: [72.4,72.8,74.3,76.2,80.8,86.8,93.2,93.4,93.3,93.3], gw: [76.0,76.7,78.7,81.1,86.2,91.7,99.8,99.4,100.5,102.9] },
  azure:     { base: [72.4,72.7,74.6,78.0,82.4,87.0,93.5,93.5,93.6,93.6], gw: [76.5,77.5,79.1,81.5,85.6,91.9,98.9,100.1,101.0,167.5] },
  bedrock:   { base: [72.2,72.8,74.7,78.4,84.0,87.8,93.6,93.4,93.5,93.4], gw: [76.2,78.0,78.9,82.3,86.0,92.1,99.6,100.4,101.9,105.2] },
  anthropic: { base: [70.9,71.6,73.1,74.7,79.3,84.2,93.1,93.0,93.4,93.3], gw: [74.8,76.1,77.5,79.7,84.8,90.0,99.3,100.1,101.1,102.6] },
//  vertex:    { base: [70.8,71.4,73.1,75.4,79.0,83.8,92.4,92.6,92.5,92.6], gw: [74.9,76.1,77.6,80.9,85.2,91.0,96.6,97.4,100.2,103.1] },
};

// ── p95 latency data (medium payload, 256/512 tokens) ──
const medP95 = {
  openai:    { base: [555.2,559.9,560.2,570.3,585.7,607.1,648.9,710.5,778.5,781.6], gw: [568.2,561.1,565.0,577.5,592.3,630.9,656.1,720.6,795.4,800.3] },
  anthropic: { base: [556.2,557.3,559.9,571.6,585.6,613.0,659.1,734.0,789.3,789.4], gw: [559.7,561.3,563.9,577.9,591.9,616.1,666.6,725.1,796.3,800.2] },
  vertex:    { base: [554.4,556.0,560.2,574.0,588.0,609.4,657.5,728.8,790.3,789.2], gw: [558.5,563.2,564.8,577.0,592.0,613.5,663.7,724.9,803.3,795.5] },
};

// ── RPS data (small payload) ──
const smallRPS = {
  openai:    { base: [27.8,55.1,108.4,212.0,401.7,747.3,1103.4,1082.6,1064.6,1099.0], gw: [26.4,52.3,102.9,200.4,380.4,708.7,1031.4,1030.9,1030.7,993.8] },
  azure:     { base: [27.6,55.2,108.2,208.0,394.8,743.4,1124.7,1120.7,1106.5,1109.9], gw: [26.2,52.0,101.9,199.5,380.6,701.2,1002.6,988.6,1014.2,1010.8] },
  bedrock:   { base: [27.7,55.1,107.9,207.1,386.1,732.5,1100.2,1105.8,1114.3,1098.1], gw: [26.4,52.0,102.7,196.3,377.2,695.6,1002.7,1006.0,968.3,983.0] },
  anthropic: { base: [28.2,56.3,109.7,213.2,404.3,757.5,1083.5,1073.9,1090.1,1076.1], gw: [26.9,52.8,103.5,201.1,380.5,706.3,1004.2,1000.7,1010.3,1001.9] },
  vertex:    { base: [28.3,56.2,110.3,214.5,408.2,764.8,1074.3,1083.8,1060.7,1070.6], gw: [26.7,52.9,104.2,201.3,382.6,706.3,981.9,990.0,976.8,989.6] },
};

// ── p95 overhead (ms) per provider per payload ──
const overheadTable = {
  openai:    { small: 6.65, medium: 1.68, large: 0.45 },
  azure:     { small: 13.20, medium: 0.62, large: 0.02 },
  bedrock:   { small: 6.61, medium: 0.78, large: 0.39 },
  anthropic: { small: 6.14, medium: 0.48, large: 0.04 },
  vertex:    { small: 7.17, medium: 0.76, large: 0.30 },
};

// ── Resource utilization ──
const cpuData = {
  labels: ["BBR Sidecar", "Envoy Gateway", "Simulator"],
  avg: [0.039, 0.051, 0.173],
  p95: [0.344, 0.413, 0.784],
  max: [0.820, 0.873, 2.149],
};

const memData = {
  labels: ["BBR Sidecar", "Envoy Gateway", "Simulator"],
  avg: [99, 245, 462],
  p95: [113, 252, 588],
  max: [144, 257, 615],
};

const netRxData = {
  labels: ["BBR Sidecar", "Envoy Gateway", "Simulator"],
  avg: [285, 502, 364],
  p95: [1788, 3289, 1233],
};

const netTxData = {
  labels: ["BBR Sidecar", "Envoy Gateway", "Simulator"],
  avg: [240, 597, 189],
  p95: [1456, 3733, 881],
};

const PROVIDERS = [
  { key: "openai", label: "OpenAI", type: "nil" },
  { key: "azure", label: "Azure", type: "nil" },
  { key: "bedrock", label: "Bedrock", type: "nil" },
  { key: "anthropic", label: "Anthropic", type: "full" },
  { key: "vertex", label: "Vertex", type: "full" },
] as const;

function ohMs(base: number[], gw: number[]): number[] {
  return base.map((b, i) => Math.round((gw[i] - b) * 10) / 10);
}

export default function GatewayPerformanceReport() {
  const theme = useHostTheme();

  return (
    <Stack gap={24} style={{ maxWidth: 1100 }}>
      <Stack gap={4}>
        <H1>MaaS AI Gateway — Performance Report</H1>
        <Text tone="secondary">
          Phase 1 overhead benchmarks: GuideLLM v0.6.0, llm-d-inference-sim (deterministic, TTFT=1ms, ITL=1ms), non-streaming.
          158 A/B pairs across 5 providers, 3 payload sizes, 10 concurrency levels.
        </Text>
      </Stack>

      <Grid columns={5} gap={12}>
        <Stat label="OpenAI (nil)" value="+2.3%" />
        <Stat label="Azure (nil)" value="+7.0%" />
        <Stat label="Bedrock (nil)" value="+3.7%" />
        <Stat label="Anthropic (full)" value="+1.6%" />
        <Stat label="Vertex (full)" value="+1.9%" />
      </Grid>
      <Text tone="secondary" size="small">Mean p95 latency overhead across all payload sizes and concurrency levels. Zero errors.</Text>

      <Divider />

      {/* ── ALL PROVIDERS: p95 OVERHEAD ── */}
      <H2>p95 Latency Overhead — All Providers</H2>
      <Text tone="secondary" size="small">Gateway p95 minus Baseline p95 (ms). Small payload (32/64 tokens).</Text>
      <LineChart
        categories={CONC}
        series={PROVIDERS.map(p => ({
          name: p.label,
          data: ohMs(smallP95[p.key].base, smallP95[p.key].gw),
        }))}
        height={320}
        valueSuffix="ms"
      />

      <Divider />

      {/* ── PER-PROVIDER: SMALL PAYLOAD p95 ── */}
      <H2>p95 Latency: Baseline vs Gateway</H2>
      <Text tone="secondary" size="small">Small payload (32 prompt / 64 output tokens)</Text>
      <Grid columns={2} gap={16}>
        {PROVIDERS.map(p => (
          <Card key={p.key}>
            <CardHeader>{p.label} ({p.type}-translator)</CardHeader>
            <CardBody>
              <LineChart
                categories={CONC}
                series={[
                  { name: "Baseline", data: smallP95[p.key].base },
                  { name: "Gateway", data: smallP95[p.key].gw },
                ]}
                height={200}
                valueSuffix="ms"
              />
            </CardBody>
          </Card>
        ))}
      </Grid>

      <Divider />

      {/* ── MEDIUM PAYLOAD p95 (full providers + openai) ── */}
      <H2>p95 Latency: Medium Payload (256/512 tokens)</H2>
      <Text tone="secondary" size="small">Full concurrency range. Overhead is minimal at this payload size.</Text>
      <Grid columns={3} gap={16}>
        {(["openai", "anthropic", "vertex"] as const).map(key => {
          const p = PROVIDERS.find(x => x.key === key)!;
          return (
            <Card key={key}>
              <CardHeader>{p.label}</CardHeader>
              <CardBody>
                <LineChart
                  categories={CONC}
                  series={[
                    { name: "Baseline", data: medP95[key].base },
                    { name: "Gateway", data: medP95[key].gw },
                  ]}
                  height={200}
                  valueSuffix="ms"
                />
              </CardBody>
            </Card>
          );
        })}
      </Grid>

      <Divider />

      {/* ── RPS ── */}
      <H2>Throughput (RPS) — Small Payload</H2>
      <Text tone="secondary" size="small">Requests per second, baseline vs gateway. Small payload (32/64 tokens).</Text>
      <Grid columns={2} gap={16}>
        {PROVIDERS.map(p => (
          <Card key={p.key}>
            <CardHeader>{p.label}</CardHeader>
            <CardBody>
              <LineChart
                categories={CONC}
                series={[
                  { name: "Baseline", data: smallRPS[p.key].base },
                  { name: "Gateway", data: smallRPS[p.key].gw },
                ]}
                height={200}
                valueSuffix=" rps"
              />
            </CardBody>
          </Card>
        ))}
      </Grid>

      <Divider />

      {/* ── OVERHEAD SUMMARY TABLE ── */}
      <H2>Mean p95 Overhead by Provider and Payload</H2>
      <Table
        headers={["Provider", "Translator", "Small (32/64)", "Medium (256/512)", "Large (1024/1024)"]}
        rows={PROVIDERS.map(p => [
          p.label,
          p.type,
          `+${overheadTable[p.key].small.toFixed(1)}%`,
          `+${overheadTable[p.key].medium.toFixed(1)}%`,
          `+${overheadTable[p.key].large.toFixed(1)}%`,
        ])}
        columnAlign={["left", "left", "right", "right", "right"]}
      />

      <Divider />

      {/* ── RESOURCE UTILIZATION ── */}
      <H2>Resource Utilization</H2>
      <Text tone="secondary" size="small">Prometheus metrics collected every 5s across all benchmark runs.</Text>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H3>CPU (cores)</H3>
          <BarChart
            categories={cpuData.labels}
            series={[
              { name: "Avg", data: cpuData.avg },
              { name: "p95", data: cpuData.p95 },
              { name: "Max", data: cpuData.max },
            ]}
            height={240}
            valueSuffix=" cores"
          />
        </Stack>

        <Stack gap={8}>
          <H3>Memory (MB)</H3>
          <BarChart
            categories={memData.labels}
            series={[
              { name: "Avg", data: memData.avg },
              { name: "p95", data: memData.p95 },
              { name: "Max", data: memData.max },
            ]}
            height={240}
            valueSuffix=" MB"
          />
        </Stack>

        <Stack gap={8}>
          <H3>Network RX (KB/s)</H3>
          <BarChart
            categories={netRxData.labels}
            series={[
              { name: "Avg", data: netRxData.avg },
              { name: "p95", data: netRxData.p95 },
            ]}
            height={240}
            valueSuffix=" KB/s"
          />
        </Stack>

        <Stack gap={8}>
          <H3>Network TX (KB/s)</H3>
          <BarChart
            categories={netTxData.labels}
            series={[
              { name: "Avg", data: netTxData.avg },
              { name: "p95", data: netTxData.p95 },
            ]}
            height={240}
            valueSuffix=" KB/s"
          />
        </Stack>
      </Grid>

      <Divider />

      <Table
        headers={["Component", "CPU Avg", "CPU p95", "Mem Avg", "Mem p95", "Net RX p95", "Net TX p95"]}
        rows={[
          ["BBR Sidecar", "0.039 cores", "0.344 cores", "99 MB", "113 MB", "1,788 KB/s", "1,456 KB/s"],
          ["Envoy Gateway", "0.051 cores", "0.413 cores", "245 MB", "252 MB", "3,289 KB/s", "3,733 KB/s"],
          ["Simulator", "0.173 cores", "0.784 cores", "462 MB", "588 MB", "1,233 KB/s", "881 KB/s"],
        ]}
        columnAlign={["left", "right", "right", "right", "right", "right", "right"]}
      />

      <Divider />
      <Text tone="secondary" size="small">
        Data sources: OpenAI from 2026-04-30, Azure/Bedrock from multi-provider-v2, Anthropic/Vertex from v2d (deterministic simulator).
        All runs on OpenShift, single BBR replica, non-streaming.
      </Text>
    </Stack>
  );
}
