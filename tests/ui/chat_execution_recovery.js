/*
 * 中文说明（停止生成的浏览器侧验证，导出 helper 而非独立用例）：
 * 用途：verifyStopWithPage(page) 从 apps/web/app.js 截取真实的 stopGeneration 片段注入空白页，
 *   验证停止按钮在「正在停止/已结束/无法确认」三种返回下的提示文案与 abort 行为。
 * 运行方式：由调用方（其它 UI 用例）传入已启动的 puppeteer page；本文件自身不启动浏览器。
 * 约束：片段定位依赖 app.js 中 "async function stopGeneration()" 到 "async function readChatEvents"
 *   之间的代码，改动函数顺序会破坏截取（已用 assert 保护）。
 */
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

exports.verifyStopWithPage = async function(page) {
  const source = fs.readFileSync(path.resolve("apps/web/app.js"), "utf8");
  const start = source.indexOf("async function stopGeneration()");
  const end = source.indexOf("async function readChatEvents", start);
  assert(start > 0 && end > start);
  await page.setContent("<!doctype html><html><body></body></html>");
  await page.addScriptTag({ content: source.slice(start, end) });
  const outcomes = await page.evaluate(async () => {
    const result = [];
    for (const status of ["stopping", "failed", "completed", "cancelled", "running"]) {
      let aborted = false, message = "";
      window.state = { activeRunId: "fixture", abortController: { abort() { aborted = true; } } };
      window.API = { async post() { return { status }; } };
      window.toast = text => { message = text; };
      await stopGeneration();
      result.push({ status, aborted, message });
    }
    return result;
  });
  assert.equal(outcomes[0].aborted, false);
  assert(outcomes[0].message.includes("正在停止"));
  for (const outcome of outcomes.slice(1, 4)) {
    assert(outcome.aborted && outcome.message.includes("任务已结束"));
  }
  assert(!outcomes[4].aborted && outcomes[4].message.includes("无法确认"));
};

exports.verifyOperations = async function(page, base, fixturePath, output) {
  const fixture = JSON.parse(fs.readFileSync(fixturePath, "utf8"));
  await page.goto(base + "/operations", { waitUntil: "networkidle0" });
  await page.waitForSelector("[data-recover-runs]", { visible: true });
  await page.click("[data-recover-runs]");
  await page.waitForSelector(".operations-recovery-note", { visible: true });
  const text = await page.$eval(".dialog-body", node => node.textContent);
  assert(text.includes("已确认本机执行者退出") && text.includes("无法确认状态，已保留"));
  assert(text.includes(fixture.lost));
  assert(!text.includes(fixture.waiting) && !text.includes(fixture.unknown));
  await page.screenshot({ path: path.join(output, "operations-recovery.png"), fullPage: false });
  const [response] = await Promise.all([
    page.waitForResponse(response => response.url().endsWith("/api/v1/operations/runs/recover")
      && response.request().postData()?.includes('"dryRun":false')),
    page.waitForNavigation({ waitUntil: "networkidle0" }),
    page.click(".dialog-form button[type=submit]"),
  ]);
  // The UI reloads immediately after save. Read persisted state below rather
  // than requesting a CDP response body that navigation may have discarded.
  assert.equal(response.status(), 200);
  const states = await page.evaluate(async fixture => {
    const headers = { Authorization: "Bearer " + localStorage.getItem("agent_token"),
      "X-Workspace-Id": localStorage.getItem("agent_workspace_id") };
    const statuses = {};
    for (const [label, id] of Object.entries(fixture)) {
      const response = await fetch("/api/v1/operations/runs/" + id, { headers });
      if (!response.ok) throw new Error("Unable to verify fixture " + response.status);
      statuses[label] = (await response.json()).data.status;
    }
    return statuses;
  }, fixture);
  assert.deepEqual(states, { lost: "failed", waiting: "waiting", unknown: "running" });
  return states;
};
