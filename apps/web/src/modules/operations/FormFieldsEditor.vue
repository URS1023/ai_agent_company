<script setup lang="ts">
/** 声明式字段编辑，不执行脚本或生成数据库结构。 */
import { computed, ref } from "vue";
import DictionaryEntries from "./DictionaryEntries.vue";
import type { FormField } from "./support";
const model = defineModel<FormField[]>({ required: true });
defineProps<{ disabled?: boolean }>();
const selected = ref(0);
const field = computed(() => model.value[selected.value]);
const kinds = [
  { value: "text", label: "文本" },
  { value: "textarea", label: "多行文本" },
  { value: "number", label: "数值" },
  { value: "integer", label: "整数" },
  { value: "select", label: "下拉选择" },
  { value: "boolean", label: "开关" },
  { value: "date", label: "日期" },
];
function add() {
  let number = model.value.length + 1;
  while (model.value.some((item) => item.key === `field_${number}`)) number++;
  model.value.push({
    key: `field_${number}`,
    label: `字段 ${number}`,
    kind: "text",
    required: false,
    max_length: 1000,
    choices: [],
  });
  selected.value = model.value.length - 1;
}
function remove(index: number) {
  model.value.splice(index, 1);
  selected.value = Math.min(selected.value, model.value.length - 1);
}
function move(offset: number) {
  const next = selected.value + offset;
  if (next < 0 || next >= model.value.length) return;
  const [item] = model.value.splice(selected.value, 1);
  model.value.splice(next, 0, item!);
  selected.value = next;
}
function changeKind() {
  if (!field.value) return;
  if (field.value.kind === "select") field.value.choices ??= [];
  if (["text", "textarea"].includes(field.value.kind))
    field.value.max_length ??= 1000;
}
</script>
<template>
  <div class="field-builder">
    <aside>
      <div class="field-list">
        <button
          v-for="(item, index) in model"
          :key="index"
          type="button"
          :class="{ active: selected === index }"
          @click="selected = index"
        >
          <span>{{ item.label || "未命名字段" }}</span
          ><span>{{
            kinds.find((kind) => kind.value === item.kind)?.label
          }}</span>
        </button>
      </div>
      <el-button v-if="!disabled" :disabled="model.length >= 100" @click="add"
        >添加字段</el-button
      >
    </aside>
    <div v-if="field" class="field-properties">
      <div class="field-order" v-if="!disabled">
        <el-button :disabled="selected === 0" @click="move(-1)">上移</el-button
        ><el-button :disabled="selected === model.length - 1" @click="move(1)"
          >下移</el-button
        ><el-button type="danger" plain @click="remove(selected)"
          >删除字段</el-button
        >
      </div>
      <el-form-item label="字段名称"
        ><el-input v-model="field.label" :disabled="disabled" maxlength="200"
      /></el-form-item>
      <el-form-item label="字段编码"
        ><el-input v-model="field.key" :disabled="disabled" maxlength="64"
      /></el-form-item>
      <el-form-item label="控件"
        ><el-select
          v-model="field.kind"
          :disabled="disabled"
          @change="changeKind"
          ><el-option
            v-for="kind in kinds"
            :key="kind.value"
            :label="kind.label"
            :value="kind.value" /></el-select
      ></el-form-item>
      <el-form-item label="必填"
        ><el-switch v-model="field.required" :disabled="disabled"
      /></el-form-item>
      <template v-if="['number', 'integer'].includes(field.kind)">
        <el-form-item label="最小值"
          ><el-input-number
            v-model="field.minimum"
            :disabled="disabled"
            :precision="field.kind === 'integer' ? 0 : undefined"
            :max="field.maximum"
        /></el-form-item>
        <el-form-item label="最大值"
          ><el-input-number
            v-model="field.maximum"
            :disabled="disabled"
            :precision="field.kind === 'integer' ? 0 : undefined"
            :min="field.minimum"
        /></el-form-item>
      </template>
      <el-form-item
        v-if="['text', 'textarea'].includes(field.kind)"
        label="最多字数"
        ><el-input-number
          v-model="field.max_length"
          :disabled="disabled"
          :min="1"
          :max="20000"
      /></el-form-item>
      <el-form-item v-if="field.kind === 'select'" label="选项"
        ><DictionaryEntries
          :model-value="field.choices ?? []"
          compact
          :disabled="disabled"
          @update:model-value="field.choices = $event"
      /></el-form-item>
      <el-form-item
        v-if="!['boolean', 'date'].includes(field.kind)"
        label="占位内容"
        ><el-input
          v-model="field.placeholder"
          :disabled="disabled"
          maxlength="200"
      /></el-form-item>
    </div>
    <el-empty v-else description="添加第一个字段" />
  </div>
</template>
<style scoped>
.field-builder {
  display: grid;
  grid-template-columns: 190px minmax(0, 1fr);
  gap: 20px;
  width: 100%;
}
.field-builder aside {
  border-right: 1px solid var(--el-border-color-light);
  padding-right: 12px;
}
.field-list {
  display: grid;
  gap: 4px;
  max-height: 430px;
  overflow: auto;
  margin-bottom: 10px;
}
.field-list button {
  background: transparent;
  border: 0;
  border-radius: 4px;
  text-align: left;
  padding: 10px;
  display: flex;
  gap: 8px;
  justify-content: space-between;
  cursor: pointer;
  color: var(--el-text-color-primary);
  font: inherit;
}
.field-list button.active {
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}
.field-list button span:last-child {
  font-size: 12px;
  white-space: nowrap;
  color: var(--el-text-color-secondary);
}
.field-properties {
  min-width: 0;
}
.field-order {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 14px;
}
@media (max-width: 700px) {
  .field-builder {
    grid-template-columns: 1fr;
  }
  .field-builder aside {
    border-right: 0;
  }
}
</style>
