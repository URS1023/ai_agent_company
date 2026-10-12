<script setup>
/**
 * JimuTopToolbar —— 大屏编辑器的顶部工具栏（组件库入口、保存/另存、撤销重做、预览、AI、数据源、历史、发布等）。
 *
 * 位置：JimuBigScreenReplica（路由 `/report/designer/:id?`）顶部。
 * 依赖接口：`api/request.can`（按权限显示/禁用按钮）、JimuComponentLibrary（组件库下拉）。
 * 关键约束：
 * - 纯事件上抛组件：不做任何持久化，保存/发布/预览均由 JimuBigScreenReplica 编排；
 * - 所有按钮的禁用态必须同时考虑 saving/dirty/status，避免在自动保存未完成时发布；
 * - 快捷键（Ctrl+Z/Y 等）由父级在窗口层监听，此处不重复注册，防止双触发。
 */
import { ref, onBeforeUnmount, onMounted } from 'vue'
import { can } from '@/api/request'
import JimuComponentLibrary from './JimuComponentLibrary.vue'

defineProps({
  groups: { type: Array, required: true },
  canUndo: { type: Boolean, default: false },
  canRedo: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  dirty: { type: Boolean, default: false },
  hasReport: { type: Boolean, default: false },
  status: { type: String, default: 'draft' },
})

const emit = defineEmits(['add', 'save', 'save-as', 'undo', 'redo', 'preview', 'ai', 'help', 'datasource', 'dataset', 'close', 'history', 'publish'])
const activeGroup = ref(null)
const activeIndex = ref(0)
const saveMenu = ref(false)
const previewMenu = ref(false)
const toolbar = ref(null)
function closeMenus(event) {
  if (event.type === 'keydown' && event.key !== 'Escape') return
  if (event.type === 'pointerdown' && toolbar.value?.contains(event.target) && event.target.closest('.drop-wrap')) return
  saveMenu.value = false
  previewMenu.value = false
}
onMounted(() => {
  document.addEventListener('pointerdown', closeMenus)
  document.addEventListener('keydown', closeMenus)
})
const aiLogo = `${import.meta.env.BASE_URL}jimu-screen/ai.png`
let hideTimer = null

function showGroup(group, index) {
  clearTimeout(hideTimer)
  activeGroup.value = group
  activeIndex.value = index
}

function keepOpen() {
  clearTimeout(hideTimer)
}

function scheduleHide() {
  clearTimeout(hideTimer)
  hideTimer = setTimeout(() => { activeGroup.value = null }, 130)
}

function add(item) {
  emit('add', item)
  activeGroup.value = null
}
onBeforeUnmount(() => {
  clearTimeout(hideTimer)
  document.removeEventListener('pointerdown', closeMenus)
  document.removeEventListener('keydown', closeMenus)
})
</script>

<template>
  <header ref="toolbar" class="jimu-toolbar">
    <div class="tool-groups">
      <button
        v-for="(group, index) in groups"
        :key="group.key"
        class="component-trigger"
        :class="{ active: activeGroup?.key === group.key }"
        :title="group.label"
        :aria-label="group.label"
        @mouseenter="showGroup(group, index)"
        @mouseleave="scheduleHide"
        @focus="showGroup(group, index)"
        @click="showGroup(group, index)"
      >
        <i :class="group.icon"></i><span class="down-arrow"></span>
      </button>
      <span class="toolbar-divider"></span>
      <div class="drop-wrap">
        <button class="icon-tool" title="保存选项" aria-label="保存选项" :aria-expanded="saveMenu" @click="saveMenu = !saveMenu; previewMenu = false"><i class="fa-solid fa-database"></i></button>
        <div v-if="saveMenu" class="tiny-menu save-menu">
          <button :disabled="saving" @click="emit('save'); saveMenu = false">保存</button>
          <button v-if="can('dashboards.export')" @click="emit('save-as'); saveMenu = false">导出配置</button>
        </div>
      </div>
      <button class="icon-tool" title="保存" aria-label="保存报表" :disabled="saving" @click="emit('save')"><i class="fa-regular fa-floppy-disk"></i></button>
    </div>

    <div class="center-tools">
      <button v-if="can('dashboards.execute')" class="ai-generate-tool" title="AI 生成" @click="emit('ai')">
        <img class="ai-logo" :src="aiLogo" alt="">
        <span>AI 生成</span>
      </button>
      <div class="drop-wrap">
        <button class="icon-tool preview-tool" title="预览" aria-label="预览选项" :aria-expanded="previewMenu" @click="previewMenu = !previewMenu; saveMenu = false"><i class="fa-regular fa-eye"></i></button>
        <div v-if="previewMenu" class="tiny-menu preview-menu">
          <button @click="previewMenu = false; emit('preview', 'new')">新窗口</button>
          <button @click="previewMenu = false; emit('preview', 'current')">本窗口</button>
        </div>
      </div>
      <button class="icon-tool" title="撤销" aria-label="撤销" :disabled="!canUndo" @click="emit('undo')"><i aria-hidden="true" class="fa-solid fa-rotate-left"></i></button>
      <button class="icon-tool" title="重做" aria-label="重做" :disabled="!canRedo" @click="emit('redo')"><i aria-hidden="true" class="fa-solid fa-rotate-right"></i></button>
    </div>

    <div class="right-tools">
      <span class="save-state" role="status" :class="{ dirty }">{{ saving ? '保存中…' : dirty ? '有未保存修改' : '修改已保存' }}</span>
      <button v-if="hasReport" title="版本历史" aria-label="版本历史" @click="emit('history')"><i class="fa-solid fa-clock-rotate-left"></i><span>版本</span></button>
      <button v-if="can('dashboards.publish')" :disabled="saving" :title="status === 'published' ? '撤回为草稿' : '发布报表'" :aria-label="status === 'published' ? '撤回为草稿' : '发布报表'" @click="emit('publish')"><i class="fa-solid fa-upload"></i><span>{{ status === 'published' ? '撤回' : '发布' }}</span></button>
      <button title="设计器帮助" aria-label="设计器帮助" @click="emit('help')"><i class="fa-regular fa-circle-question"></i><span>帮助</span></button>
      <button v-if="can('datasources.view')" title="数据源" aria-label="数据源" @click="emit('datasource')"><i class="fa-solid fa-database"></i><span>数据源</span></button>
      <button v-if="can('datasources.view')" title="数据集" aria-label="数据集" @click="emit('dataset')"><i class="fa-solid fa-table-list"></i><span>数据集</span></button>
      <button title="返回报表列表" aria-label="返回报表列表" @click="emit('close')"><i class="fa-solid fa-xmark"></i><span>关闭</span></button>
    </div>

    <JimuComponentLibrary
      v-if="activeGroup"
      :group="activeGroup"
      :anchor-left="16 + activeIndex * 44"
      @add="add"
      @keep-open="keepOpen"
      @leave="scheduleHide"
    />
  </header>
</template>

<style scoped>
.save-state { font-size: 12px; white-space: nowrap; color: #a8c9b2; }
.save-state.dirty { color: #ffd28a; }
.jimu-toolbar {
  position: relative;
  z-index: 1100;
  display: grid;
  grid-template-columns: auto auto 1fr;
  align-items: center;
  height: 45px;
  border-bottom: 1px solid #15181c;
  background: #212528;
  box-shadow: 0 2px 7px rgba(0, 0, 0, .28);
  color: #9fa6ad;
}

.tool-groups,
.center-tools,
.right-tools {
  display: flex;
  align-items: center;
  height: 100%;
}

.component-trigger {
  position: relative;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 11px;
  width: 44px;
  height: 45px;
  margin-right: 0;
  border: 0;
  background: transparent;
  color: #8f979e;
  font-size: 17px;
  cursor: default;
}

.component-trigger:hover,
.component-trigger.active { color: #e1e5e9; background: #272c31; }

.down-arrow {
  width: 6px;
  height: 6px;
  border-right: 1px solid currentColor;
  border-bottom: 1px solid currentColor;
  transform: rotate(45deg) translateY(-2px);
}

.toolbar-divider {
  width: 1px;
  height: 24px;
  margin: 0 8px;
  background: #70757a;
}

.icon-tool,
.right-tools button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  height: 32px;
  padding: 0 12px;
  border: 0;
  background: transparent;
  color: #929aa2;
  cursor: pointer;
}

.icon-tool { width: 47px; padding: 0; font-size: 17px; }
.icon-tool:hover,
.right-tools button:hover { color: #e5e9ed; background: #292e33; }
.icon-tool:disabled { color: #4b5157; cursor: default; background: transparent; }

.center-tools { gap: 0; }
.center-tools .drop-wrap,
.center-tools .preview-tool { width: 50px; }

.ai-generate-tool {
  display: inline-flex;
  align-items: center;
  gap: 7px;
  height: 32px;
  padding: 0 12px 0 9px;
  border: 1px solid #366d98;
  background: #142d43;
  color: #bde9ff;
  font-size: 12px;
  cursor: pointer;
}

.ai-generate-tool:hover {
  border-color: #54c7ff;
  background: #183a55;
  color: #fff;
  box-shadow: 0 0 14px rgba(45, 183, 255, .22);
}

.ai-logo {
  width: 30px;
  height: 18px;
  object-fit: contain;
}

.right-tools { justify-content: flex-end; padding-right: 24px; }
.right-tools button { font-size: 13px; }
.right-tools i { font-size: 14px; }
.right-tools button { width:auto;white-space:nowrap;padding:0 10px; }

.drop-wrap {
  position: relative;
  display: flex;
  align-items: center;
  height: 45px;
}

.tiny-menu {
  position: absolute;
  top: 42px;
  z-index: 1300;
  width: 88px;
  padding: 5px 0;
  border: 1px solid #353a42;
  background: #20242a;
  box-shadow: 0 8px 22px rgba(0, 0, 0, .42);
}

.save-menu { left: 0; }
.preview-menu { left: -18px; }

.tiny-menu button {
  width: 100%;
  height: 32px;
  border: 0;
  background: transparent;
  color: #e2e5e8;
  font-size: 13px;
  cursor: pointer;
}

.tiny-menu button:hover { background: #30353c; }
button:focus-visible { outline:2px solid #89caff;outline-offset:-2px; }
@media(max-width:1300px) { .right-tools { padding-right:8px; }.right-tools button { width:38px;padding:0; }.right-tools button span { display:none; } }
@media(max-width:800px) { .jimu-toolbar { height:90px;grid-template-columns:1fr auto;grid-template-rows:45px 45px; }.tool-groups { grid-column:1/-1; }.right-tools { grid-column:2;grid-row:2; }.center-tools { grid-column:1;grid-row:2; }.component-trigger { width:36px; }.tool-groups .icon-tool { width:38px; } }
</style>
