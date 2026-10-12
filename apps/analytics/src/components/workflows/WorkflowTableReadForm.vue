<script setup lang="ts">
/**
 * WorkflowTableReadForm —— 读取值节点的"直连数据表"配置（选表、选字段、预览、过滤/匹配子表单）。
 *
 * 位置：WorkflowReadValuesForm 的 table 模式分支。
 * 依赖接口：`api/request.post('/api/v1/workflows/value-preview')`（真实取数预览）、
 * `composables/useWorkflowTableMetadata`（表与列元数据）、WorkflowTableFilters / WorkflowTableMatching。
 * 关键约束：
 * - 预览是真实数据库查询（只读）：必须在配置变化时立即中止上一次请求并清空结果，
 *   防止用户看到与当前配置不一致的旧数据；组件卸载时也要中止；
 * - 切换数据表必须重置字段/过滤/别名/匹配等全部下游配置，避免残留旧表字段；
 * - 字段上限 100（selectVisible 截断），与后端限制保持一致；
 * - invalidFields 表示"已选字段在当前表中不存在"（表结构变更后会出现），提供一键清理入口；
 * - 未选表时不允许回退到默认表：预览按钮与取数都必须以显式选择的表为前提。
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { post } from '../../api/request'
import { useWorkflowTableMetadata, type ReadTable } from '../../composables/useWorkflowTableMetadata'
import WorkflowTableFilters from './WorkflowTableFilters.vue'
import WorkflowTableMatching from './WorkflowTableMatching.vue'
const props = defineProps<{ config: Record<string, any>; upstreamFields: string[]; device?: Record<string, any> }>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
const sourceId = computed(() => props.config.datasourceId || '')
const { tables, loading, error, reload } = useWorkflowTableMetadata(sourceId)
const tableSearch = shallowRef(''), fieldSearch = shallowRef(''), choosing = shallowRef(false)
const selected = computed(() => tables.value.find(t => t.tableName === props.config.tableName))
const columns = computed(() => selected.value?.columns || [])
const selectedFields = computed<string[]>(() => props.config.fields || [])
const filteredTables = computed(() => { const q = tableSearch.value.trim().toLocaleLowerCase(); return tables.value.filter(t => `${t.tableName} ${t.tableComment}`.toLocaleLowerCase().includes(q)) })
const filteredColumns = computed(() => { const q = fieldSearch.value.trim().toLocaleLowerCase(); return columns.value.filter(c => `${c.columnName} ${c.comment}`.toLocaleLowerCase().includes(q)) })
/** 已选字段在当前表中已不存在的项（表结构变更后出现），需提示并可一键清理。 */
const invalidFields = computed(() => selectedFields.value.filter(name => !columns.value.some(c => c.columnName === name)))
const preview = shallowRef<any>(null), previewError = shallowRef(''), previewing = shallowRef(false)
let previewController: AbortController | undefined
/** 是否依赖上游输入：用于提示用户该读取会被输入字段驱动（matchMode 或 input 来源的过滤条件）。 */
const usesInput = computed(() => (props.config.matchMode && props.config.matchMode !== 'off') || (props.config.filters || []).some((f: any) => f.valueSource === 'input' && !['is_null', 'not_null'].includes(f.operator)))
function patch(values: Record<string, any>) { emit('update', { ...props.config, ...values }) }
function selectTable(table: ReadTable) { patch({ tableName: table.tableName, fields: [], filters: [], fieldAliases: {}, matches: [], orderBy: [], matchMode: 'off', keepInputFields: [], take: null }); choosing.value = false; fieldSearch.value = '' }
function removeInvalidFields() { const fields = selectedFields.value.filter(f => !invalidFields.value.includes(f)); patch({ fields, fieldAliases: Object.fromEntries(Object.entries(props.config.fieldAliases || {}).filter(([f]) => fields.includes(f))) }) }
function checkField(name: string, checked: boolean) { const aliases = { ...props.config.fieldAliases }; if (!checked) delete aliases[name]; patch({ fields: checked ? [...new Set([...selectedFields.value, name])] : selectedFields.value.filter(f => f !== name), fieldAliases: aliases }) }
function selectVisible() { patch({ fields: [...new Set([...selectedFields.value, ...filteredColumns.value.map(c => c.columnName)])].slice(0, 100) }) }
watch(sourceId, () => { tableSearch.value = ''; fieldSearch.value = ''; choosing.value = false })
watch(() => props.config, () => { previewController?.abort(); preview.value = null; previewError.value = ''; previewing.value = false }, { deep: true })
onBeforeUnmount(() => previewController?.abort())
/**
 * 取数预览（真实只读查询）：先中止上一次请求，并用 controller 判定只接受最新响应。
 * 配置一有变化就作废已有预览，避免展示与当前配置不符的数据。
 */
async function loadPreview() {
  previewController?.abort()
  const controller = new AbortController(); previewController = controller
  previewError.value = ''; previewing.value = true
  try {
    const result = await post('/api/v1/workflows/value-preview', props.config, { signal: controller.signal })
    if (!controller.signal.aborted) preview.value = result.data
  } catch (cause) { if (!controller.signal.aborted) previewError.value = (cause as Error).message }
  finally { if (!controller.signal.aborted) previewing.value = false }
}
function displayValue(value: unknown) { return value === null ? '空值' : value === '' ? '空字符串' : String(value ?? '') }
</script>
<template>
  <section class="table-read">
    <p v-if="!config.datasourceId" class="wf-note">先选择数据源，再选择表和要读取的字段。</p>
    <p v-else-if="loading" role="status">正在读取表和字段注释…</p>
    <p v-else-if="error" role="alert" class="wf-error">{{ error }} <button type="button" @click="reload">重试</button></p>
    <template v-else-if="config.datasourceId">
      <div class="read-heading"><strong>选择数据表</strong><button v-if="selected" type="button" @click="choosing = !choosing">{{ choosing ? '收起表列表' : '更换数据表' }}</button></div>
      <div v-if="selected" class="selected-table"><strong>{{ selected.tableComment || '此表未填写注释' }}</strong><small>{{ selected.tableName }}</small></div>
      <p v-else-if="config.tableName" role="alert" class="wf-error">原数据表 {{ config.tableName }} 已不存在或无权读取，请重新选择。</p>
      <div v-if="!selected || choosing">
        <label>搜索表名或注释<input v-model="tableSearch" type="search" placeholder="例如：设备、保养、质检" /></label>
        <p class="wf-note">找到 {{ filteredTables.length }} 张表 · 注释来自所选数据库</p>
        <div class="table-options" aria-label="可读取的数据表">
          <button v-for="table in filteredTables" :key="table.tableName" type="button" class="table-option" :aria-pressed="config.tableName === table.tableName" @click="selectTable(table)"><strong>{{ table.tableComment || '无表注释' }}</strong><small>{{ table.tableName }}</small></button>
        </div>
        <p v-if="!filteredTables.length" class="wf-note">{{ tables.length ? '没有匹配的表，试试表名或其他关键词。' : '当前数据源没有允许读取的表，请检查数据源的表权限。' }}</p>
      </div>
      <template v-if="selected">
        <div class="read-heading"><strong>选择字段 · 已选 {{ selectedFields.length }} 项</strong><button type="button" @click="selectVisible">全选搜索结果</button></div>
        <label>搜索字段名或注释<input v-model="fieldSearch" type="search" placeholder="例如：设备名称、周期、完成时间" /></label>
        <p class="wf-note">每个选项依次显示数据库注释、字段名和类型。最多选择 100 个字段。</p>
        <div class="field-options">
          <label v-for="c in filteredColumns" :key="c.columnName" class="field-option"><input type="checkbox" :checked="selectedFields.includes(c.columnName)" :disabled="selectedFields.length >= 100 && !selectedFields.includes(c.columnName)" @change="checkField(c.columnName, ($event.target as HTMLInputElement).checked)" /><span><strong>{{ c.comment || '无字段注释' }}</strong><small>{{ c.columnName }} · {{ c.dataType }}{{ c.primaryKey ? ' · 主键' : '' }}</small></span></label>
        </div>
        <p v-if="!filteredColumns.length" class="wf-note">没有匹配的字段，请更换关键词。</p>
        <p v-if="invalidFields.length" role="alert" class="wf-error">原字段已变化：{{ invalidFields.join('、') }}。<button type="button" @click="removeInvalidFields">移除失效字段</button></p>
        <WorkflowTableFilters :filters="config.filters || []" :columns="columns" :upstream-fields="upstreamFields" :device="device" @update="patch({ filters: $event })" />
        <WorkflowTableMatching :config="config" :columns="columns" :upstream-fields="upstreamFields" @update="emit('update', $event)" />
        <label>读取行数上限<input type="number" min="1" max="10000" :value="config.maxRows || 10000" @change="patch({ maxRows: Number(($event.target as HTMLInputElement).value) })" /></label>
        <p class="wf-note">还受数据源行数上限约束。超出时会提示缩小范围，不会把不完整数据交给后续判断。</p>
        <button type="button" class="preview-button" :disabled="previewing || !selectedFields.length || !!invalidFields.length || usesInput" @click="loadPreview">{{ previewing ? '正在读取…' : '预览数据（前20条）' }}</button>
        <p v-if="usesInput" class="wf-note">筛选使用了上游字段，请点击顶部“测试运行”，在本节点“上次运行”中查看实际数据。</p>
        <p v-if="previewError" role="alert" class="wf-error">{{ previewError }}</p>
        <div v-if="preview" role="status" class="preview-result">
          <p>本次预览 {{ preview.data.length }} 条{{ preview.truncated ? '，还有更多记录' : '' }}；正式运行会重新读取数据库。</p>
          <div v-if="preview.data.length" class="preview-scroll" tabindex="0" aria-label="实际数据预览"><table><thead><tr><th v-for="c in preview.columns" :key="c.columnName">{{ c.comment || c.columnName }}<small v-if="c.comment">{{ c.columnName }}</small></th></tr></thead><tbody><tr v-for="(row, i) in preview.data" :key="i"><td v-for="c in preview.columns" :key="c.columnName">{{ displayValue(row[c.columnName]) }}</td></tr></tbody></table></div>
          <p v-else>没有符合筛选条件的记录。</p>
        </div>
      </template>
    </template>
  </section>
</template>
<style scoped>
.read-heading { display: flex; align-items: center; justify-content: space-between; gap: 8px; margin: 16px 0 8px; }
.selected-table { padding: 10px; border-radius: 8px; background: #eff4ff; color: #344054; overflow-wrap: anywhere; }
.table-read small { display: block; color: #667085; font-size: 11px; line-height: 1.5; font-weight: 400; overflow-wrap: anywhere; }
.table-options, .field-options { max-height: 248px; overflow-y: auto; border: 1px solid #e4e7ec; border-radius: 8px; }
.table-option { display: block; width: 100%; border: 0; border-bottom: 1px solid #f2f4f7; border-radius: 0; padding: 10px; text-align: left; background: #fff; white-space: normal; overflow-wrap: anywhere; }
.table-option:hover, .table-option[aria-pressed=true] { background: #eff4ff; }
.field-option { display: flex; gap: 9px; align-items: flex-start; margin: 0; padding: 9px 10px; cursor: pointer; border-bottom: 1px solid #f2f4f7; }
.field-option input { flex: 0 0 16px; width: 16px; height: 16px; margin: 3px 0 0; }
.field-option strong { font-weight: 500; overflow-wrap: anywhere; }
.preview-button { width: 100%; margin-top: 8px; }
.preview-result { font-size: 12px; }
.preview-scroll { max-height: 300px; overflow: auto; border: 1px solid #e4e7ec; border-radius: 8px; }
.preview-scroll table { border-collapse: collapse; min-width: 100%; }
.preview-scroll th, .preview-scroll td { padding: 8px; border: 1px solid #e4e7ec; white-space: nowrap; text-align: left; }
.preview-scroll th { background: #f9fafb; position: sticky; top: 0; }
</style>
