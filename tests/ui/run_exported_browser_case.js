/*
 * 中文说明（导出式浏览器用例适配器）：
 * 重复验收器通过子进程运行只导出函数的离线用例，避免在一个 Node 进程内
 * 累积浏览器实例或全局状态。参数只接受 tests/ui 下的文件名，并拒绝路径穿越。
 */
"use strict";

const assert = require("node:assert/strict");
const path = require("node:path");

async function main() {
  const name = process.argv[2];
  assert(name && /^[a-zA-Z0-9_-]+\.js$/.test(name), "仅允许传入 tests/ui 下的 JS 用例文件名");
  const file = path.resolve(__dirname, name);
  assert(file.startsWith(path.resolve(__dirname) + path.sep), "浏览器用例路径必须位于 tests/ui");
  const verify = require(file);
  assert.equal(typeof verify, "function", `${name} 必须导出可调用的浏览器用例函数`);
  await verify();
}

if (require.main === module) main().catch(error => { console.error(error.stack || error); process.exitCode = 1; });

