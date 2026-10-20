<script setup lang="ts">
import { reactive, ref } from "vue";
const props = defineProps<{
  sources: Record<string, any>[];
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({ name: "", datasource_id: "", csv_text: "" });
const fileError = ref("");
async function read(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  if (file.size > 5 * 1024 * 1024) {
    fileError.value = "请选择小于5MB的CSV文件";
    return;
  }
  fileError.value = "";
  form.csv_text = await file.text();
  if (!form.name) form.name = file.name.replace(/\.csv$/i, "");
}
function submit() {
  emit("submit", { ...form, datasource_id: form.datasource_id || undefined });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><el-form-item label="数据集名称" :error="errors.name"
      ><el-input v-model="form.name" required maxlength="160" /></el-form-item
    ><el-form-item label="数据源"
      ><el-select
        v-model="form.datasource_id"
        clearable
        placeholder="新建独立数据源"
        ><el-option
          v-for="source in sources"
          :key="source.id"
          :value="source.id"
          :label="source.name" /></el-select></el-form-item
    ><el-form-item label="CSV文件" :error="fileError || errors.csv_text"
      ><input type="file" accept=".csv,text/csv" @change="read" /></el-form-item
    ><el-form-item label="数据预览"
      ><el-input v-model="form.csv_text" type="textarea" :rows="8" required
    /></el-form-item>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!form.csv_text"
        >导入数据</el-button
      >
    </div></el-form
  >
</template>
