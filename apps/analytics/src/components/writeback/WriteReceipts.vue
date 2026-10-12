<script setup lang="ts">
/**
 * 数据写入凭据展示（只读）。
 * 路由：无独立路由；被预警报告（AlertReportView）、质检报告（QualityReport）与工作流运行结果复用。
 * 依赖接口：数据由父组件传入（WriteOperation[]，来自 api/writeback.ts 的 operations 或运行结果），本组件无请求。
 * 关键交互：无交互，仅展示目标表/版本、计划行数、确认提交行数与错误信息，并可展开快照 JSON。
 * 约束：状态文案必须用 writeStatus 映射（uncertain 表示提交结果不确定，不能显示为成功或失败）；
 *   无凭据时整块不渲染（v-if="items.length"），避免给用户"没有写入"的错误印象。
 */
import { writeStatus, type WriteOperation } from '../../api/writeback'
defineProps<{ items: WriteOperation[] }>()
</script>
<template>
  <section v-if="items.length" class="write-receipts" aria-label="实际数据库写入凭据"><h3>实际数据库写入</h3><article v-for="item in items" :key="item.id"><strong>{{ item.snapshot.target.name }} · {{ item.snapshot.target.tableName }} · {{ writeStatus[item.status] }}</strong><p>目标 v{{ item.snapshot.target.version }} · 计划 {{ item.requestedRows }} 行 · 确认提交 {{ item.affectedRows === null ? '未知' : item.affectedRows }} 行</p><p v-if="item.errorMessage" class="wf-error">{{ item.errorMessage }}</p><small>凭据 {{ item.id }} · 节点 {{ item.nodeId }}</small><details><summary>查看目标字段与数据摘要</summary><pre tabindex="0" aria-label="写入映射与数据摘要">{{ JSON.stringify(item.snapshot, null, 2) }}</pre></details></article></section>
</template>
