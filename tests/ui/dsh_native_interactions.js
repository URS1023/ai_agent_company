"use strict";
// 连续 SSE 下验证原生追问和进度；静态页面及所有接口均为离线夹具。
const assert = require("node:assert/strict");
const { openFixture } = require("./dsh_chat_fixture");
const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function main() {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, failures, blocked } = await openFixture(viewport, { questions: true, steer: true });
    try {
      await page.type("#prompt-textarea", "生成财务数字大屏");
      await page.click("#composer-submit-button");
      await page.waitForFunction(() => document.querySelector('#composer-submit-button').getAttribute('aria-label') === '停止生成');
      await page.type('#prompt-textarea', '只看上个月的应收');
      assert.equal(await page.$eval('#composer-submit-button', node => node.getAttribute('aria-label')), '补充当前任务');
      await page.locator('#composer-submit-button').click();
      await page.waitForFunction(() => document.querySelectorAll('.user-bubble').length === 2);
      assert.equal(await page.evaluate(() => window.__chatFixture.requests), 1, '原生追加不创建另一轮任务');
      assert.equal(await page.$$eval('.user-bubble', nodes => nodes.filter(node => node.textContent.includes('生成财务数字大屏')).length), 1);
      await page.type('#prompt-textarea', '下一项生成销售表格');
      await page.locator('[data-dsh-submit-queue]').click();
      await page.waitForSelector('[data-queue-id="run-2"]');
      assert.equal(await page.evaluate(() => window.__chatFixture.requests), 2, '独立入口可以排队新任务');
      await page.evaluate(() => {
        const f = window.__chatFixture;
        f.emit("todo.updated", { run_id: "run-1", todos: [{ content: "确认统计范围", status: "in_progress" }, { content: "保存数字大屏", status: "pending" }] });
        f.emit("question.pending", { run_id: "run-1", question_id: "native-1", status: "pending", questions: [
          { id: "scope", question: "使用哪些部门？", multiSelect: true, options: [{ label: "销售", description: "销售部门业务" }, { label: "财务" }] },
          { id: "metric", question: "采用哪个指标？", options: [{ label: "已开票应收" }, { label: "合同应收" }] },
        ] });
      });
      await page.waitForSelector('#messages [data-dsh-question="native-1"]');
      assert.equal(await page.$$eval('#dsh-session-controls [data-dsh-question]', rows => rows.length), 0);
      assert.equal(await page.$$eval('#messages .dsh-todos', rows => rows.length), 1);
      await page.locator('[data-answer-id="scope"] input[value="销售"]').click();
      await page.locator('[data-answer-id="scope"] input[value="财务"]').click();
      await page.locator('[data-answer-id="metric"] input[value="合同应收"]').click();
      await page.type('[data-answer-id="metric"] [data-answer-text]', "以合同审批日期为准");
      await page.evaluate(() => {
        window.__questionNode = document.querySelector('[data-dsh-question="native-1"]');
        window.__answerTop = document.querySelector('[data-answer-id="metric"] [data-answer-text]').getBoundingClientRect().top;
        window.__questionTicks = 0;
        window.__questionTimer = setInterval(() => {
          const f = window.__chatFixture;
          f.emit("message.delta", { run_id: "run-1", delta: "等待统计范围确认。" });
          f.emit("todo.updated", { run_id: "run-1", todos: [{ content: "确认统计范围", status: ++window.__questionTicks % 2 ? "in_progress" : "pending" }, { content: "保存数字大屏", status: "pending" }] });
        }, 60);
      });
      await pause(450);
      assert(await page.evaluate(() => window.__questionNode === document.querySelector('[data-dsh-question="native-1"]')), "流式更新保留原生表单节点");
      const drift = await page.evaluate(() => document.querySelector('[data-answer-id="metric"] [data-answer-text]').getBoundingClientRect().top - window.__answerTop);
      assert(Math.abs(drift) < 3, `${viewport.width}px 填写追问时输入位置不能被上方流式内容挤动，实际漂移${drift}px`);
      assert.equal(await page.$eval('[data-answer-id="metric"] [data-answer-text]', node => node.value), "以合同审批日期为准");
      assert.equal(await page.$$eval('[data-answer-id="scope"] input:checked', nodes => nodes.length), 2);
      await page.locator('[data-dsh-question="native-1"] button[type="submit"]').click();
      try { await page.waitForFunction(() => !document.querySelector('[data-dsh-question="native-1"]'), { timeout: 3000 }); }
      catch (error) {
        const state = await page.evaluate(() => ({ calls: window.__chatFixture.calls.filter(call => call.path.includes('/questions/')),
          status: document.querySelector('[data-question-status]')?.textContent,
          checked: [...document.querySelectorAll('[data-dsh-question] input:checked')].map(input => input.value),
          button: document.querySelector('[data-dsh-question] button')?.getBoundingClientRect().toJSON(),
          composer: document.querySelector('.composer-wrap')?.getBoundingClientRect().toJSON(),
          scroll: document.querySelector('#scroll-root')?.scrollTop }));
        throw Error(JSON.stringify({ viewport: viewport.width, cause: error.message, state }));
      }
      const submitted = await page.evaluate(() => window.__chatFixture.calls.find(call => call.path.endsWith('/questions/native-1')));
      assert.deepEqual(submitted.body.answers, [{ id: "scope", selected: ["销售", "财务"] }, { id: "metric", selected: ["合同应收"], custom: "以合同审批日期为准" }]);
      await page.evaluate(() => {
        clearInterval(window.__questionTimer);
        const f = window.__chatFixture;
        f.emit("question.pending", { run_id: "run-1", question_id: "cancel-me", questions: [{ id: "continue", question: "继续吗？", options: [{ label: "继续" }] }] });
      });
      await page.waitForSelector('[data-dsh-question="cancel-me"]');
      await page.evaluate(() => window.__chatFixture.emit("run.cancelled", { run_id: "run-1", status: "cancelled" }));
      await page.waitForFunction(() => !document.querySelector('[data-dsh-question="cancel-me"]'));
      await page.evaluate(() => {
        const f = window.__chatFixture;
        const data = { resultType: "dashboard", reportId: "verified-report", title: "应收数字大屏", version: 1,
          queryIds: ["real-query"], widgetCount: 1, description: "该范围无记录；未补填金额。" };
        f.emit("tool.completed", { run_id: "run-2", call_id: "save-report", tool_name: "create_business_dashboard", result: { status: "success", data } });
        f.emit("delivery.created", { run_id: "run-2", delivery: { id: "dashboard:verified-report", report_id: "verified-report", type: "dashboard", status: "ready", business_result: data } });
      });
      await page.waitForSelector('[data-report-id="verified-report"]');
      assert.equal(await page.$$eval('[data-report-id="verified-report"]', rows => rows.length), 1, "工具结果和验收交付仅显示一张大屏卡片");
      assert.equal(await page.$$eval('#dsh-session-deliveries', rows => rows.length), 0, "大屏不伪装成底部下载附件");
      assert.match(await page.$eval('[data-report-id="verified-report"]', node => node.textContent), /该范围无记录/);
      await page.evaluate(() => window.__chatFixture.emit("delivery.updated", { run_id: "run-2", delivery: {
        id: "dashboard:verified-report", report_id: "verified-report", type: "dashboard", status: "invalid", validation_error: "快照失效" } }));
      await page.waitForFunction(() => !document.querySelector('[data-report-id="verified-report"]'));
      // 只有交付事件的大屏也必须保留可打开入口，不能因缺少业务结果卡而静默消失。
      await page.evaluate(() => window.__chatFixture.emit("delivery.created", { run_id: "run-3", delivery: {
        id: "dashboard:delivery-only", report_id: "delivery-only-report", type: "dashboard", status: "ready" } }));
      await page.waitForSelector('#dsh-session-deliveries a');
      assert.match(await page.$eval('#dsh-session-deliveries', node => node.textContent), /数字大屏/);
      assert.match(await page.$eval('#dsh-session-deliveries a', node => node.textContent), /打开大屏/);
      assert.equal(await page.$$eval('#dsh-session-results, .dsh-task-list', nodes => nodes.length), 0);
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      assert.deepEqual(failures, []); assert.deepEqual(blocked, []);
      console.log(`PASS ${viewport.width}px 原生多选追问、独立custom、连续SSE保留草稿、todo消息内折叠、取消关闭表单`);
    } finally { await browser.close(); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
