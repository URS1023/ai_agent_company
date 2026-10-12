<script setup lang="ts">
/**
 * WorkflowNodePalette —— 节点选择面板（画布浮层，WorkflowEditor 打开）。
 *
 * 依赖接口：`api/workflows.NodeKind`（后端节点目录，含 available / group / configSchema）。
 * 关键约束：
 * - 目录以后端下发的 catalog 为唯一来源：`available === false` 的节点不展示，
 *   前端不得自行"兜底"渲染后端未开放的节点类型；
 * - 分组顺序固定为 groupOrder，未列入的 group 不显示；开始节点单独成页签，不在"节点"页签重复出现；
 * - 组合片段（snippet）只在片段内所有节点都可用时才展示，避免给出无法一次生成的推荐；
 * - 本组件只派发 add / addSnippet，落位与连线由 WorkflowEditor.addMany 决定。
 */
import { computed, onMounted, shallowRef, useTemplateRef } from 'vue'
import type { NodeKind } from '../../api/workflows'

const props = withDefaults(defineProps<{ catalog: NodeKind[]; disabled: boolean; scopeLabel?: string; domain?: 'alert' | 'quality' }>(), { scopeLabel: '' })
defineEmits<{ add: [kind: NodeKind]; addSnippet: [kinds: NodeKind[]]; close: [] }>()
const keyword = shallowRef('')
const activeTab = shallowRef<'nodes' | 'tools' | 'start' | 'snippets'>('nodes')
const search = useTemplateRef<HTMLInputElement>('search')
/** 仅展示后端标记为可用的节点类型；不可用节点（环境不支持/未授权）不参与渲染。 */
const available = computed(() => props.catalog.filter(item => item.available))
const toolKinds = new Set(['http-request', 'database-query', 'jsonpath-extract', 'arithmetic', 'set-operation', 'output-table', 'code', 'tool', 'template', 'text'])
const tabItems = computed(() => {
  if (activeTab.value === 'tools') return available.value.filter(item => item.group === '工具' || toolKinds.has(item.kind))
  if (activeTab.value === 'start') return available.value.filter(item => ['start'].includes(item.kind))
  return available.value.filter(item => item.kind !== 'start')
})
const matching = computed(() => {
  const query = keyword.value.trim().toLocaleLowerCase()
  return tabItems.value.filter(item => !query || [item.title, item.description, item.kind].some(text => text.toLocaleLowerCase().includes(query)))
})
const groupOrder = ['基础', 'AI', '业务', '数据', '规则', '控制', '工具']
const groups = computed(() => groupOrder.map(name => ({ name, items: matching.value.filter(item => item.group === name) })).filter(group => group.items.length))
const groupIcons: Record<string, string> = { 基础: 'fa-play', AI: 'fa-wand-magic-sparkles', 业务: 'fa-briefcase', 数据: 'fa-database', 规则: 'fa-list-check', 控制: 'fa-code-branch', 工具: 'fa-screwdriver-wrench' }
const snippetDefinitions = computed(() => props.domain === 'alert' ? [
  { title: '采集并判定预警', description: '数据查询、字段提取、预警判定与通知', kinds: ['database-query', 'jsonpath-extract', 'alert-evaluate', 'alert-notify'] },
  { title: '解释并输出预警', description: '用模型解释结果并生成输出文本', kinds: ['llm', 'template'] },
] : props.domain === 'quality' ? [
  { title: '检测并判定质量', description: '外观检测、质检判定与结果写入', kinds: ['qc-appearance', 'qc-evaluate', 'output-table'] },
  { title: '采集并执行质检', description: '数据查询、字段提取与质量判定', kinds: ['database-query', 'jsonpath-extract', 'qc-evaluate'] },
] : [
  { title: '提取并转换数据', description: '字段提取后生成模板文本', kinds: ['jsonpath-extract', 'template'] },
])
/** 组合片段：只要有一个组成节点不可用就整体隐藏，避免推荐无法生成的流程。 */
const snippets = computed(() => snippetDefinitions.value.map(snippet => ({
  ...snippet,
  items: snippet.kinds.map(kind => available.value.find(item => item.kind === kind)).filter((item): item is NodeKind => !!item),
})).filter(snippet => snippet.items.length === snippet.kinds.length))
function selectTab(tab: typeof activeTab.value) {
  activeTab.value = tab
  keyword.value = ''
  requestAnimationFrame(() => search.value?.focus())
}
onMounted(() => search.value?.focus())
</script>

<template>
  <section class="wf-palette" aria-label="添加工作流节点" @keydown.esc.stop="$emit('close')">
    <div class="wf-palette-header">
      <nav class="wf-palette-tabs" aria-label="节点类型">
        <button type="button" :class="{ active: activeTab === 'nodes' }" @click="selectTab('nodes')">节点</button>
        <button type="button" :class="{ active: activeTab === 'tools' }" @click="selectTab('tools')">工具</button>
        <button type="button" :class="{ active: activeTab === 'start' }" @click="selectTab('start')">开始</button>
        <button type="button" :class="{ active: activeTab === 'snippets' }" @click="selectTab('snippets')">Snippets</button>
      </nav>
      <button type="button" class="wf-palette-close" aria-label="关闭节点库" title="关闭" @click="$emit('close')"><strong class="wf-visually-hidden">关闭节点库</strong><i class="fa-solid fa-xmark" aria-hidden="true" /></button>
      <label v-if="activeTab !== 'snippets'" class="wf-palette-search"><i class="fa-solid fa-magnifying-glass" aria-hidden="true" /><input ref="search" v-model="keyword" type="search" aria-label="查找节点" :placeholder="activeTab === 'tools' ? '搜索工具' : activeTab === 'start' ? '搜索开始节点' : '搜索节点、Agent、人工、知识库'" /><button v-if="keyword" type="button" aria-label="清空搜索" @click="keyword = ''; search?.focus()"><strong class="wf-visually-hidden">清空搜索</strong><i class="fa-solid fa-circle-xmark" aria-hidden="true" /></button></label>
      <span role="status">{{ scopeLabel || '当前项目可用节点' }}</span>
    </div>
    <div v-if="activeTab === 'snippets' && snippets.length" class="wf-palette-list wf-snippet-list">
      <button v-for="snippet in snippets" :key="snippet.title" type="button" :disabled="disabled" @click="$emit('addSnippet', snippet.items)"><i class="fa-solid fa-diagram-project" aria-hidden="true" /><span><strong>{{ snippet.title }}</strong><small>{{ snippet.description }}</small></span><i class="fa-solid fa-plus" aria-hidden="true" /></button>
    </div>
    <div v-else-if="activeTab !== 'snippets' && matching.length" class="wf-palette-list">
      <section v-for="group in groups" :key="group.name" class="wf-palette-group">
        <h3><i class="fa-solid" :class="groupIcons[group.name]" aria-hidden="true" />{{ group.name }}</h3>
        <button v-for="kind in group.items" :key="kind.kind" type="button" :disabled="disabled" @click="$emit('add', kind)"><i class="fa-solid fa-cube" aria-hidden="true" /><span><strong>{{ kind.title }}</strong><small>{{ kind.description }}</small></span><i class="fa-solid fa-plus" aria-hidden="true" /></button>
      </section>
    </div>
    <p v-else class="wf-palette-empty" role="status">{{ activeTab === 'start' ? '当前工作流已包含开始节点。' : '没有匹配的节点，请换一个名称或用途关键词。' }}</p>
  </section>
</template>
