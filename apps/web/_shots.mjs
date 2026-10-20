/** 视觉抽查：截取工作概览与设备质检页，确认新样式在业务页上的实际观感。 */
import { chromium } from "@playwright/test";
import { readLocalUiInput } from "./tests/helpers/localUiInput.mjs";
const input = await readLocalUiInput();
import { mkdirSync } from "node:fs";

const OUT = "E:/code/Yun_tian/new_agent/ai_agent_company/.local/shots";
mkdirSync(OUT, { recursive: true });

let browser;
try {
  browser = await chromium.launch({
    executablePath:
      "C:/Users/urdcbk/AppData/Local/ms-playwright/chromium-1234/chrome-win64/chrome.exe",
  });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1000 },
  });
  await page.goto(input.base_url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1000);
  await page.locator(".auth-form input").nth(0).fill(input.username);
  await page.locator(".auth-form input").nth(1).fill(input.password);
  await page.locator(".auth-submit").click();
  await page.waitForSelector(".app-shell", { timeout: 25000 });
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${OUT}/r7-overview.png` });

  await page.locator('.rail-item[title="业务应用"]').click();
  await page.waitForTimeout(400);
  await page.locator('.menu-link[title="检测任务"]').click();
  await page.waitForTimeout(2000);
  await page.screenshot({ path: `${OUT}/r7-inspections.png` });

  await page.locator('.rail-item[title="数据分析"]').click();
  await page.waitForTimeout(400);
  await page.locator('.menu-link[title="报表设计"]').click();
  await page.waitForTimeout(2500);
  await page.screenshot({ path: `${OUT}/r7-reports.png` });

  console.log("已截图");
} catch {
  process.stderr.write("辅助界面检查未完成\n");
  process.exitCode = 1;
} finally {
  await browser?.close();
  input.password = "";
  input.username = "";
}
