/**
 * dateTime —— 时间显示工具。
 *
 * 位置：工作流日志、运行结果、监测等所有展示后端时间戳的地方。
 * 关键约束：后端记录时间可能是不带时区后缀的 UTC 字符串，展示前必须补 'Z' 再解析，
 * 否则浏览器会按本地时区解释，时间整体偏移数小时；空值或非法值统一返回占位符 '—'，不抛异常。
 */

/** Server record timestamps without an offset are stored in UTC. */
export function formatRecordTime(value?: string | null): string {
  if (!value) return '—'
  const source = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(value) ? value : `${value}Z`
  const parsed = new Date(source)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
  })
}
