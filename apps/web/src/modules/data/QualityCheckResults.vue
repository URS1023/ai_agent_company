<script setup lang="ts">
import { computed, ref } from "vue";
import ResultTable from "../../components/common/ResultTable.vue";
import { displayScalar, outcomeLabels, qualityTypes } from "./dataPreparation";
const props = defineProps<{
  result: Record<string, any>;
  busy: boolean;
  originalRows?: Record<string, any>[];
  originalColumns?: string[];
}>();
const emit = defineEmits<{
  original: [rowNumbers: number[]];
  download: [rowNumbers: number[], name: string];
}>();
const selected = ref<Record<string, any> | null>(null);
const checks = computed(() => props.result.checks ?? []);
function ruleName(value: string) {
  return qualityTypes.find((x) => x.value === value)?.label ?? value;
}
</script>
<template>
  <section class="panel">
    <div class="panel-heading">
      <h3>检查结果</h3>
      <div class="row-actions">
        <el-tag
          :type="
            result.outcome === 'passed'
              ? 'success'
              : result.outcome === 'failed'
                ? 'danger'
                : 'warning'
          "
          >{{ outcomeLabels[result.outcome] ?? result.outcome }}</el-tag
        ><span>已检查 {{ result.rows_checked }} 行</span
        ><el-tag v-if="result.truncated" type="warning">仅含截断结果</el-tag>
      </div>
    </div>
    <el-table :data="checks" stripe>
      <el-table-column label="规则" min-width="150"
        ><template #default="{ row }">{{
          row.name || ruleName(row.rule)
        }}</template></el-table-column
      ><el-table-column
        prop="field"
        label="字段"
        min-width="130"
      /><el-table-column label="结果" width="100"
        ><template #default="{ row }">{{
          outcomeLabels[row.outcome] ?? row.outcome
        }}</template></el-table-column
      ><el-table-column
        prop="affected_count"
        label="涉及行数"
        width="110"
      /><el-table-column
        prop="skipped_count"
        label="跳过空值"
        width="110"
      /><el-table-column label="操作" width="180"
        ><template #default="{ row }"
          ><el-button link type="primary" @click="selected = row"
            >查看</el-button
          ><el-button
            link
            type="primary"
            :disabled="busy || !row.row_numbers?.length"
            @click="emit('original', row.row_numbers)"
            >原始行</el-button
          ><el-button
            link
            type="primary"
            :disabled="busy || !row.row_numbers?.length"
            @click="
              emit('download', row.row_numbers, row.name || ruleName(row.rule))
            "
            >行号</el-button
          ></template
        ></el-table-column
      >
    </el-table>
    <el-drawer
      :model-value="selected !== null"
      :title="selected?.name || ruleName(selected?.rule || '')"
      size="680px"
      @close="selected = null"
    >
      <template v-if="selected"
        ><el-descriptions :column="2" border
          ><el-descriptions-item label="涉及行数">{{
            selected.affected_count
          }}</el-descriptions-item
          ><el-descriptions-item label="问题次数">{{
            selected.count
          }}</el-descriptions-item></el-descriptions
        >
        <div class="example-heading">
          <strong>失败样例</strong
          ><el-tag v-if="selected.examples_truncated" type="info"
            >前 {{ selected.examples?.length ?? 0 }} 条</el-tag
          >
        </div>
        <el-table :data="selected.examples ?? []"
          ><el-table-column
            prop="row_number"
            label="行号"
            width="75" /><el-table-column label="值" min-width="140"
            ><template #default="{ row }">{{
              displayScalar(row.value)
            }}</template></el-table-column
          ><el-table-column
            v-if="selected.rule === 'compare'"
            label="另一字段值"
            min-width="140"
            ><template #default="{ row }">{{
              displayScalar(row.other_value)
            }}</template></el-table-column
          ><el-table-column prop="reason" label="问题" min-width="180"
        /></el-table>
        <div class="example-heading"><strong>全部涉及行号</strong></div>
        <div class="row-numbers">
          {{ selected.row_numbers?.join("、") || "—" }}
        </div></template
      >
    </el-drawer>
    <el-dialog
      :model-value="originalRows !== undefined"
      title="原始行"
      width="85%"
      @close="emit('original', [])"
      ><ResultTable :rows="originalRows ?? []" :columns="originalColumns"
    /></el-dialog>
  </section>
</template>
<style scoped>
.example-heading {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 20px 0 12px;
}
.row-numbers {
  line-height: 1.8;
  overflow-wrap: anywhere;
}
</style>
