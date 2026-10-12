<script setup lang="ts">
/** 用真实画布组件预览参考模板，缩放仅发生在外层，保留源图比例。 */
import { computed, onBeforeUnmount, onMounted, shallowRef, useTemplateRef } from 'vue'
import JimuCanvas from '../jimu-screen/JimuCanvas.vue'
import { toJimuScreen } from '@/utils/jimuReportAdapter'

const props = defineProps<{ content: Record<string, any> }>()
const viewport = useTemplateRef<HTMLElement>('viewport')
const width = shallowRef(900)
const screen = computed<Record<string, any>>(() => toJimuScreen(props.content))
const zoom = computed(() => width.value / Number(screen.value.design.width || 1920))
const height = computed(() => Number(screen.value.design.height || 1080) * zoom.value)
let observer: ResizeObserver | undefined
onMounted(() => {
  observer = new ResizeObserver(entries => { width.value = entries[0].contentRect.width })
  if (viewport.value) observer.observe(viewport.value)
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <div ref="viewport" class="reference-template-preview" :style="{ height: `${height}px` }">
    <JimuCanvas :page="screen" :widgets="screen.widgets" :zoom="zoom" preview />
  </div>
</template>

<style scoped>
.reference-template-preview { width: 100%; overflow: hidden; position: relative; }
.reference-template-preview :deep(.canvas-viewport), .reference-template-preview :deep(.canvas-content) { padding: 0; margin: 0; height: 100%; min-height: 0; overflow: hidden; }
.reference-template-preview :deep(.scaled-stage) { margin: 0; }
</style>
