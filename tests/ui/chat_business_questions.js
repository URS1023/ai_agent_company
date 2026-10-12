/*
 * 中文说明（业务澄清文字的离线浏览器用例）：
 * 验证行为：用生产渲染代码（appendBusinessQuestion/assistantRow/handleFrame）在假数据下展示普通澄清文字，
 *   页面不再生成选择按钮、独立输入框或自动发送控件。
 * 运行方式：直接 node 运行，fixtureHtml() 从 apps/web/app.js 截取真实函数片段拼装页面；
 *   不需要后端、模型或数据库（发送方是假的），只需本机 Edge。
 * 边界：完全离线；只断言渲染与交互契约，不验证后端存储。
 */
"use strict";
// Uses production rendering/SSE code with a fake sender; no model or database requests.
const fs = require("node:fs"), path = require("node:path");
const root = path.resolve(__dirname, "../..");

function fixtureHtml() {
  const source = fs.readFileSync(path.join(root, "apps/web/app.js"), "utf8");
  const section = (start, end) => source.slice(source.indexOf(start), source.indexOf(end));
  const code = section("function appendBusinessQuestion(", "function businessPath(")
    + section("function assistantRow(", "async function useArtifactAsInput(")
    + section("function handleFrame(", "function customerChatError(");
  const css = fs.readFileSync(path.join(root, "apps/web/app.css"), "utf8");
  return `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>业务澄清文字验收</title><style>${css}\nbody{overflow:auto}main{max-width:860px;margin:28px auto;padding:16px}nav{display:flex;flex-wrap:wrap;gap:8px}nav button{padding:8px}#prompt-textarea{width:100%;margin-top:18px}#sent{white-space:pre-wrap;overflow-wrap:anywhere}</style>
    <main><h1>业务澄清文字验收</h1><nav><button id="stream">实时提问</button><button id="saved">刷新后提问</button><button id="history">历史已回答</button><button id="generic">旧版泛化追问</button><button id="error">真实错误</button><button id="custom">自由回答</button><button id="mobile">窄屏布局</button></nav>
    <div id="answer"></div><label>未发送的草稿<textarea id="prompt-textarea">保留我的草稿</textarea></label><output id="sent" aria-label="已发送的回答"></output></main>
    <script>const $=s=>document.querySelector(s); const state={streaming:false,openingChat:false,current:{messages:[]}};
    const IC={copy:'复制',thumb:'赞',retry:'重发'};const esc=s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');
    const toolLabel=x=>'查询业务数据';const customerChatError=x=>x; const mdRender=esc;
    const assistantArtifacts=()=>[];const businessResultMarkup=()=>'';const bindArtifactImages=()=>{};
    const updateSendState=()=>{};const scrollToBottom=()=>{};const flushStreamMarkdown=()=>{};
    const appendFollowupQuestions=()=>{};let sends=[];
    async function sendMessage(){sends.push($('#prompt-textarea').value);$('#prompt-textarea').value='';state.streaming=true;$('#sent').textContent=sends.length+' 次发送：'+sends.join('\\n');await new Promise(r=>setTimeout(r,250));state.streaming=false;}
    ${code}
    const question={type:'business_question',question:'你是想查看生产过程中的用水数据，还是其他类型的水资源消耗记录？',options:['生产过程中的用水数据','其他类型的水资源消耗记录']};
    function render(active=true){$('#answer').replaceChildren(assistantRow([{type:'tool_result',result:{status:'waiting_input'}},question,{type:'text',text:question.question}],'','帮我查看水消耗数据的统计情况',active));}
    $('#saved').onclick=()=>render();$('#history').onclick=()=>render(false);
    $('#generic').onclick=()=>$('#answer').replaceChildren(assistantRow([{type:'business_question',question:'您想继续处理这一项，还是先查看现有结果？',options:['继续处理这一项','先查看现有结果']},{type:'text',text:'已保存报告可查看。'}],''));
    $('#stream').onclick=()=>{const body=$('#answer');body.innerHTML='';const ctx={body,toolMap:{},segments:[],active:null};for(const [name,data] of [['tool.started',{tool:'query_business_data',tool_call_id:'one'}],['tool.completed',{tool:'query_business_data',tool_call_id:'one',status:'waiting_input',summary:'等待你的选择'}],['message.question',question]]) handleFrame('event: '+name+'\\ndata: '+JSON.stringify(data),ctx);};
    $('#error').onclick=()=>$('#answer').replaceChildren(assistantRow([{type:'tool_result',result:{status:'failed',error_summary:'本次查询等待时间较长，已停止处理。'}}],''));
    $('#custom').onclick=()=>appendBusinessQuestion($('#answer'),{question:'你想查看哪一种用水数据？',options:[]});
    $('#mobile').onclick=()=>{document.querySelector('main').style.maxWidth='390px';};render();
    </script></html>`;
}

async function verifyWithPage(page) {
  const assert = require("node:assert/strict");
  await page.setContent(fixtureHtml());
  assert.equal(await page.$$eval('.business-question-option, .business-question-entry', a=>a.length),0);
  assert.match(await page.$eval('#answer',e=>e.textContent),/需要补充信息/);
  assert.equal(await page.$$eval('.err',a=>a.length),0);
  await page.click('#stream');
  assert.equal(await page.$$eval('.business-question-option, .business-question-entry',a=>a.length),0);
  assert.equal(await page.$$eval('.err',a=>a.length),0);
  assert.equal(await page.$eval('#sent',e=>e.textContent),"");
  await page.click('#saved');
  assert.equal(await page.$$eval('.business-question-option, .business-question-entry',a=>a.length),0);
  await page.click('#history');
  assert.equal(await page.$$eval('.business-question, .business-question-option, .business-question-entry',a=>a.length),0);
  await page.click('#error');
  assert.equal(await page.$$eval('.tool-chip.err',a=>a.length),1);
  await page.click('#generic');
  assert.equal(await page.$$eval('.business-question',a=>a.length),0);
  assert.match(await page.$eval('#answer',e=>e.textContent),/已保存报告可查看/);
  await page.click('#saved');await page.click('#custom');
  assert.equal(await page.$$eval('.business-question, .business-question-option, .business-question-entry',a=>a.length),0);
  assert.match(await page.$eval('#answer',e=>e.textContent),/需要补充信息/);
  await page.click('#mobile');
  assert(await page.$eval('main',e=>e.scrollWidth<=e.clientWidth));
}
module.exports={fixtureHtml,verifyWithPage};
