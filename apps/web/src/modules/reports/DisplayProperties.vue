<script setup lang="ts">
/** 展示组件属性：沿用源body/option配置，仅提供当前类型实际使用的字段。 */
import { computed, watch } from "vue";
import {
  tableTypes,
  listTypes,
  progressTypes,
  staticTypes,
} from "./presentation";
import RichTextEditor from "./RichTextEditor.vue";
import { sourceCalendarTypes } from "./sourcePresentation";
const widget = defineModel<Record<string, any>>({ required: true });
const props = defineProps<{ columns: string[]; sourceReadonly?: boolean }>();
watch(
  () => widget.value.id,
  () => {
    widget.value.config ??= {};
    widget.value.config.option ??= {};
    widget.value.config.option.body ??= {};
    widget.value.config.column_labels ??= {};
  },
  { immediate: true },
);
const component = computed(() => widget.value.component);
const option = computed(() => widget.value.config.option);
const editableText = computed(() =>
  ["JText", "JDragEditor"].includes(component.value),
);
const textAppearance = computed(
  () =>
    editableText.value ||
    ["JCurrentTime", "JNumber", "JCountTo"].includes(component.value),
);
const mapped = computed(
  () => !!widget.value.binding?.query_id && !props.sourceReadonly,
);
const optionalFields = computed(() => [
  ...(listTypes.includes(component.value)
    ? [
        { key: "description_field", label: "说明字段" },
        { key: "time_field", label: "时间字段" },
      ]
    : []),
  ...(!tableTypes.includes(component.value) &&
  !staticTypes.includes(component.value) &&
  !sourceCalendarTypes.includes(component.value) &&
  component.value !== "JDevHistory"
    ? [{ key: "unit_field", label: "单位字段" }]
    : []),
  ...(progressTypes.includes(component.value)
    ? [{ key: "total_field", label: "目标字段" }]
    : []),
]);
function setMapping(key: string, value?: string) {
  if (value) widget.value.binding[key] = value;
  else delete widget.value.binding[key];
}
</script>
<template>
  <template v-if="editableText">
    <el-form-item label="内容"
      ><RichTextEditor
        v-if="component === 'JDragEditor'"
        v-model="option.body.text" /><el-input
        v-else
        v-model="option.body.text"
        type="textarea"
        :rows="3"
        maxlength="100000"
    /></el-form-item>
  </template>
  <template v-if="textAppearance">
    <el-form-item label="字号"
      ><el-input-number v-model="option.body.fontSize" :min="8" :max="160"
    /></el-form-item>
    <el-form-item label="文字颜色"
      ><el-color-picker v-model="option.body.color"
    /></el-form-item>
    <el-form-item label="对齐"
      ><el-select v-model="option.body.textAlign"
        ><el-option label="居左" value="left" /><el-option
          label="居中"
          value="center" /><el-option label="居右" value="right" /></el-select
    ></el-form-item>
  </template>
  <template v-if="component === 'JCurrentTime'"
    ><el-form-item label="时区"
      ><el-select v-model="option.timezone"
        ><el-option label="北京时间" value="Asia/Shanghai" /><el-option
          label="UTC"
          value="UTC" /></el-select></el-form-item
    ><el-form-item label="仅显示日期"
      ><el-switch v-model="option.date_only" /></el-form-item
  ></template>
  <el-form-item v-if="component === 'JSemiGauge'" label="仪表上限"
    ><el-input-number v-model="option.max" :min="0.000001"
  /></el-form-item>
  <template v-if="mapped">
    <el-form-item v-if="tableTypes.includes(component)" label="显示列"
      ><el-select v-model="widget.binding.fields" multiple :multiple-limit="20"
        ><el-option
          v-for="column in props.columns"
          :key="column"
          :value="column"
          :label="column" /></el-select
    ></el-form-item>
    <el-form-item
      v-for="column in tableTypes.includes(component)
        ? (widget.binding.fields ?? [])
        : []"
      :key="column"
      :label="column"
      ><el-input
        v-model="widget.config.column_labels[column]"
        placeholder="列名称"
    /></el-form-item>
    <el-form-item
      v-for="field in optionalFields"
      :key="field.key"
      :label="field.label"
      ><el-select
        :model-value="widget.binding[field.key]"
        clearable
        @change="setMapping(field.key, $event)"
        ><el-option
          v-for="column in props.columns"
          :key="column"
          :label="column"
          :value="column" /></el-select
    ></el-form-item>
  </template>
</template>
