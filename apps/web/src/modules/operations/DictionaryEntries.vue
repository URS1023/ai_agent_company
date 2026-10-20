<script setup lang="ts">
/** 提取qKnow字典数据页的标签、键值、排序、默认值和状态结构。
 * 来源：qKnow-develop/qknow-ui/src/views/system/system/dict/data.vue；许可见licenses/qKnow-LICENSE.txt。
 */
import type { Choice } from "./support";
const model = defineModel<Choice[]>({ required: true });
const props = defineProps<{ disabled?: boolean; compact?: boolean }>();
function add() {
  model.value.push(
    props.compact
      ? { label: "", value: "" }
      : {
          label: "",
          value: "",
          enabled: true,
          is_default: false,
          order: model.value.length,
          tag_type: "info",
        },
  );
}
function setDefault(index: number, value: unknown) {
  model.value.forEach((item, position) => {
    item.is_default = !!value && position === index;
  });
}
</script>
<template>
  <div class="entries-editor">
    <el-table :data="model" size="small" border max-height="360">
      <el-table-column label="名称" min-width="125"
        ><template #default="{ row }"
          ><el-input
            v-model="row.label"
            aria-label="选项名称"
            :disabled="disabled"
            maxlength="200" /></template
      ></el-table-column>
      <el-table-column label="值" min-width="125"
        ><template #default="{ row }"
          ><el-input
            v-model="row.value"
            aria-label="选项值"
            :disabled="disabled"
            maxlength="200" /></template
      ></el-table-column>
      <el-table-column v-if="!compact" label="排序" width="125"
        ><template #default="{ row }"
          ><el-input-number
            v-model="row.order"
            aria-label="选项排序"
            :min="0"
            :max="10000"
            :disabled="disabled"
            controls-position="right"
            style="width: 100%" /></template
      ></el-table-column>
      <el-table-column v-if="!compact" label="默认" width="65"
        ><template #default="{ row, $index }"
          ><el-checkbox
            :model-value="row.is_default"
            aria-label="默认选项"
            :disabled="disabled || !row.enabled"
            @change="setDefault($index, $event)" /></template
      ></el-table-column>
      <el-table-column v-if="!compact" label="启用" width="70"
        ><template #default="{ row }"
          ><el-switch
            v-model="row.enabled"
            aria-label="选项启用"
            :disabled="disabled"
            @change="!row.enabled && (row.is_default = false)" /></template
      ></el-table-column>
      <el-table-column v-if="!compact" label="标签" width="110"
        ><template #default="{ row }"
          ><el-select
            v-model="row.tag_type"
            aria-label="标签颜色"
            :disabled="disabled"
            ><el-option
              v-for="item in [
                { value: 'info', label: '灰' },
                { value: 'primary', label: '蓝' },
                { value: 'success', label: '绿' },
                { value: 'warning', label: '橙' },
                { value: 'danger', label: '红' },
              ]"
              :key="item.value"
              :label="item.label"
              :value="item.value" /></el-select></template
      ></el-table-column>
      <el-table-column v-if="!disabled" width="65"
        ><template #default="{ $index }"
          ><el-button link type="danger" @click="model.splice($index, 1)"
            >删除</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <el-button v-if="!disabled" :disabled="model.length >= 200" @click="add"
      >添加选项</el-button
    >
  </div>
</template>
<style scoped>
.entries-editor {
  display: grid;
  gap: 10px;
  width: 100%;
}
.entries-editor > .el-button {
  justify-self: start;
}
</style>
