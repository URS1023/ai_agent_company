/**
 * 质检产品目录接口层（/api/v1/quality/products）。
 * 职责：一次拉取质检工作台所需的"产品 → 检验模板/需求/工作流"目录快照，
 *   前端据此渲染产品卡片、待补全条目（pendingEntries）与目录问题提示（issues）。
 * 约束：列表接口本身就是快照（sourceLabel/snapshotAt 标明来源与采集时间），页面需展示时间让用户判断新鲜度；
 *   只读，不产生写操作；signal 用于切换产品分类时取消上一次请求。
 */
import { get } from './request'
import type { CatalogImage } from './catalogImages'

export const qualityCategories = { incoming: '来料质检', process: '过程质检', finished: '成品质检' } as const
export type QualityCategory = keyof typeof qualityCategories
export interface QualityProduct { id: string; name: string; code: string; hasRequirements: boolean; image?: CatalogImage | null }
export interface QualityProductGroup { id: string; name: string; code: string; entries: QualityProductEntry[]; image?: CatalogImage | null }
export interface QualityProductEntry {
  category: QualityCategory; productId: string | null; productName: string; productCode: string
  templateId: string; templateName: string; requirementCount: number; formCount: number
  workflowId: string; workflowName: string; status: string; publishedRevision: number | null
}
export interface QualityProductCatalogue {
  sourceLabel: string; snapshotAt: string; products: QualityProduct[]; entries: QualityProductEntry[]
  issues?: { code: string; count: number; message: string }[]
  pendingEntries?: { key?: string; templateName: string; reason: string; formCount: number }[]
}
/**
 * 拉取质检产品目录：GET /api/v1/quality/products。
 * 返回：{ data: QualityProductCatalogue }，含 products/entries/issues/pendingEntries 与快照时间。
 * 失败表现：鉴权失败或后端异常时抛 Error（message 已由拦截器转成中文），调用方需保留空态并允许重试。
 */
export const qualityProducts = (signal?: AbortSignal) => get<{ data: QualityProductCatalogue }>('/api/v1/quality/products', {}, { signal })
