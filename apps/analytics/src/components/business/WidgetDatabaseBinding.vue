<script setup lang="ts">
/**
 * 报表/大屏组件的数据库绑定配置（选数据源 → 选表/字段 → 生成只读 SQL → 测试取数）。
 * 路由：无独立路由，由报表设计器的组件属性面板渲染（大屏同源复用）。
 * 依赖接口：api/request.ts 的 post('/api/v1/reports/query')（只读查询，maxRows 固定 1000）；数据源与表结构来自 useSources。
 * 关键交互：切换数据源会清空表/SQL/结果列与映射（旧配置在新源上必然失效）；"测试取数"结果通过 data 事件回传给设计器预览。
 * 约束：SQL 必须只读（后端校验）；标识符按数据源类型加引号（MySQL 反引号、其它双引号）并转义，禁止拼接未处理表名；
 *   测试结果只在"请求代际 + 绑定内容"均未变化时写入（current()），避免旧响应覆盖新配置。
 * 错误呈现：error 内联展示（如"请选择数据源并配置查询"）。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { can, post } from '../../api/request'
import { useSources } from '../../composables/useSources'
import { widgetResultFields } from '../../utils/metricData'
const props = defineProps<{ widget: Record<string, any> }>()
const emit = defineEmits<{ patch: [path: string, value: unknown]; data: [rows: unknown[]] }>()
const { sources, tables, sourceId, loading, error, loadSources, loadTables } = useSources()
const fields = ref<string[]>([])
const executing = ref(false)
const testedRows = ref<number | null>(null)
let testSequence = 0
const bindingKey = () => JSON.stringify([props.widget.id, props.widget.config?.datasourceId, props.widget.config?.sql, props.widget.config?.parameters])
const selectedTable = computed(() => tables.value.find(item => item.tableName === props.widget.config?.tableName))
const source = computed(() => sources.value.find(item => item.id === sourceId.value))
const resultFields = computed(() => widgetResultFields(props.widget.config))
function patch(key: string, value: unknown) { emit('patch', 'config.' + key, value) }
/** 标识符引用：MySQL 用反引号、其它库用双引号，并对内部引号做加倍转义（防注入/歧义）。 */
function quote(name: string) {
  const mark = source.value?.type === 'mysql' ? String.fromCharCode(96) : '"'
  return mark + name.replaceAll(mark, mark + mark) + mark
}
function chooseSource(event: Event) {
  patch('datasourceId', (event.target as HTMLSelectElement).value)
  patch('tableName', ''); patch('sql', ''); patch('resultColumns', []); patch('dataMapping', {}); emit('data', []); fields.value = []; testedRows.value = null
}
function chooseTable(event: Event) {
  patch('tableName', (event.target as HTMLSelectElement).value)
  fields.value = []; testedRows.value = null
}
function useFields() {
  if (!selectedTable.value || !fields.value.length) { error.value = '请选择表和需要查询的字段'; return }
  patch('sql', 'SELECT ' + fields.value.map(quote).join(', ') + ' FROM ' + quote(selectedTable.value.tableName) + ' LIMIT 1000')
}
/** 测试取数：POST /api/v1/reports/query（只读，maxRows=1000）；结果仅在绑定未变时回传，避免竞态。 */
async function test() {
  if (!props.widget.config?.datasourceId || !props.widget.config?.sql) { error.value = '请选择数据源并配置查询'; return }
  executing.value = true; testedRows.value = null; error.value = ''
  const request = ++testSequence
  const requestedBinding = bindingKey()
  const current = () => request === testSequence && requestedBinding === bindingKey()
  try {
    const result = (await post('/api/v1/reports/query', {
      datasourceId: String(props.widget.config.datasourceId), query: props.widget.config.sql,
      parameters: props.widget.config.parameters || {}, mode: 'sql', maxRows: 1000,
    })).data
    if (!current()) return
    emit('data', result.data)
    patch('resultColumns', result.columns.map((column: Record<string, string>) => column.columnName))
    patch('queryId', result.queryId); patch('verifiedAt', new Date().toISOString())
    testedRows.value = result.totalCount
  } catch (cause) { if (current()) error.value = (cause as Error).message }
  finally { if (request === testSequence) executing.value = false }
}
watch(() => props.widget.id, () => { testSequence++; executing.value = false; testedRows.value = null; fields.value = []; error.value = '' })
watch(() => props.widget.config?.datasourceId, async id => {
  sourceId.value = String(id || ''); fields.value = []; testedRows.value = null
  if (can('datasources.view')) await loadTables()
}, { immediate: true })
onMounted(() => { if (can('datasources.view')) void loadSources() })
</script>

<template>
  <section class="widget-binding" aria-label="组件数据库数据绑定">
    <p v-if="error" role="alert" class="binding-error">{{ error }}</p>
    <label>数据源<select :value="widget.config?.datasourceId || ''" :disabled="loading" @change="chooseSource">
      <option value="">请选择已授权数据源</option><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.name }} · {{ item.type }}</option>
    </select></label>
    <label>数据表<select :value="widget.config?.tableName || ''" :disabled="loading || !sourceId" @change="chooseTable">
      <option value="">请选择表（也可直接编写 SQL）</option><option v-for="table in tables" :key="table.tableName" :value="table.tableName">{{ table.tableComment || table.tableName }}</option>
    </select></label>
    <fieldset v-if="selectedTable"><legend>查询字段</legend>
      <label v-for="column in selectedTable.columns" :key="column.columnName" class="field-choice"><input v-model="fields" type="checkbox" :value="column.columnName" /><span>{{ column.columnName }} <small>{{ column.comment }}</small></span></label>
      <button type="button" @click="fields = selectedTable.columns.map(item => item.columnName)">全选字段</button>
      <button type="button" @click="useFields">生成只读 SQL</button>
    </fieldset>
    <label>只读查询<textarea :value="widget.config?.sql || ''" rows="6" spellcheck="false" @change="patch('sql', ($event.target as HTMLTextAreaElement).value)" /></label>
    <button v-if="can('dashboards.execute')" type="button" :disabled="executing" @click="test">{{ executing ? '正在试运行…' : '试运行并更新组件数据' }}</button>
    <p v-if="testedRows !== null" role="status">已返回 {{ testedRows }} 行，查询和运行记录已保存。</p>
    <template v-if="resultFields.length">
      <p v-if="widget.component === 'JStatsSummary' && resultFields.length > 1 && !widget.config?.dataMapping?.valueField" role="status">此查询返回多个字段，请选择需要展示的指标。</p>
      <label>维度字段<select :value="widget.config?.dataMapping?.labelField || ''" @change="patch('dataMapping.labelField', ($event.target as HTMLSelectElement).value)">
        <option value="">按返回结果识别</option><option v-for="field in resultFields" :key="field" :value="field">{{ field }}</option>
      </select></label>
      <label>数值字段<select :value="widget.config?.dataMapping?.valueField || ''" @change="patch('dataMapping.valueField', ($event.target as HTMLSelectElement).value)">
        <option value="">按返回结果识别</option><option v-for="field in resultFields" :key="field" :value="field">{{ field }}</option>
      </select></label>
    </template>
    <p class="binding-note">查询只读；原始文本不强制转数值。金额字段可在 SQL 中按实际精度转换。只读查看者仅能运行已保存的绑定。</p>
  </section>
</template>

<style scoped>
.widget-binding { display:grid;gap:12px;font-size:12px;color:#d5e1f0; }
label { display:grid;gap:6px; }
input,select,textarea,button { font:inherit;color:#ecf3fc;background:#152235;border:1px solid #637b97;border-radius:4px;min-width:0;max-width:100%;padding:7px; }
select,textarea { width:100%; }textarea { resize:vertical; }
button { cursor:pointer; }button:disabled { opacity:.6;cursor:not-allowed; }
fieldset { margin:0;padding:10px;border:1px solid #516178;max-height:260px;overflow:auto; }
.field-choice { display:flex;align-items:start;gap:8px;margin-bottom:8px; }
.field-choice small { display:block;color:#b1c4db; }fieldset button { margin-top:6px; }
.binding-error { color:#ffc3b9;background:#512b2b;padding:8px;overflow-wrap:anywhere; }
.binding-note { color:#b4c5d9;line-height:1.8; }
:focus-visible { outline:2px solid #80c5ff;outline-offset:2px; }
</style>
