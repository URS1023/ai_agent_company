"use strict";

// 合成报表仅用于界面回归；全部编码、金额和查询标识都是虚构数据。
const clone = value => JSON.parse(JSON.stringify(value));
const CAPTURED = "2026-09-29T02:00:00";
const TOTAL_ID = "synthetic-total-query", GROUP_ID = "synthetic-group-query";
const theme = {
  id: "synthetic-finance", primary: "#2377FF", secondary: "#10CDEA", accent: "#FFB55B",
  text: "#EAF8FF", muted: "#A8C3DD", panel: "#09245AE8", panelStrong: "#09245AF5",
  grid: "#2377FF55", background: "#041436", positive: "#35E4A4",
  palette: ["#2377FF", "#10CDEA", "#FFB55B", "#35E4A4"]
};
const fields = { contract: "合同金额", paid: "已付金额", unpaid: "未付金额", requested: "本次申请支付金额", count: "记录数", department: "部门编码" };
const totals = [{ contract: "120000.000000", paid: "25000.000000", unpaid: "95000.000000", requested: "10000.000000", count: 12 }];
const groups = [
  { department: "SYNTH-ALPHA0123456789abcd", contract: "50000.000000", paid: "10000.000000", unpaid: "40000.000000", requested: "5000.000000", count: 4 },
  { department: "SYNTH-BRAVO0123456789ef01", contract: "40000.000000", paid: "8000.000000", unpaid: "32000.000000", requested: "3000.000000", count: 3 },
  { department: "SYNTH-CHARLIE01234567896789", contract: "30000.000000", paid: "7000.000000", unpaid: "23000.000000", requested: "2000.000000", count: 5 },
  { department: null, contract: null, paid: null, unpaid: null, requested: null, count: 0 }
];

function createDashboardSnapshotFixture({ width = 652, height = 368, multipleEmpty = false } = {}) {
  const ratioX = width / 652, ratioY = height / 368;
  const widgets = [];
  const add = (id, component, type, title, box, config = {}) => {
    const widget = { id, component, type, title, componentName: title, visible: true,
      x: Math.round(box[0] * ratioX), y: Math.round(box[1] * ratioY),
      w: Math.round(box[2] * ratioX), h: Math.round(box[3] * ratioY),
      panelVariant: "showcase-template-synthetic", theme: clone(theme),
      style: { background: "#00000000", borderColor: "#00000000" },
      config: { dataType: 1, chartDataParsed: [], ...config }, data: [] };
    widgets.push(widget); return widget;
  };
  const bind = (widget, field, summary = false) => {
    const rows = clone(summary ? totals : groups), queryId = summary ? TOTAL_ID : GROUP_ID;
    const binding = { valueField: field, unit: field === "count" ? "" : "元" };
    if (!summary) binding.labelField = "department";
    if (widget.type === "table") binding.columns = ["department", field];
    widget.metricName = fields[field] + (summary ? "" : "（按部门）");
    widget.data = clone(rows); widget.dataStatus = "verified";
    Object.assign(widget.config, { chartDataParsed: rows, chartData: JSON.stringify(rows), queryId,
      snapshotOnly: true, dataStatus: "verified", verifiedAt: CAPTURED, snapshotBinding: binding, columnLabels: { ...fields } });
    return widget;
  };
  add("synthetic-title", "JText", "title", "合成财务快照验收", [143, 4, 367, 26],
    { option: { body: { text: "合成财务快照验收", color: theme.text, fontSize: width < 1000 ? 34 : 64, letterSpacing: 3 } } });
  add("synthetic-clock", "JCurrentTime", "time", "数据时间", [533, 7, 109, 18], { option: { body: { color: theme.text } } });
  bind(add("synthetic-total", "JStatsSummary", "metric", "旧模板百分比", [143, 42, 367, 55], { option: { unit: "%" } }), "contract", true);
  ["paid", "unpaid", "requested", "count"].forEach((field, index) => bind(add(`synthetic-metric-${field}`, "JStatsSummary", "metric", "旧模板指标",
    [143 + 93 * index, 110, index === 3 ? 87 : 80, 44], { option: { unit: "%" } }), field, true));
  const chartOption = { title: { show: true, text: "旧模板标题", top: 8, left: 12, textStyle: { fontSize: 14, color: theme.text } },
    xAxis: { type: "category", data: ["样例"] }, yAxis: { type: "category", data: ["假金额"], max: 100 },
    series: [{ type: "bar", stack: "old", data: [99] }, { type: "bar", data: [100] }] };
  bind(add("synthetic-main-chart", "JBar", "bar", "旧模板中央图", [143, 168, 367, 170], { option: clone(chartOption) }), "unpaid");
  bind(add("synthetic-left-chart", "JHorizontalBar", "hbar", "旧模板来源", [13, 151, 115, 78], { option: clone(chartOption) }), "paid");
  bind(add("synthetic-right-chart", "JHorizontalBar", "hbar", "旧模板排行", [524, 42, 115, 95], { option: clone(chartOption) }), "contract");
  const tableOption = { header: ["序号", "对象", "状态", "指标"], headerBGC: "#123766", fontColor: theme.text };
  bind(add("synthetic-count-table", "JScrollBoard", "table", "旧模板实时访客", [13, 42, 115, 95], { option: clone(tableOption) }), "count");
  bind(add("synthetic-amount-table", "JScrollBoard", "table", "旧模板实时事件", [524, 151, 115, 187], { option: clone(tableOption) }), "requested");
  add("synthetic-empty-table", "JScrollBoard", "table", "旧模板实时访客", [13, 243, 115, 95], { option: clone(tableOption) });
  if (multipleEmpty) {
    const emptyChart = widgets.find(widget => widget.id === "synthetic-right-chart");
    emptyChart.data = []; delete emptyChart.dataStatus; delete emptyChart.metricName;
    emptyChart.config = { dataType: 1, chartDataParsed: [], option: clone(chartOption) };
  }
  return { report: { id: `synthetic-${width}-${multipleEmpty ? "multiple" : "single"}`, reportName: "合成财务快照验收",
    description: "合成数据，仅用于界面回归；不代表真实业务。", version: 1, refreshSeconds: 0,
    templateId: "synthetic-finance-template", templateMode: "manual",
    content: { canvas: { width, height, styleLocked: true, backgroundColor: theme.background, backgroundImage: "", theme }, widgets },
    lineage: { generationMode: "saved_query_snapshot", preserveSourceWidgets: true,
      queryScopes: widgets.filter(widget => widget.config.queryId).map(widget => ({ widgetId: widget.id, queryId: widget.config.queryId, queriedAt: CAPTURED })) }
  } };
}

module.exports = { createDashboardSnapshotFixture };
