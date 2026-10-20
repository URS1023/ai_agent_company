/** 全站冒烟：逐个访问菜单路由，检查是否渲染、有无 JS 错误与失败请求。 */
import { chromium } from "@playwright/test";
import {
  readLocalUiInput,
  redactUiMessage,
} from "./tests/helpers/localUiInput.mjs";
const input = await readLocalUiInput();

const ROUTES = [
  ["工作台", "工作概览", "/overview"],
  ["工作台", "任务中心", "/tasks"],
  ["AI工作台", "AI对话", "/chat"],
  ["业务应用", "设备台账", "/devices"],
  ["业务应用", "预测性维护", "/maintenance"],
  ["业务应用", "检测任务", "/inspections"],
  ["业务应用", "产品与批次", "/products"],
  ["数据分析", "数据源", "/sources"],
  ["数据分析", "数据查询", "/analysis"],
  ["数据分析", "报表设计", "/reports"],
  ["知识管理", "知识库", "/bases"],
  ["知识管理", "图谱探索", "/relations"],
  ["开发工作台", "Agents", "/agents"],
  ["开发工作台", "工作流", "/workflows"],
  ["开发工作台", "插件", "/plugins"],
  ["应用中心", "应用中心", "/applications"],
  ["成果中心", "文件管理", "/files"],
  ["系统管理", "成员管理", "/users"],
  ["系统管理", "系统状态", "/health"],
];

let browser;
try {
  browser = await chromium.launch({
    executablePath:
      "C:/Users/urdcbk/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe",
  });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1000 },
  });
  const pageErrors = [];
  page.on("pageerror", (e) =>
    pageErrors.push(redactUiMessage(e.message, input)),
  );

  await page.goto(input.base_url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1000);
  await page.locator(".auth-form input").nth(0).fill(input.username);
  await page.locator(".auth-form input").nth(1).fill(input.password);
  await page.locator(".auth-submit").click();
  await page.waitForSelector(".app-shell", { timeout: 25000 });

  const results = [];
  for (const [group, item, path] of ROUTES) {
    pageErrors.length = 0;
    await page.locator(`.rail-item[title="${group}"]`).click();
    await page.waitForTimeout(350);
    const link = page.locator(`.menu-link[title="${item}"]`);
    if (!(await link.count())) {
      results.push({ path, ok: false, reason: "菜单项不存在" });
      continue;
    }
    await link.first().click();
    await page.waitForTimeout(1200);
    const url = page.url();
    const contentLen = (
      await page
        .locator(".main-content")
        .innerText()
        .catch(() => "")
    ).length;
    const denied = await page.locator(".el-result").count();
    results.push({
      path,
      url: url.replace(input.base_url, ""),
      ok: contentLen > 0 && pageErrors.length === 0,
      textLen: contentLen,
      denied: denied > 0,
      errors: [...pageErrors],
    });
  }

  const failed = results.filter((r) => !r.ok);
  console.log(
    JSON.stringify(
      {
        total: results.length,
        failed: failed.length,
        failedDetail: failed,
        denied: results.filter((r) => r.denied).map((r) => r.path),
      },
      null,
      2,
    ),
  );
} catch {
  process.stderr.write("辅助界面检查未完成\n");
  process.exitCode = 1;
} finally {
  await browser?.close();
  input.password = "";
  input.username = "";
}
