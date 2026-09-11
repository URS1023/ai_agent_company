<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import JimuWidgetRenderer from './JimuWidgetRenderer.vue'
import { normalizeDashboardTheme, panelStyle } from '@/utils/dashboardTheme'

const props = defineProps({
  page: { type: Object, required: true },
  widgets: { type: Array, required: true },
  selectedId: { type: String, default: '' },
  selectedIds: { type: Array, default: () => [] },
  zoom: { type: Number, default: 0.72 },
  preview: { type: Boolean, default: false },
  canPaste: { type: Boolean, default: false }
})

const emit = defineEmits([
  'select',
  'update-widget',
  'update-widgets',
  'area-select',
  'interaction-start',
  'interaction-end',
  'context-action',
  'drilldown',
  'drop-component',
  'zoom-change',
  'fit'
])

const viewportRef = ref(null)
const hoveredId = ref('')
const areaSelection = reactive({ visible: false, x: 0, y: 0, w: 0, h: 0 })
const contextMenu = reactive({ visible: false, x: 0, y: 0, id: '', stageX: 0, stageY: 0, blank: false })
const WORKSPACE_PADDING_X = 360
const WORKSPACE_PADDING_Y = 320
let pointerAction = null

const stageWidth = computed(() => Number(props.page.design?.width || 1920))
const stageHeight = computed(() => Number(props.page.design?.height || 1080))
const sortedWidgets = computed(() => [...props.widgets].sort((left, right) => (left.orderNum || 0) - (right.orderNum || 0)))
const contextWidget = computed(() => props.widgets.find(widget => widget.id === contextMenu.id) || null)
const pageTheme = computed(() => normalizeDashboardTheme(props.page))
const isAiVisualStage = computed(() => props.page.visualMode === 'ai')
const isReferenceCommandLayout = computed(() => props.widgets.some(widget =>
  String(widget.layoutSignature || '').startsWith('reference-command-three-column')
))
const focusPoint = computed(() => {
  const focus = props.widgets.find(widget => widget.visualRole === 'primary-insight')
  if (!focus) return { x: 52, y: 46 }
  return {
    x: Math.max(12, Math.min(88, ((Number(focus.x || 0) + Number(focus.w || 0) / 2) / stageWidth.value) * 100)),
    y: Math.max(18, Math.min(82, ((Number(focus.y || 0) + Number(focus.h || 0) / 2) / stageHeight.value) * 100))
  }
})
const contextMenuItems = computed(() => {
  if (contextMenu.blank) {
    return [{ action: 'paste', label: '\u5728\u8fd9\u7c98\u8d34', disabled: !props.canPaste }]
  }
  if (contextWidget.value?.locked) {
    return [{ action: 'lock', label: '\u89e3\u9501', shortcut: 'Ctrl+L' }]
  }
  const groupItem = contextWidget.value?.groupId
    ? { action: 'ungroup', label: '\u62c6\u5206' }
    : { action: 'group', label: '\u7ec4\u5408', disabled: props.selectedIds.length < 2 }
  return [
    groupItem,
    { action: 'modify-group', label: '\u4fee\u6539\u7ec4\u5408', shortcut: 'Ctrl+M', disabled: !contextWidget.value?.groupId },
    { type: 'line' },
    { action: 'top', label: '\u7f6e\u9876', shortcut: 'Ctrl+\u2191' },
    { action: 'bottom', label: '\u7f6e\u5e95', shortcut: 'Ctrl+\u2193' },
    { action: 'move-up', label: '\u4e0a\u79fb\u4e00\u5c42', shortcut: 'Shift+\u2191' },
    { action: 'move-down', label: '\u4e0b\u79fb\u4e00\u5c42', shortcut: 'Shift+\u2193' },
    { type: 'line' },
    { action: 'copy', label: '\u590d\u5236', shortcut: 'Ctrl+C' },
    { action: 'cut', label: '\u526a\u5207', shortcut: 'Ctrl+X' },
    { action: 'paste', label: '\u7c98\u8d34', shortcut: 'Ctrl+V', disabled: !props.canPaste },
    { type: 'line' },
    { action: 'preview', label: '\u9884\u89c8' },
    { action: 'drilldown', label: '\u67e5\u770b\u6570\u636e\u660e\u7ec6', disabled: contextWidget.value?.type === 'decoration' },
    { action: 'edit', label: '\u7f16\u8f91' },
    { action: 'clear-linkage', label: '\u6e05\u7a7a\u8054\u52a8', disabled: !contextWidget.value?.config?.linkageConfig?.length },
    { type: 'line' },
    { action: 'remove', label: '\u5220\u9664', shortcut: 'Delete' },
    { type: 'line' },
    { action: 'lock', label: '\u9501\u5b9a', shortcut: 'Ctrl+L' }
  ]
})
const virtualWidth = computed(() => stageWidth.value * props.zoom + WORKSPACE_PADDING_X)
const virtualHeight = computed(() => stageHeight.value * props.zoom + WORKSPACE_PADDING_Y)
const xMarks = computed(() => Array.from(
  { length: Math.floor(stageWidth.value / 100) + 1 },
  (_, index) => index * 100
))
const yMarks = computed(() => Array.from(
  { length: Math.floor(stageHeight.value / 100) + 1 },
  (_, index) => index * 100
))

const virtualWorkspaceStyle = computed(() => props.preview ? undefined : ({
  width: `${virtualWidth.value}px`,
  height: `${virtualHeight.value}px`
}))

const scaledStageStyle = computed(() => ({
  width: `${stageWidth.value * props.zoom}px`,
  height: `${stageHeight.value * props.zoom}px`
}))

const stageStyle = computed(() => {
  const theme = pageTheme.value
  const themedBackground = [
    `radial-gradient(ellipse 58% 68% at ${focusPoint.value.x}% ${focusPoint.value.y}%, ${theme.ambientPrimary || `${theme.primary}2e`}, transparent 66%)`,
    `radial-gradient(ellipse 52% 44% at 88% 3%, ${theme.ambientSecondary || `${theme.secondary}30`}, transparent 68%)`,
    `radial-gradient(ellipse 38% 42% at 3% 96%, ${theme.accent}24, transparent 70%)`,
    `linear-gradient(138deg, ${theme.backgroundRaised || theme.background} 0%, color-mix(in srgb, ${theme.surfaceLift || theme.panelStrong} 44%, ${theme.background}) 48%, ${theme.background} 100%)`
  ].join(', ')
  const referenceBackground = [
    'radial-gradient(ellipse 52% 48% at 50% 47%, rgba(0, 103, 197, .16), transparent 72%)',
    'radial-gradient(ellipse 34% 28% at 8% 94%, rgba(0, 151, 219, .08), transparent 74%)',
    'linear-gradient(180deg, #010615 0%, #02091b 52%, #031126 100%)'
  ].join(', ')
  const backgroundImage = isAiVisualStage.value
    ? (props.page.backgroundLayers || 'none')
    : (isReferenceCommandLayout.value
    ? referenceBackground
    : (props.page.backgroundImage
        ? `url(${assetUrl(props.page.backgroundImage)})`
        : ((props.page.theme || props.page.themeId) ? themedBackground : `url(${assetUrl('/jimu-screen/bg4.png')})`)))
  return {
    width: `${stageWidth.value}px`,
    height: `${stageHeight.value}px`,
    transform: `scale(${props.zoom})`,
    backgroundColor: props.page.backgroundColor || theme.background,
    backgroundImage,
    backgroundBlendMode: isAiVisualStage.value
      ? (props.page.backgroundBlendMode || 'normal')
      : (isReferenceCommandLayout.value ? 'screen, screen, normal' : 'screen, screen, screen, normal'),
    '--stage-primary': theme.primary,
    '--stage-secondary': theme.secondary,
    '--stage-accent': theme.accent,
    '--stage-grid': theme.grid,
    '--stage-surface-lift': theme.surfaceLift || theme.panelStrong
  }
})
const areaSelectionStyle = computed(() => ({
  left: `${areaSelection.x}px`,
  top: `${areaSelection.y}px`,
  width: `${areaSelection.w}px`,
  height: `${areaSelection.h}px`
}))

function widgetStyle(widget) {
  const hasGeneratedTheme = Boolean(widget.theme || widget.themeId || widget.panelVariant)
  const visualPanel = panelStyle(widget)
  const themedPanel = hasGeneratedTheme ? visualPanel : {}
  return {
    ...themedPanel,
    width: `${widget.w}px`,
    height: `${widget.h}px`,
    transform: `translate(${widget.x}px, ${widget.y}px) rotate(${widget.rotation || 0}deg)`,
    zIndex: Number(widget.orderNum || 0) + 1,
    '--widget-enter-delay': `${Math.min(720, Math.max(0, Number(widget.orderNum || 0)) * 18)}ms`,
    display: widget.visible === false ? 'none' : undefined,
    borderRadius: visualPanel.borderRadius,
    borderWidth: visualPanel.borderWidth,
    borderStyle: visualPanel.borderStyle,
    boxShadow: visualPanel.boxShadow,
    backdropFilter: visualPanel.backdropFilter,
    WebkitBackdropFilter: visualPanel.WebkitBackdropFilter,
    background: hasGeneratedTheme
      ? themedPanel.background
      : (widget.config?.background && widget.config.background !== '#FFFFFF00' ? widget.config.background : 'transparent'),
    borderColor: hasGeneratedTheme ? themedPanel.borderColor : (widget.config?.borderColor || 'transparent')
  }
}

function classToken(value, fallback) {
  return String(value || fallback).toLowerCase().replace(/[^a-z0-9-]+/g, '-')
}

function assetUrl(url) {
  if (!url) return ''
  if (/^(https?:)?\/\//.test(url) || url.startsWith('data:')) return url
  const normalized = url
    .replace(/^\/img\/bg\//, '/jimu-screen/')
    .replace(/^\/drag\/list\/core\/img\//, '/jimu-screen/')
  return `${import.meta.env.BASE_URL}${normalized.replace(/^\//, '')}`
}

function startMove(event, widget) {
  if (props.preview) {
    activateWidget(widget)
    return
  }
  if (widget.locked || event.button !== 0) return
  event.preventDefault()
  hideContextMenu()
  if (event.ctrlKey || event.metaKey) {
    emit('select', { id: widget.id, toggle: true })
    return
  }
  const movingIds = props.selectedIds.includes(widget.id) && props.selectedIds.length > 1
    ? props.selectedIds
    : [widget.id]
  if (!props.selectedIds.includes(widget.id)) emit('select', widget.id)
  emit('interaction-start')
  pointerAction = {
    mode: 'move',
    id: widget.id,
    startX: event.clientX,
    startY: event.clientY,
    origins: movingIds.map(id => {
      const item = props.widgets.find(candidate => candidate.id === id)
      return { id, x: item.x, y: item.y, w: item.w, h: item.h }
    })
  }
  bindPointerListeners()
}

function activateWidget(widget) {
  const turnConfig = widget.config?.turnConfig || {}
  if (!turnConfig.url) return
  if (turnConfig.type === '_self') window.location.assign(turnConfig.url)
  else window.open(turnConfig.url, '_blank', 'noopener')
}

function startResize(event, widget, side) {
  if (props.preview || widget.locked || props.selectedIds.length > 1 || event.button !== 0) return
  event.preventDefault()
  event.stopPropagation()
  emit('interaction-start')
  pointerAction = {
    mode: 'resize',
    side,
    id: widget.id,
    startX: event.clientX,
    startY: event.clientY,
    origin: { x: widget.x, y: widget.y, w: widget.w, h: widget.h }
  }
  bindPointerListeners()
}

function startRotate(event, widget) {
  if (props.preview || widget.locked || props.selectedIds.length > 1 || event.button !== 0) return
  event.preventDefault()
  event.stopPropagation()
  const rect = event.currentTarget.closest('.screen-widget').getBoundingClientRect()
  const centerX = rect.left + rect.width / 2
  const centerY = rect.top + rect.height / 2
  emit('interaction-start')
  pointerAction = {
    mode: 'rotate',
    id: widget.id,
    centerX,
    centerY,
    startAngle: Math.atan2(event.clientY - centerY, event.clientX - centerX) * 180 / Math.PI,
    originRotation: Number(widget.rotation || 0)
  }
  bindPointerListeners()
}

function startAreaSelection(event) {
  if (props.preview || event.button !== 0) return
  event.preventDefault()
  hideContextMenu()
  const point = stagePoint(event)
  emit('select', '')
  areaSelection.visible = true
  areaSelection.x = point.x
  areaSelection.y = point.y
  areaSelection.w = 0
  areaSelection.h = 0
  pointerAction = {
    mode: 'area',
    startX: event.clientX,
    startY: event.clientY,
    startStageX: point.x,
    startStageY: point.y
  }
  bindPointerListeners()
}

function bindPointerListeners() {
  window.addEventListener('pointermove', handlePointerMove)
  window.addEventListener('pointerup', stopPointerAction, { once: true })
}

function handlePointerMove(event) {
  if (!pointerAction) return
  const dx = (event.clientX - pointerAction.startX) / props.zoom
  const dy = (event.clientY - pointerAction.startY) / props.zoom

  if (pointerAction.mode === 'area') {
    const endX = pointerAction.startStageX + dx
    const endY = pointerAction.startStageY + dy
    areaSelection.x = Math.round(Math.min(pointerAction.startStageX, endX))
    areaSelection.y = Math.round(Math.min(pointerAction.startStageY, endY))
    areaSelection.w = Math.round(Math.abs(dx))
    areaSelection.h = Math.round(Math.abs(dy))
    const right = areaSelection.x + areaSelection.w
    const bottom = areaSelection.y + areaSelection.h
    const ids = props.widgets
      .filter(widget => widget.visible !== false && !widget.locked)
      .filter(widget => areaSelection.x < widget.x && right > widget.x + widget.w && areaSelection.y < widget.y && bottom > widget.y + widget.h)
      .map(widget => widget.id)
    emit('area-select', ids)
    return
  }

  if (pointerAction.mode === 'rotate') {
    const angle = Math.atan2(event.clientY - pointerAction.centerY, event.clientX - pointerAction.centerX) * 180 / Math.PI
    let rotation = pointerAction.originRotation + angle - pointerAction.startAngle
    if (event.shiftKey) rotation = Math.round(rotation / 15) * 15
    emit('update-widget', pointerAction.id, { rotation: Math.round((rotation + 360) % 360) })
    return
  }

  if (pointerAction.mode === 'move') {
    emit('update-widgets', pointerAction.origins.map(origin => ({
      id: origin.id,
      patch: {
        x: Math.max(0, Math.round(origin.x + dx)),
        y: Math.max(0, Math.round(origin.y + dy))
      }
    })))
    return
  }

  const origin = pointerAction.origin
  let x = origin.x
  let y = origin.y
  let width = origin.w
  let height = origin.h
  const side = pointerAction.side
  if (side.includes('right')) width = origin.w + dx
  if (side.includes('bottom')) height = origin.h + dy
  if (side.includes('left')) {
    x = origin.x + dx
    width = origin.w - dx
  }
  if (side.includes('top')) {
    y = origin.y + dy
    height = origin.h - dy
  }

  if (width < 44) {
    if (side.includes('left')) x -= 44 - width
    width = 44
  }
  if (height < 30) {
    if (side.includes('top')) y -= 30 - height
    height = 30
  }

  emit('update-widget', pointerAction.id, {
    x: Math.round(x),
    y: Math.round(y),
    w: Math.round(width),
    h: Math.round(height)
  })
}

function stopPointerAction() {
  if (!pointerAction) return
  const mode = pointerAction.mode
  pointerAction = null
  window.removeEventListener('pointermove', handlePointerMove)
  window.removeEventListener('pointerup', stopPointerAction)
  if (mode === 'area') areaSelection.visible = false
  else emit('interaction-end')
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, value))
}

function changeZoom(value) {
  emit('zoom-change', clamp(Number(value), 0.1, 1))
}

function showHoverGuides(widget) {
  const rotation = Math.abs(Number(widget.rotation || 0) % 90)
  return !props.preview && hoveredId.value === widget.id && (rotation < 2 || rotation >= 88)
}

function stagePoint(event) {
  const stage = event.currentTarget.closest?.('.screen-stage') || event.currentTarget
  const rect = stage.getBoundingClientRect()
  return {
    x: Math.round((event.clientX - rect.left) / props.zoom),
    y: Math.round((event.clientY - rect.top) / props.zoom)
  }
}

function dropComponent(event) {
  if (props.preview) return
  const raw = event.dataTransfer?.getData('application/x-lynx-jimu-component')
  if (!raw) return
  try {
    emit('drop-component', { item: JSON.parse(raw), point: stagePoint(event) })
  } catch {
    // Ignore drops from outside the local component library.
  }
}

function openContextMenu(event, id = '', blank = false) {
  if (props.preview) return
  const point = stagePoint(event)
  contextMenu.visible = true
  contextMenu.x = Math.min(event.clientX, window.innerWidth - 210)
  contextMenu.y = Math.min(event.clientY, window.innerHeight - 430)
  contextMenu.id = id
  contextMenu.stageX = point.x
  contextMenu.stageY = point.y
  contextMenu.blank = blank
}

function showWidgetContext(event, widget) {
  emit('select', widget.id)
  openContextMenu(event, widget.id, false)
}

function showStageContext(event) {
  openContextMenu(event, '', true)
}

function hideContextMenu() {
  contextMenu.visible = false
}

function runContextAction(item) {
  if (!item.action || item.disabled) return
  emit('context-action', {
    action: item.action,
    id: contextMenu.id,
    stageX: contextMenu.stageX,
    stageY: contextMenu.stageY,
    blank: contextMenu.blank
  })
  hideContextMenu()
}

function handleWindowKeydown(event) {
  if (event.key === 'Escape') hideContextMenu()
}

onMounted(() => {
  window.addEventListener('pointerdown', hideContextMenu)
  window.addEventListener('keydown', handleWindowKeydown)
})

onBeforeUnmount(() => {
  stopPointerAction()
  window.removeEventListener('pointerdown', hideContextMenu)
  window.removeEventListener('keydown', handleWindowKeydown)
})
</script>

<template>
  <section ref="viewportRef" class="canvas-viewport" :class="{ 'is-preview': preview }" @scroll="hideContextMenu">
    <div class="canvas-content" :class="{ 'with-rulers': !preview }" :style="virtualWorkspaceStyle">
      <div v-if="!preview" class="ruler-corner"><i class="fa-regular fa-eye"></i></div>
      <div v-if="!preview" class="ruler ruler-x" :style="{ width: `${stageWidth * zoom}px` }">
        <span v-for="mark in xMarks" :key="mark" :style="{ left: `${mark * zoom}px` }">{{ mark }}</span>
      </div>
      <div v-if="!preview" class="ruler ruler-y" :style="{ height: `${stageHeight * zoom}px` }">
        <span v-for="mark in yMarks" :key="mark" :style="{ top: `${mark * zoom}px` }">{{ mark }}</span>
      </div>

      <div class="scaled-stage" :style="scaledStageStyle">
        <main
          class="screen-stage"
          :class="[
            { 'reference-command-layout': isReferenceCommandLayout },
            { 'ai-visual-stage': isAiVisualStage },
            `template-${classToken(page.templateId, 'unassigned')}`
          ]"
          :style="stageStyle"
          @dragover.prevent
          @drop.prevent="dropComponent"
          @pointerdown.self="startAreaSelection"
          @contextmenu.self.prevent="showStageContext"
        >
          <div v-if="areaSelection.visible" class="area-selection" :style="areaSelectionStyle"></div>
          <article
            v-for="widget in sortedWidgets"
            :key="widget.id"
            class="screen-widget"
            :class="[
              {
                selected: (selectedIds.includes(widget.id) || widget.id === selectedId) && !preview,
                locked: widget.locked,
                hidden: widget.visible === false,
                'themed-widget': widget.visualMode !== 'ai' && (widget.theme || widget.themeId || widget.panelVariant),
                'ai-visual-widget': widget.visualMode === 'ai'
              },
              `variant-${classToken(widget.panelVariant, 'legacy')}`,
              `role-${classToken(widget.visualRole, 'content')}`
            ]"
            :style="widgetStyle(widget)"
            :data-component="widget.component"
            :data-model-key="widget.modelKey || ''"
            :data-theme="widget.themeId || ''"
            :data-role="widget.visualRole || ''"
            :title="widget.componentName"
            :tabindex="preview ? -1 : 0"
            @focus="emit('select', widget.id)"
            @mouseenter="hoveredId = widget.id"
            @mouseleave="hoveredId = ''"
            @pointerdown="startMove($event, widget)"
            @contextmenu.stop.prevent="showWidgetContext($event, widget)"
          >
            <div v-if="showHoverGuides(widget)" class="widget-extra">
              <span class="widget-coordinate">{{ widget.x }} , {{ widget.y }}</span>
              <i class="widget-guide vertical"></i>
              <i class="widget-guide horizontal"></i>
            </div>
            <JimuWidgetRenderer
              :widget="widget"
              :interactive="preview"
              @drilldown="emit('drilldown', { widget, point: $event })"
            />
            <template v-if="widget.id === selectedId && selectedIds.length <= 1 && !preview && !widget.locked">
              <button class="rotate-handle" title="Rotate" aria-label="Rotate" @pointerdown="startRotate($event, widget)">
                <i class="fa-solid fa-rotate"></i>
              </button>
              <button
                v-for="side in ['top', 'top-right', 'right', 'bottom-right', 'bottom', 'bottom-left', 'left', 'top-left']"
                :key="side"
                class="resize-dot"
                :class="`dot-${side}`"
                :aria-label="`${side} resize`"
                @pointerdown="startResize($event, widget, side)"
              ></button>
            </template>
          </article>
        </main>
      </div>
    </div>

    <div v-if="!preview" class="zoom-area">
      <button title="适配画布" @click="emit('fit')"><i class="fa-solid fa-expand"></i></button>
      <button title="缩小" @click="changeZoom(zoom - .02)"><i class="fa-solid fa-magnifying-glass-minus"></i></button>
      <input type="range" min="0.1" max="1" step="0.01" :value="zoom" @input="changeZoom($event.target.value)">
      <button title="放大" @click="changeZoom(zoom + .02)"><i class="fa-solid fa-magnifying-glass-plus"></i></button>
      <label><input type="number" min="10" max="100" :value="Math.round(zoom * 100)" @change="changeZoom($event.target.value / 100)"><span>%</span></label>
    </div>

    <Teleport to="body">
      <nav
        v-if="contextMenu.visible && !preview"
        class="canvas-context-menu"
        :style="{ left: `${contextMenu.x}px`, top: `${contextMenu.y}px` }"
        @pointerdown.stop
        @contextmenu.prevent
      >
        <ul>
          <li v-for="(item, index) in contextMenuItems" :key="`${item.action || item.type}-${index}`" :class="item.type === 'line' ? 'line' : 'box'">
            <template v-if="item.type !== 'line'">
              <button :data-action="item.action" :disabled="item.disabled" @click="runContextAction(item)">
                <span class="name">{{ item.label }}</span>
                <span v-if="item.shortcut" class="shortcut">{{ item.shortcut }}</span>
              </button>
            </template>
          </li>
        </ul>
      </nav>
    </Teleport>
  </section>
</template>

<style scoped>
.canvas-viewport {
  position: relative;
  min-width: 0;
  height: 100%;
  overflow: auto;
  background-color: #282e35;
  scrollbar-color: #171b20 #2a3037;
  scrollbar-width: thin;
}

.canvas-content {
  position: relative;
  min-width: 100%;
  min-height: 100%;
  padding: 36px 36px 110px 54px;
  box-sizing: border-box;
  background-image: radial-gradient(circle, rgba(117, 142, 160, .72) 1px, transparent 1.2px);
  background-size: 16px 16px;
}

.canvas-content.with-rulers { padding: 36px 36px 110px 54px; }

.scaled-stage {
  position: relative;
  margin-left: 0;
  transform-origin: top left;
}

.screen-stage {
  position: relative;
  isolation: isolate;
  overflow: visible;
  background-position: center;
  background-size: cover;
  box-shadow: 0 0 0 1px rgba(255, 255, 255, .025), 0 40px 120px rgba(0, 0, 0, .5);
  transform-origin: top left;
}

.screen-stage::before {
  position: absolute;
  inset: 0;
  z-index: 0;
  background-image:
    linear-gradient(color-mix(in srgb, var(--stage-grid, #21364a) 16%, transparent) 1px, transparent 1px),
    linear-gradient(90deg, color-mix(in srgb, var(--stage-grid, #21364a) 13%, transparent) 1px, transparent 1px),
    linear-gradient(115deg, transparent 0 47%, color-mix(in srgb, var(--stage-primary, #77e6ff) 4%, transparent) 50%, transparent 53%);
  background-size: 76px 76px, 76px 76px, 100% 100%;
  mask-image: linear-gradient(to bottom, rgba(0, 0, 0, .55), transparent 92%);
  content: '';
  pointer-events: none;
}

.screen-stage::after {
  position: absolute;
  inset: 0;
  z-index: 0;
  background:
    linear-gradient(105deg, transparent 0 12%, color-mix(in srgb, var(--stage-primary) 7%, transparent) 12.2% 12.45%, transparent 12.7% 78%, color-mix(in srgb, var(--stage-accent) 6%, transparent) 78.2% 78.45%, transparent 78.7%),
    radial-gradient(circle at 72% 36%, color-mix(in srgb, var(--stage-accent) 10%, transparent) 0 1px, transparent 2px),
    repeating-linear-gradient(118deg, transparent 0 24px, color-mix(in srgb, var(--stage-secondary) 2.5%, transparent) 25px 26px);
  background-size: 100% 100%, 62px 62px, 100% 100%;
  opacity: .72;
  content: '';
  pointer-events: none;
}

.screen-stage.ai-visual-stage::before,
.screen-stage.ai-visual-stage::after {
  display: none;
}

.screen-stage.template-territory-industry-map::before,
.screen-stage.template-commodity-trade-map::before,
.screen-stage.template-regional-operation-map::before,
.screen-stage.template-factory-monitoring-center::before {
  background-image:
    linear-gradient(rgba(59, 123, 155, .13) 1px, transparent 1px),
    linear-gradient(90deg, rgba(59, 123, 155, .12) 1px, transparent 1px),
    radial-gradient(circle at 50% 48%, rgba(54, 207, 225, .07), transparent 44%);
  background-size: 36px 36px, 36px 36px, 100% 100%;
  opacity: .72;
}

.screen-stage.template-territory-industry-map::after {
  background:
    repeating-radial-gradient(ellipse 48% 34% at 57% 54%, transparent 0 33px, rgba(75, 135, 160, .055) 34px 35px, transparent 36px 48px),
    repeating-radial-gradient(ellipse 32% 44% at 28% 68%, transparent 0 27px, rgba(59, 123, 155, .045) 28px 29px, transparent 30px 43px),
    linear-gradient(104deg, transparent 0 12%, rgba(73, 161, 194, .045) 12.15% 12.3%, transparent 12.45% 78%, rgba(73, 161, 194, .04) 78.15% 78.3%, transparent 78.45%);
  opacity: .62;
}

.screen-stage.template-production-live-control::before,
.screen-stage.template-vehicle-service-matrix::before,
.screen-stage.template-enterprise-sales-annual::before,
.screen-stage.template-order-management-purple::before {
  background-image:
    linear-gradient(rgba(118, 109, 238, .09) 1px, transparent 1px),
    linear-gradient(90deg, rgba(86, 112, 236, .08) 1px, transparent 1px),
    radial-gradient(circle at 50% 26%, rgba(108, 93, 255, .14), transparent 42%);
  background-size: 54px 54px, 54px 54px, 100% 100%;
  opacity: .68;
}

.screen-stage.template-annual-black-gold::before {
  background-image:
    linear-gradient(rgba(202, 158, 77, .065) 1px, transparent 1px),
    linear-gradient(90deg, rgba(202, 158, 77, .055) 1px, transparent 1px),
    radial-gradient(ellipse at 50% 0, rgba(214, 164, 77, .11), transparent 54%);
  background-size: 64px 64px, 64px 64px, 100% 100%;
}
.screen-stage.template-annual-black-gold::after {
  background: linear-gradient(115deg, transparent 18%, rgba(209, 162, 77, .045) 18.2% 18.35%, transparent 18.5% 82%, rgba(209, 162, 77, .04) 82.2% 82.35%, transparent 82.5%);
}

.screen-stage.template-finance-light-dashboard::before {
  background-image:
    linear-gradient(rgba(76, 151, 176, .055) 1px, transparent 1px),
    linear-gradient(90deg, rgba(76, 151, 176, .045) 1px, transparent 1px);
  background-size: 48px 48px;
  opacity: .3;
}
.screen-stage.template-finance-light-dashboard::after { display: none; }

.screen-stage.template-workshop-production-orange::before,
.screen-stage.template-equipment-digital-ops::before {
  background-image:
    linear-gradient(rgba(43, 103, 153, .09) 1px, transparent 1px),
    linear-gradient(90deg, rgba(43, 103, 153, .08) 1px, transparent 1px),
    linear-gradient(120deg, transparent 0 70%, rgba(255, 112, 72, .035) 70.2% 70.4%, transparent 70.6%);
  background-size: 52px 52px, 52px 52px, 100% 100%;
}

.screen-stage.reference-command-layout::before {
  background-image:
    linear-gradient(rgba(13, 82, 132, .10) 1px, transparent 1px),
    linear-gradient(90deg, rgba(13, 82, 132, .08) 1px, transparent 1px),
    linear-gradient(118deg, transparent 0 49%, rgba(12, 140, 214, .035) 50%, transparent 51%);
  background-size: 96px 96px, 96px 96px, 100% 100%;
  mask-image: linear-gradient(to bottom, rgba(0, 0, 0, .18), transparent 88%);
  opacity: .42;
}

.screen-stage.reference-command-layout::after {
  background:
    radial-gradient(ellipse 42% 24% at 50% 92%, rgba(0, 126, 224, .10), transparent 72%),
    linear-gradient(112deg, transparent 0 13%, rgba(0, 191, 255, .025) 13.1% 13.25%, transparent 13.35% 84%, rgba(0, 191, 255, .025) 84.1% 84.25%, transparent 84.35%);
  opacity: .7;
}

.screen-widget {
  position: absolute;
  top: 0;
  left: 0;
  border: 1px solid transparent;
  transform-origin: center;
  user-select: none;
  outline: none;
}

.screen-widget.themed-widget:not([data-role*="rail"]):not([data-role*="frame"]):not([data-role*="spine"]):not([data-role*="bracket"]):not([data-role*="lamp"]):not([data-role*="pulse"]):not([data-role*="decoration"]) {
  border-color: color-mix(in srgb, var(--widget-surface) 42%, var(--widget-grid));
  background-image:
    linear-gradient(135deg, rgba(255, 255, 255, .075), transparent 24%),
    radial-gradient(circle at 92% 0, color-mix(in srgb, var(--widget-surface) 18%, transparent), transparent 38%),
    linear-gradient(90deg, color-mix(in srgb, var(--widget-surface) 7%, transparent), transparent 42%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, .09),
    inset 0 0 44px color-mix(in srgb, var(--widget-surface) 5%, transparent),
    0 14px 36px rgba(0, 0, 0, .18),
    0 0 22px color-mix(in srgb, var(--widget-surface) 7%, transparent);
  backdrop-filter: blur(12px) saturate(1.18);
  animation: dashboard-widget-enter .72s cubic-bezier(.2, .78, .22, 1) var(--widget-enter-delay, 0ms) backwards;
}

.screen-widget[class*="variant-reference-"] {
  border-color: color-mix(in srgb, var(--widget-primary) 30%, var(--widget-grid)) !important;
  background-image: linear-gradient(180deg, rgba(3, 20, 45, .94), rgba(1, 8, 24, .96)) !important;
  box-shadow: inset 0 0 18px rgba(0, 112, 190, .035) !important;
  backdrop-filter: none !important;
}

.screen-widget[class*="variant-showcase-"] {
  border: 0 !important;
  border-radius: 0 !important;
  background: transparent !important;
  background-image: none !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
}

.screen-widget.variant-showcase-template-01-section-band {
  border-left: 7px solid rgba(192, 217, 236, .92) !important;
  background: linear-gradient(90deg, rgba(76, 113, 145, .72), rgba(48, 76, 101, .45) 64%, rgba(19, 37, 52, .08)) !important;
}

.screen-widget.variant-reference-hero-panel {
  border-color: rgba(12, 106, 166, .66) !important;
  box-shadow: inset 0 0 20px rgba(0, 126, 224, .035) !important;
}

.screen-widget.variant-wisdom-brain {
  overflow: visible;
  border: 0 !important;
  background: transparent !important;
  background-image: none !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
}

.screen-widget.variant-reference-exact {
  border: 0 !important;
  background: transparent !important;
  background-image: none !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
}

.screen-widget.variant-hero-lens,
.screen-widget.variant-metric-spotlight {
  border-color: color-mix(in srgb, var(--widget-primary) 58%, var(--widget-grid)) !important;
  background-image:
    radial-gradient(ellipse at 20% 12%, color-mix(in srgb, var(--widget-primary) 20%, transparent), transparent 48%),
    radial-gradient(ellipse at 88% 92%, color-mix(in srgb, var(--widget-accent) 12%, transparent), transparent 42%),
    linear-gradient(145deg, rgba(255, 255, 255, .08), transparent 34%);
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, .065),
    inset 0 0 80px color-mix(in srgb, var(--widget-primary) 3%, transparent),
    0 30px 80px rgba(0, 0, 0, .35),
    0 0 58px color-mix(in srgb, var(--widget-glow) 7%, transparent) !important;
}

.screen-widget.variant-alert-signal,
.screen-widget.variant-metric-alert {
  background-image:
    linear-gradient(120deg, color-mix(in srgb, var(--widget-danger) 9%, transparent), transparent 52%),
    linear-gradient(145deg, rgba(255, 255, 255, .025), transparent);
  border-color: color-mix(in srgb, var(--widget-danger) 34%, var(--widget-grid)) !important;
}

.screen-widget.variant-command-title {
  border-color: transparent !important;
  background-image: none !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
}

.screen-widget.variant-reference-command-title,
.screen-widget.variant-reference-status-ribbon,
.screen-widget.variant-reference-time-ribbon,
.screen-widget.variant-reference-hero-kpi,
.screen-widget.variant-reference-hex-metric,
.screen-widget.variant-reference-score-strip,
.screen-widget.variant-reference-process-step,
.screen-widget.variant-reference-region-label {
  border: 0 !important;
  background: transparent !important;
  background-image: none !important;
  box-shadow: none !important;
  backdrop-filter: none !important;
}

.screen-widget.variant-reference-command-title,
.screen-widget.variant-reference-process-step,
.screen-widget.variant-reference-region-label { overflow: visible; }

.screen-widget.variant-reference-hero-hud {
  border-color: rgba(0, 169, 255, .88) !important;
  background-color: rgba(2, 16, 43, .88) !important;
  box-shadow:
    inset 0 0 32px color-mix(in srgb, var(--widget-primary) 7%, transparent),
    0 0 22px color-mix(in srgb, var(--widget-primary) 18%, transparent) !important;
  backdrop-filter: blur(6px) saturate(1.18) !important;
}

.screen-widget.variant-reference-news-panel,
.screen-widget.variant-reference-ranking-panel,
.screen-widget.variant-reference-radar-panel {
  border-color: rgba(10, 76, 126, .62) !important;
  background-image: linear-gradient(180deg, rgba(3, 18, 42, .96), rgba(1, 8, 24, .97)) !important;
  box-shadow: none !important;
}

.screen-widget.variant-reference-region-frame {
  overflow: visible;
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  filter: none;
}

.screen-widget.variant-status-pill,
.screen-widget.variant-time-capsule {
  border-color: color-mix(in srgb, var(--widget-grid) 46%, transparent) !important;
  background-image: linear-gradient(135deg, rgba(255, 255, 255, .035), transparent 60%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, .045), 0 12px 30px rgba(0, 0, 0, .2);
}

.screen-widget.variant-composition-orbit,
.screen-widget.variant-relation-field {
  background-image:
    radial-gradient(circle at 50% 56%, color-mix(in srgb, var(--widget-accent) 15%, transparent), transparent 48%),
    linear-gradient(145deg, rgba(255, 255, 255, .06), transparent 50%);
}

.screen-widget.variant-detail-stream {
  background-image: linear-gradient(180deg, rgba(255, 255, 255, .024), transparent 34%);
}

.screen-widget[data-role="energy-rail"],
.screen-widget[data-role="section-pulse"],
.screen-widget[data-role="status-lamp"] {
  filter: drop-shadow(0 0 7px var(--widget-primary));
}

.screen-widget[data-role="ambient-decoration"],
.screen-widget[data-role="flow-ribbon"],
.screen-widget[data-role="signal-rail"],
.screen-widget[data-role="focus-orb"] {
  border: 0 !important;
  background: transparent !important;
  box-shadow: none !important;
  pointer-events: none;
}

.screen-widget::before {
  content: '';
  position: absolute;
  inset: -2px;
  z-index: 29;
  border: 1px solid transparent;
  opacity: 0;
  pointer-events: none;
  transition: border-color .16s ease, box-shadow .16s ease, opacity .16s ease;
}

.screen-widget:not(.selected):hover::before,
.screen-widget:not(.selected):focus-visible::before {
  border-color: rgba(114, 197, 248, .78);
  box-shadow: 0 0 0 1px rgba(114, 197, 248, .2), 0 0 16px rgba(52, 157, 224, .22);
  opacity: 1;
  animation: widget-hover-breathe 1.15s ease-in-out infinite alternate;
}

.screen-widget.selected::after {
  content: '';
  position: absolute;
  inset: -2px;
  z-index: 30;
  border: 2px solid #72c5f8;
  box-shadow: 0 0 0 1px rgba(114, 197, 248, .2), 0 0 14px rgba(52, 157, 224, .28);
  animation: widget-focus-pulse .58s cubic-bezier(.22, .75, .25, 1);
  pointer-events: none;
}

.screen-widget.locked { cursor: not-allowed; }
.screen-widget.hidden { display: none; }

.area-selection {
  position: absolute;
  z-index: 1000;
  border: 1px dashed #409eff;
  background: rgba(64, 158, 255, .1);
  pointer-events: none;
}

.widget-extra {
  position: absolute;
  inset: 0;
  z-index: 45;
  pointer-events: none;
}

.widget-coordinate {
  position: absolute;
  top: -4px;
  left: -6px;
  display: block;
  padding: 4px;
  color: #72c5f8;
  font-size: 18px;
  white-space: nowrap;
  transform: translate(-100%, -100%);
}

.widget-guide {
  position: absolute;
  display: block;
  border-color: #72c5f8;
  pointer-events: none;
}

.widget-guide.vertical {
  bottom: 100%;
  left: 0;
  width: 0;
  height: 100000px;
  border-left: 1px dashed #72c5f8;
}

.widget-guide.horizontal {
  top: 0;
  right: 100%;
  width: 100000px;
  height: 0;
  border-top: 1px dashed #72c5f8;
}

.resize-dot {
  position: absolute;
  z-index: 40;
  width: 9px;
  height: 9px;
  padding: 0;
  border: 1px solid #0e7cab;
  border-radius: 50%;
  background: #72c5f8;
}

.rotate-handle {
  position: absolute;
  top: -36px;
  left: 50%;
  z-index: 41;
  display: grid;
  place-items: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 1px solid #0e7cab;
  border-radius: 50%;
  background: #1d2630;
  color: #72c5f8;
  cursor: grab;
  transform: translateX(-50%);
}

.rotate-handle::after {
  content: '';
  position: absolute;
  top: 19px;
  left: 50%;
  width: 1px;
  height: 15px;
  background: #72c5f8;
}

.rotate-handle:active { cursor: grabbing; }
.rotate-handle i { font-size: 10px; }

.dot-top { top: -6px; left: 50%; cursor: n-resize; transform: translateX(-50%); }
.dot-top-right { top: -6px; right: -6px; cursor: ne-resize; }
.dot-right { top: 50%; right: -6px; cursor: e-resize; transform: translateY(-50%); }
.dot-bottom-right { right: -6px; bottom: -6px; cursor: se-resize; }
.dot-bottom { bottom: -6px; left: 50%; cursor: s-resize; transform: translateX(-50%); }
.dot-bottom-left { bottom: -6px; left: -6px; cursor: sw-resize; }
.dot-left { top: 50%; left: -6px; cursor: w-resize; transform: translateY(-50%); }
.dot-top-left { top: -6px; left: -6px; cursor: nw-resize; }

.ruler-corner,
.ruler {
  position: absolute;
  z-index: 60;
  color: #9ca5ad;
  font-size: 10px;
  pointer-events: none;
}

.ruler-corner {
  top: 0;
  left: 0;
  display: grid;
  place-items: center;
  width: 24px;
  height: 24px;
  border-right: 1px solid #161a1f;
  border-bottom: 1px solid #161a1f;
  background: #20252b;
  font-size: 12px;
}

.ruler-x {
  top: 0;
  left: 54px;
  height: 24px;
  min-width: 100%;
  border-bottom: 1px solid #191d22;
  background-color: #262c32;
  background-image: repeating-linear-gradient(90deg, #87919a 0 1px, transparent 1px 5px);
  background-position: bottom;
  background-size: 50px 7px;
  background-repeat: repeat-x;
}

.ruler-x span {
  position: absolute;
  bottom: 2px;
  transform: translateX(3px);
}

.ruler-y {
  top: 36px;
  left: 0;
  width: 24px;
  min-height: 100%;
  border-right: 1px solid #191d22;
  background-color: #262c32;
  background-image: repeating-linear-gradient(180deg, #87919a 0 1px, transparent 1px 5px);
  background-position: right;
  background-size: 7px 50px;
  background-repeat: repeat-y;
}

.ruler-y span {
  position: absolute;
  right: 3px;
  transform: translateY(3px) rotate(-90deg);
  transform-origin: right top;
}

.zoom-area {
  position: sticky;
  right: 0;
  bottom: 0;
  z-index: 80;
  float: right;
  display: flex;
  align-items: center;
  gap: 9px;
  height: 36px;
  padding: 0 8px;
  background: #15191e;
  color: #8d969f;
}

.zoom-area button {
  width: 24px;
  height: 28px;
  border: 0;
  background: transparent;
  color: #7f8b96;
  cursor: pointer;
}

.zoom-area button:hover { color: #d9e2eb; }
.zoom-area input[type='range'] { width: 110px; accent-color: #1478bd; }

.zoom-area label {
  display: flex;
  align-items: center;
  border: 1px solid #343a41;
  background: #111418;
}

.zoom-area label input {
  width: 45px;
  height: 24px;
  border: 0;
  background: transparent;
  color: #e1e6eb;
  text-align: right;
}

.zoom-area label span { padding-right: 4px; }

.canvas-context-menu {
  position: fixed;
  z-index: 9999;
  min-width: 176px;
  box-shadow: 0 0 12px rgba(0, 0, 0, .72);
}

.canvas-context-menu ul {
  margin: 0;
  padding: 8px 0;
  border-radius: 2px;
  background: #1d1f26;
}

.canvas-context-menu li { margin: 0; list-style: none; }
.canvas-context-menu li.line { height: 1px; margin: 4px 0; background: rgba(255, 255, 255, .12); }

.canvas-context-menu button {
  display: flex;
  align-items: center;
  width: 100%;
  padding: 4px 12px;
  border: 0;
  background: transparent;
  color: #cfd3dc;
  font-family: Roboto, Helvetica, Arial, sans-serif;
  font-size: 12px;
  line-height: 1.5;
  text-align: left;
  white-space: nowrap;
  cursor: pointer;
}

.canvas-context-menu button:not(:disabled):hover { background: rgba(255, 255, 255, .08); }
.canvas-context-menu button:disabled { color: #555; cursor: default; }
.canvas-context-menu .name { min-width: 80px; }
.canvas-context-menu .shortcut { margin-left: 8px; color: #757575; letter-spacing: 0; }

.canvas-viewport.is-preview {
  overflow: hidden;
  background: #000;
}

.is-preview .canvas-content {
  display: grid;
  place-items: center;
  width: 100%;
  height: 100%;
  min-width: 0;
  min-height: 0;
  padding: 0;
}

@keyframes widget-hover-breathe {
  from { box-shadow: 0 0 0 1px rgba(114, 197, 248, .12), 0 0 7px rgba(52, 157, 224, .12); }
  to { box-shadow: 0 0 0 1px rgba(114, 197, 248, .32), 0 0 17px rgba(52, 157, 224, .3); }
}

@keyframes widget-focus-pulse {
  from {
    border-color: rgba(114, 197, 248, .25);
    box-shadow: 0 0 0 7px rgba(114, 197, 248, .18), 0 0 22px rgba(52, 157, 224, .34);
  }
  to {
    border-color: #72c5f8;
    box-shadow: 0 0 0 1px rgba(114, 197, 248, .2), 0 0 14px rgba(52, 157, 224, .28);
  }
}

@keyframes dashboard-widget-enter {
  from {
    opacity: 0;
    filter: blur(8px);
    clip-path: inset(10% 0 0 0 round 18px);
  }
  to {
    opacity: 1;
    filter: blur(0);
    clip-path: inset(0 round 18px);
  }
}

@media (prefers-reduced-motion: reduce) {
  .screen-widget,
  .screen-widget::before,
  .screen-widget.selected::after {
    animation: none !important;
    transition: none;
  }
}
</style>
