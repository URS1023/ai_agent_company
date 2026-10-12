import { computed, shallowRef, watch, type Ref } from 'vue'
import { workflows, type PublishedWorkflow, type Workflow } from '../api/workflows'

/** 正式运行契约随工作流版本刷新；旧请求和草稿字段均不能覆盖正在展示的发布契约。 */
export function usePublishedWorkflow(flow: Ref<Workflow>) {
  const contract = shallowRef<PublishedWorkflow | null>(null)
  const loading = shallowRef(false), error = shallowRef('')
  const revision = computed(() => flow.value.status === 'published' ? flow.value.publishedRevision : null)
  const ready = computed(() => !loading.value && !!contract.value && contract.value.revision === revision.value
    && contract.value.version === flow.value.version)
  watch(() => [flow.value.id, revision.value, flow.value.version] as const, async ([id, published], _previous, onCleanup) => {
    const controller = new AbortController()
    let cancelled = false
    onCleanup(() => { cancelled = true; controller.abort() })
    contract.value = null; error.value = ''; loading.value = false
    if (!id || !published) return
    loading.value = true
    try {
      const response = await workflows.published(id, controller.signal)
      if (cancelled) return
      if (response.data.revision !== published || response.data.version !== flow.value.version) throw new Error('发布版本已变化，请刷新页面后重新填写输入')
      contract.value = response.data
    } catch (cause) {
      if (!cancelled) error.value = cause instanceof Error ? cause.message : '无法加载发布版本，请刷新后重试'
    } finally { if (!cancelled) loading.value = false }
  }, { immediate: true })
  return { contract, loading, error, ready }
}
