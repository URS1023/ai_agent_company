<script setup lang="ts">
/**
 * 数据源卡片（数据源工作区列表项）。
 * 路由：无独立路由，由 DataSourceManage 在 /datasources（database 页签）内渲染。
 * 依赖接口：纯展示组件，不发请求；所有动作以事件上抛（health/view/sync/policy/edit/toggle/delete）。
 * 关键交互：健康检查按钮在无 execute 权限或数据源停用时禁用；各操作按钮由 canExecute/canEdit/canDelete 控制显隐。
 * 约束：状态展示必须区分"未同步/待更新/同步失败"与"未建索引/仅注释检索"（元数据与语义索引是两套独立状态），
 *   否则用户会把"没同步过"误当成"同步失败"。
 */
import type { DataSourceSummary } from './types'

const props = defineProps<{
  source: DataSourceSummary
  canExecute: boolean
  canEdit: boolean
  canDelete: boolean
}>()

const emit = defineEmits<{
  health: [source: DataSourceSummary]
  view: [source: DataSourceSummary]
  sync: [source: DataSourceSummary]
  policy: [source: DataSourceSummary]
  edit: [source: DataSourceSummary]
  toggle: [source: DataSourceSummary]
  delete: [source: DataSourceSummary]
}>()

function percent(value = 0, total = 0) {
  return total > 0 ? Math.round((value / total) * 100) : 0
}

/** 元数据同步状态中文名；键必须与后端 metadataStatus 取值一致。 */
function metadataLabel() {
  return ({ ready: '已同步', stale: '待更新', failed: '同步失败', not_synced: '未同步' } as Record<string, string>)[props.source.metadataStatus || ''] || '未同步'
}

/** 语义索引状态中文名；键必须与后端 semanticIndexStatus 取值一致（注释检索与向量检索是不同档位）。 */
function semanticLabel() {
  return ({ ready: '向量 + 注释', keyword_only: '注释检索', failed: '索引失败', not_indexed: '未建索引' } as Record<string, string>)[props.source.semanticIndexStatus || ''] || '未建索引'
}

/** 健康检查时间：后端可能返回不带时区的 UTC 字符串，需补 'Z' 后再按中文本地时间格式化。 */
function healthTime() {
  if (!props.source.lastHealthCheckAt) return ''
  const value = props.source.lastHealthCheckAt
  const date = new Date(value.endsWith('Z') ? value : value + 'Z')
  return new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }).format(date)
}
</script>

<template>
  <article class="datasource-card" :class="{ disabled: !source.enabled }">
    <header class="source-head">
      <span class="source-mark" :class="{ active: source.enabled }"><i class="fa-solid fa-database" /></span>
      <div class="source-identity">
        <div><h3>{{ source.name }}</h3><span class="provider">{{ source.type.toUpperCase() }}</span><span class="state" :class="{ active: source.enabled }">{{ source.enabled ? '启用' : '停用' }}</span></div>
        <p>{{ source.environmentSummary || '数据库环境尚未同步' }}<template v-if="healthTime()"> · 检查于 {{ healthTime() }}</template></p>
      </div>
      <button class="health" :class="source.healthStatus || 'unknown'" type="button" :disabled="!canExecute || !source.enabled" @click="emit('health', source)">
        <i />{{ source.healthStatus === 'healthy' ? '连接正常' : source.healthStatus === 'unhealthy' ? '连接异常' : '检查连接' }}
      </button>
    </header>

    <p class="source-description">
      {{ source.description || '同步元数据后，系统将根据表注释和字段注释自动生成用途说明。' }}
      <span>{{ source.descriptionSource === 'configured' ? '管理员说明' : source.descriptionSource === 'metadata' ? '元数据生成' : '待补充' }}</span>
    </p>

    <div v-if="source.businessDomains?.length" class="domain-row">
      <span v-for="domain in source.businessDomains.slice(0, 5)" :key="domain">{{ domain }}</span>
    </div>

    <dl class="source-facts">
      <div><dt>业务表</dt><dd>{{ source.businessTableCount || 0 }}<small>/ {{ source.tableCount || 0 }}</small></dd></div>
      <div><dt>业务字段</dt><dd>{{ source.fieldCount || 0 }}</dd></div>
      <div><dt>元数据</dt><dd class="text">{{ metadataLabel() }}</dd></div>
      <div><dt>语义召回</dt><dd class="text">{{ semanticLabel() }}</dd></div>
    </dl>

    <div class="coverage" aria-label="元数据覆盖率">
      <div><span>表注释 {{ percent(source.commentedTableCount, source.businessTableCount) }}%</span><i><b :style="{ width: percent(source.commentedTableCount, source.businessTableCount) + '%' }" /></i></div>
      <div><span>字段注释 {{ percent(source.commentedFieldCount, source.fieldCount) }}%</span><i><b :style="{ width: percent(source.commentedFieldCount, source.fieldCount) + '%' }" /></i></div>
    </div>

    <footer class="source-actions">
      <button type="button" :disabled="!source.enabled" @click="emit('view', source)"><i class="fa-solid fa-table" />查看结构</button>
      <button v-if="canExecute" type="button" :disabled="!source.enabled" @click="emit('sync', source)"><i class="fa-solid fa-rotate" />同步注释与索引</button>
      <button v-if="canEdit" type="button" @click="emit('policy', source)"><i class="fa-solid fa-gauge-high" />治理策略</button>
      <span />
      <button v-if="canEdit" class="icon" type="button" aria-label="编辑数据源" @click="emit('edit', source)"><i class="fa-solid fa-pen" /></button>
      <button v-if="canEdit" class="icon" type="button" :aria-label="source.enabled ? '停用数据源' : '启用数据源'" @click="emit('toggle', source)"><i :class="source.enabled ? 'fa-solid fa-toggle-on' : 'fa-solid fa-toggle-off'" /></button>
      <button v-if="canDelete" class="icon danger" type="button" aria-label="删除数据源" @click="emit('delete', source)"><i class="fa-solid fa-trash" /></button>
    </footer>
  </article>
</template>

<style scoped>
.datasource-card { min-width: 0; overflow: hidden; color: #172b42; background: #fff; border: 1px solid #d8e1eb; border-radius: 8px; box-shadow: 0 4px 14px rgba(22, 48, 78, .045); }
.datasource-card.disabled { background: #fbfcfe; border-style: dashed; }
.source-head { display: flex; min-width: 0; align-items: flex-start; gap: 11px; padding: 15px 16px 11px; border-bottom: 1px solid #edf1f5; }
.source-mark { display: grid; width: 34px; height: 34px; flex: 0 0 auto; place-items: center; color: #6c7f92; background: #eef2f6; border-radius: 6px; }
.source-mark.active { color: #fff; background: #2866a4; }
.source-identity { min-width: 0; flex: 1; }
.source-identity > div { display: flex; min-width: 0; align-items: center; gap: 6px; }
h3 { margin: 0; overflow: hidden; color: #102c48; font-size: 14px; font-weight: 650; text-overflow: ellipsis; white-space: nowrap; }
.provider, .state { flex: 0 0 auto; padding: 2px 5px; color: #4f657b; background: #edf1f5; border-radius: 3px; font: 600 9px ui-monospace, Consolas, monospace; }
.state.active { color: #23684e; background: #e7f4ed; }
.source-identity p { margin: 4px 0 0; overflow: hidden; color: #5e7083; font-size: 10.5px; text-overflow: ellipsis; white-space: nowrap; }
.health { display: inline-flex; min-height: 28px; flex: 0 0 auto; align-items: center; gap: 5px; padding: 4px 7px; color: #607488; background: #fff; border: 1px solid #d9e1e9; border-radius: 4px; font-size: 9.5px; cursor: pointer; }
.health i { width: 6px; height: 6px; background: #9aa8b6; border-radius: 50%; }.health.healthy i { background: #2b8a64; }.health.unhealthy i { background: #c44a40; }
.source-description { min-height: 47px; margin: 0; padding: 11px 16px 8px; color: #50667d; font-size: 11px; line-height: 1.5; }
.source-description span { display: inline-flex; margin-left: 5px; padding: 1px 4px; color: #50677d; background: #eef2f6; border-radius: 3px; font-size: 9px; vertical-align: 1px; }
.domain-row { display: flex; gap: 4px; padding: 0 16px 9px; flex-wrap: wrap; }.domain-row span { padding: 2px 5px; color: #2f5f8f; background: #eaf2fa; border-radius: 3px; font-size: 9px; }
.source-facts { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); margin: 0; background: #f8fafc; border-top: 1px solid #edf1f5; border-bottom: 1px solid #edf1f5; }
.source-facts div { min-width: 0; padding: 9px 10px; border-right: 1px solid #e4eaf0; }.source-facts div:last-child { border-right: 0; }
dt { color: #596d81; font-size: 9px; } dd { margin: 3px 0 0; color: #163c62; font: 650 16px ui-monospace, Consolas, monospace; } dd small { margin-left: 3px; color: #5c7084; font-size: 9px; } dd.text { overflow: hidden; font: 600 10px system-ui, sans-serif; text-overflow: ellipsis; white-space: nowrap; }
.coverage { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; padding: 10px 16px; }.coverage span { display: block; margin-bottom: 4px; color: #596d81; font-size: 9px; }.coverage i { display: block; height: 3px; overflow: hidden; background: #e2e8ee; }.coverage b { display: block; height: 100%; background: #2e7d69; }
.source-actions { display: flex; min-height: 45px; align-items: center; gap: 5px; padding: 7px 10px 7px 16px; border-top: 1px solid #edf1f5; }.source-actions > span { flex: 1; }.source-actions button { display: inline-flex; min-height: 29px; align-items: center; gap: 5px; padding: 4px 8px; color: #315f8d; background: #fff; border: 1px solid #d2dde8; border-radius: 4px; font-size: 9.5px; cursor: pointer; }.source-actions button:hover { background: #f4f7fa; }.source-actions button:disabled { opacity: .45; cursor: not-allowed; }.source-actions .icon { width: 29px; padding: 0; justify-content: center; color: #6c7d8f; }.source-actions .danger { color: #a1423a; }
@media (max-width: 700px) { .source-facts { grid-template-columns: repeat(2, 1fr); }.source-facts div:nth-child(2) { border-right: 0; }.coverage { grid-template-columns: 1fr; }.source-actions { overflow-x: auto; }.health { display: none; } }
</style>
