/*
 * 中文说明（已配置模型提供方的运行时探测审计）：
 * 验证行为：通过界面触发已配置模型提供方的连通探测，核对返回状态。
 * 约束：只探测，不修改任何模型设置（不保存配置、不改密钥）。
 * 运行方式：需在跑的服务 + 本机浏览器；探测会产生外部请求与计量。
 */
"use strict";
// Real configured provider probes through the UI. Does not edit model settings.
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const puppeteer = require("puppeteer-core");
const { login, waitUiResponse } = require("./browser_only_helpers");
const BASE = process.env.TEST5_E2E_BASE_URL;
const OUTPUT = path.resolve(".implementation/ui-results/lan-runtime-" + Date.now());
const checks = [], errors = [], requests = [];
let browser;
(async () => {
  assert.equal(BASE, "https://192.168.6.16:8443");
  fs.mkdirSync(OUTPUT, { recursive: true });
  browser = await puppeteer.launch({ executablePath: process.env.TEST5_EDGE_PATH, headless: true });
  const page = await browser.newPage();
  page.on("pageerror", error => errors.push(error.message));
  page.on("requestfailed", req => requests.push({ url: new URL(req.url()).pathname, error: req.failure()?.errorText }));
  await login(page, BASE, process.env.TEST5_E2E_PASSWORD);
  await page.goto(BASE + "/ai", { waitUntil: "networkidle0" });
  await page.waitForSelector("#voice-input-btn", { visible: true });
  const browserCapabilities = await page.evaluate(() => ({
    secureContext: isSecureContext,
    microphoneApi: Boolean(navigator.mediaDevices?.getUserMedia),
    speechRecognition: Boolean(window.SpeechRecognition || window.webkitSpeechRecognition),
  }));
  checks.push({ name: "HTTPS browser capabilities", ...browserCapabilities });
  assert(browserCapabilities.secureContext && browserCapabilities.microphoneApi);
  console.log("PASS HTTPS secure context and microphone API available");
  await page.goto(BASE + "/system/models", { waitUntil: "networkidle0" });
  await page.waitForSelector("[data-probe-model]", { visible: true });
  const profiles = await page.$$eval("[data-probe-model]", buttons => buttons
    .filter(button => !button.disabled && button.getClientRects().length)
    .map(button => button.getAttribute("data-probe-model")));
  for (const profile of profiles) {
    try {
      await page.goto(BASE + "/system/models", { waitUntil: "networkidle0" });
      await page.waitForFunction(name => {
        const button = [...document.querySelectorAll('[data-probe-model]')].find(node => node.getAttribute('data-probe-model') === name);
        return button && !button.disabled && button.getClientRects().length;
      }, { timeout: 30000 }, profile);
      const button = await page.$(`[data-probe-model="${profile}"]`);
      await button.scrollIntoView();
      const response = await waitUiResponse(page,
        (r, url) => url.pathname === "/api/models/probe/" + encodeURIComponent(profile) && r.request().method() === "POST",
        () => button.click(), 120000);
      const result = await response.json();
      const passed = response.status() === 200 && result.status === "passed";
      checks.push({ name: "configured model", profile, passed, httpStatus: response.status(), status: result.status || "error" });
      console.log(`${passed ? "PASS" : "FAIL"} actual model probe ${profile}: HTTP ${response.status()} ${result.status || "error"}`);
    } catch (error) {
      checks.push({ name: "configured model", profile, passed: false, status: "probe-did-not-complete", error: error.message });
      console.log("FAIL actual model probe " + profile + ": did not complete");
    }
  }
  await page.screenshot({ path: path.join(OUTPUT, "model-checks.png"), fullPage: true });
})().catch(error => {
  errors.push(error.message); console.error(error.message); process.exitCode = 1;
}).finally(async () => {
  await browser?.close();
  fs.mkdirSync(OUTPUT, { recursive: true });
  fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({ base: BASE, checks, errors, requests }, null, 2));
  console.log("REPORT " + OUTPUT);
  if (checks.some(check => check.passed === false) || errors.length) process.exitCode = 1;
});
