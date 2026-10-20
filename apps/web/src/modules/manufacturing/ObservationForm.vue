<script setup lang="ts">
import { computed, reactive, watch } from "vue";
const props = defineProps<{
  metrics: Record<string, any>[];
  samples: Record<string, any>[];
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [data: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive({
  sample_id: "",
  observations: [] as Record<string, any>[],
});
watch(
  () => props.metrics,
  (metrics) => {
    form.observations = metrics.map((metric) => ({
      metric_id: metric.id,
      value: "",
      unit: metric.unit ?? "",
      image_ref: "",
      defects: [],
      defect_type: "",
      confidence: "1",
      severity: "none",
    }));
  },
  { immediate: true },
);
const rows = computed(() =>
  form.observations.map((observation, index) => ({
    observation,
    metric: props.metrics[index],
  })),
);
function submit() {
  emit("submit", {
    sample_id: form.sample_id,
    observations: form.observations.map((row, index) =>
      props.metrics[index]?.category === "appearance"
        ? {
            metric_id: row.metric_id,
            image_ref: row.image_ref,
            defects:
              row.severity === "none"
                ? []
                : [
                    {
                      defect_type: row.defect_type || "外观缺陷",
                      confidence: row.confidence,
                      severity: row.severity,
                    },
                  ],
          }
        : { metric_id: row.metric_id, value: row.value, unit: row.unit },
    ),
  });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><el-form-item label="样本" :error="errors.sample_id"
      ><el-select v-model="form.sample_id" filterable
        ><el-option
          v-for="sample in samples"
          :key="sample.id"
          :value="sample.id"
          :label="sample.external_ref ?? sample.name ?? sample.id" /></el-select
    ></el-form-item>
    <div
      v-for="{ metric, observation } in rows"
      :key="observation.metric_id"
      class="observation-row"
    >
      <div class="observation-name">{{ metric?.name }}</div>
      <el-input
        v-if="metric?.category !== 'appearance'"
        v-model="observation.value"
        type="number"
        step="any"
        required
        ><template #append>{{ metric?.unit }}</template></el-input
      ><template v-else
        ><el-select v-model="observation.severity"
          ><el-option value="none" label="无缺陷" /><el-option
            value="minor"
            label="轻微缺陷" /><el-option
            value="moderate"
            label="中度缺陷" /><el-option
            value="severe"
            label="严重缺陷" /><el-option
            value="review"
            label="需复核" /></el-select
        ><el-input
          v-model="observation.confidence"
          type="number"
          min="0"
          max="1"
          step=".01"
          placeholder="置信度" /><el-input
          v-model="observation.image_ref"
          placeholder="图片证据引用" /><el-input
          v-if="observation.severity !== 'none'"
          v-model="observation.defect_type"
          placeholder="缺陷类型"
      /></template>
    </div>
    <div
      class="row-actions"
      style="justify-content: flex-end; margin-top: 20px"
    >
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!form.sample_id"
        >保存记录</el-button
      >
    </div></el-form
  >
</template>
<style scoped>
.observation-row {
  display: grid;
  grid-template-columns: 140px 1fr 100px;
  gap: 16px;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.observation-name {
  font-weight: 500;
}
</style>
