<script setup lang="ts">
/**
 * WorkflowOutputValuesForm —— 输出结果节点（output-values）的结论配置表单。
 *
 * 位置：WorkflowNodeForm 中 kind === 'output-values' 时渲染。
 * 关键约束：
 * - mode 决定结论文案：alert 域为 正常/预警，quality 域为 合格/不合格，切换 mode 时模板文案随之变化，
 *   但存储的仍是同一组枚举值（normal/warning/pending），不因文案改动而改变数据；
 * - 未勾选任何输出字段时保留全部字段（"未勾选即全量"），这是与后端一致的语义，不能改成空集；
 * - 结果与说明列是业务视图识别判定输出的依据（WorkflowRunResult.businessOutput），不可随意改名。
 */
const props = defineProps<{ config: Record<string, any>; fields: string[]; disabled: boolean }>()
const emit = defineEmits<{ update: [config: Record<string, any>] }>()
function patch(key: string, value: any) { emit('update', { ...props.config, [key]: value }) }
function toggle(field: string, enabled: boolean) { patch('fields', enabled ? [...new Set([...(props.config.fields || []), field])] : (props.config.fields || []).filter((f: string) => f !== field)) }
</script>
<template>
  <div>
    <label>结果用途<select :value="config.mode || 'alert'" @change="patch('mode', ($event.target as HTMLSelectElement).value)"><option value="alert">设备预警</option><option value="quality">质量检查</option></select></label>
    <label>输出结果<select :value="config.conclusion" @change="patch('conclusion', ($event.target as HTMLSelectElement).value)"><option value="normal">{{ config.mode === 'quality' ? '合格' : '正常' }}</option><option value="warning">{{ config.mode === 'quality' ? '不合格' : '预警' }}</option><option value="pending">待确认</option></select></label>
    <label>说明<textarea :value="config.message" rows="3" placeholder="例如：已超过该润滑点的保养周期" @input="patch('message', ($event.target as HTMLTextAreaElement).value)" /></label>
    <details open><summary>输出哪些字段</summary><p class="wf-note">未勾选时保留全部字段。</p><label v-for="field in fields" :key="field" class="wf-check"><input type="checkbox" :checked="config.fields?.includes(field)" @change="toggle(field, ($event.target as HTMLInputElement).checked)" />{{ field }}</label></details>
  </div>
</template>
