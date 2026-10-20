<script setup lang="ts">
/** 会话、历史与记忆入口分别展示，沿用现有统一外壳和对话样式。 */
import { computed, nextTick, shallowRef, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessageBox } from "element-plus";
import { Promotion, VideoPause, Delete, Plus } from "@element-plus/icons-vue";
import { useSession } from "../../stores/session";
import { useScopedChatSessions } from "./useScopedChatSessions";
import { useChatConversation } from "./useChatConversation";
import ChatHistory from "./ChatHistory.vue";
import MemoryWorkspace from "./MemoryWorkspace.vue";

defineProps<{ config?: string }>();
const account = useSession();
const route = useRoute();
const router = useRouter();
const mode = computed(() =>
  route.path === "/history"
    ? "history"
    : route.path === "/memory"
      ? "memory"
      : "chat",
);
const scrollBox = shallowRef<HTMLElement>();
const showReasoning = shallowRef(true);
let clearConversation = () => {};
const records = useScopedChatSessions(
  () =>
    account.value
      ? {
          userId: account.value.user.id,
          workspaceId: account.value.workspace.id,
        }
      : null,
  () => clearConversation(),
);
const { sessions, activeId, active, turns, storageError } = records;
async function scrollToEnd() {
  await nextTick();
  if (scrollBox.value) scrollBox.value.scrollTop = scrollBox.value.scrollHeight;
}
const conversation = useChatConversation(
  records,
  () => mode.value === "chat",
  scrollToEnd,
);
clearConversation = conversation.clear;
const { draft, sending, modelName, profileReady, requestError, send, stop } =
  conversation;
const canSend = computed(
  () =>
    Boolean(draft.value.trim()) &&
    !sending.value &&
    profileReady.value &&
    account.can("m02.write"),
);
const examples = [
  "帮我写一段设备点检的标准作业步骤",
  "解释一下预测性维护里的振动阈值怎么定",
];
watch(
  () => [records.key.value, route.query.session],
  () => {
    if (mode.value === "chat" && typeof route.query.session === "string")
      records.select(route.query.session);
  },
  { immediate: true, flush: "sync" },
);

function createSession() {
  stop();
  const created = records.create();
  draft.value = "";
  if (created && mode.value !== "chat")
    void router.push({ path: "/chat", query: { session: created.id } });
}
function selectSession(id: string) {
  stop();
  draft.value = "";
  requestError.value = "";
  if (records.select(id))
    void router.push({ path: "/chat", query: { session: id } });
}
async function removeSession(id: string) {
  const target = sessions.value.find((item) => item.id === id);
  if (!target) return;
  const ticket = records.capture();
  try {
    await ElMessageBox.confirm(`删除“${target.title}”？`, "删除对话", {
      type: "warning",
    });
  } catch {
    return;
  }
  if (!records.isCurrent(ticket)) return;
  if (activeId.value === id) stop();
  records.remove(id);
}
async function restart() {
  if (!active.value) return;
  const ticket = records.capture();
  const id = active.value.id;
  try {
    await ElMessageBox.confirm("清空这段对话？", "清空确认", {
      type: "warning",
    });
  } catch {
    return;
  }
  if (!records.isCurrent(ticket)) return;
  stop();
  if (records.clearSession(id)) {
    draft.value = "";
    requestError.value = "";
  }
}
</script>

<template>
  <MemoryWorkspace v-if="mode === 'memory'" />
  <div v-else-if="mode === 'history'" class="page-stack">
    <el-alert
      v-if="storageError"
      :title="storageError"
      type="error"
      :closable="false"
      show-icon
      ><el-button link @click="records.reload">重新读取</el-button></el-alert
    >
    <ChatHistory
      :sessions="sessions"
      @open="selectSession"
      @remove="removeSession"
      @create="createSession"
    />
  </div>
  <div v-else class="chat-layout">
    <aside class="chat-sessions panel panel-flush">
      <div class="chat-sessions-head">
        <span class="section-title">对话</span
        ><el-button :icon="Plus" text @click="createSession">新建</el-button>
      </div>
      <div class="chat-session-list">
        <button
          v-for="item in sessions"
          :key="item.id"
          class="chat-session-item"
          :class="{ active: item.id === activeId }"
          @click="selectSession(item.id)"
        >
          <span class="title">{{ item.title }}</span
          ><span class="meta">{{ item.turns.length }} 条</span>
        </button>
        <div v-if="!sessions.length" class="empty-block">
          <div class="hint">还没有对话</div>
        </div>
      </div>
    </aside>
    <section class="chat-main panel panel-flush">
      <header class="chat-head">
        <div class="chat-head-title">
          <i class="fa-solid fa-comment-dots" /><strong>AI 对话</strong
          ><span v-if="modelName" class="badge tone-emerald"
            ><span class="badge-dot" />{{ modelName }}</span
          >
        </div>
        <div class="row-actions">
          <el-button :icon="Delete" text :disabled="!active" @click="restart"
            >清空当前</el-button
          >
        </div>
      </header>
      <div ref="scrollBox" class="chat-scroll">
        <el-alert
          v-if="storageError"
          :title="storageError"
          type="error"
          :closable="false"
          show-icon
          ><el-button link @click="records.reload"
            >重新读取</el-button
          ></el-alert
        >
        <el-alert
          v-if="requestError"
          :title="requestError"
          type="error"
          :closable="false"
          show-icon
        />
        <div v-if="!turns.length" class="chat-empty">
          <div class="empty-state">
            <div class="glyph">
              <i class="fa-solid fa-wand-magic-sparkles" />
            </div>
            <div class="title">从这里开始提问</div>
          </div>
          <div class="chat-examples">
            <button
              v-for="example in examples"
              :key="example"
              class="chat-example"
              @click="draft = example"
            >
              {{ example }}
            </button>
          </div>
        </div>
        <div
          v-for="(turn, index) in turns"
          :key="index"
          class="chat-turn"
          :class="turn.role"
        >
          <div class="chat-avatar">
            <i
              :class="
                turn.role === 'user' ? 'fa-solid fa-user' : 'fa-solid fa-robot'
              "
            />
          </div>
          <div class="chat-bubble">
            <div
              v-if="turn.role === 'assistant' && turn.reasoning"
              class="chat-reasoning"
            >
              <button
                class="chat-reasoning-head"
                style="border: 0; background: transparent"
                :aria-expanded="showReasoning"
                @click="showReasoning = !showReasoning"
              >
                <i class="fa-solid fa-brain" />思考过程
              </button>
              <div v-if="showReasoning" class="chat-reasoning-body">
                {{ turn.reasoning }}
              </div>
            </div>
            <div class="chat-text">
              {{
                turn.content ||
                (sending && index === turns.length - 1 ? "…" : "")
              }}
            </div>
          </div>
        </div>
      </div>
      <footer class="chat-composer">
        <el-input
          v-model="draft"
          type="textarea"
          :rows="2"
          resize="none"
          :placeholder="
            profileReady
              ? '输入问题，Enter 发送，Shift+Enter 换行'
              : '模型未就绪，暂时无法对话'
          "
          :disabled="!profileReady || !account.can('m02.write')"
          @keydown.enter.exact.prevent="canSend && send()"
        />
        <div class="chat-composer-actions">
          <span class="muted chat-hint"
            ><i class="fa-solid fa-shield-halved" />本机记录</span
          ><el-button v-if="sending" :icon="VideoPause" @click="stop"
            >停止</el-button
          ><el-button
            v-else
            type="primary"
            :icon="Promotion"
            :disabled="!canSend"
            @click="send"
            >发送</el-button
          >
        </div>
      </footer>
    </section>
  </div>
</template>
