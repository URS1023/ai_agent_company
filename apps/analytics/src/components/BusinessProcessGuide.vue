<script setup lang="ts">
/**
 * 业务使用说明（步骤条 + 要点清单）提示组件。
 * 路由：无独立路由；在预警工作台（/alerts/:section）与质检工作台（/quality/:section）的"使用说明"弹窗内复用。
 * 依赖接口：纯展示组件（无状态、无请求）；内容由调用方以 props 传入（eyebrow/title/summary/steps/notes）。
 * 关键交互：步骤上有 path 时显示跳转按钮，点击触发 navigate 事件由父组件路由跳转（组件自身不导航）。
 * 约束：steps 中的描述与 notes 是业务口径说明（如"保存配置不会读取设备"），修改需与后端行为保持一致。
 */
defineProps<{
  eyebrow: string
  title: string
  summary: string
  steps: Array<{ title: string; description: string; path?: string; action?: string }>
  notes: string[]
}>()

defineEmits<{ navigate: [path: string] }>()
</script>

<template>
  <section class="business-process-guide">
    <header class="business-guide-header">
      <span>{{ eyebrow }}</span>
      <h2>{{ title }}</h2>
      <p>{{ summary }}</p>
    </header>
    <ol class="business-guide-flow">
      <li v-for="(step, index) in steps" :key="step.title">
        <i aria-hidden="true">{{ String(index + 1).padStart(2, '0') }}</i>
        <div><strong>{{ step.title }}</strong><p>{{ step.description }}</p></div>
        <button v-if="step.path" type="button" @click="$emit('navigate', step.path)">{{ step.action || '前往配置' }}</button>
      </li>
    </ol>
    <aside class="business-guide-notes" aria-label="使用要点">
      <strong>使用要点</strong>
      <ul><li v-for="note in notes" :key="note">{{ note }}</li></ul>
    </aside>
  </section>
</template>

<style scoped>
.business-process-guide { color: #17263c; }
.business-guide-header { padding: 18px 20px; border: 1px solid #d9e4f2; border-left: 4px solid #2666e8; background: linear-gradient(135deg, #f7faff 0%, #eef5ff 100%); }
.business-guide-header span { color: #2666e8; font-size: 11px; font-weight: 750; letter-spacing: .12em; }
.business-guide-header h2 { margin: 5px 0 4px; font-size: 20px; }
.business-guide-header p { margin: 0; color: #53657d; line-height: 1.65; }
.business-guide-flow { display: grid; margin: 18px 0; padding: 0; list-style: none; border: 1px solid #dce4ef; background: #fff; }
.business-guide-flow li { display: grid; grid-template-columns: 44px minmax(0, 1fr) auto; align-items: center; gap: 14px; min-height: 76px; padding: 12px 16px; border-bottom: 1px solid #e5eaf2; }
.business-guide-flow li:last-child { border-bottom: 0; }
.business-guide-flow i { display: grid; width: 34px; height: 34px; place-items: center; border-radius: 5px; background: #e8f1ff; color: #1d5cc4; font: 700 12px/1 Consolas, monospace; font-style: normal; }
.business-guide-flow div { min-width: 0; }
.business-guide-flow strong { font-size: 14px; }
.business-guide-flow p { margin: 4px 0 0; color: #5b6d85; font-size: 12px; line-height: 1.55; }
.business-guide-flow button { white-space: nowrap; }
.business-guide-notes { padding: 14px 16px; border: 1px solid #dce4ef; background: #f8fafc; }
.business-guide-notes > strong { color: #27476f; }
.business-guide-notes ul { display: grid; gap: 7px; margin: 9px 0 0; padding-left: 18px; color: #53657d; line-height: 1.55; }
@media (max-width: 650px) {
  .business-guide-header { padding: 14px; }
  .business-guide-flow li { grid-template-columns: 38px minmax(0, 1fr); padding: 12px; }
  .business-guide-flow button { grid-column: 2; justify-self: start; width: 100%; min-height: 44px; }
}
</style>
