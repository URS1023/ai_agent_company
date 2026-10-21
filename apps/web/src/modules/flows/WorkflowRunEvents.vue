<script setup lang="ts">
import { computed } from "vue";
import { useWorkflowEvents } from "./useWorkflowEvents";
import { eventRows } from "./workflowEvents";
import WorkflowJsonValue from "./WorkflowJsonValue.vue";
const props = defineProps<{
  runId: string;
  nodes: { id: string; data: Record<string, any> }[];
}>();
const emit = defineEmits<{ denied: [message: string] }>();
const events = useWorkflowEvents(
  () => props.runId,
  (message) => emit("denied", message),
);
const rows = computed(() => eventRows(events.items.value, props.nodes));
function time(value: string) {
  return new Date(value).toLocaleTimeString("zh-CN", { hour12: false });
}
</script>
<template>
  <section class="run-events">
    <div class="toolbar">
      <strong>执行记录</strong><span class="spacer" /><el-button
        :loading="events.loading.value"
        @click="events.load"
        >{{ events.hasMore.value ? "加载更多" : "刷新事件" }}</el-button
      >
    </div>
    <el-alert
      v-if="events.error.value"
      :title="events.error.value"
      type="error"
      :closable="false"
    /><el-table
      v-loading="events.loading.value"
      :data="rows"
      :row-class-name="
        ({ row }: { row: { facts: Record<string, unknown> } }) =>
          Object.keys(row.facts).length ? '' : 'no-event-facts'
      "
      row-key="cursor"
      empty-text="暂无执行事件"
      ><el-table-column
        v-if="rows.some((row) => Object.keys(row.facts).length)"
        type="expand"
        ><template #default="{ row }"
          ><WorkflowJsonValue
            v-if="Object.keys(row.facts).length"
            :value="row.facts" /></template></el-table-column
      ><el-table-column
        prop="nodeName"
        label="节点"
        min-width="160"
      /><el-table-column label="次数" width="80"
        ><template #default="{ row }">{{
          row.attempt ?? "—"
        }}</template></el-table-column
      ><el-table-column prop="label" label="进度" width="120" /><el-table-column
        label="时间"
        width="120"
        ><template #default="{ row }">{{
          time(row.created_at)
        }}</template></el-table-column
      ></el-table
    >
  </section>
</template>
<style scoped>
.run-events {
  display: grid;
  gap: var(--sp-3);
}
.run-events :deep(.el-table__expanded-cell) {
  padding: var(--sp-4);
}
.run-events :deep(.no-event-facts .el-table__expand-icon) {
  display: none;
}
</style>
