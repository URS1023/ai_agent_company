<script setup lang="ts">
/** 模板换数复用同一批已授权查询；字段映射随组件保存，不执行额外查询。 */
import { computed, ref } from "vue";
import {
  sourceCalendarTypes,
  sourceCardTypes,
  sourceTextTypes,
} from "./sourcePresentation";
import {
  tableTypes,
  listTypes,
  numberTypes,
  staticTypes,
} from "./presentation";

const bindings = defineModel<Record<string, Record<string, any>>>({
  required: true,
});
const props = defineProps<{
  widgets: Record<string, any>[];
  queries: Record<string, any>[];
}>();
const allQuery = ref("");
const staticComponents = [
  "JText",
  "JDragImg",
  "JImg",
  "JCustomIcon",
  "JDragBorder",
  "JDragDecoration",
  "JCarousel",
  "text",
  ...staticTypes,
];
const widgets = computed(() =>
  props.widgets.filter(
    (widget) =>
      !staticComponents.includes(widget.component) &&
      widget.binding?.source_type !== "inspection_run",
  ),
);
const multiTypes = [
  "JStackBar",
  "JMultipleBar",
  "JPercentBar",
  "JMixLineBar",
  "JMultipleLine",
  "JSmoothLine",
  "JStepLine",
  "JNegativeBar",
];
function columns(id: string) {
  const query = props.queries.find(
    (item) => item.id === bindings.value[id]?.query_id,
  );
  return (query?.columns ?? query?.result?.columns ?? []).map(
    String,
  ) as string[];
}
function bind(id: string, queryId: string) {
  if (!queryId) {
    const next = { ...bindings.value };
    delete next[id];
    bindings.value = next;
    return;
  }
  const query = props.queries.find((item) => item.id === queryId);
  const fields = query?.columns ?? query?.result?.columns ?? [];
  bindings.value = {
    ...bindings.value,
    [id]: {
      query_id: queryId,
      ...(fields.includes("name") ? { label_field: "name" } : {}),
      ...(fields.includes("value") ? { value_field: "value" } : {}),
    },
  };
}
function applyAll() {
  if (allQuery.value)
    for (const widget of widgets.value) bind(widget.id, allQuery.value);
}
function field(id: string, key: string, value: string | string[]) {
  const next = { ...bindings.value[id] };
  if (value?.length) next[key] = value;
  else delete next[key];
  bindings.value = { ...bindings.value, [id]: next };
}
</script>

<template>
  <div v-if="widgets.length" class="template-bindings">
    <div class="bind-all">
      <el-select
        v-model="allQuery"
        filterable
        clearable
        placeholder="统一选择数据查询"
        ><el-option
          v-for="query in queries"
          :key="query.id"
          :value="query.id"
          :label="query.name || query.sql" /></el-select
      ><el-button :disabled="!allQuery" @click="applyAll">应用全部</el-button>
    </div>
    <el-collapse>
      <el-collapse-item
        v-for="widget in widgets"
        :key="widget.id"
        :name="widget.id"
      >
        <template #title
          ><span class="binding-name">{{
            widget.title || widget.componentName || widget.name
          }}</span
          ><el-tag
            size="small"
            :type="bindings[widget.id] ? 'success' : 'info'"
            >{{ bindings[widget.id] ? "已选查询" : "未绑定" }}</el-tag
          ></template
        >
        <el-form-item label="数据查询"
          ><el-select
            :model-value="bindings[widget.id]?.query_id"
            filterable
            clearable
            @change="bind(widget.id, $event)"
            ><el-option
              v-for="query in queries"
              :key="query.id"
              :value="query.id"
              :label="query.name || query.sql" /></el-select
        ></el-form-item>
        <template v-if="bindings[widget.id]">
          <el-form-item
            v-if="tableTypes.includes(widget.component)"
            label="显示列"
            ><el-select
              :model-value="bindings[widget.id]?.fields"
              multiple
              :multiple-limit="20"
              @change="field(widget.id, 'fields', $event)"
              ><el-option
                v-for="column in columns(widget.id)"
                :key="column"
                :value="column"
                :label="column" /></el-select
          ></el-form-item>
          <el-form-item
            v-else-if="!numberTypes.includes(widget.component)"
            label="维度字段"
            ><el-select
              :model-value="bindings[widget.id]?.label_field"
              clearable
              @change="field(widget.id, 'label_field', $event)"
              ><el-option
                v-for="column in columns(widget.id)"
                :key="column"
                :value="column"
                :label="column" /></el-select
          ></el-form-item>
          <el-form-item
            v-if="multiTypes.includes(widget.component)"
            label="系列字段"
            ><el-select
              :model-value="bindings[widget.id]?.value_fields"
              multiple
              :multiple-limit="8"
              @change="field(widget.id, 'value_fields', $event)"
              ><el-option
                v-for="column in columns(widget.id)"
                :key="column"
                :value="column"
                :label="column" /></el-select
          ></el-form-item>
          <el-form-item
            v-else-if="
              !tableTypes.includes(widget.component) &&
              !listTypes.includes(widget.component) &&
              !sourceTextTypes.includes(widget.component)
            "
            label="数值字段"
            ><el-select
              :model-value="bindings[widget.id]?.value_field"
              clearable
              @change="field(widget.id, 'value_field', $event)"
              ><el-option
                v-for="column in columns(widget.id)"
                :key="column"
                :value="column"
                :label="column" /></el-select
          ></el-form-item>
          <template v-if="sourceTextTypes.includes(widget.component)">
            <el-form-item
              v-for="mapping in [
                {
                  key: 'time_field',
                  label: '时间字段',
                  required:
                    sourceCalendarTypes.includes(widget.component) ||
                    widget.component === 'JDevHistory',
                },
                {
                  key: 'description_field',
                  label: '说明字段',
                  required: false,
                },
                ...(sourceCardTypes.includes(widget.component)
                  ? [{ key: 'value_field', label: '数值字段', required: false }]
                  : []),
              ]"
              :key="mapping.key"
              :label="mapping.label"
              :required="mapping.required"
              ><el-select
                :model-value="bindings[widget.id]?.[mapping.key]"
                :clearable="!mapping.required"
                filterable
                @change="field(widget.id, mapping.key, $event)"
                ><el-option
                  v-for="column in columns(widget.id)"
                  :key="column"
                  :value="column"
                  :label="column" /></el-select
            ></el-form-item>
          </template>
        </template>
      </el-collapse-item>
    </el-collapse>
  </div>
</template>

<style scoped>
.template-bindings {
  margin: 4px 0 20px;
}
.bind-all {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.bind-all > .el-select {
  flex: 1;
  min-width: 0;
}
.binding-name {
  flex: 1;
  margin-right: 12px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-size: 14px;
}
.template-bindings :deep(.el-collapse-item__content) {
  padding: 8px 0 0;
}
.template-bindings :deep(.el-select) {
  width: 100%;
}
</style>
