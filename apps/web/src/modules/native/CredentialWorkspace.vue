<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
const op = useOperation();
const records = ref<Entity[]>([]);
const selected = ref<Entity | null>(null);
const dialog = ref(false);
const form = reactive({ name: "", kind: "api_key", value: "" });
async function load() {
  await op.run(async () => {
    records.value = (await api.get<ListResult>("/secret-references"))
      .items as Entity[];
  });
}
function edit(record: Entity | null) {
  selected.value = record;
  form.name = record?.name ?? "";
  form.value = "";
  dialog.value = true;
}
async function save() {
  await op.run(async () => {
    if (selected.value)
      await api.patch(`/secret-references/${selected.value.id}`, {
        revision: selected.value.revision,
        value: form.value,
      });
    else await api.post("/secret-references", form);
    form.value = "";
    dialog.value = false;
  }, "凭据已保存");
  await load();
}
async function remove(record: Entity) {
  try {
    await ElMessageBox.confirm(`删除凭据“${record.name}”？`, "删除确认", {
      type: "warning",
    });
    await op.run(
      () => api.delete(`/secret-references/${record.id}`),
      "凭据已删除",
    );
    await load();
  } catch {}
}
onMounted(load);
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h3>凭据</h3>
      <el-button type="primary" @click="edit(null)">新增凭据</el-button>
    </div>
    <el-table :data="records" stripe v-loading="op.busy.value"
      ><el-table-column prop="name" label="名称" /><el-table-column
        prop="kind"
        label="类型"
        width="160"
      /><el-table-column
        prop="revision"
        label="版本"
        width="90"
      /><el-table-column
        prop="updated_at"
        label="更新时间"
        width="190"
      /><el-table-column label="操作" width="160"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="edit(row)">更换密钥</el-button
          ><el-button link type="danger" @click="remove(row)"
            >删除</el-button
          ></template
        ></el-table-column
      ></el-table
    ><el-dialog
      v-model="dialog"
      :title="selected ? '更换密钥' : '新增凭据'"
      width="600px"
      destroy-on-close
      @closed="form.value = ''"
      ><el-form label-position="top" @submit.prevent="save"
        ><el-form-item
          v-if="!selected"
          label="名称"
          :error="op.fields.value.name"
          ><el-input v-model="form.name" required /></el-form-item
        ><el-form-item label="密钥" :error="op.fields.value.value"
          ><el-input
            v-model="form.value"
            type="password"
            show-password
            autocomplete="new-password"
            required /></el-form-item
        ><el-button type="primary" native-type="submit" :loading="op.busy.value"
          >保存</el-button
        ></el-form
      ></el-dialog
    >
  </section>
</template>
