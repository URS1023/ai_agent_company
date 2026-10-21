<script setup lang="ts">
/** 旧步骤编辑器保留原行为；图定义交给独立运行视图，不转写为步骤。 */
import { computed, onMounted, ref, shallowRef, watch } from "vue";
import { Delete, Plus, VideoPlay } from "@element-plus/icons-vue";
import { useOperation } from "../../composables/useOperation";
import { ElMessageBox } from "element-plus";
import { useWorkflowRunIntent } from "./useWorkflowRunIntent";
import { useWorkflowRequests } from "./useWorkflowRequests";
import GraphWorkflowRunner from "./GraphWorkflowRunner.vue";
import GraphWorkflowEditor from "./GraphWorkflowEditor.vue";
const props = defineProps<{
  canWrite: boolean;
  canDelete: boolean;
  canKnowledge: boolean;
}>();
const transport = useWorkflowRequests();
const api = {
  get: <T = any,>(path: string) => transport.send<T>(path),
  post: <T = any,>(path: string, data: unknown) =>
    transport.send<T>(path, { method: "POST", body: JSON.stringify(data) }),
  put: <T = any,>(path: string, data: unknown) =>
    transport.send<T>(path, { method: "PUT", body: JSON.stringify(data) }),
  delete: (path: string) => transport.send(path, { method: "DELETE" }),
};

interface Step {
  type: "kb" | "ai" | "text";
  name: string;
  space_ids?: string[];
  query?: string;
  top_k?: number;
  prompt?: string;
  max_tokens?: number;
  template?: string;
}
interface Workflow {
  id: string;
  name: string;
  description: string;
  enabled: boolean;
  revision: number;
  step_count: number;
  format?: "graph" | "legacy_steps";
}
interface RunStep {
  index: number;
  name: string;
  type: string;
  status: string;
  output: string;
  error?: string;
}
interface Run {
  id: string;
  status: string;
  input: string;
  steps: RunStep[];
  output: string;
  error?: string;
  started_at: string;
}

const op = useOperation();
const workflows = ref<Workflow[]>([]);
const activeId = ref("");
const spaces = ref<{ id: string; name: string }[]>([]);
const loading = ref(false);

const editorOpen = ref(false);
const graphEditorOpen = ref(false);
const graphEditingId = ref("");
function openGraphEditor(id = "") {
  graphEditingId.value = id;
  graphEditorOpen.value = true;
}
async function graphSaved(id: string) {
  activeId.value = id;
  graphEditingId.value = id;
  await op.run(loadWorkflows);
}
const editingId = ref("");
const editingFormat = shallowRef("legacy_steps");
const form = ref<{ name: string; description: string; steps: Step[] }>({
  name: "",
  description: "",
  steps: [],
});

const runInput = ref("");
const history = ref<Run[]>([]);
let historySequence = 0;
const intent = useWorkflowRunIntent(() => activeId.value);
const { currentRun, running } = intent;
const confirming = shallowRef(false);
const runLabels: Record<string, string> = {
  succeeded: "运行成功",
  failed: "运行失败",
  running: "运行中",
  unknown: "结果待核对",
};

const active = computed(() =>
  workflows.value.find((w) => w.id === activeId.value),
);

const stepLabel: Record<string, string> = {
  kb: "知识检索",
  ai: "模型回答",
  text: "文本整理",
};

async function loadWorkflows() {
  loading.value = true;
  try {
    const data = await api.get<{ items: Workflow[] }>("/workflows");
    workflows.value = data.items;
    if (!workflows.value.some((w) => w.id === activeId.value))
      activeId.value = workflows.value[0]?.id ?? "";
    await loadHistory();
  } finally {
    loading.value = false;
  }
}

async function loadHistory() {
  const workflowId = activeId.value,
    sequence = ++historySequence;
  history.value = [];
  if (!activeId.value || active.value?.format === "graph") {
    history.value = [];
    return;
  }
  const data = await api.get<{ items: Run[] }>(`/workflows/${workflowId}/runs`);
  if (sequence === historySequence && activeId.value === workflowId)
    history.value = data.items;
}

function selectWorkflow(id: string) {
  graphEditorOpen.value = false;
  activeId.value = id;
  // 切换流程时清空上一次的运行结果，避免和新流程混在一起。
  void op.run(loadHistory);
}

function addStep(type: Step["type"]) {
  const name = `${stepLabel[type]}${form.value.steps.length + 1}`;
  if (type === "kb")
    form.value.steps.push({
      type,
      name,
      space_ids: [],
      query: "{{input}}",
      top_k: 3,
    });
  else if (type === "ai")
    form.value.steps.push({
      type,
      name,
      prompt:
        "仅根据以下资料回答问题；资料没有提到就说资料未覆盖。\n问题：{{input}}\n资料：\n{{检索.output}}",
      max_tokens: 1200,
    });
  else form.value.steps.push({ type, name, template: "{{input}}" });
}

function removeStep(index: number) {
  form.value.steps.splice(index, 1);
}

function openCreate() {
  if (!props.canWrite) return;
  editingId.value = "";
  editingFormat.value = "legacy_steps";
  form.value = { name: "", description: "", steps: [] };
  addStep("kb");
  addStep("text");
  editorOpen.value = true;
}

async function openEdit() {
  if (!active.value || !props.canWrite) return;
  if (active.value.format === "graph") {
    openGraphEditor(active.value.id);
    return;
  }
  await op.run(async () => {
    const workflowId = active.value!.id;
    const detail = await api.get<{
      name: string;
      description: string;
      definition: { format?: string; steps?: Step[] };
    }>(`/workflows/${workflowId}`);
    if (activeId.value !== workflowId) return;
    if (detail.definition.format === "graph") {
      editingFormat.value = "graph";
      throw new Error("图定义编辑尚未接入");
    }
    editingFormat.value = "legacy_steps";
    editingId.value = workflowId;
    form.value = {
      name: detail.name,
      description: detail.description,
      steps: detail.definition.steps ?? [],
    };
    editorOpen.value = true;
  });
}

async function save() {
  if (
    !props.canWrite ||
    editingFormat.value === "graph" ||
    workflows.value.find((item) => item.id === editingId.value)?.format ===
      "graph"
  ) {
    op.error.value = "无法使用步骤编辑器保存此流程";
    return;
  }
  const payload = {
    name: form.value.name.trim(),
    description: form.value.description.trim(),
    definition: { steps: form.value.steps },
  };
  if (!payload.name) return;
  await op.run(
    async () => {
      if (editingId.value)
        await api.put(`/workflows/${editingId.value}`, payload);
      else {
        const created = await api.post<{ id: string }>("/workflows", payload);
        activeId.value = created.id;
      }
      editorOpen.value = false;
      await loadWorkflows();
    },
    editingId.value ? "工作流已更新" : "工作流已创建",
  );
}

async function removeWorkflow() {
  if (!active.value || !props.canDelete) return;
  const target = active.value;
  await op.run(async () => {
    await ElMessageBox.confirm(`删除“${target.name}”？`, "删除确认", {
      type: "warning",
    });
    if (!transport.active()) return;
    await api.delete(`/workflows/${target.id}`);
    await loadWorkflows();
  }, "工作流已删除");
}

async function runNow() {
  if (
    !activeId.value ||
    !props.canWrite ||
    active.value?.format === "graph" ||
    running.value ||
    intent.checking.value ||
    confirming.value
  )
    return;
  const id = activeId.value;
  let confirmNew = false;
  if (intent.uncertain.value) {
    confirming.value = true;
    try {
      await ElMessageBox.confirm(
        "上次运行结果尚未确认，仍要发起一次新运行？",
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
  if (!transport.active() || activeId.value !== id) return;
  if (await intent.start(runInput.value, confirmNew)) await op.run(loadHistory);
}

onMounted(async () => {
  await op.run(loadWorkflows);
  if (!props.canKnowledge || !transport.active()) return;
  try {
    const data = await api.get<{ items: { id: string; name: string }[] }>(
      "/kb/spaces",
    );
    spaces.value = data.items;
  } catch {
    spaces.value = [];
  }
});
watch(
  () => intent.permissionError.value,
  (message) => {
    if (message) {
      history.value = [];
      runInput.value = "";
      op.error.value = message;
    }
  },
);
</script>

<template>
  <div class="flow-layout">
    <aside class="panel panel-flush flow-list">
      <div class="flow-panel-head">
        <span class="section-title">工作流</span>
        <el-dropdown
          v-if="canWrite"
          trigger="click"
          @command="
            (command: string) =>
              command === 'graph' ? openGraphEditor() : openCreate()
          "
          ><el-button :icon="Plus" text>新建</el-button
          ><template #dropdown
            ><el-dropdown-menu
              ><el-dropdown-item command="graph">可视化流程</el-dropdown-item
              ><el-dropdown-item command="steps"
                >分步流程</el-dropdown-item
              ></el-dropdown-menu
            ></template
          ></el-dropdown
        >
      </div>
      <div class="flow-items">
        <button
          v-for="item in workflows"
          :key="item.id"
          class="flow-item"
          :class="{ active: item.id === activeId }"
          @click="selectWorkflow(item.id)"
        >
          <span class="title">{{ item.name }}</span>
          <span class="meta"
            >{{ item.step_count }}
            {{ item.format === "graph" ? "个节点" : "个步骤" }}</span
          >
        </button>
        <div v-if="!workflows.length && !loading" class="empty-block">
          <div class="icon-tile soft">
            <i class="fa-solid fa-diagram-project" />
          </div>
          <div class="muted">还没有工作流</div>
        </div>
      </div>
    </aside>

    <section class="flow-main">
      <el-alert
        v-if="op.error.value"
        :title="op.error.value"
        type="error"
        :closable="false"
      />
      <div class="panel flow-toolbar">
        <div>
          <h2>{{ active?.name ?? "工作流编排" }}</h2>
          <p v-if="active?.description" class="sub">{{ active.description }}</p>
        </div>
        <div class="row-actions">
          <el-button
            v-if="active?.format === 'graph'"
            @click="openGraphEditor(active.id)"
            >画布</el-button
          >
          <el-button v-else-if="canWrite" :disabled="!active" @click="openEdit"
            >编辑</el-button
          >
          <el-button
            :icon="Delete"
            v-if="canDelete"
            text
            :disabled="!active"
            @click="removeWorkflow"
            >删除</el-button
          >
        </div>
      </div>

      <GraphWorkflowRunner
        v-if="active?.format === 'graph'"
        :key="active.id"
        :workflow-id="active.id"
        :can-run="canWrite && active.enabled"
      />
      <template v-else>
        <div class="panel flow-run">
          <div class="section-title">运行</div>
          <div class="flow-run-row">
            <el-input
              v-model="runInput"
              placeholder="输入本次任务内容"
              :disabled="
                !active || !canWrite || running || intent.checking.value
              "
              @keydown.enter="runNow"
            />
            <el-button
              type="primary"
              :icon="VideoPlay"
              :loading="running"
              :disabled="!active || !canWrite"
              @click="runNow"
            >
              {{ intent.uncertain.value ? "新运行" : "运行" }}
            </el-button>
            <el-button
              v-if="intent.current.value"
              :loading="intent.checking.value"
              :disabled="running"
              @click="intent.verify"
              >核对结果</el-button
            >
          </div>
          <el-alert
            v-if="intent.current.value?.error"
            :title="intent.current.value.error"
            type="error"
            :closable="false"
            show-icon
          />
          <el-tag v-if="!currentRun && intent.uncertain.value" type="warning"
            >结果待核对</el-tag
          >
          <div v-if="currentRun" class="flow-run-result">
            <div class="flow-run-head">
              <span
                class="status-chip"
                :class="
                  currentRun.status === 'succeeded'
                    ? 'passed'
                    : currentRun.status === 'failed'
                      ? 'failed'
                      : 'draft'
                "
              >
                {{ runLabels[currentRun.status] ?? currentRun.status }}
              </span>
            </div>
            <div
              v-for="step in currentRun.steps"
              :key="step.index"
              class="flow-step"
              :class="step.status"
            >
              <div class="flow-step-head">
                <span
                  class="badge"
                  :class="step.status === 'ok' ? 'tone-emerald' : 'tone-rose'"
                >
                  {{ step.index }} · {{ stepLabel[step.type] ?? step.type }}
                </span>
                <span class="flow-step-name">{{ step.name }}</span>
              </div>
              <pre v-if="step.output" class="flow-step-body">{{
                step.output
              }}</pre>
              <div v-if="step.error" class="danger">{{ step.error }}</div>
            </div>
            <div v-if="currentRun.output" class="flow-final">
              <div class="section-title">最终输出</div>
              <pre class="flow-step-body">{{ currentRun.output }}</pre>
            </div>
          </div>
        </div>

        <div class="panel">
          <div class="section-title">
            运行历史
            <span class="tail">{{ history.length }} 次</span>
          </div>
          <el-table v-if="history.length" :data="history" style="width: 100%">
            <el-table-column label="状态" width="110">
              <template #default="{ row }">
                <span
                  class="status-chip"
                  :class="
                    row.status === 'succeeded'
                      ? 'passed'
                      : row.status === 'failed'
                        ? 'failed'
                        : 'draft'
                  "
                >
                  {{ runLabels[row.status] ?? row.status }}
                </span>
              </template>
            </el-table-column>
            <el-table-column
              prop="input"
              label="输入"
              min-width="220"
              show-overflow-tooltip
            />
            <el-table-column
              prop="output"
              label="输出"
              min-width="260"
              show-overflow-tooltip
            />
            <el-table-column label="时间" width="180">
              <template #default="{ row }">
                <span class="muted">{{
                  new Date(row.started_at).toLocaleString()
                }}</span>
              </template>
            </el-table-column>
          </el-table>
          <div v-else class="empty-state">
            <div class="glyph"><i class="fa-solid fa-clock-rotate-left" /></div>
            <div class="title">还没有运行记录</div>
          </div>
        </div>
      </template>
    </section>

    <GraphWorkflowEditor
      v-if="graphEditorOpen"
      :key="graphEditingId || 'new'"
      :workflow-id="graphEditingId"
      :can-write="canWrite"
      @close="graphEditorOpen = false"
      @saved="graphSaved"
      @denied="
        (message) => {
          graphEditorOpen = false;
          op.error.value = message;
        }
      "
    />
    <el-dialog
      v-model="editorOpen"
      :title="editingId ? '编辑工作流' : '新建工作流'"
      width="760px"
    >
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input
            v-model="form.name"
            maxlength="160"
            placeholder="例如：基于知识库的设备问答"
          />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" maxlength="2000" />
        </el-form-item>
      </el-form>

      <div class="section-title">
        步骤
        <span class="tail">{{ form.steps.length }} 个</span>
      </div>
      <div class="flow-editor">
        <div
          v-for="(step, index) in form.steps"
          :key="index"
          class="flow-editor-step"
        >
          <div class="flow-editor-head">
            <span class="badge gradient">{{ index + 1 }}</span>
            <el-select v-model="step.type" style="width: 130px">
              <el-option label="知识检索" value="kb" />
              <el-option label="模型回答" value="ai" />
              <el-option label="文本整理" value="text" />
            </el-select>
            <el-input
              v-model="step.name"
              placeholder="步骤名称"
              style="width: 180px"
            />
            <span class="spacer" style="flex: 1" />
            <el-button :icon="Delete" text @click="removeStep(index)" />
          </div>

          <div v-if="step.type === 'kb'" class="flow-editor-fields">
            <el-select
              v-model="step.space_ids"
              multiple
              placeholder="选择知识空间"
              style="width: 100%"
            >
              <el-option
                v-for="s in spaces"
                :key="s.id"
                :label="s.name"
                :value="s.id"
              />
            </el-select>
            <el-input
              v-model="step.query"
              placeholder="检索内容，可用 {{input}}"
            />
            <el-input-number v-model="step.top_k" :min="1" :max="10" />
          </div>
          <div v-else-if="step.type === 'ai'" class="flow-editor-fields">
            <el-input
              v-model="step.prompt"
              type="textarea"
              :rows="4"
              resize="none"
              placeholder="提示词，可用 {{input}} 与 {{步骤名.output}}"
            />
          </div>
          <div v-else class="flow-editor-fields">
            <el-input
              v-model="step.template"
              type="textarea"
              :rows="3"
              resize="none"
              placeholder="模板，可用 {{input}} 与 {{步骤名.output}}"
            />
          </div>
        </div>
      </div>
      <div class="row-actions" style="margin-top: 12px">
        <el-button :icon="Plus" @click="addStep('kb')">加知识检索</el-button>
        <el-button :icon="Plus" @click="addStep('ai')">加模型回答</el-button>
        <el-button :icon="Plus" @click="addStep('text')">加文本整理</el-button>
      </div>

      <template #footer>
        <el-button @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="op.busy.value" @click="save"
          >保存</el-button
        >
      </template>
    </el-dialog>
  </div>
</template>
