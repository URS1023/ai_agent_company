<script setup lang="ts">
/**
 * 报表/大屏组件的 HTTP 采集源绑定配置（选择允许自动调用的采集源并测试取数）。
 * 路由：无独立路由，由报表设计器的组件属性面板渲染。
 * 依赖接口：api/collection.ts 的 httpCollection.list（分页拉全量后本地筛选）、POST /api/v1/reports/http-query。
 * 关键交互：只列出 enabled 且 allowAutomatic 的采集源（未经自动调用授权的源不出现，避免设计器触发未授权请求）；
 *   切换数据源会清空结果列与映射；测试结果用"请求代际 + 组件 id + 所选源"三重校验后再写入。
 * 错误呈现：error 内联展示；busy 期间禁用测试按钮。
 */
import { computed, onMounted, shallowRef, watch } from 'vue'
import { widgetResultFields } from '../../utils/metricData'
import { can, post } from '../../api/request'
import { httpCollection, httpRequestCount, type HttpSource } from '../../api/collection'
const props = defineProps<{ widget: Record<string, any> }>()
const emit = defineEmits<{ patch: [path: string, value: unknown]; data: [rows: unknown[]] }>()
const sources = shallowRef<HttpSource[]>([])
const error = shallowRef('')
const busy = shallowRef(false)
const rows = shallowRef<number | null>(null)
let testSequence = 0
const source = computed(() => sources.value.find(item => item.id === props.widget.config?.datasourceId))
const resultFields = computed(() => widgetResultFields(props.widget.config))
function patch(path: string, value: unknown) { emit('patch', 'config.' + path, value) }
function select(event: Event) {
  rows.value = null
  patch('datasourceId', (event.target as HTMLSelectElement).value)
  patch('resultColumns', []); patch('dataMapping', {}); emit('data', [])
}
// 分页拉全量后本地筛选：只有"启用 + 允许自动调用"的源才能出现在设计器里（未授权自动调用不得被静默触发）。
async function load() {
  try {
    const found: HttpSource[] = []
    for (let offset = 0; ; offset += 50) {
      const batch = (await httpCollection.list(offset)).data; found.push(...batch)
      if (batch.length < 50) break
    }
    sources.value = found.filter(item => item.enabled && item.allowAutomatic)
  } catch (cause) { error.value = (cause as Error).message }
}
/** 测试取数：POST /api/v1/reports/http-query；结果仅在组件与所选源未变时回传（避免切组件后误写入）。 */
async function test() {
  const selectedId = props.widget.config?.datasourceId
  const widgetId = props.widget.id
  const request = ++testSequence
  const current = () => request === testSequence && widgetId === props.widget.id && selectedId === props.widget.config?.datasourceId
  busy.value = true; error.value = ''; rows.value = null
  try {
    const result = (await post('/api/v1/reports/http-query', { datasourceId: selectedId })).data
    if (!current()) return
    emit('data', result.data); patch('resultColumns', result.columns.map((column: { columnName: string }) => column.columnName))
    patch('queryId', result.queryId); patch('verifiedAt', new Date().toISOString()); rows.value = result.totalCount
  } catch (cause) { if (current()) { emit('data', []); error.value = (cause as Error).message } }
  finally { if (request === testSequence) busy.value = false }
}
watch(() => props.widget.id, () => { testSequence++; busy.value = false; rows.value = null; error.value = '' })
onMounted(() => { if (can('datasources.view')) void load() })
</script>
<template>
  <section class="http-widget-binding" aria-label="组件 HTTP 数据绑定">
    <p v-if="error" role="alert" class="binding-error">{{ error }}</p>
    <label>HTTP 数据源<select :value="widget.config?.datasourceId || ''" :disabled="busy" @change="select"><option value="">请选择已授权的 HTTP 数据源</option><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.name }} · {{ item.method }}</option></select></label>
    <p v-if="source">{{ source.method }} · {{ httpRequestCount(source) }} 次请求 · {{ source.responseFormat === 'text' ? '网页 / 文本' : (source.fields.length || '全部') + ' 个字段' }}<br />记录路径：{{ source.recordPath }}</p>
    <button v-if="can('dashboards.execute')" type="button" :disabled="busy || !source" @click="test">{{ busy ? '正在采集…' : '试运行并更新组件数据' }}</button>
    <p v-if="rows !== null" role="status">已返回 {{ rows }} 行，来源与配置版本已记录。</p>
    <template v-if="resultFields.length"><label>维度字段<select :value="widget.config?.dataMapping?.labelField || ''" @change="patch('dataMapping.labelField', ($event.target as HTMLSelectElement).value)"><option value="">按返回结果识别</option><option v-for="field in resultFields" :key="field" :value="field">{{ field }}</option></select></label>
      <label>数值字段<select :value="widget.config?.dataMapping?.valueField || ''" @change="patch('dataMapping.valueField', ($event.target as HTMLSelectElement).value)"><option value="">按返回结果识别</option><option v-for="field in resultFields" :key="field" :value="field">{{ field }}</option></select></label></template>
    <p>仅列出启用且允许自动只读采集的源。请在“数据分析 → 数据源 → HTTP 接口”维护请求体、JSONPath 和凭据；不在组件中保存明文密钥。</p>
  </section>
</template>
<style scoped>
.http-widget-binding { display:grid;gap:12px;font-size:12px;line-height:1.8;color:#d5e1f0; }.http-widget-binding label { display:grid;gap:6px; }.http-widget-binding select,.http-widget-binding button { font:inherit;color:#ecf3fc;background:#152235;border:1px solid #637b97;border-radius:4px;min-width:0;max-width:100%;padding:8px; }.http-widget-binding select { width:100%; }.http-widget-binding button { cursor:pointer; }.http-widget-binding button:disabled { opacity:.6;cursor:not-allowed; }.binding-error { color:#ffc3b9;background:#512b2b;padding:8px;overflow-wrap:anywhere; }.http-widget-binding :focus-visible { outline:2px solid #80c5ff;outline-offset:2px; }
</style>
