/*
 * 中文说明（离线浏览器重复验收门禁）：
 * 该脚本串行重复一组已经声明为离线的真实 Edge 用例，用于积累可审计的
 * 重复运行证据。默认不访问 8011、数据库、模型或远程网络；每个子进程
 * 只加载仓库中的前端静态资源和内存夹具。需要验收可写业务模块时，必须
 * 使用对应的 full_browser_* 用例和独立 APP_ENV=test 服务，不能把本脚本
 * 当作生产数据验收。
 *
 * 用法：
 *   node tests/ui/repeat_browser_acceptance.js --iterations 3
 *   node tests/ui/repeat_browser_acceptance.js --iterations 100 --cases dsh-chat,dashboard-preview
 *
 * 退出规则：单个用例失败立即停止后续轮次，并将 stdout/stderr 和汇总 JSON
 * 写入 .implementation/ui-results/repeat-browser-*。脚本不自动重试失败，
 * 防止偶发错误被重试掩盖。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const ROOT = path.resolve(__dirname, "../..");
const DEFAULT_CASES = ["dashboard-preview", "dsh-chat", "dsh-native", "markdown"];
const CASES = {
  // 完全由本地静态资源和内存 API 驱动，覆盖已保存大屏预览、重试和移动端布局。
  "dashboard-preview": { script: "chat_dashboard_preview.js", label: "离线大屏预览" },
  // DSH 外壳夹具覆盖排队、交付、追问、断线恢复和取消。
  "dsh-chat": { script: "dsh_chat_browser.js", label: "离线 DSH 对话" },
  // 原生 DSH 交互夹具覆盖连续 SSE、多选追问和交付状态。
  "dsh-native": { script: "dsh_native_interactions.js", label: "离线 DSH 原生交互" },
  // 生产 Markdown 增量渲染代码在真实 Edge 中执行，不依赖后端。
  markdown: { script: "run_exported_browser_case.js", args: ["chat_stream_markdown.js"], label: "离线 Markdown 流式渲染" },
  // Chat 外壳的离线分支会组合运行 DSH 对话、SSE 恢复和原生交互夹具。
  "chat-shell-offline": { script: "chat_shell.js", label: "离线 Chat 外壳" },
};

function argument(name, fallback) {
  const index = process.argv.indexOf(name);
  return index >= 0 && process.argv[index + 1] !== undefined ? process.argv[index + 1] : fallback;
}

function parsePositiveInteger(value, label) {
  const parsed = Number(value);
  assert(Number.isInteger(parsed) && parsed >= 1, `${label} 必须是大于等于 1 的整数`);
  return parsed;
}

function now() {
  return new Date().toISOString();
}

function runChild(entry, output, environment) {
  return new Promise((resolve) => {
    const commandArgs = [path.join(__dirname, entry.script), ...(entry.args || [])];
    const startedAt = Date.now();
    const child = spawn(process.execPath, commandArgs, {
      cwd: ROOT,
      env: { ...process.env, ...environment },
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", chunk => { stdout += chunk.toString(); });
    child.stderr.on("data", chunk => { stderr += chunk.toString(); });
    const timeoutMs = parsePositiveInteger(process.env.TEST5_UI_CASE_TIMEOUT_MS || "180000", "TEST5_UI_CASE_TIMEOUT_MS");
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      // 仅终止当前离线用例，不能让一个卡住的夹具阻塞后续人工验收。
      child.kill();
    }, timeoutMs);
    child.on("close", (code, signal) => {
      clearTimeout(timer);
      const result = {
        started_at: new Date(startedAt).toISOString(),
        finished_at: now(),
        elapsed_ms: Date.now() - startedAt,
        exit_code: code,
        signal,
        timed_out: timedOut,
        stdout,
        stderr,
      };
      fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(result, null, 2) + "\n");
      resolve(result);
    });
    child.on("error", error => {
      clearTimeout(timer);
      const result = {
        started_at: new Date(startedAt).toISOString(), finished_at: now(),
        elapsed_ms: Date.now() - startedAt, exit_code: null, signal: null,
        timed_out: false, stdout, stderr: `${stderr}${error.stack || error}`,
      };
      fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(result, null, 2) + "\n");
      resolve(result);
    });
  });
}

function selectedCases() {
  const raw = argument("--cases", process.env.TEST5_UI_CASES || DEFAULT_CASES.join(","));
  const names = raw.split(",").map(item => item.trim()).filter(Boolean);
  assert(names.length > 0, "至少选择一个浏览器用例");
  for (const name of names) assert(CASES[name], `未知浏览器用例：${name}`);
  return names;
}

async function main() {
  const iterations = parsePositiveInteger(argument("--iterations", process.env.TEST5_UI_ITERATIONS || "1"), "--iterations");
  assert(iterations <= 100, "重复轮次最多为 100；超过 100 请拆分为多次有记录的验收");
  const names = selectedCases();
  const run = `${Date.now().toString(36)}-${process.pid}`;
  const output = path.resolve(ROOT, ".implementation", "ui-results", `repeat-browser-${run}`);
  fs.mkdirSync(output, { recursive: true });
  const manifest = {
    schema_version: 1,
    started_at: now(),
    mode: "offline-browser-fixtures",
    isolation: {
      database: false,
      model_provider: false,
      service_8011: false,
      remote_network: false,
      browser: "本机 Microsoft Edge",
    },
    requested_iterations: iterations,
    cases: names.map(name => ({ name, label: CASES[name].label, script: CASES[name].script })),
    stop_policy: "first failure; no automatic retry",
    results: [],
  };
  fs.writeFileSync(path.join(output, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  console.log(`REPEAT_OUTPUT ${output}`);
  console.log(`REPEAT_SCOPE ${iterations} 轮 × ${names.length} 个离线浏览器用例；不会访问 8011、数据库或模型`);

  for (let iteration = 1; iteration <= iterations; iteration += 1) {
    for (const name of names) {
      const entry = CASES[name];
      const caseOutput = path.join(output, `iteration-${String(iteration).padStart(3, "0")}`, name);
      fs.mkdirSync(caseOutput, { recursive: true });
      const result = await runChild(entry, caseOutput, {
        // 只有 chat_shell 的离线分支会读取此变量；其它用例忽略它。
        TEST5_E2E_DSH_OFFLINE: "1",
        TEST5_E2E_OUTPUT: caseOutput,
      });
      const record = { iteration, case: name, label: entry.label, ...result };
      manifest.results.push(record);
      fs.writeFileSync(path.join(output, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
      const passed = result.exit_code === 0 && !result.signal && !result.timed_out;
      console.log(`${passed ? "PASS" : "FAIL"} iteration=${iteration} case=${name} elapsed_ms=${result.elapsed_ms}`);
      if (!passed) {
        console.error(`浏览器重复验收在第 ${iteration} 轮的 ${name} 用例停止；证据目录：${caseOutput}`);
        process.exitCode = 1;
        return;
      }
    }
  }
  manifest.finished_at = now();
  manifest.status = "passed";
  fs.writeFileSync(path.join(output, "manifest.json"), JSON.stringify(manifest, null, 2) + "\n");
  console.log(`PASS 全部离线浏览器用例完成：${iterations} 轮 × ${names.length} 个用例`);
}

if (require.main === module) main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });
