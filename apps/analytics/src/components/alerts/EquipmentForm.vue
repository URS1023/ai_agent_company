<script setup lang="ts">
/**
 * 设备/测点/专家经验登记表单（按 kind 切换字段集），由预警工作台的新增/编辑弹窗以 v-model 使用。
 * 依赖接口：本组件不发请求；设备、可选采集源由父组件传入（sources 来自 HTTP 采集源 + 数据库数据源合并）。
 * 关键交互：字段与校验由 fields 声明式生成（required/max/long 决定控件类型与长度限制）；
 *   patch() 整对象替换以触发 v-model 更新；测点所属设备在已有 id 时禁止修改（改动会使既有映射失效）。
 * 约束：设备可勾选适用业务（预警/质检），质检检测服务只列 HTTP 源且可多选；
 *   下拉列表最多展示最近 200 项，超出时给出提示（其它记录去对应管理页按名称查询）。
 */
import { computed } from 'vue'
import type { AlertRecord, RegistryKind } from '../../api/alerts'
const props = defineProps<{ kind: RegistryKind; devices: AlertRecord[]; sources: AlertRecord[]; disabled: boolean }>()
const model = defineModel<AlertRecord>({ required: true })
interface Field { key: string; label: string; max: number; required?: boolean; long?: boolean }
const fields = computed<Field[]>(() => props.kind === 'devices' ? [
  { key: 'code', label: '设备编码', max: 80, required: true }, { key: 'name', label: '设备名称', max: 160, required: true },
  { key: 'category', label: '设备类型', max: 100 }, { key: 'model', label: '型号', max: 100 }, { key: 'manufacturer', label: '生产商', max: 160 },
  { key: 'location', label: '安装位置', max: 300 }, { key: 'protocol', label: '协议标识', max: 40, required: true }, { key: 'description', label: '设备备注', max: 3000, long: true },
] : props.kind === 'points' ? [
  { key: 'code', label: '测点号', max: 80, required: true }, { key: 'name', label: '测点名称', max: 160, required: true },
  { key: 'fieldName', label: '采集字段名', max: 120, required: true }, { key: 'unit', label: '单位', max: 40 }, { key: 'pointType', label: '测点类型', max: 40, required: true },
  { key: 'deviceKey', label: '来源设备 Key', max: 120 }, { key: 'prefix', label: '来源前缀', max: 120 }, { key: 'description', label: '测点说明', max: 3000, long: true },
] : [
  { key: 'name', label: '经验名称', max: 160, required: true }, { key: 'conditionCode', label: '工况编码', max: 80 },
  { key: 'operatingCondition', label: '适用工况', max: 300 }, { key: 'alertType', label: '告警类型', max: 80 },
  { key: 'conditionDescription', label: '告警条件说明', max: 3000, long: true }, { key: 'content', label: '经验内容', max: 10000, long: true },
  { key: 'responsePlan', label: '处置预案', max: 10000, long: true }, { key: 'planShort', label: '预案简称', max: 200 },
])
/** 质检检测服务候选：只允许 HTTP 采集源（外观/成分检测通过固定源调用），数据库源不能作为检测器。 */
const qualitySources = computed(() => props.sources.filter(source => source.type === 'http'))
/** 单字段更新：必须整体替换 model 对象，直接改传入对象不会触发 v-model 变更。 */
function patch(key: string, value: any) { model.value = { ...model.value, [key]: value } }
/** 适用业务勾选（alert/quality）；取消勾选时从数组移除，避免出现重复项。 */
function domain(value: string, checked: boolean) { patch('domains', checked ? [...model.value.domains, value] : model.value.domains.filter((item: string) => item !== value)) }
</script>
<template>
  <fieldset class="equipment-form" :disabled="disabled">
    <label v-if="kind === 'points'">所属设备<select :value="model.deviceId" required :disabled="!!model.id" @change="patch('deviceId', ($event.target as HTMLSelectElement).value)"><option value="">请选择设备</option><option v-for="device in devices" :key="device.id" :value="device.id">{{ device.name }} · {{ device.code }}</option></select></label>
    <label v-for="field in fields" :key="field.key" :class="{ 'wide-field': field.long }">{{ field.label }}<textarea v-if="field.long" :value="model[field.key]" rows="3" :maxlength="field.max" @input="patch(field.key, ($event.target as HTMLTextAreaElement).value)" /><input v-else :value="model[field.key]" :required="field.required" :maxlength="field.max" @input="patch(field.key, ($event.target as HTMLInputElement).value)" /></label>
    <template v-if="kind === 'devices'"><label>预警 / 通用主采集源<select :value="model.datasourceId || ''" @change="patch('datasourceId', ($event.target as HTMLSelectElement).value || null)"><option value="">暂不绑定</option><option v-for="source in sources" :key="source.id" :value="source.id">{{ source.name }} · {{ source.type }}</option></select></label><label class="wide-field">质检检测服务<el-select aria-label="质检检测服务" :model-value="model.qualityDatasourceIds || []" multiple filterable clearable collapse-tags collapse-tags-tooltip placeholder="可选择多个外观或成分 HTTP 服务" :disabled="disabled" @update:model-value="patch('qualityDatasourceIds', $event)"><el-option v-for="source in qualitySources" :key="source.id" :value="source.id" :label="source.name + ' · HTTP · v' + source.version" :disabled="source.enabled === false" /></el-select><small>一个设备可关联多个质检服务；检测流程仍会为每个指标固定具体服务和版本。</small></label><div class="wide-field"><span>适用业务</span><div class="wf-domains"><label><input type="checkbox" :checked="model.domains.includes('alert')" @change="domain('alert', ($event.target as HTMLInputElement).checked)" />设备预警</label><label><input type="checkbox" :checked="model.domains.includes('quality')" @change="domain('quality', ($event.target as HTMLInputElement).checked)" />质量检查</label></div></div><p class="wide-field wf-note">连接地址、请求体、JSONPath 和凭据统一在数据源配置。此处登记协议不代表串口或 SDK 已经联通。</p></template>
    <template v-if="kind === 'points'"><label>期望同步间隔（分钟）<input type="number" min="1" max="525600" :value="model.frequencyMinutes" @input="patch('frequencyMinutes', ($event.target as HTMLInputElement).value ? Number(($event.target as HTMLInputElement).value) : null)" /></label><label class="wf-inline-check"><input type="checkbox" :checked="model.failureDiagnosis" @change="patch('failureDiagnosis', ($event.target as HTMLInputElement).checked)" />用于故障诊断</label><p class="wide-field wf-note">这里保存测点映射和期望频率；实际采集由工作流执行，不会自动创建定时任务。</p></template>
    <label>状态<select :value="model.status" @change="patch('status', ($event.target as HTMLSelectElement).value)"><option value="active">在用</option><option value="inactive">停用</option><option v-if="kind === 'devices'" value="maintenance">维护</option></select></label>
    <p v-if="devices.length >= 200 || sources.length >= 200" class="wide-field wf-note">选择列表当前展示最近 200 项；其他记录可从对应管理页按名称查询。</p>
  </fieldset>
</template>
