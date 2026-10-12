<script setup lang="ts">
/**
 * 外观检测证据区（原图 + 缺陷清单 + 来源说明）。
 * 路由：无独立路由，嵌在质检记录报告（QualityReport）内，只读展示。
 * 依赖接口：数据由父组件传入（质检记录的 report.results 中 category='appearance' 且带 observation 的行）。
 * 关键交互：无；有原图时渲染 QualityImageViewer（禁用框选），只有历史文字引用时提示"不自动访问外部地址"。
 * 约束：必须区分"人工录入"与"检测服务返回"两种来源（人工复核不会改写原标注）；
 *   缺陷为空数组要显式显示"未发现缺陷"，与"没有数据"区分开。
 */
import { computed } from 'vue'
import type { QualityRecord } from '../../api/quality'
import QualityImageViewer from './QualityImageViewer.vue'
const props = defineProps<{ report: QualityRecord }>()
const observations = computed(() => props.report.results.filter((row: QualityRecord) => row.category === 'appearance' && row.observation))
const levels: Record<string, string> = { minor: '轻微', moderate: '中度', severe: '重度', review: '待复核' }
</script>
<template>
  <section v-for="item in observations" :key="item.metricId" class="qc-picture-evidence">
    <h3>{{ item.name }} · 原图与缺陷证据</h3><p class="wf-note">{{ item.provenance.mode === 'manual' ? '人工录入的观察与标注；不代表模型识别结果。' : '检测服务返回的原始结果；人工复核不会改写这些标注。' }}</p>
    <QualityImageViewer v-if="item.provenance.image" :file-id="item.provenance.image.id" :inspection-id="report.id" :snapshot="item.provenance.image" :defects="item.observation.defects || []" />
    <p v-else class="wf-note">此历史记录仅保存来源文字：{{ item.observation.imageRef || '未提供' }}。未关联可核验的本地原图，不自动访问外部地址。</p>
    <ol class="qc-defect-evidence"><li v-for="(defect, index) in item.observation.defects || []" :key="index"><strong>{{ defect.defectType }}</strong> · {{ levels[defect.severity] }} · 置信度 {{ defect.confidence }}<p v-if="defect.bbox">位置 X / Y / W / H：{{ defect.bbox.join(' / ') }}</p><p v-if="defect.suggest">{{ defect.suggest }}</p></li></ol>
    <p v-if="item.observation.defects?.length === 0" class="wf-note">已明确记录：未发现缺陷。</p>
  </section>
</template>
<style scoped>
.qc-picture-evidence { margin-top: 24px; }
.qc-defect-evidence { padding-left: 24px; }
.qc-defect-evidence li { padding: 10px 0; border-bottom: 1px solid #e3e8ef; overflow-wrap: anywhere; }
</style>
