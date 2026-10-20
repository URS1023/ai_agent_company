<script setup lang="ts">
import type { FormField } from "./support";
defineProps<{
  fields: FormField[];
  errors: Record<string, string>;
  disabled?: boolean;
}>();
const values = defineModel<Record<string, any>>({ required: true });
</script>
<template>
  <el-form label-position="top" :disabled="disabled" @submit.prevent>
    <el-alert
      v-if="errors._form"
      type="error"
      :closable="false"
      :title="errors._form"
    />
    <el-form-item
      v-for="field in fields"
      :key="field.key"
      :label="field.label"
      :required="field.required"
      :error="errors[field.key]"
    >
      <el-input
        v-if="['text', 'textarea'].includes(field.kind)"
        v-model="values[field.key]"
        :type="field.kind === 'textarea' ? 'textarea' : 'text'"
        :rows="3"
        :maxlength="field.max_length"
        :placeholder="field.placeholder"
      />
      <el-input-number
        v-else-if="['number', 'integer'].includes(field.kind)"
        v-model="values[field.key]"
        :precision="field.kind === 'integer' ? 0 : undefined"
        :min="field.minimum"
        :max="field.maximum"
      />
      <el-select
        v-else-if="field.kind === 'select'"
        v-model="values[field.key]"
        clearable
        :placeholder="field.placeholder"
        ><el-option
          v-for="choice in field.choices"
          :key="choice.value"
          :label="choice.label"
          :value="choice.value"
      /></el-select>
      <el-switch
        v-else-if="field.kind === 'boolean'"
        v-model="values[field.key]"
      />
      <el-date-picker
        v-else-if="field.kind === 'date'"
        v-model="values[field.key]"
        type="date"
        value-format="YYYY-MM-DD"
      />
    </el-form-item>
  </el-form>
</template>
