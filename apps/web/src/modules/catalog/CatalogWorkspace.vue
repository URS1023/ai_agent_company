<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";
import { useSession } from "../../stores/session";
import { ElMessageBox } from "element-plus";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import { resources } from "./resources";
import ResourceForm from "./ResourceForm.vue";
import StatusTag from "../../components/common/StatusTag.vue";
const props = defineProps<{ config?: string }>();
const config = computed(
  () =>
    resources[props.config ?? ""] ?? {
      title: "资源目录",
      path: `/${props.config}`,
      fields: [],
      columns: [
        { key: "name", label: "名称" },
        { key: "status", label: "状态" },
        { key: "created_at", label: "时间" },
      ],
      readonly: true,
    },
);
const route = useRoute();
const session = useSession();
const permission = computed(() => String(route.meta.permission ?? ""));
const canWrite = computed(() =>
  session.can(
    ["/roles", "/members", "/workspaces", "/resource-grants"].includes(
      config.value.path,
    )
      ? "m14.admin"
      : permission.value.replace(".read", ".write"),
  ),
);
const canDelete = computed(() =>
  session.can(
    config.value.path === "/resource-grants"
      ? "m14.admin"
      : permission.value.replace(".read", ".delete"),
  ),
);
const list = useCollection(() => config.value.path);
const op = useOperation();
const selected = ref<Entity | null>(null);
const dialog = ref(false);
const details = ref<Entity | null>(null);
function edit(row: Entity | null) {
  selected.value = row;
  dialog.value = true;
}
async function save(data: Record<string, unknown>) {
  await op.run(async () => {
    if (selected.value)
      await api.patch(`${config.value.path}/${selected.value.id}`, {
        ...data,
        revision: selected.value.revision,
      });
    else await api.post(config.value.path, data);
    dialog.value = false;
  }, "已保存");
  await list.load();
}
async function remove(row: Entity) {
  try {
    await ElMessageBox.confirm(`删除“${row.name ?? row.id}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () => api.delete(`${config.value.path}/${row.id}`, row.revision),
      "已删除",
    );
    await list.load();
  } catch {}
}
function display(value: any) {
  return value === null || value === undefined || value === ""
    ? "—"
    : Array.isArray(value)
      ? value.join("、")
      : typeof value === "boolean"
        ? value
          ? "是"
          : "否"
        : typeof value === "object"
          ? JSON.stringify(value)
          : value;
}
onMounted(list.load);
</script>
<template>
  <div class="page-stack">
    <div class="panel toolbar">
      <el-input
        v-model="list.query.value"
        placeholder="搜索"
        clearable
        @keyup.enter="list.load"
      /><el-button @click="list.load">查询</el-button
      ><span class="spacer"></span
      ><el-button
        v-if="!config.readonly && canWrite"
        type="primary"
        @click="edit(null)"
        >新增{{ config.title.replace(/管理|定义|权限/, "") }}</el-button
      >
    </div>
    <section class="panel">
      <el-table :data="list.items.value" stripe v-loading="list.loading.value"
        ><el-table-column
          v-for="column in config.columns"
          :key="column.key"
          :prop="column.key"
          :label="column.label"
          min-width="145"
          show-overflow-tooltip
          ><template #default="{ row }"
            ><StatusTag
              v-if="column.key === 'status'"
              :value="row[column.key]"
            /><span v-else>{{ display(row[column.key]) }}</span></template
          ></el-table-column
        ><el-table-column label="操作" width="180"
          ><template #default="{ row }"
            ><el-button
              v-if="!config.readonly && !config.createOnly && canWrite"
              link
              type="primary"
              @click="edit(row)"
              >编辑</el-button
            ><el-button link type="primary" @click="details = row"
              >详情</el-button
            ><el-button
              v-if="config.deletable && canDelete"
              link
              type="danger"
              @click="remove(row)"
              >删除</el-button
            ></template
          ></el-table-column
        ></el-table
      ><el-pagination
        v-model:current-page="list.page.value"
        :total="list.total.value"
        :page-size="list.size.value"
        layout="total,prev,pager,next"
        @current-change="list.load"
      />
    </section>
    <el-dialog
      v-model="dialog"
      :title="`${selected ? '编辑' : '新增'}${config.title}`"
      width="760px"
      destroy-on-close
      ><ResourceForm
        :config="config"
        :entity="selected"
        :busy="op.busy.value"
        :errors="op.fields.value"
        @submit="save"
        @cancel="dialog = false" /></el-dialog
    ><el-drawer
      :model-value="!!details"
      title="详情"
      size="580px"
      @close="details = null"
      ><el-descriptions :column="1" border
        ><el-descriptions-item
          v-for="(value, key) in details"
          :key="key"
          :label="String(key)"
          >{{ display(value) }}</el-descriptions-item
        ></el-descriptions
      ></el-drawer
    >
  </div>
</template>
