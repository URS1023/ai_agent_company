<script setup lang="ts">
/**
 * 大屏生成等待提示（纯展示 + 本地计时）。
 * 路由：无独立路由，在智能查询/大屏生成提交后展示。
 * 依赖接口：无请求（生成任务由父组件发起）；只负责显示已等待秒数与数据源名称，不显示虚假完成率。
 * 约束：计时器在组件卸载时清理；文案明确"离开页面不会取消服务端任务"，避免用户误以为需要留在页面等待。
 */
import { onMounted, onUnmounted, ref } from 'vue'

defineProps<{ sourceName: string }>()
const seconds = ref(0)
const startedAt = Date.now()
let clock: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  clock = setInterval(() => { seconds.value = Math.floor((Date.now() - startedAt) / 1000) }, 1000)
})
onUnmounted(() => clearInterval(clock))
</script>

<template>
  <section class="draft-progress" aria-label="大屏生成状态">
    <div><strong role="status">正在生成大屏并校验真实数据…</strong><span aria-live="off">已等待 {{ seconds }} 秒</span></div>
    <p>本次数据源：{{ sourceName }}。完成后自动打开可编辑草稿。</p>
    <p>生成包含布局、查询和业务口径校验，请勿重复提交。离开页面不会取消服务端任务，完成后可在“报表与数字大屏”查看。</p>
  </section>
</template>

<style scoped>
.draft-progress { margin-top:16px;padding:14px 16px;border:1px solid #b9d6fa;border-radius:6px;background:#f1f7ff;color:#294663; }
.draft-progress > div { display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap; }
.draft-progress strong { font-size:14px; }.draft-progress span { font-size:12px;white-space:nowrap; }
.draft-progress p { margin:8px 0 0;font-size:12px;line-height:1.7; }
</style>
