import { snapshotCategoryLabel } from "./snapshotWidgetLayout.js";
import { chartPoint, withExactChartTooltip } from "./chartPrecision.js";

/** 已保存快照只展示明确绑定列；模板的样例系列、堆叠与坐标范围不能改写真实数值。 */
export function snapshotChartOption(widget, source, rows) {
  const binding = widget.config?.snapshotBinding || {};
  const field = binding.valueField;
  const labels = rows.map((row, index) =>
    String(
      binding.labelField ? (row[binding.labelField] ?? "未提供") : index + 1,
    ),
  );
  const points = rows.map((row) => chartPoint(row[field]));
  const values = points.map((point) => point.value);
  const rawSeries = Array.isArray(source.series)
    ? source.series[0]
    : source.series;
  const visualSeries = { ...(rawSeries || {}) };
  for (const key of [
    "data",
    "encode",
    "stack",
    "datasetIndex",
    "datasetId",
    "xAxisIndex",
    "yAxisIndex",
    "name",
    "dimensions",
  ])
    delete visualSeries[key];
  const axis = (raw) => {
    const result = { ...(Array.isArray(raw) ? raw[0] : raw) };
    for (const key of [
      "data",
      "min",
      "max",
      "interval",
      "name",
      "scale",
      "inverse",
      "axisLabel",
    ])
      delete result[key];
    return result;
  };
  const bar = /Bar/.test(widget.component || "");
  const horizontal =
    /HorizontalBar/.test(widget.component || "") || widget.type === "hbar";
  const area = /Area/.test(widget.component || "");
  const width = Number(widget.w) || 360,
    height = Number(widget.h) || 220;
  const scale =
    Number(
      widget.snapshotContext?.fontScale ?? widget.snapshotContext?.layoutScale,
    ) || 1;
  const compact = width / scale < 220 || height / scale < 115;
  const fontSize = (compact ? 7 : 10) * scale;
  const left = (horizontal ? (compact ? 39 : 84) : compact ? 29 : 48) * scale;
  // 宽而矮的槽位仍按宽度保留字号，但上下留白最多占高度55%，给真实序列保留绘图区。
  const verticalScale = Math.min(scale, (height * 0.55) / (compact ? 41 : 60));
  const top = (compact ? 24 : 34) * verticalScale,
    bottom = (compact ? 17 : 26) * verticalScale;
  const compactNumber = (value) =>
    Math.abs(Number(value)) >= 10000
      ? `${Number((Number(value) / 10000).toFixed(1))}万`
      : String(value);
  const categoryAxis = {
    ...axis(horizontal ? source.yAxis : source.xAxis),
    type: "category",
    data: labels,
    boundaryGap: bar,
    inverse: horizontal,
    axisLabel: {
      color: "#A8BAD3",
      fontSize,
      hideOverlap: true,
      interval: 0,
      margin: (compact ? 4 : 8) * scale,
      formatter: (value) => snapshotCategoryLabel(value, compact),
      width: horizontal
        ? left - 6 * scale
        : Math.max(
            24 * scale,
            Math.min(
              88 * scale,
              (width - left - 12 * scale) / Math.max(1, labels.length) -
                4 * scale,
            ),
          ),
      overflow: "truncate",
    },
  };
  const valueAxis = {
    ...axis(horizontal ? source.xAxis : source.yAxis),
    type: "value",
    min: values.every((value) => value == null || value >= 0) ? 0 : undefined,
    splitNumber: compact ? 2 : 3,
    axisLabel: {
      color: "#A8BAD3",
      fontSize,
      margin: (compact ? 4 : 8) * scale,
      showMaxLabel: !compact,
      hideOverlap: true,
      formatter: compactNumber,
    },
  };
  const title = widget.metricName || field;
  return withExactChartTooltip({
    ...source,
    animation: false,
    dataset: [],
    legend: { ...(source.legend || {}), show: false, data: [] },
    title: {
      show: true,
      text:
        binding.unit && !title.includes(binding.unit)
          ? `${title}（${binding.unit}）`
          : title,
      top: (compact ? 4 : 7) * verticalScale,
      left: (compact ? 5 : 10) * scale,
      textStyle: {
        color: source.title?.textStyle?.color || "#EAF8FF",
        fontSize: (compact ? 10 : 13) * scale,
        fontWeight: 600,
        width: Math.max(1, width - 16 * scale),
        overflow: "truncate",
      },
    },
    // 源模板既有 115×78 的小图，也有主图；边距与字号按槽位调整，不改变组件几何。
    grid: {
      left,
      right: (compact ? 8 : 14) * scale,
      top,
      bottom,
      containLabel: false,
    },
    tooltip: { trigger: "axis", renderMode: "richText", confine: true },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal
      ? categoryAxis
      : {
          ...valueAxis,
          name: binding.unit || "",
          nameGap: 6 * scale,
          nameTextStyle: { fontSize: 8 * scale },
        },
    series: [
      {
        ...visualSeries,
        type: bar ? "bar" : "line",
        name: widget.metricName || field,
        data: values,
        exactValues: points.map((point) => point.exactValue),
        approximateValues: points.map((point) => point.approximate),
        smooth: false,
        showBackground: false,
        label: { show: false },
        ...(area ? { areaStyle: visualSeries.areaStyle || {} } : {}),
      },
    ],
  });
}
