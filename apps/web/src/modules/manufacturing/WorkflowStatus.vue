<script setup lang="ts">
import { computed } from "vue";
import { workflowLabel } from "./workflowLabels";
const props = defineProps<{ value?: string }>();
const type = computed(() =>
  ["closed", "resolved", "normal"].includes(props.value ?? "")
    ? "success"
    : ["awaiting_review", "awaiting_reinspection", "attention"].includes(
          props.value ?? "",
        )
      ? "warning"
      : "info",
);
</script>
<template>
  <el-tag :type="type" size="small" effect="light">{{
    workflowLabel(value)
  }}</el-tag>
</template>
