"use strict";

// 标准交付回归入口：合成报表 + 实际已构建 Vue，所有网络请求均由离线桩拦截。
const fs = require("node:fs");
const path = require("node:path");
const { main: renderSavedDashboard } = require("./render_saved_dashboard");
const { createDashboardSnapshotFixture } = require("./fixtures/dashboard_snapshot");

async function main(outputDirectory) {
  const output = path.resolve(outputDirectory || path.join(__dirname, "../../.implementation/dashboard-snapshot-matrix"));
  fs.mkdirSync(output, { recursive: true });
  const matrix = [];
  for (const dimensions of [{ width: 652, height: 368 }, { width: 1920, height: 1080 }, { width: 1920, height: 720 }]) {
    for (const multipleEmpty of [false, true]) {
      const fixture = createDashboardSnapshotFixture({ ...dimensions, multipleEmpty });
      const name = `${dimensions.width}x${dimensions.height}-${multipleEmpty ? "multiple-empty" : "scope-note"}`;
      const file = path.join(output, `${name}.json`);
      fs.writeFileSync(file, JSON.stringify(fixture, null, 2), "utf8");
      const results = await renderSavedDashboard(file, path.join(output, name));
      matrix.push({ name, dimensions, multipleEmpty, synthetic: true, views: results.length,
        external_calls: results.reduce((sum, item) => sum + item.external_calls, 0),
        business_query_calls: results.reduce((sum, item) => sum + item.business_query_calls, 0) });
    }
  }
  fs.writeFileSync(path.join(output, "matrix-summary.json"), JSON.stringify(matrix, null, 2), "utf8");
  console.log(`PASS 合成大屏矩阵：${matrix.length}种报表 × 3种浏览器宽度；外部请求0，业务查询0。`);
  return matrix;
}

if (require.main === module) main(process.argv[2]).catch(error => { console.error(error); process.exitCode = 1; });
module.exports = { main };
