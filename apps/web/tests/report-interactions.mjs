/** 在隔离SQLite平台验证素材上传、真实授权读取、恢复与模板保存。 */
import { chromium } from "playwright";
import { readFile, mkdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import assert from "node:assert/strict";
const origin=process.env.E2E_ORIGIN??"http://127.0.0.1:5173";
if (!/^http:\/\/(127\.0\.0\.1|localhost):\d+$/.test(origin)) throw new Error('只允许本机隔离环境');
const browser=await chromium.launch({headless:true,executablePath:process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE});
const context=await browser.newContext({viewport:{width:1600,height:1000},reducedMotion:'reduce'});
const page=await context.newPage();page.setDefaultTimeout(15000);
const result={checks:[],requests:0,page_errors:[],external_requests:[]};
page.on('pageerror',error=>result.page_errors.push(error.message));
page.on('request',request=>{const url=new URL(request.url());if(url.protocol.startsWith('http')&&!['127.0.0.1','localhost'].includes(url.hostname))result.external_requests.push(url.origin);});
let csrf='';
async function call(method,path,data) {
  if (++result.requests>35) throw new Error('达到35次请求预算');
  const response=await context.request.fetch(`${origin}/api/v1${path}`,{method,headers:{'X-CSRF-Token':csrf},data,timeout:15000});
  assert.ok(response.ok(),`${method} ${path}: ${response.status()}`);return response.json();
}
async function openReport(report) {
  await page.goto(`${origin}/reports`);
  await page.locator('.el-table__row').filter({hasText:report.name}).getByRole('button',{name:'设计',exact:true}).click();
  await page.locator('.screen-widget').first().waitFor();
}
await mkdir('test-results',{recursive:true});
try {
  csrf=(await call('POST','/auth/login',{username:process.env.E2E_USERNAME,password:process.env.E2E_PASSWORD})).csrf_token;
  const catalog=(await call('GET','/report-components/catalog')).items;
  const firstImage=catalog.find(item=>item.imageUrl);
  const selections=[
    [catalog.find(item=>item.compType==='JDragBorder'&&item.name==='边框5'),40,40,920,500],
    [catalog.find(item=>item.compType==='JDragDecoration'&&item.name==='装饰4'),100,100,760,90],
    [firstImage,110,230,240,210],
    [catalog.find(item=>item.compType==='JCustomIcon'),400,255,130,130],
    [catalog.find(item=>item.compType==='JCarousel'),40,580,920,250],
    [catalog.find(item=>item.compType==='JImg'&&!item.imageUrl),620,230,250,210],
  ];
  const widgets=[];
  for (const [item,x,y,w,h] of selections) {
    const widget=await call('POST','/report-component-instances',{source_id:item.id,x,y,order_num:widgets.length});
    widget.w=w;widget.h=h;
    if(widget.component==='JCarousel')widget.config.images=catalog.filter(item=>item.imageUrl).slice(1,4).map(item=>item.imageUrl);
    if(widget.component==='JCustomIcon'){widget.config.option.icon='fa-solid fa-industry';widget.config.option.iconColor='#51d6c2';}
    if(widget.component==='JImg'&&!widget.config.imageUrl)widget.componentName='上传图片';
    widgets.push(widget);
  }
  const report=await call('POST','/reports',{name:`素材交互验收${Date.now()}`,layout:{canvas:{width:1440,height:900,backgroundColor:'#07182d'},widgets}});
  await openReport(report);
  await page.getByRole('button',{name:'上传图片',exact:true}).first().click();
  const uploading=page.waitForResponse(response=>response.url().endsWith('/api/v1/files')&&response.request().method()==='POST');
  const bytes=await readFile(join(process.cwd(),'public',firstImage.imageUrl));
  assert.ok(bytes.length<2*1024*1024);
  await page.locator('.report-properties input[type=file]').setInputFiles({name:'素材验证.png',mimeType:'image/png',buffer:bytes});
  assert.ok((await uploading).ok());
  const imageWidget=widgets.find(widget=>widget.componentName==='上传图片');
  await page.waitForFunction(id=>{const image=document.querySelector(`[data-widget-id="${id}"] img`);return image?.complete&&image.naturalWidth>0;},imageWidget.id);
  const saving=page.waitForResponse(response=>response.url().endsWith(`/reports/${report.id}`)&&response.request().method()==='PATCH');
  await page.getByRole('button',{name:'保存',exact:true}).click();await saving;
  const saved=await call('GET',`/reports/${report.id}`);
  const config=saved.layout.widgets.find(widget=>widget.id===imageWidget.id).config;
  assert.ok(config.file_id);assert.equal(config.imageUrl,'');assert.ok(!JSON.stringify(config).includes('signature'));
  const assets=await call('GET',`/reports/${report.id}/image-assets`);
  assert.equal(assets.items[0].file_id,config.file_id);assert.ok(assets.items[0].data_uri.startsWith('data:image/png;base64,'));
  result.checks.push('上传真实文件，仅保存file_id，图片授权读取成功');
  await openReport(report);
  await page.waitForFunction(id=>{const image=document.querySelector(`[data-widget-id="${id}"] img`);return image?.complete&&image.naturalWidth>0&&image.src.startsWith('data:image/');},imageWidget.id);
  await page.getByRole('button',{name:'预览',exact:true}).click();
  await page.waitForFunction(()=>![...document.querySelectorAll('.el-loading-mask,.el-message,.el-button.is-loading')].some(element=>element.getClientRects().length));
  await page.screenshot({path:'test-results/material-preview.png'});
  result.checks.push('保存后重新打开，服务端授权图片仍可显示');
  await page.getByRole('button',{name:'编辑',exact:true}).click();
  await page.getByRole('button',{name:'另存模板',exact:true}).click();
  const templateName=`素材模板${Date.now()}`;
  await page.locator('.el-message-box__input input').fill(templateName);
  const templateSaving=page.waitForResponse(response=>response.url().endsWith('/report-templates')&&response.request().method()==='POST');
  await page.getByRole('button',{name:'确定',exact:true}).click();assert.ok((await templateSaving).ok());
  assert.ok((await call('GET','/report-templates')).items.some(item=>item.name===templateName));
  result.checks.push('另存模板实际写入服务端');
  await page.getByRole('button',{name:'历史版本',exact:true}).click();
  await page.locator('.el-dialog:visible .el-table__row').first().waitFor();
  const original=page.locator('.el-dialog:visible .el-table__row').filter({has:page.getByText('1',{exact:true})});
  await original.getByRole('button',{name:'恢复',exact:true}).click();
  const restoring=page.waitForResponse(response=>response.url().endsWith(`/reports/${report.id}/restorations`));
  await page.getByRole('button',{name:'确定',exact:true}).click();assert.ok((await restoring).ok());
  const restored=await call('GET',`/reports/${report.id}`);
  assert.ok(restored.revision>saved.revision);
  assert.ok(!restored.layout.widgets.find(widget=>widget.id===imageWidget.id).config.file_id);
  result.checks.push('恢复真实历史布局并产生新的修订');
  assert.equal(result.page_errors.length,0);assert.equal(result.external_requests.length,0);result.passed=true;
}catch(error){result.passed=false;result.error=error.message;await page.screenshot({path:'test-results/report-interactions-failure.png'}).catch(()=>{});}
finally{await writeFile('test-results/report-interactions.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));await browser.close();}
if(!result.passed)process.exitCode=1;
