<script setup lang="ts">
/**
 * WorkflowHttpSourceDialog —— 在工作流节点内就地新建/编辑 HTTP 数据源（不跳出编排页面）。
 *
 * 位置：WorkflowDataSourceBinding 与 WorkflowAiGenerator 共用。
 * 依赖接口：`api/collection.httpCollection.save(payload, id?)`（有 id 为更新，无 id 为新建）、
 * HttpSourceForm（字段与校验表单）。
 * 关键约束：
 * - 保存成功后必须回传 saved 事件，由父级把新数据源写回节点绑定（含新版本号），
 *   否则用户会以为"已选入"但节点仍指向旧版本；
 * - busy 期间禁止关闭弹窗，避免保存请求进行中被卸载导致状态丢失；
 * - 保存失败只展示错误、保留弹窗与用户输入，不自动重试。
 */
import { shallowRef } from 'vue'
import { ElMessage } from 'element-plus'
import { httpCollection, type HttpSource, type HttpSourceInput } from '../../api/collection'
import HttpSourceForm from '../collection/HttpSourceForm.vue'

const props = defineProps<{ modelValue: boolean; source: HttpSource | null }>()
const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  saved: [source: HttpSource]
}>()
const busy = shallowRef(false)
const error = shallowRef('')

/** 关闭弹窗：busy（保存中）时忽略，防止请求回调写入已卸载组件。 */
function close() {
  if (!busy.value) emit('update:modelValue', false)
}

/**
 * 保存 HTTP 数据源（有 source.id 为更新，否则新建）。
 * 成功后依次派发 saved 与关闭弹窗；失败只写入 error 文案，用户可修正后重试。
 */
async function save(payload: HttpSourceInput) {
  busy.value = true
  error.value = ''
  try {
    const response = await httpCollection.save(payload, props.source?.id)
    emit('saved', response.data)
    emit('update:modelValue', false)
    ElMessage.success(props.source ? 'HTTP 数据源已更新，节点版本已同步' : 'HTTP 数据源已创建并选入当前流程')
  } catch (cause) {
    error.value = (cause as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <el-dialog
    :model-value="modelValue"
    class="http-config-dialog"
    :title="source ? '编辑 HTTP 数据源' : '新建 HTTP 数据源'"
    width="min(960px, 96vw)"
    top="3vh"
    destroy-on-close
    append-to-body
    :close-on-click-modal="false"
    :close-on-press-escape="!busy"
    :show-close="!busy"
    @update:model-value="$emit('update:modelValue', $event)"
  >
    <HttpSourceForm
      v-if="modelValue"
      :key="source?.id || 'new'"
      :source="source"
      :busy="busy"
      :error="error"
      @save="save"
      @cancel="close"
    />
  </el-dialog>
</template>
