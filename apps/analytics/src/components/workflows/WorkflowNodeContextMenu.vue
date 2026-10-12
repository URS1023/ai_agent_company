<script setup lang="ts">
/**
 * WorkflowNodeContextMenu —— 画布节点的右键菜单（Teleport 到 body，避免被画布 overflow 裁剪）。
 *
 * 依赖接口：`api/workflows.FlowNode`；动作以 action 事件上抛（properties/duplicate/delete），
 * 由 WorkflowCanvas.nodeMenuAction 处理。
 * 关键约束：
 * - 坐标夹取在视口内（右/下留出菜单尺寸），防止靠边右键时菜单不可见；
 * - 开始节点禁用复制与删除（流程唯一入口），禁用态与画布快捷键行为保持一致；
 * - 挂载时监听 document 的 pointerdown 关闭菜单，卸载时移除，避免残留监听；
 * - 菜单打开即聚焦首个按钮，Esc 关闭，保证键盘可用。
 */
import { computed, onBeforeUnmount, onMounted, useTemplateRef } from 'vue'
import type { FlowNode } from '../../api/workflows'

const props = defineProps<{ node: FlowNode; x: number; y: number; disabled: boolean }>()
const emit = defineEmits<{ action: [action: 'properties' | 'duplicate' | 'delete']; close: [] }>()
const menu = useTemplateRef<HTMLElement>('menu')
const position = computed(() => ({ left: `${Math.max(8, Math.min(props.x, window.innerWidth - 200))}px`, top: `${Math.max(8, Math.min(props.y, window.innerHeight - 170))}px` }))
function outside(event: PointerEvent) {
  if (!menu.value?.contains(event.target as Node)) emit('close')
}
onMounted(() => {
  document.addEventListener('pointerdown', outside, true)
  menu.value?.querySelector('button')?.focus({ preventScroll: true })
})
onBeforeUnmount(() => document.removeEventListener('pointerdown', outside, true))
</script>

<template>
  <Teleport to="body">
    <div ref="menu" class="wf-node-context-menu" :style="position" role="dialog" aria-label="节点操作" @contextmenu.prevent @keydown.esc.stop.prevent="$emit('close')">
      <strong>{{ node.title }}</strong>
      <button type="button" @click="$emit('action', 'properties')">属性</button>
      <button type="button" :disabled="disabled || node.kind === 'start'" @click="$emit('action', 'duplicate')">复制节点 <span>Ctrl D</span></button>
      <button type="button" class="danger" :disabled="disabled || node.kind === 'start'" @click="$emit('action', 'delete')">删除节点 <span>Delete</span></button>
    </div>
  </Teleport>
</template>

<style scoped>
.wf-node-context-menu{position:fixed;z-index:2100;width:192px;padding:5px;border:1px solid #e4e7ec;border-radius:10px;background:#fff;box-shadow:0 12px 28px #10182826;color:#344054;font-size:13px}
.wf-node-context-menu>strong{display:block;padding:7px 9px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:12px;color:#667085}
button{display:flex;justify-content:space-between;width:100%;padding:8px 9px;border:0;border-radius:6px;background:transparent;color:inherit;text-align:left;font:inherit;cursor:pointer}button:hover:not(:disabled),button:focus-visible{outline:none;background:#eff4ff}button:disabled{opacity:.45;cursor:not-allowed}button span{color:#98a2b3;font-size:11px}.danger{color:#d92d20}
</style>
