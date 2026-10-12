/*
 * 中文说明（追问建议的离线浏览器用例）：
 * 验证行为：从 app.js 截取 followupQuestions 等片段，验证追问解析（尾注/旧版段落）、过滤与渲染。
 * 运行方式：module.exports 形式导出，由聚合入口或调用方执行；需本机 Edge（可用 TEST5_EDGE_PATH 指定）。
 * 边界：不连后端、不调用模型、不连数据库；只断言前端解析与展示契约。
 */
"use strict";

// Isolated browser check: no backend, model provider or database requests.
module.exports = async function verifyFollowups() {
  const assert = require("node:assert/strict");
  const fs = require("node:fs");
  const path = require("node:path");
  const puppeteer = require("puppeteer-core");
  const root = path.resolve(__dirname, "../..");
  const source = fs.readFileSync(path.join(root, "apps/web/app.js"), "utf8");
  const functions = source.slice(source.indexOf("function followupQuestions("), source.indexOf("function businessPath("));
  const browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", headless: true, args: ["--no-sandbox", "--disable-gpu"] });
  try {
    const page = await browser.newPage();
    await page.setRequestInterception(true);
    page.on("request", request => request.abort());
    await page.setContent('<main style="max-width:720px;margin:24px auto"><div id="answer">根据分析，建议优先排查设备异常原因。</div><textarea id="prompt-textarea"></textarea></main>');
    await page.addStyleTag({ path: path.join(root, "apps/web/app.css") });
    await page.addScriptTag({ content: `
      const state = { streaming: false, openingChat: false };
      const $ = selector => document.querySelector(selector);
      let sent = [];
      let finishSend;
      function updateSendState() {}
      async function sendMessage() {
        sent.push($('#prompt-textarea').value);
        $('#prompt-textarea').value = '';
        state.streaming = true;
        await new Promise(resolve => { finishSend = resolve; });
        state.streaming = false;
      }
      ${functions}
      appendFollowupQuestions($('#answer'), '结果已返回。\\n<!-- followups: ["请导出两批资金申请的差异核对表", "请按部门汇总这两批资金申请", "请生成这两批资金支付分析图表"] -->', []);
    ` });
    assert.equal(await page.$$eval('.followup-question', nodes => nodes.length), 3);
    const generated = await page.evaluate(() => ({
      equipment: followupQuestions('结果。\n<!-- followups: ["请导出两批资金申请的差异核对表", "请按部门汇总这两批资金申请", "请生成这两批资金支付分析图表"] -->'),
      empty: followupQuestions("上述结果有哪些值得关注的变化？"),
      invalid: followupQuestions('结果。\n<!-- followups: [oops] -->'),
      repeat: followupQuestions('结果。\n<!-- followups: ["请按部门汇总资金申请", "请按部门汇总资金申请", 5] -->', [{role:"user", content:"请按部门汇总资金申请"}]),
      legacy: followupQuestions('**下一步建议**：我可以帮您①导出两批明细做差异核对表；②按部门汇总资金申请；③生成资金支付分析图表。需要哪个？'),
      technical: followupQuestions(['请联系管理员检查收款回款数据表的访问权限或连接状态', '帮我查看付款计划']),
    }));
    assert.deepEqual(generated.empty, []);
    assert.deepEqual(generated.invalid, []);
    assert.deepEqual(generated.repeat, []);
    assert.deepEqual(generated.technical, ['帮我查看付款计划']);
    assert.deepEqual(generated.legacy, ['导出两批明细做差异核对表', '按部门汇总资金申请', '生成资金支付分析图表']);
    assert.equal(new Set(generated.equipment).size, 3);
    await page.evaluate(() => { $('#prompt-textarea').value = '尚未发送的草稿'; });
    await page.click('.followup-question');
    await page.evaluate(() => document.querySelector('.followup-question').click());
    assert.deepEqual(await page.evaluate(() => sent), [generated.equipment[0]]);
    await page.evaluate(() => finishSend());
    await page.waitForFunction(() => $('#prompt-textarea').value === '尚未发送的草稿');
    await page.evaluate(() => { state.streaming = true; document.querySelector('.followup-question').click(); state.streaming = false; });
    assert.equal(await page.evaluate(() => sent.length), 1);
    await page.evaluate(() => {
      $('#answer').innerHTML = '';
      appendFollowupQuestions($('#answer'), '<!-- followups: ["<img src=x onerror=alert(1)>"] -->', []);
    });
    assert.equal(await page.$$eval('#answer img', nodes => nodes.length), 0);
    await page.setViewport({ width: 390, height: 844 });
    assert(await page.evaluate(() => [...document.querySelectorAll('.followup-question')].every(node => node.getBoundingClientRect().right <= innerWidth)));
    const output = path.join(root, '.implementation/ui-results/chat-followups');
    fs.mkdirSync(output, { recursive: true });
    await page.evaluate(() => { $('#answer').innerHTML = '根据分析，建议优先排查设备异常原因。'; appendFollowupQuestions($('#answer'), '结果已返回。\n<!-- followups: ["请导出两批资金申请的差异核对表", "请按部门汇总这两批资金申请", "请生成这两批资金支付分析图表"] -->', []); });
    await page.screenshot({ path: path.join(output, 'mobile.png') });
    console.log('PASS contextual suggestions, deduplication, direct send, double-click guard, streaming guard, draft preservation, text escaping and mobile layout (offline)');
  } finally {
    await browser.close();
  }
};
