<script setup lang="ts">
/**
 * WorkflowList —— 工作流列表页的表格（`/workflows` 路由，WorkflowWorkbench 无 id 时渲染）。
 *
 * 依赖接口：`api/request.can`（删除权限）、`api/workflows` 的 `domainNames` / `flowStatus` 映射。
 * 关键约束：
 * - 纯展示 + 事件上抛（open/remove/page），分页由父组件按 offset 重新拉取，组件内不缓存全量数据；
 * - 分页步长固定 30，与 WorkflowWorkbench 的 `history`/`rows` 取数窗口一致；
 * - 空数据必须给出下一步引导文案（而非留白），加载中与无匹配流程的文案不同。
 */
import { can } from '../../api/request'
import { domainNames, flowStatus, type Workflow } from '../../api/workflows'
defineProps<{ rows: Workflow[]; loading: boolean; offset: number }>()
defineEmits<{ open: [flow: Workflow]; remove: [flow: Workflow]; page: [offset: number] }>()
/** 更新时间为空或后端返回非 ISO 字符串时统一显示占位符，不抛异常。 */
const formatUpdatedAt = (value?: string) => {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  })
}
</script>
<template>
  <div class="wf-table-wrap" tabindex="0" role="region" aria-label="可视化工作流列表"><table class="wf-table"><thead><tr><th>流程名称</th><th>适用业务</th><th>版本与发布</th><th>更新时间</th><th>操作</th></tr></thead><tbody>
    <tr v-for="flow in rows" :key="flow.id"><td><button class="wf-link" @click="$emit('open', flow)">{{ flow.name }}</button><small>{{ flow.description || '未填写用途说明' }}</small></td>
      <td>{{ flow.domains.map(domain => domainNames[domain]).join('、') }}</td><td>{{ flowStatus[flow.status] }}<small>草稿 v{{ flow.version }} / 正式 {{ flow.publishedRevision ? 'v' + flow.publishedRevision : '未发布' }}</small></td><td>{{ formatUpdatedAt(flow.updateTime) }}</td>
      <td><div class="wf-actions"><button @click="$emit('open', flow)">打开</button><button v-if="can('workflows.delete')" @click="$emit('remove', flow)">移除</button></div></td></tr>
    <tr v-if="!rows.length"><td colspan="5">{{ loading ? '正在加载工作流…' : '暂无匹配流程。新建流程后可配置采集、计算和判定节点。' }}</td></tr>
  </tbody></table></div>
  <div class="wf-actions wf-pagination"><span>第 {{ offset / 30 + 1 }} 页 · 本页 {{ rows.length }} 项</span><button :disabled="loading || !offset" @click="$emit('page', offset - 30)">上一页</button><button :disabled="loading || rows.length < 30" @click="$emit('page', offset + 30)">下一页</button></div>
</template>
