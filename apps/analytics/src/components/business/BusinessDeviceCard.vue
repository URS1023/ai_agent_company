<script setup lang="ts">
/**
 * 设备卡片（含可选的工作流条目：巡检/保养/维修）。
 * 路由：无独立路由，由 BusinessDeviceSelector 在 /alerts、/quality 设备目录中渲染。
 * 依赖接口：纯展示，无请求；动作以事件上抛（open/edit/remove/image）；图片编辑入口受 files.create/view 权限限制。
 * 关键交互：状态徽标由 workflowEntry（或 device.workflow）推导：未配置 / 已停用 / 未发布 / 已发布；
 *   无 workflows.view 权限时"打开工作流"按钮禁用。
 * 约束：设备的分类、编码、位置缺失时显示"未分类/未填写"，不允许空白单元格。
 */
import { computed } from 'vue'
import { equipmentStatus, type AlertRecord } from '../../api/alerts'
import { can } from '../../api/request'
import CatalogCard from './CatalogCard.vue'
import CatalogPhoto from './CatalogPhoto.vue'
defineEmits<{ open: [workflowId?: string]; edit: []; remove: []; image: [] }>()
const props = defineProps<{ device: AlertRecord; workflowEntry?: { id: string; label: string; workflow: { status?: string; publishedRevision?: number | null } }; busy: boolean; canOpen: boolean; canEdit: boolean; canRemove: boolean }>()
const title = computed(() => props.workflowEntry ? `${props.device.name} · ${props.workflowEntry.label}` : props.device.name)
/** 发布状态徽标：未配置 / 已停用 / 未发布 / 已发布（publishedRevision 是否存在表示是否发布过）。 */
const state = computed(() => {
  const workflow = props.workflowEntry?.workflow || props.device.workflow
  if (!workflow) return { text: '未配置', tone: 'gray' as const }
  if (workflow.status === 'disabled') return { text: '已停用', tone: 'gray' as const }
  return workflow.publishedRevision ? { text: '已发布', tone: 'green' as const } : { text: '未发布', tone: 'gray' as const }
})
</script>
<template>
  <CatalogCard :title="title" :status="state.text" :tone="state.tone" :data-device-id="device.id">
    <dl>
      <div><dt>所属分类:</dt><dd class="catalog-link" :title="device.category">{{ device.category || '未分类' }}</dd></div>
      <div><dt>设备编码:</dt><dd :title="device.code">{{ device.code }}</dd></div>
      <div><dt>安装位置:</dt><dd :title="device.location">{{ device.location || '未填写' }}</dd></div>
      <div><dt>设备状态:</dt><dd>{{ equipmentStatus[device.status] || device.status }}</dd></div>
    </dl>
    <template #image><CatalogPhoto :target="{ kind: 'device', id: device.id || '', name: device.name, image: device.image }" :editable="canEdit && can('files.create') && can('files.view')" @edit="$emit('image')" /></template>
    <template #actions>
      <button type="button" :disabled="busy" @click="$emit('edit')"><i class="fa-regular fa-pen-to-square" aria-hidden="true" />{{ canEdit ? '设备设置' : '详情' }}</button>
      <button v-if="canRemove" type="button" class="catalog-danger" :disabled="busy" :aria-label="'移除设备 ' + device.name" @click="$emit('remove')"><i class="fa-regular fa-trash-can" aria-hidden="true" />移除</button>
      <button type="button" class="catalog-workflow" :disabled="busy || !canOpen" :aria-label="title + '工作流'" @click="$emit('open', workflowEntry?.id)"><i class="fa-solid fa-diagram-project" aria-hidden="true" />打开{{ workflowEntry?.label || '' }}工作流</button>
    </template>
  </CatalogCard>
</template>
<style scoped>
:deep(.catalog-card-actions) { flex-wrap: wrap; row-gap: 4px; }
</style>
