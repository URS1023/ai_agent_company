<script setup lang="ts">
/** 沿用qKnow系统管理的筛选、工具栏、列表与编辑弹窗，领域内容交给专属编辑器。
 * 来源：qKnow-develop/qknow-ui/src/views/system/system/{dict,config,notice}/index.vue；许可见licenses/qKnow-LICENSE.txt。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import ApiDirectory from "./ApiDirectory.vue";
import SupportEditor from "./SupportEditor.vue";
import FormTools from "./FormTools.vue";
import SupportVersions from "./SupportVersions.vue";
import {
  dateLabel,
  resourcePath,
  sections,
  type SupportKind,
  type SupportResource,
} from "./support";
const session = useSession();
const kind = ref<SupportKind | "api">("dictionary");
const q = ref("");
const enabled = ref<boolean>();
const scope = ref<string>("");
const rows = ref<SupportResource[]>([]);
const page = ref(1);
const total = ref(0);
const listOp = useOperation();
const op = useOperation();
const editor = ref(false);
const current = ref<SupportResource>();
const preview = ref<SupportResource>();
const history = ref<SupportResource>();
const canManage = computed(() => session.can("m16.admin"));
let sequence = 0;
let active = true;
async function fetchRows() {
  const requestSequence = ++sequence;
  if (kind.value === "api") return;
  const requestKind = kind.value;
  const workspace = session.value?.workspace.id;
  const query = new URLSearchParams();
  query.set("limit", "30");
  query.set("offset", String((page.value - 1) * 30));
  if (q.value.trim()) query.set("q", q.value.trim());
  if (scope.value) query.set("scope", scope.value);
  if (enabled.value !== undefined) query.set("enabled", String(enabled.value));
  const result = await api.get<ListResult<SupportResource>>(
    `${resourcePath(kind.value)}?${query}`,
  );
  if (
    active &&
    requestSequence === sequence &&
    requestKind === kind.value &&
    workspace === session.value?.workspace.id
  ) {
    rows.value = result.items;
    total.value = result.total;
  }
}
let queuedReload = false;
async function load() {
  if (!active) return;
  if (listOp.busy.value) {
    queuedReload = true;
    sequence++;
    return;
  }
  await listOp.run(fetchRows);
  // 标签或工作区变动产生的新读取顺序执行，不重放失败请求。
  if (queuedReload) {
    queuedReload = false;
    await load();
  }
}
function search() {
  page.value = 1;
  void load();
}
function edit(row?: SupportResource) {
  current.value = row;
  editor.value = true;
}
async function saved() {
  editor.value = false;
  history.value = undefined;
  await load();
}
async function toggle(row: SupportResource) {
  await op.run(
    async () => {
      await api.patch(resourcePath(row.kind, row.id), {
        revision: row.revision,
        enabled: !row.enabled,
      });
      await fetchRows();
    },
    row.enabled ? "已停用" : "已启用",
  );
}
async function remove(row: SupportResource) {
  await op.run(async () => {
    await ElMessageBox.confirm(`删除「${row.name}」？`, "删除配置", {
      type: "warning",
      confirmButtonText: "删除",
      cancelButtonText: "取消",
    });
    await api.delete(resourcePath(row.kind, row.id), row.revision);
    await fetchRows();
  }, "已删除");
}
watch(kind, () => {
  rows.value = [];
  total.value = 0;
  page.value = 1;
  q.value = "";
  scope.value = "";
  enabled.value = undefined;
  void load();
});
watch(
  () => session.value?.workspace.id,
  () => {
    sequence++;
    rows.value = [];
    total.value = 0;
    page.value = 1;
    editor.value = false;
    current.value = undefined;
    preview.value = undefined;
    history.value = undefined;
    void load();
  },
);
onBeforeUnmount(() => {
  active = false;
  sequence++;
});
onMounted(load);
</script>
<template>
  <div class="support-workspace">
    <el-tabs v-model="kind">
      <el-tab-pane
        v-for="section in sections"
        :key="section.kind"
        :name="section.kind"
        :label="section.label"
      />
      <el-tab-pane name="api" label="接口目录" />
    </el-tabs>
    <ApiDirectory v-if="kind === 'api'" />
    <template v-else>
      <div class="support-toolbar">
        <el-form inline @submit.prevent="search">
          <el-form-item
            ><el-input
              v-model="q"
              clearable
              placeholder="名称或编码"
              aria-label="搜索配置"
          /></el-form-item>
          <el-form-item
            ><el-select v-model="scope" aria-label="作用域" style="width: 145px"
              ><el-option label="全部作用域" value="" /><el-option
                label="当前工作区"
                value="workspace" /><el-option
                label="个人"
                value="personal" /></el-select
          ></el-form-item>
          <el-form-item
            ><el-select
              v-model="enabled"
              clearable
              placeholder="全部状态"
              aria-label="状态"
              style="width: 120px"
              ><el-option label="启用" :value="true" /><el-option
                label="停用"
                :value="false" /></el-select
          ></el-form-item>
          <el-form-item
            ><el-button :loading="listOp.busy.value" native-type="submit"
              >搜索</el-button
            ></el-form-item
          >
        </el-form>
        <el-button v-if="canManage" type="primary" @click="edit()"
          >新增{{
            sections.find((item) => item.kind === kind)?.label
          }}</el-button
        >
      </div>
      <el-table
        v-loading="listOp.busy.value"
        :data="rows"
        stripe
        empty-text="暂无配置"
      >
        <el-table-column
          prop="name"
          label="名称"
          min-width="180"
          show-overflow-tooltip
        /><el-table-column
          prop="code"
          label="编码"
          min-width="160"
          show-overflow-tooltip
        />
        <el-table-column label="作用域" width="110"
          ><template #default="{ row }">{{
            row.scope === "personal" ? "个人" : "当前工作区"
          }}</template></el-table-column
        >
        <el-table-column label="状态" width="90"
          ><template #default="{ row }"
            ><el-tag :type="row.enabled ? 'success' : 'info'">{{
              row.enabled ? "启用" : "停用"
            }}</el-tag></template
          ></el-table-column
        >
        <el-table-column label="版本" width="80"
          ><template #default="{ row }"
            >v{{ row.revision }}</template
          ></el-table-column
        >
        <el-table-column label="更新时间" min-width="175"
          ><template #default="{ row }">{{
            dateLabel(row.updated_at)
          }}</template></el-table-column
        >
        <el-table-column
          label="操作"
          :width="kind === 'form' ? 340 : 280"
          fixed="right"
          ><template #default="{ row }">
            <el-button link type="primary" @click="edit(row)">{{
              canManage ? "编辑" : "查看"
            }}</el-button>
            <el-button
              v-if="kind === 'form'"
              link
              type="primary"
              @click="preview = row"
              >预览</el-button
            >
            <el-button link @click="history = row">历史</el-button>
            <el-button
              v-if="canManage"
              link
              :disabled="op.busy.value"
              @click="toggle(row)"
              >{{ row.enabled ? "停用" : "启用" }}</el-button
            >
            <el-button
              v-if="canManage"
              link
              type="danger"
              :disabled="op.busy.value"
              @click="remove(row)"
              >删除</el-button
            >
          </template></el-table-column
        >
      </el-table>
      <el-pagination
        v-if="total > 30"
        v-model:current-page="page"
        :page-size="30"
        :total="total"
        layout="prev, pager, next, total"
        @current-change="load"
      />
      <SupportEditor
        v-if="editor"
        :kind="kind"
        :record="current"
        :editable="canManage"
        @close="editor = false"
        @saved="saved"
      />
    </template>
    <FormTools v-if="preview" :record="preview" @close="preview = undefined" />
    <SupportVersions
      v-if="history"
      :record="history"
      :editable="canManage"
      @close="history = undefined"
      @updated="saved"
    />
  </div>
</template>
<style scoped>
.support-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}
.support-toolbar .el-form-item {
  margin-right: 12px;
  margin-bottom: 0;
}
.support-toolbar .el-form {
  display: flex;
  flex-wrap: wrap;
  gap: 10px 0;
}
.support-workspace :deep(.el-tabs__header) {
  margin-bottom: 20px;
}
</style>
