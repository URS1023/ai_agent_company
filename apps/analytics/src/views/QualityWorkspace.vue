<script setup lang="ts">
/**
 * 数据质量检查页面（路由 /analytics/quality，属于数据分析布局的子路由）。
 * 职责：对已接入数据源的表做样本检查（缺失/重复/离群），展示完整性摘要与问题清单，并支持导出检查结果。
 * 关键交互：选源 → 选表 → 选检查字段（留空=全部可访问字段）→ 设置样本上限 → 开始检查；
 *   "开始检查"需 can('analytics.execute')，"导出检查结果"需 can('analytics.export')。
 * 加载/取消：用 querySequence 作请求代际，切换数据源/改参数时作废在途请求并清空结果（避免结果与参数不一致）。
 * 只读约束：检查过程不修改源数据（页面文案已明确说明）；本页与"质量检查"业务模块（质检产品/流程）不同，后者见 /quality。
 * 错误呈现：error 同时来自 useSources（数据源/表加载）与本页检查请求，统一用 el-alert 展示。
 */
import { computed, onMounted, onScopeDispose, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { can, downloadQuery, post } from '../api/request'
import { useSources } from '../composables/useSources'

type QualityResult = {
  queryId: string
  tableName: string
  scope: string
  sampleSize: number
  truncated: boolean
  analysis: string
  statistics: {
    completeness?: number | null
    missingCells?: number
    duplicateRows?: number
    columns?: Record<string, unknown>
  }
  issues: Array<{ type: string; severity: string; title: string; description: string; recommendation?: string }>
}

const { sources, tables, sourceId, loading, error, loadSources, loadTables } = useSources({ rememberSelection: true })
const tableName = ref('')
const fields = ref<string[]>([])
const sampleSize = ref(1000)
const executing = ref(false)
const exporting = ref(false)
const result = ref<QualityResult | null>(null)
let querySequence = 0
let controller: AbortController | null = null
const table = computed(() => tables.value.find(item => item.tableName === tableName.value))
const source = computed(() => sources.value.find(item => item.id === sourceId.value))
const sourceDescription = computed(() => source.value?.description || source.value?.autoDescription || '')
const completeness = computed(() => {
  const value = result.value?.statistics?.completeness
  return value == null ? '—' : `${(Number(value) * 100).toFixed(1)}%`
})
/**
 * 执行数据质量检查：POST /api/v1/analytics/quality-check。
 * 关键字段：datasourceId、tableName、columns（空数组=检查全部可访问字段）、maxRows（样本上限）。
 * 约束：需 can('analytics.execute')；未选数据源/表时直接给出中文提示且不发请求；
 *   结果只在"本次请求仍是最新"（current === querySequence）时写入，避免旧响应覆盖新参数的结果。
 * 失败表现：错误写入 error，结果区保持为空。
 */
async function run() {
  if (executing.value || !can('analytics.execute')) return
  if (!sourceId.value || !tableName.value) { error.value = '请选择数据源和数据表'; return }
  const current = ++querySequence
  controller = new AbortController()
  executing.value = true; error.value = ''; result.value = null
  try {
    const response = await post('/api/v1/analytics/quality-check', { datasourceId: sourceId.value, tableName: tableName.value, columns: fields.value, maxRows: sampleSize.value }, { signal: controller.signal })
    if (current === querySequence) result.value = response.data
  }
  catch (caught) { if (current === querySequence) error.value = (caught as Error).message }
  finally { if (current === querySequence) { executing.value = false; controller = null } }
}
/** 作废当前检查：自增代际并中断在途请求，同时清空结果（切换数据源或修改参数时调用，保证界面与参数一致）。 */
function invalidateResult() { ++querySequence; controller?.abort(); controller = null; executing.value = false; result.value = null }
// 切换数据源是同步事件：必须立刻清空表/字段与结果，再拉取新数据源的表清单（flush: 'sync' 防止中间态被渲染）。
watch(sourceId, () => { tableName.value = ''; fields.value = []; invalidateResult(); void loadTables() }, { flush: 'sync' })
watch([tableName, fields, sampleSize], invalidateResult)
/**
 * 导出检查结果：按格式（xlsx/csv/html/markdown/json）下载该次查询的结果文件。
 * 依赖：使用 result.queryId 作为导出标识（历史查询体系复用），需 can('analytics.export') 才显示入口。
 */
async function exportResult(format: string) {
  if (!result.value?.queryId) return
  exporting.value = true
  try { await downloadQuery(result.value.queryId, format) }
  catch (caught) { ElMessage.error((caught as Error).message) }
  finally { exporting.value = false }
}
onMounted(loadSources)
onScopeDispose(invalidateResult)
</script>
<template>
  <section class="business-panel" aria-label="数据质量检查">
    <div class="panel-heading"><div><h2>数据质量检查</h2><p>检查已接入数据的缺失、重复与离群情况，不修改源数据。产品外观与成分检测属于“质量检查”业务模块。</p></div></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-form label-position="top" class="quality-form" v-loading="loading">
      <el-form-item label="数据源"><el-select v-model="sourceId" placeholder="选择数据源" popper-class="quality-source-popper"><el-option v-for="item in sources" :key="item.id" :value="item.id" :label="item.name"><div class="quality-source-option"><strong>{{ item.name }}</strong><span>{{ item.description || item.autoDescription || '暂无业务说明' }}</span></div></el-option></el-select><p v-if="sourceDescription" class="quality-source-context">{{ sourceDescription }}</p></el-form-item>
      <el-form-item label="数据表"><el-select v-model="tableName" placeholder="选择数据表" @change="fields = []"><el-option v-for="item in tables" :key="item.tableName" :value="item.tableName" :label="item.tableComment || item.tableName" /></el-select></el-form-item>
      <el-form-item label="检查字段"><el-select v-model="fields" multiple collapse-tags placeholder="留空检查全部可访问字段"><el-option v-for="column in table?.columns || []" :key="column.columnName" :value="column.columnName" :label="column.comment || column.columnName" /></el-select></el-form-item>
      <el-form-item label="样本行数上限"><el-input-number v-model="sampleSize" :min="1" :max="10000" /></el-form-item>
    </el-form>
    <el-button v-if="can('analytics.execute')" type="primary" :loading="executing" :disabled="!sourceId || !tableName" @click="run">开始检查</el-button>
    <div v-if="result" class="quality-results">
      <div class="quality-result-heading"><el-alert :title="'已检查 ' + result.sampleSize + ' 行 · ' + (result.truncated ? '限量样本' : '本次查询完整结果')" :type="result.truncated ? 'warning' : 'success'" :closable="false" show-icon /><el-dropdown v-if="can('analytics.export')" @command="exportResult"><el-button :loading="exporting">导出检查结果</el-button><template #dropdown><el-dropdown-menu><el-dropdown-item command="xlsx">Excel 工作簿</el-dropdown-item><el-dropdown-item command="csv">CSV 数据</el-dropdown-item><el-dropdown-item command="html">HTML 分析报告</el-dropdown-item><el-dropdown-item command="markdown">Markdown 报告</el-dropdown-item><el-dropdown-item command="json">JSON 完整结果</el-dropdown-item></el-dropdown-menu></template></el-dropdown></div>
      <div class="quality-summary" aria-label="质量检查摘要"><article><small>完整性</small><strong>{{ completeness }}</strong></article><article><small>缺失单元格</small><strong>{{ result.statistics.missingCells || 0 }}</strong></article><article><small>所选字段重复行</small><strong>{{ result.statistics.duplicateRows || 0 }}</strong></article><article><small>发现问题</small><strong>{{ result.issues.length }}</strong></article></div>
      <p class="preserve-lines">{{ result.analysis }}</p>
      <el-table :data="result.issues" empty-text="本次样本未发现这些类型的问题，并不代表通过全部业务质检">
        <el-table-column prop="title" label="问题" min-width="210" /><el-table-column prop="description" label="检查结果" min-width="260" /><el-table-column prop="recommendation" label="建议" min-width="320" />
      </el-table>
    </div>
  </section>
</template>

<style scoped>
.quality-source-context { width: 100%; margin: 6px 0 0; color: #53657d; font-size: 12px; line-height: 1.5; }
.quality-source-option { display: grid; min-width: 0; padding: 4px 0; line-height: 1.35; }
.quality-source-option strong { overflow: hidden; color: #21354e; font-size: 13px; text-overflow: ellipsis; }
.quality-source-option span { overflow: hidden; color: #53657d; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.quality-result-heading { display: grid; grid-template-columns: minmax(0, 1fr) auto; align-items: center; gap: 12px; }
.quality-result-heading :deep(.el-alert) { margin: 0; }
.quality-result-heading :deep(.el-button:hover), .quality-result-heading :deep(.el-button:focus) { color: #195bad; }
.quality-summary { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 10px; margin: 14px 0; }
.quality-summary article { min-width: 0; padding: 12px 14px; border: 1px solid #dce4ef; border-radius: 6px; background: #fbfcfe; }
.quality-summary small { display: block; color: #53657d; font-size: 11px; }
.quality-summary strong { display: block; margin-top: 5px; color: #173b5e; font: 600 21px/1.2 Consolas, 'Microsoft YaHei UI', sans-serif; }
:global(.quality-source-popper .el-select-dropdown__item) { height: auto; min-height: 46px; padding-top: 3px; padding-bottom: 3px; }
@media (max-width: 720px) {
  .quality-result-heading { grid-template-columns: 1fr; }
  .quality-result-heading :deep(.el-dropdown), .quality-result-heading :deep(.el-button) { width: 100%; }
  .quality-summary { grid-template-columns: repeat(2, minmax(0, 1fr)); }
}
</style>
