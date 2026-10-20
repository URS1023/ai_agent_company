<script setup lang="ts">
import { reactive, watch } from "vue";
import type { Entity } from "../../api/client";
import SecretPicker from "./SecretPicker.vue";
const props = defineProps<{
  entity?: Entity | null;
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({
  name: "",
  engine: "dify",
  base_url: "",
  console_url: "",
  credential_ref: "",
  dataset_credential_ref: "",
  enabled: true,
});
watch(
  () => props.entity,
  (value) => {
    Object.assign(form, {
      name: value?.name ?? "",
      engine: value?.engine ?? "dify",
      base_url: value?.base_url ?? "",
      console_url: value?.console_url ?? "",
      credential_ref: value?.credential_ref ?? "",
      dataset_credential_ref: value?.dataset_credential_ref ?? "",
      enabled: value?.enabled ?? true,
    });
  },
  { immediate: true },
);
</script>
<template>
  <el-form
    label-position="top"
    @submit.prevent="
      emit('submit', {
        ...form,
        credential_ref: form.credential_ref || null,
        dataset_credential_ref: form.dataset_credential_ref || null,
      })
    "
    ><div class="form-grid">
      <el-form-item label="连接名称" :error="errors.name"
        ><el-input v-model="form.name" required maxlength="120" /></el-form-item
      ><el-form-item label="应用"
        ><el-select v-model="form.engine" :disabled="!!entity"
          ><el-option value="dify" label="Dify" /><el-option
            value="openwebui"
            label="Open WebUI" /></el-select></el-form-item
      ><el-form-item label="API地址" :error="errors.base_url" class="full-width"
        ><el-input
          v-model="form.base_url"
          type="url"
          required
          placeholder="https://" /></el-form-item
      ><el-form-item
        label="控制台地址"
        :error="errors.console_url"
        class="full-width"
        ><el-input
          v-model="form.console_url"
          type="url"
          required
          placeholder="https://" /></el-form-item
      ><el-form-item label="应用凭据" :error="errors.credential_ref"
        ><SecretPicker v-model="form.credential_ref" /></el-form-item
      ><el-form-item v-if="form.engine === 'dify'" label="知识库凭据"
        ><SecretPicker v-model="form.dataset_credential_ref" /></el-form-item
      ><el-form-item label="启用"
        ><el-switch v-model="form.enabled"
      /></el-form-item>
    </div>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button type="primary" native-type="submit" :loading="busy"
        >保存连接</el-button
      >
    </div></el-form
  >
</template>
