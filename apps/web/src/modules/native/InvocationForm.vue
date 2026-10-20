<script setup lang="ts">
import { computed, reactive } from "vue";
const props = defineProps<{
  binding: Record<string, any>;
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({ inputs: {} as Record<string, any>, query: "" });
const fields = computed<Record<string, any>[]>(() =>
  Object.entries(props.binding.input_schema?.properties ?? {}).map(
    ([name, schema]) => ({ name, ...(schema as Record<string, any>) }),
  ),
);
let requestId = crypto.randomUUID();
let fingerprint = "";
const required = computed(() => props.binding.input_schema?.required ?? []);
function submit() {
  const next = JSON.stringify({ inputs: form.inputs, query: form.query });
  if (fingerprint && fingerprint !== next) requestId = crypto.randomUUID();
  fingerprint = next;
  emit("submit", {
    inputs: form.inputs,
    query: form.query || undefined,
    idempotency_key: requestId,
  });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><el-form-item
      v-for="field in fields"
      :key="field.name"
      :label="field.title ?? field.name"
      :error="errors[field.name]"
      ><el-input-number
        v-if="field.type === 'number' || field.type === 'integer'"
        v-model="form.inputs[field.name]"
        :precision="field.type === 'integer' ? 0 : undefined" /><el-switch
        v-else-if="field.type === 'boolean'"
        v-model="form.inputs[field.name]" /><el-select
        v-else-if="Array.isArray(field.enum)"
        v-model="form.inputs[field.name]"
        ><el-option
          v-for="value in field.enum"
          :key="String(value)"
          :value="value"
          :label="String(value)" /></el-select
      ><el-input
        v-else
        v-model="form.inputs[field.name]"
        :required="required.includes(field.name)" /></el-form-item
    ><el-form-item
      v-if="binding.app_mode !== 'workflow'"
      label="输入内容"
      :error="errors.query"
      ><el-input
        v-model="form.query"
        type="textarea"
        :rows="5"
        required /></el-form-item
    ><el-empty
      v-if="!fields.length && binding.app_mode === 'workflow'"
      description="无额外输入参数"
      :image-size="60"
    />
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button type="primary" native-type="submit" :loading="busy"
        >运行</el-button
      >
    </div></el-form
  >
</template>
