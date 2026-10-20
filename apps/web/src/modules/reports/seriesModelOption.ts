import { normalizeDashboardTheme } from "../../utils/dashboardTheme";
import { chartPoint } from "../../utils/chartPrecision";
import { emptyChartOption } from "../../utils/aiChartOption";

export interface SeriesModel {
  status: "ready" | "empty" | "needs_mapping" | "invalid_data";
  message?: string;
  categories: string[];
  horizontal?: boolean;
  percentage: boolean;
  series: {
    field: string;
    name: string;
    values: (string | null)[];
    raw_values: (string | null)[];
    axis: "primary" | "secondary";
    type: "bar" | "line";
    stack?: string;
    smooth?: boolean;
    step?: false | "start" | "middle" | "end";
  }[];
}
const variantTypes = new Set([
  "JStackBar",
  "JMultipleBar",
  "JPercentBar",
  "JMixLineBar",
  "JMultipleLine",
  "JSmoothLine",
  "JStepLine",
  "JNegativeBar",
]);
export function usesSeriesModel(widget: Record<string, any>) {
  return variantTypes.has(widget.component) || !!widget.series_model;
}
/** 精提Test5图表的主题/坐标/样式装配；系列与百分比只消费服务端可信模型，不重算业务口径。 */
export function seriesModelOption(
  widget: Record<string, any>,
  source: Record<string, any>,
) {
  const model = widget.series_model as SeriesModel | undefined;
  if (!model || model.status !== "ready") {
    return emptyChartOption(
      { textStyle: source.textStyle },
      model?.message ||
        (widget.binding ? "刷新数据以绘制图表" : "选择数据查询"),
    );
  }
  const theme = normalizeDashboardTheme(widget);
  const palette =
    Array.isArray(source.color) && source.color.length
      ? source.color
      : theme.palette;
  const sourceSeries = Array.isArray(source.series)
    ? source.series
    : source.series
      ? [source.series]
      : [];
  const secondary = model.series.some((series) => series.axis === "secondary");
  const sourceX = Array.isArray(source.xAxis)
    ? source.xAxis[0]
    : (source.xAxis ?? {});
  const sourceY = Array.isArray(source.yAxis)
    ? source.yAxis[0]
    : (source.yAxis ?? {});
  const valueAxis = (index = 0) => ({
    type: "value",
    position: !model.horizontal && index === 1 ? "right" : undefined,
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: {
      color: theme.muted,
      fontSize: 11,
      ...sourceY.axisLabel,
      formatter: model.percentage ? "{value}%" : undefined,
    },
    splitLine: {
      show: index === 0,
      lineStyle: { color: theme.grid, type: "dashed" },
    },
    ...(model.percentage ? { min: 0, max: 100 } : {}),
  });
  const categoryAxis = {
    type: "category",
    data: model.categories,
    boundaryGap: model.series.some((series) => series.type === "bar"),
    axisTick: { show: false },
    axisLine: { lineStyle: { color: theme.grid } },
    axisLabel: {
      color: theme.muted,
      fontSize: 11,
      overflow: "truncate",
      width: model.horizontal ? 100 : undefined,
      ...sourceX.axisLabel,
    },
  };
  return {
    backgroundColor: source.backgroundColor ?? "transparent",
    textStyle: {
      fontFamily: "Microsoft YaHei, Arial, sans-serif",
      color: theme.text,
      ...source.textStyle,
    },
    color: palette,
    animation: false,
    title: {
      ...source.title,
      text: widget.title ?? widget.componentName ?? source.title?.text ?? "",
      textStyle: {
        color: theme.text,
        fontSize: 15,
        ...source.title?.textStyle,
      },
    },
    grid: {
      left: model.horizontal ? 105 : 55,
      right: secondary ? 65 : 30,
      top: 70,
      bottom: 45,
      containLabel: false,
      ...source.grid,
    },
    legend: {
      show: true,
      top: 34,
      textStyle: { color: theme.muted, fontSize: 11 },
      ...source.legend,
      data: model.series.map((series) => series.name),
    },
    tooltip: {
      trigger: "axis",
      backgroundColor: theme.background,
      borderColor: theme.primary,
      textStyle: { color: theme.text },
    },
    xAxis: model.horizontal ? valueAxis() : categoryAxis,
    yAxis: model.horizontal
      ? categoryAxis
      : secondary
        ? [valueAxis(), valueAxis(1)]
        : valueAxis(),
    series: model.series.map((series, index) => {
      const original = sourceSeries[index] ?? sourceSeries[0] ?? {};
      const color = palette[index % palette.length];
      return {
        name: series.name,
        type: series.type,
        stack: series.stack,
        smooth: series.smooth ?? false,
        step: series.step ?? false,
        yAxisIndex: model.horizontal ? 0 : series.axis === "secondary" ? 1 : 0,
        connectNulls: false,
        showSymbol: series.type === "line",
        symbol: original.symbol ?? "circle",
        symbolSize: original.symbolSize ?? 5,
        barMaxWidth: original.barMaxWidth ?? 30,
        barGap: original.barGap,
        barCategoryGap: original.barCategoryGap ?? "35%",
        lineStyle: { color, width: 2, ...original.lineStyle },
        itemStyle: { color, ...original.itemStyle },
        ...(widget.component === "JArea"
          ? { areaStyle: original.areaStyle ?? { opacity: 0.25 } }
          : {}),
        data: series.values.map((value, pointIndex) => ({
          ...chartPoint(value),
          exactValue:
            series.raw_values[pointIndex] === null
              ? null
              : model.percentage && value !== null
                ? `${series.raw_values[pointIndex]}（${value}%）`
                : series.raw_values[pointIndex],
          category: model.categories[pointIndex],
          field: series.field,
        })),
      };
    }),
  };
}
