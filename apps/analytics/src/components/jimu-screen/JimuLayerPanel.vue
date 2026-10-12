<script setup>
/**
 * JimuLayerPanel —— 大屏编辑器的图层列表（层级排序、显隐、锁定、重命名、删除、复制）。
 *
 * 位置：JimuBigScreenReplica（路由 `/report/designer/:id?`）左侧栏。
 * 关键约束：
 * - 列表按 orderNum 倒序展示（图层语义：越靠上越靠前），与画布绘制顺序相反，不要"修正"成升序；
 * - 所有操作只派发事件，实际状态与持久化由 JimuBigScreenReplica 处理；
 * - visualLocked（视觉锁定/无编辑权限）时禁止改名与结构变更，但允许选中查看；
 * - 重命名用本地编辑态（editingId/editingName），提交时才上抛，避免逐字符触发自动保存。
 */
import { computed, ref } from 'vue'

const props = defineProps({
  widgets: { type: Array, required: true },
  selectedId: { type: String, default: '' },
  selectedIds: { type: Array, default: () => [] },
  visualLocked: { type: Boolean, default: false }
})

const emit = defineEmits(['select', 'toggle-visible', 'toggle-lock', 'reorder', 'duplicate', 'remove', 'rename'])
const menuId = ref('')
const editingId = ref('')
const editingName = ref('')

/** 图层顺序与画布绘制顺序相反：orderNum 大者显示在上方，便于用户理解遮挡关系。 */
const layers = computed(() => [...props.widgets].sort((left, right) => (right.orderNum || 0) - (left.orderNum || 0)))

function startRename(widget) {
  if (props.visualLocked) return
  editingId.value = widget.id
  editingName.value = widget.componentName
}

function finishRename(widget) {
  const value = editingName.value.trim()
  if (value && value !== widget.componentName) emit('rename', widget.id, value)
  editingId.value = ''
}

function action(name, widget) {
  emit(name, widget.id)
  menuId.value = ''
}
</script>

<template>
  <aside class="layer-panel">
    <header class="layer-title">
      <div><strong>图层</strong><span>{{ widgets.length }}</span><button title="显示全部"><i class="fa-regular fa-eye"></i></button></div>
      <em>全屏模式</em>
    </header>
    <div class="layer-list">
      <div
        v-for="widget in layers"
        :key="widget.id"
        class="layer-row"
        :class="{ active: selectedIds.includes(widget.id) || widget.id === selectedId, muted: widget.visible === false, grouped: Boolean(widget.groupId) }"
        @click="emit('select', widget.id)"
        @dblclick="startRename(widget)"
      >
        <span class="component-icon"><i class="fa-solid fa-chart-column"></i></span>
        <input
          v-if="editingId === widget.id"
          v-model="editingName"
          class="rename-input"
          autofocus
          @click.stop
          @blur="finishRename(widget)"
          @keydown.enter.prevent="finishRename(widget)"
          @keydown.esc="editingId = ''"
        >
        <span v-else class="layer-name">{{ widget.metricName || widget.componentName }}</span>
        <button v-if="!visualLocked" class="visibility" :title="widget.visible === false ? '显示' : '隐藏'" @click.stop="emit('toggle-visible', widget.id)">
          <i :class="widget.visible === false ? 'fa-regular fa-eye-slash' : 'fa-regular fa-eye'"></i>
        </button>
        <span v-else class="source-lock" title="源模板视觉锁定"><i class="fa-solid fa-lock"></i></span>
        <button v-if="!visualLocked" class="more" title="图层操作" @click.stop="menuId = menuId === widget.id ? '' : widget.id"><i></i><i></i><i></i></button>
        <div v-if="!visualLocked && menuId === widget.id" class="layer-menu" @click.stop>
          <button @click="action('toggle-lock', widget)"><i :class="widget.locked ? 'fa-solid fa-lock-open' : 'fa-solid fa-lock'"></i>{{ widget.locked ? '解锁' : '锁定' }}</button>
          <button @click="emit('reorder', widget.id, 1); menuId = ''"><i class="fa-solid fa-arrow-up"></i>上移一层</button>
          <button @click="emit('reorder', widget.id, -1); menuId = ''"><i class="fa-solid fa-arrow-down"></i>下移一层</button>
          <button @click="action('duplicate', widget)"><i class="fa-regular fa-copy"></i>复制</button>
          <button class="danger" @click="action('remove', widget)"><i class="fa-regular fa-trash-can"></i>删除</button>
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.layer-panel {
  width: 180px;
  height: 100%;
  overflow: hidden;
  border-right: 1px solid #111419;
  background: #1d2026;
  color: #dce0e5;
}

.layer-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 42px;
  padding: 0 7px;
  border-bottom: 1px solid #272b31;
  background: #20242a;
}

.layer-title div { display: flex; align-items: center; gap: 6px; }
.layer-title strong { color: #f4f6f8; font-size: 14px; }
.layer-title span {
  display: grid;
  place-items: center;
  min-width: 20px;
  height: 18px;
  border-radius: 9px;
  background: #4b4f56;
  color: #fff;
  font-size: 11px;
}

.layer-title button {
  border: 0;
  background: transparent;
  color: #a7adb5;
  cursor: pointer;
}

.layer-title em { color: #e5e7ea; font-size: 13px; font-style: normal; }
.source-lock { display: grid; width: 22px; height: 22px; place-items: center; color: #7893ae; font-size: 9px; }

.layer-list {
  height: calc(100% - 42px);
  overflow-y: auto;
  scrollbar-color: #343942 #1b1e23;
  scrollbar-width: thin;
}

.layer-row {
  position: relative;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) 24px 20px;
  align-items: center;
  height: 41px;
  border-bottom: 1px solid rgba(255, 255, 255, .025);
  cursor: default;
}

.layer-row:hover,
.layer-row.active { background: #3a3d43; }
.layer-row.muted { opacity: .45; }
.layer-row.grouped .component-icon { border-left: 2px solid #72c5f8; }

.component-icon {
  display: grid;
  place-items: center;
  width: 30px;
  height: 30px;
  margin-left: 3px;
  border: 1px solid #38506e;
  background: #252a31;
  color: #1688ff;
  font-size: 16px;
}

.layer-name {
  overflow: hidden;
  padding: 0 5px;
  color: #eef1f4;
  font-size: 13px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rename-input {
  min-width: 0;
  height: 26px;
  border: 1px solid #1785d8;
  background: #15181d;
  color: #fff;
}

.visibility,
.more {
  width: 22px;
  height: 30px;
  border: 0;
  background: transparent;
  color: #bac0c7;
  cursor: pointer;
  opacity: 0;
}

.layer-row:hover .visibility,
.layer-row:hover .more,
.layer-row.active .visibility,
.layer-row.active .more { opacity: 1; }

.more { display: grid; place-items: center; align-content: center; gap: 2px; }
.more i { width: 3px; height: 3px; border-radius: 50%; background: currentColor; }

.layer-menu {
  position: absolute;
  top: 34px;
  right: 5px;
  z-index: 90;
  width: 130px;
  padding: 5px;
  border: 1px solid #3b4048;
  background: #20242a;
  box-shadow: 0 8px 22px rgba(0, 0, 0, .42);
}

.layer-menu button {
  display: flex;
  align-items: center;
  gap: 9px;
  width: 100%;
  height: 31px;
  border: 0;
  background: transparent;
  color: #d9dde2;
  text-align: left;
  cursor: pointer;
}

.layer-menu button:hover { background: #30353c; }
.layer-menu .danger { color: #ff9f9f; }
</style>
