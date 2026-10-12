<script setup lang="ts">
/**
 * 工作流"输出表（output-table）"节点配置表单：选择授权写入目标、写入方式与字段映射。
 * 路由：无独立路由，由 WorkflowNodeForm 按 node.kind === 'output-table' 渲染；
 *   也被预警流程向导与质检流程向导直接复用（回写配置部分）。
 * 依赖接口：api/writeback.ts 的 list/get（目标列表与详情）；parseWorkflowJson 校验固定值的 JSON 文本。
 * 关键交互（版本固定）：选择目标时把 targetId 与 targetVersion 成对写回，并把不兼容的写入方式清空；
 *   目标当前版本与已固定版本不一致时提供"确认采用当前目标版本"（必须人工确认，不自动跟随）；
 *   写入方式切换为 update 时清空 keys（定位键依赖列映射，重新勾选更安全）。
 * 约束：只允许选择目标已授权列；update 模式必须勾选完整主键/唯一键作为定位条件；每批最多 1000 行。
 * 错误呈现：error 内联展示（含固定值 JSON 解析失败）。
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { writeback, modeNames, type WriteTarget } from '../../api/writeback'
import { parseWorkflowJson } from '../../utils/workflowJson.js'
const props = defineProps<{ config: Record<string, any>; disabled: boolean; fields: string[] }>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
const choices = shallowRef<WriteTarget[]>([]), current = shallowRef<WriteTarget | null>(null)
const offset = shallowRef(0), error = shallowRef(''), loading = shallowRef(false)
let request: AbortController | null = null, stopped = false
const mappedColumns = computed<string[]>(() => (props.config.fields || []).map((field: any) => field.column).filter(Boolean))
function patch(values: object) { emit('update', { ...props.config, ...values }) }
function field(index: number, values: object) { patch({ fields: props.config.fields.map((item: any, i: number) => i === index ? { ...item, ...values } : item) }) }
/** 选择写入目标：固定 targetVersion，重置字段映射，并把不支持的写入方式清空（只允许目标已授权的方式）。 */
function choose(id: string) { const item = choices.value.find(target => target.id === id); if (item) patch({ targetId: id, targetVersion: item.version, fields: [], keys: [], mode: item.modes.includes(props.config.mode) ? props.config.mode : '' }) }
/** 固定值字段：按 JSON 解析后写入 value，解析失败只报错不改数据（避免把非法 JSON 提交到后端）。 */
function literal(index: number, value: string) { try { field(index, { value: parseWorkflowJson(value) }); error.value = '' } catch (cause) { error.value = (cause as Error).message } }
async function load(page: number) {
  request?.abort(); const active = new AbortController(); request = active; loading.value = true
  try { const result = await writeback.list(page, active.signal); if (!stopped && !active.signal.aborted) { choices.value = result.data; offset.value = page } }
  catch (cause) { if (!active.signal.aborted && !stopped) error.value = (cause as Error).message }
  finally { if (active === request) loading.value = false }
}
watch(() => props.config.targetId, async (id, _, cleanup) => {
  current.value = null; if (!id) return
  const abort = new AbortController(); cleanup(() => abort.abort())
  try { const result = await writeback.get(id, abort.signal); if (!abort.signal.aborted && !stopped) current.value = result.data }
  catch (cause) { if (!abort.signal.aborted && !stopped) error.value = (cause as Error).message }
}, { immediate: true })
onBeforeUnmount(() => { stopped = true; request?.abort() })
void load(0)
</script>
<template>
  <div class="write-node-form"><p class="wf-note">该节点会修改数据库。先在“数据源 → 结果写入目标”明确授权表与字段，再选择目标和映射。</p>
    <label>授权写入目标<select :value="config.targetId" :disabled="disabled || loading" @change="choose(($event.target as HTMLSelectElement).value)"><option value="">请选择目标</option><option v-if="current && !choices.some(item => item.id === current?.id)" :value="current.id">{{ current.name }}</option><option v-for="target in choices" :key="target.id" :value="target.id">{{ target.name }} · {{ target.tableName }} · v{{ target.version }}</option></select></label>
    <div class="wf-actions"><button type="button" :disabled="loading || !offset" @click="load(offset - 30)">上一页目标</button><button type="button" :disabled="loading || choices.length < 30" @click="load(offset + 30)">下一页目标</button></div>
    <p v-if="current">目标表：{{ current.tableName }} · 固定 v{{ config.targetVersion }} <button v-if="current.version !== config.targetVersion" type="button" :disabled="disabled" @click="patch({ targetVersion: current.version })">确认采用当前目标版本</button></p>
    <label>写入方式<select :value="config.mode" :disabled="disabled" @change="patch({ mode: ($event.target as HTMLSelectElement).value, keys: [] })"><option value="">请选择</option><option v-for="mode in current?.modes || []" :key="mode" :value="mode">{{ modeNames[mode] }}</option></select></label>
    <label>写入记录 JSONPath<input :value="config.recordPath" :disabled="disabled" @input="patch({ recordPath: ($event.target as HTMLInputElement).value })" /></label><small>$.data 写实际数据，$.issues 写逐项问题；每批最多 1000 行，失败不截断后继续。</small>
    <div v-for="(mapping, index) in config.fields || []" :key="index" class="wf-rule-card">
      <label>目标字段<select :value="mapping.column" :disabled="disabled" @change="field(index, { column: ($event.target as HTMLSelectElement).value })"><option value="">请选择已授权字段</option><option v-for="column in current?.columns || []" :key="column" :value="column">{{ column }}</option></select></label>
      <label>值的来源<select :value="mapping.origin || 'row'" :disabled="disabled" @change="field(index, { origin: ($event.target as HTMLSelectElement).value })"><option value="row">当前记录</option><option value="input">完整上游结果</option><option value="literal">固定值</option></select></label>
      <label v-if="mapping.origin === 'literal'">固定值（JSON）<input :value="JSON.stringify(mapping.value ?? null)" :disabled="disabled" @change="literal(index, ($event.target as HTMLInputElement).value)" /></label>
      <template v-else><label>取值 JSONPath<input :value="mapping.path" :disabled="disabled" @input="field(index, { path: ($event.target as HTMLInputElement).value })" /></label><label v-if="fields.length && mapping.origin !== 'input'">从已知字段填入路径<select value="" :disabled="disabled" @change="field(index, { path: '$[' + JSON.stringify(($event.target as HTMLSelectElement).value) + ']' })"><option value="">选择字段</option><option v-for="name in fields" :key="name">{{ name }}</option></select></label></template>
      <button type="button" :disabled="disabled" @click="patch({ fields: config.fields.filter((_: unknown, i: number) => i !== index), keys: (config.keys || []).filter((key: string) => key !== mapping.column) })">移除此映射</button>
    </div><button type="button" :disabled="disabled || !current" @click="patch({ fields: [...(config.fields || []), { column: '', origin: 'row', path: '$', value: null }] })">添加写入字段</button>
    <fieldset v-if="config.mode === 'update'" :disabled="disabled"><legend>更新定位键（完整主键或唯一键）</legend><label class="write-check" v-for="name in mappedColumns" :key="name"><input type="checkbox" :checked="config.keys?.includes(name)" @change="patch({ keys: ($event.target as HTMLInputElement).checked ? [...(config.keys || []), name] : config.keys.filter((key: string) => key !== name) })" />{{ name }}</label></fieldset>
    <p v-if="error" role="alert" class="wf-error">{{ error }}</p>
  </div>
</template>
