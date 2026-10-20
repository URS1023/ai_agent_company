/** 本机Dify原生界面验收；只由verify_dify_native传入已核实的专属runtime计划。 */
import { createHash } from 'node:crypto';
import { execFile } from 'node:child_process';
import { writeFile } from 'node:fs/promises';
import path from 'node:path';
import { promisify } from 'node:util';

const execute = promisify(execFile);
const ORIGIN = 'http://127.0.0.1:13001';
export const runBodyHash = () => createHash('sha256').update('{"files":[],"inputs":{}}').digest('hex');

/** 仅保留有限方法/路由信息；不记录query、请求体、Cookie、Authorization或响应内容。 */
export function blockedRequestMetadata(method, url) {
  let pathname = '/invalid-url';
  try { pathname = new URL(url).pathname.slice(0, 240); } catch { /* 非URL不回显原输入。 */ }
  return { method: /^[A-Z]{1,12}$/.test(method) ? method : 'UNKNOWN', path: pathname };
}

/** 原生匿名登录探测会刷新Cookie；仅允许精确同源空请求，每浏览器最多两次。 */
export class CookieRefreshGate {
  constructor() { this.requests = 0; }
  allow(method, url, rawBody) {
    const target = new URL(url);
    if (method !== 'POST' || target.origin !== ORIGIN || target.pathname !== '/console/api/refresh-token'
      || target.search || target.username || target.password) return false;
    if (rawBody !== null && rawBody !== undefined && rawBody !== '') {
      if (typeof rawBody !== 'string' || rawBody.length > 32) return false;
      try {
        const body = JSON.parse(rawBody);
        if (!body || typeof body !== 'object' || Array.isArray(body) || Object.keys(body).length) return false;
      } catch { return false; }
    }
    if (this.requests >= 2) throw new Error('cookie_refresh_budget_exceeded');
    this.requests++;
    return true;
  }
}

/** 仅接受固定空输入和无文件，防止原生界面意外追加其他调用参数。 */
export function fixedRunBody(body) {
  if (!body || Object.keys(body).some(key => !['inputs', 'files'].includes(key))) return false;
  return body.inputs && Object.keys(body.inputs).length === 0
    && (body.files === undefined || body.files === null || Array.isArray(body.files) && body.files.length === 0);
}

/** 自动保存可以改变布局和本轮代码注释，禁止增加节点、输入或外部功能。 */
export function fixedDraft(body, flow) {
  const nodes = body?.graph?.nodes;
  const edges = body?.graph?.edges;
  if (!Array.isArray(nodes) || nodes.length !== 3 || !Array.isArray(edges) || edges.length !== 2) return false;
  const byId = Object.fromEntries(nodes.map(node => [node.id, node.data]));
  if (Object.keys(byId).sort().join() !== 'code,end,start') return false;
  if (byId.start?.type !== 'start' || byId.code?.type !== 'code' || byId.end?.type !== 'end') return false;
  if (byId.start.variables?.length !== 0 || byId.code.variables?.length !== 0) return false;
  if (byId.code.code_language !== flow.language) return false;
  if (![flow.code.trimEnd(), flow.edited_code.trimEnd()].includes(byId.code.code?.trimEnd())) return false;
  if (edges.map(edge => `${edge.source}:${edge.target}`).sort().join() !== 'code:end,start:code') return false;
  const outputSignature = values => Array.isArray(values) && values.map(value =>
    [value.variable, value.value_type, value.value_selector?.join('.')].join(':')).sort().join(',');
  if (outputSignature(byId.end.outputs) !== outputSignature(flow.graph.nodes[2].data.outputs)) return false;
  if (Object.values(body.features || {}).some(value => value && typeof value === 'object' && value.enabled === true)) return false;
  return !body.environment_variable_patch?.environment_variables?.length && !body.conversation_variables?.length;
}

/** 按锁定Dify事件结构核对同一原生run的开始、代码执行和确切终态，不推断成功。 */
export function workflowSse(text, flow) {
  if (Buffer.byteLength(text) > 2 * 1024 * 1024) throw new Error('workflow_sse_size_budget');
  const events = text.split(/\r?\n\r?\n/).flatMap(block => {
    const data = block.split(/\r?\n/).filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n');
    if (!data || data === '[DONE]') return [];
    try { return [JSON.parse(data)]; } catch { throw new Error('workflow_sse_invalid_json'); }
  });
  if (events.some(event => event.event === 'error')) throw new Error('workflow_sse_error');
  const starts = events.filter(event => event.event === 'workflow_started');
  const finishes = events.filter(event => event.event === 'workflow_finished');
  if (starts.length !== 1 || finishes.length !== 1) throw new Error('workflow_lifecycle_incomplete');
  const start = starts[0];
  const finish = finishes[0];
  if (!start.data?.id || finish.data?.id !== start.data.id || finish.workflow_run_id !== start.workflow_run_id) {
    throw new Error('workflow_run_identity_changed');
  }
  if (finish.data.status !== 'succeeded' || finish.data.total_tokens !== 0 || finish.data.error
    || finish.data.outputs?.result !== flow.marker || finish.data.outputs?.value !== 42) {
    throw new Error('workflow_outputs_not_verified');
  }
  const code = events.find(event => event.event === 'node_finished' && event.data?.node_id === 'code');
  if (!code || code.data.node_type !== 'code' || code.data.status !== 'succeeded') throw new Error('native_code_node_not_verified');
  return { workflow_run_id: finish.data.id, task_id: start.task_id, status: 'succeeded',
    outputs: { result: flow.marker, value: 42 }, total_tokens: 0, native_code_node: true };
}

/** API准备与浏览器共享请求上限；单调时钟在开始时固定剩余总预算。 */
export class RequestBudget {
  constructor(input) {
    if (input.origin !== ORIGIN || input.max_business !== 120 || ![400, 600].includes(input.max_static)) throw new Error('invalid_browser_scope');
    if (!Number.isInteger(input.business_used) || input.business_used < 0 || input.business_used > 120
      || input.deadline_unix_ms > Date.now() + 180000) throw new Error('invalid_browser_budget');
    this.business = input.business_used;
    this.static = 0;
    this.maxStatic = input.max_static;
    this.stopAt = performance.now() + Math.max(0, input.deadline_unix_ms - Date.now());
  }
  remaining() {
    const seconds = this.stopAt - performance.now();
    if (seconds <= 0) throw new Error('verification_total_deadline');
    return seconds;
  }
  take(url, resourceType) {
    this.remaining();
    const target = new URL(url);
    if (target.origin !== ORIGIN) return 'external';
    const isStatic = ['script', 'stylesheet', 'image', 'font'].includes(resourceType)
      || target.pathname.startsWith('/_next/static/') || /\.(ico|png|svg|woff2?)$/i.test(target.pathname);
    if (isStatic) {
      if (++this.static > this.maxStatic) throw new Error('static_request_budget_exceeded');
      return 'static';
    }
    if (++this.business > 120) throw new Error('business_http_budget_exceeded');
    return 'business';
  }
}

export async function main(input) {
  const { chromium } = await import('playwright');
  const budget = new RequestBudget(input);
  const result = { passed: false, run_id: input.run_id, business_http: budget.business, static_requests: 0,
    workflow_requests: 0, model_requests: 0, blocked_external: 0, screenshots: [], flows: [], page_error_count: 0,
    cookie_refresh_requests: 0, blocked_requests: [] };
  const allowedFlows = new Map(input.flows.map(flow => [flow.app_id, flow]));
  if (allowedFlows.size !== input.flows.length || ![1, 2].includes(allowedFlows.size)
    || input.flows.some(flow => !['javascript', 'python3'].includes(flow.language))) {
    throw new Error('fixed_workflow_scope_required');
  }
  let browser;
  let context;
  let fatal;
  const dispatched = new Set();
  const editedSaved = new Set();
  const refreshGate = new CookieRefreshGate();
  const fail = code => {
    fatal ||= code;
    if (context) void context.close().catch(() => {});
  };
  const timer = setTimeout(() => fail('verification_total_deadline'), budget.remaining());
  try {
    browser = await chromium.launch({ channel: 'chrome', headless: true, timeout: Math.min(15000, budget.remaining()),
      args: ['--disable-gpu', '--renderer-process-limit=2', '--no-proxy-server', '--disable-background-networking'] });
    context = await browser.newContext({ locale: 'en-US', viewport: { width: 1500, height: 950 },
      serviceWorkers: 'block', ignoreHTTPSErrors: false });
    await context.addCookies([{ name: 'locale', value: 'en-US', url: ORIGIN }]);
    await context.addInitScript(origin => {
      if (location.origin === origin) {
        localStorage.setItem('locale', 'en-US');
        localStorage.setItem('i18nextLng', 'en-US');
      }
    }, ORIGIN);
    await context.route('**/*', async route => {
      const request = route.request();
      try {
        const kind = budget.take(request.url(), request.resourceType());
        if (kind === 'external') {
          result.blocked_external++;
          return await route.abort('blockedbyclient');
        }
        const url = new URL(request.url());
        const method = request.method();
        if (method === 'GET' || method === 'HEAD') return await route.continue();
        if (refreshGate.allow(method, request.url(), request.postData())) {
          result.cookie_refresh_requests = refreshGate.requests;
          return await route.continue();
        }
        if (method === 'POST' && url.pathname === '/console/api/login') return await route.continue();
        const match = url.pathname.match(/^\/console\/api\/apps\/([a-f0-9-]{36})\/workflows\/draft(\/run)?$/);
        const flow = match && allowedFlows.get(match[1]);
        if (method !== 'POST' || !flow) throw new Error('unexpected_native_write_endpoint');
        const body = request.postDataJSON();
        if (!match[2]) {
          if (!fixedDraft(body, flow)) throw new Error('unsafe_native_autosave');
          if (body.graph.nodes.find(node => node.id === 'code').data.code.trimEnd() === flow.edited_code.trimEnd()) {
            editedSaved.add(flow.app_id);
          }
        } else {
          if (!fixedRunBody(body) || dispatched.has(flow.app_id) || !editedSaved.has(flow.app_id)) throw new Error('workflow_dispatch_not_safe_or_duplicate');
          dispatched.add(flow.app_id);
          // 派发前由同一Python账本同步加密；即使浏览器断开，也禁止再次运行同一流程。
          await execute(input.python, [input.verifier, 'checkpoint', '--run-id', input.run_id,
            '--language', flow.language, '--app-id', flow.app_id, '--request-sha256', runBodyHash()],
          { timeout: Math.min(8000, budget.remaining()), windowsHide: true, maxBuffer: 16384 });
          result.workflow_requests++;
        }
        await route.continue();
      } catch (error) {
        const metadata = blockedRequestMetadata(request.method(), request.url());
        result.blocked_request ||= metadata;
        if (result.blocked_requests.length < 5) result.blocked_requests.push(metadata);
        await route.abort('blockedbyclient').catch(() => {});
        fail(error instanceof Error && /^[a-z0-9_]+$/.test(error.message) ? error.message : 'browser_route_failed');
      }
    });
    context.on('response', response => {
      if (response.url().startsWith(ORIGIN + '/console/api/') && response.status() >= 500) {
        // 只保留端点与状态定位服务错误，不记录响应正文、查询参数或认证信息。
        result.failed_response ||= { path: new URL(response.url()).pathname, status: response.status() };
        fail('native_api_server_error');
      }
    });
    const page = await context.newPage();
    page.setDefaultTimeout(12000);
    page.setDefaultNavigationTimeout(20000);
    page.on('pageerror', () => { result.page_error_count++; fail('native_ui_javascript_error'); });
    result.phase = 'native_login';
    await page.goto(ORIGIN + '/signin', { waitUntil: 'domcontentloaded' });
    await page.locator('input[type="email"]').fill(input.account.email);
    await page.locator('input[type="password"]').fill(input.account.password);
    const login = page.waitForResponse(response => response.url() === ORIGIN + '/console/api/login' && response.request().method() === 'POST');
    // 点击被拦截时，响应等待也必须被观察，避免收尾关闭页面产生未处理拒绝。
    void login.catch(() => {});
    await page.getByRole('button', { name: 'Sign in', exact: true }).click();
    if ((await login).status() !== 200) throw new Error('native_ui_login_failed');
    await page.waitForURL(url => url.pathname !== '/signin', { timeout: Math.min(15000, budget.remaining()) });
    result.login_verified = true;
    for (const flow of input.flows) {
      if (fatal) throw new Error(fatal);
      result.phase = flow.language + '_editor';
      await page.goto(ORIGIN + '/app/' + flow.app_id + '/workflow', { waitUntil: 'domcontentloaded' });
      const codeNode = page.locator('.react-flow__node[data-id="code"]');
      await codeNode.waitFor({ state: 'visible' });
      await codeNode.click();
      const editor = page.locator('.monaco-editor textarea.inputarea').first();
      await editor.waitFor({ state: 'visible' });
      const saved = page.waitForResponse(response => response.url() === ORIGIN + `/console/api/apps/${flow.app_id}/workflows/draft`
        && response.request().method() === 'POST' && (() => {
          try { return response.request().postDataJSON().graph.nodes.find(node => node.id === 'code').data.code.trimEnd() === flow.edited_code.trimEnd(); }
          catch { return false; }
        })());
      void saved.catch(() => {});
      // 通过原生Monaco编辑实际代码，追加无副作用注释，再核原生自动保存响应。
      await editor.focus();
      await page.keyboard.press('Control+End');
      await page.keyboard.insertText('\n' + flow.editor_comment);
      await page.getByRole('button', { name: /^Test Run/ }).first().focus();
      if ((await saved).status() !== 200) throw new Error('native_editor_save_failed');
      const editorShot = path.join(input.output, flow.language + '-editor.png');
      await page.screenshot({ path: editorShot });
      result.screenshots.push(editorShot);
      // 原生无输入流程点击Test Run即派发，不出现Start Run；必须先监听SSE，避免漏掉快速结果。
      const completion = page.waitForResponse(response => response.url() === ORIGIN + `/console/api/apps/${flow.app_id}/workflows/draft/run`
        && response.request().method() === 'POST', { timeout: Math.min(30000, budget.remaining()) });
      void completion.catch(() => {});
      result.phase = flow.language + '_run';
      await page.getByRole('button', { name: /^Test Run/ }).first().click();
      const response = await completion;
      if (response.status() !== 200 || !response.headers()['content-type']?.includes('text/event-stream')) throw new Error('native_workflow_requires_sse');
      const nativeResult = workflowSse(await response.text(), flow);
      result.flows.push({ language: flow.language, app_id: flow.app_id, editor_saved: true, ...nativeResult });
      result.last_step = 'result_detail_tab';
      const detailTab = page.getByText('DETAIL', { exact: true }).or(page.getByText('Detail', { exact: true })).first();
      await detailTab.waitFor({ state: 'visible' });
      const panelId = await detailTab.evaluate(element => element.closest('div.rounded-l-2xl')?.id);
      if (!panelId) throw new Error('native_run_panel_not_identified');
      const panel = page.locator('[id=' + JSON.stringify(panelId) + ']');
      await panel.getByText('DETAIL', { exact: true }).or(panel.getByText('Detail', { exact: true })).first().click();
      result.last_step = 'result_success_status';
      await panel.getByText(/^success$/i, { exact: true }).first().waitFor({ state: 'visible' });
      // 详情页输入区可能把输出推到虚拟滚动区域；切原生RESULT页查看本次输出，不读取编辑器源码。
      await panel.getByText(/^RESULT$/i, { exact: true }).first().click();
      // 输出JSON由Monaco按token和换行拆开；限定原生结果面板归一化空白，不能读左侧代码冒充结果。
      result.last_step = 'result_output_marker';
      await page.waitForFunction(({ id, marker }) => document.getElementById(id)?.textContent
        ?.replace(/\s+/g, '').includes(marker), { id: panelId, marker: flow.marker });
      const resultShot = path.join(input.output, flow.language + '-result.png');
      await page.screenshot({ path: resultShot });
      result.screenshots.push(resultShot);
    }
    if (fatal || dispatched.size !== allowedFlows.size || result.flows.length !== allowedFlows.size) throw new Error(fatal || 'fixed_workflows_not_completed');
    result.passed = true;
    result.phase = 'complete';
  } catch (error) {
    // Playwright错误可能含fill的密码；仅保留固定代码，绝不记录原始错误/trace/响应正文。
    result.error_code = fatal || (error instanceof Error && /^[a-z0-9_]+$/.test(error.message) ? error.message : 'native_ui_operation_failed');
    if (result.login_verified && context) {
      const failurePage = context.pages()[0];
      if (failurePage && !failurePage.isClosed()) {
        const shot = path.join(input.output, 'failure.png');
        await failurePage.screenshot({ path: shot }).then(() => result.screenshots.push(shot)).catch(() => {});
      }
    }
  } finally {
    clearTimeout(timer);
    if (context) await context.close().catch(() => {});
    if (browser) await browser.close().catch(() => {});
    result.business_http = budget.business;
    result.static_requests = budget.static;
    await writeFile(path.join(input.output, 'receipt.json'), JSON.stringify(result, null, 2), 'utf8');
  }
  return result;
}

if (process.env.DIFY_VERIFY_INPUT) {
  let result;
  try { result = await main(JSON.parse(process.env.DIFY_VERIFY_INPUT)); }
  catch { result = { passed: false, error_code: 'browser_initialization_failed' }; }
  console.log(JSON.stringify({ passed: result.passed, error_code: result.error_code }));
  process.exitCode = result.passed ? 0 : 1;
}
