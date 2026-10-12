<script setup lang="ts">
/**
 * 查询历史页面（路由 /analytics/history，属于数据分析布局的子路由）。
 * 职责：分页展示历史查询（问题/SQL、状态、行数、耗时、失败原因），按需查看已保存结果，或复用查询回到问数/SQL 页继续分析。
 * 加载/取消：列表与详情各自用递增 sequence 作请求代际，过期响应直接丢弃（页面销毁时自增以作废在途请求）。
 * 关键交互：查看结果需 status='success'（按钮禁用），失败记录只允许复用；复用会带上 datasourceId 与原查询内容。
 * 错误呈现：顶部 el-alert 展示 error；弹窗内结果面板自行处理渲染失败。
 * 备注：请求 limit=21、展示 20 条，多取一条用于判断是否还有下一页。
 */
import { onMounted, onScopeDispose, ref } from 'vue'
import { useRouter } from 'vue-router'
import { get } from '../api/request'
import type { Envelope, History, QueryResult } from '../types'
import QueryResultPanel from '../components/business/QueryResultPanel.vue'
const router = useRouter()
const items = ref<History[]>([])
const loading = ref(false)
const error = ref('')
const selected = ref<QueryResult | null>(null)
const dialogVisible = ref(false)
const page = ref(1)
const hasNext = ref(false)
const openingId = ref('')
let loadSequence = 0
let detailSequence = 0
/** 加载本页历史（limit=21、展示 20 条）；只有最后一次请求的结果会被写入，避免翻页时旧响应覆盖新页。 */
async function load() {
  const current = ++loadSequence
  loading.value = true; error.value = ''
  try {
    const rows = (await get<Envelope<History[]>>('/api/v1/analytics/history/recent', { limit: 21, offset: (page.value - 1) * 20 })).data
    if (current !== loadSequence) return
    items.value = rows.slice(0, 20)
    hasNext.value = rows.length > 20
  }
  catch (caught) { if (current === loadSequence) error.value = (caught as Error).message }
  finally { if (current === loadSequence) loading.value = false }
}
/** 打开某条记录的已保存结果（GET /api/v1/analytics/history/{id}）并在弹窗中展示；openingId 用于给该行按钮单独加 loading。 */
async function open(item: History) {
  const current = ++detailSequence
  openingId.value = item.id
  error.value = ''
  try {
    const result = (await get<Envelope<QueryResult>>('/api/v1/analytics/history/' + item.id)).data
    if (current !== detailSequence) return
    selected.value = result; dialogVisible.value = true
  }
  catch (caught) { if (current === detailSequence) error.value = (caught as Error).message }
  finally { if (current === detailSequence) openingId.value = '' }
}
/**
 * 复用历史查询：SQL 模式（或已生成 SQL）跳转到 SQL 工作区，
 *   成功记录带 historyId 由 SQL 页回放参数，失败记录则用原 SQL 文本回填；问数模式回到智能问数页并带原问题。
 */
function reuse(item: History) {
  if (item.queryMode === 'sql' || item.generatedSql) {
    router.push({ path: '/analytics/sql', query: item.status === 'success'
      ? { datasourceId: item.datasourceId, historyId: item.id }
      : { datasourceId: item.datasourceId, sql: item.generatedSql || item.queryText } })
  } else router.push({ path: '/analytics', query: { datasourceId: item.datasourceId, query: item.queryText } })
}
function localTime(value: string) {
  if (!value) return '—'
  const date = new Date(/[zZ]$|[+-]\d{2}:?\d{2}$/.test(value) ? value : value + 'Z')
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false })
}
onMounted(load)
onScopeDispose(() => { ++loadSequence; ++detailSequence })
</script>
<template>
  <section class="business-panel" aria-label="查询历史">
    <div class="panel-heading"><div><h2>查询历史</h2><p>查看已保存的结果，或复用查询继续分析。</p></div><el-button @click="page = 1; load()" :loading="loading">刷新</el-button></div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-table :data="items" v-loading="loading" stripe empty-text="还没有查询记录">
      <el-table-column prop="queryText" label="问题 / SQL" min-width="320" show-overflow-tooltip />
      <el-table-column label="方式" width="95"><template #default="{ row }">{{ row.queryMode === 'sql' ? 'SQL' : '智能问数' }}</template></el-table-column>
      <el-table-column label="状态" width="100"><template #default="{ row }"><el-tag :type="row.status === 'success' ? 'success' : row.status === 'failed' ? 'danger' : 'info'">{{ row.status === 'success' ? '已完成' : row.status === 'failed' ? '失败' : '执行中' }}</el-tag></template></el-table-column>
      <el-table-column prop="resultCount" label="返回行数" width="100" />
      <el-table-column prop="executionTime" label="耗时 ms" width="105" />
      <el-table-column label="执行时间" min-width="180"><template #default="{ row }">{{ localTime(row.createTime) }}</template></el-table-column>
      <el-table-column label="失败原因" min-width="220" show-overflow-tooltip><template #default="{ row }">{{ row.errorMessage || '—' }}</template></el-table-column>
      <el-table-column label="操作" width="180" fixed="right"><template #default="{ row }">
        <el-button link type="primary" :loading="openingId === row.id" :disabled="row.status !== 'success'" @click="open(row)">结果</el-button>
        <el-button link type="primary" @click="reuse(row)">{{ row.queryMode === 'sql' || row.generatedSql ? '复用 SQL' : '修改问题' }}</el-button>
      </template></el-table-column>
    </el-table>
    <div class="list-pagination"><el-button :disabled="loading || page <= 1" @click="page--; load()">上一页</el-button><span>第 {{ page }} 页</span><el-button :disabled="loading || !hasNext" @click="page++; load()">下一页</el-button></div>
    <el-dialog v-model="dialogVisible" title="已保存的分析结果" width="min(1200px, 96vw)" destroy-on-close><QueryResultPanel v-if="selected" :result="selected" /></el-dialog>
  </section>
</template>
