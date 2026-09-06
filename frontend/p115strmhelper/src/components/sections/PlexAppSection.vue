<template>
  <v-card-text>
    <v-alert type="info" variant="tonal" density="compact" class="mb-4" icon="mdi-information">
      <div class="text-body-2 mb-1"><strong>Plex App 播放支持</strong></div>
      <div class="text-caption">
        使用 ffprobe 探测 STRM 指向的真实媒体，再通过 Plex MediaInfo Helper 写入 Plex 数据库。
        这里不包含 Plex Web 反向代理；Plex Web 仍应直接访问原 Plex 地址。
      </div>
    </v-alert>

    <v-row>
      <v-col cols="12" md="4">
        <v-switch v-model="config.plex_app_enabled" label="启用 Plex App 媒体补全" color="success"
          density="compact" hide-details></v-switch>
      </v-col>
      <v-col cols="12" md="4">
        <v-switch v-model="config.plex_app_webhook_enabled" label="启用播放停止 Webhook" color="info"
          density="compact" hide-details :disabled="!config.plex_app_enabled"></v-switch>
      </v-col>
      <v-col cols="12" md="4">
        <v-switch v-model="config.plex_app_only_missing" label="仅补缺少媒体流的项目" color="primary"
          density="compact" hide-details></v-switch>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12" md="6">
        <v-text-field v-model="config.plex_app_plex_url" label="Plex 直连地址" hint="例如 http://host.docker.internal:32400"
          persistent-hint density="compact" variant="outlined"></v-text-field>
      </v-col>
      <v-col cols="12" md="6">
        <v-text-field v-model="config.plex_app_plex_token" label="Plex Token" type="password" density="compact"
          variant="outlined" hide-details="auto"></v-text-field>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12" md="6">
        <v-text-field v-model="config.plex_app_helper_url" label="Plex MediaInfo Helper 地址"
          hint="例如 http://host.docker.internal:9001" persistent-hint density="compact" variant="outlined"></v-text-field>
      </v-col>
      <v-col cols="12" md="6">
        <v-text-field v-model="config.plex_app_helper_token" label="Helper Token" type="password" density="compact"
          variant="outlined" hide-details="auto"></v-text-field>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12">
        <v-text-field v-model="config.plex_app_sections" label="Plex 媒体库 key" hint="逗号分隔，例如 39,40,44；留空则不能执行全量/播放补全"
          persistent-hint density="compact" variant="outlined"></v-text-field>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12" md="6">
        <v-textarea v-model="config.plex_app_ffprobe_path_map" label="Plex 路径 → MoviePilot 路径映射"
          hint="每行一条：Plex路径=/容器路径；也支持 =>" persistent-hint rows="2" auto-grow density="compact"
          variant="outlined"></v-textarea>
      </v-col>
      <v-col cols="12" md="6">
        <v-alert type="warning" variant="tonal" density="compact" icon="mdi-link-variant">
          <div class="text-caption mb-1">Plex Webhook 地址</div>
          <code>/api/v1/plugin/P115StrmHelper/plex_app/webhook?apikey=你的MP_API_KEY</code>
          <div class="text-caption mt-1">Plex 事件选择 media.stop；迁移后请把旧 Webhook 地址改成这个地址。</div>
        </v-alert>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12" md="3">
        <v-text-field v-model.number="config.plex_app_ffprobe_timeout" label="ffprobe 超时(秒)" type="number" min="1"
          max="300" density="compact" variant="outlined"></v-text-field>
      </v-col>
      <v-col cols="12" md="3">
        <v-text-field v-model.number="config.plex_app_concurrency" label="探测并发数" type="number" min="1" max="16"
          density="compact" variant="outlined"></v-text-field>
      </v-col>
      <v-col cols="12" md="3">
        <v-text-field v-model.number="config.plex_app_forward_episodes" label="剧集预取集数" type="number" min="0" max="50"
          density="compact" variant="outlined"></v-text-field>
      </v-col>
      <v-col cols="12" md="3">
        <v-text-field v-model.number="config.plex_app_dedup_window" label="Webhook 去重(秒)" type="number" min="0" max="86400"
          density="compact" variant="outlined"></v-text-field>
      </v-col>
    </v-row>

    <v-row>
      <v-col cols="12" md="4">
        <v-switch v-model="config.plex_app_overwrite_streams" label="覆盖旧媒体流" color="warning" density="compact"
          hide-details></v-switch>
      </v-col>
      <v-col cols="12" md="8" class="d-flex align-center ga-2 flex-wrap">
        <v-btn size="small" variant="tonal" color="info" prepend-icon="mdi-heart-pulse" :loading="helperLoading"
          @click="checkHelper">检查 Helper</v-btn>
        <v-btn size="small" variant="tonal" color="primary" prepend-icon="mdi-library-shelves" :loading="sectionsLoading"
          @click="loadSections">读取媒体库</v-btn>
        <v-btn size="small" variant="tonal" color="success" prepend-icon="mdi-playlist-check" :loading="completeLoading"
          :disabled="!config.plex_app_enabled" @click="completeNow">立即补全</v-btn>
      </v-col>
    </v-row>

    <v-alert v-if="helperStatus" :type="helperStatus.success ? 'success' : 'error'" variant="tonal" density="compact"
      class="mt-2">
      {{ helperStatus.success ? 'Helper 正常' : (helperStatus.error || 'Helper 不可用') }}
      <span v-if="helperStatus.dbinfo?.path" class="text-caption ml-2">{{ helperStatus.dbinfo.path }}</span>
    </v-alert>

    <v-card v-if="sections.length" variant="outlined" class="mt-3">
      <v-card-title class="text-subtitle-2">Plex 媒体库</v-card-title>
      <v-card-text class="d-flex flex-wrap ga-2">
        <v-chip v-for="item in sections" :key="item.key" size="small" variant="tonal"
          @click="appendSection(item.key)">{{ item.key }} · {{ item.title }} ({{ item.type }})</v-chip>
      </v-card-text>
    </v-card>
  </v-card-text>
</template>

<script setup>
import { inject, ref } from 'vue';

const config = inject('config');
const api = inject('api');
const message = inject('message');
const PLUGIN_ID = inject('PLUGIN_ID');

const helperLoading = ref(false);
const sectionsLoading = ref(false);
const completeLoading = ref(false);
const helperStatus = ref(null);
const sections = ref([]);

function success(response) {
  return response?.success === true || response?.code === 0 || response?.ok === true;
}

async function checkHelper() {
  helperLoading.value = true;
  try {
    const response = await api.get(`plugin/${PLUGIN_ID}/plex_app/helper_check`);
    helperStatus.value = response;
    message.text = success(response) ? 'Plex MediaInfo Helper 正常' : (response?.error || 'Helper 检查失败');
    message.type = success(response) ? 'success' : 'error';
  } catch (error) {
    helperStatus.value = { success: false, error: error.message };
    message.text = `Helper 检查失败: ${error.message}`;
    message.type = 'error';
  } finally {
    helperLoading.value = false;
  }
}

async function loadSections() {
  sectionsLoading.value = true;
  try {
    const response = await api.get(`plugin/${PLUGIN_ID}/plex_app/sections`);
    if (success(response)) {
      sections.value = response.sections || response.data?.sections || [];
      message.text = `读取到 ${sections.value.length} 个 Plex 媒体库`;
      message.type = 'success';
    } else {
      message.text = response?.error || '读取 Plex 媒体库失败';
      message.type = 'error';
    }
  } catch (error) {
    message.text = `读取 Plex 媒体库失败: ${error.message}`;
    message.type = 'error';
  } finally {
    sectionsLoading.value = false;
  }
}

function appendSection(key) {
  const current = String(config.plex_app_sections || '').split(',').map(item => item.trim()).filter(Boolean);
  if (!current.includes(String(key))) current.push(String(key));
  config.plex_app_sections = current.join(',');
}

async function completeNow() {
  completeLoading.value = true;
  try {
    const response = await api.post(`plugin/${PLUGIN_ID}/plex_app/complete`, {
      section_keys: config.plex_app_sections,
    });
    message.text = success(response)
      ? `补全任务完成：写入 ${response.written_ok || 0} 条`
      : (response?.error || '补全失败');
    message.type = success(response) ? 'success' : 'error';
  } catch (error) {
    message.text = `补全失败: ${error.message}`;
    message.type = 'error';
  } finally {
    completeLoading.value = false;
  }
}
</script>
