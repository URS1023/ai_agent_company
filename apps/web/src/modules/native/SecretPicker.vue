<script setup lang="ts">
import { onMounted, reactive, ref } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
const value = defineModel<string>({ default: "" });
const session = useSession();
const op = useOperation();
const secrets = ref<Entity[]>([]);
const dialog = ref(false);
const form = reactive({ name: "", kind: "api_key", value: "" });
async function load() {
  if (session.can("m15.read"))
    await op.run(async () => {
      secrets.value = (await api.get<ListResult>("/secret-references"))
        .items as Entity[];
    });
}
async function create() {
  await op.run(async () => {
    const record = await api.post<Entity>("/secret-references", form);
    form.value = "";
    value.value = record.id;
    dialog.value = false;
    secrets.value.push(record);
  }, "凭据已保存");
}
onMounted(load);
</script>
<template>
  <div class="secret-picker">
    <el-select v-model="value" clearable filterable placeholder="选择凭据"
      ><el-option
        v-for="secret in secrets"
        :key="secret.id"
        :value="secret.id"
        :label="secret.name ?? secret.id" /></el-select
    ><el-button v-if="session.can('m15.admin')" @click="dialog = true"
      >新增</el-button
    >
  </div>
  <el-dialog
    v-model="dialog"
    title="新增凭据"
    width="560px"
    append-to-body
    destroy-on-close
    @closed="form.value = ''"
    ><el-form label-position="top" @submit.prevent="create"
      ><el-form-item label="凭据名称" :error="op.fields.value.name"
        ><el-input v-model="form.name" required maxlength="160" /></el-form-item
      ><el-form-item label="密钥" :error="op.fields.value.value"
        ><el-input
          v-model="form.value"
          type="password"
          show-password
          autocomplete="new-password"
          required /></el-form-item
      ><el-button type="primary" native-type="submit" :loading="op.busy.value"
        >保存凭据</el-button
      ></el-form
    ></el-dialog
  >
</template>
<style scoped>
.secret-picker {
  display: flex;
  gap: 8px;
  width: 100%;
}
.secret-picker .el-select {
  flex: 1;
}
</style>
