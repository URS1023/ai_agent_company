<script setup lang="ts">
/**
 * 单批次检测结果表（只读）。
 * 路由：无独立路由，由 QualityRunPanel 在拿到 QualityBatchResult 后渲染。
 * 依赖接口：无请求；刷新/打开报告/下一批由父组件处理（refresh 不会重发检测）。
 * 关键交互：逐样本展示"原始结论 / 复核后结论 / 报告入口"，未生成报告的样本提示"未完成，请查看运行状态"。
 * 约束：执行成功（status=completed）仅表示流程跑完，样本是否合格以各自报告为准 —— 页面必须保留该说明，
 *   不得用流程状态替代样本结论。running 状态下不提供"开始下一批"。
 */
import { qualityOutcomes } from '../../api/quality'
import { flowStatus } from '../../api/workflows'
import type { QualityBatchResult } from '../../api/qualityRuns'
defineProps<{ result: QualityBatchResult; disabled: boolean }>()
const emit = defineEmits<{ report: [id: string]; refresh: []; next: [] }>()
</script>
<template>
  <section class="qc-run-result" aria-label="本批次检测结果"><h3>{{ flowStatus[result.execution.status] || result.execution.status }}</h3><p>执行编号 {{ result.execution.id }} · 流程发布修订 v{{ result.execution.revision }}</p><p class="wf-note">执行成功仅表示流程完成；各样本是否合格以独立报告为准。刷新和打开报告不会重发检测。</p><p v-if="result.execution.errorMessage" class="wf-error" role="alert">{{ result.execution.errorMessage }}</p>
    <div class="wf-table-wrap" tabindex="0" role="region" aria-label="逐样本报告，可横向滚动"><table class="wf-table"><thead><tr><th>设备标准组</th><th>样本编号</th><th>原始结论</th><th>复核后结论</th><th>报告</th></tr></thead><tbody><tr v-for="sample in result.samples" :key="sample.nodeId + ':' + sample.sampleId"><td>{{ sample.deviceName || sample.nodeId }}<small>{{ sample.templateName }}</small></td><td>{{ sample.sampleId }}</td><td>{{ qualityOutcomes[sample.outcome] || '未生成报告' }}</td><td>{{ sample.effectiveOutcome ? qualityOutcomes[sample.effectiveOutcome] : '—' }}</td><td><button v-if="sample.inspectionId" type="button" :disabled="disabled" @click="emit('report', sample.inspectionId)">查看报告</button><span v-else>未完成，请查看运行状态</span></td></tr></tbody></table></div>
    <div class="wf-actions"><button type="button" :disabled="disabled" @click="emit('refresh')">刷新批次结果</button><button v-if="result.execution.status !== 'running'" type="button" :disabled="disabled" @click="emit('next')">开始下一批</button></div>
  </section>
</template>
