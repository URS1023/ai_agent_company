<script setup>
/**
 * JimuChart —— 大屏/报表的 ECharts 渲染器（含地图、主题、空数据呈现与点击下钻）。
 *
 * 使用场景：JimuWidgetRenderer 的图表类组件，以及属性面板中的图表预览。
 * 依赖接口：`utils/safeContent.safeChartOption`（清洗来自模型/历史的 option，禁止危险字段）、
 * `utils/dashboardTheme`（normalizeDashboardTheme / shouldShowPanelChrome / withAlpha）、
 * `utils/dashboardData`（inferSeriesSchema / normalizeSeriesRows / sanitizeChartOption / toMapSeriesData）、
 * `utils/aiChartOption`（buildAiAuthoritativeChartOption：AI 生成页以模型给的 option 为准；emptyChartOption：空数据兜底）。
 * 关键约束：
 * - 数据来源优先级固定：props.data（外部注入/远程）> config.chartDataParsed > config.chartData（可能是 JSON 字符串），
 *   字符串解析失败时返回空数组而不是抛错；
 * - 空数据必须走 emptyChartOption 呈现"暂无数据"而不是渲染出误导性的坐标轴/空图；
 * - AI 视觉模式（visualMode === 'ai'）下图表以 AI 下发的 option 为权威，仅补主题色与标题，
 *   不得用默认样式覆盖模型给出的设计；
 * - 中国地图 GeoJSON 按需异步加载并缓存（chinaMapPromise 去重），避免并发重复请求；
 * - latestRenderSequence 用于丢弃过期渲染结果：异步取数/加载地图期间可能已切换组件，
 *   旧结果不得覆盖新图；
 * - 组件卸载时销毁 ECharts 实例并断开 ResizeObserver，防止内存泄漏。
 */
import * as echarts from "echarts";
import { safeChartOption } from "@/utils/safeContent";
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from "vue";
import {
  normalizeDashboardTheme,
  shouldShowPanelChrome,
  withAlpha,
} from "@/utils/dashboardTheme";
import {
  inferSeriesSchema,
  normalizeSeriesRows,
  sanitizeChartOption,
  toMapSeriesData,
} from "@/utils/dashboardData";
import {
  buildAiAuthoritativeChartOption,
  emptyChartOption,
} from "@/utils/aiChartOption";
import { snapshotChartOption } from "@/utils/snapshotChartOption";
import { seriesModelOption, usesSeriesModel } from "../seriesModelOption";
import {
  chartPoint,
  chartValueText,
  sumChartDecimals,
  withExactChartTooltip,
  chartBusinessName,
  chartLegendSelection,
} from "@/utils/chartPrecision";

const props = defineProps({
  widget: { type: Object, required: true },
  data: { type: [Array, Object], default: null },
});
const emit = defineEmits(["data-click", "legend-select"]);

const chartEl = ref(null);
// 地图资源或图表配置失败时保留可读状态，避免页面只剩一块空白画布。
const renderError = ref("");
let chart = null;
let renderedOption = null;
let resizeObserver = null;
let chinaMapPromise = null;
let latestRenderSequence = 0;

function clone(value) {
  return JSON.parse(JSON.stringify(value || {}));
}

/**
 * 解析图表数据源：外部 data > config.chartDataParsed > config.chartData。
 * chartData 可能是 JSON 字符串（历史数据/手工填写），解析失败或非数组时返回空数组，不抛异常。
 */
function chartData(widget) {
  if (Array.isArray(props.data)) return props.data;
  const config = widget.config || {};
  if (Array.isArray(config.chartDataParsed)) return config.chartDataParsed;
  if (Array.isArray(config.chartData)) return config.chartData;
  if (typeof config.chartData === "string" && config.chartData.trim()) {
    try {
      const parsed = JSON.parse(config.chartData);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      return [];
    }
  }
  return [];
}

function safeColor(value, fallback = "#5ba8f0") {
  if (!value || value === "#FFFFFF00") return fallback;
  return value;
}

function chartTitle(widget, sourceTitle, theme) {
  const source =
    typeof sourceTitle === "string"
      ? { text: sourceTitle }
      : sourceTitle && typeof sourceTitle === "object"
        ? sourceTitle
        : {};
  const hasTopChrome = shouldShowPanelChrome(widget);
  const customTitle =
    widget.metricName ||
    widget.title ||
    (widget.componentName && widget.componentName !== source.text
      ? widget.componentName
      : "");
  return {
    ...source,
    show: source.show !== false,
    text: customTitle || source.text || widget.componentName || "",
    left: source.left ?? 18,
    top: source.top ?? (hasTopChrome ? 37 : 14),
    textStyle: {
      color: theme.text,
      fontFamily: "Bahnschrift, Microsoft YaHei UI, sans-serif",
      fontSize: widget.chromeMode === "hero" ? 16 : 13,
      fontWeight: 600,
      letterSpacing: 1,
      ...(source.textStyle || {}),
      ...(widget.metricName
        ? {
            width: Math.max(80, widget.w - 40),
            overflow: "truncate",
            fontSize: Math.min(22, source.textStyle?.fontSize || 18),
          }
        : {}),
    },
    subtextStyle: {
      color: theme.muted,
      fontSize: 9,
      lineHeight: 16,
      ...(source.subtextStyle || {}),
    },
  };
}

function polishGrid(grid, widget) {
  if (Array.isArray(grid)) return grid.map((item) => polishGrid(item, widget));
  const source = grid || {};
  if (widget.visualMode === "ai") return { containLabel: true, ...source };
  const hasTopChrome = shouldShowPanelChrome(widget);
  const minimumTop = hasTopChrome ? 82 : 54;
  const configuredTop = Number(source.top);
  return {
    ...source,
    top: Number.isFinite(configuredTop)
      ? Math.max(minimumTop, configuredTop)
      : minimumTop,
    right: source.right ?? 22,
    bottom: source.bottom ?? 18,
    left: source.left ?? 18,
    containLabel: true,
  };
}

function polishLegend(legend, theme, widget = {}) {
  if (Array.isArray(legend))
    return legend.map((item) => polishLegend(item, theme, widget));
  const hasTopChrome = shouldShowPanelChrome(widget);
  return {
    ...(legend || {}),
    top:
      legend?.bottom != null
        ? undefined
        : (legend?.top ?? (widget.metricName ? 52 : hasTopChrome ? 43 : 17)),
    type: "scroll",
    right: legend?.right ?? 16,
    itemWidth: legend?.itemWidth ?? 13,
    itemHeight: legend?.itemHeight ?? 6,
    icon: legend?.icon || "roundRect",
    textStyle: {
      color: theme.muted,
      fontSize: 9,
      ...(legend?.textStyle || {}),
    },
  };
}

function polishAxis(axis, theme) {
  if (Array.isArray(axis)) return axis.map((item) => polishAxis(item, theme));
  if (!axis || typeof axis !== "object") return axis;
  return {
    ...axis,
    axisTick: { show: false, ...(axis.axisTick || {}) },
    axisLine: {
      show: false,
      lineStyle: { color: theme.grid, ...(axis.axisLine?.lineStyle || {}) },
      ...(axis.axisLine || {}),
    },
    axisLabel: {
      color: theme.muted,
      fontSize: 10,
      margin: 12,
      hideOverlap: true,
      ...(axis.axisLabel || {}),
    },
    splitLine: {
      show: true,
      lineStyle: {
        color: theme.grid,
        opacity: 0.18,
        type: "dashed",
        ...(axis.splitLine?.lineStyle || {}),
      },
      ...(axis.splitLine || {}),
    },
  };
}

function polishSeries(series, index, theme, palette) {
  const current = clone(series);
  const signal = palette[index % palette.length];
  if (current.type === "line") {
    current.smooth ??= true;
    current.showSymbol ??= false;
    current.symbolSize ??= 7;
    current.lineStyle = {
      color: signal,
      width: 2.4,
      shadowBlur: 9,
      shadowColor: `${signal}66`,
      ...(current.lineStyle || {}),
    };
    current.itemStyle = {
      color: theme.text,
      borderColor: signal,
      borderWidth: 2,
      ...(current.itemStyle || {}),
    };
    if (current.areaStyle) {
      current.areaStyle = {
        opacity: 0.42,
        color:
          current.areaStyle.color ||
          new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: `${signal}aa` },
            { offset: 0.72, color: `${signal}20` },
            { offset: 1, color: `${signal}00` },
          ]),
        ...current.areaStyle,
      };
    }
    current.emphasis = { focus: "series", ...(current.emphasis || {}) };
  } else if (current.type === "bar") {
    const explicitColor = current.itemStyle?.color;
    const itemColors = Array.isArray(current.itemColors)
      ? current.itemColors
      : null;
    delete current.itemColors;
    current.barMaxWidth ??= 34;
    current.itemStyle = {
      borderRadius: current.itemStyle?.borderRadius || [4, 4, 0, 0],
      shadowBlur: 8,
      shadowColor: `${signal}42`,
      ...(current.itemStyle || {}),
      color:
        explicitColor ||
        (itemColors
          ? (params) => itemColors[params.dataIndex % itemColors.length]
          : new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: signal },
              { offset: 0.55, color: `${signal}cc` },
              { offset: 1, color: `${theme.secondary}66` },
            ])),
    };
    current.showBackground ??= true;
    current.backgroundStyle = {
      color: `${theme.grid}22`,
      borderRadius: 4,
      ...(current.backgroundStyle || {}),
    };
  } else if (current.type === "pie") {
    current.padAngle ??= 2;
    current.itemStyle = {
      borderColor: theme.background,
      borderWidth: 2,
      ...(current.itemStyle || {}),
    };
    current.emphasis = {
      scaleSize: 7,
      itemStyle: { shadowBlur: 18, shadowColor: `${signal}77` },
      ...(current.emphasis || {}),
    };
  } else if (current.type === "radar") {
    current.symbolSize ??= 5;
    current.lineStyle = {
      width: 2,
      shadowBlur: 8,
      shadowColor: `${signal}66`,
      ...(current.lineStyle || {}),
    };
    current.areaStyle = { opacity: 0.24, ...(current.areaStyle || {}) };
  }
  return current;
}

/**
 * 生成 ECharts 基础 option：AI 视觉模式以模型下发的 option 为权威（只补主题色与标题）；
 * 普通模式按 series 数据推导，无有效数据时直接返回 emptyChartOption（空数据必须显式呈现）。
 */
function baseOption(widget) {
  const config = widget.config || {};
  const source = sanitizeChartOption(clone(config.option || {}));
  if (usesSeriesModel(widget)) return seriesModelOption(widget,source);
  const data = chartData(widget);
  if (config.snapshotBinding) {
    return snapshotChartOption(widget, source, data);
  }
  // 参考组件已经按原图参数与当前数据构建 option；通用美化会改动柱角、环间距等细节。
  if (widget.referenceSkin?.version === 1 && widget.visualMode === "ai")
    return source;
  if (widget.visualMode === "ai") {
    const option = buildAiAuthoritativeChartOption({ widget, source, data });
    const theme = normalizeDashboardTheme(widget);
    if (!Array.isArray(option.color) || !option.color.length)
      option.color = theme.palette;
    if (option.title != null) {
      option.title = chartTitle(widget, option.title, theme);
    }
    return option;
  }
  const type = widget.component || "";
  const parsedRows = normalizeSeriesRows(
    data,
    config.snapshotBinding || config.dataMapping,
  );
  if (!parsedRows.length) return emptyChartOption(source);
  const normalizedRows = parsedRows;
  const inferredSchema = inferSeriesSchema(
    data,
    config.snapshotBinding || config.dataMapping,
  );
  inferredSchema.series.forEach((series) => {
    series.name = config.columnLabels?.[series.name] || series.name;
  });
  const returnedFields = new Set(Object.keys(data[0] || {}));
  const datasetFieldsMatch =
    (source.dataset?.dimensions || []).every((field) =>
      returnedFields.has(typeof field === "string" ? field : field?.name),
    ) &&
    (Array.isArray(source.series)
      ? source.series
      : source.series
        ? [source.series]
        : []
    ).every((series) =>
      Object.values(series.encode || {})
        .flat()
        .every(
          (field) => typeof field !== "string" || returnedFields.has(field),
        ),
    );
  const labels = normalizedRows.map((item) => item.label);
  const values = normalizedRows.map((item) => item.value);
  const firstPoint = normalizedRows[0] || chartPoint(null);
  const resolvedFirstValue = firstPoint.value;
  const theme = normalizeDashboardTheme(widget);
  const palette = source.color || theme.palette;
  const usesSpecialRenderer =
    /Gauge|Radar|Funnel|Pyramid|Progress|Liquid|Rectangle|WordCloud|Ring|Pie|Rose|Doughnut|Radial|Scatter|Quadrant|Bubble/i.test(
      type,
    );

  const common = {
    animationDuration: 1100,
    animationDurationUpdate: 720,
    animationEasing: "cubicOut",
    animationEasingUpdate: "cubicInOut",
    backgroundColor: source.backgroundColor ?? "transparent",
    textStyle: {
      color: theme.text,
      fontFamily: "Microsoft YaHei, Arial, sans-serif",
    },
    title: chartTitle(widget, source.title, theme),
    legend: polishLegend(source.legend, theme, widget),
    tooltip: {
      trigger: type.includes("Ring") || type.includes("Pie") ? "item" : "axis",
      backgroundColor: `${theme.background}ee`,
      borderColor: `${theme.primary}55`,
      borderWidth: 1,
      extraCssText:
        "box-shadow:0 18px 48px rgba(0,0,0,.46);border-radius:12px;backdrop-filter:blur(12px);",
      textStyle: { color: theme.text, fontSize: 11 },
    },
  };

  // AI 报表采用 dataset 与 encode 描述多系列，保留原契约，不把每行压缩成 name/value。
  if (
    source.dataset &&
    datasetFieldsMatch &&
    Array.isArray(source.series) &&
    source.series.length &&
    Array.isArray(data) &&
    data.length &&
    !usesSpecialRenderer
  ) {
    return {
      ...common,
      ...source,
      color: palette,
      dataset: { ...source.dataset, source: data },
      animationDuration: source.animationDuration ?? 1100,
      animationEasing: source.animationEasing || "cubicOut",
      backgroundColor: source.backgroundColor ?? "transparent",
      title: chartTitle(widget, source.title, theme),
      legend: polishLegend(source.legend, theme, widget),
      textStyle: { ...common.textStyle, ...(source.textStyle || {}) },
      tooltip: { ...common.tooltip, ...(source.tooltip || {}) },
      grid: polishGrid(source.grid, widget),
      xAxis: polishAxis(source.xAxis, theme),
      yAxis: polishAxis(source.yAxis, theme),
      series: source.series.map((series, index) =>
        polishSeries(
          /Area/i.test(type) && series.type === "line" && !series.areaStyle
            ? {
                ...series,
                name: config.columnLabels?.[series.name] || series.name,
                areaStyle: {},
              }
            : {
                ...series,
                name: config.columnLabels?.[series.name] || series.name,
              },
          index,
          theme,
          palette,
        ),
      ),
    };
  }

  if (/Gauge/i.test(type)) {
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      series: [
        {
          type: "gauge",
          startAngle: type.includes("Semi") ? 180 : 220,
          endAngle: type.includes("Semi") ? 0 : -40,
          center: ["50%", type.includes("Semi") ? "68%" : "56%"],
          radius: type.includes("Semi") ? "78%" : "72%",
          progress: { show: true, width: 12, itemStyle: { color: palette[0] } },
          axisLine: { lineStyle: { width: 12, color: [[1, "#19324d"]] } },
          axisTick: { show: false },
          splitLine: { length: 7, lineStyle: { color: "#5b7892", width: 1 } },
          axisLabel: { color: "#8ea7bc", distance: 18, fontSize: 9 },
          pointer: {
            show: !type.includes("Antv"),
            width: 3,
            itemStyle: { color: "#f7c948" },
          },
          anchor: { show: true, size: 7, itemStyle: { color: "#f7c948" } },
          detail: {
            valueAnimation: false,
            formatter: () => chartValueText(firstPoint),
            color: "#d8ecff",
            fontSize: 21,
            offsetCenter: [0, "42%"],
          },
          data: [{ ...firstPoint, name: labels[0] || "" }],
        },
      ],
    };
  }

  if (/Radar/i.test(type)) {
    const radarRows = normalizedRows.slice(0, 8);
    const radarLabels = radarRows.map((item) => item.label);
    const radarValues = radarRows.map((item) => item.value);
    const validValues = radarValues.filter((value) => value != null);
    const radarMax = Math.max(...validValues.map(Math.abs), 1);
    const total = sumChartDecimals(radarRows.map((item) => item.exactValue));
    const average =
      total.value == null || !validValues.length
        ? null
        : total.value / validValues.length;
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      legend: {
        top: 34,
        right: 14,
        itemWidth: 10,
        itemHeight: 6,
        textStyle: { color: theme.muted, fontSize: 9 },
        data: ["实际分布", "均值基线（近似）"],
      },
      radar: {
        ...(source.radar || {}),
        center: source.radar?.center || ["50%", "60%"],
        radius: source.radar?.radius || "57%",
        shape:
          source.radar?.shape ||
          (type.includes("Circle") ? "circle" : "polygon"),
        indicator: radarValues.map((value, index) => ({
          name: radarLabels[index] || `${index + 1}`,
          min: Math.min(0, ...validValues) < 0 ? -radarMax * 1.2 : 0,
          max: Math.max(radarMax * 1.2, value * 1.35, 1),
        })),
        axisName: {
          color: theme.muted,
          fontSize: 9,
          ...(source.radar?.axisName || {}),
        },
        splitLine: {
          lineStyle: {
            color: `${theme.grid}aa`,
            ...(source.radar?.splitLine?.lineStyle || {}),
          },
        },
        splitArea: {
          areaStyle: {
            color: [
              withAlpha(theme.panelStrong, "88"),
              withAlpha(theme.background, "33"),
            ],
          },
        },
        axisLine: { lineStyle: { color: `${theme.primary}55` } },
      },
      series: [
        {
          ...(source.series?.[0] || {}),
          type: "radar",
          symbolSize: 5,
          data: [
            {
              name: "实际分布",
              value: radarValues,
              exactValue: radarRows.map((item) => item.exactValue),
              exactLabels: radarLabels,
              exactApproximate: radarRows.map((item) => item.approximate),
              lineStyle: {
                color: palette[0],
                width: 2.4,
                shadowBlur: 9,
                shadowColor: `${palette[0]}77`,
              },
              itemStyle: {
                color: theme.text,
                borderColor: palette[0],
                borderWidth: 2,
              },
              areaStyle: { color: palette[0], opacity: 0.3 },
            },
            {
              name: "均值基线（近似）",
              value: radarValues.map(() => average),
              exactValue: radarValues.map(() =>
                average == null ? null : String(average),
              ),
              exactLabels: radarLabels,
              approximate: true,
              symbol: "none",
              lineStyle: {
                color: theme.accent,
                width: 1,
                type: "dashed",
                opacity: 0.78,
              },
              areaStyle: { color: theme.accent, opacity: 0.06 },
            },
          ],
        },
      ],
    };
  }

  if (/Funnel|Pyramid/i.test(type)) {
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      series: [
        {
          type: "funnel",
          top: 46,
          bottom: 12,
          left: "16%",
          width: "68%",
          sort: type.includes("Pyramid") ? "ascending" : "descending",
          gap: 2,
          label: { color: "#d8e8f4", fontSize: 9 },
          labelLine: { length: 8, lineStyle: { color: "#5d7d96" } },
          itemStyle: { borderColor: "#091b2b", borderWidth: 1 },
          data: normalizedRows.map((item, index) => ({
            ...item,
            name: labels[index],
          })),
        },
      ],
    };
  }

  if (/Scatter|Quadrant|Bubble/i.test(type)) {
    const points = normalizedRows.map((item, index) => ({
      ...item,
      name: labels[index],
      value: [
        index * 18 + 14,
        item.value,
        type.includes("Bubble") && item.value != null
          ? Math.max(0, item.value / 2 + 8)
          : 10,
      ],
    }));
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      grid: polishGrid(source.grid, widget),
      xAxis: polishAxis({ min: 0, max: 100 }, theme),
      yAxis: polishAxis({ min: 0, max: 100 }, theme),
      series: [
        {
          type: "scatter",
          data: points,
          symbolSize: (point) => point[2],
          itemStyle: {
            color: palette[0],
            opacity: 0.78,
            shadowBlur: 8,
            shadowColor: palette[0],
          },
        },
      ],
    };
  }

  if (/Progress|Liquid/i.test(type)) {
    const progress =
      resolvedFirstValue == null
        ? null
        : Math.max(0, Math.min(100, resolvedFirstValue));
    if (/Round|Ring|Liquid/i.test(type)) {
      return {
        ...common,
        color: palette,
        title: chartTitle(widget, source.title, theme),
        series: [
          {
            type: "pie",
            radius: ["54%", "70%"],
            center: ["50%", "58%"],
            silent: true,
            label: {
              show: true,
              position: "center",
              formatter: () =>
                chartValueText(firstPoint) + (progress == null ? "" : "%"),
              color: "#d8ecff",
              fontSize: 23,
            },
            data: [
              {
                ...firstPoint,
                value: progress,
                itemStyle: {
                  color: palette[0],
                  shadowBlur: 8,
                  shadowColor: palette[0],
                },
              },
              {
                ...chartPoint(progress == null ? null : 100 - progress),
                name: "剩余（近似）",
                itemStyle: { color: "#17324b" },
              },
            ],
          },
        ],
      };
    }
    return {
      ...common,
      title: chartTitle(widget, source.title, theme),
      grid: polishGrid({ top: 64, left: 24, right: 36, bottom: 26 }, widget),
      xAxis: { type: "value", max: 100, show: false },
      yAxis: {
        type: "category",
        data: [labels[0] || ""],
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: "#a9bfd1" },
      },
      series: [
        {
          type: "bar",
          data: [{ ...firstPoint, value: progress }],
          barWidth: 14,
          showBackground: true,
          backgroundStyle: { color: "#17324b", borderRadius: 8 },
          itemStyle: { color: palette[0], borderRadius: 8 },
          label: {
            show: true,
            position: "right",
            color: "#d8ecff",
            formatter: () =>
              chartValueText(firstPoint) + (progress == null ? "" : "%"),
          },
        },
      ],
    };
  }

  if (/Rectangle/i.test(type)) {
    return {
      ...common,
      title: chartTitle(widget, source.title, theme),
      series: [
        {
          type: "treemap",
          top: widget.chromeMode === "hero" ? 74 : 48,
          left: 12,
          right: 12,
          bottom: 12,
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          label: { color: "#dcecff", fontSize: 10 },
          itemStyle: { borderColor: "#091b2b", gapWidth: 2 },
          data: normalizedRows.map((item, index) => ({
            ...item,
            name: labels[index],
            itemStyle: { color: palette[index % palette.length] },
          })),
        },
      ],
    };
  }

  if (/WordCloud/i.test(type)) {
    return {
      ...common,
      title: chartTitle(widget, source.title, theme),
      graphic: labels.map((label, index) => ({
        type: "text",
        left: `${12 + ((index * 19) % 70)}%`,
        top: `${28 + ((index * 23) % 54)}%`,
        rotation: index % 3 === 0 ? 0.18 : 0,
        style: {
          text: label,
          fill: palette[index % palette.length],
          fontSize: 15 + values[index] / 7,
          fontWeight: 600,
        },
      })),
    };
  }

  if (/Ring|Pie|Rose|Doughnut|Radial/i.test(type)) {
    const seriesSource = clone(source.series?.[0] || {});
    const totalPoint = sumChartDecimals(
      normalizedRows.map((item) => item.exactValue),
    );
    totalPoint.approximate ||= normalizedRows.some((item) => item.approximate);
    const total = totalPoint.value;
    const singleCategory = data.length === 1;
    const showCenterSummary = singleCategory || !/Pie|Rose/i.test(type);
    // 小组件上方居中显示环图、下方显示图例；大组件使用左图右图例。
    const isSmall = props.widget.w < 470;
    const ringCenter = isSmall ? ["50%", "48%"] : ["38%", "55%"];
    const graphicLeft = isSmall ? "50%" : "38%";
    const graphicTop = isSmall ? "60%" : "56%";
    return {
      ...common,
      color: palette,
      title: chartTitle(widget, source.title, theme),
      legend: isSmall
        ? polishLegend(
            {
              ...source.legend,
              orient: "horizontal",
              left: 16,
              right: 16,
              top: "auto",
              bottom: 6,
            },
            theme,
            widget,
          )
        : polishLegend(
            {
              ...source.legend,
              orient: "vertical",
              right: "3%",
              top: "middle",
            },
            theme,
            widget,
          ),
      graphic: showCenterSummary
        ? [
            {
              type: "group",
              left: graphicLeft,
              top: graphicTop,
              children: [
                {
                  type: "text",
                  style: {
                    text: chartValueText(totalPoint),
                    width: Math.max(80, widget.w * 0.4),
                    overflow: "truncate",
                    ellipsis: "…",
                    fill: theme.text,
                    font: `600 ${total >= 1e6 ? 17 : total >= 1e4 ? 19 : 22}px Bahnschrift, "Microsoft YaHei UI"`,
                    textAlign: "center",
                  },
                },
                {
                  type: "text",
                  top: 27,
                  style: {
                    text: "TOTAL",
                    fill: theme.muted,
                    font: "9px Consolas",
                    textAlign: "center",
                  },
                },
              ],
            },
          ]
        : undefined,
      series: [
        {
          ...seriesSource,
          type: "pie",
          radius: singleCategory
            ? ["48%", "70%"]
            : seriesSource.radius ||
              (type.includes("Pie") || type.includes("Rose")
                ? ["12%", "68%"]
                : ["43%", "70%"]),
          center: seriesSource.center || ringCenter,
          label: { show: false },
          labelLine: { show: false },
          roseType: type.includes("Rose") ? "radius" : undefined,
          emphasis: {
            scaleSize: 7,
            itemStyle: { shadowBlur: 16, shadowColor: `${theme.primary}77` },
          },
          itemStyle: { borderColor: theme.background, borderWidth: 2 },
          data: normalizedRows.map((item, index) => ({
            ...item,
            name: labels[index],
          })),
        },
      ],
    };
  }

  const isBar = /Bar|Histogram|Column|Capsule|Pictorial/i.test(type);
  const isHorizontal = /Horizontal|Dynamic|Negative|Percent|Capsule/i.test(
    type,
  );
  const isArea = /Area/i.test(type);
  const primary = safeColor(palette[0], isBar ? "#f7c948" : "#36d7b7");
  const seriesSource = clone(source.series?.[0] || {});
  // 横向条形图按实际分类名计算纵轴标签宽度，长名称截断，防止溢出组件。
  const maxLabelLen = isHorizontal
    ? Math.max(...labels.map((l) => String(l || "").length), 0)
    : 0;
  // 按每个汉字约 11px 估算，标签上限为 100px，为条形保留空间。
  const horizontalLabelWidth = isHorizontal
    ? Math.min(maxLabelLen * 11, 100)
    : 0;
  const sourceGrid = source.grid || {};
  const gridLeft = isHorizontal
    ? (sourceGrid.left ?? horizontalLabelWidth + 8)
    : (sourceGrid.left ?? 18);
  const grid = polishGrid({ ...sourceGrid, left: gridLeft }, widget);
  const axisLine = { show: false, lineStyle: { color: theme.grid } };
  const axisLabel = isHorizontal
    ? {
        color: theme.muted,
        fontSize: 10,
        margin: 8,
        hideOverlap: false,
        overflow: "truncate",
        width: Math.max(horizontalLabelWidth, 60),
        ellipsis: "…",
      }
    : { color: theme.muted, fontSize: 10, margin: 12, hideOverlap: true };
  const barData = values;
  const barGradient = new echarts.graphic.LinearGradient(
    0,
    0,
    isHorizontal ? 1 : 0,
    isHorizontal ? 0 : 1,
    [
      { offset: 0, color: isHorizontal ? `${primary}99` : primary },
      { offset: 0.58, color: isHorizontal ? primary : `${primary}cc` },
      {
        offset: 1,
        color: isHorizontal ? theme.secondary : `${theme.secondary}66`,
      },
    ],
  );

  let chartSeries = [
    {
      ...seriesSource,
      type: isBar ? "bar" : "line",
      data: barData,
      exactValues: normalizedRows.map((item) => item.exactValue),
      approximateValues: normalizedRows.map((item) => item.approximate),
      smooth: !isBar,
      symbol: isBar ? "none" : "circle",
      showSymbol: false,
      symbolSize: 7,
      barMaxWidth: isBar ? (isHorizontal ? 17 : 32) : undefined,
      barCategoryGap: isHorizontal ? "44%" : "36%",
      showBackground: isBar,
      backgroundStyle: isBar
        ? { color: `${theme.grid}25`, borderRadius: 8 }
        : undefined,
      lineStyle: isBar
        ? undefined
        : {
            color: primary,
            width: widget.chromeMode === "hero" ? 3 : 2.2,
            shadowBlur: 10,
            shadowColor: `${primary}55`,
          },
      itemStyle: isBar
        ? {
            color: barGradient,
            borderRadius: /Capsule|Horizontal/i.test(type)
              ? 10
              : isHorizontal
                ? [0, 5, 5, 0]
                : [4, 4, 0, 0],
            shadowBlur: 8,
            shadowColor: `${primary}28`,
          }
        : { color: theme.text, borderColor: primary, borderWidth: 2 },
      areaStyle: isArea
        ? {
            opacity: 0.72,
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: `${primary}99` },
              { offset: 0.58, color: `${primary}24` },
              { offset: 1, color: `${primary}00` },
            ]),
          }
        : undefined,
    },
  ];

  if (inferredSchema.series.length > 1) {
    chartSeries = inferredSchema.series.map((measure, index) => {
      const signal = safeColor(palette[index % palette.length], primary);
      const secondarySeries = isBar && index > 0;
      return {
        type: secondarySeries ? "line" : isBar ? "bar" : "line",
        name: measure.name,
        data: measure.data,
        exactValues: measure.exactValues,
        approximateValues: measure.approximateValues,
        yAxisIndex:
          !isHorizontal && inferredSchema.series.length > 1
            ? Math.min(index, 1)
            : 0,
        smooth: !isBar || secondarySeries,
        showSymbol: secondarySeries,
        symbolSize: secondarySeries ? 5 : 7,
        barMaxWidth:
          isBar && !secondarySeries ? (isHorizontal ? 17 : 28) : undefined,
        showBackground: isBar && !secondarySeries,
        backgroundStyle:
          isBar && !secondarySeries
            ? { color: `${theme.grid}25`, borderRadius: 8 }
            : undefined,
        lineStyle:
          secondarySeries || !isBar
            ? {
                color: signal,
                width: 2.2,
                shadowBlur: 8,
                shadowColor: `${signal}55`,
              }
            : undefined,
        itemStyle:
          isBar && !secondarySeries
            ? {
                color: new echarts.graphic.LinearGradient(
                  0,
                  0,
                  isHorizontal ? 1 : 0,
                  isHorizontal ? 0 : 1,
                  [
                    { offset: 0, color: `${signal}99` },
                    { offset: 0.6, color: signal },
                    { offset: 1, color: `${theme.secondary}77` },
                  ],
                ),
                borderRadius: isHorizontal ? [0, 6, 6, 0] : [4, 4, 0, 0],
              }
            : {
                color: signal,
                borderColor: theme.text,
                borderWidth: secondarySeries ? 1 : 0,
              },
        areaStyle:
          isArea && index === 0
            ? {
                color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
                  { offset: 0, color: `${signal}88` },
                  { offset: 1, color: `${signal}00` },
                ]),
              }
            : undefined,
      };
    });
  }

  if (/StackBar|MultipleBar|PercentBar|MixLineBar/i.test(type)) {
    const stacked = /StackBar|PercentBar/i.test(type);
    // 模板类型只选择样式，不按固定比例制造查询中不存在的业务系列。
    chartSeries = chartSeries.map((series, index) => ({
      ...series,
      type: type.includes("MixLineBar") && index > 0 ? "line" : "bar",
      stack: stacked ? "total" : undefined,
    }));
  }

  return {
    ...common,
    color: palette,
    title: chartTitle(widget, source.title, theme),
    grid,
    xAxis: polishAxis(
      {
        type: isHorizontal ? "value" : "category",
        data: isHorizontal ? undefined : labels,
        boundaryGap: isBar,
        axisTick: { show: false },
        axisLine,
        axisLabel,
        ...(source.xAxis || {}),
      },
      theme,
    ),
    yAxis:
      !isHorizontal && inferredSchema.series.length > 1
        ? inferredSchema.series.slice(0, 2).map((measure, index) =>
            polishAxis(
              {
                type: "value",
                name: measure.name,
                position: index === 0 ? "left" : "right",
                nameTextStyle: {
                  color: palette[index % palette.length],
                  fontSize: 9,
                },
                axisTick: { show: false },
                axisLine,
                axisLabel: {
                  ...axisLabel,
                  color: palette[index % palette.length],
                },
                splitLine: {
                  show: index === 0,
                  lineStyle: {
                    color: theme.grid,
                    opacity: 0.16,
                    type: "dashed",
                  },
                },
              },
              theme,
            ),
          )
        : polishAxis(
            {
              type: isHorizontal ? "category" : "value",
              data: isHorizontal ? labels : undefined,
              axisTick: { show: false },
              axisLine,
              axisLabel,
              splitLine: {
                show: !isHorizontal,
                lineStyle: { color: theme.grid, opacity: 0.16, type: "dashed" },
              },
              ...(source.yAxis || {}),
            },
            theme,
          ),
    series: chartSeries,
  };
}

/**
 * 确保中国地图 GeoJSON 已注册：先查 ECharts 地图缓存，再用 chinaMapPromise 去重并发请求，
 * 避免多个地图组件同时挂载时重复拉取 china.json。
 */
async function ensureChinaMap() {
  if (echarts.getMap("china")) return;
  if (!chinaMapPromise) {
    chinaMapPromise = fetch(`${import.meta.env.BASE_URL}jimu-screen/china.json`)
      .then((response) => {
        if (!response.ok)
          throw new Error(`China map request failed: ${response.status}`);
        return response.json();
      })
      .then((geoJson) => echarts.registerMap("china", geoJson))
      .catch((error) => {
        // rejected Promise 不能复用，否则一次 503 会让后续重试永久失败。
        chinaMapPromise = null;
        throw error;
      });
  }
  await chinaMapPromise;
}

/** 地图 option：AI 视觉模式下交给 buildAiAuthoritativeChartOption，否则按数据最大值生成 visualMap 分级配色。 */
async function mapOption(widget) {
  await ensureChinaMap();
  const source = sanitizeChartOption(clone(widget.config?.option || {}));
  const data = toMapSeriesData(chartData(widget));
  if (widget.visualMode === "ai") {
    return buildAiAuthoritativeChartOption({
      widget,
      source,
      data: chartData(widget),
      map: true,
    });
  }
  const max = Math.max(...data.map((item) => Number(item.value || 0)), 1);
  const theme = normalizeDashboardTheme(widget);
  const hasTopChrome = shouldShowPanelChrome(widget);
  const hero =
    widget.chromeMode === "hero" ||
    widget.visualRole === "primary-insight" ||
    widget.visualRole === "hero-map";
  return {
    animationDuration: 1300,
    animationEasing: "cubicOut",
    backgroundColor: source.backgroundColor ?? "transparent",
    title: chartTitle(widget, source.title, theme),
    tooltip: {
      trigger: "item",
      backgroundColor: `${theme.background}ee`,
      borderColor: theme.primary,
      textStyle: { color: theme.text },
    },
    visualMap: {
      ...(source.visualMap || {}),
      min: 0,
      max,
      left: "5%",
      bottom: "3%",
      itemWidth: 10,
      itemHeight: 95,
      calculable: true,
      precision: 0,
      textStyle: { color: theme.muted, fontSize: 10 },
      inRange: {
        color: [
          theme.backgroundRaised || theme.background,
          theme.surfaceLift || theme.panelStrong,
          theme.secondary,
          theme.primary,
          theme.accent,
        ],
      },
    },
    series: [
      {
        ...(source.series?.[0] || {}),
        type: "map",
        map: "china",
        roam: source.geo?.roam ?? hero,
        zoom: source.geo?.zoom ?? (hero ? 1.14 : 1.04),
        top: hasTopChrome ? 62 : 28,
        right: hero ? "8%" : "5%",
        bottom: 14,
        left: hero ? "8%" : "5%",
        selectedMode: "single",
        label: {
          show: hero && widget.w >= 680,
          color: withAlpha(theme.text, 0.78),
          fontSize: 9,
          textBorderColor: withAlpha(theme.background, 0.84),
          textBorderWidth: 2,
        },
        itemStyle: {
          areaColor: theme.surfaceLift || theme.panelStrong,
          borderColor: withAlpha(theme.primary, 0.78),
          borderWidth: 1.15,
          shadowBlur: hero ? 18 : 9,
          shadowColor: withAlpha(theme.primary, hero ? 0.46 : 0.28),
        },
        emphasis: {
          label: { show: true, color: "#fff", fontWeight: 700 },
          itemStyle: {
            areaColor: theme.accent,
            borderColor: theme.text,
            borderWidth: 1.5,
            shadowBlur: 24,
            shadowColor: withAlpha(theme.accent, 0.72),
          },
        },
        select: {
          label: { show: true, color: theme.text, fontWeight: 700 },
          itemStyle: { areaColor: theme.secondary, borderColor: theme.primary },
        },
        data,
      },
    ],
  };
}

/**
 * 渲染入口：地图及已保存快照用 SVG，快照小画布在聊天中放大仍保持文字清晰。
 * 用 latestRenderSequence 丢弃过期结果——地图 GeoJSON 是异步加载的，期间用户可能已切换组件，
 * 旧 option 不得覆盖新图。最终 option 仍要过一遍 safeChartOption 才交给 ECharts。
 */
async function render() {
  const renderSequence = ++latestRenderSequence;
  try {
    if (!chartEl.value) return;
    await nextTick();
    if (!chart) {
      const renderer =
        props.widget.config?.snapshotBinding ||
        /Map|Earth/i.test(props.widget.component)
          ? "svg"
          : "canvas";
      chart = echarts.init(chartEl.value, null, { renderer });
      chart.on("click", handleChartDataClick);
      chart.on("legendselectchanged", (event) =>
        emit("legend-select", chartLegendSelection(renderedOption, event)),
      );
      chart.getZr().on("click", handleChartCanvasClick);
    }
    const option = /Map|Earth/i.test(props.widget.component)
      ? await mapOption(props.widget)
      : baseOption(props.widget);
    if (renderSequence !== latestRenderSequence) return;
    option.tooltip = {
      ...(option.tooltip || {}),
      ...(props.widget.config?.option?.tooltip || {}),
    };
    renderedOption = safeChartOption(withExactChartTooltip(option));
    if (matchMedia("(prefers-reduced-motion: reduce)").matches)
      renderedOption.animation = false;
    chart.setOption(renderedOption, true);
    chart.resize();
    renderError.value = "";
  } catch (error) {
    if (renderSequence !== latestRenderSequence) return;
    const detail = error instanceof Error ? error.message : String(error || "");
    renderError.value = /Map|Earth/i.test(props.widget.component)
      ? `地图资源加载失败${detail ? `：${detail}` : ""}`
      : `图表渲染失败${detail ? `：${detail}` : ""}`;
  }
}

/** 清除失败的地图缓存并重新渲染，供用户在网络恢复后继续当前页面。 */
function retryRender() {
  if (/Map|Earth/i.test(props.widget.component)) chinaMapPromise = null;
  renderError.value = "";
  void render();
}

/** 数据点点击：把原始行数据（rows[dataIndex]）一并抛出，供下钻面板展示明细。 */
function handleChartDataClick(params = {}) {
  const rows = chartData(props.widget);
  emit("data-click", {
    name: chartBusinessName(renderedOption, params.name),
    value: params.value,
    dataIndex: params.dataIndex,
    seriesName: chartBusinessName(renderedOption, params.seriesName),
    data: rows[params.dataIndex] ?? params.data,
  });
}

/**
 * 画布空白处点击：命中图形元素时直接返回 —— 此时语义事件（handleChartDataClick）已负责该次点击，
 * 再发一个空 payload 会把用户刚点中的维度信息抹掉。
 */
function handleChartCanvasClick(event = {}) {
  // ECharts 数据事件与 zrender 事件可能同时触发；已有图形目标时交给数据事件处理，避免空载荷覆盖所点维度。
  if (event?.target) return;
  emit("data-click", {});
}

onMounted(() => {
  void render();
  resizeObserver = new ResizeObserver(() => chart?.resize());
  resizeObserver.observe(chartEl.value);
});

watch(
  [() => props.widget, () => props.data],
  () => {
    void render();
  },
  { deep: true },
);

onBeforeUnmount(() => {
  latestRenderSequence += 1;
  resizeObserver?.disconnect();
  chart?.off("click", handleChartDataClick);
  chart?.getZr().off("click", handleChartCanvasClick);
  chart?.dispose();
});

// 预览验收读取实际渲染后的系列，不能只以画布非空认定数值正确。
defineExpose({ getRenderedOption: () => chart?.getOption() });
</script>

<template>
  <div class="jimu-chart-shell">
    <div ref="chartEl" class="jimu-chart"></div>
    <div v-if="renderError" class="jimu-chart-error" role="alert">
      <span>{{ renderError }}</span>
      <button type="button" @click="retryRender">重新加载</button>
    </div>
  </div>
</template>

<style scoped>
.jimu-chart {
  width: 100%;
  height: 100%;
}
.jimu-chart-shell {
  position: relative;
  width: 100%;
  height: 100%;
  min-height: 120px;
}
.jimu-chart-error {
  position: absolute;
  inset: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  color: #9b2c2c;
  background: rgba(255, 247, 247, 0.94);
  border: 1px solid #f0b7b7;
  border-radius: 8px;
  text-align: center;
}
.jimu-chart-error button {
  padding: 6px 12px;
  border: 1px solid #c43d3d;
  border-radius: 5px;
  color: #fff;
  background: #b83232;
  cursor: pointer;
}
</style>
