<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
const session = useSession();
const op = useOperation();
const records = ref<Entity[]>([]);
const selected = ref<Entity | null>(null);
const dialog = ref(false);
const form = reactive({ name: "", all: false, permissions: [] as string[] });
const actions = [
  { key: "read", label: "查看" },
  { key: "write", label: "新增/修改" },
  { key: "delete", label: "删除" },
  { key: "publish", label: "发布" },
  { key: "admin", label: "管理" },
  { key: "audit", label: "审计" },
];
const names = [
  "工作台",
  "AI对话",
  "智能体",
  "工作流",
  "数据接入",
  "数据分析",
  "报告",
  "知识库",
  "图谱",
  "设备维护",
  "质量",
  "工具与技能",
  "应用",
  "组织",
  "模型",
  "运维",
  "成果",
];
const modules = computed(() =>
  names.map((name, index) => ({
    id: `m${String(index + 1).padStart(2, "0")}`,
    name,
  })),
);
function label(permission: string) {
  if (permission === "*") return "全部权限";
  const [module, action] = permission.split(".");
  return `${modules.value.find((item) => item.id === module)?.name ?? module} · ${action === "*" ? "全部" : (actions.find((item) => item.key === action)?.label ?? action)}`;
}
function edit(record: Entity | null) {
  selected.value = record;
  form.name = record?.name ?? "";
  form.all = record?.permissions?.includes("*") ?? false;
  form.permissions = (record?.permissions ?? []).flatMap(
    (permission: string) =>
      permission.endsWith(".*")
        ? actions.map((action) => `${permission.split(".")[0]}.${action.key}`)
        : permission === "*"
          ? []
          : [permission],
  );
  dialog.value = true;
}
function toggle(permission: string, checked: boolean) {
  form.permissions = checked
    ? Array.from(new Set([...form.permissions, permission]))
    : form.permissions.filter((item) => item !== permission);
}
function readAll() {
  form.permissions = Array.from(
    new Set([
      ...form.permissions,
      ...modules.value.map((module) => `${module.id}.read`),
    ]),
  );
}
async function load() {
  await op.run(async () => {
    records.value = (await api.get<ListResult>("/roles")).items as Entity[];
  });
}
async function save() {
  await op.run(async () => {
    const payload = {
      name: form.name,
      permissions: form.all ? ["*"] : form.permissions,
    };
    if (selected.value)
      await api.patch(`/roles/${selected.value.id}`, {
        ...payload,
        revision: selected.value.revision,
      });
    else await api.post("/roles", payload);
    dialog.value = false;
  }, "角色已保存");
  await load();
}
onMounted(load);
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>角色权限</h2>
      <el-button
        v-if="session.can('m14.admin')"
        type="primary"
        @click="edit(null)"
        >新增角色</el-button
      >
    </div>
    <section class="panel">
      <el-table :data="records" stripe v-loading="op.busy.value"
        ><el-table-column
          prop="name"
          label="角色名称"
          width="180"
        /><el-table-column label="权限" min-width="500"
          ><template #default="{ row }"
            ><el-tag
              v-for="permission in row.permissions"
              :key="permission"
              style="margin: 4px"
              type="info"
              >{{ label(permission) }}</el-tag
            ></template
          ></el-table-column
        ><el-table-column width="90"
          ><template #default="{ row }"
            ><el-button
              v-if="session.can('m14.admin')"
              link
              type="primary"
              @click="edit(row)"
              >编辑</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </section>
    <el-dialog
      v-model="dialog"
      :title="selected ? '编辑角色' : '新增角色'"
      width="960px"
      destroy-on-close
      ><el-form label-position="top" @submit.prevent="save"
        ><el-form-item label="角色名称" :error="op.fields.value.name"
          ><el-input v-model="form.name" required maxlength="120"
        /></el-form-item>
        <div class="toolbar" style="margin-bottom: 15px">
          <el-checkbox v-model="form.all">全部权限</el-checkbox
          ><span class="spacer"></span
          ><el-button :disabled="form.all" @click="readAll"
            >选择全部查看权限</el-button
          ><el-button :disabled="form.all" @click="form.permissions = []"
            >清空</el-button
          >
        </div>
        <el-table :data="modules" border size="small" max-height="520"
          ><el-table-column
            prop="name"
            label="模块"
            width="170" /><el-table-column
            v-for="action in actions"
            :key="action.key"
            :label="action.label"
            align="center"
            ><template #default="{ row }"
              ><el-checkbox
                v-if="action.key !== 'audit' || row.id === 'm14'"
                :model-value="
                  form.all ||
                  form.permissions.includes(`${row.id}.${action.key}`)
                "
                :disabled="form.all"
                :aria-label="`${row.name}·${action.label}`"
                @change="
                  toggle(`${row.id}.${action.key}`, Boolean($event))
                " /></template></el-table-column
        ></el-table>
        <div style="text-align: right; margin-top: 20px">
          <el-button @click="dialog = false">取消</el-button
          ><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            >保存角色</el-button
          >
        </div></el-form
      ></el-dialog
    >
  </div>
</template>
