<script setup lang="ts">
import { NDatePicker, NText, NSpin } from "naive-ui";
import { IconChartLine } from "@tabler/icons-vue";
import { ref, computed, onMounted, watch, nextTick } from "vue";
import { useChart } from "../composables/useChart";
import { api, type ToolMetricsRow } from "../api/client";
import {
  Chart as ChartJS,
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Filler,
  Legend,
  Tooltip,
  type ChartDataset,
} from "chart.js";
import "chartjs-adapter-date-fns";
import { format, startOfDay, subDays } from "date-fns";

ChartJS.register(
  LineController,
  LineElement,
  PointElement,
  LinearScale,
  TimeScale,
  Filler,
  Legend,
  Tooltip,
);

const PALETTE = [
  "#6366f1",
  "#22c55e",
  "#f59e0b",
  "#ef4444",
  "#06b6d4",
  "#8b5cf6",
  "#ec4899",
  "#14b8a6",
  "#f97316",
  "#64748b",
];

const canvasRef = ref<HTMLCanvasElement>();
const loading = ref(false);
const error = ref<string | null>(null);
const rows = ref<ToolMetricsRow[]>([]);

const now = new Date();
const thirtyDaysAgo = startOfDay(subDays(now, 30));
const dateRange = ref<[number, number]>([
  thirtyDaysAgo.getTime(),
  now.getTime(),
]);

const startDate = computed(() =>
  format(new Date(dateRange.value[0]), "yyyy-MM-dd"),
);
const endDate = computed(() =>
  format(new Date(dateRange.value[1]), "yyyy-MM-dd"),
);

interface DayBucket {
  date: string;
  count: number;
}

const chartDataByTool = computed(() => {
  const byTool = new Map<string, Map<string, number>>();

  for (const r of rows.value) {
    const day = r.period_hour.substring(0, 10);
    if (!byTool.has(r.tool_name)) byTool.set(r.tool_name, new Map());
    const dayMap = byTool.get(r.tool_name)!;
    dayMap.set(day, (dayMap.get(day) || 0) + r.call_count);
  }

  const allDays = new Set<string>();
  for (const dayMap of byTool.values()) {
    for (const d of dayMap.keys()) allDays.add(d);
  }
  const sortedDays = [...allDays].sort();

  const toolEntries = [...byTool.entries()].sort((a, b) => {
    const totalA = [...a[1].values()].reduce((s, v) => s + v, 0);
    const totalB = [...b[1].values()].reduce((s, v) => s + v, 0);
    return totalB - totalA;
  });

  const toolDatasets: { name: string; buckets: DayBucket[] }[] = [];
  for (const [name, dayMap] of toolEntries) {
    const buckets: DayBucket[] = sortedDays.map((d) => ({
      date: d,
      count: dayMap.get(d) || 0,
    }));
    toolDatasets.push({ name, buckets });
  }

  const totalBuckets: DayBucket[] = sortedDays.map((d) => {
    let total = 0;
    for (const [, dayMap] of byTool) {
      total += dayMap.get(d) || 0;
    }
    return { date: d, count: total };
  });

  return { toolDatasets, totalBuckets, sortedDays };
});

function buildDatasets(): ChartDataset<"line">[] {
  const { toolDatasets, totalBuckets } = chartDataByTool.value;
  const datasets: ChartDataset<"line">[] = [];

  datasets.push({
    label: "Total",
    data: totalBuckets.map((b) => ({ x: b.date, y: b.count })),
    borderColor: "#94a3b8",
    backgroundColor: "rgba(148, 163, 184, 0.1)",
    borderWidth: 2,
    pointRadius: 3,
    pointHoverRadius: 5,
    tension: 0.3,
    fill: false,
  });

  toolDatasets.forEach((td, i) => {
    datasets.push({
      label: td.name,
      data: td.buckets.map((b) => ({ x: b.date, y: b.count })),
      borderColor: PALETTE[i % PALETTE.length],
      backgroundColor: PALETTE[i % PALETTE.length] + "1a",
      borderWidth: 2,
      pointRadius: 3,
      pointHoverRadius: 5,
      tension: 0.3,
      fill: false,
    });
  });

  return datasets;
}

const { create } = useChart<"line">(canvasRef, () => ({
  type: "line",
  data: {
    datasets: buildDatasets(),
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: "index",
      intersect: false,
    },
    plugins: {
      legend: {
        position: "bottom",
        labels: {
          usePointStyle: true,
          pointStyle: "circle",
          padding: 16,
        },
      },
      tooltip: {
        mode: "index",
        intersect: false,
      },
    },
    scales: {
      x: {
        type: "time",
        min: startDate.value,
        max: endDate.value,
        time: {
          unit: "day",
          tooltipFormat: "yyyy-MM-dd",
          displayFormats: {
            day: "MMM d",
          },
        },
        grid: {
          display: true,
        },
        ticks: {
          maxTicksLimit: 15,
          autoSkip: true,
        },
        title: {
          display: true,
          text: "Date",
        },
      },
      y: {
        beginAtZero: true,
        grid: {
          display: true,
        },
        ticks: {
          precision: 0,
        },
        title: {
          display: true,
          text: "Calls",
        },
      },
    },
  },
}));

async function fetchMetrics() {
  loading.value = true;
  error.value = null;
  try {
    console.log("[analytics] fetching", startDate.value, endDate.value);
    const result = await api.getToolMetrics(startDate.value, endDate.value);
    console.log("[analytics] got", result.metrics.length, "rows", result.metrics);
    rows.value = result.metrics;
    loading.value = false;
    console.log("[analytics] datasets:", JSON.stringify(buildDatasets().map((d) => ({ label: d.label, points: d.data?.length }))));
    await nextTick();
    create();
  } catch (e: any) {
    console.error("[analytics] fetch error:", e);
    error.value = e.message || "Failed to load metrics";
    loading.value = false;
  }
}

watch(dateRange, () => {
  fetchMetrics();
});

onMounted(fetchMetrics);

const shortcuts = {
  "Last 7 days": () => {
    const end = new Date();
    const start = startOfDay(subDays(end, 7));
    return [start.getTime(), end.getTime()] as [number, number];
  },
  "Last 30 days": () => {
    const end = new Date();
    const start = startOfDay(subDays(end, 30));
    return [start.getTime(), end.getTime()] as [number, number];
  },
  "Last 90 days": () => {
    const end = new Date();
    const start = startOfDay(subDays(end, 90));
    return [start.getTime(), end.getTime()] as [number, number];
  },
};
</script>

<template>
  <div class="settings-analytics">
    <div class="section-header">
      <IconChartLine :size="24" class="section-icon" />
      <NText class="section-title">Analytics</NText>
    </div>

    <div class="chart-panel">
      <div class="chart-panel-header">
        <NText strong>Tool Calls</NText>
      </div>
      <div class="controls">
        <NDatePicker
          v-model:value="dateRange"
          type="daterange"
          clearable
          :shortcuts="shortcuts"
          start-placeholder="Start date"
          end-placeholder="End date"
          style="width: 360px"
        />
      </div>
      <div v-if="loading" class="chart-loading">
        <NSpin size="medium" />
      </div>
      <div v-else-if="error" class="chart-error">
        <NText type="error">{{ error }}</NText>
      </div>
      <div v-else-if="rows.length === 0" class="chart-empty">
        <NText depth="3">No metrics data available for this period.</NText>
      </div>
      <div v-else class="chart-container">
        <canvas ref="canvasRef"></canvas>
      </div>
    </div>
  </div>
</template>

<style scoped>
.settings-analytics {
  padding: 24px;
  max-width: 960px;
  width: 100%;
}

.section-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}

.section-icon {
  color: var(--n-primary-color);
}

.section-title {
  font-size: 1.3em;
  font-weight: 600;
}

.chart-panel {
  border: 1px solid var(--n-border-color, #e0e0e0);
  border-radius: 6px;
  padding: 16px;
}

.chart-panel-header {
  margin-bottom: 12px;
}

.chart-panel-header span {
  font-size: 1.1em;
}

.controls {
  margin-bottom: 16px;
}

.chart-container {
  position: relative;
  height: 380px;
}

.chart-loading,
.chart-error,
.chart-empty {
  display: flex;
  justify-content: center;
  align-items: center;
  height: 380px;
}
</style>
