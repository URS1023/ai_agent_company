"use strict";
// 只读取当前构建入口及静态依赖，避免页面加载间接触发任何业务请求。
const fs = require("node:fs"), path = require("node:path"), assert = require("node:assert/strict");
const root = path.resolve(__dirname, "../../apps/web/assets/analytics");
const visited = new Set(), pending = [path.join(root, "index.html")];
while (pending.length) {
  const current = pending.pop(); if (visited.has(current)) continue;
  assert(fs.existsSync(current), "缺少当前入口依赖：" + current); visited.add(current);
  if (!/\.(?:html|js|css)$/.test(current)) continue;
  const source = fs.readFileSync(current, "utf8");
  for (const match of source.matchAll(/["'(]((?:\.\.?\/|\/assets\/analytics\/|assets\/)[^"'()\s]+\.(?:js|css|woff2?|ttf|svg|png|jpg))(?:["')])/g)) {
    const relative = match[1], dependency = relative.startsWith("/assets/analytics/") ? path.join(root, relative.slice(18))
      : relative.startsWith("assets/") ? path.join(root, relative) : path.resolve(path.dirname(current), relative);
    assert(dependency.startsWith(root + path.sep), "资源路径越界：" + dependency);
    pending.push(dependency);
  }
}
console.log(`PASS 当前 Analytics 构建入口与 ${visited.size - 1} 项静态依赖完整；没有HTTP/数据库/模型请求`);
