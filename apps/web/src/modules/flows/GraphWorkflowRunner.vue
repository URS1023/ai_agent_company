<script setup lang="ts">
import {
  computed,
  onMounted,
  onScopeDispose,
  ref,
  shallowRef,
  watch,
} from "vue";
import { ElMessageBox } from "element-plus";
import { ApiError } from "../../api/client";
import { useWorkflowRequests } from "./useWorkflowRequests";
import {
  useWorkflowRunIntent,
  type WorkflowRunResult,
} from "./useWorkflowRunIntent";
import type { GraphDefinition } from "./graphInputs";
import GraphInputForm from "./GraphInputForm.vue";
import WorkflowJsonValue from "./WorkflowJsonValue.vue";
import WorkflowRunEvents from "./WorkflowRunEvents.vue";
const props = defineProps<{ workflowId: string; canRun: boolean }>();
const transport = useWorkflowRequests();
const definition = ref<GraphDefinition | null>(null),
  history = ref<WorkflowRunResult[]>([]),
  selected = ref<WorkflowRunResult | null>(null);
const loading = shallowRef(false),
  reading = shallowRef(false),
  error = shallowRef(""),
  confirming = shallowRef(false);
const intent = useWorkflowRunIntent(() => props.workflowId);
const shown = computed(() => selected.value ?? intent.currentRun.value);
const status = {
  running: "运行中",
  succeeded: "运行成功",
  failed: "运行失败",
  unknown: "结果待核对",
} as Record<string, string>;
let generation = 0,
  readSequence = 0,
  historySequence = 0;
function deny(message: string) {
  generation++;
  historySequence++;
  definition.value = null;
  history.value = [];
  selected.value = null;
  intent.clear();
  readSequence++;
  error.value = message;
  reading.value = false;
  loading.value = false;
}
function failure(value: unknown) {
  const message = value instanceof Error ? value.message : "读取失败";
  if (value instanceof ApiError && [401, 403, 404].includes(value.status))
    deny(message);
  else error.value = message;
}
async function loadDefinition() {
  const ticket = generation;
  loading.value = true;
  definition.value = null;
  error.value = "";
  try {
    const result = await transport.send<{ definition: GraphDefinition }>(
      `/workflows/${encodeURIComponent(props.workflowId)}`,
    );
    if (!transport.active() || ticket !== generation) return;
    if (result.definition.format !== "graph")
      throw new Error("流程定义已变化，请重新选择流程");
    definition.value = result.definition;
  } catch (value) {
    if (transport.active() && ticket === generation) failure(value);
  } finally {
    if (transport.active() && ticket === generation) loading.value = false;
  }
}
async function loadHistory() {
  const sequence = ++historySequence;
  try {
    const result = await transport.send<{ items: WorkflowRunResult[] }>(
      `/workflows/${encodeURIComponent(props.workflowId)}/runs`,
    );
    if (transport.active() && sequence === historySequence)
      history.value = result.items;
  } catch (value) {
    if (transport.active() && sequence === historySequence) failure(value);
  }
}
async function read(run: WorkflowRunResult) {
  const sequence = ++readSequence;
  selected.value = null;
  reading.value = true;
  error.value = "";
  try {
    const result = await transport.send<WorkflowRunResult>(
      `/workflows/runs/${encodeURIComponent(run.id)}`,
    );
    if (transport.active() && sequence === readSequence)
      selected.value = result;
  } catch (value) {
    if (transport.active() && sequence === readSequence) failure(value);
  } finally {
    if (transport.active() && sequence === readSequence) reading.value = false;
  }
}
async function run(inputs: Record<string, unknown>) {
  if (
    !props.canRun ||
    !definition.value ||
    intent.running.value ||
    confirming.value
  )
    return;
  let confirmNew = false;
  if (intent.uncertain.value) {
    confirming.value = true;
    try {
      await ElMessageBox.confirm(
        "上次结果尚未确认，仍要发起一次新运行？",
        "新运行确认",
        { type: "warning", confirmButtonText: "发起新运行" },
      );
      confirmNew = true;
    } catch {
      return;
    } finally {
      confirming.value = false;
    }
  }
  if (!transport.active()) return;
  selected.value = null;
  if (await intent.start(inputs, confirmNew)) {
    if (
      transport.active() &&
      intent.currentRun.value &&
      !intent.uncertain.value
    )
      await loadHistory();
  }
}
watch(
  () => intent.currentRun.value,
  () => {
    selected.value = null;
  },
);
watch(
  () => intent.permissionError.value,
  (message) => {
    if (message) deny(message);
  },
);
onMounted(async () => {
  await loadDefinition();
  if (transport.active() && definition.value) await loadHistory();
});
onScopeDispose(() => {
  definition.value = null;
  history.value = [];
  selected.value = null;
});
function time(value: string) {
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}
</script>
<template>
  <section v-loading="loading" class="panel graph-runner">
    <div class="toolbar">
      <strong>运行</strong
      ><el-tag v-if="intent.running.value" type="info">等待结果</el-tag
      ><span class="spacer" /><el-button
        :disabled="intent.running.value || loading"
        @click="loadDefinition"
        >刷新定义</el-button
      ><el-button
        v-if="intent.current.value"
        :loading="intent.checking.value"
        :disabled="intent.running.value"
        @click="intent.verify"
        >核对结果</el-button
      >
    </div>
    <el-alert
      v-if="error || intent.current.value?.error"
      :title="error || intent.current.value?.error"
      type="error"
      :closable="false"
      show-icon
    /><GraphInputForm
      v-if="definition"
      :definition="definition"
      :disabled="
        !canRun || intent.running.value || intent.checking.value || confirming
      "
      :errors="intent.current.value?.fields ?? {}"
      @submit="run"
    /><el-tag v-if="!shown && intent.uncertain.value" type="warning"
      >结果待核对</el-tag
    >
  </section>
  <section v-if="shown" v-loading="reading" class="panel graph-result">
    <div class="toolbar">
      <strong>{{ status[shown.status] ?? "结果待核对" }}</strong
      ><span
        v-if="shown.graph_progress?.completed_nodes !== undefined"
        class="muted"
        >已完成 {{ shown.graph_progress.completed_nodes }} 次节点执行</span
      ><span class="spacer" /><span v-if="shown.started_at" class="muted">{{
        time(shown.started_at)
      }}</span>
    </div>
    <el-alert
      v-if="shown.error"
      :title="shown.error"
      type="error"
      :closable="false"
    /><el-collapse
      ><el-collapse-item title="本次输入" name="inputs"
        ><WorkflowJsonValue
          :value="shown.inputs ?? shown.input" /></el-collapse-item
    ></el-collapse>
    <div class="section-title">输出</div>
    <WorkflowJsonValue
      :value="shown.format === 'graph' ? shown.outputs : shown.output"
    /><WorkflowRunEvents
      v-if="shown.format === 'graph'"
      :key="shown.id"
      :run-id="shown.id"
      :nodes="
        shown.definition_snapshot?.graph?.nodes ?? definition?.graph.nodes ?? []
      "
      @denied="deny"
    />
  </section>
  <section class="panel">
    <div class="toolbar">
      <strong>运行历史</strong><span class="spacer" /><el-button
        :disabled="intent.running.value"
        @click="loadHistory"
        >刷新记录</el-button
      >
    </div>
    <el-table v-loading="reading" :data="history" empty-text="暂无运行记录"
      ><el-table-column label="状态" width="140"
        ><template #default="{ row }">{{
          status[row.status] ?? "结果待核对"
        }}</template></el-table-column
      ><el-table-column label="时间" min-width="190"
        ><template #default="{ row }">{{
          time(row.started_at)
        }}</template></el-table-column
      ><el-table-column label="节点执行" width="110"
        ><template #default="{ row }">{{
          row.graph_progress?.completed_nodes ?? "—"
        }}</template></el-table-column
      ><el-table-column label="操作" width="100"
        ><template #default="{ row }"
          ><el-button link type="primary" :disabled="reading" @click="read(row)"
            >查看</el-button
          ></template
        ></el-table-column
      ></el-table
    >
  </section>
</template>
<style scoped>
.graph-runner,
.graph-result {
  display: grid;
  gap: var(--sp-4);
}
.graph-result > .section-title {
  margin: 0;
}
</style>
