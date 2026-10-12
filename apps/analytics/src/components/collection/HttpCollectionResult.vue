<script setup lang="ts">
/**
 * HTTP 采集结果展示（只读，含逐请求状态与实际数据）。
 * 路由：无独立路由，由 HttpSourcesManager 在采集完成后渲染（也可复用于历史执行回看）。
 * 依赖接口：数据由父组件传入（api/collection.ts 的 CollectionResult）；状态中文取自 collectionStatus。
 * 关键交互：数据表本地分页（每页 50 行），并用 rowOrigins 显示"该行来自第几次请求"（便于定位批量请求中的哪一条）。
 * 约束：status 必须按后端语义展示（partial 表示部分完成）；data 为空时区分"采集成功但接口无数据"与"提取失败"，
 *   不得把空结果渲染成成功。
 */
import { computed, shallowRef } from 'vue'
import { collectionStatus, jsonCell, type CollectionResult } from '../../api/collection'
const props = defineProps<{ result: CollectionResult }>()
const page = shallowRef(1)
/** 当前页数据（每页 50 行，纯本地切片，避免大结果一次性渲染）。 */
const visibleRows = computed(() => props.result.data.slice((page.value - 1) * 50, page.value * 50))
</script>
<template>
  <section class="http-result" aria-label="HTTP 采集结果">
    <div :class="['http-result-summary', result.status]" role="status"><strong>{{ collectionStatus[result.status] }} · {{ result.totalCount }} 行</strong><p>{{ result.analysis }}</p></div>
    <dl class="http-provenance"><div><dt>配置版本</dt><dd>v{{ result.datasourceVersion }}</dd></div><div><dt>运行记录</dt><dd>{{ result.runId }}</dd></div><div><dt>执行耗时</dt><dd>{{ result.executionTime }} ms</dd></div></dl>
    <h3>逐条请求状态</h3><div class="http-table-wrap"><table class="http-table"><thead><tr><th>请求序号</th><th>状态</th><th>HTTP 状态</th><th>返回行数</th><th>说明</th></tr></thead><tbody>
      <tr v-for="item in result.outcomes" :key="item.index"><td>{{ item.index + 1 }}</td><td>{{ collectionStatus[item.status] }}</td><td>{{ item.httpStatus ?? '—' }}</td><td>{{ item.rows }}</td><td>{{ item.message || (item.truncated ? '已截断，还有未返回记录' : '完成') }}</td></tr>
    </tbody></table></div>
    <h3>数据结果</h3><p v-if="!result.data.length" class="http-note">{{ result.status === 'success' ? '采集成功，接口返回空结果。' : '没有成功提取的记录，请先检查上方错误。' }}</p>
    <div v-else class="http-table-wrap"><table class="http-table"><thead><tr><th>来源请求</th><th v-for="column in result.columns" :key="column.columnName">{{ column.columnName }}<small>{{ column.comment || column.dataType }}</small></th></tr></thead><tbody>
      <tr v-for="(row, index) in visibleRows" :key="index"><td>{{ result.rowOrigins[(page - 1) * 50 + index] + 1 }}</td><td v-for="column in result.columns" :key="column.columnName">{{ jsonCell(row[column.columnName]) }}</td></tr>
    </tbody></table></div>
    <div v-if="result.data.length > 50" class="http-pagination"><button type="button" :disabled="page <= 1" @click="page--">上一页</button><span>{{ page }} / {{ Math.ceil(result.data.length / 50) }}</span><button type="button" :disabled="page * 50 >= result.data.length" @click="page++">下一页</button></div>
  </section>
</template>
