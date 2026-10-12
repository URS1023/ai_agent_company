/*
 * 中文说明（质检批次运行的浏览器验收）：
 * 验证行为：多设备/多样本质检运行的页面展示与执行结果、共享工作流记录一致。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：会产生质检运行记录（隔离环境）；断言以运行结果与页面一致性为准。
 */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const puppeteer = require('puppeteer-core');
const BASE = process.env.TEST5_E2E_BASE_URL || 'http://127.0.0.1:8011';
const RUN = Date.now().toString(36), OUTPUT = path.resolve('.implementation/ui-results/quality-runs-' + RUN);
fs.mkdirSync(OUTPUT,{recursive:true});
const checks = [], failures = [], violations = [], calls = [], created = [], imageIds = [], deviceIds = [], sourceIds = [];
let browser, page, frame, fixture, workflowId;
const pass = message => { checks.push(message); console.log('PASS ' + message); };
async function api(url, method = 'GET', body) {
  return page.evaluate(async (url, method, body) => {
    const response = await fetch(url, { method, headers: { 'Content-Type':'application/json', Authorization:'Bearer ' + localStorage.getItem('agent_token'), 'X-Workspace-Id':localStorage.getItem('agent_workspace_id') }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status:response.status, body: await response.json() };
  }, url, method, body);
}
async function create(url, payload) { const response = await api(url,'POST',payload); assert.equal(response.status,201,JSON.stringify(response.body)); return response.body.data; }
async function visit(route) {
  assert.equal((await page.goto(BASE + route, { waitUntil:'networkidle0' })).status(),200);
  frame = await (await page.waitForSelector('#business-module-frame')).contentFrame();
  await frame.waitForSelector('.quality-ui', {visible:true});
}
async function button(label, root = '') {
  const selector = root + ' button';
  await frame.waitForFunction((label, selector) => [...document.querySelectorAll(selector)].some(el => el.getClientRects().length && el.textContent.trim() === label && !el.disabled), {}, label, selector);
  for (const element of await frame.$$(selector)) if (await element.evaluate((el,text) => el.getClientRects().length && el.textContent.trim() === text,label)) {
    await element.scrollIntoView();
    await frame.waitForFunction(el => { const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); }, {}, element);
    await element.click(); return;
  }
  throw Error('Missing button ' + label);
}
async function field(label, value, root = '.quality-ui') {
  await frame.waitForFunction((label, root) => [...document.querySelectorAll(root+' label')].some(el=>el.getClientRects().length && el.firstChild?.textContent.trim()===label && el.querySelector('input,select')),{},label,root);
  const handle=await frame.evaluateHandle((label,root)=>[...document.querySelectorAll(root+' label')].find(el=>el.getClientRects().length && el.firstChild?.textContent.trim()===label)?.querySelector('input,select'),label,root);
  const element=handle.asElement(); assert(element); await element.scrollIntoView();
  if(await element.evaluate(el=>el.tagName==='SELECT')) { await frame.waitForFunction((el,value)=>!el.disabled&&[...el.options].some(item=>item.value===value),{},element,value); await element.select(value); }
  else { await element.click({clickCount:3}); await page.keyboard.down('Control'); await page.keyboard.press('KeyA'); await page.keyboard.up('Control'); await element.type(value); await element.press('Tab'); }
}
async function audit(label) {
  await frame.waitForFunction(()=>!document.querySelector('[class*="enter-active"],[class*="leave-active"]'));
  await frame.addScriptTag({path:require.resolve('axe-core/axe.min.js')});
  const found=await frame.evaluate(async()=> (await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa','wcag22aa']}})).violations.map(item=>({id:item.id,impact:item.impact,nodes:item.nodes.map(node=>({target:node.target,summary:node.failureSummary}))})));
  violations.push(...found.map(item=>({label,...item})));
}
async function uploadTo(group, index, imagePath) {
  const selector=`[data-group-id="${group}"] [data-sample-index="${index}"] input[type="file"]`;
  await frame.waitForFunction(selector=>document.querySelector(selector)&&!document.querySelector(selector).disabled,{},selector);
  const [response]=await Promise.all([page.waitForResponse(response=>response.url().endsWith('/api/v1/quality/images')&&response.request().method()==='POST'), (await frame.$(selector)).uploadFile(imagePath)]);
  assert.equal(response.status(),201,await response.text()); const image=(await response.json()).data; imageIds.push(image.id);
  await frame.waitForFunction(selector=>!document.querySelector(selector)?.disabled,{},selector);
}
(async()=>{
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE,'isolated'); assert(process.env.TEST5_E2E_PASSWORD);
  assert(['127.0.0.1','localhost'].includes(new URL(BASE).hostname)); assert.equal((await(await fetch(BASE+'/api/health')).json()).app,'test');
  fs.mkdirSync(OUTPUT,{recursive:true});
  fixture=http.createServer((request,response)=>{
    let raw='';request.on('data',chunk=>raw+=chunk);request.on('end',()=>{
      const body=JSON.parse(raw);calls.push({path:request.url,...body});
      const failed=body.sampleId==='SAMPLE-B';
      const item=request.url==='/appearance'?{sampleId:body.sampleId,defects:failed?[{defectType:'测试划痕',confidence:'0.98',severity:'severe',bbox:[0.2,0.25,0.3,0.15]}]:[]}:
        {sampleId:body.sampleId,components:[{componentName:'C',measuredValue:failed?'0.24':'0.15',unit:'%'}]};
      response.writeHead(200,{'Content-Type':'application/json'});response.end(JSON.stringify({items:[item]}));
    });
  });
  await new Promise(resolve=>fixture.listen(0,'127.0.0.1',resolve));
  browser=await puppeteer.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  page=await browser.newPage();await page.setViewport({width:1600,height:1100});
  page.on('pageerror',cause=>failures.push(cause.message));page.on('response',response=>{if(response.status()>=500)failures.push(response.status()+' '+response.url());});page.on('dialog',dialog=>dialog.accept());
  await page.goto(BASE,{waitUntil:'networkidle0'});await page.type('#login-username','workspace-admin');await page.type('#login-password',process.env.TEST5_E2E_PASSWORD);await page.click('#login-btn');await page.waitForSelector('.overview-intro');
  const png=await page.evaluate(()=>{const canvas=document.createElement('canvas');canvas.width=640;canvas.height=400;const c=canvas.getContext('2d');c.fillStyle='#e9edf2';c.fillRect(0,0,640,400);c.fillStyle='#a7b7c7';c.fillRect(100,60,440,270);c.fillStyle='#17263c';c.font='16px sans-serif';c.fillText('SYNTHETIC UI TEST SAMPLE - NOT INDUSTRIAL EVIDENCE',40,375);return canvas.toDataURL('image/png').split(',')[1];});
  const imagePath=path.join(OUTPUT,'synthetic-sample.png');fs.writeFileSync(imagePath,Buffer.from(png,'base64'));
  const node=(id,kind,config={},inputs={})=>({id,kind,title:id,config,inputs});
  const nodes=[node('start','start')],edges=[];
  for(const [index,kind] of ['component','appearance'].entries()) {
    const body={sampleId:'${input.sampleId}'};if(kind==='appearance')body.image='${input.imageBase64}';
    const source=await create('/api/v1/collection/sources',{name:'普通质检源 '+index+' '+RUN,url:`http://127.0.0.1:${fixture.address().port}/${kind}`,method:'POST',bodies:[body],recordPath:'$.items',allowPrivate:true,allowAutomatic:true});sourceIds.push(source.id);
    const device=await create('/api/v1/alerts/devices',{code:'QCR-'+index+'-'+RUN,name:(kind==='appearance'?'外观检查设备':'成分检查设备')+' '+RUN,datasourceId:source.id,domains:['quality']});deviceIds.push(device.id);
    const metric=await create('/api/v1/quality/metrics',kind==='appearance'?{name:'外观质量 '+RUN,category:'appearance'}:{name:'碳含量 '+RUN,category:'component',componentName:'C',unit:'%',standardMin:'0.10',standardMax:'0.20'});created.push(['metrics',metric.id]);
    const template=await create('/api/v1/quality/templates',{name:(kind==='appearance'?'外观标准':'成分标准')+' '+RUN,metricIds:[metric.id]});created.push(['templates',template.id]);
    nodes.push(node('detect'+index,'qc-'+kind,{deviceId:device.id,metricId:metric.id,metricVersion:1,datasourceId:source.id,datasourceVersion:1,requestMode:'input'},{input:{nodeId:'start',path:'$.samples'+index}}),
      node('evaluate'+index,'qc-evaluate',{deviceId:device.id,templateId:template.id,templateRevision:1,sampleMode:'input'}));
    edges.push({id:'start'+index,source:'start',target:'detect'+index,port:'out'},{id:'detect'+index,source:'detect'+index,target:'evaluate'+index,port:'out'},{id:'report'+index,source:'evaluate'+index,target:'merge',port:'out'});
  }
  nodes.push(node('merge','merge'),node('end','end'));edges.push({id:'end',source:'merge',target:'end',port:'out'});
  const flow=await create('/api/v1/workflows',{name:'普通多设备质检 '+RUN,domains:['quality'],graph:{nodes,edges}});workflowId=flow.id;
  assert.equal((await api('/api/v1/workflows/'+workflowId+'/publish','POST',{version:flow.version})).status,200);
  await visit('/quality/inspections');await button('运行检测');assert.equal(await frame.$('#qc-run-form'),null);assert.equal(calls.length,0);
  await field('已发布检测流程',workflowId);await frame.waitForFunction(()=>document.querySelectorAll('.qc-run-group').length===2);
  assert(await frame.evaluate(()=>document.querySelector('#qc-run-form').textContent.includes('固定修订 v1')));assert.equal(calls.length,0);
  pass('Normal run page explicitly selects a published workflow and displays both fixed device/template groups without default selection or device calls');
  for(const group of ['evaluate0','evaluate1']){const root=`[data-group-id="${group}"]`;await field('样本编号 1','SAMPLE-A',root);await button('添加样本',root);await field('样本编号 2','SAMPLE-B',root);}
  await button('开始检测并生成报告');await frame.waitForFunction(()=>document.querySelector('.qc-run-panel .wf-error')?.textContent.includes('上传图片'));assert.equal(calls.length,0);
  await uploadTo('evaluate1',0,imagePath);await uploadTo('evaluate1',1,imagePath);
  await field('样本编号 2','SAMPLE-A','[data-group-id="evaluate0"]');await button('开始检测并生成报告');await frame.waitForFunction(()=>document.querySelector('.qc-run-panel .wf-error')?.textContent.includes('重复样本'));assert.equal(calls.length,0);await field('样本编号 2','SAMPLE-B','[data-group-id="evaluate0"]');
  pass('Missing original images and duplicate sample IDs stop before HTTP; different devices can use the same sample labels');
  await button('预览原图','[data-group-id="evaluate1"]');await frame.waitForFunction(()=>document.querySelector('.qc-run-panel .qc-original-image')?.naturalWidth===640);
  await audit('Normal multi-device sample and image form');await (await frame.$('.qc-run-panel')).scrollIntoView();await page.screenshot({path:path.join(OUTPUT,'batch-run-form.png'),fullPage:true});
  const [response]=await Promise.all([page.waitForResponse(response=>response.url().endsWith(`/quality/workflows/${workflowId}/runs`)&&response.request().method()==='POST'),button('开始检测并生成报告')]);
  assert.equal(response.status(),200,await response.text());const run=(await response.json()).data;
  assert.equal(run.samples.length,4);assert.deepEqual(run.samples.map(item=>item.outcome),['passed','failed','passed','failed']);assert.equal(calls.length,4);
  assert(calls.filter(item=>item.path==='/appearance').every(item=>Buffer.from(item.image,'base64').equals(Buffer.from(png,'base64'))));
  for(const sample of run.samples)created.push(['inspections',sample.inspectionId]);
  await frame.waitForSelector('.qc-run-result',{visible:true});assert.equal(await frame.$$eval('.qc-run-result tbody tr',rows=>rows.length),4);
  assert(await frame.evaluate(()=>document.querySelector('.qc-run-result').textContent.includes('成分检查设备')));
  await frame.waitForFunction(()=>[...document.querySelectorAll('.business-page > .wf-table-wrap tbody tr')].filter(row=>row.querySelector('button')?.textContent.trim()==='查看报告').length===4);
  await audit('Batch outcomes separated by device and sample');await page.screenshot({path:path.join(OUTPUT,'batch-results.png'),fullPage:true});
  pass('Four actual HTTP detections create four independent reports with correct device/sample outcomes and original image bytes');
  await button('查看报告','.qc-run-result');await frame.waitForSelector('.qc-report',{visible:true});await button('查看所属检测批次');await frame.waitForSelector('.qc-report',{hidden:true});await button('刷新批次结果');assert.equal(calls.length,4);
  pass('Ordinary report opens its owning batch, and refreshing stored results never repeats detector calls');
  const deepLink=`/quality/inspections?qualityWorkflow=${workflowId}&qualityRun=${run.execution.id}`;
  await page.setViewport({width:390,height:844});await visit(deepLink);await frame.waitForSelector('.qc-run-result',{visible:true});
  assert.equal(await frame.$$eval('.qc-run-result tbody tr',rows=>rows.length),4);assert(await frame.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  await (await frame.$('.qc-run-result')).scrollIntoView();await audit('390px persisted batch result');await page.screenshot({path:path.join(OUTPUT,'batch-results-mobile.png'),fullPage:true});assert.equal(calls.length,4);
  pass('390px direct batch link reloads all four persisted reports in the Qknow enterprise shell');
  await button('开始下一批');await frame.waitForSelector('#qc-run-form',{visible:true});assert.equal(await frame.$$eval('.qc-run-sample input:not([type=file])',inputs=>inputs.filter(item=>item.value).length),0);assert.equal(calls.length,4);
  await audit('390px new batch form');await page.screenshot({path:path.join(OUTPUT,'batch-form-mobile.png'),fullPage:true});
  pass('Starting another batch requires new explicit sample entries and does not automatically re-execute the previous batch');
  assert.deepEqual(failures,[]);assert.deepEqual(violations,[]);
})().catch(cause=>{failures.push(cause.stack||String(cause));console.error(cause);process.exitCode=1;}).finally(async()=>{
  if(page)try{
    if(failures.length)await page.screenshot({path:path.join(OUTPUT,'failure.png'),fullPage:true});
    for(const [kind,id] of [...created].reverse()){const row=await api('/api/v1/quality/'+kind+'/'+id);if(row.status===200)assert.equal((await api('/api/v1/quality/'+kind+'/'+id+'?version='+row.body.data.version,'DELETE')).status,200);}
    if(workflowId){const row=await api('/api/v1/workflows/'+workflowId);if(row.status===200)assert.equal((await api('/api/v1/workflows/'+workflowId+'?version='+row.body.data.version,'DELETE')).status,200);}
    for(const id of deviceIds){const row=await api('/api/v1/alerts/devices/'+id);if(row.status===200)assert.equal((await api('/api/v1/alerts/devices/'+id+'?version='+row.body.data.version,'DELETE')).status,200);}
    for(const id of sourceIds){const row=await api('/api/v1/collection/sources/'+id);if(row.status===200)assert.equal((await api('/api/v1/collection/sources/'+id+'?version='+row.body.data.version,'DELETE')).status,200);}
    for(const id of imageIds)await api('/api/files/'+id,'DELETE');
  }catch(cause){failures.push('Cleanup: '+cause.message);process.exitCode=1;}
  fs.writeFileSync(path.join(OUTPUT,'result.json'),JSON.stringify({checks,failures,violations,actualHttpRequests:calls.length},null,2));
  if(browser)await browser.close();if(fixture)await new Promise(resolve=>fixture.close(resolve));console.log('Evidence '+OUTPUT);
});
