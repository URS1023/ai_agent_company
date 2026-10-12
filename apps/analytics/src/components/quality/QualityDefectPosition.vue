<script setup lang="ts">
/**
 * 缺陷位置标注（归一化坐标 x/y/w/h 的手工输入与清除）。
 * 路由：无独立路由，嵌在 QualityDefectEditor 内，也可被“原图框选”流程反向写入（父组件传入 modelValue）。
 * 依赖接口：无请求。坐标约定：取值 0–1 的归一化值，宽高必须大于 0，且 x+w、y+h 不得超过 1（见 box 计算）。
 * 关键交互：编辑态用草稿 draft 承载输入，只有点击“应用位置”才写回（取消/外部值变化都会退出编辑态）；
 *   非法输入（缺项、越界、非有限数）时应用按钮禁用，不产生半有效数据。
 */
import { computed, shallowRef, watch } from 'vue'
import type { DefectBox } from '../../api/quality'

const props = defineProps<{ modelValue?: DefectBox | null; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: DefectBox | null] }>()
const editing = shallowRef(false)
const draft = shallowRef<string[]>(['', '', '', ''])
const labels = ['左侧 X', '顶部 Y', '框宽 W', '框高 H']
/** 校验后的坐标框：四项齐全、均为有限数、宽高 > 0、且不越出 0–1 范围时返回，否则为 null（应用按钮随之禁用）。 */
const box = computed<DefectBox | null>(() => {
  if (draft.value.some(value => !value.trim())) return null
  const values = draft.value.map(Number) as DefectBox
  const [x, y, width, height] = values
  return values.every(Number.isFinite) && x >= 0 && y >= 0 && width > 0 && height > 0 &&
    x + width <= 1 && y + height <= 1 ? values : null
})
// 外部值变化（例如在原图上重新框选）时退出编辑态，避免草稿与已保存坐标不一致。
watch(() => props.modelValue, () => { editing.value = false }, { deep: true })
/** 进入编辑：以当前坐标初始化草稿（无坐标时为四个空串，强制用户完整填写）。 */
function edit() {
  draft.value = props.modelValue?.map(String) || ['', '', '', '']
  editing.value = true
}
function coordinate(index: number, value: string) {
  draft.value = draft.value.map((item, i) => i === index ? value : item)
}
function apply() {
  if (props.disabled || !box.value) return
  emit('update:modelValue', box.value)
  editing.value = false
}
</script>

<template>
  <div class="qc-span qc-defect-position">
    <p v-if="modelValue">已标注位置：{{ modelValue.join('，') }}</p>
    <p v-else class="wf-note">尚未标注缺陷位置。</p>
    <div class="wf-actions">
      <button type="button" :disabled="disabled" @click="edit">{{ modelValue ? '编辑缺陷位置' : '标注缺陷位置' }}</button>
      <button v-if="modelValue" type="button" :disabled="disabled" @click="emit('update:modelValue', null)">移除位置标注</button>
    </div>
    <fieldset v-if="editing" :disabled="disabled" class="qc-form-grid">
      <legend>输入实际位置坐标</legend>
      <label v-for="(label, index) in labels" :key="label">{{ label }}
        <input type="number" min="0" max="1" step="any" :value="draft[index]" @input="coordinate(index, ($event.target as HTMLInputElement).value)" />
      </label>
      <p class="qc-span wf-note">请填写全部四个坐标，宽、高必须大于 0，X + W 和 Y + H 不得超过 1。点击“应用位置”后才会写入报告；也可在上方原图中框选。</p>
      <div class="qc-span wf-actions">
        <button type="button" :disabled="disabled || !box" @click="apply">应用位置</button>
        <button type="button" :disabled="disabled" @click="editing = false">取消位置编辑</button>
      </div>
    </fieldset>
  </div>
</template>
