/*
 * 中文说明（浏览器端测试辅助模块，非独立用例）：
 * 被其它 tests/ui/*.js 用例 require，提供 sleep、信封解包（envelopeData）、按句柄点击等小工具。
 * 运行方式：不单独执行；由调用方传入 puppeteer 的 page/elementHandle。
 * 约束：这里只做等待与断言辅助，不包含任何业务断言，也不访问后端接口。
 */
"use strict";

const assert = require("node:assert/strict");

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function envelopeData(body) {
  return body && Object.prototype.hasOwnProperty.call(body, "data") ? body.data : body;
}

async function clickHandle(handle) {
  assert(handle, "Expected a clickable element");
  await handle.evaluate(node => node.scrollIntoView({ block: "center", inline: "nearest" }));
  await handle.evaluate(node => node.click());
}

async function visibleByText(target, selector, label, exact = true) {
  for (const handle of await target.$$(selector)) {
    const matches = await handle.evaluate((node, expected, useExact) => {
      const shown = (node.textContent || "").trim();
      return node.getClientRects().length > 0 && !node.disabled
        && (useExact ? shown === expected : shown.includes(expected));
    }, label, exact);
    if (matches) return handle;
  }
  return null;
}

async function clickText(target, selector, label, exact = true) {
  const deadline = Date.now() + 30000;
  let handle = null;
  while (!handle && Date.now() < deadline) {
    try { handle = await visibleByText(target, selector, label, exact); }
    catch { /* A route transition can briefly detach the old control tree. */ }
    if (!handle) await sleep(50);
  }
  assert(handle, `Visible ${selector} not found: ${label}`);
  await clickHandle(handle);
  return handle;
}

async function fillElement(page, handle, value) {
  assert(handle, "Expected an editable element");
  await handle.evaluate(node => node.scrollIntoView({ block: "center", inline: "nearest" }));
  await handle.focus();
  await page.keyboard.down("Control");
  await page.keyboard.press("KeyA");
  await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  if (String(value)) await handle.type(String(value));
  await page.keyboard.press("Tab");
}

async function labeledControl(target, label, root = "body", selector = "input:not([type=file]),textarea,select") {
  const raw = await target.evaluateHandle((expected, rootSelector, controlSelector) => {
    const roots = [...document.querySelectorAll(rootSelector)].filter(node => node.getClientRects().length > 0);
    for (const current of roots.reverse()) {
      const labels = [...current.querySelectorAll("label")].filter(node => node.getClientRects().length > 0);
      const matching = labels.find(node => {
        const direct = [...node.childNodes]
          .filter(child => child.nodeType === Node.TEXT_NODE)
          .map(child => child.textContent || "")
          .join(" ")
          .replace(/\s+/g, " ")
          .trim();
        const full = (node.textContent || "").replace(/\s+/g, " ").trim();
        return direct === expected || direct.startsWith(expected) || full === expected || full.startsWith(expected);
      });
      const control = matching?.querySelector(controlSelector);
      if (control) return control;
    }
    return null;
  }, label, root, selector);
  const handle = raw.asElement();
  assert(handle, `Labeled control not found: ${label} in ${root}`);
  return handle;
}

async function fillLabeled(page, target, label, value, root = "body") {
  const handle = await labeledControl(target, label, root);
  if (await handle.evaluate(node => node.tagName === "SELECT")) {
    await handle.select(String(value));
  } else {
    await fillElement(page, handle, value);
  }
  return handle;
}

async function setCheckboxByText(target, label, checked, root = "body") {
  const raw = await target.evaluateHandle((expected, rootSelector) => {
    const roots = [...document.querySelectorAll(rootSelector)].filter(node => node.getClientRects().length > 0);
    for (const current of roots.reverse()) {
      const candidate = [...current.querySelectorAll("label")].find(node =>
        node.getClientRects().length > 0 && (node.textContent || "").replace(/\s+/g, " ").trim().includes(expected));
      const input = candidate?.querySelector('input[type="checkbox"],input[type="radio"]');
      if (input) return input;
    }
    return null;
  }, label, root);
  const input = raw.asElement();
  assert(input, `Checkbox not found: ${label}`);
  const current = await input.evaluate(node => node.checked);
  if (current !== checked) await clickHandle(input);
  assert.equal(await input.evaluate(node => node.checked), checked, label);
  return input;
}

async function waitUiResponse(page, predicate, action, timeout = 90000) {
  const pending = page.waitForResponse(response => {
    try {
      return predicate(response, new URL(response.url()));
    } catch {
      return false;
    }
  }, { timeout });
  // A failed click must not leave an unhandled response wait at browser close.
  pending.catch(() => {});
  await action();
  return pending;
}

async function confirmElementPlus(frame, label = "确定") {
  await frame.waitForSelector(".el-message-box", { visible: true, timeout: 30000 });
  await clickText(frame, ".el-message-box button", label);
}

async function visitBusiness(page, base, route, readySelector) {
  const response = await page.goto(base + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  if (await page.$("#analytics-app")) {
    await page.waitForSelector(readySelector, { visible: true, timeout: 90000 });
    return page;
  }
  const frameHandle = await page.waitForSelector("#business-module-frame", { visible: true, timeout: 90000 });
  const frame = await frameHandle.contentFrame();
  assert(frame, "Business module iframe is unavailable");
  await frame.waitForSelector(readySelector, { visible: true, timeout: 90000 });
  return frame;
}

async function login(page, base, password) {
  assert(password, "Supply TEST5_E2E_PASSWORD");
  const response = await page.goto(base, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200);
  await fillElement(page, await page.waitForSelector("#login-username", { visible: true }), "workspace-admin");
  await fillElement(page, await page.waitForSelector("#login-password", { visible: true }), password);
  await clickHandle(await page.$("#login-btn"));
  await page.waitForSelector(".enterprise-area-grid, .overview-intro", { visible: true, timeout: 90000 });
}

async function fillHttpField(page, frame, index, field) {
  const row = (await frame.$$(".http-field-row"))[index];
  assert(row, `HTTP output field row ${index + 1} is missing`);
  const values = [field.name, field.path, field.type || "auto", field.comment || ""];
  const controls = await row.$$("input:not([type=checkbox]),select");
  assert(controls.length >= 4, `HTTP output field row ${index + 1} is incomplete`);
  await fillElement(page, controls[0], values[0]);
  await fillElement(page, controls[1], values[1]);
  await controls[2].select(values[2]);
  await fillElement(page, controls[3], values[3]);
  if (field.required) {
    const required = (await row.$$('input[type="checkbox"]'))[0];
    if (!await required.evaluate(node => node.checked)) await clickHandle(required);
  }
  if (field.multiple) {
    const multiple = (await row.$$('input[type="checkbox"]'))[1];
    if (!await multiple.evaluate(node => node.checked)) await clickHandle(multiple);
  }
}

async function createHttpSourceThroughUi(page, base, input) {
  const frame = await visitBusiness(page, base, "/datasources?type=http", ".datasource-workspace");
  await clickText(frame, "button", "新建 HTTP 数据源");
  await frame.waitForSelector(".http-config-dialog .http-form", { visible: true, timeout: 30000 });
  await fillLabeled(page, frame, "数据源名称", input.name, ".http-config-dialog .http-form");
  await fillLabeled(page, frame, "请求方式", input.method || "GET", ".http-config-dialog .http-form");
  await fillLabeled(page, frame, "接口地址", input.url, ".http-config-dialog .http-form");
  await fillLabeled(page, frame, "用途说明", input.description || "浏览器终验真实 HTTP 数据源", ".http-config-dialog .http-form");
  await fillLabeled(page, frame, "请求头 · JSON 对象", JSON.stringify(input.headers || {}, null, 2), ".http-config-dialog .http-form");
  if ((input.method || "GET") === "POST") {
    const bodies = input.bodies || [{}];
    await setCheckboxByText(frame, "批量请求体", bodies.length > 1, ".http-config-dialog .http-form");
    await fillLabeled(page, frame, bodies.length > 1 ? "批量请求体 · JSON 数组" : "单条请求体 · JSON",
      JSON.stringify(bodies.length > 1 ? bodies : bodies[0], null, 2), ".http-config-dialog .http-form");
  }
  await fillLabeled(page, frame, "记录 JSONPath", input.recordPath || "$", ".http-config-dialog .http-form");
  if (input.fields?.length) {
    await setCheckboxByText(frame, "选择输出字段", true, ".http-config-dialog .http-form");
    for (let index = 0; index < input.fields.length; index += 1) {
      await clickText(frame, ".http-field-list > button", "添加字段");
      await fillHttpField(page, frame, index, input.fields[index]);
    }
  }
  await setCheckboxByText(frame, "启用数据源", input.enabled !== false, ".http-config-dialog .http-form");
  await setCheckboxByText(frame, "允许访问此本机 / 内网设备", Boolean(input.allowPrivate), ".http-config-dialog .http-form");
  await setCheckboxByText(frame, "确认仅查询数据，允许报表自动调用", Boolean(input.allowAutomatic), ".http-config-dialog .http-form");
  if (input.continueOnError) await setCheckboxByText(frame, "单条失败后继续其他请求", true, ".http-config-dialog .http-form");
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname === "/api/v1/collection/sources" && candidate.request().method() === "POST",
    () => clickText(frame, ".http-form-footer button", "创建数据源"));
  assert.equal(response.status(), 201, await response.text());
  const source = envelopeData(await response.json());
  assert(source?.id, "HTTP source creation did not return an id");
  await frame.waitForFunction(name => [...document.querySelectorAll(".http-source-table tbody tr")]
    .some(row => row.textContent.includes(name)), { timeout: 90000 }, input.name);
  return { frame, source };
}

async function removeHttpSourceThroughUi(page, base, name) {
  const frame = await visitBusiness(page, base, "/datasources?type=http", ".datasource-workspace");
  const raw = await frame.evaluateHandle(expected => [...document.querySelectorAll(".http-source-table tbody tr")]
    .find(row => row.textContent.includes(expected)) || null, name);
  const row = raw.asElement();
  if (!row) return false;
  const remove = await visibleByText(row, "button", "移除");
  assert(remove, `HTTP source remove button is missing for ${name}`);
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname.startsWith("/api/v1/collection/sources/") && candidate.request().method() === "DELETE",
    async () => { await clickHandle(remove); await confirmElementPlus(frame, "确认移除"); });
  assert.equal(response.status(), 200, await response.text());
  await frame.waitForFunction(expected => ![...document.querySelectorAll(".http-source-table tbody tr")]
    .some(item => item.textContent.includes(expected)), { timeout: 90000 }, name);
  return true;
}

async function removeWorkflowThroughUi(page, base, name) {
  const frame = await visitBusiness(page, base, "/workflows", ".workflow-ui");
  const filter = await labeledControl(frame, "流程名称", ".wf-list-filter");
  await fillElement(page, filter, name);
  await clickText(frame, "button", "查询");
  await frame.waitForFunction(() => !document.querySelector(".wf-table")?.textContent.includes("正在加载工作流"),
    { timeout: 30000 });
  await sleep(150);
  const raw = await frame.evaluateHandle(expected => [...document.querySelectorAll(".wf-table tbody tr")]
    .find(row => row.textContent.includes(expected)) || null, name);
  const row = raw.asElement();
  if (!row) return false;
  const remove = await visibleByText(row, "button", "移除");
  assert(remove, `Workflow remove button is missing for ${name}`);
  const response = await waitUiResponse(page,
    (candidate, url) => url.pathname.startsWith("/api/v1/workflows/") && candidate.request().method() === "DELETE",
    async () => { await clickHandle(remove); await confirmElementPlus(frame, "确认移除"); });
  assert.equal(response.status(), 200, await response.text());
  return true;
}

module.exports = {
  clickHandle,
  clickText,
  createHttpSourceThroughUi,
  envelopeData,
  fillElement,
  fillLabeled,
  labeledControl,
  login,
  removeHttpSourceThroughUi,
  removeWorkflowThroughUi,
  setCheckboxByText,
  sleep,
  visibleByText,
  visitBusiness,
  waitUiResponse,
  confirmElementPlus,
};
