/**
 * 质检产品目录（产品 → 模板/工作流）的组合式函数。
 * 职责：拉取目录快照、按关键字过滤并按产品分组、分页展示，以及跳转到对应质检工作流。
 * 过滤与分页：groups 为本地计算（关键字同时匹配产品名/编码/模板名/类别中文名），因此分页也在前端做；
 *   关键字变化会重置页码，分组数量变化时收敛页码，避免停在空白页。
 * 加载/取消：持有单个 AbortController，workspaceId 变化（切工作区/重新登录）时自动重新加载。
 * 错误暴露：error 字符串（页面空态与提示条），不向上抛出。
 */
import { computed, onBeforeUnmount, shallowRef, watch } from 'vue'
import { useRoute } from 'vue-router'
import { access } from '../api/request'
import { qualityCategories, qualityProducts, type QualityProductCatalogue, type QualityProductEntry, type QualityProductGroup } from '../api/qualityProducts'
import { openTopLevel } from '../utils/topLevelNavigation'

/**
 * 质检产品目录状态。
 * 返回：catalogue（原始快照）、groups/pagedGroups（过滤分组与分页结果）、keyword/page/pageSize/changeSize、
 *   currentEntries、unassociated（未关联产品的条目）/unassociatedCount、unconfigured（缺检验需求的产品）、load、open。
 * 副作用：open 以顶层方式打开质检工作流页（logs 决定进入日志页签，并带来源标识 from=quality 与搜索词）。
 */
export function useQualityProducts() {
  const route = useRoute()
  const catalogue = shallowRef<QualityProductCatalogue | null>(null)
  const loading = shallowRef(false), error = shallowRef('')
  const keyword = shallowRef(typeof route.query.q === 'string' ? route.query.q : '')
  const page = shallowRef(1), pageSize = shallowRef(12)
  let controller: AbortController | undefined
  const currentEntries = computed(() => catalogue.value?.entries || [])
  const groups = computed(() => {
    const search = keyword.value.trim().toLocaleLowerCase()
    const result = new Map<string, QualityProductGroup>()
    const products = new Map(catalogue.value?.products.map(product => [product.id, product]))
    for (const entry of currentEntries.value) {
      if (!entry.productId || (search && !`${entry.productName} ${entry.productCode} ${entry.templateName} ${qualityCategories[entry.category]}`.toLocaleLowerCase().includes(search))) continue
      const group = result.get(entry.productId) || { id: entry.productId, name: entry.productName, code: entry.productCode, entries: [], image: products.get(entry.productId)?.image }
      group.entries.push(entry); result.set(entry.productId, group)
    }
    return [...result.values()].sort((a, b) => a.code.localeCompare(b.code))
  })
  const pagedGroups = computed(() => groups.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
  watch(keyword, () => { page.value = 1 })
  watch(() => groups.value.length, count => { page.value = Math.min(page.value, Math.max(1, Math.ceil(count / pageSize.value))) })
  function changeSize(value: number) { pageSize.value = value; page.value = 1 }
  const unassociated = computed(() => currentEntries.value.filter(entry => !entry.productId))
  const unassociatedCount = computed(() => unassociated.value.reduce((total, entry) => total + entry.formCount, 0))
  const unconfigured = computed(() => catalogue.value?.products.filter(product => !product.hasRequirements) || [])
  async function load() {
    controller?.abort(); controller = new AbortController()
    const active = controller
    loading.value = true; error.value = ''
    try {
      const result = await qualityProducts(active.signal)
      if (!active.signal.aborted) catalogue.value = result.data
    } catch (cause) { if (!active.signal.aborted) error.value = (cause as Error).message }
    finally { if (!active.signal.aborted) loading.value = false }
  }
  function open(entry: QualityProductEntry, logs = false) {
    const search = keyword.value.trim() ? '&qualitySearch=' + encodeURIComponent(keyword.value.trim()) : ''
    openTopLevel(`/workflows/${encodeURIComponent(entry.workflowId)}${logs ? '/logs' : ''}?from=quality${search}`)
  }
  watch(() => access.workspaceId, () => { void load() }, { immediate: true })
  onBeforeUnmount(() => controller?.abort())
  return { catalogue, loading, error, keyword, groups, pagedGroups, page, pageSize, changeSize, currentEntries, unassociated, unassociatedCount, unconfigured, load, open }
}
