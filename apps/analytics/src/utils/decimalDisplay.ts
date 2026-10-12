/** 精确统计值只做字符串排版；不将后端 Decimal 字符串转换为浮点数。 */
function receivedText(value: unknown): string | null {
  if (value === null || value === undefined || (typeof value === 'number' && !Number.isFinite(value))) return null
  const text = typeof value === 'number' && Object.is(value, -0) ? '-0' : String(value).trim()
  return text === '' ? null : text
}

function unsafeInteger(value: unknown): boolean {
  return typeof value === 'number' && Number.isInteger(value) && !Number.isSafeInteger(value)
}

/** 只分组整数部分，保留小数的全部位数与末尾零；科学计数原样保留，避免展开异常指数。 */
export function formatDecimalText(value: unknown): string {
  const text = receivedText(value)
  if (text === null) return '—'
  const match = /^([+-]?)(?:(\d+)(\.\d*)?|(\.\d+))([eE][+-]?\d+)?$/.exec(text)
  if (!match) return text
  let formatted = text
  if (!match[5]) {
    const integer = match[2] || '0'
    const firstGroup = integer.length % 3 || 3
    const groups = [integer.slice(0, firstGroup)]
    for (let offset = firstGroup; offset < integer.length; offset += 3) groups.push(integer.slice(offset, offset + 3))
    formatted = match[1] + groups.join(',') + (match[3] || match[4] || '')
  }
  // JSON number 在到达组件前可能已经丢失整数位；展示现有值，不伪造“恢复”后的原始精度。
  return unsafeInteger(value) ? '约 ' + formatted : formatted
}

/** 完整原值用于长数字提示；不安全 JSON 整数说明其接收时已有的精度边界。 */
export function decimalValueTitle(value: unknown): string {
  const text = receivedText(value) ?? '—'
  return unsafeInteger(value) ? `接收到的数值：${text}；超出安全整数范围，原始精度需核对。` : text
}
