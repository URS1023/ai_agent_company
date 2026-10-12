<script setup lang="ts">
/**
 * 按执行 id 拉取并展示写入凭据（写操作审计视图）。
 * 路由：无独立路由，嵌入工作流运行结果（WorkflowRunResult）等处。
 * 依赖接口：api/writeback.ts 的 writeback.operations（GET /api/v1/writeback/operations?executionId=...）。
 * 加载/取消：watch 的 cleanup 中 abort 上一次请求，executionId 为空时清空列表不请求。
 * 错误呈现：读取失败时提示"写入凭据读取失败：…"，并保留为空列表（不影响运行结果其它区域展示）。
 */
import { shallowRef, watch } from 'vue'
import { writeback, type WriteOperation } from '../../api/writeback'
import WriteReceipts from './WriteReceipts.vue'
import '../../styles/writeback.css'
const props = defineProps<{ executionId: string }>()
const items = shallowRef<WriteOperation[]>([]), error = shallowRef('')
watch(() => props.executionId, async (id, _, cleanup) => {
  const abort = new AbortController(); cleanup(() => abort.abort()); items.value = []; error.value = ''
  if (!id) return
  try { const result = await writeback.operations(id, abort.signal); if (!abort.signal.aborted) items.value = result.data }
  catch (cause) { if (!abort.signal.aborted) error.value = '写入凭据读取失败：' + (cause as Error).message }
}, { immediate: true })
</script>
<template><WriteReceipts :items="items" /><p v-if="error" class="wf-error" role="alert">{{ error }}</p></template>
