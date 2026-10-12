<script setup lang="ts">
/**
 * 大屏查看舞台（只读渲染 + 自适应缩放）。
 * 路由：无独立路由，由 ReportViewer（/report/view/:id）内嵌使用。
 * 依赖接口：无请求；screen 数据由父组件加载（含 design.width/height 与 widgets），渲染复用 jimu-screen 的 JimuCanvas（preview 只读模式）。
 * 关键交互：按容器尺寸与设计分辨率取最小缩放比（fit）实现等比适配，ResizeObserver 跟随容器变化重算；
 *   retry/back/drilldown 事件上抛给父组件（本组件不导航、不取数）。
 * 约束：缩放只在 0.1–1 之间收敛，禁止放大导致模糊；组件卸载时断开 ResizeObserver（防内存泄漏）。
 */
import { nextTick, onBeforeUnmount, onMounted, shallowRef, watch } from 'vue'
import JimuCanvas from '@/components/jimu-screen/JimuCanvas.vue'

const props = defineProps<{
  screen: Record<string, any>
  loading: boolean
  error: string
}>()

const emit = defineEmits<{
  retry: []
  back: []
  drilldown: [payload: Record<string, any>]
}>()

const stage = shallowRef<HTMLElement | null>(null)
const zoom = shallowRef(1)
let resizeObserver: ResizeObserver | null = null

/** 按"设计分辨率 vs 容器尺寸"计算等比缩放，取较小值以保证整屏可见（下限 0.1，避免极端缩略）。 */
function fit() {
  const bounds = stage.value?.getBoundingClientRect()
  const width = Number(props.screen.design?.width || 1920)
  const height = Number(props.screen.design?.height || 1080)
  if (!bounds?.width || !bounds?.height || !width || !height) return
  zoom.value = Math.max(.1, Math.min(bounds.width / width, bounds.height / height))
}

watch(
  () => [props.loading, props.error, props.screen.design?.width, props.screen.design?.height],
  async () => { await nextTick(); fit() },
)

onMounted(() => {
  resizeObserver = new ResizeObserver(fit)
  if (stage.value) resizeObserver.observe(stage.value)
  fit()
})

onBeforeUnmount(() => resizeObserver?.disconnect())
</script>

<template>
  <section ref="stage" class="dashboard-stage" :aria-busy="loading">
    <JimuCanvas
      v-if="!loading && !error"
      :page="screen"
      :widgets="screen.widgets || []"
      :zoom="zoom"
      preview
      @drilldown="emit('drilldown', $event)"
      @zoom-change="zoom = $event"
    />
    <div v-else-if="loading" class="stage-loading" role="status" aria-label="正在加载数字大屏">
      <span aria-hidden="true"></span>
    </div>
    <section v-else class="stage-error" role="alert">
      <small>REPORT ACCESS</small>
      <h1>无法打开报表</h1>
      <p>{{ error }}</p>
      <div>
        <button type="button" @click="emit('retry')">重新加载</button>
        <button type="button" class="quiet" @click="emit('back')">返回报表列表</button>
      </div>
    </section>
  </section>
</template>

<style scoped>
.dashboard-stage { position: absolute; inset: 0; min-width: 0; min-height: 0; overflow: hidden; background: #020713; }
.stage-loading { position: absolute; inset: 0; display: grid; place-items: center; background: radial-gradient(circle at 50% 46%, #092342, #020713 42%); }
.stage-loading span { width: 32px; height: 32px; border: 1px solid rgba(63, 205, 255, .18); border-top-color: #4ed6ff; border-radius: 50%; box-shadow: 0 0 24px rgba(78, 214, 255, .18); animation: stage-spin .8s linear infinite; }
.stage-error { position: absolute; top: 50%; left: 50%; width: min(430px, calc(100vw - 40px)); padding: 30px 32px 28px; border: 1px solid rgba(85, 190, 231, .2); border-radius: 10px; color: #eaf8ff; background: linear-gradient(145deg, rgba(8, 27, 51, .96), rgba(3, 12, 27, .98)); box-shadow: 0 30px 90px rgba(0, 0, 0, .44); transform: translate(-50%, -50%); }
.stage-error::before { position: absolute; top: -1px; left: 32px; width: 72px; height: 2px; background: #49d5ff; box-shadow: 0 0 16px rgba(73, 213, 255, .6); content: ''; }
.stage-error small { color: #5eb9dd; font: 10px Consolas, monospace; letter-spacing: .22em; }
.stage-error h1 { margin: 12px 0 8px; font: 600 25px Bahnschrift, 'Microsoft YaHei UI', sans-serif; letter-spacing: .03em; }
.stage-error p { margin: 0; color: #9bb6c7; font: 13px/1.8 'Microsoft YaHei UI', sans-serif; }
.stage-error div { display: flex; gap: 9px; margin-top: 23px; }
.stage-error button { min-height: 34px; padding: 0 15px; border: 1px solid #3ebae8; border-radius: 5px; color: #ecfaff; background: rgba(29, 133, 181, .25); cursor: pointer; }
.stage-error button.quiet { border-color: rgba(119, 165, 186, .35); color: #a9c1ce; background: transparent; }
.stage-error button:hover { background: rgba(49, 169, 219, .35); }
.stage-error button:focus-visible { outline: 2px solid #63dcff; outline-offset: 2px; }
@keyframes stage-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .stage-loading span { animation: none; } }
</style>
