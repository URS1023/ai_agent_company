<script setup lang="ts">
import { onMounted, reactive, ref, watch } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import type { ResourceConfig, ResourceField } from "./resources";
const props = defineProps<{
  config: ResourceConfig;
  entity?: Entity | null;
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive<Record<string, any>>({});
const jsonErrors = reactive<Record<string, string>>({});
const roles = ref<{ value: string; label: string }[]>([]);
watch(
  () => props.entity,
  (entity) => {
    for (const field of props.config.fields) {
      const value = entity?.[field.key];
      form[field.key] =
        field.type === "json"
          ? JSON.stringify(
              value ?? (field.key.endsWith("_ids") ? [] : {}),
              null,
              2,
            )
          : (value ??
            (field.type === "multi"
              ? []
              : field.type === "boolean"
                ? field.key === "enabled"
                : ""));
    }
  },
  { immediate: true },
);
function options(field: ResourceField) {
  return field.key === "role_ids" ? roles.value : (field.options ?? []);
}
function submit() {
  const payload: Record<string, unknown> = {};
  let valid = true;
  for (const field of props.config.fields) {
    if (props.entity && field.readonlyOnEdit) continue;
    jsonErrors[field.key] = "";
    try {
      payload[field.key] =
        field.type === "json" ? JSON.parse(form[field.key]) : form[field.key];
    } catch {
      jsonErrors[field.key] = "请输入有效的JSON";
      valid = false;
    }
  }
  if (valid) emit("submit", payload);
}
onMounted(async () => {
  if (props.config.path === "/members") {
    try {
      roles.value = (await api.get<ListResult>("/roles")).items.map((role) => ({
        value: role.id,
        label: role.name,
      }));
    } catch {}
  }
});
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><div class="form-grid">
      <el-form-item
        v-for="field in config.fields"
        :key="field.key"
        :label="field.label"
        :error="errors[field.key] ?? jsonErrors[field.key]"
        :class="{
          'full-width': ['textarea', 'json', 'multi'].includes(
            field.type ?? '',
          ),
        }"
        ><el-switch
          v-if="field.type === 'boolean'"
          v-model="form[field.key]" /><el-input-number
          v-else-if="field.type === 'number'"
          v-model="form[field.key]" /><el-select
          v-else-if="field.type === 'multi' || field.type === 'select'"
          v-model="form[field.key]"
          :multiple="field.type === 'multi'"
          filterable
          :allow-create="!options(field).length"
          style="width: 100%"
          ><el-option
            v-for="option in options(field)"
            :key="option.value"
            :value="option.value"
            :label="option.label" /></el-select
        ><el-input
          v-else
          v-model="form[field.key]"
          :type="
            ['json', 'textarea'].includes(field.type ?? '')
              ? 'textarea'
              : field.type === 'password'
                ? 'password'
                : 'text'
          "
          :show-password="field.type === 'password'"
          :rows="field.type === 'json' ? 8 : 4"
          :required="field.required"
          :disabled="!!entity && field.readonlyOnEdit"
          :minlength="field.type === 'password' ? 12 : undefined"
          :autocomplete="field.type === 'password' ? 'new-password' : 'off'"
      /></el-form-item>
    </div>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button type="primary" native-type="submit" :loading="busy"
        >保存</el-button
      >
    </div></el-form
  >
</template>
