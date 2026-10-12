"use strict";
// 从已保存或合成的 Report 快照加载实际 Vue 构建；不访问原服务、模型或数据库。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");
const ROOT = path.resolve(__dirname, "../.."), WEB = path.join(ROOT, "apps/web");
const ORIGIN = "http://saved-dashboard-offline.invalid";

function snapshotCharts(content) {
  // 仅核验已经绑定真实快照的图表；模板空槽和纯文字、指标卡不进入 ECharts 比对。
  return (content.widgets || []).filter(widget => widget.visible !== false && widget.config?.snapshotBinding?.valueField
    && Array.isArray(widget.data) && widget.data.length && /^(bar|hbar|line|area|pie|scatter|radar|gauge|funnel|map)$/.test(widget.type || ""));
}

function assertSnapshotChart(widget, rendered) {
  const label = `${widget.id || widget.i} ${widget.metricName || widget.title || ""}`;
  assert(rendered && rendered.option, `${label} 必须读取到实际 ECharts 实例的 getOption()`);
  const binding = widget.config.snapshotBinding, rows = widget.data;
  const expectedValues = rows.map(row => row[binding.valueField] == null ? null : Number(row[binding.valueField]));
  assert(expectedValues.every(value => value === null || Number.isFinite(value)), `${label} 的绑定值必须为有效数值或原始空值`);
  const expectedLabels = rows.map((row, index) => String(binding.labelField ? row[binding.labelField] ?? "未提供" : index + 1));
  const option = rendered.option, series = Array.isArray(option.series) ? option.series : option.series ? [option.series] : [];
  assert.equal(series.length, 1, `${label} 只能显示当前 valueField 的真实系列，不能残留模板样例系列`);
  assert(!series[0].stack, `${label} 不得沿用模板堆叠配置改变金额柱高`);
  const actualValues = (series[0].data || []).map(item => {
    const value = item && typeof item === "object" && !Array.isArray(item) ? item.value : item;
    return value == null ? null : Number(value);
  });
  assert.deepEqual(actualValues, expectedValues, `${label} 实际系列必须逐项等于快照 ${binding.valueField}，不能取错列、归一化或保留样例`);
  const axes = [...(Array.isArray(option.xAxis) ? option.xAxis : option.xAxis ? [option.xAxis] : []),
    ...(Array.isArray(option.yAxis) ? option.yAxis : option.yAxis ? [option.yAxis] : [])];
  if (/^(bar|hbar|line|area)$/.test(widget.type)) {
    const categoryAxes = axes.filter(axis => axis.type === "category"), valueAxes = axes.filter(axis => axis.type === "value");
    assert.equal(categoryAxes.length, 1, `${label} 必须只有一个分类轴`);
    assert.equal(valueAxes.length, 1, `${label} 金额必须使用数值轴，不能作为等距分类绘制`);
    assert.deepEqual((categoryAxes[0].data || []).map(item => String(item && typeof item === "object" ? item.value : item)), expectedLabels,
      `${label} 实际类目必须与原始快照逐行对应，包括缺失部门名称`);
    const finite = expectedValues.filter(value => value !== null);
    if (finite.length && finite.every(value => value >= 0)) assert.equal(valueAxes[0].min, 0, `${label} 非负金额必须从零起画，不能保留模板截断范围`);
    assert(valueAxes[0].max == null, `${label} 不得沿用模板最大值截断真实金额`);
    const grid = Array.isArray(option.grid) ? option.grid[0] : option.grid;
    if (grid && rendered.chart_size?.height) {
      const height = rendered.chart_size.height;
      const pixels = value => typeof value === "string" && value.endsWith("%") ? parseFloat(value) * height / 100 : Number(value || 0);
      const availableHeight = height - pixels(grid.top) - pixels(grid.bottom);
      assert(availableHeight >= Math.min(60, height * 0.35), `${label} 内部上下边距把绘图区压缩到${availableHeight}px，金额轴将重叠`);
    }
  } else if (/^(pie|funnel)$/.test(widget.type)) {
    assert.deepEqual((series[0].data || []).map(item => String(item.name ?? "未提供")), expectedLabels, `${label} 实际分类必须来自快照`);
  }
  return { widget_id: widget.id || widget.i, title: widget.metricName || widget.title, value_field: binding.valueField,
    label_field: binding.labelField || null, expected_labels: expectedLabels, expected_values: expectedValues,
    actual_values: actualValues, rendered_option: option };
}

async function main(snapshotPath, outputPath) {
  assert(snapshotPath, "用法：node tests/ui/render_saved_dashboard.js <验收Report快照.json> [截图输出目录]");
  const saved = JSON.parse(fs.readFileSync(path.resolve(snapshotPath), "utf8"));
  const report = saved.report || saved;
  assert(report.content && report.reportName, "快照必须包含真实Report名称和content");
  const content = typeof report.content === "string" ? JSON.parse(report.content) : report.content;
  const boundCharts = snapshotCharts(content);
  const output = path.resolve(outputPath || path.join(path.dirname(snapshotPath), "preview-" + (report.id || "report")));
  fs.mkdirSync(output, { recursive: true });
  const results = [];
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 1024, height: 820 }, { width: 390, height: 844 }]) {
    const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
      headless: true, args: ["--disable-background-networking", "--disable-component-update", "--no-first-run", "--disable-sync"] });
    const page = await browser.newPage(), blocked = [], failures = [], calls = [], chartModules = new Set();
    try {
      await page.setViewport(viewport);
      await page.emulateTimezone("Asia/Shanghai");
      page.on("pageerror", error => failures.push(error.message));
      await page.setRequestInterception(true);
      page.on("request", request => {
        const url = new URL(request.url());
        if (url.origin !== ORIGIN) { blocked.push(url.href); void request.abort(); return; }
        if (url.pathname === "/favicon.ico") { void request.respond({ status: 204 }); return; }
        if (url.pathname.startsWith("/api/")) {
          calls.push(url.pathname);
          let data;
          if (url.pathname === "/api/auth/me") data = { user: { id: "fixture", must_change_password: false }, workspace: { id: "fixture" } };
          else if (url.pathname === "/api/v1/platform/navigation") data = { permissions: ["dashboards.view"] };
          else if (url.pathname === "/api/v1/reports/saved") data = { data: { ...report, content: JSON.stringify(content) } };
          else { blocked.push(url.pathname); void request.respond({ status: 403, body: "禁止重新执行业务查询" }); return; }
          void request.respond({ status: 200, contentType: "application/json", body: JSON.stringify(data) }); return;
        }
        if (url.pathname === "/fixture") {
          void request.respond({ status: 200, contentType: "text/html", body: `<!doctype html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><link rel="stylesheet" href="/assets/platform/ai-workbench.css"><link rel="stylesheet" href="/assets/platform/chat-dashboard-preview.css"><style>body{margin:0;background:#f9fbff;font-family:Arial}main{max-width:1120px;margin:24px auto;padding:8px}</style></head><body class="enterprise-ai-workbench"><main id="mount"></main><script src="/assets/platform/chat-dashboard-preview.js"></script></body></html>` }); return;
        }
        const relative = url.pathname === "/analytics-app" ? "assets/analytics/index.html" : decodeURIComponent(url.pathname).replace(/^\/+/, "");
        const file = path.resolve(WEB, relative);
        if (!file.startsWith(WEB + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { blocked.push(url.pathname); void request.respond({ status: 404 }); return; }
        const mime = ({ ".js": "application/javascript", ".css": "text/css", ".html": "text/html", ".svg": "image/svg+xml", ".png": "image/png", ".woff2": "font/woff2" })[path.extname(file)] || "application/octet-stream";
        const body = fs.readFileSync(file);
        if (path.extname(file) === ".js" && body.includes(Buffer.from("_echarts_instance_"))) chartModules.add(url.href);
        void request.respond({ status: 200, contentType: mime, body });
      });
      await page.goto(ORIGIN + "/fixture", { waitUntil: "networkidle0" });
      await page.evaluate(({ title, version }) => {
        localStorage.setItem("agent_token", "offline-only"); localStorage.setItem("agent_workspace_id", "fixture");
        const mount = document.getElementById("mount");
        mount.innerHTML = ChatDashboardPreview.markup({ previewPath: "/report/view/saved?workspace_id=fixture", title, version });
        ChatDashboardPreview.hydrate(mount);
      }, { title: report.reportName, version: report.version });
      await page.waitForSelector('[data-preview-state="ready"]', { timeout: 25000 });
      const frame = page.frames().find(item => item.url().includes("analytics-app"));
      assert(frame, "真实预览iframe必须加载");
      await frame.waitForFunction(() => document.body.innerText.trim().length > 10);
      await frame.evaluate(() => document.fonts.ready);
      // 等待组件一次性入场动画结束后核验，避免把入场的模糊帧当作交付截图。
      await frame.waitForFunction(() => !document.getAnimations().some(animation =>
        animation.effect?.getTiming().iterations !== Infinity && ['running', 'pending'].includes(animation.playState)), { timeout: 5000 });
      const text = await frame.$eval("body", body => body.innerText);
      assert(!/未保存预览数据|加载失败|未知组件/.test(text), text.slice(0, 1000));
      if (report.lineage?.generationMode === "saved_query_snapshot") {
        assert(!text.includes("LOCAL · LIVE"), "历史查询快照不能显示为实时采集");
        const unbound = (content.widgets || []).filter(widget => widget.visible !== false
          && (widget.type === "metric" || widget.type === "table" || /Table|ScrollBoard|RankingBoard/.test(widget.component || "")
            || /^(bar|hbar|line|area|pie|scatter|radar|gauge|funnel|map)$/.test(widget.type || ""))
          && !widget.config?.queryId);
        for (const widget of unbound) {
          const displayed = await frame.$eval(`[data-widget-id="${widget.id || widget.i}"]`, node => node.innerText);
          if (unbound.length === 1 && report.description?.trim()) {
            const count = new Set((content.widgets || []).filter(item => item.config?.snapshotOnly && item.config?.queryId)
              .map(item => item.config.queryId)).size;
            assert(displayed.includes('数据口径') && displayed.includes(report.description.trim())
              && displayed.includes(`${count} 项已保存查询`) && displayed.includes('不自动刷新'),
            '唯一空槽须展示实际报表说明和去重查询数，不能显示模板调试文案或伪造数值');
            assert(!displayed.includes('本次未绑定'));
          } else assert(displayed.includes("本次未绑定"), `模板空槽 ${widget.title || widget.id} 必须明确没有绑定业务结果`);
          assert(!widget.title || !displayed.includes(widget.title), `模板空槽不能继续显示 ${widget.title} 等旧业务标题`);
        }
        const captured = (report.lineage.queryScopes || []).map(scope => scope.queriedAt).filter(Boolean).sort().at(-1);
        if (captured && (content.widgets || []).some(widget => widget.component === "JCurrentTime")) {
          const normalized = captured.replace(" ", "T"), instant = new Date(/[zZ]|[+-]\d\d:\d\d$/.test(normalized) ? normalized : normalized + "Z");
          const parts = Object.fromEntries(new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
            hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(instant).map(part => [part.type, part.value]));
          const expectedTime = `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
          assert(text.includes(expectedTime), `快照时间必须将服务端UTC转为浏览器本地时间 ${expectedTime}`);
        }
      }
      const numbers = (text.replace(/,/g, "").match(/\d+(?:\.\d+)?/g) || []).map(Number);
      for (const [customer, date, amount] of saved.expected_groups || []) {
        assert(text.includes(customer) && text.includes(date), "实际DOM必须显示真实客户和日期");
        assert(numbers.includes(Number(amount)), `实际DOM必须显示原始金额${amount}`);
      }
      const charts = await frame.$$eval(".jimu-chart canvas", nodes => nodes.map(canvas => ({ width: canvas.width, height: canvas.height,
        painted: [...canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height).data].some((channel, index) => index % 4 === 3 && channel > 0) })));
      assert(charts.every(chart => chart.width > 0 && chart.height > 0 && chart.painted), "图表画布不可为空");
      const vectorCharts = await frame.$$eval('.jimu-chart svg', nodes => nodes.map(node => ({
        width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height,
        shapes: node.querySelectorAll('path, rect, circle, polyline, text').length
      })));
      assert(vectorCharts.every(chart => chart.width > 0 && chart.height > 0 && chart.shapes > 0), '矢量快照图表必须实际绘制');
      const renderedCharts = await frame.evaluate(async ({ ids, modules }) => {
        let init;
        const output = [];
        for (const id of ids) {
          const node = document.querySelector(`[data-widget-id="${CSS.escape(id)}"] .jimu-chart`);
          if (!node) { output.push({ widget_id: id, error: "未找到图表 DOM" }); continue; }
          let component = node.__vueParentComponent, option;
          for (let depth = 0; component && depth < 12; depth++, component = component.parent) {
            if (typeof component.exposed?.getRenderedOption === "function") { option = component.exposed.getRenderedOption(); break; }
          }
          if (!option && node.getAttribute("_echarts_instance_")) {
            // 生产构建可能移除 Vue 的 DOM 指针；原生 init 对已注册 DOM 直接返回既有实例，不创建新图表。
            for (const url of modules) {
              if (init) break;
              const exported = await import(url);
              init = Object.values(exported).find(value => typeof value === "function" && /["']ec_["']/.test(String(value)) && /afterinit/.test(String(value)));
            }
            if (init) option = init(node).getOption();
          }
          output.push({ widget_id: id, instance_id: node.getAttribute("_echarts_instance_"),
            chart_size: { width: node.clientWidth, height: node.clientHeight },
            visible_labels: [...node.querySelectorAll('svg text')].map(text => text.textContent),
            visible_font_sizes: [...node.querySelectorAll('svg text')].filter(text => text.textContent.trim()).map(text =>
              parseFloat(getComputedStyle(text).fontSize) * node.getBoundingClientRect().width / node.clientWidth), option: option || null });
        }
        return output;
      }, { ids: boundCharts.map(widget => widget.id || widget.i), modules: [...chartModules] });
      fs.writeFileSync(path.join(output, `rendered-chart-options-${viewport.width}.json`), JSON.stringify(renderedCharts, null, 2));
      const verifiedCharts = boundCharts.map(widget => assertSnapshotChart(widget, renderedCharts.find(item => item.widget_id === (widget.id || widget.i))));
      if (viewport.width >= 1000) for (const chart of renderedCharts) {
        assert(chart.visible_font_sizes.every(size => size >= 9),
          `${chart.widget_id} 在桌面预览的轴字缩成不可读尺寸：${JSON.stringify(chart.visible_font_sizes)}`);
      }
      const geometry = await frame.evaluate(() => {
        const rect = node => { const box = node.getBoundingClientRect(); return { x: box.x, y: box.y, right: box.right, bottom: box.bottom, width: box.width, height: box.height }; };
        return [...document.querySelectorAll('.jimu-text, .jimu-time span, .snapshot-bound .stat-value, .snapshot-bound .stat-label, .snapshot-scope-note > *')].map(node => {
          const widget = node.closest('[data-widget-id]'), range = document.createRange(); range.selectNodeContents(node);
          return { widget_id: widget?.dataset.widgetId, text: node.textContent, widget: widget ? rect(widget) : null,
            text_box: rect(range), font_size: getComputedStyle(node).fontSize,
            visible_font_size: widget ? parseFloat(getComputedStyle(node).fontSize) * widget.getBoundingClientRect().width / widget.clientWidth : 0 };
        });
      });
      for (const item of geometry) {
        if (!item.widget || !item.text.trim()) continue;
        assert(item.text_box.y >= item.widget.y - 1 && item.text_box.bottom <= item.widget.bottom + 1,
          `${item.widget_id} 的文本“${item.text}”超出组件高度，存在标题/金额裁切：${JSON.stringify(item)}`);
        assert(item.text_box.x >= item.widget.x - 1 && item.text_box.right <= item.widget.right + 1,
          `${item.widget_id} 的文本“${item.text}”超出组件宽度：${JSON.stringify(item)}`);
        if (viewport.width >= 1000) assert(item.visible_font_size >= 9,
          `${item.widget_id} 的桌面文字字号过小，不能只检查未越界：${JSON.stringify(item)}`);
      }
      const tableChecks = [];
      for (const widget of content.widgets || []) {
        const columns = widget.config?.snapshotBinding?.columns;
        if (!Array.isArray(columns) || !/Table|ScrollBoard/.test(widget.component || '')) continue;
        const actual = await frame.$eval(`[data-widget-id="${widget.id || widget.i}"]`, node => {
          const bounds = node.getBoundingClientRect(), title = node.querySelector('.table-panel-title')?.getBoundingClientRect(),
            head = node.querySelector('.scroll-head')?.getBoundingClientRect();
          return { headers: [...node.querySelectorAll('.scroll-head span')].map(cell => cell.title || cell.textContent),
            rows: [...node.querySelectorAll('.scroll-row')].map(row => [...row.children].map(cell => cell.title || cell.textContent)),
            visible_rows: [...node.querySelectorAll('.scroll-row')].filter(row => {
              const area = row.getBoundingClientRect(); return area.top >= (head?.bottom || bounds.top) - 1 && area.bottom <= bounds.bottom + 1;
            }).length,
            visible_font_sizes: [...node.querySelectorAll('.scroll-head span, .scroll-row span')].map(cell =>
              parseFloat(getComputedStyle(cell).fontSize) * bounds.width / node.clientWidth),
            title_gap: title && head ? (head.top - title.bottom) / bounds.height : 0,
            body_ratio: head ? (bounds.bottom - head.bottom) / bounds.height : 0 };
        });
        assert.equal(actual.headers.length, columns.length, '窄表必须仅渲染已声明的可读列');
        assert(actual.title_gap >= -.02 && actual.title_gap < .15, '表格不能沿用模板60px标题留白挤压实际数据');
        assert(actual.body_ratio >= .45, '表格须为真实数据保留至少45%的槽位高度');
        if (viewport.width >= 1000) assert(actual.visible_font_sizes.every(size => size >= 10),
          '1920等常规画布缩放后表格字号仍须可读，不能固定8px设计字号');
        // 用同一预览宽度下的等效槽高判断容量；不能将1920画布的90像素当成小画布90像素。
        const normalizedTableHeight = widget.h * 652 / Number(content.canvas?.width || report.width || 652);
        if (widget.data.length <= 4 && normalizedTableHeight >= 90) assert.equal(actual.visible_rows, widget.data.length,
          '4行以内的快照表应同时显示全部分组，不能无谓轮播把已返回分组藏在画面之外');
        const expected = widget.data.map(row => columns.map(key => String(row[key] ??
          (key === widget.config.snapshotBinding.labelField ? '未提供' : '—'))));
        assert.deepEqual(actual.rows.slice(0, expected.length), expected, '表格悬停信息保留原始完整字段，不编造部门名称或丢失金额精度');
        tableChecks.push({ widget_id: widget.id || widget.i, columns, ...actual });
      }
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(blocked, []); assert.deepEqual(failures, []);
      const screenshot = path.join(output, `saved-dashboard-${viewport.width}.png`);
      await page.screenshot({ path: screenshot, fullPage: true });
      results.push({ viewport, screenshot, chart_canvases: charts, chart_vectors: vectorCharts, verified_snapshot_charts: verifiedCharts,
        displayed_text: text, text_geometry: geometry, table_checks: tableChecks,
        local_fixture_api_calls: calls, external_calls: 0, business_query_calls: 0 });
      console.log(`PASS ${viewport.width}px Report快照/实际Vue组件可见，${verifiedCharts.length}个ECharts实际系列及类目与快照一致，无任何重查询：${screenshot}`);
    } finally { await browser.close(); }
  }
  fs.writeFileSync(path.join(output, "preview-check.json"), JSON.stringify({ snapshot: path.resolve(snapshotPath), results }, null, 2));
  return results;
}
if (require.main === module) main(process.argv[2], process.argv[3]).catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main, assertSnapshotChart, snapshotCharts };
