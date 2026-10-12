<script setup lang="ts">
/**
 * 质检报告（只读原文 + 人工复核表单 + 复核历史），嵌在质检记录详情弹窗中。
 * 路由：无独立路由，由质检工作台（QualityWorkbench）在打开记录时渲染；也用于运行结果跳转过来的报告页。
 * 依赖接口：数据由父组件传入（quality.get('inspections') / qualityRuns.result），本组件不发请求；
 *   复核提交由父组件调用 quality.review（带 version），导出由父组件调用 inspections/{id}/export。
 * 关键交互：同时展示"原始判定"与"复核后结论"（两栏并列，原始证据不可被复核篡改）；
 *   复核表单仅在 can('quality.review') 且 report.status === 'active' 时出现，必须选择指标、结论并填写复核依据；
 *   报告版本变化（version）时重置复核表单草稿，避免把上一次的内容提交到新版本。
 * 约束：按钮与表单受 disabled（父组件的 busy 状态）统一禁用，防止并发复核。
 */
import { shallowRef, watch } from 'vue'
import { can } from '../../api/request'
import { qualityOutcomes, type QualityRecord } from '../../api/quality'
import QualityImageEvidence from './QualityImageEvidence.vue'
import WriteReceipts from '../writeback/WriteReceipts.vue'
const props = defineProps<{ report: QualityRecord; disabled: boolean }>()
defineEmits<{ review: [payload: object]; export: []; workflow: []; batch: [] }>()
const metricId = shallowRef(''), decision = shallowRef('retest'), note = shallowRef('')
// 报告版本变化（复核成功或重新拉取）后清空复核草稿，避免旧输入被误提交到新版本。
watch(() => props.report.version, () => { metricId.value = ''; note.value = ''; decision.value = 'retest' })
</script>
<template>
  <article class="qc-report"><header class="wf-heading"><div><h2>{{ report.sampleId }}</h2><p>{{ report.snapshot.device.name }} · {{ report.snapshot.template.name }} · 固定修订 v{{ report.snapshot.templateRevision }}</p></div><div class="wf-actions"><button v-if="report.fromQualityForm" type="button" :disabled="disabled" @click="$emit('batch')">查看所属检测批次</button><button type="button" v-if="can('quality.export')" :disabled="disabled" @click="$emit('export')">下载质检报告</button></div></header><WriteReceipts :items="report.writeOperations || []" /><div class="qc-verdicts"><section><span>原始判定</span><strong :class="'qc-' + report.outcome">{{ qualityOutcomes[report.outcome] }}</strong><small>检测证据与原始结论不随复核修改</small></section><section><span>复核后结论</span><strong :class="'qc-' + report.effectiveOutcome">{{ qualityOutcomes[report.effectiveOutcome] }}</strong><small>依据下方可追溯的人工复核记录</small></section></div><div class="wf-table-wrap" role="region" aria-label="质检逐项原始结果" tabindex="0"><table class="wf-table"><thead><tr><th>指标</th><th>原始判断</th><th>实际值与理由</th><th>来源</th></tr></thead><tbody><tr v-for="item in report.results" :key="item.metricId"><td>{{ item.name }}</td><td>{{ qualityOutcomes[item.outcome] }}</td><td>{{ item.reason }}<details v-if="item.observation"><summary>实测 / 缺陷证据</summary><pre tabindex="0">{{ JSON.stringify(item.observation, null, 2) }}</pre></details></td><td>{{ item.provenance.mode === 'detector' ? '检测服务' : item.provenance.mode === 'manual' ? '人工录入' : '未检测' }}<small v-if="item.provenance.detectionId">{{ item.provenance.detectionId }}</small></td></tr></tbody></table></div><QualityImageEvidence :report="report" /><form v-if="can('quality.review') && report.status === 'active'" class="qc-review-form" @submit.prevent="$emit('review', { metricId, decision, note })"><h3>逐项人工复核</h3><fieldset :disabled="disabled" class="qc-form-grid"><label>复核指标<select v-model="metricId" required><option value="">请选择指标</option><option v-for="item in report.results" :key="item.metricId" :value="item.metricId">{{ item.name }}</option></select></label><label>复核结论<select v-model="decision"><option value="retest">要求复检</option><option value="passed">人工判定合格</option><option value="failed">人工判定不合格</option></select></label><label class="qc-span">复核依据<textarea v-model="note" rows="3" maxlength="3000" required placeholder="填写实际复核依据与证据，不会覆盖原始结果" /></label></fieldset><button type="submit" class="wf-primary" :disabled="disabled || !metricId || !note.trim()">保存复核</button></form><h3>复核历史</h3><ol class="qc-history"><li v-for="item in report.reviews" :key="item.id"><strong>{{ report.results.find((row: QualityRecord) => row.metricId === item.metricId)?.name }} · {{ qualityOutcomes[item.decision] }}</strong><p>{{ item.note }}</p><small>{{ item.createTime }} · 操作人 {{ item.actorId }} · v{{ item.version }}</small></li></ol><p v-if="!report.reviews.length" class="wf-note">尚无人工复核记录。</p><details><summary>模板标准、设备快照与运行来源</summary><pre tabindex="0">{{ JSON.stringify({ inspectionId: report.id, executionId: report.executionId, nodeId: report.nodeId, createdAt: report.createTime, snapshot: report.snapshot }, null, 2) }}</pre></details></article>
</template>
