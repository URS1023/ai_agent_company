<script setup lang="ts">
import { computed } from "vue";
const props = defineProps<{ value?: string }>();
const labels: Record<string, string> = {
  unknown: "待核对",
  cancel_requested: "停止请求已提交",
  pending_approval: "待批准",
  blocked: "已暂停",
  candidate: "待确认",
  attention: "需关注",
  normal: "正常",
  queued: "排队中",
  waiting_review: "待复核",
  succeeded: "已完成",
  awaiting_review: "待复核",
  reviewed: "已复核",
  incomplete: "证据不足",
  insufficient: "数据不足",
  degrading: "出现劣化",
  stable: "稳定",
  published: "已发布",
  acknowledged: "已受理",
  draft: "草稿",
  ready: "待执行",
  pending: "待处理",
  running: "执行中",
  completed: "已完成",
  passed: "合格",
  failed: "不合格",
  review: "待复核",
  needs_review: "待复核",
  approved: "已批准",
  rejected: "已驳回",
  cancelled: "已取消",
  open: "待处置",
  processing: "处置中",
  resolved: "已解决",
  closed: "已关闭",
  healthy: "正常",
  error: "异常",
  high: "高风险",
  medium: "需关注",
  low: "正常",
  insufficient_data: "数据不足",
  enabled: "已启用",
  disabled: "已停用",
  active: "已启用",
  inactive: "已停用",
};
const text = computed(() => labels[props.value ?? ""] ?? props.value ?? "—");
</script>
<template>
  <span :class="['status-chip', value]">{{ text }}</span>
</template>
