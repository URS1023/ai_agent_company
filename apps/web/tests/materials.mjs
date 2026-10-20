/** 只验证本轮隔离平台与静态素材；不连接原业务库、外部图床或模型。 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { mkdir, writeFile } from "node:fs/promises";
const origin = process.env.E2E_ORIGIN ?? "http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin))
  throw new Error("只允许本机隔离环境");
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
});
const context = await browser.newContext({
  viewport: { width: 1600, height: 1000 },
  reducedMotion: "reduce",
});
const page = await context.newPage();
const result = {
  checks: [],
  page_errors: [],
  external_requests: [],
  requests: 0,
};
page.setDefaultTimeout(15000);
page.on("pageerror", (error) => result.page_errors.push(error.message));
page.on("request", (request) => {
  const url = new URL(request.url());
  if (
    url.protocol.startsWith("http") &&
    !["localhost", "127.0.0.1"].includes(url.hostname)
  )
    result.external_requests.push(url.origin);
});
let csrf = "";
async function call(method, path, data) {
  if (++result.requests > 260) throw new Error("超过260次请求预算");
  const response = await context.request.fetch(`${origin}/api/v1${path}`, {
    method,
    headers: { "X-CSRF-Token": csrf },
    data,
    timeout: 15000,
  });
  assert.ok(response.ok(), `${method} ${path} ${response.status()}`);
  return response.json();
}
await mkdir("test-results", { recursive: true });
try {
  const me = await call("POST", "/auth/login", {
    username: process.env.E2E_USERNAME,
    password: process.env.E2E_PASSWORD,
  });
  csrf = me.csrf_token;
  const catalog = await call("GET", "/report-components/catalog");
  const material = catalog.items.filter((item) => item.group === "material");
  assert.equal(material.length, 205);
  assert.equal(material.filter((item) => item.renderer_supported).length, 205);
  const widgets = [];
  for (const [index, item] of material.entries()) {
    const widget = await call("POST", "/report-component-instances", {
      source_id: item.id,
      x: (index % 20) * 420,
      y: Math.floor(index / 20) * 480,
      order_num: index,
    });
    assert.equal(widget.component, item.compType);
    assert.equal(widget.source_component_id, item.id);
    assert.ok(widget.w > 0 && widget.h > 0);
    assert.ok(!widget.binding);
    assert.ok(
      !widget.config?.chartDataParsed?.length,
      "实例不能携带预览业务数据",
    );
    if (item.imageUrl) assert.equal(widget.config.imageUrl, item.imageUrl);
    if (widget.component === "JCarousel")
      widget.config.images = material
        .filter((x) => x.imageUrl)
        .slice(0, 2)
        .map((x) => x.imageUrl);
    widgets.push(widget);
  }
  assert.equal(new Set(widgets.map((widget) => widget.id)).size, 205);
  assert.equal(
    new Set(
      widgets
        .filter(
          (widget) => widget.component === "JImg" && widget.config.imageUrl,
        )
        .map((widget) => widget.config.imageUrl),
    ).size,
    177,
  );
  assert.equal(
    new Set(
      widgets
        .filter((widget) => widget.component === "JDragBorder")
        .map((widget) => widget.config.option.type),
    ).size,
    13,
  );
  assert.equal(
    new Set(
      widgets
        .filter((widget) => widget.component === "JDragDecoration")
        .map((widget) => widget.config.option.type),
    ).size,
    12,
  );
  result.checks.push("205源素材实例保留独立配置，无样例业务数据");
  const representatives = [
    "JDragBorder",
    "JDragDecoration",
    "JImg",
    "JCarousel",
    "JCustomIcon",
  ].map((type) => widgets.find((widget) => widget.component === type));
  const arranged = [
    ...representatives,
    ...widgets.filter((widget) => !representatives.includes(widget)),
  ];
  const chunks = [arranged.slice(0, 180), arranged.slice(180)];
  let report,
    rendered = 0,
    decoded = 0;
  for (const [index, part] of chunks.entries()) {
    const created = await call("POST", "/reports", {
      name: `素材验收${Date.now()}-${index}`,
      layout: {
        canvas: { width: 10000, height: 6000, backgroundColor: "#07182d" },
        widgets: part,
      },
    });
    await page.goto(`${origin}/reports`);
    await page
      .locator(".el-table__row")
      .filter({ hasText: created.name })
      .getByRole("button", { name: "设计", exact: true })
      .click();
    await page.locator(".screen-widget").first().waitFor();
    assert.equal(await page.locator(".screen-widget").count(), part.length);
    const expectedImages = part.filter(
      (widget) => widget.component === "JImg" && widget.config.imageUrl,
    ).length;
    await page.waitForFunction(
      (count) =>
        [...document.querySelectorAll('[data-component="JImg"] img')].length ===
          count &&
        [...document.querySelectorAll('[data-component="JImg"] img')].every(
          (image) => image.complete && image.naturalWidth > 0,
        ),
      expectedImages,
    );
    rendered += part.length;
    decoded += expectedImages;
    assert.equal(
      await page.locator('[data-component="JDragBorder"] svg').count(),
      part.filter((widget) => widget.component === "JDragBorder").length,
    );
    assert.equal(
      await page
        .locator('[data-component="JDragDecoration"] .jimu-decoration')
        .count(),
      part.filter((widget) => widget.component === "JDragDecoration").length,
    );
    if (index === 0) {
      assert.equal(
        await page
          .locator('[data-component="JCustomIcon"] .custom-material-icon')
          .count(),
        1,
      );
      await page
        .locator('[data-component="JCarousel"] .next')
        .click({ force: true });
      assert.ok(
        await page
          .locator('[data-component="JCarousel"] img')
          .getAttribute("src"),
      );
    }
    report = created;
  }
  assert.equal(rendered, 205);
  assert.equal(decoded, 177);
  result.checks.push(
    "205素材分两张合法画布渲染，177图片全部解码、25边框装饰及真实轮播显示",
  );
  const currentCount = chunks[1].length;
  await page.getByRole("button", { name: "全部组件", exact: true }).click();
  const sample = material.find((item) => item.compType === "JDragBorder");
  await page
    .locator(".catalog-items button")
    .filter({ has: page.getByText(sample.name, { exact: true }) })
    .click();
  await page.locator(".el-dialog:visible").waitFor({ state: "hidden" });
  assert.equal(await page.locator(".screen-widget").count(), currentCount + 1);
  await page.getByRole("button", { name: "复制", exact: true }).click();
  assert.equal(await page.locator(".screen-widget").count(), currentCount + 2);
  await page.getByRole("button", { name: "撤销", exact: true }).click();
  assert.equal(await page.locator(".screen-widget").count(), currentCount + 1);
  const saving = page.waitForResponse(
    (response) =>
      response.url().endsWith(`/reports/${report.id}`) &&
      response.request().method() === "PATCH",
  );
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await saving;
  const saved = await call("GET", `/reports/${report.id}`);
  assert.equal(saved.layout.widgets.length, currentCount + 1);
  result.checks.push("目录选用真实工厂、复制撤销和保存保留全部素材");
  await page.screenshot({ path: "test-results/material-canvas.png" });
  await page.getByRole("button", { name: "历史版本", exact: true }).click();
  await page.locator(".el-dialog:visible .el-table__row").first().waitFor();
  assert.ok(
    (await page.locator(".el-dialog:visible .el-table__row").count()) >= 1,
  );
  result.checks.push("历史版本从服务端读取");
  assert.equal(result.page_errors.length, 0);
  assert.equal(result.external_requests.length, 0);
  result.passed = true;
} catch (error) {
  result.passed = false;
  result.error = error.message;
  await page
    .screenshot({ path: "test-results/material-failure.png" })
    .catch(() => {});
} finally {
  await writeFile(
    "test-results/materials.json",
    JSON.stringify(result, null, 2),
  );
  console.log(JSON.stringify(result, null, 2));
  await browser.close();
}
if (!result.passed) process.exitCode = 1;
