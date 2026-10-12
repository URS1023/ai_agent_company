<script setup lang="ts">
/**
 * HTTP 数据源管理（路由 /datasources?type=http）。
 * 职责：列表与前端筛选、新建/编辑配置、移除、手动采集、采集历史与历史结果回看。
 * 依赖接口：api/collection.ts 的 httpCollection（list/save/remove/collect/history/result）。
 * 关键交互（安全约束）：采集前必须二次确认并显示"方法 + 地址 + 请求次数 + 配置版本"，
 *   POST 额外提示可能产生业务副作用；采集不会自动重试，离开页面也不会撤回已发出的请求；
 *   移除数据源会保留历史结果与运行记录（提示文案已明确）。
 * 权限：新建/编辑/删除/采集分别对应 datasources.create / edit / delete / execute。
 * 错误呈现：error（列表与操作）与 historyError（历史/结果）分开展示；删除确认被取消不算错误。
 */
import { computed, onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { can } from '../../api/request'
import { collectionStatus, httpCollection, httpRequestCount, type CollectionHistory, type CollectionResult, type HttpSource, type HttpSourceInput } from '../../api/collection'
import HttpSourceForm from './HttpSourceForm.vue'
import HttpCollectionResult from './HttpCollectionResult.vue'
import '../../styles/http-collection.css'
const sources = ref<HttpSource[]>([])
const loading = shallowRef(false)
const error = shallowRef('')
const keyword = shallowRef('')
const offset = shallowRef(0)
const total = shallowRef(0), hasMore = shallowRef(false)
const busy = shallowRef(false)
const formOpen = shallowRef(false)
const formError = shallowRef('')
const editing = shallowRef<HttpSource | null>(null)
const result = shallowRef<CollectionResult | null>(null)
const resultOpen = shallowRef(false)
const historyOpen = shallowRef(false)
const historySource = shallowRef<HttpSource | null>(null)
const history = ref<CollectionHistory[]>([])
const historyOffset = shallowRef(0)
const historyTotal = shallowRef(0), historyHasMore = shallowRef(false)
const historyError = shallowRef('')
let controller: AbortController | null = null
let stopped = false
const filtered = computed(() => sources.value.filter(source => [source.name, source.url, source.description].join(' ').toLowerCase().includes(keyword.value.trim().toLowerCase())))
async function load() {
  controller?.abort(); controller = new AbortController()
  const active = controller
  loading.value = true; error.value = ''
  try { const response = await httpCollection.list(offset.value, active.signal); if (active === controller && !stopped) { sources.value = response.data; total.value = response.total; hasMore.value = response.hasMore; offset.value = response.offset } }
  catch (cause) { if (!active.signal.aborted && !stopped) error.value = (cause as Error).message }
  finally { if (active === controller && !stopped) loading.value = false }
}
function edit(source: HttpSource | null) { editing.value = source; formError.value = ''; formOpen.value = true }
async function save(payload: HttpSourceInput) {
  busy.value = true; formError.value = ''
  try { await httpCollection.save(payload, editing.value?.id); formOpen.value = false; ElMessage.success('HTTP 数据源已保存，尚未执行请求'); await load() }
  catch (cause) { formError.value = (cause as Error).message }
  finally { busy.value = false }
}
async function remove(source: HttpSource) {
  busy.value = true
  try {
    await ElMessageBox.confirm('移除“' + source.name + '”？现有报表将无法继续采集；历史结果与运行记录保留。', '移除 HTTP 数据源', { type: 'warning', confirmButtonText: '确认移除', cancelButtonText: '取消' })
    await httpCollection.remove(source); ElMessage.success('已移除配置并保留历史'); await load()
  } catch (cause) { if (cause instanceof Error) error.value = cause.message }
  finally { busy.value = false }
}
/** 采集执行：先确认（含请求次数与版本），再调用 collect；响应缺少 outcomes 视为异常（提示查看运行记录）。 */
async function collect(source: HttpSource) {
  busy.value = true; error.value = ''
  try {
    await ElMessageBox.confirm(source.method + ' ' + source.url + '，共 ' + httpRequestCount(source) + ' 次请求，配置 v' + source.version + '。' + (source.method === 'POST' ? 'POST 可能产生业务副作用，请确认该接口和请求体用于本次采集。' : '将读取此接口的当前数据。'), '确认执行采集', { type: 'warning', confirmButtonText: '执行已保存配置', cancelButtonText: '取消' })
    result.value = null
    const response = await httpCollection.collect(source)
    if (!response.data?.outcomes) throw new Error(response.message || '采集未返回有效结果，请检查运行记录')
    result.value = response.data; resultOpen.value = true
  } catch (cause) { if (cause instanceof Error) error.value = cause.message }
  finally { busy.value = false }
}
async function showHistory(source?: HttpSource) {
  if (source) { historySource.value = source; historyOffset.value = 0; history.value = [] }
  if (!historySource.value) return
  historyOpen.value = true; historyError.value = ''; busy.value = true
  try { const response = await httpCollection.history(historySource.value, historyOffset.value); if (stopped) return; history.value = response.data; historyTotal.value = response.total; historyHasMore.value = response.hasMore; historyOffset.value = response.offset }
  catch (cause) { historyError.value = (cause as Error).message }
  finally { busy.value = false }
}
async function showResult(id: string) {
  busy.value = true; historyError.value = ''
  try { result.value = (await httpCollection.result(id)).data; resultOpen.value = true }
  catch (cause) { historyError.value = (cause as Error).message }
  finally { busy.value = false }
}
function page(delta: number) { offset.value += delta; void load() }
onMounted(load)
onBeforeUnmount(() => { stopped = true; controller?.abort() })
</script>

<template>
  <section class="http-manager" aria-label="HTTP 数据源管理">
    <div class="http-section-header"><div><h2>HTTP 数据源</h2><p>配置一次，在报表中复用。单条与批量采集共用字段口径，并保留逐条执行结果。</p></div><button v-if="can('datasources.create')" type="button" class="http-primary" :disabled="busy" @click="edit(null)">新建 HTTP 数据源</button></div>
    <div class="http-toolbar"><label>本页筛选<input v-model="keyword" placeholder="名称、地址或用途" /></label><button type="button" :disabled="loading || busy" @click="load">刷新列表</button></div>
    <p v-if="error" role="alert" class="http-error">{{ error }}</p><p v-if="loading" role="status">正在加载 HTTP 数据源…</p>
    <div class="http-table-wrap"><table class="http-table http-source-table"><thead><tr><th>数据源</th><th>请求配置</th><th>字段提取</th><th>使用范围</th><th>操作</th></tr></thead><tbody>
      <tr v-for="source in filtered" :key="source.id"><td><strong>{{ source.name }}</strong><small>{{ source.description || '未填写用途说明' }}</small><small>版本 {{ source.version }} · {{ source.enabled ? '启用' : '停用' }}</small></td>
        <td>{{ source.method }} · {{ httpRequestCount(source) }} 次请求<small>{{ source.url }}</small></td><td><template v-if="source.responseFormat === 'text'">网页 / 文本<small>原文、内容类型和页面地址</small></template><template v-else><code>{{ source.recordPath }}</code><small>{{ source.fields.length ? source.fields.length + ' 个已选字段' : '保留全部顶层字段' }}</small></template></td>
        <td>{{ source.allowAutomatic ? '允许自动只读采集' : '仅手动采集' }}<small>{{ source.allowPrivate ? '已授权此内网目标' : '仅公网' }}</small></td>
        <td><div class="http-row-actions"><button v-if="can('datasources.execute')" type="button" :disabled="busy || !source.enabled" @click="collect(source)">采集</button><button type="button" :disabled="busy" @click="showHistory(source)">历史</button><button v-if="can('datasources.edit')" type="button" :disabled="busy" @click="edit(source)">编辑</button><button v-if="can('datasources.delete')" type="button" :disabled="busy" @click="remove(source)">移除</button></div></td>
      </tr>
      <tr v-if="!filtered.length && !loading"><td colspan="5" class="http-empty">{{ keyword ? '本页没有匹配项，请更换关键词或翻页。' : '暂无 HTTP 数据源。新建配置后，可以选择字段并执行采集。' }}</td></tr>
    </tbody></table></div>
    <div class="http-pagination"><span>共 {{ total }} 项 · 第 {{ offset / 50 + 1 }} 页 · 本页 {{ sources.length }} 项</span><button type="button" :disabled="loading || busy || offset === 0" @click="page(-50)">上一页</button><button type="button" :disabled="loading || busy || !hasMore" @click="page(50)">下一页</button></div>
    <p v-if="busy && !formOpen" role="status" class="http-note">操作进行中。采集不会自动重试；离开页面也不会撤回已发出的 HTTP 请求。</p>
    <el-dialog v-model="formOpen" class="http-config-dialog" :title="editing ? '编辑 HTTP 数据源' : '新建 HTTP 数据源'" width="min(960px, 96vw)" top="3vh" destroy-on-close :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy">
      <HttpSourceForm v-if="formOpen" :key="editing?.id || 'new'" :source="editing" :busy="busy" :error="formError" @save="save" @cancel="formOpen = false" />
    </el-dialog>
    <el-dialog v-model="historyOpen" :title="'采集历史 · ' + (historySource?.name || '')" width="min(900px, 96vw)">
      <p v-if="historyError" class="http-error" role="alert">{{ historyError }}</p><p v-if="busy" role="status">正在读取…</p>
      <div class="http-table-wrap"><table class="http-table"><thead><tr><th>时间（UTC）</th><th>状态</th><th>结果行数</th><th>耗时</th><th>操作</th></tr></thead><tbody><tr v-for="item in history" :key="item.id"><td>{{ item.createTime }}</td><td>{{ collectionStatus[item.status] || item.status }}</td><td>{{ item.resultCount }}</td><td>{{ item.executionTime }} ms</td><td><button type="button" :disabled="busy" @click="showResult(item.id)">查看结果</button></td></tr><tr v-if="!history.length && !busy"><td colspan="5">暂无采集记录。</td></tr></tbody></table></div>
      <div class="http-pagination"><button type="button" :disabled="busy || historyOffset === 0" @click="historyOffset -= 50; showHistory()">上一页</button><span>共 {{ historyTotal }} 项 · 第 {{ historyOffset / 50 + 1 }} 页</span><button type="button" :disabled="busy || !historyHasMore" @click="historyOffset += 50; showHistory()">下一页</button></div>
    </el-dialog>
    <el-dialog v-model="resultOpen" title="HTTP 采集结果" width="min(1120px, 96vw)" destroy-on-close><HttpCollectionResult v-if="result" :key="result.queryId" :result="result" /></el-dialog>
  </section>
</template>
