<script setup lang="ts">
import { onMounted, ref, reactive } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useRouter } from "vue-router";
import { useSession } from "../../stores/session";
import { menuItems } from "../manifest";
import { ElMessageBox } from "element-plus";
const router = useRouter();
const session = useSession();
const op = useOperation();
const records = ref<Entity[]>([]);
const connections = ref<Entity[]>([]);
const dialog = ref(false);
const selected = ref<Entity | null>(null);
const form = reactive({
  name: "",
  description: "",
  engine: "platform",
  entry: "",
  integration_id: "",
  resource_id: "",
  version: "",
  enabled: true,
});
async function load() {
  await op.run(async () => {
    records.value = (await api.get<ListResult>("/applications?limit=200"))
      .items as Entity[];
  });
}
function edit(entity: Entity | null) {
  selected.value = entity;
  Object.assign(form, {
    name: entity?.name ?? "",
    description: entity?.description ?? "",
    engine: entity?.engine ?? "platform",
    entry: entity?.entry ?? "",
    integration_id: entity?.integration_id ?? "",
    resource_id: entity?.resource_id ?? "",
    version: entity?.version ?? "",
    enabled: entity?.enabled ?? true,
  });
  dialog.value = true;
}
async function save() {
  await op.run(async () => {
    if (selected.value)
      await api.patch(`/applications/${selected.value.id}`, {
        ...form,
        revision: selected.value.revision,
      });
    else await api.post("/applications", form);
    dialog.value = false;
  }, "应用已保存");
  await load();
}
async function launch(record: Entity) {
  if (record.engine === "platform" || !record.engine) {
    if (menuItems.some((item) => item.path === record.entry))
      router.push(record.entry);
    else
      await op.run(async () => {
        throw new Error("应用入口已变更，请更新应用设置");
      });
    return;
  }
  await op.run(async () => {
    const result = await api.post<{ url: string }>(
      `/integrations/${record.integration_id}/launches`,
      {
        target: record.engine === "openwebui" ? "chat" : "apps",
        native_id: record.resource_id,
      },
    );
    const parsed = new URL(result.url);
    if (!["http:", "https:"].includes(parsed.protocol))
      throw new Error("应用地址不可用");
    window.open(parsed.href, "_blank", "noopener,noreferrer");
  });
}
async function publish(record: Entity) {
  await op.run(
    () =>
      api.post(`/applications/${record.id}/publications`, {
        revision: record.revision,
      }),
    "应用已发布",
  );
  await load();
}
async function remove(record: Entity) {
  try {
    await ElMessageBox.confirm(`删除“${record.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () => api.delete(`/applications/${record.id}`, record.revision),
      "应用已删除",
    );
    await load();
  } catch {}
}
onMounted(async () => {
  await load();
  if (session.can("m15.read"))
    connections.value = (
      await api.get<ListResult>("/integration-instances?limit=100")
    ).items as Entity[];
});
</script>
<template>
  <div class="page-stack">
    <div class="page-heading">
      <h2>应用中心</h2>
      <el-button
        v-if="session.can('m13.write')"
        type="primary"
        @click="edit(null)"
        >新增应用</el-button
      >
    </div>
    <div class="card-grid">
      <article v-for="record in records" :key="record.id" class="app-card">
        <h3>{{ record.name }}</h3>
        <p>{{ record.description }}</p>
        <div class="row-actions">
          <el-button
            v-if="record.status === 'published'"
            type="primary"
            :disabled="record.enabled === false"
            @click="launch(record)"
            >立即使用</el-button
          ><el-button
            v-else-if="session.can('m13.publish')"
            type="primary"
            @click="publish(record)"
            >发布应用</el-button
          ><el-button v-if="session.can('m13.write')" @click="edit(record)"
            >设置</el-button
          ><el-button
            v-if="session.can('m13.delete') && record.status !== 'published'"
            link
            type="danger"
            @click="remove(record)"
            >删除</el-button
          >
        </div>
      </article>
    </div>
    <el-empty
      v-if="!records.length"
      description="暂无应用"
      :image-size="90"
    /><el-dialog
      v-model="dialog"
      :title="selected ? '编辑应用' : '新增应用'"
      width="720px"
      ><el-form label-position="top" @submit.prevent="save"
        ><div class="form-grid">
          <el-form-item label="应用名称" :error="op.fields.value.name"
            ><el-input v-model="form.name" required /></el-form-item
          ><el-form-item label="来源"
            ><el-select v-model="form.engine"
              ><el-option value="platform" label="平台业务" /><el-option
                value="dify"
                label="Dify" /><el-option
                value="openwebui"
                label="Open WebUI" /></el-select></el-form-item
          ><el-form-item
            v-if="form.engine === 'platform'"
            label="应用入口"
            class="full-width"
            ><el-select v-model="form.entry" filterable
              ><el-option
                v-for="item in menuItems.filter((item) =>
                  session.can(item.permission),
                )"
                :key="item.key"
                :value="item.path"
                :label="item.label" /></el-select></el-form-item
          ><template v-else
            ><el-form-item label="连接"
              ><el-select v-model="form.integration_id" filterable
                ><el-option
                  v-for="connection in connections.filter(
                    (item) => item.engine === form.engine,
                  )"
                  :key="connection.id"
                  :value="connection.id"
                  :label="connection.name" /></el-select></el-form-item
            ><el-form-item label="原生应用ID"
              ><el-input v-model="form.resource_id" required /></el-form-item
            ><el-form-item label="固定版本"
              ><el-input
                v-model="form.version"
                required /></el-form-item></template
          ><el-form-item label="启用"
            ><el-switch v-model="form.enabled" /></el-form-item
          ><el-form-item label="描述" class="full-width"
            ><el-input v-model="form.description" type="textarea" :rows="3"
          /></el-form-item>
        </div>
        <el-button type="primary" native-type="submit" :loading="op.busy.value"
          >保存</el-button
        ></el-form
      ></el-dialog
    >
  </div>
</template>
