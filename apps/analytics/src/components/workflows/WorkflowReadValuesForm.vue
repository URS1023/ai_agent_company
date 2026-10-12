<script setup lang="ts">
/**
 * WorkflowReadValuesForm —— 读取值节点（read-values）的数据源/数据集/字段配置表单。
 *
 * 位置：WorkflowNodeForm 中 kind === 'read-values' 时渲染。
 * 依赖接口：`api/request.get('/api/v1/workflows/value-sources')`（数据集目录）、
 * `api/alerts.alerts.list('devices')`（设备范围数据集需要设备清单）、WorkflowTableReadForm（直连表模式）。
 * 关键约束：
 * - 两种读取模式：dataset（按后端模板读取，历史配置没有 readMode 时按 templateId 推断）与 table（直连表）；
 *   切换数据源时必须重置模板/字段/过滤等下游配置（tableDefaults），否则会残留不属于新源的字段；
 * - 数据集目录按 datasourceId 拉取，带 AbortController 并在依赖变化时中止上一次请求；
 *   设备清单拉取失败只置空、不阻断主流程（设备为可选项）；
 * - 设备上下文下若数据源与当前设备一致，默认把 deviceId 填入配置，避免用户在设备入口重复选择；
 * - fields 为空表示读取全部列（checkedFields 的回退语义），不要改成空集。
 */
import { computed, shallowRef, watch } from 'vue'
import { get } from '../../api/request'
import { alerts } from '../../api/alerts'
import WorkflowTableReadForm from './WorkflowTableReadForm.vue'
const props = defineProps<{ config: Record<string, any>; sources: any[]; disabled: boolean; fields: string[]; device?: Record<string, any> }>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
const datasets = shallowRef<any[]>([]), devices = shallowRef<any[]>([])
const loading = shallowRef(false), error = shallowRef('')
const isDataset = computed(() => props.config.readMode === 'dataset' || (!props.config.readMode && !!props.config.templateId))
const sourceChoices = computed(() => props.sources.filter(s => isDataset.value ? s.type !== 'http' : ['mysql', 'postgres', 'sqlite'].includes(s.type)))
const selectedSource = computed(() => sourceChoices.value.find(s => s.id === props.config.datasourceId))
const selected = computed(() => datasets.value.find(d => d.id === props.config.templateId))
const columns = computed<any[]>(() => selected.value?.columns || [])
const parameterFields = computed<any[]>(() => (selected.value?.parameters || []).filter((p: any) => p.name !== 'deviceKey'))
const checkedFields = computed<string[]>(() => props.config.fields?.length ? props.config.fields : columns.value.map(c => c.name))
const deviceChoices = computed(() => devices.value.filter(d => d.datasourceId === props.config.datasourceId))
/**
 * 拉取数据集目录（仅 dataset 模式且已选数据源时）。
 * 依赖变化时通过 cleanup 中止上一次请求；设备清单属于附加信息，失败时静默置空不影响主流程。
 */
watch(() => [props.config.datasourceId, isDataset.value] as const, async ([id, legacy], _old, cleanup) => {
  const controller = new AbortController(); cleanup(() => controller.abort())
  datasets.value = []; error.value = ''; loading.value = false
  if (!id || !legacy) return
  loading.value = true
  try {
    const result = await get<any>('/api/v1/workflows/value-sources', { datasourceId: id }, { signal: controller.signal })
    if (!controller.signal.aborted) datasets.value = result.data
    if (result.data.some((d: any) => d.deviceScoped !== false)) {
      try { const equipment = await alerts.list('devices', { limit: 200 }, controller.signal); if (!controller.signal.aborted) devices.value = equipment.data }
      catch { if (!controller.signal.aborted) devices.value = [] }
    }
  } catch (cause) { if (!controller.signal.aborted) error.value = (cause as Error).message }
  finally { if (!controller.signal.aborted) loading.value = false }
}, { immediate: true })
function patch(values: Record<string, any>) { emit('update', { ...props.config, ...values }) }
function tableDefaults() { return { tableName: '', filters: [], fields: [], fieldAliases: {}, matches: [], orderBy: [], matchMode: 'off', keepInputFields: [], take: null } }
/** 切换数据源：必须清空模板与表配置（tableDefaults），避免残留属于旧数据源的字段/过滤条件。 */
function source(id: string) { const selected = sourceChoices.value.find(s => s.id === id); patch({ ...tableDefaults(), readMode: isDataset.value ? 'dataset' : 'table', datasourceId: id, datasourceVersion: selected?.version ?? null, templateId: '', templateVersion: 1, deviceId: props.device?.datasourceId === id ? props.device.id : null, parameters: {}, parameterSources: {} }) }
function directTable() { patch({ ...tableDefaults(), readMode: 'table', templateId: '', templateVersion: 1, parameters: {}, parameterSources: {} }) }
/**
 * 选择数据集：字段默认全选、参数取模板默认值，并记录 templateVersion 以锁定模板版本快照；
 * deviceScoped === false 的数据集清空 deviceId（非设备范围不应携带设备过滤）。
 */
function dataset(id: string) {
  const selected = datasets.value.find(d => d.id === id)
  patch({ templateId: id, templateVersion: selected?.version || 1, fields: selected?.columns.map((c: any) => c.name) || [], parameters: Object.fromEntries((selected?.parameters || []).map((p: any) => [p.name, p.default ?? null])), parameterSources: {}, deviceId: selected?.deviceScoped === false ? null : props.device?.id || props.config.deviceId || null })
}
function field(name: string, enabled: boolean) { patch({ fields: enabled ? [...new Set([...checkedFields.value, name])] : checkedFields.value.filter(f => f !== name) }) }
function parameter(name: string, value: string) { patch({ parameters: { ...props.config.parameters, [name]: value } }) }
</script>
<template>
  <fieldset class="read-values-form" :disabled="disabled">
    <label>从哪里读取<select :value="config.datasourceId" @change="source(($event.target as HTMLSelectElement).value)"><option value="">选择数据源</option><option v-for="s in sourceChoices" :key="s.id" :value="s.id">{{ s.name }}</option></select></label>
    <p v-if="selectedSource && config.datasourceVersion != null && config.datasourceVersion !== selectedSource.version" class="wf-error">数据源配置已更新。<button type="button" @click="source(selectedSource.id)">重新选择并核对字段</button></p>
    <WorkflowTableReadForm v-if="!isDataset" :config="config" :upstream-fields="fields" :device="device" @update="emit('update', $event)" />
    <template v-else>
    <p class="wf-note">此节点使用已有的数据集配置。<button type="button" @click="directTable">改为直接选择表和字段</button></p>
    <label>读取什么数据<select :disabled="loading || !config.datasourceId" :value="config.templateId" @change="dataset(($event.target as HTMLSelectElement).value)"><option value="">选择数据集</option><option v-for="d in datasets" :key="d.id" :value="d.id">{{ d.name }}</option></select></label>
    <p class="wf-note">这些选项是本项目已配置的读取数据集，定义了来源表、关联方式和可读字段。运行时按下方范围，从所选数据库读取实际记录。</p>
    <details v-if="selected" class="wf-dataset-source">
      <summary>查看数据来源</summary>
      <p><strong>读取数据集：</strong>{{ selected.name }}</p>
      <p><strong>数据源：</strong>{{ selectedSource?.name || '所选数据源' }}<span v-if="selectedSource?.databaseName">（{{ selectedSource.databaseName }}）</span></p>
      <p><strong>来源表：</strong></p>
      <ul v-if="selected.sourceTables?.length">
        <li v-for="table in selected.sourceTables" :key="table.name">{{ table.label }}<small v-if="table.label !== table.name">{{ table.name }}</small></li>
      </ul>
      <p v-else class="wf-note">暂未取得来源表说明，请检查数据集定义。</p>
      <p class="wf-note">这里只读取记录。计算、判断条件和结果由后续节点配置。</p>
    </details>
    <p v-if="loading" role="status">正在读取可用字段…</p><p v-if="error" role="alert" class="wf-error">{{ error }}</p>
    <p v-if="config.datasourceId && !loading && !error && !datasets.length" class="wf-note">此数据源尚未配置可读取的数据集。</p>
    <label v-if="device?.id && selected?.deviceScoped !== false">当前设备<input :value="device.name" readonly /></label>
    <label v-else-if="selected?.deviceScoped !== false">设备范围<select :value="config.deviceId || ''" @change="patch({ deviceId: ($event.target as HTMLSelectElement).value || null })"><option value="">数据集中的全部设备</option><option v-for="d in deviceChoices" :key="d.id" :value="d.id">{{ d.name }}</option></select></label>
    <p v-if="selected && selected.version !== config.templateVersion" class="wf-error">数据集定义已更新。<button type="button" @click="dataset(selected.id)">使用最新字段</button></p>
    <div v-for="p in parameterFields" :key="p.name">
      <label>{{ p.label || p.name }}<select :value="config.parameterSources?.[p.name] || 'input'" @change="patch({ parameterSources: { ...config.parameterSources, [p.name]: ($event.target as HTMLSelectElement).value } })"><option value="input">使用上游同名输入</option><option value="fixed">使用固定值</option></select></label>
      <label>{{ config.parameterSources?.[p.name] === 'fixed' ? '固定值' : '上游没有此输入时的默认值' }}
        <select v-if="p.options?.length" :value="config.parameters?.[p.name] ?? p.default ?? ''" @change="parameter(p.name, ($event.target as HTMLSelectElement).value)">
          <option v-for="option in p.options" :key="String(option.value)" :value="option.value">{{ option.label }}</option>
        </select>
        <input v-else :value="config.parameters?.[p.name] ?? p.default ?? ''" @change="parameter(p.name, ($event.target as HTMLInputElement).value)" />
      </label>
      <p v-if="config.parameterSources?.[p.name] !== 'fixed'" class="wf-note">上游提供此输入时优先使用上游值；选择“固定值”可始终使用本节点的设置。</p>
    </div>
    <details v-if="columns.length" open><summary>读取哪些值（至少一项）</summary><button type="button" @click="patch({ fields: columns.map(c => c.name) })">全选字段</button><label v-for="c in columns" :key="c.name" class="wf-check"><input type="checkbox" :checked="checkedFields.includes(c.name)" :disabled="checkedFields.length === 1 && checkedFields.includes(c.name)" @change="field(c.name, ($event.target as HTMLInputElement).checked)" />{{ c.label || c.name }}<small v-if="c.unit">（{{ c.unit }}）</small></label></details>
    </template>
  </fieldset>
</template>

<style scoped>
.read-values-form { border: 0; padding: 0; margin: 0; min-width: 0; }
.wf-dataset-source { margin: 10px 0 16px; padding: 10px 12px; border: 1px solid #e4e7ec; border-radius: 8px; background: #f9fafb; font-size: 12px; }
.wf-dataset-source summary { cursor: pointer; color: #344054; }
.wf-dataset-source p { margin: 8px 0; overflow-wrap: anywhere; }
.wf-dataset-source ul { padding-left: 18px; margin: 8px 0; }
.wf-dataset-source li { margin: 6px 0; overflow-wrap: anywhere; }
.wf-dataset-source small { display: block; color: #667085; }
</style>
