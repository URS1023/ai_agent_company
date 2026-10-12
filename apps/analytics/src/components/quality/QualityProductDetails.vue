<script setup lang="ts">
/**
 * 产品检测要求详情弹窗（只读）。
 * 路由：无独立路由，由 QualityProductWorkspace 在产品卡片点"详情"时打开（product 为 null 即关闭）。
 * 依赖接口：无请求；数据来自目录快照分组（QualityProductGroup）。
 * 关键交互：逐条展示检测方案（类别/模板/要求数/历史单据数），提供"打开工作流"与"运行记录"两个出口；
 *   无历史单据时明确标注"暂无历史单据"，避免与"检测未执行"混淆。
 */
import { qualityCategories, type QualityProductEntry, type QualityProductGroup } from '../../api/qualityProducts'
defineProps<{ product: QualityProductGroup | null }>()
defineEmits<{ close: []; open: [entry: QualityProductEntry, logs?: boolean] }>()
</script>
<template>
  <el-dialog :model-value="!!product" :title="product?.name + ' · 检测要求'" width="min(760px, 95vw)" @close="$emit('close')">
    <p class="product-detail-code">产品编码：{{ product?.code || '未填写' }}</p>
    <section v-for="entry in product?.entries || []" :key="entry.workflowId" class="product-detail-entry">
      <span>{{ qualityCategories[entry.category] }}</span><h3>{{ entry.templateName }}</h3>
      <p>{{ entry.requirementCount }} 项检测要求 · {{ entry.formCount }} 张历史质检单{{ !entry.formCount ? '（暂无历史单据）' : '' }}</p>
      <div><el-button type="primary" @click="$emit('open', entry)">打开工作流</el-button><el-button @click="$emit('open', entry, true)">运行记录</el-button></div>
    </section>
  </el-dialog>
</template>
<style scoped>
.product-detail-code { color: #717b87; font-size: 13px; }.product-detail-entry { padding: 18px 0; border-top: 1px solid #eceff3; }.product-detail-entry>span { color: #238ece; font-size: 12px; }.product-detail-entry h3 { margin: 8px 0; color: #303b48; font-size: 16px; }.product-detail-entry p { color: #667085; font-size: 13px; }
</style>
