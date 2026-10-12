<script setup lang="ts">
/**
 * 质检原图与缺陷框渲染画布（支持在图上拖拽框选缺陷区域）。
 * 路由：无独立路由，用于质检记录的图片证据（QualityImageEvidence）与缺陷标注交互。
 * 依赖接口：无请求；图片 url 由 useQualityImage 生成（objectURL），name 用于无障碍替代文本。
 * 关键交互：editable 时按住左键拖拽生成草稿框（指针捕获保证拖出元素仍能收到事件），
 *   抬起时若宽高 > 0.001 才 emit('box')，并把坐标四舍五入到 1e-6 精度；
 *   showBoxes 控制是否叠加已有缺陷框（只读预览可关闭）。
 * 约束：坐标一律使用 0–1 归一化值，与后端 DefectBox 契约一致（换图/换分辨率不会错位）。
 */
import { computed, shallowRef } from 'vue'
import type { DefectBox, QualityDefect } from '../../api/quality'
const props = withDefaults(defineProps<{ url: string; name: string; defects: QualityDefect[]; editable?: boolean; selected?: number; showBoxes?: boolean }>(), { editable: false, selected: 0, showBoxes: true })
const emit = defineEmits<{ box: [value: DefectBox] }>()
const start = shallowRef<[number, number] | null>(null), end = shallowRef<[number, number] | null>(null)
const draft = computed<DefectBox | null>(() => start.value && end.value ? [Math.min(start.value[0], end.value[0]), Math.min(start.value[1], end.value[1]), Math.abs(end.value[0] - start.value[0]), Math.abs(end.value[1] - start.value[1])] : null)
/** 把指针事件换算成 0–1 归一化坐标（相对当前元素的实际显示尺寸，做上下界裁剪）。 */
function point(event: PointerEvent): [number, number] {
  const rect = (event.currentTarget as HTMLElement).getBoundingClientRect()
  return [Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)), Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))]
}
function begin(event: PointerEvent) {
  if (!props.editable || event.button !== 0 || !props.defects[props.selected]) return
  event.preventDefault(); (event.currentTarget as HTMLElement).setPointerCapture(event.pointerId); start.value = point(event); end.value = start.value
}
function finish(event: PointerEvent) {
  if (!start.value) return
  end.value = point(event)
  if (draft.value && draft.value[2] > 0.001 && draft.value[3] > 0.001) emit('box', draft.value.map(value => Math.round(value * 1000000) / 1000000) as DefectBox)
  start.value = null; end.value = null
}
function boxStyle(box: DefectBox) { return { left: box[0] * 100 + '%', top: box[1] * 100 + '%', width: box[2] * 100 + '%', height: box[3] * 100 + '%' } }
</script>
<template>
  <div class="qc-image-canvas" :class="{ 'qc-image-drawing': editable }" @pointerdown="begin" @pointermove="start && (end = point($event))" @pointerup="finish" @pointercancel="start = null; end = null">
    <img class="qc-original-image" :src="url" :alt="name + '，原始质检图片'" draggable="false" />
    <template v-if="showBoxes"><template v-for="(item, index) in defects" :key="index"><span v-if="item.bbox" class="qc-defect-box" :class="{ 'qc-defect-selected': editable && index === selected }" :style="boxStyle(item.bbox)" aria-hidden="true"><span class="qc-defect-number">{{ index + 1 }}</span></span></template></template>
    <span v-if="draft" class="qc-defect-box qc-defect-draft" :style="boxStyle(draft)" aria-hidden="true" />
  </div>
</template>
<style scoped>
.qc-image-canvas { position: relative; width: 100%; line-height: 0; }
.qc-original-image { width: 100%; height: auto; display: block; user-select: none; }
.qc-image-drawing { cursor: crosshair; touch-action: none; }
.qc-defect-box { position: absolute; border: 2px solid #b42318; box-sizing: border-box; pointer-events: none; line-height: 1; }
.qc-defect-number { display: inline-block; background: #b42318; color: #fff; padding: 3px 5px; font: 12px/1.2 Consolas, monospace; }
.qc-defect-selected, .qc-defect-draft { border-color: #2666e8; border-style: dashed; }
</style>
