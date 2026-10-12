<script setup lang="ts">
/**
 * WorkflowAiGenerator —— 「AI 生成工作流草稿」表单（WorkflowWorkbench 的 aiOpen 弹窗内）。
 *
 * 依赖接口：`api/request.can`（新建/编辑权限）、`api/collection.HttpSource`、WorkflowHttpSourceDialog（就地新建 HTTP 数据源）。
 * 关键约束：
 * - 只把勾选资源的 ID、版本与脱敏业务说明交给模型；不勾选任何资源时不做隐式默认选择
 *   （避免 AI 把敏感数据源当默认目标）；
 * - 监听 sources 变化并剔除已禁用/已删除的资源 ID，防止提交过期选择；
 * - 生成是异步长任务，busy 期间禁用全部输入；生成结果由父级写入画布草稿，本组件不落库；
 * - 新建 HTTP 数据源成功后自动勾选该数据源（用户刚刚显式创建，语义上就是本次目标）。
 */
import { computed, shallowRef, watch } from 'vue'
import { can } from '../../api/request'
import type { HttpSource } from '../../api/collection'
import WorkflowHttpSourceDialog from './WorkflowHttpSourceDialog.vue'

const props = defineProps<{ busy: boolean; domains: string[]; sources: any[] }>()
const emit = defineEmits<{
  generate: [value: { prompt: string; name: string; resourceIds: string[] }]
  sourceSaved: [source: any]
}>()
const name = shallowRef('AI 生成工作流')
const prompt = shallowRef('')
const resourceKeyword = shallowRef('')
const resourceIds = shallowRef<string[]>([])
const sourceDialog = shallowRef(false)
/** 可选资源：仅 enabled 的数据源参与搜索与勾选，禁用资源不得进入 AI 上下文。 */
const available = computed(() => {
  const keyword = resourceKeyword.value.trim().toLocaleLowerCase()
  return props.sources.filter(source => source.enabled && (!keyword || [source.name, source.description, source.environmentSummary, source.url].join(' ').toLocaleLowerCase().includes(keyword)))
})
const httpSources = computed(() => available.value.filter(source => source.type === 'http'))
const databaseSources = computed(() => available.value.filter(source => source.type !== 'http'))
/** 数据源列表变化后剔除失效勾选，避免提交已被禁用或删除的资源 ID。 */
watch(() => props.sources, sources => {
  const allowed = new Set(sources.filter(source => source.enabled).map(source => source.id))
  resourceIds.value = resourceIds.value.filter(id => allowed.has(id))
}, { deep: true })
function toggle(id: string, checked: boolean) {
  resourceIds.value = checked ? [...new Set([...resourceIds.value, id])] : resourceIds.value.filter(value => value !== id)
}
function saved(source: HttpSource) {
  emit('sourceSaved', source)
  if (source.enabled) toggle(source.id, true)
}
</script>

<template>
  <form class="wf-ai-generator" @submit.prevent="emit('generate', { name: name.trim(), prompt: prompt.trim(), resourceIds })">
    <label>草稿名称<input v-model="name" maxlength="200" :disabled="busy" /></label>
    <label>业务目标<textarea v-model="prompt" rows="6" maxlength="8000" :disabled="busy" placeholder="例如：每天读取设备数据库中的温度与压力，按阈值生成预警问题并写入指定报告表；异常时等待值班人员复核。" /></label>
    <section class="wf-ai-resources" aria-labelledby="wf-ai-resource-title">
      <header><div><strong id="wf-ai-resource-title">本次可用数据源</strong><small>仅把勾选项的 ID、版本和脱敏业务说明交给 AI；多个候选不会默认选择首项。</small></div><span>{{ resourceIds.length }} 项已选</span></header>
      <label class="wf-ai-resource-search">筛选资源<input v-model="resourceKeyword" type="search" placeholder="名称、用途或地址" :disabled="busy" /></label>
      <details :open="httpSources.length > 0"><summary>HTTP / API · {{ httpSources.length }}</summary>
        <div v-if="httpSources.length" class="wf-ai-resource-list"><label v-for="source in httpSources" :key="source.id" class="wf-ai-resource-card"><input type="checkbox" :checked="resourceIds.includes(source.id)" :disabled="busy" @change="toggle(source.id, ($event.target as HTMLInputElement).checked)" /><span><b>{{ source.name }}</b><small>{{ source.method }} · {{ source.url }}</small><small :class="{ 'wf-resource-warning': !source.allowAutomatic }">{{ source.allowAutomatic ? '可自动只读采集' : '尚未授权工作流自动采集' }} · v{{ source.version }}</small></span></label></div>
        <p v-else class="wf-note">当前没有启用的 HTTP 数据源。需要请求豆瓣或其他接口时，请先新建并明确 JSONPath、字段和自动采集权限。</p>
        <div class="wf-source-actions"><button v-if="can('datasources.create')" type="button" :disabled="busy" @click="sourceDialog = true">新建 HTTP 数据源</button><RouterLink class="wf-source-link" :to="{ path: '/datasources', query: { type: 'http' } }">管理 HTTP 数据源</RouterLink></div>
      </details>
      <details :open="databaseSources.length > 0"><summary>数据库 / 分析源 · {{ databaseSources.length }}</summary>
        <div v-if="databaseSources.length" class="wf-ai-resource-list"><label v-for="source in databaseSources" :key="source.id" class="wf-ai-resource-card"><input type="checkbox" :checked="resourceIds.includes(source.id)" :disabled="busy" @change="toggle(source.id, ($event.target as HTMLInputElement).checked)" /><span><b>{{ source.name }}</b><small>{{ source.environmentSummary || `${String(source.type).toUpperCase()} · ${source.databaseName || '未命名数据库'}` }}</small><small>{{ source.description || '未填写业务口径' }} · v{{ source.version }}</small></span></label></div>
        <p v-else class="wf-note">当前没有启用的数据库分析源。</p>
      </details>
    </section>
    <p class="wf-note">生成后会自动保存为草稿；请检查提示和节点配置后再发布。生成草稿不会自动执行数据采集或写回业务数据。</p>
    <button class="wf-primary" type="submit" :disabled="busy || prompt.trim().length < 8 || !name.trim()">生成草稿</button>
    <WorkflowHttpSourceDialog v-model="sourceDialog" :source="null" @saved="saved" />
  </form>
</template>
