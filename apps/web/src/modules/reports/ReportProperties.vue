<script setup lang="ts">
import { computed, watch } from "vue";
import MaterialProperties from "./MaterialProperties.vue";
const widget = defineModel<Record<string, any> | null>({ required: true });
const props = defineProps<{ queries: Record<string, any>[] }>();
const inspectionSource = computed(
  () => widget.value?.binding?.source_type === "inspection_run",
);
const currentQuery = computed(() =>
  props.queries.find((x) => x.id === widget.value?.binding?.query_id),
);
const multipleSeries = computed(() =>
  [
    "JStackBar",
    "JMultipleBar",
    "JPercentBar",
    "JMixLineBar",
    "JMultipleLine",
    "JSmoothLine",
    "JStepLine",
    "JNegativeBar",
  ].includes(widget.value?.component),
);
const columns = computed(
  () =>
    currentQuery.value?.columns ?? currentQuery.value?.result?.columns ?? [],
);

function invalidateSeries() {
  if (!widget.value || inspectionSource.value) return;
  // 映射改变后旧结果不能代表新字段；等待服务端重新执行并生成可信系列。
  widget.value.data = [];
  delete widget.value.data_provenance;
  delete widget.value.series_model;
  if (widget.value.config) widget.value.config.chartDataParsed = [];
}

watch(
  () => {
    const binding = widget.value?.binding;
    return {
      id: widget.value?.id,
      mapping: JSON.stringify([
        binding?.query_id ?? null,
        binding?.label_field ?? null,
        binding?.value_field ?? null,
        binding?.value_fields ?? [],
        binding?.secondary_value_fields ?? [],
      ]),
    };
  },
  (current, previous) => {
    // 切换选中组件或接收相同映射的服务端刷新，不应清除其已验证结果。
    if (current.id === previous.id && current.mapping !== previous.mapping)
      invalidateSeries();
  },
  { flush: "sync" },
);

function bindQuery(id: string) {
  if (!widget.value || inspectionSource.value) return;
  if (id) widget.value.binding = { query_id: id };
  else delete widget.value.binding;
  invalidateSeries();
}
function setTitle(value: string) {
  if (widget.value) {
    widget.value.title = value;
    widget.value.componentName = value;
    if (widget.value.component === "JText") {
      widget.value.config ??= {};
      widget.value.config.option ??= {};
      widget.value.config.option.body ??= {};
      widget.value.config.option.body.text = value;
    }
  }
}
</script>
<template>
  <aside class="report-properties">
    <template v-if="widget"
      ><h3>组件属性</h3>
      <el-form label-position="top"
        ><el-form-item label="名称"
          ><el-input
            :model-value="widget.title ?? widget.componentName"
            @update:model-value="setTitle"
        /></el-form-item>
        <div class="property-pair">
          <el-form-item label="X"
            ><el-input-number
              v-model="widget.x"
              :step="5"
              controls-position="right" /></el-form-item
          ><el-form-item label="Y"
            ><el-input-number
              v-model="widget.y"
              :step="5"
              controls-position="right" /></el-form-item
          ><el-form-item label="宽度"
            ><el-input-number
              v-model="widget.w"
              :min="20"
              :step="5"
              controls-position="right" /></el-form-item
          ><el-form-item label="高度"
            ><el-input-number
              v-model="widget.h"
              :min="20"
              :step="5"
              controls-position="right"
          /></el-form-item>
        </div>
        <el-form-item label="旋转"
          ><el-slider
            v-model="widget.rotation"
            :min="-180"
            :max="180" /></el-form-item
        ><el-form-item label="锁定"
          ><el-switch v-model="widget.locked" /></el-form-item
        ><el-form-item
          v-if="
            !inspectionSource &&
            widget.type !== 'decoration' &&
            ![
              'JText',
              'JImg',
              'JCustomIcon',
              'JDragBorder',
              'JDragDecoration',
            ].includes(widget.component)
          "
          label="数据查询"
          ><el-select
            :model-value="widget.binding?.query_id"
            filterable
            clearable
            @change="bindQuery"
            ><el-option
              v-for="query in queries"
              :key="query.id"
              :value="query.id"
              :label="
                query.name ?? query.sql ?? query.id
              " /></el-select></el-form-item
        ><MaterialProperties :key="widget.id" v-model="widget" /><template
          v-if="!inspectionSource && widget.binding?.query_id"
          ><el-form-item label="维度字段"
            ><el-select
              v-model="widget.binding.label_field"
              allow-create
              filterable
              clearable
              ><el-option
                v-for="column in currentQuery?.columns ??
                currentQuery?.result?.columns ??
                []"
                :key="String(column)"
                :value="String(column)"
                :label="String(column)" /></el-select></el-form-item
          ><el-form-item v-if="!multipleSeries" label="数值字段"
            ><el-select
              v-model="widget.binding.value_field"
              allow-create
              filterable
              clearable
              ><el-option
                v-for="column in currentQuery?.columns ??
                currentQuery?.result?.columns ??
                []"
                :key="String(column)"
                :value="String(column)"
                :label="String(column)" /></el-select
          ></el-form-item>
          <el-form-item v-else label="系列字段">
            <el-select
              v-model="widget.binding.value_fields"
              multiple
              filterable
              :multiple-limit="8"
            >
              <el-option
                v-for="column in columns"
                :key="String(column)"
                :value="String(column)"
                :label="String(column)"
              />
            </el-select>
          </el-form-item>
          <el-form-item
            v-if="widget.component === 'JMixLineBar'"
            label="右轴系列"
          >
            <el-select
              v-model="widget.binding.secondary_value_fields"
              multiple
              clearable
            >
              <el-option
                v-for="column in (widget.binding.value_fields ?? []).slice(1)"
                :key="column"
                :value="column"
                :label="column"
              />
            </el-select>
          </el-form-item> </template></el-form></template
    ><el-empty v-else description="选择组件" :image-size="70" />
  </aside>
</template>
<style scoped>
.report-properties {
  background: white;
  width: 270px;
  min-height: 0;
  overflow: auto;
  padding: 20px;
  border-left: 1px solid #e5e7eb;
}
.property-pair {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 10px;
}
.property-pair :deep(.el-input-number) {
  width: 100%;
}
</style>
