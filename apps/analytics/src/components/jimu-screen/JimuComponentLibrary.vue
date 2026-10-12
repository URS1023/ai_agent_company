<script setup>
/**
 * JimuComponentLibrary —— 大屏组件库（按分组/分类浏览组件，点击或拖拽加入画布）。
 *
 * 位置：JimuTopToolbar 的组件库面板内（`/report/designer/:id?`）。
 * 依赖接口：`utils/widgetFactory.createPreviewWidget`（用真实组件渲染缩略预览）、JimuWidgetRenderer。
 * 关键约束：
 * - 缩略图使用真实组件渲染（preview 形态），因此必须与画布渲染同一套工厂函数，避免预览与实际不一致；
 * - 只派发 add / keep-open / leave，落位坐标与复制规则由画布与父级决定；
 * - 分组数据 groups 由父级提供（来自 data/jimuComponentLibrary.json），组件内不内置目录。
 */
import { computed, ref, watch } from 'vue'
import JimuWidgetRenderer from './JimuWidgetRenderer.vue'
import { createPreviewWidget } from './widgetFactory'

const props = defineProps({
  group: { type: Object, required: true },
  anchorLeft: { type: Number, default: 0 }
})

const emit = defineEmits(['add', 'keep-open', 'leave'])
const activeCategory = ref('')

/** 切换分组时重置已选分类，避免新分组下停留在不存在的分类上导致空列表。 */
watch(() => props.group.key, () => {
  activeCategory.value = ''
})

const category = computed(() => props.group.categories.find(item => item.name === activeCategory.value) || null)
const previewItems = computed(() => (category.value?.items || []).map(item => ({
  item,
  widget: createPreviewWidget(item)
})))
const panelClass = computed(() => ({
  'has-category': Boolean(category.value),
  'is-wide': ['chart', 'material'].includes(props.group.key),
  'is-gallery': props.group.key === 'material' && activeCategory.value === '图库'
}))

function previewModelStyle(widget) {
  const scale = Math.min(128 / widget.w, 94 / widget.h)
  return {
    width: `${widget.w}px`,
    height: `${widget.h}px`,
    transform: `translate(-50%, -50%) scale(${scale})`
  }
}

function startDrag(event, item) {
  event.dataTransfer.effectAllowed = 'copy'
  event.dataTransfer.setData('application/x-cloud-brain-jimu-component', JSON.stringify(item))
  event.dataTransfer.setData('text/plain', item.name)
  emit('keep-open')
}
</script>

<template>
  <section
    class="component-library"
    :class="panelClass"
    :style="{ left: `${anchorLeft}px` }"
    @mouseenter="emit('keep-open')"
    @mouseleave="emit('leave')"
  >
    <nav class="category-list">
      <button
        v-for="entry in group.categories"
        :key="entry.name"
        :class="{ active: entry.name === activeCategory }"
        @mouseenter="activeCategory = entry.name"
        @focus="activeCategory = entry.name"
      >
        <span>{{ entry.name }}</span><em>{{ entry.count }}</em><i class="fa-solid fa-angle-right"></i>
      </button>
    </nav>
    <div v-if="category" class="component-grid">
      <button
        v-for="{ item, widget } in previewItems"
        :key="item.id"
        class="component-item"
        :title="`${item.name} (${item.compType})`"
        :data-component="widget.component"
        :data-model-key="widget.modelKey"
        draggable="true"
        @dragstart="startDrag($event, item)"
        @click="emit('add', item)"
      >
        <span class="component-preview">
          <span class="component-model" :style="previewModelStyle(widget)">
            <JimuWidgetRenderer :widget="widget" />
          </span>
        </span>
        <span class="component-name">{{ item.name }}</span>
      </button>
      <div v-if="!category?.items?.length" class="empty-category">暂无可用组件</div>
    </div>
  </section>
</template>

<style scoped>
.component-library {
  position: fixed;
  top: 53px;
  z-index: 1200;
  display: block;
  width: 160px;
  max-height: 540px;
  border: 0;
  background: #20242a;
  box-shadow: 0 12px 34px rgba(0, 0, 0, .48);
  color: #d8dde3;
}

.component-library.has-category { min-height: 190px; }

.category-list {
  max-height: 540px;
  overflow-y: auto;
  padding: 0;
  border-right: 1px solid #353a42;
  background: #1d2025;
  scrollbar-color: #40464f #1d2025;
  scrollbar-width: thin;
}

.category-list button {
  display: grid;
  grid-template-columns: 1fr auto 12px;
  align-items: center;
  gap: 6px;
  width: 100%;
  height: 36px;
  padding: 0 9px 0 14px;
  border: 0;
  background: transparent;
  color: #c5cbd2;
  font-size: 13px;
  text-align: left;
  cursor: default;
}

.category-list button.active { background: #30353c; color: #fff; }
.category-list em { color: #838c96; font-size: 11px; font-style: normal; }
.category-list i { color: #737d87; font-size: 10px; }

.component-grid {
  position: absolute;
  top: 0;
  left: 170px;
  display: grid;
  grid-template-columns: repeat(3, 130px);
  align-content: start;
  gap: 10px 8px;
  width: 458px;
  max-height: 528px;
  overflow-y: auto;
  padding: 9px;
  box-sizing: border-box;
  background: #20242a;
  box-shadow: 0 12px 34px rgba(0, 0, 0, .48);
  scrollbar-color: #40464f #20242a;
  scrollbar-width: thin;
}

.component-item {
  display: flex;
  flex-direction: column;
  gap: 0;
  align-items: center;
  min-width: 0;
  height: 115px;
  padding: 0;
  border: 1px solid #4a4f55;
  background: #1c2025;
  color: #cbd1d8;
  cursor: pointer;
}

.component-item:hover,
.component-item:focus-visible {
  border-color: #198bd4;
  background: #2d3239;
  color: #fff;
  outline: none;
}

.component-preview {
  position: relative;
  display: grid;
  place-items: center;
  width: 100%;
  height: 94px;
  overflow: hidden;
  background: #000;
}

.component-model {
  position: absolute;
  top: 50%;
  left: 50%;
  display: block;
  flex: none;
  transform-origin: center;
}

.component-name {
  overflow: hidden;
  width: 100%;
  font-size: 12px;
  line-height: 19px;
  text-align: center;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.empty-category {
  grid-column: 1 / -1;
  padding: 55px 0;
  color: #78818b;
  text-align: center;
}
</style>
