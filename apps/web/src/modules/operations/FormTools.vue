<script setup lang="ts">
/** 保存后的字段从同一服务端版本生成预览、JSON Schema和开发文件。 */
import { ref, watch } from "vue";
import { ArrowDown } from "@element-plus/icons-vue";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import FormPreview from "./FormPreview.vue";
import { download, resourcePath, type SupportResource } from "./support";
const props = defineProps<{ record: SupportResource }>();
defineEmits<{ close: [] }>();
const op = useOperation();
const values = ref<Record<string, any>>(
  Object.fromEntries(
    props.record.definition.fields
      .filter((field: any) => field.kind === "boolean")
      .map((field: any) => [field.key, false]),
  ),
);
const errors = ref<Record<string, string>>({});
async function validate() {
  const validated = await op.run(async () => {
    const result = await api.post<{
      valid: boolean;
      errors: Record<string, string>;
    }>(`${resourcePath("form", props.record.id)}/validations`, {
      revision: props.record.revision,
      values: values.value,
    });
    errors.value = result.errors;
    if (result.valid) return true;
  });
  return validated === true;
}
const valid = ref(false);
watch(
  values,
  () => {
    valid.value = false;
  },
  { deep: true },
);
async function check() {
  valid.value = false;
  valid.value = await validate();
}
async function exportFile(format: "schema" | "vue" | "typescript") {
  await op.run(async () => {
    const path = resourcePath("form", props.record.id);
    if (format === "schema")
      download(
        JSON.stringify(await api.get(`${path}/schema`), null, 2),
        `${props.record.code}.schema.json`,
      );
    else
      download(
        await api.get<string>(`${path}/scaffold?format=${format}`),
        `${props.record.code}.${format === "vue" ? "vue" : "ts"}`,
        "text/plain",
      );
  });
}
</script>
<template>
  <el-dialog
    :model-value="true"
    :title="`${record.name} · 预览`"
    width="min(680px, 95vw)"
    @close="$emit('close')"
  >
    <div class="export-actions">
      <el-button :disabled="op.busy.value" @click="exportFile('schema')"
        >导出 JSON Schema</el-button
      ><el-dropdown :disabled="op.busy.value" @command="exportFile"
        ><el-button
          >生成开发文件<el-icon class="el-icon--right"
            ><ArrowDown /></el-icon></el-button
        ><template #dropdown
          ><el-dropdown-menu
            ><el-dropdown-item command="vue">Vue 表单</el-dropdown-item
            ><el-dropdown-item command="typescript"
              >TypeScript 类型</el-dropdown-item
            ></el-dropdown-menu
          ></template
        ></el-dropdown
      >
    </div>
    <FormPreview
      v-model="values"
      :fields="record.definition.fields"
      :errors="errors"
      :disabled="op.busy.value"
      @update:model-value="valid = false"
    />
    <el-alert v-if="valid" type="success" title="校验通过" :closable="false" />
    <template #footer
      ><el-button @click="$emit('close')">关闭</el-button
      ><el-button type="primary" :loading="op.busy.value" @click="check"
        >校验</el-button
      ></template
    >
  </el-dialog>
</template>
<style scoped>
.export-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-bottom: 20px;
}
</style>
