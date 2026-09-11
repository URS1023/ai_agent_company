<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import common from '../../../web/i18n/zh-Hans/common.json'
import JimuCanvas from '../lynx-renderer/src/components/JimuCanvas.vue'

const props = defineProps({
  content: { type: Object, default: null },
  status: { type: String, default: null },
})

const container = ref(null)
const bounds = ref({ width: 0, height: 0 })
let observer
const zoom = computed(() =>
  props.content
    ? Math.min(
        bounds.value.width / props.content.page.design.width,
        bounds.value.height / props.content.page.design.height,
      )
    : 1,
)
onMounted(() => {
  observer = new ResizeObserver(([entry]) => {
    bounds.value = { width: entry.contentRect.width, height: entry.contentRect.height }
  })
  if (container.value) observer.observe(container.value)
})
onBeforeUnmount(() => observer?.disconnect())
</script>

<template>
  <main ref="container" class="template-viewport">
    <p v-if="status === 'failed'" class="dashboard-status" role="alert">
      {{ common['api.actionFailed'] }}
    </p>
    <p v-else-if="status === 'empty'" class="dashboard-status" role="status">{{ common.noData }}</p>
    <JimuCanvas
      v-if="content && zoom > 0"
      :page="content.page"
      :widgets="content.widgets"
      :zoom="zoom"
      preview
    />
    <p v-else-if="!content" role="alert">{{ common['enterprise.devices.loadError'] }}</p>
  </main>
</template>

<style>
html,
body,
#app {
  margin: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
}
.template-viewport {
  position: relative;
  width: 100%;
  height: 100%;
}
.dashboard-status {
  position: absolute;
  z-index: 100;
  top: 8px;
  right: 8px;
  margin: 0;
  padding: 8px 12px;
  color: #fff;
  background: #222;
  border: 1px solid currentColor;
  border-radius: 4px;
}
.template-viewport > .canvas-viewport {
  width: 100%;
  height: 100%;
}

/* The authored 112x50 metric slots cannot contain the legacy large-card chrome. */
.template-viewport .variant-showcase-template-19-metric .stats-summary {
  padding: 0;
  gap: 0;
}
.template-viewport .variant-showcase-template-19-metric .stat-card {
  min-height: 0;
  padding: 4px 8px;
  box-sizing: border-box;
}
.template-viewport .variant-showcase-template-19-metric .stat-kicker,
.template-viewport .variant-showcase-template-19-metric .stat-icon,
.template-viewport .variant-showcase-template-19-metric .stat-sync,
.template-viewport .variant-showcase-template-19-metric .stat-signal {
  display: none;
}
.template-viewport .variant-showcase-template-19-metric .stat-main {
  margin-top: 0;
  gap: 0;
  width: 100%;
}
.template-viewport .variant-showcase-template-19-metric .stat-reading {
  width: 100%;
  min-width: 0;
}
.template-viewport .variant-showcase-template-19-metric .stat-value {
  font-size: 18px !important;
}
.template-viewport .variant-showcase-template-19-metric .stat-label {
  margin-top: 2px;
  font-size: 9px;
}
</style>
