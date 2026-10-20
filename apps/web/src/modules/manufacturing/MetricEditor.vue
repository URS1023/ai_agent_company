<script setup lang="ts">
export interface InspectionMetric {
  id: string;
  name: string;
  category: string;
  unit: string;
  component_name?: string;
  standard_min: string;
  standard_max: string;
  minimum_confidence: string;
  blocking_severities: string[];
}
const metrics = defineModel<InspectionMetric[]>({ required: true });
function add() {
  metrics.value.push({
    id: crypto.randomUUID(),
    name: "",
    category: "numeric",
    unit: "",
    component_name: "",
    standard_min: "",
    standard_max: "",
    minimum_confidence: "0.8",
    blocking_severities: ["moderate", "severe"],
  });
}
function remove(index: number) {
  metrics.value.splice(index, 1);
}
</script>
<template>
  <div class="metric-editor">
    <el-table :data="metrics" border
      ><el-table-column label="检测项目" min-width="150"
        ><template #default="{ row }"
          ><el-input v-model="row.name" required /></template></el-table-column
      ><el-table-column label="检测维度" width="135"
        ><template #default="{ row }"
          ><el-select v-model="row.category"
            ><el-option value="numeric" label="数值指标" /><el-option
              value="component"
              label="成分含量" /><el-option
              value="appearance"
              label="外观缺陷" /></el-select></template></el-table-column
      ><el-table-column label="成分名称" width="115"
        ><template #default="{ row }"
          ><el-input
            v-model="row.component_name"
            :disabled="row.category !== 'component'"
            :required="
              row.category === 'component'
            " /></template></el-table-column
      ><el-table-column label="单位" width="85"
        ><template #default="{ row }"
          ><el-input
            v-model="row.unit"
            :disabled="
              row.category === 'appearance'
            " /></template></el-table-column
      ><el-table-column label="下限" width="100"
        ><template #default="{ row }"
          ><el-input
            v-model="row.standard_min"
            :disabled="row.category === 'appearance'"
            type="number"
            step="any" /></template></el-table-column
      ><el-table-column label="上限" width="100"
        ><template #default="{ row }"
          ><el-input
            v-model="row.standard_max"
            :disabled="row.category === 'appearance'"
            type="number"
            step="any" /></template></el-table-column
      ><el-table-column label="最低置信度" width="105"
        ><template #default="{ row }"
          ><el-input
            v-model="row.minimum_confidence"
            type="number"
            min="0"
            max="1"
            step="0.01" /></template></el-table-column
      ><el-table-column label="拦截等级" min-width="150"
        ><template #default="{ row }"
          ><el-select
            v-model="row.blocking_severities"
            multiple
            collapse-tags
            :disabled="row.category !== 'appearance'"
            ><el-option value="severe" label="严重" /><el-option
              value="moderate"
              label="中度" /><el-option
              value="minor"
              label="轻微" /></el-select></template></el-table-column
      ><el-table-column width="65"
        ><template #default="{ $index }"
          ><el-button link type="danger" @click="remove($index)"
            >删除</el-button
          ></template
        ></el-table-column
      ></el-table
    ><el-button style="margin-top: 12px" @click="add">添加检测项目</el-button>
  </div>
</template>
