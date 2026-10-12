<script setup lang="ts">
/**
 * 预警策略表单（策略名称/设备/问题定位字段/专家经验/状态 + 多条规则项）。
 * 路由：无独立路由，供预警工作台的新增/编辑策略弹窗使用（v-model 传 AlertRecord）。
 * 依赖接口：本组件不发请求；设备/专家经验/测点由父组件加载后传入，规则表达式编辑器复用工作流的 ExpressionEditor。
 * 关键交互：patch() 采用整对象替换以触发 v-model 更新（禁止直接改传入对象）；规则项增删互斥（至少 1 条、上限 100 条）。
 * 约束：设备被工作流锁定时只展示"固定设备"，不允许在表单里改设备（改设备会导致既有绑定失效）；
 *   保存会产生新的策略修订，已引用旧修订的工作流不会自动跟随（提示文案已说明）。
 */
import { computed } from 'vue'
import { newAlertCondition, type AlertRecord } from '../../api/alerts'
import ExpressionEditor from '../workflows/ExpressionEditor.vue'
const props = defineProps<{ devices: AlertRecord[]; advice: AlertRecord[]; points: AlertRecord[]; disabled: boolean; lockedDeviceId?: string }>()
const model = defineModel<AlertRecord>({ required: true })
/** 表达式可选字段：只能取当前所选设备的测点字段（跨设备字段会导致运行时取不到值）。 */
const fields = computed(() => props.points.filter(point => point.deviceId === model.value.deviceId).map(point => point.fieldName as string))
/** 设备下拉只列出适用预警域（domains 含 'alert'）的设备，避免把质检专用设备绑进预警策略。 */
const devices = computed(() => props.devices.filter(device => device.domains.includes('alert')))
const lockedDevice = computed(() => devices.value.find(device => device.id === props.lockedDeviceId))
/** 单字段更新：以新对象替换 model（v-model 是 defineModel，必须赋值触发更新，不能就地修改）。 */
function patch(key: string, value: any) { model.value = { ...model.value, [key]: value } }
/** 更新第 index 条规则项：同样整体替换 rules 数组，保证父组件收到变更。 */
function rule(index: number, changes: object) { patch('rules', model.value.rules.map((item: AlertRecord, i: number) => i === index ? { ...item, ...changes } : item)) }
</script>
<template>
  <fieldset class="alert-rule-form" :disabled="disabled">
    <div class="equipment-form"><label>策略名称<input :value="model.name" required maxlength="160" @input="patch('name', ($event.target as HTMLInputElement).value)" /></label><div v-if="lockedDeviceId" class="wf-bound-resource"><small>固定设备</small><strong>{{ lockedDevice?.name || model.deviceId }}</strong><span>{{ lockedDevice?.code }}</span></div><label v-else>设备<select :value="model.deviceId" required @change="patch('deviceId', ($event.target as HTMLSelectElement).value)"><option value="">请选择预警设备</option><option v-for="device in devices" :key="device.id" :value="device.id">{{ device.name }} · {{ device.code }}</option></select></label><label>问题定位字段<input :value="model.recordKeyField" maxlength="120" placeholder="例如 device_id；留空使用原始行号" @input="patch('recordKeyField', ($event.target as HTMLInputElement).value)" /></label><label>专家经验<select :value="model.adviceId || ''" @change="patch('adviceId', ($event.target as HTMLSelectElement).value || null)"><option value="">不关联</option><option v-for="item in advice" :key="item.id" :value="item.id">{{ item.name }}</option></select></label><label>状态<select :value="model.status" @change="patch('status', ($event.target as HTMLSelectElement).value)"><option value="active">启用</option><option value="inactive">停用</option></select></label><label class="wide-field">用途说明<textarea :value="model.description" maxlength="3000" rows="2" @input="patch('description', ($event.target as HTMLTextAreaElement).value)" /></label></div>
    <div v-for="(item, index) in model.rules" :key="item.id" class="wf-rule-card"><label>规则项名称<input :value="item.name" required maxlength="160" @input="rule(index, { name: ($event.target as HTMLInputElement).value })" /></label><ExpressionEditor :model-value="item.expression" :fields="fields" @update:model-value="rule(index, { expression: $event })" /><label>问题等级<select :value="item.severity" @change="rule(index, { severity: ($event.target as HTMLSelectElement).value })"><option value="info">提示</option><option value="warning">警告</option><option value="critical">严重</option></select></label><label>问题说明<input :value="item.message" maxlength="1000" @input="rule(index, { message: ($event.target as HTMLInputElement).value })" /></label><button type="button" :disabled="model.rules.length <= 1" @click="patch('rules', model.rules.filter((_: unknown, i: number) => i !== index))">移除规则项</button></div>
    <button type="button" :disabled="model.rules.length >= 100" @click="patch('rules', [...model.rules, newAlertCondition()])">添加规则项</button><p class="wf-note">满足异常条件时生成问题。保存产生新修订，工作流中已经选择的旧修订不自动改变。</p>
  </fieldset>
</template>
