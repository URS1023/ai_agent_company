/** 纯Node/ECharts SVG验收，不启动浏览器、HTTP、模型或数据库。 */
import test from "node:test";
import assert from "node:assert/strict";
import * as echarts from "echarts";
import { specialChartOption } from "../src/modules/reports/specialChartOption.ts";
import { safeChartOption } from "../src/utils/safeContent.ts";
import { withExactChartTooltip } from "../src/utils/chartPrecision.js";

function widget(
  component,
  rows = [
    { name: "一班", value: "25.0000" },
    { name: "二班", value: "10.00" },
  ],
) {
  return {
    component,
    title: "实际数据",
    binding: { query_id: "test-fixture" },
    presentation_model: { status: "ready", rows, columns: [], maximum: "100" },
  };
}
function option(component, rows) {
  return safeChartOption(
    withExactChartTooltip(specialChartOption(widget(component, rows), {})),
  );
}

test("六个源图表保留自身ECharts类型与实际SVG", () => {
  const types = {
    JFunnel: "funnel",
    JPyramidFunnel: "funnel",
    JRose: "pie",
    JRectangle: "treemap",
    JSemiGauge: "gauge",
    JCircleRadar: "radar",
  };
  for (const [component, type] of Object.entries(types)) {
    const rows =
      component === "JSemiGauge"
        ? [{ name: "电流", value: "25.0000" }]
        : undefined;
    const actual = option(component, rows);
    assert.equal(actual.series[0].type, type);
    const chart = echarts.init(null, undefined, {
      renderer: "svg",
      ssr: true,
      width: 640,
      height: 320,
    });
    try {
      chart.setOption(actual);
      assert.equal(chart.getOption().series[0].type, type);
      const svg = chart.renderToSVGString();
      assert.match(svg, /^<svg/);
      assert.match(svg, /<path/);
      assert.doesNotMatch(svg, /NaN|undefined/);
    } finally {
      chart.dispose();
    }
  }
});

test("漏斗排序、玫瑰零值、矩形布局、半仪表上限与圆形雷达正确", () => {
  assert.equal(option("JFunnel").series[0].sort, "descending");
  assert.equal(option("JPyramidFunnel").series[0].sort, "ascending");
  assert.equal(option("JRose").series[0].roseType, "area");
  assert.equal(option("JRose").series[0].stillShowZeroSum, false);
  assert.equal(option("JRectangle").series[0].nodeClick, false);
  const gauge = option("JSemiGauge", [{ name: "电流", value: "0" }]);
  assert.equal(gauge.series[0].startAngle, 180);
  assert.equal(gauge.series[0].endAngle, 0);
  assert.equal(gauge.series[0].max, 100);
  assert.equal(gauge.series[0].data[0].value, 0);
  const radar = option("JCircleRadar");
  assert.equal(radar.radar.shape, "circle");
  assert.deepEqual(radar.series[0].data[0].value, [25, 10]);
  assert.deepEqual(radar.series[0].data[0].exactValue, ["25.0000", "10.00"]);
});

test("十进制原值用于提示，不从近似几何值反推", () => {
  const value = "9007199254740993123456789.1200";
  const actual = option("JFunnel", [{ name: "constructor", value }]);
  assert.equal(actual.series[0].data[0].exactValue, value);
  const text = actual.tooltip.formatter({
    seriesIndex: 0,
    dataIndex: 0,
    name: actual.series[0].data[0].name,
  });
  assert.match(text, /constructor/);
  assert.ok(text.includes(value));
  const radar = option("JCircleRadar");
  assert.match(
    radar.tooltip.formatter({ seriesIndex: 0, dataIndex: 0 }),
    /一班: 25\.0000/,
  );
});

test("模型未就绪时不输出假图或默认数据", () => {
  for (const status of ["empty", "invalid_data", "needs_mapping"]) {
    const actual = specialChartOption(
      {
        ...widget("JFunnel"),
        presentation_model: {
          status,
          rows: [],
          columns: [],
          message: "缺少实际字段",
        },
      },
      {},
    );
    assert.equal(actual.series, undefined);
    assert.equal(actual.graphic.style.text, "缺少实际字段");
  }
});
