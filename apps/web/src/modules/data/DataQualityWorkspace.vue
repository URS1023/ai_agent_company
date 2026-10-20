<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { api } from "../../api/client";
import { useSession } from "../../stores/session";
import { useOperation } from "../../composables/useOperation";
import { usePreparationQueries } from "./usePreparationQueries";
import {
  PreparationError,
  newQualityRule,
  preparationErrors,
  qualityPayload,
  queryColumns,
  type Fields,
  type QualityDraft,
} from "./dataPreparation";
import DataFieldSelect from "./DataFieldSelect.vue";
import DataQualityRuleForm from "./DataQualityRuleForm.vue";
import QualityCheckResults from "./QualityCheckResults.vue";
import PreparationQuerySelect from "./PreparationQuerySelect.vue";
const session = useSession();
const op = useOperation();
const sources = usePreparationQueries();
const required = ref<string[]>([]);
const unique = ref<string[]>([]);
const rules = ref<QualityDraft[]>([]);
const localErrors = ref<Fields>({});
const errors = computed(() => ({
  ...preparationErrors(op.fields.value, op.error.value),
  ...localErrors.value,
}));
const result = ref<Record<string, any> | null>(null);
const originalRows = ref<Record<string, any>[] | undefined>();
const originalColumns = ref<string[]>([]);
async function submit() {
  localErrors.value = {};
  let payload;
  try {
    payload = qualityPayload(
      sources.queryId.value,
      required.value,
      unique.value,
      rules.value,
      sources.columns.value,
    );
  } catch (error) {
    if (error instanceof PreparationError) localErrors.value = error.fields;
    return;
  }
  await op.run(async () => {
    result.value = await api.post("/data-quality-checks", payload);
    originalRows.value = undefined;
  }, "质量检查已完成");
}
/** 原始行和行号下载均先重查历史结果的当前授权，不重新执行SQL。 */
async function authorizedSource() {
  return api.get(`/queries/${encodeURIComponent(result.value!.query_id)}`);
}
async function showOriginal(rowNumbers: number[]) {
  if (!rowNumbers.length) {
    originalRows.value = undefined;
    return;
  }
  originalRows.value = undefined;
  await op.run(async () => {
    const query = await authorizedSource();
    const rows = query.rows ?? query.result?.rows ?? [];
    originalColumns.value = ["原始行号", ...queryColumns(query)];
    originalRows.value = rowNumbers.map((n) => ({
      ...rows[n - 1],
      原始行号: n,
    }));
  });
}
async function downloadRows(rowNumbers: number[], name: string) {
  await op.run(async () => {
    await authorizedSource();
    const url = URL.createObjectURL(
      new Blob(["\uFEFF原始行号\r\n", rowNumbers.join("\r\n")], {
        type: "text/csv;charset=utf-8",
      }),
    );
    const link = document.createElement("a");
    link.href = url;
    link.download = `${name.replace(/[\\/:*?"<>|]/g, "_")}-行号.csv`;
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
onMounted(() => op.run(sources.load));
</script>
<template>
  <div class="page-stack">
    <section class="panel">
      <el-form
        label-position="top"
        :disabled="op.busy.value || !session.can('m06.write')"
        @submit.prevent="submit"
      >
        <el-form-item label="数据查询" :error="errors.query_id"
          ><PreparationQuerySelect :catalog="sources"
        /></el-form-item>
        <div class="form-grid">
          <el-form-item label="必填字段" :error="errors.required_fields"
            ><DataFieldSelect
              :model-value="required"
              :columns="sources.columns.value"
              multiple
              @update:model-value="
                required = $event as string[]
              " /></el-form-item
          ><el-form-item label="唯一字段" :error="errors.unique_fields"
            ><DataFieldSelect
              :model-value="unique"
              :columns="sources.columns.value"
              multiple
              @update:model-value="unique = $event as string[]"
          /></el-form-item>
        </div>
        <DataQualityRuleForm
          v-for="(rule, index) in rules"
          :key="rule.key"
          v-model="rules[index]!"
          :columns="sources.columns.value"
          :index="index"
          :errors="errors"
          @remove="rules.splice(index, 1)"
        />
        <el-form-item v-if="errors.rules" :error="errors.rules" />
        <div class="row-actions">
          <el-button
            :disabled="rules.length >= 50"
            @click="rules.push(newQualityRule())"
            >添加规则</el-button
          ><span class="spacer" /><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            :disabled="
              !sources.queryId.value ||
              sources.selecting.value ||
              sources.loading.value
            "
            >执行检查</el-button
          >
        </div>
      </el-form>
    </section>
    <QualityCheckResults
      v-if="result"
      :result="result"
      :busy="op.busy.value"
      :original-rows="originalRows"
      :original-columns="originalColumns"
      @original="showOriginal"
      @download="downloadRows"
    />
  </div>
</template>
