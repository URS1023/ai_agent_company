<script setup lang="ts">
/** 平台智能体应用：配置提示词与知识空间，直接在本系统内对话。 */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, shallowRef } from "vue";
import { Delete, Plus, Promotion, VideoPause } from "@element-plus/icons-vue";
import { api, stream } from "../../api/client";
import { createDeltaReader } from "../../api/sse";
import { useOperation } from "../../composables/useOperation";

interface AgentItem {
  id: string;
  name: string;
  description: string;
  space_count: number;
}
interface AgentDetail extends AgentItem {
  prompt: string;
  space_ids: string[];
  top_k: number;
  max_tokens: number;
  temperature: number;
  revision: number;
}
interface Turn {
  role: "user" | "assistant";
  content: string;
}

const op = useOperation();
const agents = ref<AgentItem[]>([]);
const activeId = ref("");
const spaces = ref<{ id: string; name: string }[]>([]);

const editorOpen = ref(false);
const editingId = ref("");
const form = ref({
  name: "",
  description: "",
  prompt: "",
  space_ids: [] as string[],
  top_k: 3,
  max_tokens: 1200,
  temperature: 0.3,
});

const turns = ref<Turn[]>([]);
const question = ref("");
const asking = ref(false);
const scrollBox = ref<HTMLElement>();
const controller = shallowRef<AbortController>();

const active = computed(() => agents.value.find((a) => a.id === activeId.value));
const activeSpaceLabel = computed(() => {
  const item = agents.value.find((a) => a.id === activeId.value);
  return item ? `${item.space_count} 个知识空间` : "未绑定知识空间";
});

async function loadAgents() {
  const data = await api.get<{ items: AgentItem[] }>("/agents");
  agents.value = data.items;
  if (!agents.value.some((a) => a.id === activeId.value))
    activeId.value = agents.value[0]?.id ?? "";
}

function selectAgent(id: string) {
  activeId.value = id;
  turns.value = [];
  question.value = "";
}

function openCreate() {
  editingId.value = "";
  form.value = {
    name: "",
    description: "",
    prompt: "你是制造业现场助手。回答要具体、可执行；资料未覆盖时明确说明，不要编造参数。",
    space_ids: [],
    top_k: 3,
    max_tokens: 1200,
    temperature: 0.3,
  };
  editorOpen.value = true;
}

async function openEdit() {
  if (!active.value) return;
  const detail = await api.get<AgentDetail>(`/agents/${active.value.id}`);
  editingId.value = detail.id;
  form.value = {
    name: detail.name,
    description: detail.description,
    prompt: detail.prompt,
    space_ids: detail.space_ids ?? [],
    top_k: detail.top_k,
    max_tokens: detail.max_tokens,
    temperature: detail.temperature,
  };
  editorOpen.value = true;
}

async function save() {
  const payload = { ...form.value, name: form.value.name.trim() };
  if (!payload.name) return;
  await op.run(
    async () => {
      if (editingId.value) await api.put(`/agents/${editingId.value}`, payload);
      else {
        const created = await api.post<{ id: string }>("/agents", payload);
        activeId.value = created.id;
        turns.value = [];
      }
      editorOpen.value = false;
      await loadAgents();
    },
    editingId.value ? "应用已更新" : "应用已创建",
  );
}

async function removeAgent() {
  if (!active.value) return;
  await op.run(async () => {
    await api.delete(`/agents/${active.value!.id}`);
    turns.value = [];
    await loadAgents();
  }, "应用已删除");
}

async function scrollToEnd() {
  await nextTick();
  if (scrollBox.value) scrollBox.value.scrollTop = scrollBox.value.scrollHeight;
}

function readerFor(turn: Turn) {
  return createDeltaReader((delta) => {
    if (delta.content) turn.content += delta.content;
  });
}

async function ask() {
  const text = question.value.trim();
  if (!text || !activeId.value || asking.value) return;
  question.value = "";
  turns.value.push({ role: "user", content: text });
  const reply: Turn = { role: "assistant", content: "" };
  turns.value.push(reply);
  await scrollToEnd();

  asking.value = true;
  controller.value = new AbortController();
  const reader = readerFor(reply);
  try {
    await stream(
      `/agents/${activeId.value}/messages`,
      { question: text },
      (chunk) => {
        reader.push(chunk);
        void scrollToEnd();
      },
      controller.value.signal,
    );
  } catch (error) {
    if (!reply.content)
      reply.content = error instanceof Error ? `本次回答未完成：${error.message}` : "本次回答未完成";
  } finally {
    reader.flush();
    asking.value = false;
    controller.value = undefined;
    await scrollToEnd();
  }
}

function stop() {
  controller.value?.abort();
}

onMounted(async () => {
  await op.run(loadAgents);
  try {
    const data = await api.get<{ items: { id: string; name: string }[] }>("/kb/spaces");
    spaces.value = data.items;
  } catch {
    spaces.value = [];
  }
});
onBeforeUnmount(() => controller.value?.abort());
</script>

<template>
  <div class="agent-layout">
    <aside class="panel panel-flush agent-list">
      <div class="agent-panel-head">
        <span class="section-title">智能体应用</span>
        <el-button :icon="Plus" text @click="openCreate">新建</el-button>
      </div>
      <div class="agent-items">
        <button
          v-for="item in agents"
          :key="item.id"
          class="agent-item"
          :class="{ active: item.id === activeId }"
          @click="selectAgent(item.id)"
        >
          <span class="title">{{ item.name }}</span>
          <span class="meta">{{ item.space_count }} 个知识空间</span>
        </button>
        <div v-if="!agents.length" class="empty-block">
          <div class="icon-tile soft"><i class="fa-solid fa-robot" /></div>
          <div class="muted">还没有智能体应用</div>
        </div>
      </div>
    </aside>

    <section class="panel panel-flush agent-main">
      <header class="agent-head">
        <div class="agent-head-title">
          <i class="fa-solid fa-robot" />
          <strong>{{ active?.name ?? "智能体应用" }}</strong>
          <span class="badge tone-violet">{{ activeSpaceLabel }}</span>
        </div>
        <div class="row-actions">
          <el-button :disabled="!active" @click="openEdit">编辑</el-button>
          <el-button :icon="Delete" text :disabled="!active" @click="removeAgent">删除</el-button>
        </div>
      </header>

      <div ref="scrollBox" class="agent-scroll">
        <div v-if="!turns.length" class="empty-state">
          <div class="glyph"><i class="fa-solid fa-comments" /></div>
          <div class="title">{{ active ? "向这个应用提问" : "先创建或选择一个应用" }}</div>
          <div class="hint">
            {{ active?.description || "应用会按你设定的提示词回答，并自动检索绑定的知识空间。" }}
          </div>
        </div>
        <div v-for="(turn, index) in turns" :key="index" class="chat-turn" :class="turn.role">
          <div class="chat-avatar">
            <i :class="turn.role === 'user' ? 'fa-solid fa-user' : 'fa-solid fa-robot'" />
          </div>
          <div class="chat-bubble">
            <div class="chat-text">
              {{ turn.content || (asking && index === turns.length - 1 ? "…" : "") }}
            </div>
          </div>
        </div>
      </div>

      <footer class="agent-composer">
        <el-input
          v-model="question"
          type="textarea"
          :rows="2"
          resize="none"
          placeholder="输入问题，Enter 发送，Shift+Enter 换行"
          :disabled="!active"
          @keydown.enter.exact.prevent="ask"
        />
        <div class="chat-composer-actions">
          <span class="muted chat-hint">
            <i class="fa-solid fa-book-open" /> 回答会引用绑定的知识空间
          </span>
          <el-button v-if="asking" :icon="VideoPause" @click="stop">停止</el-button>
          <el-button
            v-else
            type="primary"
            :icon="Promotion"
            :disabled="!active || !question.trim()"
            @click="ask"
          >
            提问
          </el-button>
        </div>
      </footer>
    </section>

    <el-dialog v-model="editorOpen" :title="editingId ? '编辑应用' : '新建应用'" width="680px">
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="160" placeholder="例如：设备点检问答助手" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="form.description" maxlength="2000" />
        </el-form-item>
        <el-form-item label="系统提示词">
          <el-input v-model="form.prompt" type="textarea" :rows="5" resize="none" />
        </el-form-item>
        <el-form-item label="绑定知识空间">
          <el-select v-model="form.space_ids" multiple placeholder="可多选；不选则纯提示词回答" style="width: 100%">
            <el-option v-for="s in spaces" :key="s.id" :label="s.name" :value="s.id" />
          </el-select>
        </el-form-item>
        <div class="form-grid-3">
          <el-form-item label="检索条数">
            <el-input-number v-model="form.top_k" :min="1" :max="10" />
          </el-form-item>
          <el-form-item label="最大输出">
            <el-input-number v-model="form.max_tokens" :min="64" :max="4096" :step="128" />
          </el-form-item>
          <el-form-item label="温度">
            <el-input-number v-model="form.temperature" :min="0" :max="2" :step="0.1" />
          </el-form-item>
        </div>
      </el-form>
      <template #footer>
        <el-button @click="editorOpen = false">取消</el-button>
        <el-button type="primary" :loading="op.busy.value" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
