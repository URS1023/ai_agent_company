<script setup lang="ts">
/**
 * WorkflowListOperationForm —— 列表操作节点（list-operator）的过滤/排序配置表单。
 *
 * 位置：WorkflowNodeForm 中 kind === 'list-operator' 时渲染。
 * 关键约束：
 * - 过滤表达式复用 ExpressionEditor，字段列表额外注入 `item`（表示当前列表项），
 *   这是后端约定的项内引用名，不可改名；
 * - 仅当对应开关（filterEnabled / sortEnabled）开启时才展示并提交相关配置。
 */
import ExpressionEditor from './ExpressionEditor.vue'
const props = defineProps<{ config: Record<string, any>; fields: string[] }>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
function patch(key: string, value: unknown) { emit('update', { ...props.config, [key]: value }) }
</script>

<template>
  <section class="wf-list-operation-form">
    <label class="wf-check"><input type="checkbox" :checked="config.filterEnabled" @change="patch('filterEnabled', ($event.target as HTMLInputElement).checked)" />过滤条件</label>
    <ExpressionEditor v-if="config.filterEnabled" :model-value="config.expression" :fields="['item', ...fields]" @update:model-value="patch('expression', $event)" />
    <label class="wf-check"><input type="checkbox" :checked="config.sortEnabled" @change="patch('sortEnabled', ($event.target as HTMLInputElement).checked)" />排序</label>
    <template v-if="config.sortEnabled">
      <label>排序字段<input :value="config.sortField" placeholder="对象字段名；数值或文本列表留空" @input="patch('sortField', ($event.target as HTMLInputElement).value)" /></label>
      <label>排序方式<select :value="config.sortOrder" @change="patch('sortOrder', ($event.target as HTMLSelectElement).value)"><option value="asc">升序</option><option value="desc">降序</option></select></label>
    </template>
    <label class="wf-check"><input type="checkbox" :checked="config.limitEnabled" @change="patch('limitEnabled', ($event.target as HTMLInputElement).checked)" />取前 N 项</label>
    <label v-if="config.limitEnabled">数量<input type="number" min="1" max="10000" :value="config.limit" @change="patch('limit', Number(($event.target as HTMLInputElement).value))" /></label>
  </section>
</template>
