<script setup lang="ts">
/**
 * 目录卡片图片区（设备/产品共用，含默认占位图与"上传/更换图片"入口）。
 * 路由：无独立路由，由 BusinessDeviceCard / QualityProductCard 通过 CatalogCard 的 image 插槽渲染。
 * 依赖接口：api/catalogImages.ts 的 catalogImages.content（GET .../card-images/{kind}/{id}/content，Blob）。
 * 关键交互：图片地址随 target.id / image.fileId / image.version / 当前工作区变化自动重取；
 *   无 files.view 权限时直接显示占位图、不发起请求；图片加载失败（onerror）会释放 URL 并提示"图片暂不可用"。
 * 约束：objectURL 必须在重取与组件卸载时 revoke（clear()），否则会持续占用内存。
 */
import { onBeforeUnmount, shallowRef, watch } from 'vue'
import { access, can } from '../../api/request'
import { catalogImages, type ImageTarget } from '../../api/catalogImages'
const props = defineProps<{ target: ImageTarget; editable?: boolean }>()
defineEmits<{ edit: [] }>()
const url = shallowRef(''), failed = shallowRef(false)
let controller: AbortController | undefined
/** 释放当前 objectURL 并中止在途请求；重取图片与组件卸载都必须先调用它。 */
function clear() { controller?.abort(); if (url.value) URL.revokeObjectURL(url.value); url.value = '' }
function imageFailed() { clear(); failed.value = true }
watch(() => [props.target.id, props.target.image?.fileId, props.target.image?.version, access.workspaceId], async () => {
  clear(); failed.value = false
  if (!props.target.image?.fileId || !can('files.view')) return
  const active = new AbortController(); controller = active
  try { const response = await catalogImages.content(props.target, active.signal); if (!active.signal.aborted) url.value = URL.createObjectURL(response.data) }
  catch { if (!active.signal.aborted) failed.value = true }
}, { immediate: true })
onBeforeUnmount(clear)
</script>
<template>
  <div class="catalog-photo">
    <img v-if="url" :src="url" :alt="target.name + '图片'" @error="imageFailed" />
    <svg v-else-if="target.kind === 'device'" viewBox="0 0 100 94" role="img" aria-label="默认设备图片" class="catalog-default">
      <g fill="#efedf7" stroke="#8a83a6" stroke-width="3" stroke-linejoin="round"><path d="M24 44V19h6v25M70 44V19h6v25" /><path d="M27 19V2M73 19V2" fill="none" /><rect x="17" y="43" width="66" height="31" rx="5" /><path d="M13 73h74v7H13zM19 80v5h6v-5M75 80v5h6v-5" /></g>
      <g stroke="#8a83a6" stroke-width="2.5" fill="none"><path d="M24 51v11M30 51v11M36 51v11M44 25q6-6 12 0M47 29q3-3 6 0" /><circle cx="50" cy="32" r="1" /></g><circle cx="57" cy="60" r="3" fill="#8982a4" /><circle cx="67" cy="60" r="3" fill="#7dbab4" /><circle cx="77" cy="60" r="3" fill="#d1bd7a" />
    </svg>
    <i v-else class="fa-solid fa-cubes catalog-product-placeholder" aria-label="默认产品图片" />
    <button v-if="editable" type="button" class="catalog-photo-edit" :aria-label="`${target.image?.fileId ? '更换' : '上传'}${target.name}图片`" @click="$emit('edit')"><i class="fa-solid fa-camera" aria-hidden="true" />{{ target.image?.fileId ? '更换图片' : '上传图片' }}</button>
    <span v-else-if="failed" class="catalog-photo-unavailable">图片暂不可用</span>
  </div>
</template>
<style scoped>
.catalog-photo { display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 5px; width: 96px; min-height: 103px; flex-shrink: 0; }
.catalog-photo img { width: 96px; height: 87px; object-fit: contain; border-radius: 6px; }.catalog-default { width: 83px; height: 82px; }.catalog-product-placeholder { color: #8982a4; font-size: 62px; padding: 10px 0; }
.catalog-photo .catalog-photo-edit { display: flex; align-items: center; justify-content: center; gap: 4px; padding: 3px 4px; border: 0; background: transparent; color: #59636f; font-size: 11px; line-height: 18px; }.catalog-photo .catalog-photo-edit:hover { color: #1269b5; background: #f1f8ff; }.catalog-photo-unavailable { font-size: 10px; color: #59636f; }
</style>
