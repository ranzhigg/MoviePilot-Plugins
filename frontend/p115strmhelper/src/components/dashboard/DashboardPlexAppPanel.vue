<template>
  <div class="dashboard-widget dashboard-widget--plex-app">
    <v-card
      :flat="!config?.attrs?.border"
      :loading="loading"
      class="strm-dash-card plex-app-dash-card fill-height d-flex flex-column dashboard-grid-fill"
    >
      <v-card-item class="plex-app-dash-card__head pb-2">
        <v-card-title class="d-flex flex-wrap align-center gap-2 ps-0">
          <v-icon icon="mdi-database-search" color="primary" size="small" />
          <span>{{ config?.attrs?.title || "Plex 媒体信息补全" }}</span>
          <v-chip size="small" :color="statusColor" variant="tonal">
            {{ statusLabel }}
          </v-chip>
        </v-card-title>
        <v-card-subtitle v-if="config?.attrs?.subtitle">
          {{ config.attrs.subtitle }}
        </v-card-subtitle>
        <template v-slot:append>
          <div class="d-flex align-center flex-shrink-0">
            <v-btn
              size="small"
              color="primary"
              variant="tonal"
              prepend-icon="mdi-database-search"
              class="dashboard-grid-no-drag mr-1"
              :loading="triggerLoading"
              :disabled="busy"
              @click="triggerFullScan"
            >
              全库扫描/补全
            </v-btn>
            <v-btn
              icon
              size="small"
              variant="text"
              class="dashboard-grid-no-drag"
              :loading="loading"
              @click="loadResult()"
            >
              <v-icon>mdi-refresh</v-icon>
            </v-btn>
          </div>
        </template>
      </v-card-item>

      <v-card-text class="flex-grow-1 pa-3 pt-0 plex-app-dash-card__body">
        <div v-if="loading && !loaded" class="text-center py-8">
          <v-progress-circular indeterminate color="primary" size="40" />
        </div>

        <v-alert v-else-if="error" type="error" variant="tonal" density="compact" class="mb-3">
          {{ error }}
        </v-alert>

        <template v-else>
          <v-alert v-if="notice" :type="noticeType" variant="tonal" density="compact" class="mb-3">
            {{ notice }}
          </v-alert>

          <v-alert v-if="!hasFullStats" type="info" variant="tonal" density="compact" class="mb-3">
            当前历史结果只有待补候选数，没有全库总量；点击“全库扫描/补全”后会写入完整统计。
          </v-alert>

          <v-row dense class="plex-app-stat-row mb-2">
            <v-col v-for="card in statCards" :key="card.label" cols="12" sm="6" md="3">
              <v-card variant="outlined" class="plex-app-stat-card h-100">
                <v-card-text class="py-3">
                  <div class="text-caption text-medium-emphasis">{{ card.label }}</div>
                  <div class="text-h5 font-weight-bold mt-1" :class="`text-${card.color}`">
                    {{ card.value }}
                  </div>
                  <div class="text-caption text-medium-emphasis mt-1">{{ card.hint }}</div>
                </v-card-text>
              </v-card>
            </v-col>
          </v-row>

          <v-card variant="outlined" class="plex-app-summary-card mb-3">
            <v-card-text class="py-3">
              <div class="d-flex flex-wrap align-center justify-space-between gap-2 mb-2">
                <div class="text-subtitle-2 font-weight-medium">本次扫描明细</div>
                <v-chip size="small" variant="tonal" color="primary">
                  {{ scanModeLabel }}
                </v-chip>
              </div>
              <div class="d-flex flex-wrap gap-2">
                <v-chip size="small" variant="tonal">扫描候选 {{ formatNumber(candidateCount) }}</v-chip>
                <v-chip size="small" color="success" variant="tonal">
                  解析成功 {{ formatNumber(resolvedCount) }}
                </v-chip>
                <v-chip size="small" color="warning" variant="tonal">
                  未解析 {{ formatNumber(unresolvedCount) }}
                </v-chip>
                <v-chip size="small" :color="writeFailedCount ? 'error' : 'success'" variant="tonal">
                  写入失败 {{ formatNumber(writeFailedCount) }}
                </v-chip>
              </div>
              <div v-if="progressTotal > 0 && busy" class="mt-3">
                <div class="d-flex justify-space-between text-caption text-medium-emphasis mb-1">
                  <span>{{ progressLabel }}</span>
                  <span>{{ progressDone }}/{{ progressTotal }}</span>
                </div>
                <v-progress-linear :model-value="progressPercent" color="primary" rounded />
              </div>
            </v-card-text>
          </v-card>

          <div class="d-flex flex-wrap align-center justify-space-between gap-2 text-caption text-medium-emphasis">
            <span v-if="lastScanDisplay">最近扫描：{{ lastScanDisplay }}</span>
            <span v-else>尚未完成一次全库统计</span>
            <span v-if="selectedSectionCount">已选媒体库：{{ selectedSectionCount }} 个</span>
          </div>
        </template>
      </v-card-text>

      <v-divider v-if="allowRefresh" />
      <v-card-actions v-if="allowRefresh" class="px-3 py-2 refresh-actions">
        <span class="text-caption text-disabled">{{ lastRefreshedDisplay }}</span>
        <v-spacer />
        <v-btn
          icon
          variant="text"
          size="small"
          class="dashboard-grid-no-drag"
          :loading="loading"
          @click="loadResult()"
        >
          <v-icon size="small">mdi-refresh</v-icon>
        </v-btn>
      </v-card-actions>
    </v-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from "vue";
import { P115_STRM_HELPER_PLUGIN_ID } from "../../utils/pluginId.js";

const props = defineProps({
  api: {
    type: [Object, Function],
    required: true,
  },
  config: {
    type: Object,
    default: () => ({ attrs: {} }),
  },
  allowRefresh: {
    type: Boolean,
    default: false,
  },
});

const loading = ref(false);
const loaded = ref(false);
const triggerLoading = ref(false);
const error = ref("");
const notice = ref("");
const noticeType = ref("success");
const response = ref(null);
const lastRefreshedAt = ref(null);
let pollTimer = null;

const completion = computed(() => response.value?.completion || {});
const dashboard = computed(() => response.value?.dashboard || {});
const lastResult = computed(() => response.value?.result || {});
const progress = computed(() => completion.value?.progress || {});
const busy = computed(() => ["queued", "running"].includes(String(completion.value?.status || "")));
const countValue = (value, fallback = 0) => {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
};

const liveCount = (name) => {
  const value = busy.value
    ? dashboard.value[name] ?? progress.value[name] ?? lastResult.value[name]
    : dashboard.value[name] ?? lastResult.value[name];
  return countValue(value);
};

const totalStrmParts = computed(() => {
  const value = busy.value
    ? progress.value.total_strm_parts ?? dashboard.value.total_strm_parts ?? lastResult.value.total_strm_parts
    : dashboard.value.total_strm_parts ?? lastResult.value.total_strm_parts;
  return value === null || value === undefined ? null : countValue(value);
});
const hasFullStats = computed(
  () => totalStrmParts.value !== null,
);
const candidateCount = computed(() => {
  const value = busy.value
    ? progress.value.missing_before ?? progress.value.count ?? dashboard.value.missing_before ?? lastResult.value.missing_before
    : dashboard.value.missing_before ?? lastResult.value.missing_before ?? lastResult.value.strm_parts;
  return countValue(value);
});
const resolvedCount = computed(() => liveCount("resolved"));
const unresolvedCount = computed(() => liveCount("unresolved"));
const writtenCount = computed(() => liveCount("written_ok"));
const writeFailedCount = computed(() => liveCount("write_failed"));
const pendingCount = computed(() => {
  if (busy.value) {
    // “仍待补”只统计尚未成功写入 Helper 的候选；解析完成但尚未 flush
    // 的小批次也会继续保留在这里，不会提前显示为已完成。
    return Math.max(0, candidateCount.value - writtenCount.value);
  }
  return countValue(dashboard.value.pending_after ?? unresolvedCount.value + writeFailedCount.value);
});
const currentCompleted = computed(() => {
  if (busy.value) {
    const before = countValue(progress.value.completed_before ?? dashboard.value.completed_before);
    return before + writtenCount.value;
  }
  const value = dashboard.value.completed_after ?? lastResult.value.completed_after;
  return value === null || value === undefined ? null : countValue(value);
});

const statusLabel = computed(() => {
  const value = String(completion.value?.status || "idle");
  return {
    idle: "待扫描",
    queued: "排队中",
    running: "补全中",
    done: "已完成",
    failed: "失败",
  }[value] || value;
});

const statusColor = computed(() => {
  const value = String(completion.value?.status || "idle");
  if (value === "done") return "success";
  if (value === "failed") return "error";
  if (value === "queued" || value === "running") return "warning";
  return "grey";
});

const scanModeLabel = computed(() => {
  if (lastResult.value.full_scan || dashboard.value.full_scan) return "全库统计 + 缺失项补全";
  if (dashboard.value.scan_mode === "all") return "全量处理";
  return "仅缺失项增量补全";
});

const statCards = computed(() => [
  {
    label: "全库 STRM",
    value: hasFullStats.value ? formatNumber(totalStrmParts.value) : "—",
    hint: hasFullStats.value ? "已选 Plex 媒体库" : "需要全库扫描",
    color: "primary",
  },
  {
    label: "当前已完整",
    value: currentCompleted.value === null ? "—" : formatNumber(currentCompleted.value),
    hint: hasFullStats.value ? `扫描前完整 ${formatNumber(dashboard.value.completed_before)}` : "等待全库统计",
    color: "success",
  },
  {
    label: "本次成功写入",
    value: formatNumber(writtenCount.value),
    hint: `候选 ${formatNumber(candidateCount.value)}`,
    color: "info",
  },
  {
    label: "仍待补",
    value: hasFullStats.value ? formatNumber(pendingCount.value) : "—",
    hint: hasFullStats.value ? `未解析 ${formatNumber(unresolvedCount.value)}` : "等待全库统计",
    color: pendingCount.value ? "warning" : "success",
  },
]);

const progressTotal = computed(() => {
  if (!busy.value) return countValue(progress.value.total || progress.value.count);
  return candidateCount.value;
});
const progressDone = computed(() => {
  if (busy.value && (progress.value.resolved !== undefined || progress.value.unresolved !== undefined)) {
    return Math.min(progressTotal.value, resolvedCount.value + unresolvedCount.value);
  }
  return countValue(progress.value.done);
});
const progressPercent = computed(() => {
  if (!progressTotal.value) return 0;
  return Math.min(100, Math.round((progressDone.value / progressTotal.value) * 100));
});
const progressLabel = computed(() => {
  const phase = String(progress.value.phase || "");
  return phase === "writing"
    ? "已处理候选（写入持续进行）"
    : phase === "resolving"
      ? "正在探测媒体流"
      : "正在枚举媒体库";
});
const selectedSectionCount = computed(
  () => (dashboard.value.selected_sections || []).length,
);
const lastScanDisplay = computed(() => {
  const ts = Number(dashboard.value.last_scan_ts || 0);
  if (!ts) return "";
  return new Date(ts * 1000).toLocaleString();
});
const lastRefreshedDisplay = computed(() => {
  if (!lastRefreshedAt.value) return "";
  return `更新于 ${new Date(lastRefreshedAt.value).toLocaleTimeString()}`;
});

function formatNumber(value) {
  if (value === null || value === undefined || value === "") return "—";
  const number = Number(value);
  return Number.isFinite(number) ? number.toLocaleString() : "—";
}

function schedulePoll() {
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
  if (busy.value) {
    pollTimer = setTimeout(() => loadResult({ silent: true }), 4000);
  }
}

async function loadResult({ silent = false } = {}) {
  if (!silent) loading.value = true;
  error.value = "";
  try {
    const data = await props.api.get(`plugin/${P115_STRM_HELPER_PLUGIN_ID}/plex_app/result/`);
    if (!data || data.success === false) throw new Error(data?.error || "获取 Plex 补全结果失败");
    response.value = data;
    loaded.value = true;
    lastRefreshedAt.value = Date.now();
  } catch (err) {
    error.value = err?.message || "获取 Plex 补全结果失败";
  } finally {
    if (!silent) loading.value = false;
    schedulePoll();
  }
}

async function triggerFullScan() {
  if (busy.value) return;
  triggerLoading.value = true;
  notice.value = "";
  try {
    const data = await props.api.post(`plugin/${P115_STRM_HELPER_PLUGIN_ID}/plex_app/complete`, {
      full_scan: true,
      // 全库任务只处理缺失项；明确强制写入，避免 Plex 扫描 busy 时丢掉已解析数据。
      force_write: true,
    });
    if (!data || data.success === false) throw new Error(data?.error || "全库扫描启动失败");
    notice.value = "全库扫描已排队，完成后会把总量、补全数和剩余数写入看板。";
    noticeType.value = "success";
    await loadResult();
  } catch (err) {
    notice.value = err?.message || "全库扫描启动失败";
    noticeType.value = "error";
  } finally {
    triggerLoading.value = false;
  }
}

onMounted(() => loadResult());
onUnmounted(() => {
  if (pollTimer) clearTimeout(pollTimer);
});
</script>

<style scoped>
.dashboard-widget--plex-app {
  width: 100%;
  min-height: 420px;
}

.plex-app-dash-card {
  min-height: 420px;
}

.plex-app-dash-card__body {
  min-height: 320px;
}

.plex-app-stat-card,
.plex-app-summary-card {
  border-radius: 12px !important;
  background: rgba(var(--v-theme-surface), 0.3);
}

.plex-app-stat-card {
  transition: box-shadow 0.2s ease, transform 0.2s ease;
}

.plex-app-stat-card:hover {
  transform: translateY(-1px);
  box-shadow: 0 2px 12px rgba(var(--v-theme-on-surface), 0.12);
}
</style>
