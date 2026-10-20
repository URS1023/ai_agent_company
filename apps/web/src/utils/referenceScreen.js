/** 参考皮肤只用于明确的原图示例；任何真实绑定、快照或编辑都会走数据渲染。 */
import { inferSeriesSchema, normalizeSeriesRows } from "./dashboardData.js";

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (!value || typeof value !== "object") return value;
  return Object.fromEntries(
    Object.keys(value)
      .sort()
      .map((key) => [key, canonical(value[key])]),
  );
}

export function isReferenceSample(widget, data, remote = false) {
  return (
    !remote &&
    !widget.snapshotContext &&
    !widget.config?.snapshotOnly &&
    widget.config?.referenceSample === true &&
    widget.config?.dataStatus === "sample" &&
    referenceDataHash(data) === widget.referenceSkin?.sampleHash
  );
}

export function referenceDataHash(data) {
  const value = JSON.stringify(canonical(data)) || "";
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1)
    hash = Math.imul(hash ^ value.charCodeAt(index), 16777619) >>> 0;
  return hash.toString(16).padStart(8, "0");
}

/** 只从当前数据派生系列；不复用参考截图或组件库中的旧数据。 */
export function referenceChartOption(widget, data) {
  const skin = widget.referenceSkin || {};
  const fontSize = skin.fontSize || 10;
  const color = skin.palette || [skin.color || "#22cfd1"];
  const rows = Array.isArray(data) ? data : [];
  const schema = inferSeriesSchema(
    rows,
    widget.config?.snapshotBinding || widget.config?.dataMapping,
  );
  const points = normalizeSeriesRows(rows);
  const base = {
    animation: false,
    backgroundColor: "transparent",
    color,
    textStyle: {
      color: skin.textColor,
      fontSize,
      fontFamily: "Microsoft YaHei, sans-serif",
    },
    tooltip: { trigger: "axis", confine: true },
    grid: {
      left: "3%",
      right: "4%",
      top: "10%",
      bottom: "3%",
      containLabel: true,
    },
    legend: {
      show: schema.series.length > 1,
      top: 0,
      textStyle: { color: skin.textColor, fontSize },
      itemWidth: fontSize,
      itemHeight: fontSize / 2,
    },
  };
  const kind = skin.kind;
  if (["pie", "ring"].includes(kind)) {
    const values = points.map((point) => ({ ...point, name: point.label }));
    if (
      rows.length === 1 &&
      rows[0]?.unit === "%" &&
      Number(values[0]?.value) >= 0 &&
      Number(values[0]?.value) <= 100
    ) {
      values.push({
        name: "未完成",
        value: 100 - Number(values[0].value),
        itemStyle: { color: "#ffffff28" },
      });
    }
    return {
      ...base,
      legend: {
        show: values.length > 1,
        orient: "vertical",
        right: "1%",
        top: "center",
        textStyle: base.textStyle,
      },
      tooltip: { trigger: "item", confine: true },
      series: [
        {
          type: "pie",
          radius: kind === "pie" ? "76%" : ["48%", "72%"],
          center: values.length > 1 ? ["37%", "50%"] : ["50%", "50%"],
          label: { show: false },
          data: values,
        },
      ],
    };
  }
  if (kind === "gauge")
    return {
      ...base,
      legend: { show: false },
      tooltip: { trigger: "item", confine: true },
      series: [
        {
          type: "gauge",
          radius: "86%",
          min: 0,
          max: Math.max(100, Number(points[0]?.value) || 0),
          axisLine: {
            lineStyle: {
              width: Math.max(3, widget.h * 0.045),
              color: [
                [0.3, color[0]],
                [0.7, color[1] || color[0]],
                [1, color[2] || color[0]],
              ],
            },
          },
          progress: { show: true, width: Math.max(3, widget.h * 0.045) },
          axisLabel: { color: skin.textColor, fontSize },
          splitLine: { length: 5 },
          axisTick: { length: 3 },
          pointer: { length: "55%", width: 3 },
          title: { show: false },
          detail: {
            color: skin.color,
            fontSize: Math.max(12, widget.h * 0.18),
            offsetCenter: [0, "65%"],
          },
          data: points
            .slice(0, 1)
            .map((point) => ({ ...point, name: point.label })),
        },
      ],
    };
  if (kind === "radar")
    return {
      ...base,
      legend: { show: false },
      tooltip: { trigger: "item", confine: true },
      radar: {
        radius: "62%",
        indicator: points.map((point) => ({
          name: point.label,
          max: Math.max(100, Number(point.value) || 0),
        })),
        axisName: { color: skin.textColor, fontSize },
        splitArea: { areaStyle: { color: ["#ffffff05", "#ffffff0b"] } },
      },
      series: [
        {
          type: "radar",
          areaStyle: { opacity: 0.35 },
          data: [{ value: points.map((point) => point.value) }],
        },
      ],
    };
  const horizontal = kind === "hbar";
  const axis = {
    axisLabel: { color: skin.textColor, fontSize, hideOverlap: true },
    axisTick: { show: false },
    axisLine: { lineStyle: { color: `${skin.textColor || "#eaf8ff"}35` } },
    splitLine: { lineStyle: { color: `${skin.textColor || "#eaf8ff"}18` } },
  };
  const category = {
    ...axis,
    type: "category",
    data: schema.labels,
    inverse: horizontal,
    splitLine: { show: false },
  };
  const value = {
    ...axis,
    type: "value",
    axisLabel: { ...axis.axisLabel, show: !horizontal },
    splitLine: { ...axis.splitLine, show: !horizontal },
    axisLine: { show: !horizontal, ...axis.axisLine },
    min: schema.series.every((series) =>
      series.data.every((value) => value == null || value >= 0),
    )
      ? 0
      : undefined,
  };
  return {
    ...base,
    xAxis: horizontal ? value : category,
    yAxis: horizontal ? category : value,
    series: schema.series.map((series, index) => ({
      name: series.name,
      type: ["line", "area"].includes(kind) ? "line" : "bar",
      smooth: ["line", "area"].includes(kind),
      symbolSize: 3,
      barMaxWidth: Math.max(3, widget.w * 0.06),
      showBackground: horizontal,
      backgroundStyle: { color: "#ffffff18" },
      label: {
        show: horizontal,
        position: "right",
        color: skin.textColor,
        fontSize,
      },
      itemStyle: {
        borderRadius: 0,
        color: ["bar", "hbar"].includes(kind)
          ? {
              type: "linear",
              x: 0,
              y: 0,
              x2: horizontal ? 1 : 0,
              y2: horizontal ? 0 : 1,
              colorStops: [
                { offset: 0, color: color[index % color.length] },
                { offset: 1, color: `${color[index % color.length]}55` },
              ],
            }
          : color[index % color.length],
      },
      lineStyle: { width: Math.max(1, widget.w / 250) },
      areaStyle: kind === "area" ? { opacity: 0.28 } : undefined,
      data: series.data,
    })),
  };
}
