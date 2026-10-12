<script setup lang="ts">
/**
 * 质检原图查看器（含缺陷框选与证据摘要）。
 * 路由：无独立路由；用于质检记录报告（QualityReport / QualityImageEvidence）与流程向导的外观样本录入。
 * 依赖接口：useQualityImage（api/quality.ts 的 imageContent + image），图片以 objectURL 呈现并由 composable 负责释放。
 * 关键交互：可选缺陷框选（drawing 开关 + selected 指定目标缺陷），框选结果通过 box 事件回传父组件；
 *   缩放仅影响显示（归一化坐标不受影响）；错误态提供"重新读取图片"（reload）。
 * 约束：只显示本平台已保存的原图，绝不自动访问外部地址；SHA-256/EXIF 方向以 details 形式展示，尊重"原始字节未改写"的证据要求。
 */
import { shallowRef, toRef, watch } from 'vue'
import type { DefectBox, QualityDefect, QualityImage } from '../../api/quality'
import { useQualityImage } from '../../composables/useQualityImage'
import QualityImageCanvas from './QualityImageCanvas.vue'
const props = withDefaults(defineProps<{ fileId: string; inspectionId?: string; snapshot?: QualityImage; defects?: QualityDefect[]; editable?: boolean; disabled?: boolean }>(), { defects: () => [], editable: false, disabled: false })
const emit = defineEmits<{ box: [index: number, value: DefectBox] }>()
const { url, image, loading, error, reload } = useQualityImage(toRef(props, 'fileId'), toRef(props, 'inspectionId'), toRef(props, 'snapshot'))
const showBoxes = shallowRef(true), drawing = shallowRef(false), selected = shallowRef(0), zoom = shallowRef(1)
// 缺陷数量变化时收敛选中项，避免选中下标越界；没有缺陷则退出框选态。
watch(() => props.defects.length, count => { if (selected.value >= count) selected.value = Math.max(0, count - 1); if (!count) drawing.value = false })
watch(() => props.fileId, () => { drawing.value = false; zoom.value = 1 })
</script>
<template>
  <section class="qc-image-viewer" aria-label="质检原图与缺陷位置">
    <p v-if="loading" role="status">正在核验并读取原图…</p><div v-if="error" role="alert"><p class="wf-error">{{ error }}</p><button type="button" @click="reload">重新读取图片</button></div>
    <template v-if="url && image"><div class="qc-image-toolbar"><label class="qc-check"><input v-model="showBoxes" type="checkbox" />显示缺陷标注</label><label>图片缩放<select v-model="zoom"><option :value="1">适应宽度</option><option :value="1.5">150%</option><option :value="2">200%</option></select></label>
      <template v-if="editable && defects.length"><label>框选对应缺陷<select v-model="selected" :disabled="disabled"><option v-for="(item, index) in defects" :value="index" :key="index">{{ index + 1 }} · {{ item.defectType || '尚未命名' }}</option></select></label><button type="button" :disabled="disabled" :aria-pressed="drawing" @click="drawing = !drawing; showBoxes = true">{{ drawing ? '结束框选' : '在图上框选' }}</button></template>
    </div><p v-if="drawing" class="wf-note" role="status">拖动鼠标或触控框选当前缺陷；也可在缺陷表单中输入坐标。位置以显示方向归一化保存。</p>
    <div class="qc-image-scroll" role="region" tabindex="0" aria-label="图片预览，可滚动查看放大内容"><div :style="{ width: zoom * 100 + '%' }"><QualityImageCanvas :url="url" :name="image.name" :defects="defects" :selected="selected" :editable="editable && drawing && !disabled" :show-boxes="showBoxes" @box="emit('box', selected, $event)" /></div></div>
    <p class="qc-image-caption">{{ image.name }} · {{ image.width }} × {{ image.height }} px · {{ (image.size / 1024).toFixed(1) }} KB</p><details><summary>原图证据标识</summary><p class="qc-image-hash">文件：{{ image.id }}<br />SHA-256：{{ image.sha256 }}<br />EXIF 方向：{{ image.orientation }}；原始字节未改写</p></details></template>
  </section>
</template>
<style scoped>
.qc-image-viewer { min-width: 0; border: 1px solid #e3e8ef; border-radius: 6px; padding: 14px; background: #fff; }
.qc-image-toolbar { display: flex; gap: 12px; align-items: end; flex-wrap: wrap; margin-bottom: 12px; }
.qc-image-scroll { max-height: 620px; overflow: auto; border: 1px solid #e3e8ef; background: #f4f6f9; }
.qc-image-caption { color: #5c6d84; overflow-wrap: anywhere; }
.qc-image-hash { font: 12px/1.7 Consolas, monospace; overflow-wrap: anywhere; }
.qc-image-scroll:focus-visible { outline: 2px solid #2666e8; outline-offset: 2px; }
</style>
