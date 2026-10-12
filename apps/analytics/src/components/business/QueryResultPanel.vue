<script setup lang="ts">
/**
 * 查询结果面板（表格 / 图表 / 大屏 / 洞察等多页签展示 + 导出）。
 * 路由：无独立路由；用于智能问数、SQL 工作区与查询历史（结果弹窗）中展示同一份 QueryResult。
 * 依赖接口：api/request.ts 的 downloadQuery（POST /api/v1/analytics/export，blob）；图表与大屏分别复用 SmartChart / DataAnalysisDashboard。
 * 关键交互：复制 SQL（剪贴板失败给出可操作提示）、按格式导出（需 can('analytics.export')）。
 * 约束：统计数值只做精确字符串排版，空值显示 '—'；只展示真实结果，不做前端重算或兜底数据。
 * 错误呈现：导出/复制失败用 ElMessage 提示，不改变已有结果展示。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { can, downloadQuery } from '../../api/request'
import type { QueryResult } from '../../types'
import SmartChart from '../SmartChart.vue'
import DataAnalysisDashboard from '../DataAnalysisDashboard.vue'
import { formatDecimalText as valueText, decimalValueTitle } from '../../utils/decimalDisplay'

const props = defineProps<{ result: QueryResult }>()
const activeTab = ref('table')
const exporting = ref(false)
const enhancedMetrics = computed(() => Array.isArray(props.result.enhancedAnalysis?.metrics)
  ? props.result.enhancedAnalysis.metrics.filter((item: any) => item.trend || item.projection || item.anomaly?.count).slice(0, 4)
  : [])
const recommendations = computed(() => Array.isArray(props.result.enhancedAnalysis?.recommendations)
  ? props.result.enhancedAnalysis.recommendations : [])
const retrieval = computed(() => props.result.retrievalEvidence || {})
const selectedTables = computed(() => Array.isArray(retrieval.value.selectedTables) ? retrieval.value.selectedTables : [])
/** 意图/检索通道中文名映射：键与后端返回的枚举一致，禁止翻译键名（检索证据用于解释"为什么选这些表"）。 */
const intentNames: Record<string, string> = {
  anomaly: '异常识别', prediction: '预测', trend: '趋势', ranking: '排行', comparison: '对比',
  detail: '明细', summary: '汇总', general: '综合查询',
}
const channelNames: Record<string, string> = {
  metadata_comment: '元数据与注释', embedding: '向量语义', declared_relation: '声明关系', explicit_table: '明确指定表',
}
async function copySQL() {
  try { await navigator.clipboard.writeText(props.result.generatedSql); ElMessage.success('SQL 已复制') }
  catch { ElMessage.error('无法访问剪贴板，请手动选择下方 SQL 复制') }
}
async function exportResult(format: string) {
  exporting.value = true
  try { await downloadQuery(props.result.queryId, format) }
  catch (error) { ElMessage.error((error as Error).message) }
  finally { exporting.value = false }
}
</script>

<template>
  <section class="query-result-panel" aria-label="真实分析结果">
    <div class="result-summary">
      <div><strong>{{ result.totalCount }}</strong> 行 · {{ result.columns.length }} 列 · {{ result.executionTime }} ms</div>
      <el-dropdown v-if="can('analytics.export')" @command="exportResult">
        <el-button :loading="exporting">导出结果 <el-icon><ArrowDown /></el-icon></el-button>
        <template #dropdown><el-dropdown-menu>
          <el-dropdown-item command="xlsx">Excel 工作簿</el-dropdown-item>
          <el-dropdown-item command="csv">CSV 数据</el-dropdown-item>
          <el-dropdown-item command="html">HTML 分析报告</el-dropdown-item>
          <el-dropdown-item command="markdown">Markdown 报告</el-dropdown-item>
          <el-dropdown-item command="json">JSON 完整结果</el-dropdown-item>
        </el-dropdown-menu></template>
      </el-dropdown>
    </div>
    <el-alert v-if="result.truncated" type="warning" :closable="false" title="结果已达到行数上限，以下统计仅覆盖返回样本。" show-icon />
    <el-tabs v-model="activeTab">
      <el-tab-pane label="数据结果" name="table">
        <el-table :data="result.data" stripe border max-height="480" empty-text="查询已完成，没有匹配的数据">
          <el-table-column v-for="column in result.columns" :key="column.columnName" :prop="column.columnName" :label="column.comment || column.columnName" min-width="145" show-overflow-tooltip>
            <template #header><span>{{ column.comment || column.columnName }}</span><small v-if="column.comment" class="result-column-name">{{ column.columnName }}</small></template>
          </el-table-column>
        </el-table>
      </el-tab-pane>
      <el-tab-pane label="推荐图表" name="charts" lazy><SmartChart v-if="result.data.length" :data="result.data" :columns="result.columns" /><el-empty v-else description="没有可绘制的数据" /></el-tab-pane>
      <el-tab-pane v-if="result.analysis || result.insights.length || enhancedMetrics.length" label="统计与洞察" name="statistics">
        <p class="preserve-lines">{{ result.analysis }}</p>
        <div v-if="enhancedMetrics.length" class="enhanced-metrics">
          <article v-for="metric in enhancedMetrics" :key="metric.field" class="enhanced-card">
            <strong>{{ metric.label }}</strong><small>{{ metric.field }}</small>
            <dl>
              <template v-if="metric.trend"><dt>趋势</dt><dd :class="['trend-value', metric.trend.direction]">{{ metric.trend.direction === 'up' ? '上升' : metric.trend.direction === 'down' ? '下降' : '持平' }} <span class="exact-stat-value" :title="metric.trend.percentChange == null ? decimalValueTitle(metric.trend.absoluteChange) : decimalValueTitle(metric.trend.percentChange) + '%'">{{ metric.trend.percentChange == null ? valueText(metric.trend.absoluteChange) : valueText(metric.trend.percentChange) + '%' }}</span></dd></template>
              <template v-if="metric.anomaly?.count"><dt>统计离群</dt><dd>{{ metric.anomaly.count }} 项</dd></template>
              <template v-if="metric.projection"><dt>下一观测点</dt><dd><span class="exact-stat-value" :title="decimalValueTitle(metric.projection.value)">{{ valueText(metric.projection.value) }}</span> <el-tag size="small" effect="plain">{{ metric.projection.confidence === 'medium' ? '中等参考' : '低置信参考' }}</el-tag></dd></template>
            </dl>
            <p v-if="metric.projection">{{ metric.projection.caveat }}</p>
          </article>
        </div>
        <div class="insights-list">
          <el-alert v-for="(insight, index) in result.insights" :key="index" :title="insight.title" :description="[insight.description, insight.recommendation].filter(Boolean).join(' ')" :type="insight.severity === 'high' ? 'error' : ['warning', 'medium'].includes(insight.severity) ? 'warning' : 'info'" :closable="false" show-icon />
        </div>
        <ul v-if="recommendations.length" class="analysis-actions"><li v-for="item in recommendations" :key="item.title"><strong>{{ item.title }}</strong><span>{{ item.description }}</span></li></ul>
        <details><summary>逐列统计详情</summary><pre>{{ JSON.stringify(result.statistics.columns, null, 2) }}</pre></details>
      </el-tab-pane>
      <el-tab-pane label="可视化看板" name="dashboard" lazy><DataAnalysisDashboard :data="result.data" :columns="result.columns" :statistics="result.statistics" :truncated="result.truncated" /></el-tab-pane>
      <el-tab-pane label="SQL 与来源" name="source">
        <div class="result-source-actions"><el-tag v-if="result.queryType" size="small">{{ result.queryType }}</el-tag><el-button size="small" @click="copySQL">复制 SQL</el-button></div>
        <dl class="origin-details"><dt>数据源</dt><dd>{{ result.datasourceId }}</dd><dt>查询标识</dt><dd>{{ result.queryId }}</dd><dt>运行标识</dt><dd>{{ result.runId }}</dd></dl>
        <details v-if="selectedTables.length" class="retrieval-details" open>
          <summary>自然语言选表依据</summary>
          <div class="retrieval-summary">
            <el-tag size="small">{{ intentNames[retrieval.intent] || retrieval.intent }}</el-tag>
            <el-tag v-for="domain in retrieval.businessDomains || []" :key="domain" size="small" effect="plain">{{ domain }}</el-tag>
            <span>上下文 {{ retrieval.contextTurns || 0 }} 轮</span>
          </div>
          <ul><li v-for="table in selectedTables" :key="table.tableName"><strong>{{ table.tableName }}</strong><span>{{ table.reason }}</span><small>{{ (table.channels || []).map((item: string) => channelNames[item] || item).join(' · ') }}</small></li></ul>
        </details>
        <pre class="sql-preview">{{ result.generatedSql }}</pre>
      </el-tab-pane>
    </el-tabs>
  </section>
</template>

<style scoped>
.result-column-name { display: block; font-size: 11px; color: #5c6d84; font-weight: 400; }
.result-source-actions { display: flex; align-items: center; justify-content: flex-end; gap: 12px; margin: 8px 0 16px; }
.enhanced-metrics { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 10px; margin: 14px 0; }
.enhanced-card { min-width: 0; border: 1px solid #dce4ef; border-radius: 6px; padding: 13px; background: #fbfcfe; }
.enhanced-card > strong { color: #17263c; font-size: 14px; }
.enhanced-card > small { display: block; color: #7a899d; margin: 3px 0 9px; }
.enhanced-card dl { display: grid; grid-template-columns: auto minmax(0, 1fr); gap: 7px 12px; margin: 0; font-size: 12px; }
.enhanced-card dt { color: #7a899d; }
.enhanced-card dd { margin: 0; color: #2e4057; text-align: right; }
.exact-stat-value { display: inline-block; max-width: 100%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; vertical-align: bottom; }
.enhanced-card p { margin: 9px 0 0; color: #7a899d; font-size: 11px; line-height: 1.5; }
.trend-value.up { color: #247342; }
.trend-value.down { color: #bb293a; }
.analysis-actions { list-style: none; padding: 0; margin: 14px 0; border-top: 1px solid #e3e8ef; }
.analysis-actions li { display: grid; grid-template-columns: minmax(150px, 220px) 1fr; gap: 14px; padding: 11px 0; border-bottom: 1px solid #e8edf4; font-size: 12px; }
.analysis-actions strong { color: #2e4057; }
.analysis-actions span { color: #5c6d84; }
.retrieval-details { margin: 14px 0; border: 1px solid #dce4ef; border-radius: 6px; padding: 12px 14px; }
.retrieval-details summary { color: #2e4057; font-weight: 600; cursor: pointer; }
.retrieval-summary { display: flex; align-items: center; gap: 7px; flex-wrap: wrap; margin: 10px 0; color: #7a899d; font-size: 12px; }
.retrieval-details ul { list-style: none; margin: 0; padding: 0; }
.retrieval-details li { display: grid; grid-template-columns: minmax(150px, 210px) 1fr auto; gap: 12px; padding: 9px 0; border-top: 1px solid #edf1f6; font-size: 12px; }
.retrieval-details li span { color: #5c6d84; }
.retrieval-details li small { color: #7a899d; }
@media (max-width: 680px) {
  .analysis-actions li, .retrieval-details li { grid-template-columns: 1fr; gap: 4px; }
}
</style>
