<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import ConnectionForm from "./ConnectionForm.vue";
import NativeBindingForm from "./NativeBindingForm.vue";
import InvocationForm from "./InvocationForm.vue";
import StatusTag from "../../components/common/StatusTag.vue";
import { useSession } from "../../stores/session";
import KnowledgeOperations from "./KnowledgeOperations.vue";
import {
  applicationModeLabel,
  bindingEntry,
  connectionEntry,
  pageTarget,
  requiresNativeRun,
  targetPermissions,
  type NativeEntry,
} from "./nativeTargets";
const props = defineProps<{ config?: string }>();
const op = useOperation();
const loading = useOperation();
const session = useSession();
const connections = ref<Entity[]>([]);
const records = ref<Entity[]>([]);
const connectionDialog = ref(false);
const bindingDialog = ref(false);
const selected = ref<Entity | null>(null);
const launchUrl = ref("");
const knowledgeSpace = ref<Entity | null>(null);
const kind = computed(() =>
  props.config === "models"
    ? "model-profiles"
    : props.config === "dify-knowledge"
      ? "knowledge-spaces"
      : ["dify-workflows", "dify-triggers"].includes(props.config ?? "")
        ? "automations"
        : "agent-app-bindings",
);
const invocation = ref<Entity | null>(null);
const runResult = ref<any>(null);
function viewKey() {
  return `${session.value?.workspace.id}:${props.config ?? ""}`;
}
const module = computed(() =>
  kind.value === "knowledge-spaces"
    ? "m08"
    : kind.value === "model-profiles"
      ? "m15"
      : kind.value === "automations"
        ? "m04"
        : "m03",
);
const canWrite = computed(() => session.can(`${module.value}.write`));
const canDelete = computed(() => session.can(`${module.value}.delete`));
function recordEntry(record: Entity): NativeEntry {
  if (kind.value === "knowledge-spaces")
    return { target: "knowledge", native_id: record.native_id };
  if (kind.value === "model-profiles") return { target: "models" };
  return bindingEntry(record);
}
function canOpen(entry: NativeEntry) {
  return session.can(targetPermissions[entry.target]);
}
function connectionName(record: Entity) {
  return (
    connections.value.find(
      (connection) => connection.id === record.integration_id,
    )?.name ?? (record.app_mode === "openwebui_model" ? "Open WebUI" : "Dify")
  );
}
async function invoke(payload: Record<string, unknown>) {
  if (!invocation.value) return;
  const key = viewKey();
  await op.run(async () => {
    const result = await api.post(
      `/${kind.value}/${invocation.value!.id}/invocations`,
      payload,
    );
    if (active && key === viewKey()) {
      runResult.value = result;
      invocation.value = null;
    }
  }, "运行请求已提交");
  if (!active || key !== viewKey()) return;
  const runId = op.fields.value.run_id;
  if (runId)
    await op.run(async () => {
      const result = await api.get(`/native-runs/${runId}`);
      if (active && key === viewKey()) {
        runResult.value = result;
        invocation.value = null;
      }
    });
}
function beginRun(record: Entity) {
  if (requiresNativeRun(record)) void launchRecord(record);
  else if (canWrite.value) invocation.value = record;
}
async function updateRun(action: "refreshes" | "stops") {
  if (!runResult.value) return;
  const key = viewKey();
  await op.run(async () => {
    const result = await api.post(
      `/native-runs/${runResult.value.id}/${action}`,
      {},
    );
    if (active && key === viewKey()) runResult.value = result;
  });
}
const connectionMode = computed(
  () =>
    props.config === "connections" ||
    props.config === "openwebui" ||
    props.config === "dify-plugins",
);
const engine = computed(() =>
  props.config === "openwebui" ? "openwebui" : "dify",
);
const relevant = computed(() =>
  connections.value.filter(
    (x) =>
      ["connections", "models", "dify-agents"].includes(props.config ?? "") ||
      x.engine === engine.value,
  ),
);
const title = computed(() =>
  props.config === "models"
    ? "模型配置"
    : props.config === "dify-knowledge"
      ? "知识库"
      : props.config === "dify-triggers"
        ? "触发器"
        : props.config === "dify-workflows"
          ? "工作流"
          : props.config === "dify-agents"
            ? "Agents"
            : props.config === "openwebui"
              ? "AI对话"
              : props.config === "dify-plugins"
                ? "插件"
                : "集成连接",
);
const target = computed(() => pageTarget(props.config));
let loadSequence = 0;
let active = true;
let queuedLoad = false;
async function load() {
  if (!active) return;
  if (loading.busy.value) {
    queuedLoad = true;
    loadSequence++;
    return;
  }
  await loading.run(async () => {
    const sequence = ++loadSequence;
    const config = props.config,
      workspace = session.value?.workspace.id;
    const resourceKind = kind.value,
      isConnection = connectionMode.value;
    const targets = [target.value];
    if (config === "dify-agents") targets.push("model_chat");
    let available: Entity[] = [];
    if (session.can("m15.read"))
      available = (
        await api.get<ListResult>("/integration-instances?limit=100")
      ).items as Entity[];
    else if (config !== "connections") {
      for (const entry of targets) {
        if (!session.can(targetPermissions[entry])) continue;
        available.push(
          ...((await api.get<ListResult>(`/native-apps?target=${entry}`))
            .items as Entity[]),
        );
      }
    }
    const items = !isConnection
      ? ((await api.get<ListResult>(`/${resourceKind}?limit=100`))
          .items as Entity[])
      : [];
    if (
      active &&
      sequence === loadSequence &&
      config === props.config &&
      workspace === session.value?.workspace.id
    ) {
      connections.value = [
        ...new Map(
          available.map((connection) => [connection.id, connection]),
        ).values(),
      ];
      records.value = items;
    }
  });
  // 页面或工作区变化产生的新读取依次执行，失败请求不自动重发。
  if (queuedLoad) {
    queuedLoad = false;
    await load();
  }
}
function editConnection(entity: Entity | null) {
  selected.value = entity;
  connectionDialog.value = true;
}
function editBinding(entity: Entity | null) {
  selected.value = entity;
  bindingDialog.value = true;
}
async function saveConnection(data: Record<string, unknown>) {
  const key = viewKey();
  await op.run(async () => {
    if (selected.value) {
      const { engine, ...changes } = data;
      await api.patch(`/integration-instances/${selected.value.id}`, {
        ...changes,
        revision: selected.value.revision,
      });
    } else await api.post("/integration-instances", data);
    if (active && key === viewKey()) connectionDialog.value = false;
  }, "连接已保存");
  if (active && key === viewKey()) await load();
}
async function saveBinding(data: Record<string, unknown>) {
  const key = viewKey();
  await op.run(async () => {
    if (selected.value)
      await api.patch(`/${kind.value}/${selected.value.id}`, {
        ...data,
        revision: selected.value.revision,
      });
    else await api.post(`/${kind.value}`, data);
    if (active && key === viewKey()) bindingDialog.value = false;
  }, "已保存");
  if (active && key === viewKey()) await load();
}
async function remove(row: Entity, isConnection = false) {
  try {
    await ElMessageBox.confirm(`删除“${row.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () =>
        api.delete(
          `/${isConnection ? "integration-instances" : kind.value}/${row.id}`,
          row.revision,
        ),
      "已删除",
    );
    await load();
  } catch {}
}
async function launchDefault() {
  const record = records.value.find(
    (row) => row.enabled && canOpen(recordEntry(row)),
  );
  if (record) return launchRecord(record);
  const connection = relevant.value.find(
    (row) => row.enabled && canOpen(connectionEntry(row.engine, props.config)),
  );
  if (connection) return launchConnection(connection);
  if (session.can("m15.write")) editConnection(null);
  else
    await op.run(async () => {
      throw new Error("暂无可打开的应用，请联系管理员");
    });
}
async function launchRecord(record: Entity) {
  return launchEntry(record.integration_id, recordEntry(record));
}
async function launchConnection(connection: Entity) {
  return launchEntry(
    connection.id,
    connectionEntry(connection.engine, props.config),
  );
}
async function launchEntry(id: string, entry: NativeEntry) {
  const key = viewKey();
  await op.run(async () => {
    const result = await api.post<{ url: string }>(
      `/integrations/${id}/launches`,
      entry,
    );
    const parsed = new URL(result.url);
    if (!["http:", "https:"].includes(parsed.protocol))
      throw new Error("应用地址不可用");
    if (!active || key !== viewKey()) return;
    launchUrl.value = result.url;
    window.open(result.url, "_blank", "noopener,noreferrer");
  });
}
function configureKnowledge() {
  const record = knowledgeSpace.value;
  knowledgeSpace.value = null;
  if (record) editBinding(record);
}
function configureKnowledgeConnection() {
  const connection = connections.value.find(
    (row) => row.id === knowledgeSpace.value?.integration_id,
  );
  knowledgeSpace.value = null;
  if (connection) editConnection(connection);
}
function openKnowledgeNative() {
  const record = knowledgeSpace.value;
  if (record) void launchRecord(record);
}
watch([() => props.config, () => session.value?.workspace.id], () => {
  loadSequence++;
  connections.value = [];
  records.value = [];
  runResult.value = null;
  knowledgeSpace.value = null;
  invocation.value = null;
  selected.value = null;
  bindingDialog.value = false;
  connectionDialog.value = false;
  launchUrl.value = "";
  void load();
});
onBeforeUnmount(() => {
  active = false;
  loadSequence++;
});
onMounted(load);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>{{ title }}</h2>
      <div>
        <el-button :loading="loading.busy.value" @click="load">刷新</el-button
        ><el-button
          v-if="!connectionMode && canWrite"
          @click="editBinding(null)"
          >{{
            kind === "knowledge-spaces"
              ? "绑定知识库"
              : kind === "model-profiles"
                ? "新增模型"
                : "绑定应用"
          }}</el-button
        ><el-button
          v-if="config === 'connections' && session.can('m15.write')"
          type="primary"
          @click="editConnection(null)"
          >新增连接</el-button
        ><el-button
          v-else-if="config !== 'connections' && canOpen({ target })"
          type="primary"
          :loading="op.busy.value"
          @click="launchDefault"
          >进入{{ config === "openwebui" ? "对话" : "工作台" }}</el-button
        >
      </div>
    </div>
    <el-alert
      v-if="loading.error.value"
      type="error"
      :title="loading.error.value"
      :closable="false"
    />
    <el-alert
      v-if="launchUrl"
      type="success"
      :closable="true"
      @close="launchUrl = ''"
      ><a :href="launchUrl" target="_blank" rel="noopener noreferrer"
        >打开{{ title }} →</a
      ></el-alert
    >
    <div v-if="connectionMode" class="native-grid">
      <article
        v-for="connection in relevant"
        :key="connection.id"
        class="native-card"
      >
        <div class="native-card-head">
          <div class="engine-icon">
            {{ connection.engine === "dify" ? "D" : "O" }}
          </div>
          <h3>{{ connection.name }}</h3>
          <span class="spacer"></span
          ><span
            :class="['connection-status', { enabled: connection.enabled }]"
            >{{ connection.enabled ? "已启用" : "已停用" }}</span
          >
        </div>
        <div class="native-card-body">
          <div>{{ connection.engine === "dify" ? "Dify" : "Open WebUI" }}</div>
          <a
            v-if="connection.console_url"
            :href="connection.console_url"
            target="_blank"
            rel="noopener noreferrer"
            class="muted"
            >{{ connection.console_url }}</a
          >
        </div>
        <div class="native-card-actions">
          <el-button
            v-if="session.can('m15.write')"
            @click="editConnection(connection)"
            >连接设置</el-button
          ><el-button
            v-if="canOpen(connectionEntry(connection.engine, config))"
            type="primary"
            :disabled="!connection.enabled || op.busy.value"
            @click="launchConnection(connection)"
            >打开应用</el-button
          ><el-button
            v-if="session.can('m15.delete')"
            link
            type="danger"
            @click="remove(connection, true)"
            >删除</el-button
          >
        </div>
      </article>
      <article v-if="!relevant.length" class="native-card empty-block">
        <el-empty
          :description="`尚未连接${config === 'openwebui' ? 'Open WebUI' : 'Dify'}`"
          :image-size="80"
          ><el-button
            v-if="session.can('m15.write')"
            type="primary"
            @click="editConnection(null)"
            >添加连接</el-button
          ></el-empty
        >
      </article>
    </div>
    <div v-else-if="kind === 'knowledge-spaces'" class="knowledge-grid">
      <article
        v-for="record in records"
        :key="record.id"
        class="knowledge-card"
      >
        <div class="panel-heading">
          <h3>{{ record.name }}</h3>
          <span :class="['connection-status', { enabled: record.enabled }]">{{
            record.enabled ? "已启用" : "已停用"
          }}</span>
        </div>
        <div class="knowledge-body">
          <div>
            <p v-if="record.description">{{ record.description }}</p>
            <div class="knowledge-meta">
              <span>{{ record.updated_at?.slice(0, 10) }}</span>
            </div>
          </div>
          <img src="/knowledge-cover.png" alt="" />
        </div>
        <div class="row-actions">
          <el-button type="primary" @click="knowledgeSpace = record"
            >文档管理</el-button
          ><el-button v-if="canWrite" @click="editBinding(record)"
            >设置</el-button
          ><el-button
            v-if="canDelete"
            link
            type="danger"
            @click="remove(record)"
            >删除</el-button
          >
        </div>
      </article>
    </div>
    <div v-else-if="kind === 'model-profiles'" class="native-grid">
      <article v-for="record in records" :key="record.id" class="native-card">
        <div class="native-card-head">
          <div class="engine-icon">M</div>
          <h3>{{ record.name }}</h3>
          <el-tag>{{ record.role }}</el-tag>
        </div>
        <div class="native-card-body">{{ record.native_model_id }}</div>
        <div class="native-card-actions">
          <el-button v-if="canWrite" @click="editBinding(record)"
            >模型设置</el-button
          ><el-button @click="launchRecord(record)">供应商配置</el-button
          ><el-button
            v-if="canDelete"
            link
            type="danger"
            @click="remove(record)"
            >删除</el-button
          >
        </div>
      </article>
    </div>
    <section v-else class="panel">
      <el-table :data="records" stripe v-loading="loading.busy.value"
        ><el-table-column
          prop="name"
          label="名称"
          min-width="220"
        /><el-table-column label="类型" width="150"
          ><template #default="{ row }">{{
            applicationModeLabel(row.app_mode)
          }}</template></el-table-column
        ><el-table-column label="连接" min-width="220" show-overflow-tooltip
          ><template #default="{ row }">{{
            connectionName(row)
          }}</template></el-table-column
        ><el-table-column
          prop="updated_at"
          label="更新时间"
          width="185"
        /><el-table-column label="操作" width="220"
          ><template #default="{ row }"
            ><el-button
              v-if="
                requiresNativeRun(row) ? canOpen(recordEntry(row)) : canWrite
              "
              link
              type="primary"
              :disabled="!row.enabled || op.busy.value"
              @click="beginRun(row)"
              >{{ requiresNativeRun(row) ? "打开" : "运行" }}</el-button
            ><el-button
              link
              type="primary"
              v-if="canOpen(recordEntry(row)) && !requiresNativeRun(row)"
              @click="launchRecord(row)"
              >构建</el-button
            ><el-button
              v-if="canWrite"
              link
              type="primary"
              @click="editBinding(row)"
              >设置</el-button
            ><el-button v-if="canDelete" link type="danger" @click="remove(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-empty
      v-if="!connectionMode && !records.length"
      description="暂无记录"
      :image-size="90"
      ><el-button v-if="canWrite" type="primary" @click="editBinding(null)"
        >添加{{
          kind === "knowledge-spaces"
            ? "知识库"
            : kind === "model-profiles"
              ? "模型"
              : "应用"
        }}绑定</el-button
      ></el-empty
    >
    <section v-if="runResult" class="panel">
      <div class="panel-heading">
        <h3>运行结果</h3>
        <div class="row-actions">
          <StatusTag
            :value="runResult.task?.status ?? runResult.status"
          /><el-button :loading="op.busy.value" @click="updateRun('refreshes')"
            >刷新状态</el-button
          ><el-button
            v-if="
              canWrite &&
              ['queued', 'running', 'unknown'].includes(runResult.status)
            "
            type="danger"
            plain
            @click="updateRun('stops')"
            >停止</el-button
          ><RouterLink
            v-if="runResult.task_id"
            :to="{ path: '/tasks', query: { id: runResult.task_id } }"
            >查看任务</RouterLink
          >
        </div>
      </div>
      <pre class="json-preview">{{
        JSON.stringify(
          runResult.outputs ?? runResult.result ?? runResult,
          null,
          2,
        )
      }}</pre>
    </section>
    <el-dialog
      :model-value="!!invocation"
      :title="invocation?.name ?? '运行'"
      width="680px"
      destroy-on-close
      @close="invocation = null"
      ><InvocationForm
        v-if="invocation"
        :binding="invocation"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="invoke"
        @cancel="invocation = null" /></el-dialog
    ><el-dialog
      v-model="connectionDialog"
      :title="selected ? '编辑连接' : '新增连接'"
      width="760px"
      destroy-on-close
      ><ConnectionForm
        :entity="selected"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="saveConnection"
        @cancel="connectionDialog = false" /></el-dialog
    ><el-dialog
      v-model="bindingDialog"
      :title="selected ? '编辑绑定' : '新增绑定'"
      width="760px"
      destroy-on-close
      ><NativeBindingForm
        :entity="selected"
        :kind="kind"
        :connections="connections"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="saveBinding"
        @cancel="bindingDialog = false"
    /></el-dialog>
    <KnowledgeOperations
      v-if="knowledgeSpace"
      :key="knowledgeSpace.id"
      :space="knowledgeSpace"
      :connection="
        connections.find((row) => row.id === knowledgeSpace?.integration_id)
      "
      @close="knowledgeSpace = null"
      @native="openKnowledgeNative"
      @configure="configureKnowledge"
      @connection="configureKnowledgeConnection"
    />
  </div>
</template>
<style scoped>
.spacer {
  flex: 1;
}
.engine-icon {
  width: 42px;
  height: 42px;
  border-radius: 5px;
  background: #e7efff;
  color: #2666fb;
  display: grid;
  place-items: center;
  font-size: 23px;
  font-weight: 700;
}
.connection-status {
  color: #aaa;
  font-size: 12px;
}
.connection-status.enabled {
  color: #0baa84;
}
.knowledge-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(440px, 1fr));
  gap: 20px;
}
.knowledge-card {
  background: #fff;
  padding: 22px;
  border-radius: 2px;
  min-height: 240px;
}
.knowledge-card h3 {
  font-size: 20px;
}
.knowledge-body {
  display: grid;
  grid-template-columns: 1fr 170px;
  gap: 20px;
  padding: 10px 0 20px;
}
.knowledge-body p {
  min-height: 45px;
  line-height: 1.6;
}
.knowledge-body img {
  width: 170px;
  height: 110px;
  object-fit: cover;
  border-radius: 8px;
}
.knowledge-meta {
  display: flex;
  flex-direction: column;
  gap: 8px;
  color: #777;
  font-size: 12px;
  overflow-wrap: anywhere;
}
@media (max-width: 1180px) {
  .knowledge-grid {
    grid-template-columns: minmax(0, 1fr);
  }
}
@media (max-width: 600px) {
  .knowledge-body {
    grid-template-columns: 1fr;
  }
  .knowledge-body img {
    display: none;
  }
}
</style>
