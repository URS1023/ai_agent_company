<script setup lang="ts">
/**
 * WorkflowLogs —— 工作流「日志」页，路由 `/workflows/:id/logs`
 * （设备侧 `/alerts|/quality/devices/:deviceId/workflow/logs`）。
 *
 * 职责：按状态/时间/关键词分页查询真实运行记录，点击某行派发 open 事件交给父级打开结果详情。
 * 依赖接口：`api/workflows.workflows.runs(...)`（多取 1 条判断是否还有下一页）、
 * `workflows.result(workflowId, executionId)`（用于从 URL 直达某次执行）、`utils/dateTime.formatRecordTime`。
 * 关键约束：
 * - 只读页面：不触发运行、重试或取消，全部动作都是查询；
 * - 关键词搜索必须防抖 300ms，并在输入时立即 abort 上一请求，否则会按输入序列发起大量查询；
 * - 用 `controller === active` 判定接受最新响应，防止旧页数据覆盖新筛选结果；
 * - 组件卸载时中止请求并清理定时器。
 */
import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { flowStatus, workflows, type FlowRun, type Workflow } from '../../api/workflows'
import { formatRecordTime } from '../../utils/dateTime'

const props = defineProps<{ flow: Workflow }>()
const emit = defineEmits<{ open: [run: FlowRun] }>()
const route = useRoute()
const rows = shallowRef<FlowRun[]>([])
const loading = shallowRef(false)
const error = shallowRef('')
const keyword = shallowRef('')
const status = shallowRef('all')
const period = shallowRef('7')
const page = shallowRef(1)
const pageSize = 20
const hasMore = shallowRef(false)
let controller: AbortController | null = null
let searchTimer: ReturnType<typeof setTimeout> | undefined
/** 耗时按量级切换精度：10s 以上只保留 1 位小数，避免列表里出现无意义的长小数。 */
function formatDuration(value: number) {
  if (value < 1000) return `${value} ms`
  return `${(value / 1000).toFixed(value < 10_000 ? 2 : 1)} s`
}
function changePage(next: number) { page.value = next; void load() }
/** 筛选条件变化后回到第 1 页再查询，否则会停留在旧页码上出现空列表。 */
function resetSearch() { clearTimeout(searchTimer); page.value = 1; void load() }
/**
 * 拉取当前页运行记录。多取 1 条（pageSize + 1）仅用于判断 hasMore，展示时截断回 pageSize，
 * 因为后端未提供总数；请求失败只写 error，不自动重试（避免放大后端压力）。
 */
async function load() {
  if (!props.flow.id) return
  controller?.abort()
  const active = new AbortController()
  controller = active
  loading.value = true
  error.value = ''
  try {
    const response = await workflows.runs(props.flow.id, (page.value - 1) * pageSize, pageSize + 1, active.signal,
      { keyword: keyword.value.trim(), status: status.value, days: Number(period.value) })
    if (!active.signal.aborted && controller === active) {
      hasMore.value = response.data.length > pageSize
      rows.value = response.data.slice(0, pageSize)
    }
  } catch (cause) {
    if (!active.signal.aborted && controller === active) error.value = (cause as Error).message
  } finally {
    if (!active.signal.aborted && controller === active) loading.value = false
  }
}

watch([() => props.flow.id, status, period], resetSearch, { immediate: true })
watch(keyword, () => {
  controller?.abort()
  loading.value = true
  clearTimeout(searchTimer)
  searchTimer = setTimeout(resetSearch, 300)
})
onBeforeUnmount(() => { controller?.abort(); clearTimeout(searchTimer) })
watch(() => [props.flow.id, route.query.execution], async ([workflowId, executionId]) => {
  if (!workflowId || typeof executionId !== 'string') return
  try { emit('open', (await workflows.result(String(workflowId), executionId)).data) }
  catch (cause) { error.value = (cause as Error).message }
}, { immediate: true })
</script>

<template>
  <main class="wf-workspace-surface wf-logs-page" aria-labelledby="wf-logs-title">
    <header class="wf-workspace-heading">
      <div><h1 id="wf-logs-title">日志</h1><p>查看工作流的真实运行记录、状态、耗时与失败原因。</p></div>
      <button type="button" :disabled="loading" @click="load"><i class="fa-solid fa-rotate-right" aria-hidden="true" />刷新</button>
    </header>
    <form class="wf-log-filters" @submit.prevent="resetSearch">
      <label><span>状态</span><select v-model="status"><option value="all">全部状态</option><option value="completed">执行成功</option><option value="failed">执行失败</option><option value="active">运行中 / 等待中</option><option value="cancelled">已取消</option></select></label>
      <label><span>时间</span><select v-model="period"><option value="1">今天</option><option value="7">最近 7 天</option><option value="30">最近 30 天</option><option value="0">全部时间</option></select></label>
      <label class="wf-log-search"><span class="wf-visually-hidden">搜索日志</span><i class="fa-solid fa-magnifying-glass" aria-hidden="true" /><input v-model="keyword" type="search" maxlength="200" placeholder="搜索运行 ID、修订或错误" /></label>
    </form>

    <p v-if="error" class="wf-error" role="alert">{{ error }} <button type="button" @click="load">重试</button></p>
    <section class="wf-log-list" :aria-busy="loading">
      <div v-if="loading" class="wf-workspace-loading" role="status"><span /><strong>正在加载运行日志</strong></div>
      <button v-for="item in rows" v-else-if="!error" :key="item.id" type="button" class="wf-log-row" @click="emit('open', item)">
        <span class="wf-log-status" :class="`is-${item.status}`"><i class="fa-solid" :class="item.status === 'completed' ? 'fa-circle-check' : item.status === 'failed' ? 'fa-circle-xmark' : item.status === 'cancelled' ? 'fa-ban' : 'fa-circle-notch'" aria-hidden="true" />{{ flowStatus[item.status] || item.status }}</span>
        <span class="wf-log-time"><strong>{{ formatRecordTime(item.createTime) }}</strong><small>{{ item.runId }}</small></span>
        <span><small>运行修订</small><strong>v{{ item.revision }}</strong></span>
        <span><small>耗时</small><strong>{{ formatDuration(item.durationMs) }}</strong></span>
        <span class="wf-log-message"><small>结果</small><strong>{{ item.errorMessage || '运行记录已保存' }}</strong></span>
        <i class="fa-solid fa-chevron-right" aria-hidden="true" />
      </button>
      <div v-if="!loading && !error && !rows.length" class="wf-workspace-empty"><i class="fa-regular fa-folder-open" aria-hidden="true" /><strong>没有符合条件的日志</strong><p>可调整状态、时间或搜索条件；测试运行或正式调用后，日志会保存在这里。</p></div>
    </section>
    <footer v-if="!error && (rows.length || page > 1)" class="wf-log-pagination"><span>本页 {{ rows.length }} 条</span><div><button type="button" :disabled="loading || page <= 1" @click="changePage(page - 1)">上一页</button><span>第 {{ page }} 页</span><button type="button" :disabled="loading || !hasMore" @click="changePage(page + 1)">下一页</button></div></footer>
  </main>
</template>
