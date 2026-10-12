/**
 * 台账图片接口层（/api/v1/alerts/card-images/{kind}/{id}），设备与质检产品共用。
 * 职责：读取/上传/删除卡片图片；图片内容以 Blob 返回，由调用方自行 createObjectURL 并负责 revoke。
 * 关键约束：上传与删除都携带当前图片 version 做乐观锁，避免并发覆盖；
 *   图片地址按 kind+id 拼装且必须 encodeURIComponent，禁止把未编码的 id 拼进路径。
 */
import request, { del, post } from './request'

export interface CatalogImage { fileId: string | null; version: number }
export interface ImageTarget { kind: 'device' | 'product'; id: string; name: string; image?: CatalogImage | null }
const path = (target: Pick<ImageTarget, 'kind' | 'id'>) => `/api/v1/alerts/card-images/${target.kind}/${encodeURIComponent(target.id)}`
export const catalogImages = {
  /** 读取图片二进制：GET {path}/content，responseType=blob；调用方需在组件卸载时释放 objectURL。 */
  content: (target: ImageTarget, signal?: AbortSignal) => request.get<Blob>(path(target) + '/content', { responseType: 'blob', signal }),
  /**
   * 上传图片：POST {path}（multipart/form-data，字段名 file）。
   * 关键字段：query version 传当前图片版本（无图传 0），用于并发覆盖保护。
   */
  upload: (target: ImageTarget, file: File) => {
    const data = new FormData(); data.append('file', file)
    return post<{ data: CatalogImage }>(path(target), data, { params: { version: target.image?.version || 0 } })
  },
  /** 删除图片：DELETE {path}?version=...；版本不匹配会失败，提示用户先刷新再删。 */
  remove: (target: ImageTarget) => del<{ data: CatalogImage }>(path(target), { params: { version: target.image?.version } }),
}
