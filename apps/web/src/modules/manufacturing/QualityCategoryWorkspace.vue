<script setup lang="ts">
import { computed, reactive, shallowRef, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import { allRecords } from "./workflowOptions";
const props = defineProps<{ targetType?: string }>();
const session = useSession(),
  op = useOperation();
const target = shallowRef(props.targetType ?? "product"),
  categories = shallowRef<Entity[]>([]),
  selected = shallowRef(""),
  resourceType = shallowRef("product"),
  editing = shallowRef<Entity | null>(null),
  dialog = shallowRef(false),
  binding = shallowRef<Entity | null>(null),
  destination = shallowRef("");
const form = reactive({
  name: "",
  code: "",
  parent_id: "",
  description: "",
  status: "active",
});
const list = useCollection(
  () =>
    `/quality-category-resources?${new URLSearchParams({ resource_type: resourceType.value, target_type: target.value, ...(selected.value ? { category_id: selected.value } : {}) })}`,
);
const tree = computed(() => {
  const nodes: (Entity & { label: string | undefined; children: any[] })[] =
    categories.value.map((row) => ({
      ...row,
      label: row.name,
      children: [] as any[],
    }));
  const index = new Map(nodes.map((row) => [row.id, row]));
  const roots: any[] = [];
  for (const node of nodes) {
    const parent = index.get(node.parent_id);
    if (parent) parent.children.push(node);
    else roots.push(node);
  }
  return [{ id: "", label: "全部类别", children: roots }];
});
const activeCategory = computed(() =>
  categories.value.find((row) => row.id === selected.value),
);
const parentOptions = computed(() => {
  const excluded = new Set(editing.value ? [editing.value.id] : []);
  let changed = true;
  while (changed) {
    changed = false;
    for (const row of categories.value)
      if (excluded.has(row.parent_id) && !excluded.has(row.id)) {
        excluded.add(row.id);
        changed = true;
      }
  }
  return categories.value.filter(
    (row) => !excluded.has(row.id) && row.status === "active",
  );
});
async function load() {
  await op.run(async () => {
    categories.value = await allRecords(
      `/quality-categories?target_type=${target.value}`,
    );
    await list.load();
  });
}
function begin(row: Entity | null) {
  editing.value = row;
  Object.assign(form, {
    name: row?.name ?? "",
    code: row?.code ?? "",
    parent_id: row?.parent_id ?? selected.value,
    description: row?.description ?? "",
    status: row?.status ?? "active",
  });
  dialog.value = true;
}
async function save() {
  const saved = await op.run(async () => {
    const data = {
      ...form,
      target_type: target.value,
      parent_id: form.parent_id || null,
    };
    if (editing.value)
      await api.patch(`/quality-categories/${editing.value.id}`, {
        ...data,
        revision: editing.value.revision,
      });
    else await api.post("/quality-categories", data);
    dialog.value = false;
    return true;
  }, "类别已保存");
  if (saved) await load();
}
async function remove() {
  if (!activeCategory.value) return;
  const row = activeCategory.value;
  try {
    await ElMessageBox.confirm(`删除类别“${row.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(async () => {
      await api.delete(`/quality-categories/${row.id}`, row.revision);
      selected.value = "";
    }, "已删除");
    await load();
  } catch {}
}
function beginBinding(row: Entity) {
  binding.value = row;
  destination.value = row.category_id ?? selected.value;
}
async function saveBinding() {
  if (!binding.value) return;
  await op.run(async () => {
    await api.post("/quality-category-bindings", {
      category_id: destination.value,
      resource_type: resourceType.value,
      resource_id: binding.value!.id,
      revision: binding.value!.binding_revision ?? null,
    });
    binding.value = null;
    await list.load();
  }, "类别已更新");
}
async function unbind(row: Entity) {
  if (!row.binding_id) return;
  await op.run(async () => {
    await api.delete(
      `/quality-category-bindings/${row.binding_id}`,
      row.binding_revision,
    );
    await list.load();
  }, "已移出类别");
}
watch(
  () => props.targetType,
  (value) => {
    target.value = value ?? "product";
    resourceType.value = target.value;
    selected.value = "";
    void load();
  },
  { immediate: true },
);
function changeTarget() {
  resourceType.value = target.value;
  selected.value = "";
  void load();
}
</script>
<template>
  <div class="category-layout">
    <section class="panel categories">
      <div class="toolbar">
        <el-select v-if="!targetType" v-model="target" @change="changeTarget"
          ><el-option value="product" label="产品" /><el-option
            value="device"
            label="设备" /></el-select
        ><strong v-else>类别</strong><span class="spacer"></span
        ><el-button
          v-if="session.can('m11.write')"
          link
          type="primary"
          @click="begin(null)"
          >新增</el-button
        >
      </div>
      <el-tree
        :data="tree"
        node-key="id"
        default-expand-all
        highlight-current
        :current-node-key="selected"
        @node-click="
          (node: any) => {
            selected = node.id;
            list.page.value = 1;
            list.load();
          }
        "
      />
      <div v-if="activeCategory" class="row-actions category-actions">
        <el-button
          v-if="session.can('m11.write')"
          link
          @click="begin(activeCategory)"
          >编辑</el-button
        ><el-button
          v-if="session.can('m11.delete')"
          link
          type="danger"
          @click="remove"
          >删除</el-button
        >
      </div>
    </section>
    <section class="panel resources">
      <div class="toolbar">
        <el-radio-group
          v-model="resourceType"
          @change="
            list.page.value = 1;
            list.load();
          "
          ><el-radio-button :value="target">{{
            target === "device" ? "设备" : "产品"
          }}</el-radio-button
          ><el-radio-button value="template"
            >检验标准</el-radio-button
          ></el-radio-group
        ><el-input
          v-model="list.query.value"
          placeholder="名称"
          clearable
          style="max-width: 240px"
          @keyup.enter="list.load"
        /><el-button @click="list.load">查询</el-button>
      </div>
      <el-table
        :data="list.items.value"
        v-loading="list.loading.value || op.busy.value"
        stripe
        ><el-table-column
          prop="name"
          label="名称"
          min-width="220"
        /><el-table-column
          prop="category_name"
          label="类别"
          min-width="150"
        /><el-table-column label="操作" width="160"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can('m11.write')"
              link
              type="primary"
              @click="beginBinding(row)"
              >设置类别</el-button
            ><el-button
              v-if="row.category_id && session.can('m11.write')"
              link
              @click="unbind(row)"
              >移出</el-button
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
      :title="editing ? '编辑类别' : '新增类别'"
      width="600px"
      ><el-form label-position="top" @submit.prevent="save"
        ><div class="form-grid">
          <el-form-item label="类别名称" :error="op.fields.value.name"
            ><el-input
              v-model="form.name"
              required
              maxlength="160" /></el-form-item
          ><el-form-item label="编号" :error="op.fields.value.code"
            ><el-input v-model="form.code" maxlength="80"
          /></el-form-item>
        </div>
        <el-form-item label="上级类别" :error="op.fields.value.parent_id"
          ><el-select v-model="form.parent_id" clearable filterable
            ><el-option
              v-for="row in parentOptions"
              :key="row.id"
              :value="row.id"
              :label="row.name" /></el-select></el-form-item
        ><el-form-item label="类别资料" :error="op.fields.value.description"
          ><el-input
            v-model="form.description"
            type="textarea"
            :rows="4"
            maxlength="2000" /></el-form-item
        ><el-form-item label="状态"
          ><el-switch
            v-model="form.status"
            active-value="active"
            inactive-value="inactive"
            active-text="启用" /></el-form-item
        ><el-button
          type="primary"
          native-type="submit"
          :disabled="!form.name.trim()"
          :loading="op.busy.value"
          >保存</el-button
        ></el-form
      ></el-dialog
    >
    <el-dialog
      :model-value="!!binding"
      title="设置类别"
      width="500px"
      @close="binding = null"
      ><el-form label-position="top" @submit.prevent="saveBinding"
        ><el-form-item
          :label="binding?.name"
          :error="op.fields.value.category_id"
          ><el-select v-model="destination" filterable
            ><el-option
              v-for="row in categories.filter(
                (item) => item.status === 'active',
              )"
              :key="row.id"
              :value="row.id"
              :label="row.name" /></el-select></el-form-item
        ><el-button
          type="primary"
          native-type="submit"
          :disabled="!destination"
          :loading="op.busy.value"
          >保存</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>
<style scoped>
.category-layout {
  display: grid;
  grid-template-columns: 260px minmax(0, 1fr);
  gap: 16px;
  align-items: start;
}
.categories .toolbar {
  padding-bottom: 16px;
}
.category-actions {
  padding-top: 18px;
}
.resources {
  min-width: 0;
}
@media (max-width: 900px) {
  .category-layout {
    grid-template-columns: 1fr;
  }
}
</style>
