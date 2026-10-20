<script setup lang="ts">
import { reactive } from "vue";
const props = defineProps<{
  name: string;
  busy?: boolean;
  errors?: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [data: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({ name: `${props.name} · 维修工单`, note: "" });
</script>
<template>
  <el-form label-position="top" @submit.prevent="emit('submit', { ...form })"
    ><el-form-item label="工单名称" :error="errors?.name"
      ><el-input v-model="form.name" required maxlength="160" /></el-form-item
    ><el-form-item label="检查记录" :error="errors?.note"
      ><el-input
        v-model="form.note"
        type="textarea"
        :rows="4"
        minlength="5"
        maxlength="3000"
        required
    /></el-form-item>
    <div class="row-actions">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!form.name.trim() || form.note.trim().length < 5"
        >创建工单</el-button
      >
    </div></el-form
  >
</template>
