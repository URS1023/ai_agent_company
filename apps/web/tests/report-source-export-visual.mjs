/** 三个工厂夹具的离线导出检查：1浏览器/60秒，所有网络阻断，0API/SQL/模型。 */
import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const output = resolve(process.argv[2] ?? "");
const base = resolve(repo, ".local/acceptance/r34-20261021");
assert.equal(dirname(output), base, "offline_output_outside_acceptance");
assert.match(output.split(/[\\/]/).at(-1), /^report-export-visual-[a-z0-9-]+$/);
const started = performance.now();
const requested = process.argv.slice(3);
const reportNames = requested.length
  ? requested
  : ["gauges", "calendar", "cards"];
assert.ok(
  reportNames.length <= 3 &&
    new Set(reportNames).size === reportNames.length &&
    reportNames.every((name) => ["gauges", "calendar", "cards"].includes(name)),
  "offline_fixture_selection_invalid",
);
const receipt = {
  scope: "纯工厂HTML样式回归；不是企业样本或新增真实业务验收",
  http_requests: 0,
  sql_queries: 0,
  model_calls: 0,
  screenshots: [],
  checks: [],
};
let browser,
  context,
  stopped = false;
const deadline = setTimeout(() => {
  stopped = true;
  void browser?.close();
}, 60000);
try {
  browser = await chromium.launch({
    headless: true,
    executablePath:
      process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ??
      "C:/Program Files/Google/Chrome/Application/chrome.exe",
  });
  context = await browser.newContext({
    viewport: { width: 1400, height: 1000 },
    javaScriptEnabled: false,
    reducedMotion: "reduce",
    serviceWorkers: "block",
  });
  await context.route("**/*", async (route) => {
    stopped = true;
    receipt.http_requests++;
    await route.abort("blockedbyclient");
  });
  for (const name of reportNames) {
    assert.ok(
      !stopped && performance.now() - started < 60000,
      "offline_render_stopped",
    );
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    await page.setContent(
      await readFile(resolve(output, `${name}.html`), "utf8"),
      { waitUntil: "load", timeout: 10000 },
    );
    const state = await page.evaluate(() => {
      const canvas = document.querySelector("main");
      const rgb = (value) =>
        value
          .match(/[\d.]+/g)
          .slice(0, 3)
          .map(Number);
      const luminance = (value) =>
        rgb(value)
          .map((channel) => {
            const fraction = channel / 255;
            return fraction <= 0.04045
              ? fraction / 12.92
              : ((fraction + 0.055) / 1.055) ** 2.4;
          })
          .reduce(
            (total, value, index) =>
              total + value * [0.2126, 0.7152, 0.0722][index],
            0,
          );
      const background = luminance(getComputedStyle(canvas).backgroundColor);
      const contrast = (element) => {
        const foreground = luminance(getComputedStyle(element).color);
        return (
          (Math.max(background, foreground) + 0.05) /
          (Math.min(background, foreground) + 0.05)
        );
      };
      const inBounds = (element, parent) => {
        const rect = element.getBoundingClientRect(),
          box = parent.getBoundingClientRect();
        return (
          rect.top >= box.top - 1 &&
          rect.bottom <= box.bottom + 1 &&
          rect.left >= box.left - 1 &&
          rect.right <= box.right + 1
        );
      };
      const text = [
        ...canvas.querySelectorAll(
          "svg text,.source-history strong,.source-cards h3,.source-cards strong",
        ),
      ];
      const cards = [...canvas.querySelectorAll(".source-cards article")];
      return {
        min_contrast: text.length ? Math.min(...text.map(contrast)) : null,
        gauge_strokes: [...canvas.querySelectorAll("line[data-value]")].map(
          (element) => element.getAttribute("stroke"),
        ),
        cards: cards.length,
        cards_in_bounds: cards.every(
          (element) =>
            inBounds(element, element.closest("section")) &&
            element.scrollHeight <= element.clientHeight + 1,
        ),
        clipped_cards: cards.flatMap((element, index) =>
          inBounds(element, element.closest("section")) &&
          element.scrollHeight <= element.clientHeight + 1
            ? []
            : [
                {
                  index,
                  client_height: element.clientHeight,
                  scroll_height: element.scrollHeight,
                },
              ],
        ),
        event_labels: [...canvas.querySelectorAll(".source-event")].map(
          (element) => element.textContent,
        ),
        event_labels_in_bounds: [
          ...canvas.querySelectorAll(".source-event"),
        ].every((element) => inBounds(element, element.parentElement)),
        complete_agendas: [...document.querySelectorAll(".source-agenda")]
          .length,
        agenda_rows: [...document.querySelectorAll(".source-agenda tbody tr")]
          .length,
        agenda_after_canvas: [
          ...document.querySelectorAll(".source-agenda"),
        ].every(
          (element) =>
            element.getBoundingClientRect().top >=
            canvas.getBoundingClientRect().bottom,
        ),
        height: document.documentElement.scrollHeight,
      };
    });
    receipt.last_observation = { name, ...state };
    assert.ok(
      state.min_contrast === null || state.min_contrast >= 4.5,
      "source_export_foreground_unreadable",
    );
    assert.ok(
      state.cards_in_bounds && state.event_labels_in_bounds,
      "source_export_content_clipped",
    );
    if (name === "gauges")
      assert.deepEqual(state.gauge_strokes, ["#eaf8ff", "#eaf8ff"]);
    if (name === "calendar") {
      assert.equal(state.complete_agendas, 1);
      assert.equal(state.agenda_rows, 1);
      assert.ok(state.agenda_after_canvas);
      assert.ok(state.event_labels.every((text) => text === "月度复核"));
    }
    if (name === "cards") assert.equal(state.cards, 16);
    const screenshot = resolve(output, `export-${name}-fixed.png`);
    await page.screenshot({ path: screenshot, fullPage: true, timeout: 10000 });
    receipt.screenshots.push(screenshot);
    receipt.checks.push({ name, ...state });
    await page.close();
  }
  assert.equal(receipt.http_requests, 0);
  assert.ok(!stopped);
  receipt.passed = true;
} catch (error) {
  receipt.passed = false;
  receipt.error_code = /^[a-z][a-z0-9_]+$/.test(error?.message ?? "")
    ? error.message
    : "offline_visual_check_failed";
  process.exitCode = 1;
} finally {
  clearTimeout(deadline);
  await browser?.close();
  receipt.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  await writeFile(
    resolve(output, "offline-visual.json"),
    JSON.stringify(receipt, null, 2),
    { flag: "wx" },
  );
  console.log(JSON.stringify(receipt));
}
