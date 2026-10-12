<script setup lang="ts">
/**
 * WorkflowBusinessSummary —— 业务口径的检查结论摘要（WorkflowRunResult 内使用，替代逐条数字罗列）。
 *
 * 输入：`rows` 为业务结果行（判定节点输出的 data）。
 * 关键约束：
 * - 质检与设备预警共用本组件：以行内是否出现「质检单号」/「单据」判定业务域，进而切换"张单据/个设备对象"
 *   的量词与是否展示标准值，新增业务域需同步扩展这两处判断；
 * - 分组名按「质检单号 > 单据 > 设备 > deviceName > name」优先级取，全空时显示"未记录名称"而非丢行；
 * - 结论只覆盖本次读取的记录，界面必须明示范围；质检场景还需声明"不替代审批放行"；
 * - 明细最多展示 12 条，避免长列表淹没结论。
 */
import { computed } from 'vue'

const props = defineProps<{ rows: Record<string, any>[] }>()
/** 质检域判定：出现单据类字段即视为质检场景，影响量词与标准值展示。 */
const isQuality = computed(() => props.rows.some(row => row['质检单号'] || row['单据']))
/** 按对象分组统计项数/问题数/待确认数；行内缺少名称字段时归入"未记录名称"，不丢弃数据。 */
const groups = computed(() => {
  const values = new Map<string, { name: string; items: number; issues: number; pending: number }>()
  for (const row of props.rows) {
    const name = String(row['质检单号'] || row['单据'] || row['设备'] || row.deviceName || row.name || '未记录名称')
    const group = values.get(name) || { name, items: 0, issues: 0, pending: 0 }
    group.items++
    if (['不合格', '预警'].includes(row['结果'])) group.issues++
    if (row['结果'] === '待确认') group.pending++
    values.set(name, group)
  }
  return [...values.values()]
})
const attention = computed(() => props.rows.filter(row => ['不合格', '预警', '待确认'].includes(row['结果'])))
</script>

<template>
  <section class="business-summary" aria-label="业务检查结论">
    <h3>本次检查结论</h3>
    <p>检查 {{ groups.length }} {{ isQuality ? '张单据' : '个设备对象' }}、{{ rows.length }} 项；{{ groups.filter(item => item.issues).length }} {{ isQuality ? '张单据' : '个对象' }}发现问题，{{ groups.filter(item => item.pending).length }} {{ isQuality ? '张单据' : '个对象' }}有待确认项。</p>
    <ul v-if="attention.length"><li v-for="(row, index) in attention.slice(0, 12)" :key="index">
      <strong>{{ row['质检单号'] || row['单据'] || row['设备'] || row.deviceName }} · {{ row['检验项目'] || row['检查项目'] || row['项目'] || row.point || row.planName }}：{{ row['结果'] }}</strong>
      <span v-if="isQuality">，标准 {{ row['标准原文'] || row['标准'] || '未记录' }}，实测 {{ row['测量值'] ?? row['实测值'] ?? '未记录' }}</span>。
      {{ row['说明'] }}
    </li></ul>
    <p v-else>本次检查项均通过；结论仅覆盖本次读取的记录。</p>
    <p v-if="isQuality" class="scope">以上为检查结果，不替代审批放行。</p>
  </section>
</template>

<style scoped>
.business-summary { padding: 18px; background: #f8fafc; border: 1px solid #e4e7ec; border-radius: 10px; margin: 16px 0; line-height: 1.75; }
.business-summary h3 { margin: 0 0 8px; }
.business-summary li { margin-bottom: 8px; }
.scope { color: #667085; font-size: 13px; }
</style>
