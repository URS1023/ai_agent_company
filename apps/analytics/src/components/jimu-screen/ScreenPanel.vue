<template>
  <section class="screen-panel" :class="[`tone-${tone}`]">
    <header class="screen-panel__header">
      <div class="screen-panel__title-wrap">
        <i v-if="icon" :class="icon" class="screen-panel__icon"></i>
        <div class="screen-panel__title-group">
          <h3 class="screen-panel__title">{{ title }}</h3>
          <p v-if="subtitle" class="screen-panel__subtitle">{{ subtitle }}</p>
        </div>
      </div>
      <span v-if="badge !== '' && badge !== null && badge !== undefined" class="screen-panel__badge">
        {{ badge }}
      </span>
    </header>

    <div class="screen-panel__body">
      <slot />
    </div>
  </section>
</template>

<script setup>
/**
 * ScreenPanel —— 大屏通用面板容器（标题/副标题/图标/角标 + 插槽内容）。
 *
 * 使用场景：大屏与 AI 生成页面的分区外框，由调用方提供标题与内容。
 * 关键约束：纯布局组件，不取数、不持状态；badge 为空串/null/undefined 时不渲染角标
 * （因此不能用 0 判断"无角标"，0 是合法角标值）。
 */
defineProps({
  title: { type: String, required: true },
  subtitle: { type: String, default: '' },
  icon: { type: String, default: '' },
  badge: { type: [String, Number], default: '' },
  tone: { type: String, default: 'cyan' }
})
</script>

<style scoped>
.screen-panel {
  position: relative;
  display: flex;
  flex-direction: column;
  min-height: 0;
  border: 1px solid rgba(71, 194, 255, 0.12);
  border-radius: 8px;
  background: rgba(6, 16, 35, 0.78);
  backdrop-filter: blur(18px);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.03), 0 16px 32px rgba(0, 0, 0, 0.22);
  overflow: hidden;
}

.screen-panel::before {
  content: "";
  position: absolute;
  inset: 0;
  pointer-events: none;
  background: linear-gradient(135deg, rgba(71, 194, 255, 0.08), transparent 46%, rgba(154, 92, 255, 0.08));
}

.screen-panel::after {
  content: "";
  position: absolute;
  left: 18px;
  right: 18px;
  top: 0;
  height: 1px;
  background: linear-gradient(90deg, transparent, rgba(71, 194, 255, 0.55), transparent);
}

.screen-panel__header {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 12px 14px 11px;
  border-bottom: 1px solid rgba(71, 194, 255, 0.08);
  background: rgba(71, 194, 255, 0.02);
}

.screen-panel__title-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 0;
}

.screen-panel__icon {
  flex: none;
  color: #4fd1ff;
  font-size: 14px;
}

.screen-panel__title-group {
  min-width: 0;
}

.screen-panel__title {
  margin: 0;
  font-size: 14px;
  line-height: 1.1;
  color: #dbeafe;
  font-weight: 700;
  letter-spacing: 0;
}

.screen-panel__subtitle {
  margin: 4px 0 0;
  font-size: 11px;
  line-height: 1.2;
  color: #7d8aa7;
  letter-spacing: 0;
}

.screen-panel__badge {
  flex: none;
  padding: 4px 10px;
  border-radius: 999px;
  background: rgba(71, 194, 255, 0.12);
  border: 1px solid rgba(71, 194, 255, 0.16);
  color: #8bd8ff;
  font-size: 11px;
  line-height: 1;
}

.screen-panel__body {
  position: relative;
  flex: 1;
  min-height: 0;
  padding: 12px 14px 14px;
}

.tone-cyan .screen-panel__icon,
.tone-cyan .screen-panel__badge {
  color: #55d6ff;
}

.tone-violet .screen-panel__icon,
.tone-violet .screen-panel__badge {
  color: #bf9dff;
}

.tone-amber .screen-panel__icon,
.tone-amber .screen-panel__badge {
  color: #f7c76a;
}
</style>
