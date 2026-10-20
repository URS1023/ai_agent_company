/** 图表的原值与坐标分开处理；十进制运算有界，不能从已失真的 JSON number 恢复原值。 */
const DECIMAL_LIMIT = 4096;

export function parseChartDecimal(value) {
  if (!["string", "number", "bigint"].includes(typeof value)) return null;
  if (typeof value === "number" && !Number.isFinite(value)) return null;
  const text =
    typeof value === "number" && Object.is(value, -0)
      ? "-0"
      : String(value).trim();
  if (!text || text.length > DECIMAL_LIMIT) return null;
  // 仅接受规范千分位，避免把 1,2 之类的非数值文本改写为 12。
  const normalized =
    /^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d*)?(?:[eE][+-]?\d+)?$/.test(text)
      ? text.replace(/,/g, "")
      : text;
  const match =
    /^([+-]?)(?:(\d+)(?:\.(\d*))?|\.(\d+))(?:[eE]([+-]?\d+))?$/.exec(
      normalized,
    );
  if (!match) return null;
  const exponent = Number(match[5] || 0);
  if (!Number.isSafeInteger(exponent) || Math.abs(exponent) > DECIMAL_LIMIT)
    return null;
  const fraction = match[3] ?? match[4] ?? "";
  let coefficient =
    BigInt((match[2] || "0") + fraction) * (match[1] === "-" ? -1n : 1n);
  let scale = fraction.length - exponent;
  if (Math.abs(scale) > DECIMAL_LIMIT) return null;
  if (scale < 0) {
    coefficient *= 10n ** BigInt(-scale);
    scale = 0;
  }
  const number = Number(normalized);
  // 非零原值下溢为 0 时不画零点；原文仍保留供明细与提示核对。
  const coordinate =
    Number.isFinite(number) && (number !== 0 || coefficient === 0n)
      ? number
      : null;
  return {
    text,
    coefficient,
    scale,
    coordinate,
    approximate:
      typeof value === "number" &&
      Number.isInteger(value) &&
      !Number.isSafeInteger(value),
  };
}

export function chartPoint(value, extra = {}) {
  const parsed = parseChartDecimal(value);
  return {
    ...extra,
    value: parsed?.coordinate ?? null,
    exactValue: parsed?.text ?? null,
    approximate: parsed?.approximate ?? false,
  };
}

function decimalText(coefficient, scale) {
  const negative = coefficient < 0n;
  const digits = (negative ? -coefficient : coefficient)
    .toString()
    .padStart(scale + 1, "0");
  return (
    (negative ? "-" : "") +
    (scale ? digits.slice(0, -scale) + "." + digits.slice(-scale) : digits)
  );
}

/** 只加有效值并记录缺失数量；没有有效值的分组保持未知，部分和会在提示中明确标注。 */
export function sumChartDecimals(values) {
  let coefficient = 0n,
    scale = 0,
    validCount = 0,
    missingCount = 0,
    approximate = false;
  for (const value of values) {
    const parsed = parseChartDecimal(value);
    if (!parsed) {
      missingCount++;
      continue;
    }
    const nextScale = Math.max(scale, parsed.scale);
    coefficient =
      coefficient * 10n ** BigInt(nextScale - scale) +
      parsed.coefficient * 10n ** BigInt(nextScale - parsed.scale);
    scale = nextScale;
    approximate ||= parsed.approximate;
    validCount++;
  }
  const result = chartPoint(
    validCount ? decimalText(coefficient, scale) : null,
  );
  return { ...result, approximate, validCount, missingCount };
}

export function aggregateChartRows(rows, categoryField, valueField) {
  const groups = new Map();
  for (const row of rows) {
    const name =
      row[categoryField] == null ? "未提供" : String(row[categoryField]);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(row[valueField]);
  }
  return [...groups].map(([name, values]) => ({
    name,
    ...sumChartDecimals(values),
  }));
}

export function chartDecimalDifference(last, first) {
  const right = parseChartDecimal(first),
    left = parseChartDecimal(last);
  if (!left || !right) return chartPoint(null);
  const result = sumChartDecimals([
    left.text,
    decimalText(-right.coefficient, right.scale),
  ]);
  return { ...result, approximate: left.approximate || right.approximate };
}

export function escapeChartHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (character) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        character
      ],
  );
}

export function chartValueText(point) {
  const text = point?.exactValue == null ? "未提供" : String(point.exactValue);
  const partial = point?.missingCount
    ? `（有效 ${point.validCount}/${point.validCount + point.missingCount} 条）`
    : "";
  return (
    (point?.approximate && point?.exactValue != null ? "约 " : "") +
    text +
    partial
  );
}

/** zrender 旧版测宽缓存使用普通对象；不可见分隔符隔离继承键，数据与业务标签原文不变。 */
export function chartDisplayText(value) {
  return String(value ?? "")
    .split("\n")
    .map((line) =>
      Object.prototype.hasOwnProperty.call(Object.prototype, line)
        ? line + "\u2060"
        : line,
    )
    .join("\n");
}

export function chartBusinessName(option, value) {
  const names = option?.__chartBusinessNames || {};
  return Object.prototype.hasOwnProperty.call(names, value)
    ? names[value]
    : value;
}

export function chartLegendSelection(option, event) {
  return {
    name: chartBusinessName(option, event.name),
    selected: Object.fromEntries(
      Object.entries(event.selected || {}).map(([name, selected]) => [
        chartBusinessName(option, name),
        selected,
      ]),
    ),
  };
}

/** 只为继承键冲突建立绘图库标识；映射不写回原始行，选择与点击出口必须还原。 */
function protectChartNames(option, series) {
  const names = { ...(option.__chartBusinessNames || {}) };
  const list = (value) => (Array.isArray(value) ? value : value ? [value] : []);
  const known = new Set();
  const scan = (value) => {
    if (typeof value === "string") known.add(value);
    else if (Array.isArray(value)) value.forEach(scan);
    else if (value && typeof value === "object")
      Object.values(value).forEach(scan);
  };
  scan(option);
  const encode = (value) => {
    if (
      typeof value !== "string" ||
      !Object.prototype.hasOwnProperty.call(Object.prototype, value)
    )
      return value;
    const existing = Object.keys(names).find((key) => names[key] === value);
    if (existing) return existing;
    let index = Object.keys(names).length,
      key;
    do {
      key = `__chart_business_${index++}__`;
    } while (known.has(key));
    known.add(key);
    names[key] = value;
    return key;
  };
  for (const item of series) {
    item.name = encode(item.name);
    if (Array.isArray(item.data))
      item.data = item.data.map((point) =>
        point && typeof point === "object" && !Array.isArray(point)
          ? {
              ...point,
              ...(point.name != null ? { name: encode(point.name) } : {}),
            }
          : point,
      );
  }
  for (const axis of [...list(option.xAxis), ...list(option.yAxis)]) {
    if (Array.isArray(axis.data))
      axis.data = axis.data.map((item) =>
        item && typeof item === "object"
          ? { ...item, value: encode(item.value) }
          : encode(item),
      );
  }
  for (const legend of list(option.legend)) {
    if (Array.isArray(legend.data))
      legend.data = legend.data.map((item) =>
        typeof item === "object"
          ? { ...item, name: encode(item.name) }
          : encode(item),
      );
    if (legend.selected)
      legend.selected = Object.fromEntries(
        Object.entries(legend.selected).map(([key, value]) => [
          encode(key),
          value,
        ]),
      );
  }
  for (const radar of list(option.radar))
    if (Array.isArray(radar.indicator))
      radar.indicator = radar.indicator.map((item) => ({
        ...item,
        name: encode(item.name),
      }));
  for (const dataset of list(option.dataset))
    if (Array.isArray(dataset.source))
      dataset.source = dataset.source.map((row) =>
        Array.isArray(row)
          ? row.map(encode)
          : row && typeof row === "object"
            ? Object.fromEntries(
                Object.entries(row).map(([key, value]) => [key, encode(value)]),
              )
            : row,
      );
  option.__chartBusinessNames = names;
}

function protectTextCache(option, series) {
  const list = (value) => (Array.isArray(value) ? value : value ? [value] : []);
  const decode = (value) => chartBusinessName(option, value);
  const format =
    (previous, token) =>
    (value, ...args) =>
      chartDisplayText(
        typeof previous === "function"
          ? previous(decode(value), ...args)
          : typeof previous === "string"
            ? previous.replaceAll(token, String(decode(value)))
            : decode(value),
      );
  for (const axis of [...list(option.xAxis), ...list(option.yAxis)]) {
    if (axis.name != null) axis.name = chartDisplayText(axis.name);
    axis.axisLabel = {
      ...(axis.axisLabel || {}),
      formatter: format(axis.axisLabel?.formatter, "{value}"),
    };
  }
  for (const legend of list(option.legend))
    legend.formatter = format(legend.formatter, "{name}");
  for (const radar of list(option.radar))
    radar.axisName = {
      ...(radar.axisName || {}),
      formatter: format(radar.axisName?.formatter, "{value}"),
    };
  for (const title of list(option.title)) {
    if (typeof title !== "object") continue;
    if (title.text != null) title.text = chartDisplayText(title.text);
    if (title.subtext != null) title.subtext = chartDisplayText(title.subtext);
  }
  for (const item of series) {
    for (const owner of [item, item.emphasis].filter(Boolean)) {
      const previous = owner.label?.formatter;
      owner.label = {
        ...(owner.label || {}),
        formatter: (params) => {
          params = {
            ...params,
            name: decode(params.name),
            seriesName: decode(params.seriesName),
          };
          if (typeof previous === "function")
            return chartDisplayText(previous(params));
          if (typeof previous === "string")
            return chartDisplayText(
              previous.replace(/\{([abcd])\}/g, (_, key) =>
                String(
                  {
                    a: params.seriesName,
                    b: params.name,
                    c: params.value,
                    d: params.percent,
                  }[key] ?? "",
                ),
              ),
            );
          return chartDisplayText(
            ["pie", "funnel", "treemap"].includes(item.type)
              ? params.name
              : params.value,
          );
        },
      };
    }
  }
}

/** 强制纯文本 richText 提示，不插入 HTML/marker；只从独立原值通道读取业务数值。 */
export function withExactChartTooltip(option) {
  const series = Array.isArray(option.series)
    ? option.series
    : option.series
      ? [option.series]
      : [];
  protectChartNames(option, series);
  protectTextCache(option, series);
  const formatter = (payload) =>
    chartDisplayText(
      (Array.isArray(payload) ? payload : [payload])
        .map((item) => {
          if (!item) return "";
          const current = series[item.seriesIndex || 0] || {};
          const datum = current.data?.[item.dataIndex] ?? item.data;
          let point =
            datum && typeof datum === "object" && !Array.isArray(datum)
              ? datum
              : {};
          if (!Object.prototype.hasOwnProperty.call(point, "exactValue")) {
            if (current.exactValues)
              point = {
                ...chartPoint(current.exactValues[item.dataIndex]),
                approximate:
                  current.approximateValues?.[item.dataIndex] || false,
              };
            else if (
              item.data &&
              typeof item.data === "object" &&
              item.encode
            ) {
              // dataset 的原始行尚未丢失精度；按 ECharts 已解析的维度编码取值，不猜字段顺序。
              const dimensions = [
                ...new Set([
                  ...(item.encode.y || []),
                  ...(item.encode.x || []),
                ]),
              ];
              const fields = dimensions
                .map((index) => item.dimensionNames?.[index])
                .filter(Boolean);
              return fields
                .filter((field) => parseChartDecimal(item.data[field]) != null)
                .map(
                  (field) =>
                    `${field}: ${chartValueText(chartPoint(item.data[field]))}`,
                )
                .join("\n");
            } else point = chartPoint(item.value);
          }
          if (Array.isArray(point.exactValue)) {
            return point.exactValue
              .map(
                (value, index) =>
                  `${point.exactLabels?.[index] ?? index + 1}: ${chartValueText(
                    {
                      ...chartPoint(value),
                      approximate:
                        point.approximate ||
                        point.exactApproximate?.[index] ||
                        false,
                    },
                  )}`,
              )
              .join("\n");
          }
          return [
            chartBusinessName(option, item.name),
            chartBusinessName(option, item.seriesName),
            chartValueText(point),
          ]
            .filter((value) => value !== undefined && value !== "")
            .join(": ");
        })
        .filter(Boolean)
        .join("\n"),
    );
  const itemTrigger = series.some((item) =>
    ["radar", "pie", "gauge", "scatter", "treemap", "funnel", "map"].includes(
      item.type,
    ),
  );
  option.tooltip = {
    ...(option.tooltip || {}),
    trigger: itemTrigger ? "item" : option.tooltip?.trigger || "axis",
    renderMode: "richText",
    confine: true,
    formatter,
  };
  // 系列配置不能用旧模板的 {c} 覆盖已核验的业务原值。
  for (const item of series)
    if (item.tooltip)
      item.tooltip = { ...item.tooltip, renderMode: "richText", formatter };
  return option;
}
