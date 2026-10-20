/** 精提Test5来源变体；来源ID决定结构，标题可自由修改，不参与类型判断。 */
export const sourcePresentationRegistry = {
  "index.js:X:4:1009386233476579328:胶囊图:JCapsuleChart": "JCapsuleChart",
  "index.js:X:4:1536952329568276481:背景柱形图:JBackgroundBar":
    "JBackgroundBar",
  "index.js:X:4:1501138710950854657:多色仪表盘:JColorGauge": "JColorGauge",
  "index.js:X:4:200300100100:渐变仪表盘:JAntvGauge": "JAntvGauge",
  "index.js:X:4:1011136904469872896:发展历程:JDevHistory": "JDevHistory",
  "index.js:X:4:1756792729039:日历:JPermanentCalendar": "JPermanentCalendar",
  "index.js:r:1:local-15013142797119490:日历:JCalendar": "JCalendar",
  "index.js:r:1:local-15013142797119491:多视图日历:JMultiViewCalendar":
    "JMultiViewCalendar",
  "index.js:X:4:1757057327940:卡片滚动(横向):JCardScroll": "JCardScroll",
  "index.js:X:4:1757057327940:卡片滚动(竖向+序号):JCardScroll": "JCardScroll",
  "index.js:X:4:1757057327940:卡片滚动(高亮):JCardScroll": "JCardScroll",
  "index.js:X:4:1756869734251:卡片轮播:JCardCarousel": "JCardCarousel",
} as const;

export const sourceChartTypes = [
  "JCapsuleChart",
  "JBackgroundBar",
  "JColorGauge",
  "JAntvGauge",
];
export const sourceCalendarTypes = [
  "JPermanentCalendar",
  "JCalendar",
  "JMultiViewCalendar",
];
export const sourceCardTypes = ["JCardScroll", "JCardCarousel"];
export const sourceTextTypes = [
  "JDevHistory",
  ...sourceCalendarTypes,
  ...sourceCardTypes,
];
export const sourcePresentationTypes = [
  ...sourceChartTypes,
  ...sourceTextTypes,
];

export interface SourcePresentationRow {
  name: string;
  value: string | null;
  description: string;
  time: string;
  time_display?: string;
  unit: string;
  date?: string;
}
export interface SourcePresentationModel {
  status: "ready" | "empty" | "needs_mapping" | "invalid_data";
  component: string;
  message?: string;
  rows: SourcePresentationRow[];
  minimum?: string;
  maximum?: string;
  gauge_position?: string | null;
  gauge_stops?: { value: string; color: string; position: string }[];
  gauge_ticks?: { position: string; value: string }[];
  calendar?: { month: string; timezone: "Asia/Shanghai" };
  variant?: {
    direction: "horizontal" | "vertical";
    show_index: boolean;
    highlight: boolean;
    mode: "scroll" | "carousel";
  };
}

export function isSourcePresentation(widget: Record<string, any>) {
  const type =
    sourcePresentationRegistry[
      widget.source_component_id as keyof typeof sourcePresentationRegistry
    ];
  return !!type && type === widget.component;
}

/** 不从原始rows推断或补造模型，刷新后只消费后端授权投影。 */
export function sourceModel(
  widget: Record<string, any>,
): SourcePresentationModel | undefined {
  const model = widget.presentation_model;
  return isSourcePresentation(widget) &&
    model?.component === widget.component &&
    Array.isArray(model.rows)
    ? model
    : undefined;
}

export function sourceEmptyMessage(widget: Record<string, any>) {
  if (!isSourcePresentation(widget)) return "组件来源无效";
  const model = sourceModel(widget);
  return (
    model?.message ||
    (model ? "暂无数据" : widget.binding ? "刷新数据" : "选择数据查询")
  );
}

/** 所有日期算术采用UTC日历；事件的上海自然日由后端给出，不猜测无时区时间。 */
export function validMonth(value: unknown): value is string {
  return (
    typeof value === "string" && /^(?!0000)\d{4}-(0[1-9]|1[0-2])$/.test(value)
  );
}
export function shanghaiDate(now = new Date()) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const part = (key: string) =>
    parts.find((item) => item.type === key)?.value ?? "";
  return `${part("year")}-${part("month")}-${part("day")}`;
}
export function shiftMonth(month: string, offset: number) {
  if (!validMonth(month) || !Number.isInteger(offset)) return month;
  const total =
    Number(month.slice(0, 4)) * 12 + Number(month.slice(5)) - 1 + offset;
  const year = Math.floor(total / 12);
  return year >= 1 && year <= 9999
    ? `${String(year).padStart(4, "0")}-${String((total % 12) + 1).padStart(2, "0")}`
    : month;
}
export function calendarGrid(
  month: string,
  rows: SourcePresentationRow[],
  today = shanghaiDate(),
) {
  if (!validMonth(month)) return [];
  const first = new Date(`${month}-01T00:00:00Z`);
  const start = first.getTime() - ((first.getUTCDay() + 6) % 7) * 86400000;
  const byDate = new Map<string, SourcePresentationRow[]>();
  for (const row of rows)
    if (row.date) byDate.set(row.date, [...(byDate.get(row.date) ?? []), row]);
  return Array.from({ length: 42 }, (_, index) => {
    const current = new Date(start + index * 86400000);
    const date = current.toISOString().split("T")[0]!;
    return {
      date,
      day: current.getUTCDate(),
      inMonth: date.startsWith(month),
      today: date === today,
      rows: byDate.get(date) ?? [],
    };
  });
}
export function firstCalendarDate(
  month: string,
  rows: SourcePresentationRow[],
  today = shanghaiDate(),
) {
  const event = rows.find((row) => row.date?.startsWith(month));
  return event?.date ?? (today.startsWith(month) ? today : `${month}-01`);
}
export function cardVariant(
  widget: Record<string, any>,
  model?: SourcePresentationModel,
) {
  if (model?.variant) return model.variant;
  // 原source ID已经核对；只做结构兼容，不依赖标题或补造业务内容。
  const id = String(widget.source_component_id ?? "");
  return {
    direction: id.includes("竖向+序号")
      ? ("vertical" as const)
      : ("horizontal" as const),
    show_index: id.includes("竖向+序号"),
    highlight: id.includes("高亮"),
    mode:
      widget.component === "JCardCarousel"
        ? ("carousel" as const)
        : ("scroll" as const),
  };
}
export function pageCount(length: number, size: number) {
  return Math.max(1, Math.ceil(length / Math.max(1, size)));
}
export function pageIndex(current: number, offset: number, count: number) {
  return (
    (current + (offset % Math.max(1, count)) + Math.max(1, count)) %
    Math.max(1, count)
  );
}
export function pageRows<T>(rows: T[], page: number, size: number) {
  return rows.slice(page * size, (page + 1) * size);
}

/** 图形用浮点定位，显示和tooltip始终保留后端Decimal字符串。 */
export function sourceChartOption(
  widget: Record<string, any>,
  model: SourcePresentationModel,
) {
  const colors = ["#38bdf8", "#22d3a6", "#f0b65b", "#8b7cf6"];
  const option = widget.config?.option ?? {};
  const configured = Array.isArray(option.color)
    ? option.color.filter(
        (color: unknown): color is string =>
          typeof color === "string" &&
          /^#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?$/.test(color),
      )
    : [];
  const palette: string[] = configured.length ? configured : colors;
  const rows = model.rows;
  const tooltip = {
    trigger: "item",
    renderMode: "richText",
    formatter: (point: any) =>
      `${rows[point.dataIndex]?.name ?? ""}: ${rows[point.dataIndex]?.value ?? "—"}${rows[point.dataIndex]?.unit ?? ""}`,
  };
  const common = {
    backgroundColor: "transparent",
    animation: false,
    textStyle: {
      color: "#d0e0f0",
      fontFamily: "Microsoft YaHei, Arial, sans-serif",
    },
    tooltip,
  };
  return ["JColorGauge", "JAntvGauge"].includes(widget.component)
    ? gaugeChartOption(widget, model, common)
    : barChartOption(widget, model, palette, common);
}

function gaugeChartOption(
  widget: Record<string, any>,
  model: SourcePresentationModel,
  common: Record<string, any>,
) {
  const rows = model.rows;
  // 量程与相邻大数的比例由服务端Decimal生成，不能先转Number相减。
  const stops = model.gauge_stops ?? [];
  if (
    model.gauge_position == null ||
    !stops.length ||
    !model.gauge_ticks?.length
  )
    return {
      ...common,
      graphic: {
        type: "text",
        left: "center",
        top: "middle",
        style: { text: "刷新数据", fill: "#9ab4c7" },
      },
    };
  const segments = stops.map((stop) => [Number(stop.position), stop.color]);
  const gradient = {
    type: "linear",
    x: 0,
    y: 0,
    x2: 1,
    y2: 0,
    colorStops: [
      { offset: 0, color: stops[0]!.color },
      ...stops.map((stop) => ({
        offset: Number(stop.position),
        color: stop.color,
      })),
    ],
  };
  return {
    ...common,
    series: [
      {
        type: "gauge",
        min: 0,
        max: 1,
        splitNumber: 5,
        startAngle: 220,
        endAngle: -40,
        center: ["50%", "55%"],
        radius: "76%",
        axisLine: {
          lineStyle: {
            width: 16,
            color:
              widget.component === "JAntvGauge" ? [[1, gradient]] : segments,
          },
        },
        progress: { show: false },
        pointer: {
          show: widget.component === "JColorGauge",
          itemStyle: { color: "#eaf8ff" },
        },
        axisTick: { show: false },
        splitLine: { length: 8, lineStyle: { color: "#7f9ab0" } },
        axisLabel: {
          color: "#9ab4c7",
          distance: 24,
          fontSize: 11,
          formatter: (value: number) =>
            model.gauge_ticks!.find(
              (tick) => Math.abs(Number(tick.position) - value) < 1e-8,
            )?.value ?? "",
        },
        detail: {
          color: "#eaf8ff",
          fontSize: 22,
          offsetCenter: [0, "45%"],
          formatter: () => `${rows[0]?.value ?? "—"}${rows[0]?.unit ?? ""}`,
        },
        data: [
          {
            name: rows[0]?.name ?? "",
            value: Number(model.gauge_position),
          },
        ],
      },
    ],
  };
}

function barChartOption(
  widget: Record<string, any>,
  model: SourcePresentationModel,
  palette: string[],
  common: Record<string, any>,
) {
  const rows = model.rows;
  const horizontal = widget.component === "JCapsuleChart";
  const categoryAxis = {
    type: "category",
    data: rows.map((row) => row.name),
    axisLine: { show: false },
    axisTick: { show: false },
    axisLabel: {
      color: "#a9bfd1",
      fontSize: 12,
      overflow: "truncate",
      width: 100,
    },
  };
  const valueAxis = {
    type: "value",
    axisLabel: { color: "#a9bfd1" },
    splitLine: { lineStyle: { color: "#27415a", type: "dashed" } },
  };
  return {
    ...common,
    grid: {
      top: 22,
      left: horizontal ? 110 : 45,
      right: 28,
      bottom: 35,
      containLabel: true,
    },
    xAxis: horizontal ? valueAxis : categoryAxis,
    yAxis: horizontal ? categoryAxis : valueAxis,
    series: [
      {
        type: "bar",
        data: rows.map((row) =>
          row.value === null ? null : Number(row.value),
        ),
        barMaxWidth: horizontal ? 18 : 32,
        showBackground: true,
        backgroundStyle: {
          color: "#1b354e",
          borderRadius: horizontal ? 12 : [3, 3, 0, 0],
        },
        itemStyle: {
          borderRadius: horizontal ? 12 : [3, 3, 0, 0],
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: horizontal ? 1 : 0,
            y2: horizontal ? 0 : 1,
            colorStops: [
              { offset: 0, color: palette[0] },
              { offset: 1, color: palette[1 % palette.length] },
            ],
          },
        },
      },
    ],
  };
}
