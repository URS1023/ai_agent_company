<script setup lang="ts">
/**
 * 预警报告详情（只读展示，嵌在预警工作台的报告抽屉/弹窗内）。
 * 路由：无独立路由，由 AlertWorkspace / AlertWorkbench 在打开报告时渲染。
 * 依赖接口：数据由父组件传入（api/alerts.ts 的 alerts.get('reports') + alerts.list('problems')），本组件不发请求。
 * 关键交互：查看来源工作流、下载报告（需 can('alerts.export')）、问题翻页（page 事件，每页 30 条）、
 *   实际数据本地分页（每页 30 行，切换报告时重置页码）、展开/收起追溯快照与逐项结果。
 * 约束：executionStatus !== 'completed' 时必须提示"工作流未成功完成"；outcome === 'no_data' 必须提示"不能据此认定设备正常"，
 *   这两条是业务合规要求，不允许隐藏或改写。
 */
import { computed, shallowRef, watch } from 'vue'
import { can } from '../../api/request'
import { outcomeNames, type AlertRecord } from '../../api/alerts'
import { flowStatus } from '../../api/workflows'
import { formatRecordTime } from '../../utils/dateTime'
import AlertTable from './AlertTable.vue'
import WriteReceipts from '../writeback/WriteReceipts.vue'
import '../../styles/writeback.css'
const props = defineProps<{ report: AlertRecord; problems: AlertRecord[]; offset: number; busy: boolean }>()
defineEmits<{ page: [offset: number]; handle: [row: AlertRecord]; export: []; workflow: [id: string] }>()
// 切换报告时重置本地分页与展开态，避免把上一份报告的数据页码带到新报告。
const dataPage = shallowRef(0), showRaw = shallowRef(false)
watch(() => props.report.id, () => { dataPage.value = 0; showRaw.value = false })
const rows = computed(() => props.report.result.data.slice(dataPage.value * 30, (dataPage.value + 1) * 30))
</script>
<template>
  <section class="alert-report-detail"><h2>{{ report.title }}</h2><dl class="alert-facts"><div><dt>设备</dt><dd>{{ report.deviceName }}</dd></div><div><dt>固定规则修订</dt><dd>v{{ report.ruleRevision }}</dd></div><div><dt>判定结果</dt><dd>{{ outcomeNames[report.outcome] }}</dd></div><div><dt>工作流状态</dt><dd>{{ flowStatus[report.executionStatus] }}</dd></div><div><dt>参与记录 / 原始问题</dt><dd>{{ report.summary.rows }} / {{ report.summary.issues }}</dd></div><div><dt>生成时间</dt><dd>{{ formatRecordTime(report.createTime) }}</dd></div></dl>
    <p v-if="report.executionStatus !== 'completed'" class="wf-error" role="status">整个工作流尚未成功完成；以下为此节点已保存的实际判定。{{ report.executionError }}</p><p v-if="report.outcome === 'no_data'" class="wf-note">没有取得可判定记录，不能据此认定设备正常。</p>
    <div class="wf-actions"><button type="button" :disabled="busy" @click="$emit('workflow', report.workflowId)">查看来源工作流</button><button type="button" v-if="can('alerts.export')" :disabled="busy" @click="$emit('export')">下载报告</button></div>
    <WriteReceipts :items="report.writeOperations || []" />
    <h3>问题与处置</h3><AlertTable :rows="problems" section="problems" permission="alerts" report-context :busy="busy" @handle="$emit('handle', $event)" /><div class="wf-actions"><button type="button" :disabled="busy || !offset" @click="$emit('page', offset - 30)">上一页问题</button><button type="button" :disabled="busy || problems.length < 30" @click="$emit('page', offset + 30)">下一页问题</button></div>
    <h3 id="alert-actual-data-title">实际数据</h3><div class="wf-table-wrap" tabindex="0" role="region" aria-labelledby="alert-actual-data-title" data-testid="alert-actual-data"><table class="wf-table"><thead><tr><th v-for="column in report.result.columns" :key="column.columnName">{{ column.columnName }}</th></tr></thead><tbody><tr v-for="(row, index) in rows" :key="index"><td v-for="column in report.result.columns" :key="column.columnName">{{ row[column.columnName] == null ? '—' : typeof row[column.columnName] === 'object' ? JSON.stringify(row[column.columnName]) : row[column.columnName] }}</td></tr></tbody></table></div><div class="wf-actions"><button type="button" :disabled="!dataPage" @click="dataPage--">上一页数据</button><span>共 {{ report.result.data.length }} 行</span><button type="button" :disabled="(dataPage + 1) * 30 >= report.result.data.length" @click="dataPage++">下一页数据</button></div>
    <button type="button" class="wf-link" @click="showRaw = !showRaw">{{ showRaw ? '收起' : '展开' }}追溯快照与逐项结果</button><div v-if="showRaw"><p>执行标识：{{ report.executionId }} · 节点：{{ report.nodeId }}</p><pre tabindex="0" aria-label="追溯快照与逐项结果">{{ JSON.stringify({ snapshot: report.snapshot, assessments: report.result.assessments }, null, 2) }}</pre></div>
  </section>
</template>
