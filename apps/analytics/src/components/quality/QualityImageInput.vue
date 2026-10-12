<script setup lang="ts">
/**
 * 质检原图上传控件（v-model 绑定的值是图片 fileId）。
 * 路由：无独立路由，用于外观样本录入（质检流程向导/质检记录表单）。
 * 依赖接口：api/quality.ts 的 quality.uploadImage（POST /api/v1/quality/images，multipart）。
 * 关键交互：选择文件即上传（成功后写入 fileId 并显示文件名），父组件通过 uploading 事件感知上传中状态
 *   （用于阻止提交）；"移除本次图片关联"只清空本地绑定，不删除服务端图片。
 * 约束：仅 PNG/JPEG/WebP、单张不超过 5 MB（超限在前端拦截并提示）；需同时具备 quality.create 与
 *   files.create / files.view 权限，缺权限时禁用并给出说明；上传保留原图，不触发任何检测。
 * 失败表现：error 就近展示；组件卸载或重新选择文件时 abort 上一次上传。
 */
import { computed, onBeforeUnmount, shallowRef } from 'vue'
import { can } from '../../api/request'
import { quality } from '../../api/quality'
const model = defineModel<string | null>({ default: null })
defineProps<{ disabled: boolean }>()
const emit = defineEmits<{ uploading: [value: boolean] }>()
const busy = shallowRef(false), error = shallowRef(''), filename = shallowRef('')
const canUpload = computed(() => can('files.create') && can('files.view') && can('quality.create'))
let controller: AbortController | null = null
/**
 * 上传图片：先做大小与权限校验，再调用 uploadImage；成功后把 fileId 写入 v-model。
 * 约束：同一时刻只保留一个在途上传（重新选择会 abort 上一个）；input.value 必须清空，否则同一文件无法再次触发 change。
 */
async function upload(event: Event) {
  const input = event.target as HTMLInputElement, file = input.files?.[0]
  if (!file || busy.value) return
  error.value = ''
  if (file.size > 5 * 1024 * 1024) { error.value = '图片不能超过 5 MB，请选择较小的原始图片'; input.value = ''; return }
  controller?.abort(); controller = new AbortController(); const active = controller
  busy.value = true; emit('uploading', true)
  try {
    const response = await quality.uploadImage(file, active.signal)
    if (!active.signal.aborted) { model.value = response.data.id; filename.value = response.data.name }
  } catch (cause) { if (!active.signal.aborted) error.value = (cause as Error).message }
  finally { if (!active.signal.aborted) { busy.value = false; emit('uploading', false); input.value = '' } }
}
onBeforeUnmount(() => { controller?.abort(); emit('uploading', false) })
</script>
<template>
  <div class="qc-image-input"><label>上传质检原图<input type="file" accept="image/png,image/jpeg,image/webp" :disabled="disabled || busy || !canUpload" @change="upload" /></label><p class="wf-note">PNG / JPEG / WebP，单张最多 5 MB。上传保留原图，不执行检测；记录保存后可追溯哈希和缺陷位置。</p><p v-if="!canUpload" class="wf-note">上传需要质检新增、文件新增及文件查看权限；当前账号未全部具备，请联系管理员。</p><p v-if="busy" role="status">正在上传并核验图片…</p><p v-else-if="filename && model" role="status">已上传：{{ filename }}</p><button v-if="model" type="button" :disabled="disabled || busy" @click="model = null; filename = ''">移除本次图片关联</button><p v-if="error" class="wf-error" role="alert">{{ error }}</p></div>
</template>
