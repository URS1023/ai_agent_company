/*
 * 中文说明（语音输入前端逻辑单元测试）：
 * 验证行为：在 vm 沙箱里加载 apps/web/app.js 的语音输入相关片段，验证安全上下文判断、
 *   错误码到提示文案的映射以及按钮状态复位。
 * 运行方式：node --test tests/ui/voice_input.test.cjs（纯 Node，无需浏览器与后端）。
 * 边界：不真正访问麦克风；只验证前端分支与文案，模型/后端不参与。
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const app = fs.readFileSync(path.resolve(__dirname, '../../apps/web/app.js'), 'utf8');
const source = app.slice(app.indexOf('function showVoiceInputError('), app.indexOf('function attachDroppedFiles('));
function setup({ secure = true, supported = true, throws = false } = {}) {
  const span = { textContent: '' };
  const panel = { hidden: true, querySelector: () => span };
  const button = { style: {}, setAttribute(name, value) { this[name] = value; } };
  const textarea = { value: '原文：', focus() {} };
  const recognizers = [];
  class Recognition {
    constructor() { recognizers.push(this); }
    start() { if (throws) { const e = new Error(); e.name = 'NotAllowedError'; throw e; } this.onstart(); }
    stop() { this.onend(); }
  }
  const context = vm.createContext({ window: { isSecureContext: secure, SpeechRecognition: supported ? Recognition : undefined }, location: { port: '8011' }, $: selector => ({ '#voice-input-feedback': panel, '#voice-input-btn': button, '#prompt-textarea': textarea })[selector], toast() {}, updateSendState() {} });
  vm.runInContext(source, context);
  return { context, span, panel, button, textarea, recognizers, start: () => vm.runInContext('startVoiceInput()', context) };
}
test('HTTP LAN guard explains localhost versus HTTPS without activating a microphone', () => {
  const s = setup({ secure: false }); s.start();
  assert.equal(s.recognizers.length, 0);
  assert.match(s.span.textContent, /localhost:8011\/ai/);
  assert.match(s.span.textContent, /其他电脑.*HTTPS/);
  assert.equal(s.panel.hidden, false);
});
test('permission denial and synchronous exceptions leave a reusable button and persistent instructions', () => {
  for (const throws of [false, true]) {
    const s = setup({ throws }); s.start();
    if (!throws) s.recognizers[0].onerror({ error: 'not-allowed' });
    assert.match(s.span.textContent, /网站设置.*允许/);
    assert.match(s.span.textContent, /Windows 设置/);
    assert.equal(s.button['aria-pressed'], 'false');
    s.start(); assert.equal(s.recognizers.length, 2);
  }
});
test('unsupported browsers and service/network errors receive distinct recovery instructions', () => {
  const s = setup({ supported: false }); s.start(); assert.match(s.span.textContent, /不支持网页语音识别/);
  const r = setup();
  for (const [code, expected] of [['network', /网络/], ['service-not-allowed', /语音策略/], ['audio-capture', /输入设备/], ['no-speech', /没有识别到/]]) {
    r.start(); r.recognizers.at(-1).onerror({ error: code }); assert.match(r.span.textContent, expected);
  }
});
test('second click stops the active session; recognition updates preserve earlier final segments', () => {
  const s = setup(); s.start();
  s.recognizers[0].onresult({ resultIndex: 0, results: [[{ transcript: '第一句。' }]] });
  s.recognizers[0].onresult({ resultIndex: 1, results: [[{ transcript: '第一句。' }], [{ transcript: '第二句。' }]] });
  assert.equal(s.textarea.value, '原文：第一句。第二句。');
  s.start(); assert.equal(s.recognizers.length, 1); assert.equal(s.button['aria-pressed'], 'false');
});
