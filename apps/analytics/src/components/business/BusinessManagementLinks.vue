<script setup lang="ts">
/**
 * 业务配置管理入口（预警域的测点/专家经验/预警策略，质检域的指标/模板/质检记录）。
 * 路由：无独立路由，挂在 /alerts、/quality 页面标题下方。
 * 依赖接口：无请求；静态链接清单 + can() 权限过滤。
 * 约束：链接的 path 必须与 router.ts 中的路由一致（否则会被守卫拦到 /unavailable）；
 *   无任何可访问入口时整块不渲染，避免出现空导航条。
 */
import { computed } from 'vue'
import { can } from '../../api/request'

const props = defineProps<{ domain: 'alert' | 'quality' }>()
/** 按域给出管理入口并按权限过滤；permission 为不翻译的权限码字符串。 */
const links = computed(() => (props.domain === 'alert' ? [
  { path: '/alerts/points', label: '测点管理', permission: 'devices.view' },
  { path: '/alerts/advice', label: '专家经验', permission: 'alerts.view' },
  { path: '/alerts/rules', label: '预警策略', permission: 'alerts.view' },
] : [
  { path: '/quality/metrics', label: '质检指标', permission: 'quality.view' },
  { path: '/quality/templates', label: '检验模板', permission: 'quality.view' },
  { path: '/quality/inspections', label: '质检记录', permission: 'quality.view' },
]).filter(link => can(link.permission)))
</script>

<template>
  <nav v-if="links.length" class="management-links" aria-label="业务配置管理">
    <RouterLink v-for="link in links" :key="link.path" :to="link.path">{{ link.label }}</RouterLink>
  </nav>
</template>

<style scoped>
.management-links { display: flex; flex-wrap: wrap; gap: 12px; margin: 12px 0 20px; }
.management-links a { color: #334155; background: #f1f5f9; border: 1px solid #cbd5e1; border-radius: 6px; padding: 8px 12px; text-decoration: none; }
.management-links a:hover { background: #e2e8f0; }
.management-links a:focus-visible { outline: 2px solid #2563eb; outline-offset: 2px; }
</style>
