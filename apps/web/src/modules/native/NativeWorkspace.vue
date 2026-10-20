<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import ConnectionForm from "./ConnectionForm.vue";
import NativeBindingForm from "./NativeBindingForm.vue";
import InvocationForm from "./InvocationForm.vue";
import StatusTag from "../../components/common/StatusTag.vue";
import { useSession } from "../../stores/session";
const props = defineProps<{ config?: string }>();
const op = useOperation();
const session = useSession();
const connections = ref<Entity[]>([]);
const records = ref<Entity[]>([]);
const connectionDialog = ref(false);
const bindingDialog = ref(false);
const selected = ref<Entity | null>(null);
const launchUrl = ref("");
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
async function invoke(payload: Record<string, unknown>) {
  if (!invocation.value) return;
  await op.run(async () => {
    runResult.value = await api.post(
      `/${kind.value}/${invocation.value!.id}/invocations`,
      payload,
    );
    invocation.value = null;
  }, "运行请求已提交");
  if (op.fields.value.run_id) {
    runResult.value = await api.get(`/native-runs/${op.fields.value.run_id}`);
    invocation.value = null;
  }
}
async function updateRun(action: "refreshes" | "stops") {
  if (!runResult.value) return;
  await op.run(async () => {
    runResult.value = await api.post(
      `/native-runs/${runResult.value.id}/${action}`,
      {},
    );
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
    (x) => props.config === "connections" || x.engine === engine.value,
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
const target = computed(() =>
  props.config === "openwebui"
    ? "chat"
    : props.config === "dify-knowledge"
      ? "knowledge"
      : ["dify-workflows", "dify-triggers"].includes(props.config ?? "")
        ? "workflow"
        : props.config === "dify-agents"
          ? "agent"
          : props.config === "models"
            ? "models"
            : props.config === "dify-plugins"
              ? "plugins"
              : "apps",
);
async function load() {
  await op.run(async () => {
    if (session.can("m15.read"))
      connections.value = (
        await api.get<ListResult>("/integration-instances?limit=100")
      ).items as Entity[];
    if (!connectionMode.value)
      records.value = (await api.get<ListResult>(`/${kind.value}?limit=100`))
        .items as Entity[];
  });
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
  await op.run(async () => {
    if (selected.value) {
      const { engine, ...changes } = data;
      await api.patch(`/integration-instances/${selected.value.id}`, {
        ...changes,
        revision: selected.value.revision,
      });
    } else await api.post("/integration-instances", data);
    connectionDialog.value = false;
  }, "连接已保存");
  await load();
}
async function saveBinding(data: Record<string, unknown>) {
  await op.run(async () => {
    if (selected.value)
      await api.patch(`/${kind.value}/${selected.value.id}`, {
        ...data,
        revision: selected.value.revision,
      });
    else await api.post(`/${kind.value}`, data);
    bindingDialog.value = false;
  }, "已保存");
  await load();
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
async function launch(connectionId?: string, nativeId?: string) {
  const id =
    connectionId ??
    records.value.find((x) => x.enabled)?.integration_id ??
    relevant.value.find((x) => x.enabled)?.id;
  if (!id) {
    if (session.can("m15.write")) editConnection(null);
    else
      await op.run(async () => {
        throw new Error("应用尚未配置，请联系管理员");
      });
    return;
  }
  await op.run(async () => {
    const result = await api.post<{ url: string }>(
      `/integrations/${id}/launches`,
      { target: target.value, native_id: nativeId },
    );
    const parsed = new URL(result.url);
    if (!["http:", "https:"].includes(parsed.protocol))
      throw new Error("应用地址不可用");
    launchUrl.value = result.url;
    window.open(result.url, "_blank", "noopener,noreferrer");
  });
}
watch(() => props.config, load);
onMounted(load);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>{{ title }}</h2>
      <div>
        <el-button @click="load">刷新</el-button
        ><el-button v-if="!connectionMode" @click="editBinding(null)">{{
          kind === "knowledge-spaces"
            ? "绑定知识库"
            : kind === "model-profiles"
              ? "新增模型"
              : "绑定应用"
        }}</el-button
        ><el-button
          v-if="config === 'connections'"
          type="primary"
          @click="editConnection(null)"
          >新增连接</el-button
        ><el-button v-else type="primary" @click="launch()"
          >进入{{ config === "openwebui" ? "对话" : "工作台" }}</el-button
        >
      </div>
    </div>
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
            :href="connection.console_url"
            target="_blank"
            rel="noopener noreferrer"
            class="muted"
            >{{ connection.console_url }}</a
          >
        </div>
        <div class="native-card-actions">
          <el-button @click="editConnection(connection)">连接设置</el-button
          ><el-button type="primary" @click="launch(connection.id)"
            >打开应用</el-button
          ><el-button link type="danger" @click="remove(connection, true)"
            >删除</el-button
          >
        </div>
      </article>
      <article v-if="!relevant.length" class="native-card empty-block">
        <el-empty
          :description="`尚未连接${config === 'openwebui' ? 'Open WebUI' : 'Dify'}`"
          :image-size="80"
          ><el-button type="primary" @click="editConnection(null)"
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
            <p>{{ record.description }}</p>
            <div class="knowledge-meta">
              <span>{{ record.native_id }}</span
              ><span>{{ record.updated_at?.slice(0, 10) }}</span>
            </div>
          </div>
          <img src="/knowledge-cover.png" alt="" />
        </div>
        <div class="row-actions">
          <el-button
            type="primary"
            @click="launch(record.integration_id, record.native_id)"
            >文档管理</el-button
          ><el-button @click="editBinding(record)">设置</el-button
          ><el-button link type="danger" @click="remove(record)"
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
          <el-button @click="editBinding(record)">模型设置</el-button
          ><el-button @click="launch(record.integration_id)">模型详情</el-button
          ><el-button link type="danger" @click="remove(record)"
            >删除</el-button
          >
        </div>
      </article>
    </div>
    <section v-else class="panel">
      <el-table :data="records" stripe v-loading="op.busy.value"
        ><el-table-column
          prop="name"
          label="名称"
          min-width="220"
        /><el-table-column
          prop="app_mode"
          label="类型"
          width="150"
        /><el-table-column
          prop="native_id"
          label="应用ID"
          min-width="220"
          show-overflow-tooltip
        /><el-table-column
          prop="updated_at"
          label="更新时间"
          width="185"
        /><el-table-column label="操作" width="220"
          ><template #default="{ row }"
            ><el-button link type="primary" @click="invocation = row"
              >运行</el-button
            ><el-button
              link
              type="primary"
              @click="launch(row.integration_id, row.native_id)"
              >构建</el-button
            ><el-button link type="primary" @click="editBinding(row)"
              >设置</el-button
            ><el-button link type="danger" @click="remove(row)"
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
      ><el-button type="primary" @click="editBinding(null)"
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
          /><el-button @click="updateRun('refreshes')">刷新状态</el-button
          ><el-button
            v-if="['queued', 'running', 'unknown'].includes(runResult.status)"
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
</style>
