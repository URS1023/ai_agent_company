<script setup lang="ts">
/**
 * 工作流业务结果浏览（预警/质检两域共用，含关键字与时间范围筛选、分页与详情抽屉）。
 * 路由：无独立路由，由 BusinessResultsPanel 在 /alerts、/quality 的结果弹窗内渲染。
 * 依赖接口：GET /api/v1/workflows/business-results 与 .../business-results/{id}（kind 区分域，时间按 ISO 提交）。
 * 关键交互：时间范围先校验"结束晚于开始"再发请求；分页用 pages 栈支持"上一页"回到准确偏移（接口只给 nextOffset，不回退）。
 * 约束：查询结果来自真实执行记录，只读；详情复用 WorkflowReportMarkdown 与 WorkflowValuesPreview 渲染原始值。
 * 加载/取消：单个 AbortController，kind 切换或重新加载会 abort 上一次；卸载时中止。
 * 错误呈现：error 内联展示（含时间范围校验失败）。
 */
import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { get, can } from '../../api/request'
import { formatRecordTime } from '../../utils/dateTime'
import WorkflowReportMarkdown from '../workflows/WorkflowReportMarkdown.vue'
import WorkflowValuesPreview from '../workflows/WorkflowValuesPreview.vue'
const props = defineProps<{ kind: 'quality' | 'alert' }>()
const rows = shallowRef<any[]>([]), selected = shallowRef<any>(null), detailOpen = shallowRef(false)
const keyword = shallowRef(''), start = shallowRef(''), end = shallowRef(''), error = shallowRef('')
const loading = shallowRef(false), offset = shallowRef(0), nextOffset = shallowRef(0), hasMore = shallowRef(false), pages = shallowRef<number[]>([])
let controller: AbortController | null = null
function reset() { offset.value = 0; pages.value = []; void load() }
async function load() {
  controller?.abort(); const active = new AbortController(); controller = active; loading.value = true; error.value = ''
  try {
    if (start.value && end.value && start.value >= end.value) throw new Error('结束时间应晚于开始时间')
    const result = await get('/api/v1/workflows/business-results', { kind: props.kind, keyword: keyword.value, offset: offset.value, limit: 12,
      start: start.value ? new Date(start.value).toISOString() : '', end: end.value ? new Date(end.value).toISOString() : '' }, { signal: active.signal })
    if (active.signal.aborted) return
    rows.value = result.data.items; nextOffset.value = result.data.nextOffset; hasMore.value = result.data.hasMore
  } catch (cause) { if (!active.signal.aborted) error.value = (cause as Error).message }
  finally { if (!active.signal.aborted) loading.value = false }
}
async function open(row: any) {
  error.value = ''; loading.value = true
  try { selected.value = (await get(`/api/v1/workflows/business-results/${row.id}`, { kind: props.kind })).data; detailOpen.value = true }
  catch (cause) { error.value = (cause as Error).message }
  finally { loading.value = false }
}
// 分页用"偏移栈"实现上一页：接口只返回 nextOffset，无法回退，故每次前进前压栈当前偏移。
function next() { pages.value = [...pages.value, offset.value]; offset.value = nextOffset.value; void load() }
function previous() { offset.value = pages.value.at(-1) || 0; pages.value = pages.value.slice(0, -1); void load() }
watch(() => props.kind, reset, { immediate: true })
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <section class="business-results" aria-label="工作流业务结果">
    <header><div><h2>{{ kind === 'quality' ? '质检运行结果' : '设备检查结果' }}</h2><p>查看定时和手动运行已保存的报告。页面和 AI 对话使用同一份记录。</p></div><button type="button" :disabled="loading" @click="load">刷新结果</button></header>
    <form class="business-result-filters" @submit.prevent="reset"><label>工作流名称<input v-model="keyword" placeholder="产品、设备或检查类型" /></label><label>运行开始时间<input v-model="start" type="datetime-local" /></label><label>运行结束时间<input v-model="end" type="datetime-local" /></label><button type="submit" :disabled="loading">筛选结果</button><button type="button" :disabled="loading" @click="keyword = ''; start = ''; end = ''; reset()">全部时间</button></form>
    <p class="result-note">这里筛选报告的运行时间。重新检查某段业务数据时，打开工作流“运行”选择采集开始、结束时间；留空则按上次成功采集后的时间自动取数。</p>
    <p v-if="error" role="alert" class="wf-error">{{ error }}</p><p v-if="loading" role="status">正在读取报告…</p>
    <div v-if="rows.length" class="business-result-list"><button v-for="row in rows" :key="row.id" class="business-result-card" type="button" :disabled="loading" @click="open(row)">
      <span class="result-card-title"><strong>{{ row.workflowName }}</strong><span>{{ formatRecordTime(row.createTime) }}</span></span>
      <span v-if="row.window?.start" class="result-note">采集 {{ row.window.start }} 至 {{ row.window.end }}{{ row.window.hasMore ? ' · 本时间段还有后续批次' : '' }}</span>
      <span class="result-counts"><span>检查对象 <b>{{ row.summary.records ?? '—' }}</b></span><span>检查项 <b>{{ row.summary.items ?? '—' }}</b></span><span>异常 <b>{{ row.summary.issues ?? '—' }}</b></span><span>待确认 <b>{{ row.summary.pending ?? '—' }}</b></span></span>
      <span>{{ row.outcome === 'no_changes' ? '本时段无新增或更新' : row.outcome === 'no_data' ? '本次未读取到符合范围的记录' : '查看报告与逐项结果 →' }}</span>
    </button></div>
    <p v-else-if="!loading && !error">当前筛选范围内没有已完成报告，可调整时间范围查看历史结果。</p>
    <div class="wf-actions"><button :disabled="loading || !pages.length" @click="previous">上一页</button><span>第 {{ pages.length + 1 }} 页</span><button :disabled="loading || !hasMore" @click="next">下一页</button></div>
    <el-dialog v-model="detailOpen" :title="selected?.title || '业务报告'" width="min(1180px, 96vw)" append-to-body destroy-on-close><div v-if="selected" class="workflow-ui business-result-detail">
      <p>{{ formatRecordTime(selected.createTime) }} · 修订 v{{ selected.revision }} · {{ (selected.durationMs / 1000).toFixed(2) }} 秒</p>
      <p v-if="selected.window?.start">采集范围：{{ selected.window.start }} 至 {{ selected.window.end }}（{{ selected.window.timeZone }}）</p><p v-if="selected.window?.hasMore">本时间段尚有后续记录。自动采集会在下一周期继续；手动检查可接着读取本时间段。</p>
      <RouterLink v-if="selected.window?.hasMore && can('workflows.execute')" :to="{ path: '/workflows/' + selected.workflowId + '/run', query: { windowStart: selected.window.start, windowEnd: selected.window.end, windowAfterKey: selected.window.afterKey } }">继续读取本时间段</RouterLink>
      <WorkflowReportMarkdown :content="selected.content || ''" /><WorkflowValuesPreview v-if="selected.data?.length" :output="{ data: selected.data }" /><p><a :href="selected.reportUrl" target="_blank" rel="noopener">查看工作流运行详情</a></p>
    </div></el-dialog>
  </section>
</template>
<style scoped>
.business-results{padding:8px 0 24px;color:#172b4d}.business-results header,.result-card-title{display:flex;align-items:center;justify-content:space-between;gap:16px}.business-results h2{margin:0 0 8px}.business-results header p,.result-note{color:#637187;font-size:13px;line-height:1.7}.business-result-filters{display:flex;flex-wrap:wrap;gap:12px;align-items:end;margin:18px 0}.business-result-filters label{display:grid;gap:6px}.business-result-filters input{padding:9px;border:1px solid #d4dbe6;border-radius:6px}.business-result-list{display:grid;gap:12px;margin:20px 0}.business-result-card{display:grid;gap:14px;padding:18px;text-align:left;background:#fff;border:1px solid #dce3ed;border-radius:10px;color:inherit;cursor:pointer}.business-result-card:hover{border-color:#4875e7;background:#f8fbff}.result-card-title>span{font-size:13px;color:#637187}.result-counts{display:flex;gap:28px;flex-wrap:wrap}.result-counts b{font-size:20px;margin-left:8px}.business-result-detail{line-height:1.75}.business-results button:not(.business-result-card){padding:9px 14px;border:1px solid #cad4e3;border-radius:6px;background:white;cursor:pointer}@media(max-width:700px){.result-card-title{align-items:flex-start;flex-direction:column}.result-counts{gap:14px}}
</style>
