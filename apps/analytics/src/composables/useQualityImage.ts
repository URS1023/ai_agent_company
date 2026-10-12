/**
 * 质检图片读取的组合式函数：按 fileId + inspectionId 取元数据与二进制内容，并生成可直接展示的 objectURL。
 * 状态：url（objectURL）、image（元数据）、loading、error，以及 reload() 手动重取。
 * 加载/取消策略：watch 的 cleanup 中 abort 请求并 revoke 上一次 objectURL；
 *   同时监听 workspaceId/userId，切换工作区或用户时自动重取（避免串号看到他人图片）。
 * 权限：需同时具备 quality.view 与 files.view，否则直接给出中文错误且不发请求。
 * 失败表现：错误写入 error（组件以占位图 + 文案展示），不使用 ElMessage。
 */
import { shallowRef, watch, type Ref } from 'vue'
import { access, can } from '../api/request'
import { quality, type QualityImage } from '../api/quality'

/** Cancel stale image reads and revoke object URLs on every context change/unmount. */
export function useQualityImage(fileId: Ref<string>, inspectionId: Ref<string | undefined>, snapshot: Ref<QualityImage | undefined>) {
  const url = shallowRef(''), image = shallowRef<QualityImage | null>(null), loading = shallowRef(false), error = shallowRef('')
  const revision = shallowRef(0)
  watch([fileId, inspectionId, snapshot, revision, () => access.workspaceId, () => access.userId], async ([id], _, cleanup) => {
    const controller = new AbortController()
    let objectUrl = ''
    cleanup(() => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl) })
    url.value = ''; image.value = null; error.value = ''; loading.value = false
    if (!id) return
    if (!can('quality.view') || !can('files.view')) { error.value = '需要质检与图片资料查看权限'; return }
    loading.value = true
    try {
      const metadata = snapshot.value || (await quality.image(id, controller.signal)).data
      const blob = await quality.imageContent(id, inspectionId.value, controller.signal)
      if (controller.signal.aborted) return
      objectUrl = URL.createObjectURL(blob); image.value = metadata; url.value = objectUrl
    } catch (cause) { if (!controller.signal.aborted) error.value = (cause as Error).message }
    finally { if (!controller.signal.aborted) loading.value = false }
  }, { immediate: true })
  return { url, image, loading, error, reload: () => { revision.value++ } }
}
