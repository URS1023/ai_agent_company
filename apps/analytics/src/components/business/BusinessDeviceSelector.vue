<script setup lang="ts">
/**
 * 设备卡片列表（搜索、工作流类型筛选、加载骨架屏、空态）。
 * 路由：无独立路由，由 BusinessDeviceWorkspace 在 /alerts、/quality 页面渲染。
 * 依赖接口：无请求（数据由父组件传入，搜索/刷新以事件上抛由页面触发重新加载）。
 * 关键交互：预警域下可把一台设备按工作流类型（巡检/保养/维修）拆成多张卡片；
 *   关键字与筛选条件为本地状态，只有点击"搜索"才会上抛事件（避免每次输入都请求）。
 * 约束：空态文案区分"当前筛选类型没有工作流""无匹配设备""还没有设备"三种情形，且仅在无关键字时提供新增入口。
 */
import { computed, shallowRef } from 'vue'
import { type AlertRecord } from '../../api/alerts'
import type { BusinessDeviceDomain } from '../../api/deviceWorkflows'
import BusinessDeviceCard from './BusinessDeviceCard.vue'

const props = defineProps<{
  domain: BusinessDeviceDomain
  rows: AlertRecord[]
  loading: boolean
  busy: boolean
  error: string
  canOpen: boolean
  canEdit: boolean
  canCreate: boolean
  canRemove: boolean
}>()
const categories = [{ key: 'all', label: '全部' }, { key: 'inspection', label: '巡检' }, { key: 'maintenance', label: '保养' }, { key: 'repair', label: '维修' }] as const
const category = shallowRef<string>('all')
type DeviceWorkflowCard = { key: string; device: AlertRecord; entry?: { id: string; label: string; workflow: { status?: string; publishedRevision?: number | null } } }
/**
 * 卡片数据：预警域且设备带 workflitEntries 时，按工作流类型展开为多张卡（仅保留该类型已存在工作流的项）；
 * 质检域或没有任何工作流条目时保持"一台设备一张卡"，并仍受当前类型筛选影响。
 */
const cards = computed(() => props.rows.flatMap<DeviceWorkflowCard>(device => {
  const entries = device.workflowEntries || {}
  if (props.domain !== 'alert' || !Object.values(entries).some((entry: any) => entry?.id)) {
    return category.value === 'all' || props.domain !== 'alert' ? [{ key: device.id || device.code, device, entry: undefined }] : []
  }
  return categories.filter(item => item.key !== 'all' && (category.value === 'all' || category.value === item.key) && entries[item.key]?.id)
    .map(item => ({ key: `${device.id}-${item.key}`, device, entry: { id: String(entries[item.key].id), label: item.label, workflow: entries[item.key] } }))
}))
const keyword = defineModel<string>('keyword', { required: true })
defineEmits<{
  search: []
  refresh: []
  open: [device: AlertRecord, workflowId?: string]
  edit: [device: AlertRecord]
  create: []
  remove: [device: AlertRecord]
  image: [device: AlertRecord]
}>()
</script>

<template>
  <section class="device-selector" aria-label="设备列表">
    <form class="device-search" role="search" @submit.prevent="$emit('search')">
      <label>
        <span class="sr-only">搜索设备</span>
        <i class="fa-solid fa-magnifying-glass" aria-hidden="true" />
        <input v-model="keyword" maxlength="200" placeholder="搜索设备名称、编码或位置" autocomplete="off" />
      </label>
      <button type="submit" :disabled="loading || busy">搜索</button>
      <button type="button" :disabled="loading || busy" @click="$emit('refresh')">刷新</button>
    </form>

    <div v-if="domain === 'alert'" class="workflow-category-filter" role="group" aria-label="工作流类型">
      <button v-for="item in categories" :key="item.key" type="button" :aria-pressed="category === item.key" @click="category = item.key">{{ item.label }}</button>
    </div>
    <p v-if="error" class="wf-error" role="alert">{{ error }}</p>
    <div v-if="loading" class="catalog-grid" role="status" aria-label="正在加载设备">
      <div v-for="index in 12" :key="index" class="device-card device-card-skeleton"><span /><span /><span /></div>
    </div>

    <div v-else-if="cards.length" class="catalog-grid" data-testid="business-device-list">
      <BusinessDeviceCard v-for="card in cards" :key="card.key" :device="card.device" :workflow-entry="card.entry" :busy="busy" :can-open="canOpen" :can-edit="canEdit" :can-remove="canRemove"
        @open="workflowId => $emit('open', card.device, workflowId)" @edit="$emit('edit', card.device)" @remove="$emit('remove', card.device)" @image="$emit('image', card.device)" />
    </div>

    <div v-else class="device-empty">
      <span class="device-empty-icon" aria-hidden="true"><i class="fa-solid fa-microchip" /></span>
      <h2 id="device-selector-title">{{ rows.length ? '当前类型没有工作流' : keyword ? '没有匹配的设备' : `还没有用于${domain === 'alert' ? '预警' : '质检'}的设备` }}</h2>
      <p>{{ rows.length ? '请选择其他工作流类型。' : keyword ? '换一个关键词，或清空搜索后重试。' : '新增设备后，点击一次即可进入该设备的业务工作流。' }}</p>
      <button v-if="canCreate && !keyword" type="button" class="wf-primary" @click="$emit('create')">新增设备</button>
    </div>
  </section>
</template>

<style scoped>
.workflow-category-filter { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 18px; }
.workflow-category-filter button { padding: 8px 20px; border: 1px solid #dbe3ed; border-radius: 6px; background: white; color: #44546a; cursor: pointer; }
.workflow-category-filter button[aria-pressed="true"] { background: #eaf4ff; color: #1269b5; border-color: #8cc7f5; }
.workflow-category-filter button:focus-visible { outline: 2px solid #1475c9; outline-offset: 2px; }
</style>
