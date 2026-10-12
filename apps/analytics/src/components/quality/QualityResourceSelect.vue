<script setup lang="ts">
/**
 * 通用质检资源下拉（模板 / 设备 / 指标 / HTTP 检测服务 / 工作流共用）。
 * 路由：无独立路由，被 QualityInspectionForm、QualityNodeForm、QualityRunPanel 等复用。
 * 依赖接口：endpoint 由调用方指定（如 /api/v1/quality/templates、/api/v1/alerts/devices、/api/v1/collection/sources、
 *   /api/v1/workflows），固定 limit=30 分页，按 keyword 与可选 domain 过滤。
 * 关键交互：选择时同时抛 update:modelValue（id）与 select（整行），供调用方固定版本号；
 *   当已选 id 不在当前页选项中时，额外补一条占位 option（用详情接口取回名称），防止回显为空。
 * 加载/取消：listing 与详情各持有 AbortController，endpoint/domain/翻页/刷新变化时 abort 上一次；卸载时中止。
 * 约束：statuses 用于按状态过滤（如只列已发布工作流）；statusNames 只影响展示，不改变接口语义。
 * 错误呈现：error 就近展示在控件下方（不弹全局消息）。
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { get } from '../../api/request'
import type { QualityRecord } from '../../api/quality'
const props = defineProps<{ endpoint: string; modelValue: string; label: string; disabled?: boolean; statuses?: string[]; domain?: string }>()
const emit = defineEmits<{ 'update:modelValue': [value: string]; select: [row: QualityRecord | null] }>()
const options = shallowRef<QualityRecord[]>([]), selected = shallowRef<QualityRecord | null>(null)
const keyword = shallowRef(''), offset = shallowRef(0), error = shallowRef(''), loading = shallowRef(false), refresh = shallowRef(0)
/** 页面内可见选项：statuses 为空时全部展示，否则只保留允许的状态。 */
const visibleOptions = computed(() => options.value.filter(row => !props.statuses || props.statuses.includes(row.status)))
const statusNames: Record<string, string> = { published: '已发布', draft: '草稿', disabled: '停用', inactive: '停用', deleted: '已移除' }
let controller: AbortController | null = null
watch([() => props.endpoint, () => props.domain, offset, refresh], async (_, __, cleanup) => {
  controller?.abort(); const request = new AbortController(); controller = request; cleanup(() => request.abort()); loading.value = true; error.value = ''
  try { const response = await get<{data: QualityRecord[]}>(props.endpoint, { limit: 30, offset: offset.value, keyword: keyword.value, ...(props.domain ? { domain: props.domain } : {}) }, { signal: request.signal }); if (!request.signal.aborted) options.value = response.data }
  catch (cause) { if (!request.signal.aborted) error.value = (cause as Error).message }
  finally { if (!request.signal.aborted) loading.value = false }
}, { immediate: true })
watch(() => props.modelValue, async (id, _, cleanup) => {
  selected.value = null; if (!id) return
  const request = new AbortController(); cleanup(() => request.abort())
  try { const response = await get<{data: QualityRecord}>(props.endpoint + '/' + id, {}, { signal: request.signal }); if (!request.signal.aborted) selected.value = response.data }
  catch (cause) { if (!request.signal.aborted) error.value = (cause as Error).message }
}, { immediate: true })
/** 选择项：先更新 v-model 的 id，再按需抛整行（调用方据此固定资源版本）。 */
function choose(event: Event) { const id = (event.target as HTMLSelectElement).value; const row = options.value.find(row => row.id === id) || selected.value; emit('update:modelValue', id); emit('select', id ? row : null) }
/** 按关键字重新查询：offset 归零并通过 refresh 自增触发 watch 重新拉取。 */
function search() { offset.value = 0; refresh.value++ }
onBeforeUnmount(() => controller?.abort())
</script>
<template>
  <div class="qc-resource"><label>{{ label }}<select :value="modelValue" :disabled="disabled || loading" @change="choose"><option value="">请选择</option><option v-if="modelValue && !options.some(row => row.id === modelValue)" :value="modelValue">{{ selected?.name || modelValue }}</option><option v-for="row in visibleOptions" :key="row.id" :value="row.id">{{ row.name }}{{ statusNames[row.status] ? '（' + statusNames[row.status] + '）' : '' }} · {{ row.version ? 'v' + row.version : row.code }}</option></select></label><div class="qc-select-tools"><input v-model="keyword" :aria-label="'搜索' + label" placeholder="按名称搜索" :disabled="disabled" @keydown.enter.prevent="search" /><button type="button" :disabled="disabled || loading" @click="search">查找</button><button type="button" :disabled="disabled || loading || !offset" @click="offset -= 30">上一页</button><button type="button" :disabled="disabled || loading || options.length < 30" @click="offset += 30">下一页</button></div><p v-if="error" role="alert" class="wf-error">{{ error }}</p></div>
</template>
