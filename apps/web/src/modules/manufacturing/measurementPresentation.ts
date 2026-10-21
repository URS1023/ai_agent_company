/** 仅格式化概览，不改变服务端判定或原始证据；字符串数值不经过Number丢精度。 */
export function measurementText(value: unknown, digits = 3, scale = 0): string {
  const source =
    typeof value === "string"
      ? value.trim()
      : typeof value === "number" && Number.isFinite(value)
        ? String(value)
        : "";
  const match = /^([+-]?)(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i.exec(source);
  if (!match || !(match[2] || match[3])) return "—";
  const exponent = Number(match[4] ?? 0) + scale;
  // 异常大的量程保留原文，避免为展示创建无界字符串。
  if (source.length > 1000 || Math.abs(exponent) > 1000) return source;
  const decimals = Math.min(6, Math.max(0, Math.trunc(digits)));
  const coefficient = match[2] + (match[3] ?? "");
  const point = match[2].length + exponent;
  const integer =
    point <= 0 ? "0" : coefficient.slice(0, point).padEnd(point, "0");
  const fraction =
    point <= 0 ? "0".repeat(-point) + coefficient : coefficient.slice(point);
  const kept = fraction.slice(0, decimals).padEnd(decimals, "0");
  let rounded = BigInt(integer + kept);
  if ((fraction[decimals] ?? "0") >= "5") rounded++;
  const negative = match[1] === "-";
  if (rounded === 0n && /[1-9]/.test(coefficient)) {
    const boundary = decimals ? `0.${"0".repeat(decimals - 1)}1` : "1";
    return negative ? `> -${boundary}` : `< ${boundary}`;
  }
  const formatted = rounded.toString().padStart(decimals + 1, "0");
  const tail = decimals ? formatted.slice(-decimals).replace(/0+$/, "") : "";
  const whole = decimals ? formatted.slice(0, -decimals) : formatted;
  return `${negative && rounded !== 0n ? "-" : ""}${whole}${tail ? "." + tail : ""}`;
}

/** 原值只在用户展开明细或查看证据时展示，不补零、不丢小数位。 */
export function originalMeasurement(value: unknown): string | null {
  if (typeof value === "string") return value.trim() ? value : null;
  return typeof value === "number" && Number.isFinite(value)
    ? String(value)
    : null;
}

export function comparisonText(value: unknown): string {
  return value === true ? "可比较" : value === false ? "不可比较" : "—";
}
