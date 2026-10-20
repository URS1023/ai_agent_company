<script setup lang="ts">
import { computed, onMounted, ref, shallowRef } from "vue";
import { api, type Entity } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import {
  newOperation,
  operationColumns,
  processingPayload,
  PreparationError,
  preparationErrors,
  type Fields,
  type OperationDraft,
} from "./dataPreparation";
import { usePreparationQueries } from "./usePreparationQueries";
import ProcessingOperationForm from "./ProcessingOperationForm.vue";
import PreparationResult from "./PreparationResult.vue";
import PreparationQuerySelect from "./PreparationQuerySelect.vue";
import PreparationHistory from "./PreparationHistory.vue";
import { usePreparationHistory } from "./usePreparationHistory";
const session = useSession();
const op = useOperation();
const sources = usePreparationQueries();
const history = usePreparationHistory();
const name = shallowRef("");
const steps = ref<OperationDraft[]>([newOperation()]);
const localErrors = ref<Fields>({});
const errors = computed(() => ({
  ...preparationErrors(op.fields.value, op.error.value),
  ...localErrors.value,
}));
const result = ref<Entity | null>(null);
const stepColumns = computed(() =>
  steps.value.map((_, i) =>
    operationColumns(sources.columns.value, steps.value.slice(0, i)),
  ),
);
function move(index: number, offset: number) {
  const target = index + offset;
  if (target < 0 || target >= steps.value.length) return;
  const values = [...steps.value];
  [values[index], values[target]] = [values[target]!, values[index]!];
  steps.value = values;
}
async function submit() {
  localErrors.value = {};
  let payload;
  try {
    payload = processingPayload(
      name.value,
      sources.queryId.value,
      steps.value,
      sources.columns.value,
    );
  } catch (error) {
    if (error instanceof PreparationError) localErrors.value = error.fields;
    return;
  }
  await op.run(async () => {
    result.value = await api.post<Entity>("/data-processing-jobs", payload);
  }, "数据处理已完成");
}
onMounted(() => op.run(sources.load));
</script>
<template>
  <div class="page-stack">
    <div class="row-actions" style="justify-content: flex-end">
      <el-button v-if="session.can('m06.read')" @click="history.open()"
        >运行历史</el-button
      >
    </div>
    <section class="panel">
      <el-form
        label-position="top"
        :disabled="op.busy.value || !session.can('m06.write')"
        @submit.prevent="submit"
      >
        <div class="form-grid">
          <el-form-item label="任务名称" :error="errors.name"
            ><el-input v-model="name" /></el-form-item
          ><el-form-item label="数据查询" :error="errors.query_id"
            ><PreparationQuerySelect :catalog="sources" />
          </el-form-item>
        </div>
        <ProcessingOperationForm
          v-for="(step, index) in steps"
          :key="step.key"
          v-model="steps[index]!"
          :columns="stepColumns[index] ?? []"
          :index="index"
          :errors="errors"
          :first="index === 0"
          :last="index === steps.length - 1"
          :removable="steps.length > 1"
          @move="move(index, $event)"
          @remove="steps.splice(index, 1)"
        />
        <el-form-item v-if="errors.operations" :error="errors.operations" />
        <div class="row-actions">
          <el-button
            :disabled="steps.length >= 20"
            @click="steps.push(newOperation())"
            >添加处理步骤</el-button
          ><span class="spacer" /><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            :disabled="
              !sources.queryId.value ||
              sources.selecting.value ||
              sources.loading.value
            "
            >执行处理</el-button
          >
        </div>
      </el-form>
    </section>
    <PreparationResult
      v-if="result"
      :job="result"
      @permission-denied="result = null"
    />
    <PreparationHistory :history="history" />
  </div>
</template>
