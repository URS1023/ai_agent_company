"use strict";
// 整页操作全部使用内存API，不访问业务服务、数据库或模型。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { ROOT, openFixture, geometry } = require("./dsh_chat_fixture");

async function main() {
  const baseline = require("./fixtures/dsh-chat-shell-baseline.json").viewports;
  const output = path.join(ROOT, ".implementation/ui-results/dsh-chat-after"); fs.mkdirSync(output, { recursive: true });
  const report = { baseline_commit: "a9e8a9c", command: "node tests/ui/dsh_chat_browser.js", isolation: "所有API为内存夹具，静态资源来自本地；真实服务/数据库/模型请求均为0", viewports: [] };
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, failures, blocked } = await openFixture(viewport, { questions: true });
    try {
      await page.waitForFunction(() => window.__chatFixture.streams.length === 1);
      await page.waitForFunction(() => !document.querySelector("#dsh-session-controls") || document.querySelector("#dsh-session-controls").hidden);
      const current = await geometry(page), expected = baseline.find(item => item.width === viewport.width);
      for (const selector of ["#sidebar", ".composer-wrap", "#composer-form", "#prompt-textarea", "#scroll-root", "#messages"]) {
        for (const metric of ["x", "y", "width", "height"]) assert(Math.abs(current[selector][metric] - expected[selector][metric]) < 1.1, `${viewport.width} ${selector}.${metric}: ${current[selector][metric]} != ${expected[selector][metric]}`);
      }
      assert(!current.overflow);
      report.viewports.push({ width: viewport.width, height: viewport.height, baseline: expected, after: current });
      await page.screenshot({ path: path.join(output, `empty-${viewport.width}.png`) });
      await page.click("#add-menu-btn"); await page.click('[data-act="web"]');
      assert.match(await page.$eval("#toast", node => node.textContent), /未配置搜索/);
      assert(await page.$eval("#active-mode-btn", node => node.classList.contains("hidden")));
      await page.evaluate(() => [...document.querySelectorAll("#menu-panel button")].find(button => button.textContent === "技能与业务能力").click());
      await page.click('[data-skill-id="business-analysis"]');
      assert.match(await page.$eval("#composer-attachments", node => node.textContent), /业务分析/);
      await page.keyboard.press("Escape");
      await page.type("#prompt-textarea", "先分析本月业务"); await page.click("#composer-submit-button");
      await page.waitForFunction(() => document.querySelector("#composer-submit-button").getAttribute("aria-label") === "停止生成");
      await page.type("#prompt-textarea", "之后制作PPT");
      assert.equal(await page.$eval("#composer-submit-button", node => node.getAttribute("aria-label")), "加入待执行队列");
      await page.click("#composer-submit-button");
      await page.waitForSelector('[data-queue-id="run-2"]');
      await page.click('[data-queue-id="run-2"] [data-queue-edit]');
      await page.$eval('[data-queue-id="run-2"] textarea', node => { node.value = "之后制作Word"; });
      await page.click('[data-queue-id="run-2"] [data-queue-edit]');
      await page.waitForFunction(() => document.querySelector('[data-queue-id="run-2"]')?.textContent.includes("制作Word"));
      await page.waitForFunction(() => [...document.querySelectorAll(".user-bubble")].some(node => node.textContent === "之后制作Word"));
      assert.equal(await page.$$eval(".user-bubble", nodes => nodes.filter(node => node.textContent.includes("制作PPT")).length), 0);
      assert.equal(await page.$$eval("#dsh-session-results, .dsh-task-list, [data-dsh-task-continue]", nodes => nodes.length), 0);
      assert.match(await page.evaluate(() => window.__chatFixture.detail.projections.tasks.find(task => task.id === "task-queue").title), /制作Word/);
      assert.equal(await page.evaluate(() => window.__chatFixture.detail.projections.tasks.find(task => task.id === "task-queue").required_deliveries[0].type), "document");
      await page.evaluate(() => {
        const f = window.__chatFixture;
        f.emit("message.replace", { run_id: "run-1", message_id: "assistant-1", content: "", attempt_id: "one" });
        f.emit("reasoning.delta", { run_id: "run-1", message_id: "assistant-1", delta: "先核对实际来源。" });
        f.emit("tool.started", { run_id: "run-1", tool_call_id: "read", tool: "read_file" });
        f.emit("tool.completed", { run_id: "run-1", tool_call_id: "read", tool: "read_file", status: "success" });
        f.emit("tool.completed", { run_id: "run-1", call_id: "skill", tool_name: "read_skill", status: "success", result: { content: [{ type: "text", text: "已读取业务分析技能" }] } });
        f.emit("message.delta", { run_id: "run-1", message_id: "assistant-1", delta: "已完成分析，报告尚缺。" });
        f.detail.projections.tasks = [{ id: "task-1", title: "业务分析与报告", status: "partial", missing_deliveries: [{ type: "presentation", count: 1 }] }];
        f.detail.projections.deliveries = [{ id: "artifact-1", artifact_id: "artifact-1", name: "分析结果.docx", type: "document", status: "completed", download_url: "/api/artifacts/artifact-1/download" }];
        f.emit("task.updated", { task: f.detail.projections.tasks[0] });
        f.emit("delivery.created", { delivery: f.detail.projections.deliveries[0] });
        f.emit("run.completed", { run_id: "run-1", status: "partial" }); f.emit("done", { run_id: "run-1", status: "partial" });
      });
      await page.waitForSelector("[data-dsh-reasoning]");
      assert.match(await page.$eval('[data-dsh-tool-result="skill"]', node => node.textContent), /已读取业务分析技能/);
      assert.equal(await page.$$eval("#dsh-session-results, .dsh-task-list, [data-dsh-task-continue]", nodes => nodes.length), 0);
      assert.equal(await page.evaluate(() => window.__chatFixture.detail.projections.tasks[0].status), "partial");
      assert.match(await page.$eval("#dsh-session-deliveries", node => node.textContent), /分析结果.docx/);
      assert.equal(await page.$$eval("[data-delivery-id]", nodes => nodes.length), 1);
      await page.click('[data-dsh-artifact-use="artifact-1"]');
      await page.waitForSelector('[data-k="file-file-artifact-1"]');
      await page.click('[data-k="file-file-artifact-1"]');
      assert.equal(await page.evaluate(() => window.__chatFixture.streams.length), 1);
      await page.click('[data-queue-id="run-2"] [data-queue-remove]');
      await page.waitForFunction(() => !document.querySelector('[data-queue-id="run-2"]'));
      await page.evaluate(() => window.__chatFixture.emit("question.requested", { question: { id: "question-1", question: "确认报告范围", options: ["本月", "本年"] } }));
      await page.waitForSelector('[data-dsh-question="question-1"]');
      await page.click('[data-dsh-question="question-1"] input[value="本月"]');
      await page.click('[data-dsh-question="question-1"] button[type="submit"]');
      await page.waitForFunction(() => !document.querySelector('[data-dsh-question="question-1"]'));
      const sends = await page.evaluate(() => window.__chatFixture.calls.filter(call => call.path.endsWith("/send")));
      assert.equal(sends.length, 2); assert.equal(sends[0].body.mode, "standard"); assert.equal(sends[0].body.delivery_mode, "queue");
      assert.deepEqual(sends[0].body.skill_ids, ["business-analysis"]);
      await page.evaluate(() => {
        const f = window.__chatFixture;
        f.detail.messages.push({ id: "assistant-1", run_id: "run-1", role: "assistant", content: "已完成分析，报告尚缺。", parts: [{ type: "reasoning", text: "先核对实际来源。" }, { type: "text", text: "已完成分析，报告尚缺。" }, { type: "artifact", artifact: f.detail.projections.deliveries[0] }] });
        f.detail.last_run = { id: "run-1", status: "partial" }; f.detail.projections.queue = [];
        f.disconnect();
      });
      await page.waitForFunction(() => window.__chatFixture.streams.length === 1 && document.querySelectorAll("[data-dsh-reasoning]").length === 1);
      assert.equal(await page.evaluate(() => window.__chatFixture.requests), 2);
      assert.equal(await page.$$eval("#dsh-session-results, .dsh-task-list, [data-dsh-task-continue]", nodes => nodes.length), 0);
      assert.equal(await page.evaluate(() => window.__chatFixture.detail.last_run.status), "partial");
      assert.equal(await page.$$eval('[data-artifact-use="artifact-1"], [data-dsh-artifact-use="artifact-1"]', nodes => nodes.length), 1);
      assert(!(await geometry(page)).overflow);
      await page.reload({ waitUntil: "networkidle0" });
      await page.waitForSelector("[data-dsh-reasoning]");
      assert.equal(await page.$$eval("[data-dsh-reasoning]", nodes => nodes.length), 1);
      assert.equal(await page.evaluate(() => window.__chatFixture.requests), 2);
      assert.equal(await page.$$eval(".user-bubble", nodes => nodes.filter(node => node.textContent === "之后制作Word").length), 1);
      assert.equal(await page.$$eval("#dsh-session-results, .dsh-task-list, [data-dsh-task-continue]", nodes => nodes.length), 0);
      assert.equal(await page.evaluate(() => window.__chatFixture.detail.last_run.status), "partial");
      await page.screenshot({ path: path.join(output, `conversation-${viewport.width}.png`) });
      await page.click('[data-artifact-use="artifact-1"], [data-dsh-artifact-use="artifact-1"]');
      await page.waitForSelector('[data-k="file-file-artifact-1"]');
      assert.match(await page.$eval("#composer-attachments", node => node.textContent), /分析结果.docx/);
      await page.type("#prompt-textarea", "修改报告结论，保留其余内容");
      await page.click("#composer-submit-button");
      await page.waitForFunction(() => window.__chatFixture.requests === 3);
      const followup = await page.evaluate(() => window.__chatFixture.calls.filter(call => call.path.endsWith("/send")).at(-1).body);
      assert.deepEqual(followup.file_ids, ["file-artifact-1"]);
      await page.waitForFunction(() => document.querySelector("#composer-submit-button").getAttribute("aria-label") === "停止生成");
      // 停止文案先于异步进度面板重排出现；等待按钮几何稳定后点击，避免落在旧坐标。
      await page.locator("#composer-submit-button").click();
      await page.waitForFunction(() => window.__chatFixture.calls.some(call => call.path.endsWith("/runs/run-3/cancel")));
      await page.evaluate(() => window.__chatFixture.emit("run.cancelled", { run_id: "run-3", status: "cancelled" }));
      await page.waitForFunction(() => document.querySelector("#composer-submit-button").getAttribute("aria-label") === "发送");
      assert.deepEqual(failures, []); assert.deepEqual(blocked, []);
      const probe = await browser.newPage();
      await probe.setRequestInterception(true); probe.on("request", request => request.abort());
      await probe.setViewport(viewport);
      await probe.setContent(require("./chat_scroll_layout").fixtureHtml());
      assert.equal((await probe.evaluate(() => window.runChatLayoutChecks())).checks.length, 19);
      await require("./chat_execution_recovery").verifyStopWithPage(probe);
      await require("./chat_business_questions").verifyWithPage(probe);
      await probe.close();
      console.log(`PASS ${viewport.width}px：外壳几何不变，能力/排队/编辑/移除/思考/部分交付/回答/断线恢复闭环`);
    } catch (error) {
      await page.screenshot({ path: path.join(output, `failure-${viewport.width}.png`) });
      console.error(JSON.stringify({ failures, text: await page.$eval("body", node => node.innerText), composer: await page.evaluate(() => ({
        prompt: document.querySelector("#prompt-textarea").value, label: document.querySelector("#composer-submit-button").getAttribute("aria-label"),
        files: state.fileIds, run: dshChat.controller.store.activeRun(), lastSend: window.__chatFixture.calls.filter(call => call.path.endsWith("/send")).at(-1),
      })) }));
      throw error;
    } finally { await browser.close(); }
  }
  fs.writeFileSync(path.join(ROOT, "docs/implementation/dsh-chat-layout-verification.json"), JSON.stringify(report, null, 2) + "\n");
  console.log("PASS DSH 整页离线浏览器验收；未启动服务，全部API由内存夹具回答");
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
