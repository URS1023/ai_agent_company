<script setup lang="ts">
import { computed, shallowRef, watch } from "vue";
import StatusTag from "../../components/common/StatusTag.vue";
import {
  defectSeverity,
  inspectionDemoImage,
  observationText,
  type InspectionObservationData,
} from "./inspectionPresentation";
import {
  measurementText,
  originalMeasurement,
} from "./measurementPresentation";

const props = withDefaults(
  defineProps<{
    observation: InspectionObservationData;
    name?: string;
    showName?: boolean;
    showImageReference?: boolean;
    preservePrecision?: boolean;
  }>(),
  { showName: true, showImageReference: true, preservePrecision: false },
);
const originalValue = computed(() =>
  originalMeasurement(props.observation.value),
);
const summary = computed(() =>
  props.observation.value == null || props.preservePrecision
    ? observationText(props.observation)
    : measurementText(props.observation.value),
);
const defects = computed(() => props.observation.defects ?? []);
const demoImage = computed(() =>
  inspectionDemoImage(props.observation.image_ref),
);
const previewVisible = shallowRef(false);
watch(
  () => props.observation,
  () => {
    previewVisible.value = false;
  },
);
const hasDetails = computed(
  () =>
    !!(
      originalValue.value !== null ||
      props.observation.reason ||
      props.observation.note ||
      defects.value.length ||
      (props.showImageReference && props.observation.image_ref)
    ),
);
</script>

<template>
  <div class="inspection-observation">
    <div class="observation-summary">
      <span v-if="showName" class="observation-title"
        >{{ name ?? observation.name ?? "检测项目" }}：</span
      >
      <span class="observation-value"
        >{{ summary
        }}<template v-if="observation.unit">
          {{ observation.unit }}</template
        ></span
      >
      <StatusTag v-if="observation.outcome" :value="observation.outcome" />
      <el-button
        v-if="demoImage"
        link
        type="primary"
        @click="previewVisible = true"
        >查看演示图</el-button
      >
    </div>
    <details v-if="hasDetails" class="observation-details">
      <summary class="observation-toggle">查看详情</summary>
      <div v-if="originalValue !== null" class="observation-source">
        <span class="source-label">实测值</span>
        <span class="observation-original"
          >{{ originalValue
          }}<template v-if="observation.unit">
            {{ observation.unit }}</template
          ></span
        >
      </div>
      <p v-if="observation.reason" class="observation-reason">
        {{ observation.reason }}
      </p>
      <el-table
        v-if="defects.length"
        :data="defects"
        size="small"
        max-height="280"
        class="defect-table"
      >
        <el-table-column prop="defect_type" label="缺陷类型" min-width="140" />
        <el-table-column label="严重度" width="100">
          <template #default="{ row }"
            ><span :class="['defect-severity', row.severity]">{{
              defectSeverity(row.severity)
            }}</span></template
          >
        </el-table-column>
        <el-table-column prop="confidence" label="置信度" min-width="140" />
      </el-table>
      <p v-if="observation.note" class="observation-note">
        {{ observation.note }}
      </p>
      <div
        v-if="showImageReference && observation.image_ref"
        class="observation-source"
      >
        <span class="source-label">图片来源</span
        ><span>{{ observation.image_ref }}</span>
      </div>
    </details>
    <el-dialog
      v-if="demoImage"
      v-model="previewVisible"
      title="演示图"
      width="760px"
      append-to-body
      destroy-on-close
    >
      <img
        v-if="previewVisible"
        :src="demoImage"
        alt="合成质检示意图"
        class="inspection-demo-image"
      />
    </el-dialog>
  </div>
</template>

<style scoped>
.inspection-observation + .inspection-observation {
  margin-top: var(--sp-3);
}
.observation-summary {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: var(--sp-2);
}
.observation-title {
  color: var(--text-strong);
}
.observation-details {
  margin-top: var(--sp-1);
}
.observation-toggle {
  color: var(--brand-600);
  cursor: pointer;
  width: fit-content;
}
.observation-toggle:focus-visible {
  outline: 2px solid var(--brand-500);
  outline-offset: 3px;
}
.observation-reason,
.observation-note {
  margin: var(--sp-2) 0;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.defect-table {
  margin-top: var(--sp-2);
}
.defect-severity.severe {
  color: var(--tone-rose);
}
.defect-severity.moderate,
.defect-severity.review {
  color: var(--tone-amber);
}
.observation-source {
  display: flex;
  flex-wrap: wrap;
  gap: var(--sp-2);
  margin-top: var(--sp-2);
  overflow-wrap: anywhere;
}
.source-label {
  color: var(--text-muted);
}
.inspection-demo-image {
  display: block;
  width: 100%;
  max-height: 70vh;
  object-fit: contain;
}
</style>
