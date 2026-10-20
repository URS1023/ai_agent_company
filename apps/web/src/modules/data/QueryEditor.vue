<script setup lang="ts">
import { computed, reactive, watch } from "vue";
const props = defineProps<{
  sources: Record<string, any>[];
  sourceId: string;
  busy: boolean;
  errors: Record<string, string>;
  sql?: string;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  sourceChange: [id: string];
}>();
const form = reactive({
  name: "",
  datasource_id: props.sourceId,
  sql: props.sql ?? "",
  max_rows: 100,
  parameterText: "{}",
});
watch(
  () => props.sourceId,
  (value) => {
    form.datasource_id = value;
  },
);
watch(
  () => props.sql,
  (value) => {
    if (value) form.sql = value;
  },
);
const parameterError = computed(() => {
  try {
    const v = JSON.parse(form.parameterText);
    return typeof v === "object" && v !== null && !Array.isArray(v)
      ? ""
      : "参数需要为对象";
  } catch {
    return "请输入有效的JSON参数";
  }
});
function submit() {
  if (parameterError.value) return;
  emit("submit", {
    name: form.name,
    datasource_id: form.datasource_id,
    sql: form.sql,
    parameters: JSON.parse(form.parameterText),
    max_rows: form.max_rows,
  });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><div class="toolbar">
      <el-select
        v-model="form.datasource_id"
        filterable
        placeholder="选择数据源"
        @change="emit('sourceChange', $event)"
        ><el-option
          v-for="source in sources"
          :key="source.id"
          :value="source.id"
          :label="source.name" /></el-select
      ><el-input v-model="form.name" placeholder="查询名称" /><span
        class="spacer"
      ></span
      ><el-input-number
        v-model="form.max_rows"
        :min="1"
        :max="1000"
        :step="100"
      /><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!form.datasource_id || !form.sql.trim() || !!parameterError"
        >执行查询</el-button
      >
    </div>
    <el-form-item :error="errors.sql" style="margin-top: 16px"
      ><el-input
        v-model="form.sql"
        type="textarea"
        :rows="9"
        placeholder="输入查询语句"
        spellcheck="false"
        class="sql-editor"
        required /></el-form-item
    ><el-collapse
      ><el-collapse-item title="查询参数" name="parameters"
        ><el-form-item :error="parameterError"
          ><el-input
            v-model="form.parameterText"
            type="textarea"
            :rows="3"
            spellcheck="false" /></el-form-item></el-collapse-item></el-collapse
  ></el-form>
</template>
<style scoped>
.sql-editor :deep(textarea) {
  font:
    14px/1.7 Consolas,
    monospace;
  background: #f8fafc;
  color: #18345a;
}
</style>
