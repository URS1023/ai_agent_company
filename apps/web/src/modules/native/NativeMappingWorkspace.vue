<script setup lang="ts">
import { computed, onMounted, reactive, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import SecretPicker from "./SecretPicker.vue";
const props = defineProps<{
  kind: "external-identities" | "external-workspaces";
}>();
const op = useOperation();
const records = ref<Entity[]>([]);
const connections = ref<Entity[]>([]);
const members = ref<Entity[]>([]);
const dialog = ref(false);
const selected = ref<Entity | null>(null);
const identities = computed(() => props.kind === "external-identities");
const form = reactive({
  name: "",
  integration_id: "",
  platform_user_id: "",
  external_user_id: "",
  external_workspace_id: "",
  credential_ref: "",
  enabled: true,
});
async function load() {
  await op.run(async () => {
    const [items, instances, users] = await Promise.all([
      api.get<ListResult>(`/${props.kind}?limit=100`),
      api.get<ListResult>("/integration-instances?limit=100"),
      api.get<ListResult>("/members"),
    ]);
    records.value = items.items as Entity[];
    connections.value = instances.items as Entity[];
    members.value = users.items as Entity[];
  });
}
function edit(record: Entity | null) {
  selected.value = record;
  Object.assign(form, {
    name: record?.name ?? "",
    integration_id: record?.integration_id ?? "",
    platform_user_id: record?.platform_user_id ?? "",
    external_user_id: record?.external_user_id ?? "",
    external_workspace_id: record?.external_workspace_id ?? "",
    credential_ref: record?.credential_ref ?? "",
    enabled: record?.enabled ?? true,
  });
  dialog.value = true;
}
async function save() {
  await op.run(async () => {
    const payload: Record<string, unknown> = identities.value
      ? {
          external_user_id: form.external_user_id,
          external_workspace_id: form.external_workspace_id || null,
          credential_ref: form.credential_ref || null,
          enabled: form.enabled,
        }
      : {
          name: form.name,
          external_workspace_id: form.external_workspace_id,
          enabled: form.enabled,
        };
    if (selected.value)
      await api.patch(`/${props.kind}/${selected.value.id}`, {
        ...payload,
        revision: selected.value.revision,
      });
    else
      await api.post(`/${props.kind}`, {
        ...payload,
        integration_id: form.integration_id,
        ...(identities.value
          ? { platform_user_id: form.platform_user_id }
          : {}),
      });
    dialog.value = false;
  }, "映射已保存");
  await load();
}
async function remove(record: Entity) {
  try {
    await ElMessageBox.confirm("删除此映射？", "删除确认", { type: "warning" });
    await op.run(
      () => api.delete(`/${props.kind}/${record.id}`, record.revision),
      "映射已删除",
    );
    await load();
  } catch {}
}
onMounted(load);
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h3>{{ identities ? "用户映射" : "工作区映射" }}</h3>
      <el-button type="primary" @click="edit(null)">新增映射</el-button>
    </div>
    <el-table :data="records" stripe v-loading="op.busy.value"
      ><el-table-column label="连接" min-width="180"
        ><template #default="{ row }">{{
          connections.find((item) => item.id === row.integration_id)?.name ??
          row.integration_id
        }}</template></el-table-column
      ><el-table-column v-if="identities" label="平台用户" min-width="160"
        ><template #default="{ row }">{{
          members.find((item) => item.user_id === row.platform_user_id)
            ?.display_name ?? row.platform_user_id
        }}</template></el-table-column
      ><el-table-column v-else prop="name" label="名称" /><el-table-column
        v-if="identities"
        prop="external_user_id"
        label="原生用户ID"
        min-width="220"
      /><el-table-column
        prop="external_workspace_id"
        label="原生工作区ID"
        min-width="220"
      /><el-table-column label="操作" width="150"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="edit(row)">编辑</el-button
          ><el-button link type="danger" @click="remove(row)"
            >删除</el-button
          ></template
        ></el-table-column
      ></el-table
    ><el-dialog
      v-model="dialog"
      title="用户与工作区映射"
      width="700px"
      destroy-on-close
      ><el-form label-position="top" @submit.prevent="save"
        ><div class="form-grid">
          <el-form-item label="连接"
            ><el-select
              v-model="form.integration_id"
              :disabled="!!selected"
              filterable
              ><el-option
                v-for="connection in connections"
                :key="connection.id"
                :value="connection.id"
                :label="connection.name" /></el-select></el-form-item
          ><el-form-item v-if="identities" label="平台用户"
            ><el-select
              v-model="form.platform_user_id"
              :disabled="!!selected"
              filterable
              ><el-option
                v-for="member in members"
                :key="member.user_id"
                :value="member.user_id"
                :label="
                  member.display_name ?? member.username
                " /></el-select></el-form-item
          ><el-form-item v-else label="名称"
            ><el-input v-model="form.name" required /></el-form-item
          ><el-form-item v-if="identities" label="原生用户ID"
            ><el-input v-model="form.external_user_id" required /></el-form-item
          ><el-form-item label="原生工作区ID"
            ><el-input
              v-model="form.external_workspace_id"
              :required="!identities" /></el-form-item
          ><el-form-item v-if="identities" label="用户专属凭据"
            ><SecretPicker v-model="form.credential_ref" /></el-form-item
          ><el-form-item label="启用"
            ><el-switch v-model="form.enabled"
          /></el-form-item>
        </div>
        <el-button type="primary" native-type="submit" :loading="op.busy.value"
          >保存映射</el-button
        ></el-form
      ></el-dialog
    >
  </section>
</template>
