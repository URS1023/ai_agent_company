"use strict";
// 仅使用本地静态资源和内存接口，覆盖长任务输出的真实 DOM 与移动端布局。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { ROOT, openFixture, geometry } = require("./dsh_chat_fixture");

async function main() {
  const output = path.join(ROOT, ".implementation/ui-results/chat-output-compact");
  fs.mkdirSync(output, { recursive: true });
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const { browser, page, failures, blocked } = await openFixture(viewport);
    try {
      await page.evaluate(() => {
        const f = window.__chatFixture, artifacts = [
          { id: "alerts", name: "设备预警信息表.xlsx", type: "spreadsheet" },
          { id: "stock", name: "备品备件库存表.xlsx", type: "spreadsheet" },
          { id: "analysis", name: "设备预警与备品备件分析报告.pptx", type: "presentation" },
          { id: "erp", name: "ERP系统介绍.pptx", type: "presentation" },
        ];
        const content = "已完成 4 份文件。预警和库存数据已分别整理，ERP 介绍使用通用业务流程。\n\n"
          + "## 交付文件\n\n| # | 文件 | 类型 | 下载地址 |\n| --- | --- | --- | --- |\n"
          + artifacts.map((a, i) => `| ${i + 1} | ${a.name} | ${i < 2 ? "Excel（当前数据快照）" : "PPT（5页正文）"} | [/api/artifacts/${a.id}/download](/api/artifacts/${a.id}/download) |`).join("\n")
          + "\n\n## 需要关注\n\n- 1 项严重预警需要现场核实。\n- 安全库存字段为空，暂不据此判断补货数量。\n\n"
          + "| 指标 | 数值 |\n| --- | --- |\n| 预警报告 | 50 |\n| 库存记录 | 4 |";
        const parts = [{ type: "reasoning", text: "核对预警与库存数据后生成交付文件。" }, { type: "text", text: content }];
        for (let i = 0; i < 28; i++) parts.push(
          { type: "tool_call", id: "read-" + i, name: "read_tool_result", status: "success" },
          { type: "tool_result", id: "read-" + i, tool: "read_tool_result", result: { status: "success", content: [{ type: "text", text: "原始查询回执，第 " + (i + 1) + " 页" }] } },
        );
        parts.push({ type: "tool_call", id: "rename", name: "rename_file", status: "failed" },
          { type: "tool_result", id: "rename", tool: "rename_file", result: { status: "failed", error_summary: "文件不存在，已保留原始导出文件" } },
          ...artifacts.map(artifact => ({ type: "artifact", artifact })));
        f.detail.messages = [{ id: "u", role: "user", content: "查看设备预警和备品备件，生成两份 Excel 和两份 PPT" },
          { id: "a", role: "assistant", run_id: "run", content, parts, timing: { started_at: "2026-09-26T09:00:00Z", completed_at: "2026-09-26T09:05:33Z", duration_ms: 333000 } }];
        f.detail.last_run = { id: "run", status: "completed" };
        f.emit("session.snapshot", f.detail);
      });
      await page.waitForSelector("[data-dsh-process]");
      const initial = await page.evaluate(() => ({
        panels: document.querySelectorAll("[data-dsh-process]").length,
        open: document.querySelector("[data-dsh-process]").open,
        summary: document.querySelector("[data-dsh-process] > summary").innerText,
        receipts: document.querySelectorAll("[data-dsh-tool-result]").length,
        chips: document.querySelectorAll("#messages .tool-chip").length,
        cards: document.querySelectorAll("#messages .artifact-card").length,
        tables: document.querySelectorAll("#messages .markdown-body table").length,
        text: document.querySelector("#messages").innerText,
      }));
      assert.equal(initial.panels, 1); assert.equal(initial.open, false);
      assert.match(initial.summary, /用时 5 分 33 秒/); assert.match(initial.summary, /29 次调用/); assert.match(initial.summary, /1 次调用未完成/);
      assert.equal(initial.receipts, 29); assert.equal(initial.chips, 0); assert.equal(initial.cards, 4);
      assert.equal(initial.tables, 1, "业务表格保留，交付清单转换为单组文件卡");
      assert(!initial.text.includes("read_tool_result")); assert(!initial.text.includes("/api/artifacts/"));
      assert(initial.text.includes("当前数据快照")); assert(initial.text.includes("5页正文"));
      assert(!initial.text.includes("原始查询回执"));
      assert(!(await geometry(page)).overflow);
      await page.$eval("#scroll-root", node => { node.scrollTop = 0; });
      await page.screenshot({ path: path.join(output, `collapsed-${viewport.width}.png`), fullPage: false });
      await page.click("[data-dsh-process] > summary");
      assert.match(await page.$eval(".dsh-process-group", node => node.innerText), /读取查询结果.*28 次/s);
      await page.click(".dsh-process-group > summary");
      await page.click('[data-dsh-tool-result="read-0"] > summary');
      assert.match(await page.$eval('[data-dsh-tool-result="read-0"]', node => node.innerText), /原始查询回执，第 1 页/);
      await page.evaluate(() => window.__chatFixture.emit("task.progress", { message: "同步完成状态" }));
      await new Promise(resolve => setTimeout(resolve, 100));
      assert(await page.$eval("[data-dsh-process]", node => node.open));
      assert(await page.$eval('[data-dsh-tool-result="read-0"]', node => node.open));
      await page.$eval('[data-dsh-tool-result="rename"]', node => node.parentElement.querySelector("summary").click());
      await page.click('[data-dsh-tool-result="rename"] > summary');
      assert.match(await page.$eval('[data-dsh-tool-result="rename"]', node => node.innerText), /文件不存在/);
      await page.evaluate(() => {
        const f = window.__chatFixture;
        f.detail.messages.push({ id: "no-timing", role: "assistant", run_id: "no-time", content: "没有记录起止时间。", parts: [
          { type: "tool_call", id: "unknown-time", name: "skill", status: "success" },
          { type: "tool_result", id: "source-discovery", tool: "list_business_data_sources", result: { status: "success", data: {
            resultType: "resource_resolution", returnedCount: 1,
            sources: [{ id: "fixture-source", name: "离线库存", provider: "mysql", version: 1, metadataStatus: "ready" }],
          } } },
        ] });
        f.detail.projections.tasks = [{ id: "finished-task", title: "查询数据库内容", status: "completed" }];
        f.emit("session.snapshot", f.detail);
      });
      await page.waitForSelector('[data-dsh-process="no-timing"]');
      assert(!await page.$eval('[data-dsh-process="no-timing"] > summary', node => node.innerText.includes("用时")));
      assert.equal(await page.$$eval('[data-result-type="resource_resolution"], #dsh-session-results, .dsh-task-list, [data-dsh-task-continue]', nodes => nodes.length), 0);
      assert.equal(await page.$$eval('#dsh-session-deliveries', nodes => nodes.length), 0, "没有额外交付时不创建底部空区域");
      assert.equal(await page.evaluate(() => state.dataSources.some(source => source.id === "fixture-source")), true);
      assert.deepEqual(failures, []); assert.deepEqual(blocked, []);
      console.log(`PASS ${viewport.width}px：29 次工具调用折叠为一条过程、失败可见、4 个文件去重、真实耗时、业务表格与原始回执保留`);
    } finally { await browser.close(); }
  }
  console.log("PASS 网络/模型/业务数据库请求均为 0；截图：" + output);
}

/** 历史业务内容仅从显式传入的本机文件读取，不复制进仓库夹具。 */
async function renderActual(messagePath) {
  const message = JSON.parse(fs.readFileSync(path.resolve(messagePath), "utf8"));
  const output = path.join(ROOT, ".implementation/ui-results/chat-output-compact");
  fs.mkdirSync(output, { recursive: true });
  for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
    const { browser, page, failures, blocked } = await openFixture(viewport);
    try {
      await page.evaluate(message => {
        const f = window.__chatFixture;
        f.detail.messages = [{ id: "actual-user", role: "user", content: "帮我查看设备预警信息和仓库的备品备件，生成两份 Excel 表格和 PPT，再做一份 ERP 系统的介绍 PPT" }, message];
        f.emit("session.snapshot", f.detail);
      }, message);
      await page.waitForSelector("[data-dsh-process]");
      const result = await page.evaluate(() => ({
        panels: document.querySelectorAll("[data-dsh-process]").length,
        expanded: document.querySelector("[data-dsh-process]").open,
        summary: document.querySelector("[data-dsh-process] > summary").innerText,
        cards: document.querySelectorAll("#messages .artifact-card").length,
        chips: document.querySelectorAll("#messages .tool-chip").length,
        visibleInternalUrls: document.querySelector("#messages").innerText.includes("/api/artifacts/"),
        fileActions: document.querySelectorAll("[data-artifact-use]").length,
        visibleReceiptText: [...document.querySelectorAll("[data-dsh-tool-result]")].some(node => node.checkVisibility()),
      }));
      assert.equal(result.panels, 1); assert.equal(result.expanded, false); assert.equal(result.cards, 4);
      assert.equal(result.chips, 0); assert.equal(result.fileActions, 4); assert.equal(result.visibleInternalUrls, false);
      assert.equal(result.visibleReceiptText, false); assert.match(result.summary, /用时 5 分 33 秒/);
      assert.match(result.summary, /40 次调用/); assert(!(await geometry(page)).overflow);
      await page.$eval("#scroll-root", node => { node.scrollTop = 0; });
      await page.screenshot({ path: path.join(output, `actual-${viewport.width}.png`), fullPage: false });
      await page.click("[data-dsh-process] > summary");
      const readGroup = await page.$$eval(".dsh-process-group > summary", nodes => nodes.map(node => node.innerText).find(text => text.includes("读取查询结果")));
      assert.match(readGroup, /22 次/);
      await page.screenshot({ path: path.join(output, `actual-process-${viewport.width}.png`), fullPage: false });
      assert.deepEqual(failures, []); assert.deepEqual(blocked, []);
      console.log(`PASS 真实记录 ${viewport.width}px：40 次调用（22 次分页）仅一条折叠过程，4 个文件保留下载和继续使用，耗时 333 秒`);
    } finally { await browser.close(); }
  }
}
module.exports = { main, renderActual };
if (require.main === module) (async () => { await main(); if (process.argv[2]) await renderActual(process.argv[2]); })().catch(error => { console.error(error); process.exitCode = 1; });
