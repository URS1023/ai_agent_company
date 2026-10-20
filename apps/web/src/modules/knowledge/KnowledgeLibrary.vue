<script setup lang="ts">
/** 保留空间侧栏、语义检索和文档各自布局，文件处理交给文档组件。 */
import { computed, onMounted, onScopeDispose, ref, shallowRef } from "vue";
import { ElMessageBox } from "element-plus";
import { ApiError, type ListResult } from "../../api/client";
import { useKnowledgeRequests } from "./useKnowledgeRequests";
import type { KnowledgeSpace } from "./knowledge";
import KnowledgeDocuments from "./KnowledgeDocuments.vue";
import KnowledgeSearch from "./KnowledgeSearch.vue";
defineProps<{ canWrite: boolean; canDelete: boolean; canFiles: boolean }>();
const transport = useKnowledgeRequests();
const spaces = ref<KnowledgeSpace[]>([]),
  activeId = shallowRef(""),
  loading = shallowRef(false),
  busy = shallowRef(false),
  error = shallowRef("");
const spaceDialog = shallowRef(false),
  fields = ref<Record<string, string>>({}),
  spaceForm = ref({ name: "", description: "" });
const openRequest = shallowRef({ id: "", sequence: 0 }),
  searchGeneration = shallowRef(0);
const active = computed(() =>
  spaces.value.find((space) => space.id === activeId.value),
);
let sequence = 0;
async function load(preferredId?: string) {
  const activeAtStart = activeId.value;
  const current = ++sequence;
  loading.value = true;
  error.value = "";
  try {
    const result =
      await transport.send<ListResult<KnowledgeSpace>>("/kb/spaces");
    if (!transport.active() || current !== sequence) return;
    spaces.value = result.items;
    const preferred =
      activeId.value !== activeAtStart
        ? activeId.value
        : (preferredId ?? activeId.value);
    activeId.value = result.items.some((item) => item.id === preferred)
      ? preferred
      : (result.items[0]?.id ?? "");
  } catch (failure) {
    if (transport.active() && current === sequence) {
      spaces.value = [];
      activeId.value = "";
      error.value =
        failure instanceof Error ? failure.message : "无法读取知识空间";
    }
  } finally {
    if (transport.active() && current === sequence) loading.value = false;
  }
}
function select(id: string) {
  activeId.value = id;
  openRequest.value = { id: "", sequence: 0 };
}
function open(id: string) {
  openRequest.value = { id, sequence: openRequest.value.sequence + 1 };
}
function clearCitations() {
  searchGeneration.value++;
  openRequest.value = { id: "", sequence: 0 };
}
function beginCreate() {
  fields.value = {};
  error.value = "";
  spaceForm.value = { name: "", description: "" };
  spaceDialog.value = true;
}
async function create() {
  const name = spaceForm.value.name.trim();
  if (busy.value || !name) return;
  busy.value = true;
  fields.value = {};
  error.value = "";
  try {
    const result = await transport.send<KnowledgeSpace>("/kb/spaces", {
      method: "POST",
      body: JSON.stringify({
        name,
        description: spaceForm.value.description.trim(),
      }),
    });
    if (!transport.active()) return;
    spaceDialog.value = false;
    spaceForm.value = { name: "", description: "" };
    await load(result.id);
  } catch (failure) {
    if (transport.active()) {
      error.value = failure instanceof Error ? failure.message : "创建失败";
      if (failure instanceof ApiError) fields.value = failure.fields;
    }
  } finally {
    if (transport.active()) busy.value = false;
  }
}
async function remove() {
  const target = active.value;
  if (!target || busy.value) return;
  try {
    await ElMessageBox.confirm(`删除知识空间“${target.name}”？`, "删除确认", {
      type: "warning",
    });
  } catch {
    return;
  }
  if (!transport.active()) return;
  busy.value = true;
  error.value = "";
  try {
    await transport.send(`/kb/spaces/${encodeURIComponent(target.id)}`, {
      method: "DELETE",
    });
    if (!transport.active()) return;
    clearCitations();
    activeId.value = "";
    await load("");
  } catch (failure) {
    if (transport.active())
      error.value = failure instanceof Error ? failure.message : "删除失败";
  } finally {
    if (transport.active()) busy.value = false;
  }
}
onMounted(() => load());
onScopeDispose(() => {
  spaces.value = [];
  activeId.value = "";
  spaceForm.value = { name: "", description: "" };
  openRequest.value = { id: "", sequence: 0 };
});
</script>
<template>
  <div class="kb-layout">
    <aside class="panel panel-flush kb-spaces">
      <div class="kb-panel-head">
        <span class="section-title">知识空间</span
        ><el-button v-if="canWrite" text :disabled="busy" @click="beginCreate"
          >新建</el-button
        >
      </div>
      <div v-loading="loading" class="kb-space-list">
        <button
          v-for="item in spaces"
          :key="item.id"
          class="kb-space-item"
          :class="{ active: item.id === activeId }"
          @click="select(item.id)"
        >
          <span class="title">{{ item.name }}</span
          ><span class="meta">{{ item.document_count }} 份文档</span></button
        ><el-empty
          v-if="!spaces.length && !loading"
          description="暂无知识空间"
          :image-size="64"
        />
      </div>
    </aside>
    <section class="kb-main">
      <el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
        show-icon
        ><el-button link @click="load()">刷新</el-button></el-alert
      >
      <div class="panel kb-toolbar">
        <div class="kb-title">
          <h2>{{ active?.name ?? "知识库" }}</h2>
          <p v-if="active?.description" class="sub">{{ active.description }}</p>
        </div>
        <el-button v-if="canDelete && active" :disabled="busy" @click="remove"
          >删除空间</el-button
        >
      </div>
      <template v-if="active"
        ><KnowledgeSearch
          :key="`${active.id}:${searchGeneration}`"
          :space-id="active.id"
          @open="open" /><KnowledgeDocuments
          :key="active.id"
          :space-id="active.id"
          :can-write="canWrite"
          :can-delete="canDelete"
          :can-files="canFiles"
          :open-request="openRequest"
          @changed="load()"
          @invalidate="clearCitations"
      /></template>
    </section>
    <el-dialog
      v-model="spaceDialog"
      title="新建知识空间"
      width="440px"
      :close-on-click-modal="false"
      :close-on-press-escape="!busy"
      :show-close="!busy"
      ><el-alert
        v-if="error"
        :title="error"
        type="error"
        :closable="false"
      /><el-form label-position="top" @submit.prevent="create"
        ><el-form-item label="名称" required :error="fields.name"
          ><el-input
            v-model="spaceForm.name"
            aria-label="名称"
            :maxlength="160"
            :disabled="busy" /></el-form-item
        ><el-form-item label="说明" :error="fields.description"
          ><el-input
            v-model="spaceForm.description"
            aria-label="说明"
            :maxlength="2000"
            :disabled="busy" /></el-form-item></el-form
      ><template #footer
        ><el-button :disabled="busy" @click="spaceDialog = false"
          >取消</el-button
        ><el-button
          type="primary"
          :loading="busy"
          :disabled="!spaceForm.name.trim()"
          @click="create"
          >创建</el-button
        ></template
      ></el-dialog
    >
  </div>
</template>
