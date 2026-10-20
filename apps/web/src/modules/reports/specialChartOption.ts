/** 精提Test5漏斗、圆形雷达、玫瑰、半仪表和矩形分支；不使用示例或补造系列。 */
import type { PresentationModel } from "./presentation";
export function specialChartOption(
  widget: Record<string, any>,
  source: Record<string, any>,
) {
  const model = widget.presentation_model as PresentationModel | undefined;
  if (!model || model.status !== "ready")
    return {
      graphic: {
        type: "text",
        left: "center",
        top: "middle",
        style: {
          text:
            model?.message ||
            (model?.status === "empty"
              ? "暂无数据"
              : widget.binding
                ? "刷新数据"
                : "选择数据查询"),
          fill: "#8298b5",
          fontSize: 14,
        },
      },
    };
  const palette =
    Array.isArray(source.color) && source.color.length
      ? source.color
      : ["#38bdf8", "#22d3a6", "#f0b65b", "#8b7cf6"];
  const rows = model.rows.filter((row) => row.value !== null);
  const values = rows.map((row) => Number(row.value));
  const data = rows.map((row, index) => ({
    name: row.name,
    value: values[index],
    exactValue: row.value,
  }));
  const common = {
    color: palette,
    backgroundColor: "transparent",
    animation:
      typeof window !== "undefined" &&
      !window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    textStyle: {
      color: "#d0e0f0",
      fontFamily: "Microsoft YaHei, Arial, sans-serif",
    },
    title: {
      ...source.title,
      text: widget.title ?? widget.componentName,
      left: "center",
      textStyle: { color: "#d0e0f0", fontSize: 14 },
    },
    tooltip: {
      trigger: "item",
      renderMode: "richText",
      formatter: (point: any) =>
        `${point.name}: ${point.data?.exactValue ?? "—"}`,
    },
  };
  if (["JFunnel", "JPyramidFunnel"].includes(widget.component))
    return {
      ...common,
      series: [
        {
          type: "funnel",
          top: 45,
          bottom: 15,
          left: "15%",
          width: "70%",
          sort:
            widget.component === "JPyramidFunnel" ? "ascending" : "descending",
          gap: 2,
          label: { color: "#d0e0f0" },
          data,
        },
      ],
    };
  if (widget.component === "JRose")
    return {
      ...common,
      legend: { type: "scroll", bottom: 0, textStyle: { color: "#d0e0f0" } },
      series: [
        {
          type: "pie",
          roseType: "area",
          stillShowZeroSum: false,
          radius: [0, "65%"],
          center: ["50%", "52%"],
          label: { color: "#d0e0f0" },
          data,
        },
      ],
    };
  if (widget.component === "JRectangle")
    return {
      ...common,
      series: [
        {
          type: "treemap",
          top: 40,
          left: 10,
          right: 10,
          bottom: 10,
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          label: {
            formatter: (point: any) =>
              `${point.name}\n${point.data.exactValue}`,
          },
          data,
        },
      ],
    };
  if (widget.component === "JSemiGauge")
    return {
      ...common,
      series: [
        {
          type: "gauge",
          startAngle: 180,
          endAngle: 0,
          center: ["50%", "72%"],
          radius: "85%",
          min: 0,
          max: Number(model.maximum),
          progress: { show: true, width: 14 },
          axisLine: { lineStyle: { width: 14 } },
          detail: {
            formatter: () => rows[0]?.value ?? "—",
            color: "#d0e0f0",
            valueAnimation: false,
            offsetCenter: [0, "-20%"],
          },
          data: data.slice(0, 1),
        },
      ],
    };
  const maximum = Math.max(...values, 1);
  return {
    ...common,
    radar: {
      shape: "circle",
      center: ["50%", "57%"],
      radius: "60%",
      indicator: rows.map((row) => ({ name: row.name, max: maximum })),
    },
    series: [
      {
        type: "radar",
        data: [
          {
            value: values,
            name: widget.title ?? widget.componentName,
            exactValue: rows.map((row) => row.value),
            exactLabels: rows.map((row) => row.name),
          },
        ],
        areaStyle: { opacity: 0.25 },
      },
    ],
  };
}
