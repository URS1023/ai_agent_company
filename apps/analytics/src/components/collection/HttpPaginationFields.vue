<script setup lang="ts">
/**
 * HTTP 采集源的分页参数编辑块（v-model 绑定 HttpSourceInput.pagination）。
 * 路由：无独立路由，嵌入 HttpSourceForm 的采集配置区。
 * 依赖接口：无请求；字段契约见 api/collection.ts 的 HttpSourceInput.pagination。
 * 关键交互：勾选"按页采集"写入默认分页参数（start=0、step=25、pages=4），取消勾选置 null（表示只请求一次）。
 * 约束：分页参数名限定为 [A-Za-z][A-Za-z0-9_.-]*（与后端替换规则一致）；页数上限 20，
 *   实际请求数仍受整批超时与结果行数上限约束（提示文案已说明）。
 */
import type { HttpSourceInput } from '../../api/collection'
const props = defineProps<{ modelValue: HttpSourceInput['pagination'] }>()
const emit = defineEmits<{ 'update:modelValue': [value: HttpSourceInput['pagination']] }>()
function toggle(event: Event) {
  emit('update:modelValue', (event.target as HTMLInputElement).checked ? { parameter: 'start', start: 0, step: 25, pages: 4 } : null)
}
function update(key: string, event: Event) {
  if (!props.modelValue) return
  const value = (event.target as HTMLInputElement).value
  emit('update:modelValue', { ...props.modelValue, [key]: key === 'parameter' ? value : Number(value) })
}
</script>
<template>
  <div class="http-pagination-fields">
    <label class="http-check"><input type="checkbox" :checked="!!modelValue" @change="toggle" /> 按页采集</label>
    <template v-if="modelValue">
      <div class="http-form-grid">
        <label>分页参数<input :value="modelValue.parameter" required maxlength="80" pattern="[A-Za-z][A-Za-z0-9_.-]*" @input="update('parameter', $event)" /></label>
        <label>起始值<input type="number" :value="modelValue.start" min="0" max="10000000" required @input="update('start', $event)" /></label>
        <label>每页递增<input type="number" :value="modelValue.step" min="1" max="100000" required @input="update('step', $event)" /></label>
        <label>采集页数<input type="number" :value="modelValue.pages" min="1" max="20" required @input="update('pages', $event)" /></label>
      </div>
      <p class="http-note">在同一地址中替换分页参数，保留其他查询条件。例如 start 从 0 开始，每页加 25，采集 4 页即请求 0、25、50、75。受整批超时与结果上限限制。</p>
    </template>
  </div>
</template>
