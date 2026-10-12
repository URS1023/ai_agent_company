<script setup lang="ts">
/**
 * 目录卡片图片上传/移除弹窗（设备与质检产品共用）。
 * 路由：无独立路由，由 BusinessDeviceWorkspace / QualityProductWorkspace 以 target 传入打开（target 为 null 即关闭）。
 * 依赖接口：api/catalogImages.ts 的 catalogImages.upload（POST，带 version 乐观锁）与 remove（DELETE，带 version）。
 * 关键交互：本地预览（objectURL 由本组件负责 revoke）；类型/大小在前端先拦（PNG/JPEG/WebP、≤5 MB）；
 *   移除按钮仅在已有图片时出现；成功后 emit('saved')（触发父组件重新加载目录）并关闭弹窗。
 * 约束：上传/移除过程中禁止关闭弹窗（busy 时 show-close/ESC/遮罩点击均被禁用），避免请求在途状态被中断。
 * 错误呈现：error 内联展示（后端 message 或本地上传校验提示）。
 */
import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { catalogImages, type ImageTarget } from '../../api/catalogImages'
const props = defineProps<{ target: ImageTarget | null }>()
const emit = defineEmits<{ close: []; saved: [] }>()
const file = shallowRef<File | null>(null), preview = shallowRef(''), busy = shallowRef(false), error = shallowRef('')
function clear() { if (preview.value) URL.revokeObjectURL(preview.value); preview.value = ''; file.value = null }
watch(() => props.target, () => { clear(); error.value = '' })
onBeforeUnmount(clear)
/** 选择文件：先重置 input（同一文件才能再次触发 change），再做类型/大小校验后生成本地预览。 */
function choose(event: Event) {
  const input = event.target as HTMLInputElement, selected = input.files?.[0]
  input.value = ''; if (!selected) return
  error.value = ''
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(selected.type) || selected.size > 5 * 1024 * 1024) { error.value = '请选择不超过 5 MB 的 PNG、JPG 或 WebP 图片'; return }
  clear(); file.value = selected; preview.value = URL.createObjectURL(selected)
}
/** 保存：remove=true 走删除（恢复默认图片），否则上传新文件；两者都会触发父组件刷新。 */
async function save(remove = false) {
  if (!props.target || busy.value || (!remove && !file.value)) return
  busy.value = true; error.value = ''
  try {
    if (remove) await catalogImages.remove(props.target)
    else await catalogImages.upload(props.target, file.value!)
    ElMessage.success(remove ? '已恢复默认图片' : '图片已保存'); emit('saved'); emit('close')
  } catch (cause) { error.value = (cause as Error).message }
  finally { busy.value = false }
}
</script>
<template>
  <el-dialog :model-value="!!target" :title="(target?.name || '') + ' · 图片'" width="min(460px, 95vw)" :close-on-click-modal="false" :close-on-press-escape="!busy" :show-close="!busy" @close="emit('close')">
    <div class="catalog-image-form">
      <p>图片为选填项，不上传也可正常使用工作流。</p>
      <label class="catalog-image-picker"><img v-if="preview" :src="preview" alt="待上传图片预览" /><span v-else><i class="fa-regular fa-image" aria-hidden="true" />选择图片</span><input type="file" accept="image/png,image/jpeg,image/webp" aria-label="选择卡片图片" :disabled="busy" @change="choose" /></label>
      <p class="catalog-image-hint">{{ file?.name || 'PNG / JPG / WebP，最大 5 MB' }}</p>
      <p v-if="error" role="alert" class="wf-error">{{ error }}</p>
    </div>
    <template #footer><el-button v-if="target?.image?.fileId" type="danger" plain :disabled="busy" @click="save(true)">移除图片</el-button><el-button :disabled="busy" @click="emit('close')">取消</el-button><el-button type="primary" :loading="busy" :disabled="!file" @click="save()">保存图片</el-button></template>
  </el-dialog>
</template>
<style scoped>
.catalog-image-form p { margin: 0 0 14px; color: #67707b; font-size: 13px; }.catalog-image-picker { display: flex; position: relative; align-items: center; justify-content: center; width: 100%; height: 200px; border: 1px dashed #bec8d4; border-radius: 10px; background: #f9fbfd; cursor: pointer; }.catalog-image-picker input { position: absolute; inset: 0; width: 100%; height: 100%; opacity: 0; cursor: pointer; }.catalog-image-picker:focus-within { outline: 3px solid #96c9fc; }.catalog-image-picker img { max-width: 100%; max-height: 180px; object-fit: contain; }.catalog-image-picker span { display: grid; gap: 12px; justify-items: center; color: #607286; }.catalog-image-picker i { font-size: 32px; }.catalog-image-form .catalog-image-hint { margin-top: 12px; overflow-wrap: anywhere; }
</style>
