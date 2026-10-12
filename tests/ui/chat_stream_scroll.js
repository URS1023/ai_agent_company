"use strict";
// 真实页面和连续 SSE 内存夹具：运行中用鼠标滚轮阅读历史，不连接服务、模型或业务库。
const assert = require("node:assert/strict");
const { openFixture } = require("./dsh_chat_fixture");

const pause = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
async function main() {
  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
    const { browser, page, failures, blocked } = await openFixture(viewport, {}, { routes: {
      "/analytics-app": { contentType: "text/html", body: '<!doctype html><html><style>html,body{margin:0;height:100%;overflow:hidden;background:#10233b;color:white}</style><body><p>已保存的大屏数据</p><script>parent.postMessage({type:"cloud-brain-chat-preview",status:"ready"},location.origin)</script></body></html>' },
    } });
    try {
      await page.evaluate(() => {
        const f = window.__chatFixture;
        f.detail.messages = Array.from({ length: 30 }, (_, i) => ({ id: "history-" + i, role: i % 2 ? "assistant" : "user",
          content: "历史消息 " + i + "：业务查询已核对，保留当前数据口径。\n".repeat(3), parts: [] }));
        f.detail.messages.push({ id: "stream", run_id: "live", role: "assistant", content: "正在处理。", parts: [{ type: "text", text: "正在处理。" }] });
        f.detail.last_run = { id: "live", status: "running" };
        f.detail.projections.tasks = [{ id: "old-task", title: "不再显示的历史任务", status: "completed" }];
        f.detail.projections.deliveries = ["delivery-one", "delivery-two"].map(id => ({ id, artifact_id: "same-file", name: "结果.xlsx", type: "spreadsheet", status: "completed" }));
        const legacy = document.createElement("section"); legacy.id = "dsh-session-results"; legacy.textContent = "旧版任务列表";
        document.querySelector("#messages").appendChild(legacy);
        f.emit("session.snapshot", f.detail);
      });
      await page.waitForFunction(() => document.querySelectorAll("#messages > .msg-row").length === 31);
      await pause(120);
      await page.evaluate(() => {
        const f = window.__chatFixture;
        window.__firstHistoryNode = document.querySelector("#messages > .msg-row");
        window.__deliveryNode = document.querySelector("[data-delivery-id]");
        window.__streamTicks = 0;
        window.__streamTimer = setInterval(() => {
          f.emit("message.delta", { run_id: "live", message_id: "stream", delta: "\n持续输出第 " + (++window.__streamTicks) + " 段。" });
          f.emit("task.progress", { run_id: "live", message: "正在检查第 " + window.__streamTicks + " 项" });
        }, 60);
      });
      const root = await page.$("#scroll-root"), rect = await root.boundingBox();
      await page.mouse.move(rect.x + rect.width / 2, rect.y + rect.height / 2);
      const before = await page.$eval("#scroll-root", node => node.scrollTop);
      await page.mouse.wheel({ deltaY: -550 });
      await pause(300);
      const after = await page.$eval("#scroll-root", node => ({ top: node.scrollTop, remaining: node.scrollHeight - node.scrollTop - node.clientHeight }));
      assert(after.top < before - 200 && after.remaining > 250, `${viewport.width}px 运行中滚轮必须能离开底部：${JSON.stringify({ before, after })}`);
      await pause(360);
      assert(Math.abs(await page.$eval("#scroll-root", node => node.scrollTop) - after.top) < 3, "持续 SSE 不改变历史阅读位置");
      assert(await page.evaluate(() => document.querySelector("#messages > .msg-row") === window.__firstHistoryNode), "流式更新保留未变化的历史节点");
      assert(await page.evaluate(() => document.querySelector("[data-delivery-id]") === window.__deliveryNode), "流式更新不重建未变化的交付附件");
      assert.equal(await page.$$eval("[data-delivery-id]", nodes => nodes.length), 1, "相同附件的多条投影只展示一次");
      assert.equal(await page.$$eval("#dsh-session-results, .dsh-task-list, [data-dsh-task-continue]", nodes => nodes.length), 0, "旧版任务 DOM 和历史任务不再堆叠");
      if (viewport.width < 500) {
        const touch = await page.createCDPSession();
        await touch.send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 1 });
        const touchBefore = await page.$eval("#scroll-root", node => node.scrollTop);
        const point = { x: Math.round(rect.x + rect.width / 2), y: Math.round(rect.y + rect.height / 2) };
        await touch.send("Input.dispatchTouchEvent", { type: "touchStart", touchPoints: [point] });
        for (let step = 1; step <= 9; step++) {
          await touch.send("Input.dispatchTouchEvent", { type: "touchMove", touchPoints: [{ ...point, y: point.y + step * 20 }] });
          await pause(25);
        }
        await touch.send("Input.dispatchTouchEvent", { type: "touchEnd", touchPoints: [] });
        // 真实触摸可能有惯性滚动；先等待手势自然停止，再检查SSE是否改变阅读位置。
        // 若仍被流式输出拖到底部，following或持续位移会使此有界等待失败。
        await page.evaluate(() => { window.__touchRest = { top: document.querySelector("#scroll-root").scrollTop, changedAt: performance.now() }; });
        await page.waitForFunction(() => {
          const node = document.querySelector("#scroll-root"), rest = window.__touchRest;
          if (Math.abs(node.scrollTop - rest.top) > .5) { rest.top = node.scrollTop; rest.changedAt = performance.now(); }
          return !node._chatScrolling?.following && performance.now() - rest.changedAt >= 180;
        }, { polling: "raf", timeout: 2000 });
        const touchAfter = await page.$eval("#scroll-root", node => node.scrollTop);
        assert(touchAfter < touchBefore - 80, "手机触摸上滑浏览历史时必须能滚动：" + JSON.stringify({ touchBefore, touchAfter, rect }));
        await pause(160);
        const touchSettled = await page.$eval("#scroll-root", node => node.scrollTop);
        assert(Math.abs(touchSettled - touchAfter) < 3, "手机触摸后继续接收 SSE 时保持阅读位置：" + JSON.stringify({ touchAfter, touchSettled }));
        await touch.detach();
      }
      await page.mouse.wheel({ deltaY: 100000 });
      await pause(360);
      await page.waitForFunction(() => { const node = document.querySelector("#scroll-root"); return node._chatScrolling?.following && node.scrollHeight - node.scrollTop - node.clientHeight < 5; }, { polling: "raf", timeout: 1500 });
      await page.mouse.wheel({ deltaY: -35 });
      await pause(200);
      const smallScroll = await page.$eval("#scroll-root", node => ({ top: node.scrollTop, following: node._chatScrolling?.following }));
      assert.equal(smallScroll.following, false, "小幅触控板上滑也应停止跟随");
      await pause(160);
      assert(Math.abs(await page.$eval("#scroll-root", node => node.scrollTop) - smallScroll.top) < 3, "底部阈值不能覆盖小幅上滑意图");
      await page.evaluate(() => clearInterval(window.__streamTimer));
      await pause(100);
      const copy = await page.evaluate(async () => {
        const original = copyChatText;
        let copied;
        copyChatText = async text => { copied = text; };
        document.querySelector('[data-chat-message="stream"] [data-msg-action="copy"]').click();
        await Promise.resolve();
        copyChatText = original;
        return { copied, expected: dshChat.controller.store.value.messages.find(message => message.id === "stream").content };
      });
      assert.equal(copy.copied, copy.expected, "持续输出后的复制按钮必须绑定完整最新正文");
      const retry = await page.evaluate(() => {
        const row = assistantRow([], "相同回答", "旧提问");
        document.querySelector("#messages").appendChild(row); rememberMessageMarkup(row);
        reconcileMessageChildren(row, assistantRow([], "相同回答", "修改后的提问"));
        const original = retryLastPrompt; let prompt;
        retryLastPrompt = value => { prompt = value; };
        row.querySelector('[data-msg-action="retry"]').click();
        retryLastPrompt = original; row.remove();
        return prompt;
      });
      assert.equal(retry, "修改后的提问", "回答相同而提问已编辑时，重试按钮也必须绑定新提问");
      await page.evaluate(() => {
        window.__chatFixture.emit("tool.completed", { run_id: "live", message_id: "stream", tool_call_id: "saved-dashboard", tool: "create_business_dashboard",
          result: { status: "success", data: { resultType: "dashboard", reportId: "fixture-preview", previewPath: "/report/view/fixture-preview", title: "已保存的大屏", version: 1 } } });
      });
      await page.waitForSelector("[data-dashboard-preview-key]");
      await page.evaluate(() => scrollToBottom());
      await page.waitForSelector('[data-preview-state="ready"] iframe');
      await page.evaluate(() => {
        window.__mountedPreview = document.querySelector('[data-preview-state="ready"] iframe');
        window.__previewReloads = 0;
        window.__mountedPreview.addEventListener("load", () => window.__previewReloads++);
        window.__mountedPreview.contentWindow.fixtureState = "保持大屏交互";
        window.__streamTimer = setInterval(() => window.__chatFixture.emit("message.delta", { run_id: "live", message_id: "stream", delta: "\n大屏后的补充说明。" }), 60);
      });
      await pause(150);
      const frameBounds = await page.$eval('[data-preview-state="ready"] iframe', frame => {
        const box = frame.getBoundingClientRect(), root = document.querySelector("#scroll-root").getBoundingClientRect();
        return { x: box.x + box.width / 2, y: (Math.max(box.top, root.top) + Math.min(box.bottom, root.bottom)) / 2 };
      });
      await page.mouse.move(frameBounds.x, frameBounds.y);
      await page.mouse.wheel({ deltaY: -35 });
      await pause(200);
      const iframeScroll = await page.$eval("#scroll-root", node => ({ top: node.scrollTop, following: node._chatScrolling.following }));
      assert.equal(iframeScroll.following, false, "大屏内部小幅滚轮也必须取消父聊天自动跟随");
      await pause(180);
      assert(Math.abs(await page.$eval("#scroll-root", node => node.scrollTop) - iframeScroll.top) < 3, "鼠标停在大屏内时连续 SSE 不拉回底部");
      assert.deepEqual(await page.evaluate(() => ({ same: document.querySelector('[data-preview-state="ready"] iframe') === window.__mountedPreview,
        state: window.__mountedPreview.contentWindow.fixtureState, reloads: window.__previewReloads })), { same: true, state: "保持大屏交互", reloads: 0 }, "真实预览模块在同消息继续输出时保留 iframe 与交互状态");
      await page.evaluate(() => clearInterval(window.__streamTimer));
      await pause(100);
      const preserved = await page.evaluate(async () => {
        const row = document.createElement("div");
        const markup = text => `<div class="msg-body"><div class="assistant-content">${text}</div><section class="business-result-card" data-result-type="dashboard"><header>${text}</header><div data-dashboard-preview-key="saved@1"></div></section><div class="message-actions">复制</div></div>`;
        row.innerHTML = markup("最初正文"); document.querySelector("#messages").appendChild(row);
        rememberMessageMarkup(row);
        const preview = row.querySelector("[data-dashboard-preview-key]"), frame = document.createElement("iframe");
        frame.srcdoc = "<p>离线保存的大屏</p>";
        const loaded = new Promise(resolve => frame.onload = resolve); preview.appendChild(frame); await loaded;
        frame.contentWindow.fixtureState = "交互状态";
        let reloads = 0; frame.onload = () => reloads++;
        for (let i = 0; i < 5; i++) {
          const fresh = document.createElement("div"); fresh.innerHTML = markup("后续正文 " + i);
          reconcileMessageChildren(row, fresh);
        }
        await new Promise(resolve => setTimeout(resolve, 80));
        return { sameContainer: row.querySelector("[data-dashboard-preview-key]") === preview, sameFrame: row.querySelector("iframe") === frame,
          state: frame.contentWindow.fixtureState, reloads, updated: row.textContent.includes("后续正文 4") };
      });
      assert.deepEqual(preserved, { sameContainer: true, sameFrame: true, state: "交互状态", reloads: 0, updated: true }, "同一消息的正文和大屏元信息变化时，预览 iframe 必须保持连接和交互状态");
      assert.deepEqual(failures, []); assert.deepEqual(blocked, []);
      console.log(`PASS ${viewport.width}px：连续 SSE 期间真实滚轮${viewport.width < 500 ? "及触摸" : ""}阅读历史、小幅上滑保持位置、返回底部跟随；历史及交付 DOM 保留、附件去重、旧任务清理、大屏 iframe 不重载、复制与重试绑定最新内容`);
    } finally { await browser.close(); }
  }
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
