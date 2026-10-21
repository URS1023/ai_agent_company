<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { ElMessageBox } from "element-plus";
import { Plus, Delete, Edit } from "@element-plus/icons-vue";
import { useNativeChat } from "./useNativeChat";
import { fileView } from "./nativeTypes";
import type { ChatSnapshot, ChatActions } from "@openwebui-chat";
import NativeChatIsland from "./NativeChatIsland.vue";
const props = defineProps<{
  canWrite: boolean;
  canDelete: boolean;
  historyMode: boolean;
  displayName: string;
}>();
const route = useRoute(),
  router = useRouter();
const chat = useNativeChat(
  () => props.canWrite,
  () => props.canDelete,
);
const createOpen = ref(false),
  title = ref("新对话"),
  instance = ref("");
async function create() {
  if (await chat.create(instance.value, title.value)) {
    createOpen.value = false;
    await router.push({
      path: "/chat",
      query: { conversation: chat.detail.value!.id },
    });
  }
}
async function select(id: string) {
  if (props.historyMode) {
    await router.push({ path: "/chat", query: { conversation: id } });
    return;
  }
  await chat.open(id);
}
async function rename() {
  if (!chat.detail.value) return;
  try {
    const value = await ElMessageBox.prompt("", "重命名对话", {
      inputValue: chat.detail.value.title,
      inputValidator: (text: string) =>
        (!!text.trim() && text.length <= 200) || "请输入200字以内的名称",
    });
    await chat.rename(value.value);
  } catch {}
}
async function remove(id: string) {
  const target = chat.items.value.find((item) => item.id === id);
  if (!target) return;
  try {
    await ElMessageBox.confirm(`删除“${target.title}”？`, "删除对话", {
      type: "warning",
    });
    await chat.remove(id);
  } catch {}
}
const snapshot = computed<ChatSnapshot>(() => ({
  conversationId: chat.detail.value?.id ?? "",
  history: chat.history.value,
  displayName: props.displayName,
  models: chat.models.value,
  modelId: chat.modelId.value,
  canWrite: props.canWrite && chat.detail.value?.status === "ready",
  busy:
    chat.busy.value ||
    chat.turn.busy.value ||
    chat.files.busy.value ||
    chat.loading.value,
  status: chat.turn.current.value?.status ?? chat.detail.value?.status ?? "",
  draft: chat.draft.value,
  files: chat.files.files.value.map(fileView),
  attachmentIds: chat.files.selected.value,
  messageAttachments: chat.messageAttachments.value,
  error: chat.error.value,
  canStop: props.canWrite && chat.turn.canStop.value,
  uncertain: chat.turn.uncertain.value,
  editing: chat.turn.editing.value,
}));
const actions: ChatActions = {
  draft: (value) => {
    chat.draft.value = value;
  },
  model: (id) => {
    if (
      !chat.turn.busy.value &&
      chat.models.value.some((item) => item.id === id)
    )
      chat.modelId.value = id;
  },
  send: () => {
    chat.error.value = "";
    void chat.turn.send();
  },
  stop: () => {
    void chat.turn.stop();
  },
  verify: () => {
    chat.error.value = "";
    void chat.turn.verify();
  },
  branch: chat.branch,
  upload: (file) => {
    void chat.files.upload(file);
  },
  attach: chat.files.attach,
  refreshFile: (id) => {
    void chat.files.action(id, "refreshes");
  },
  reconcileFile: (id) => {
    void chat.files.action(id, "reconciliations");
  },
  download: (id) => {
    const source = Object.values(chat.detail.value?.message_attachments ?? {})
      .flat()
      .find((file) => file.id === id);
    void chat.files.download(id, source?.source_file.name);
  },
  resolve: (id, approved) => chat.turn.resolve(id, approved),
  beginEdit: chat.turn.beginEdit,
  updateEdit: chat.turn.updateEdit,
  cancelEdit: chat.turn.cancelEdit,
  editAndSend: () => {
    chat.error.value = "";
    void chat.turn.editAndSend();
  },
  regenerate: (id) => {
    chat.error.value = "";
    void chat.turn.regenerate(id);
  },
};
onMounted(async () => {
  await chat.initialize(!props.historyMode && !route.query.conversation);
  instance.value = chat.instances.value[0]?.id ?? "";
  if (!props.historyMode && typeof route.query.conversation === "string")
    await chat.open(route.query.conversation);
});
watch(
  () => route.query.conversation,
  (id) => {
    if (
      !props.historyMode &&
      typeof id === "string" &&
      chat.detail.value?.id !== id
    )
      void chat.open(id);
  },
);
</script>
<template>
  <div v-if="historyMode" class="page-stack">
    <el-alert
      v-if="chat.error.value"
      :title="chat.error.value"
      type="error"
      :closable="false"
    />
    <section class="panel">
      <div class="toolbar">
        <strong>对话历史</strong><span class="spacer" /><el-button
          @click="chat.loadDirectory"
          >刷新</el-button
        ><el-button
          v-if="canWrite"
          type="primary"
          :icon="Plus"
          @click="createOpen = true"
          >新建对话</el-button
        >
      </div>
      <el-table :data="chat.items.value" v-loading="chat.loading.value"
        ><el-table-column
          prop="title"
          label="对话"
          min-width="220"
        /><el-table-column label="最近更新" width="200"
          ><template #default="{ row }">{{
            new Date(row.updated_at).toLocaleString("zh-CN")
          }}</template></el-table-column
        ><el-table-column label="操作" width="150"
          ><template #default="{ row }"
            ><el-button link @click="select(row.id)">打开</el-button
            ><el-button
              v-if="canDelete"
              link
              type="danger"
              @click="remove(row.id)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      ><el-pagination
        v-model:current-page="chat.page.value"
        :total="chat.total.value"
        :page-size="20"
        layout="prev,pager,next"
        @current-change="chat.loadDirectory"
      />
    </section>
    <router-link to="/history?source=local">本机旧记录</router-link>
  </div>
  <div v-else class="chat-layout">
    <aside class="chat-sessions panel panel-flush">
      <div class="chat-sessions-head">
        <strong>对话</strong
        ><el-button v-if="canWrite" :icon="Plus" text @click="createOpen = true"
          >新建</el-button
        >
      </div>
      <div class="chat-session-list" v-loading="chat.loading.value">
        <button
          v-for="item in chat.items.value"
          :key="item.id"
          class="chat-session-item"
          :class="{ active: chat.detail.value?.id === item.id }"
          @click="select(item.id)"
        >
          <span class="title">{{ item.title }}</span
          ><span v-if="item.status !== 'ready'" class="meta">{{
            item.status === "unknown" ? "待核对" : "准备中"
          }}</span>
        </button>
      </div>
      <el-pagination
        small
        v-model:current-page="chat.page.value"
        :total="chat.total.value"
        :page-size="20"
        layout="prev,next"
        @current-change="chat.loadDirectory"
      />
    </aside>
    <section class="chat-main panel panel-flush native-chat-main">
      <header class="chat-head">
        <strong>{{ chat.detail.value?.title ?? "AI 对话" }}</strong
        ><span class="spacer" /><el-button
          v-if="chat.identityReview.value"
          :disabled="chat.busy.value"
          @click="chat.reconcileIdentity"
          >核对身份</el-button
        ><el-button
          v-if="chat.detail.value"
          :disabled="
            chat.turn.posting.value ||
            chat.loading.value ||
            chat.turn.uncertain.value ||
            !!chat.turn.editing.value
          "
          text
          @click="chat.open(chat.detail.value!.id)"
          >刷新</el-button
        ><el-button
          v-if="canWrite && chat.detail.value"
          :icon="Edit"
          text
          :disabled="chat.turn.busy.value"
          @click="rename"
          >重命名</el-button
        ><el-button
          v-if="canDelete && chat.detail.value"
          :icon="Delete"
          text
          :disabled="chat.turn.busy.value"
          @click="remove(chat.detail.value!.id)"
          >删除</el-button
        >
      </header>
      <NativeChatIsland
        v-if="chat.detail.value"
        :key="chat.detail.value.id"
        :snapshot="snapshot"
        :actions="actions"
        @failure="(message) => (chat.error.value = message)"
        @recovered="
          (message) => {
            if (chat.error.value === message) chat.error.value = '';
          }
        "
      />
      <div v-else class="chat-empty">
        <el-alert
          v-if="chat.error.value"
          :title="chat.error.value"
          type="error"
          :closable="false"
        /><el-empty description="暂无对话"
          ><el-button
            v-if="canWrite"
            type="primary"
            :disabled="!chat.instances.value.length"
            @click="createOpen = true"
            >新建对话</el-button
          ></el-empty
        >
      </div>
    </section>
  </div>
  <el-dialog
    v-model="createOpen"
    title="新建对话"
    width="460px"
    :close-on-click-modal="false"
    ><el-form label-position="top"
      ><el-form-item label="名称"
        ><el-input v-model="title" maxlength="200" /></el-form-item
      ><el-form-item label="对话服务"
        ><el-select v-model="instance"
          ><el-option
            v-for="item in chat.instances.value"
            :key="item.id"
            :value="item.id"
            :label="item.name" /></el-select></el-form-item></el-form
    ><el-alert
      v-if="chat.error.value"
      :title="chat.error.value"
      type="error"
      :closable="false"
    /><template #footer
      ><el-button :disabled="chat.busy.value" @click="createOpen = false"
        >取消</el-button
      ><el-button
        v-if="chat.identityReview.value"
        :disabled="chat.busy.value || !canWrite"
        @click="chat.reconcileIdentity"
        >核对身份</el-button
      ><el-button
        type="primary"
        :loading="chat.busy.value"
        :disabled="!instance || !title.trim() || !canWrite"
        @click="create"
        >创建</el-button
      ></template
    ></el-dialog
  >
</template>
<style scoped>
.native-chat-main {
  display: flex;
  flex-direction: column;
  min-height: 0;
}
.native-chat-main > .native-chat-island {
  flex: 1;
  min-height: 0;
}
.native-chat-main > .chat-head {
  flex: none;
}
.chat-sessions > .el-pagination {
  padding: 12px;
  justify-content: center;
}
.page-stack .el-pagination {
  margin-top: 14px;
}
</style>
