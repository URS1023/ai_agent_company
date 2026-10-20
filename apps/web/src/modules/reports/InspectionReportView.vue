<script setup lang="ts">
/** 正式报告只预览服务端文档；事实与绑定不在自由画布中编辑。 */
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { api, request, type Entity } from "../../api/client";
import { useSession } from "../../stores/session";
import { useOperation } from "../../composables/useOperation";
import StatusTag from "../../components/common/StatusTag.vue";

const props = defineProps<{ report: Entity }>();
const emit = defineEmits<{ saved: [report: Entity]; back: [] }>();
const session = useSession();
const op = useOperation();
const name = ref(props.report.name ?? "");
const previewUrl = ref("");
const previewBusy = ref(false);
const previewError = ref("");
const canWrite = computed(() => session.can("m07.write"));
const busy = computed(() => op.busy.value || previewBusy.value);
const nameChanged = computed(() => name.value.trim() !== props.report.name);
const formats = ["html", "pdf", "docx", "xlsx", "pptx", "csv", "json"];
let previewSequence = 0;
let previewController: AbortController | undefined;

function releasePreview() {
  if (previewUrl.value) URL.revokeObjectURL(previewUrl.value);
  previewUrl.value = "";
}

async function loadPreview() {
  const sequence = ++previewSequence;
  previewController?.abort();
  previewController = new AbortController();
  releasePreview();
  previewError.value = "";
  previewBusy.value = true;
  try {
    const blob = await request<Blob>(`/reports/${props.report.id}/exports`, {
      method: "POST",
      body: JSON.stringify({ format: "html", revision: props.report.revision }),
      asBlob: true,
      signal: previewController.signal,
    });
    if (sequence !== previewSequence) return;
    if (!blob.type.includes("text/html")) throw new Error("报告预览格式无效");
    previewUrl.value = URL.createObjectURL(blob);
  } catch (error) {
    if (sequence !== previewSequence) return;
    previewError.value =
      error instanceof Error ? error.message : "报告预览失败";
  } finally {
    if (sequence === previewSequence) previewBusy.value = false;
  }
}

async function rename() {
  if (!canWrite.value || !name.value.trim() || !nameChanged.value) return;
  await op.run(async () => {
    const report = await api.patch<Entity>(`/reports/${props.report.id}`, {
      name: name.value.trim(),
      revision: props.report.revision,
    });
    emit("saved", report);
  }, "名称已保存");
}

async function refresh() {
  await op.run(async () => {
    const report = await api.post<Entity>(
      `/reports/${props.report.id}/data-refreshes`,
      { revision: props.report.revision },
    );
    emit("saved", report);
  }, "报告内容已刷新");
}

async function publish() {
  await op.run(async () => {
    const report = await api.post<Entity>(
      `/reports/${props.report.id}/publications`,
      { revision: props.report.revision },
    );
    emit("saved", report);
  }, "报告已发布");
}

async function download(format: string) {
  await op.run(async () => {
    const blob = await api.postBlob(`/reports/${props.report.id}/exports`, {
      format,
      revision: props.report.revision,
    });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${props.report.name ?? "质检报告"}.${format}`;
    anchor.click();
    // 下载事件派发完成后释放临时地址；不把内容地址保存进报告。
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  });
}

watch(
  () => [props.report.id, props.report.revision],
  () => {
    name.value = props.report.name ?? "";
    void loadPreview();
  },
  { immediate: true },
);
onBeforeUnmount(() => {
  previewSequence++;
  previewController?.abort();
  releasePreview();
});
</script>

<template>
  <div class="inspection-report-view">
    <header class="document-toolbar">
      <el-button @click="emit('back')">返回</el-button>
      <el-form class="document-name" @submit.prevent="rename"
        ><el-form-item :error="op.fields.value.name"
          ><el-input
            v-model="name"
            aria-label="报告名称"
            :readonly="!canWrite"
            required
            maxlength="160" /></el-form-item
      ></el-form>
      <StatusTag :value="report.status" /><span class="document-revision"
        >v{{ report.revision }}</span
      ><span class="spacer" />
      <el-button
        v-if="canWrite"
        :disabled="!nameChanged || !name.trim() || busy"
        @click="rename"
        >保存名称</el-button
      >
      <el-button
        v-if="canWrite"
        :loading="op.busy.value"
        :disabled="previewBusy || nameChanged"
        @click="refresh"
        >刷新内容</el-button
      >
      <el-button
        v-if="session.can('m07.publish')"
        type="primary"
        :loading="op.busy.value"
        :disabled="
          previewBusy ||
          !previewUrl ||
          nameChanged ||
          report.status === 'published'
        "
        @click="publish"
        >发布</el-button
      >
      <el-dropdown :disabled="busy || nameChanged" @command="download"
        ><el-button :disabled="busy || nameChanged">导出</el-button
        ><template #dropdown
          ><el-dropdown-menu
            ><el-dropdown-item
              v-for="format in formats"
              :key="format"
              :command="format"
              >{{ format.toUpperCase() }}</el-dropdown-item
            ></el-dropdown-menu
          ></template
        ></el-dropdown
      >
    </header>
    <div
      class="document-preview"
      v-loading="previewBusy"
      :aria-busy="previewBusy"
    >
      <iframe
        v-if="previewUrl"
        :src="previewUrl"
        title="质检报告正文"
        sandbox=""
        referrerpolicy="no-referrer"
      />
      <el-result v-else-if="previewError" icon="error" :title="previewError"
        ><template #extra
          ><el-button @click="loadPreview">重新加载</el-button></template
        ></el-result
      >
    </div>
  </div>
</template>

<style scoped>
.inspection-report-view {
  height: calc(100vh - 124px);
  min-height: 500px;
  display: grid;
  grid-template-rows: auto minmax(0, 1fr);
  background: #eef1f5;
}
.document-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 12px 15px;
  background: var(--surface);
  border-bottom: 1px solid #e2e7ef;
}
.document-name {
  width: 270px;
  min-width: 180px;
}
.document-name :deep(.el-form-item) {
  margin: 0;
}
.document-revision {
  color: #66778c;
  font-size: 14px;
}
.spacer {
  flex: 1;
}
.document-preview {
  min-height: 0;
  overflow: hidden;
}
.document-preview iframe {
  width: 100%;
  height: 100%;
  border: 0;
  display: block;
  background: var(--surface);
}
@media (max-width: 1200px) {
  .document-toolbar {
    flex-wrap: wrap;
  }
  .document-name {
    flex: 1;
  }
  .spacer {
    display: none;
  }
}
</style>
