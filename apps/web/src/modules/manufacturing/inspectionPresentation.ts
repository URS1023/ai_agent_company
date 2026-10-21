/** 检测结果只做展示投影；计数、结论和原因均来自服务端，不重新判定。 */
export interface InspectionDefect {
  defect_type: string;
  severity: string;
  confidence: string;
  bbox?: number[] | null;
}

export interface InspectionObservationData {
  metric_id?: string;
  name?: string;
  category?: string;
  value?: string | number | null;
  unit?: string;
  defects?: InspectionDefect[] | null;
  image_ref?: string;
  note?: string;
  outcome?: string;
  reason?: string;
  has_observation?: boolean;
  provenance?: { type?: string };
}

interface MetricResult {
  metric_id?: string;
  name?: string;
  category?: string;
  outcome?: string;
  reason?: string;
  provenance?: { type?: string };
  observation?: InspectionObservationData | null;
}

export function expectedSamplesError(value: unknown): string {
  return typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 1 &&
    value <= 10000
    ? ""
    : "请输入 1 到 10000 的整数";
}

/** 保留缺失观测、空缺陷数组和原小数字符串之间的区别。 */
export function projectInspectionObservation(
  metric: MetricResult,
): InspectionObservationData {
  return {
    ...metric.observation,
    metric_id: metric.metric_id,
    name: metric.name,
    category: metric.category,
    outcome: metric.outcome,
    reason: metric.reason,
    provenance: metric.provenance,
    has_observation: metric.observation != null,
  };
}

export function observationText(
  observation: InspectionObservationData,
): string {
  if (observation.value != null) return String(observation.value);
  if (Array.isArray(observation.defects)) {
    return observation.defects.length
      ? `${observation.defects.length}处缺陷`
      : "未发现缺陷";
  }
  return observation.has_observation === false ? "未提交结果" : "—";
}

const severityLabels: Record<string, string> = {
  minor: "轻微",
  moderate: "中度",
  severe: "严重",
  review: "待复核",
};
export function defectSeverity(value: string): string {
  return severityLabels[value] ?? (value || "—");
}

const demoImages = new Map([
  ["/demo/quality/normal.svg", "/demo/quality/normal.svg"],
  ["/demo/quality/seam-open.svg", "/demo/quality/seam-open.svg"],
  ["/demo/quality/wrinkle.svg", "/demo/quality/wrinkle.svg"],
  ["/demo/quality/uncertain.svg", "/demo/quality/uncertain.svg"],
  ["/demo/quality/uninspected.svg", "/demo/quality/uninspected.svg"],
]);

/** 仅返回已随平台发布的五张合成演示图常量；业务引用不转成请求地址。 */
export function inspectionDemoImage(reference: unknown): string | null {
  return typeof reference === "string"
    ? (demoImages.get(reference) ?? null)
    : null;
}

/** 未运行时仅计划数可取任务字段；实际数和缺样数必须由已计算结果提供。 */
export function inspectionSampleCounts(result: unknown, planned?: unknown) {
  const record =
    result && typeof result === "object"
      ? (result as Record<string, unknown>)
      : {};
  const count = (value: unknown) =>
    typeof value === "number" && Number.isSafeInteger(value) && value >= 0
      ? value
      : null;
  return {
    expected: count(record.expected_samples) ?? count(planned),
    actual: count(record.actual_samples),
    missing: count(record.missing_samples),
  };
}
