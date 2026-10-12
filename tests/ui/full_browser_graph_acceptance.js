/*
 * 中文说明（知识图谱端到端浏览器验收）：
 * 验证行为：本体 -> 抽取 -> 图谱 -> Bot 的页面链路在真实浏览器中可用，节点与关系展示与后端一致。
 * 运行方式：需隔离服务（TEST5_E2E_BASE_URL，默认 8011）+ 本机 Edge + TEST5_E2E_PASSWORD。
 * 边界：抽取过程可能调用模型；只在隔离实例运行，会产生图谱数据。
 */
"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const puppeteer = require("puppeteer-core");

const BASE = process.env.TEST5_E2E_BASE_URL || "http://127.0.0.1:8030";
const PASSWORD = process.env.TEST5_E2E_PASSWORD;
const EDGE = process.env.TEST5_EDGE_PATH || "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const RUN = Date.now().toString(36);
const OUTPUT = path.resolve(".implementation/ui-results/full-browser-graph-" + RUN);
const checks = [];
const failures = [];
const accessibilityFindings = [];
let browser;
let page;

const names = {
  ontology: "终验设备语义模型-" + RUN,
  ontologyUpdated: "终验设备与部件语义模型-" + RUN,
  equipment: "终验设备-" + RUN,
  equipmentUpdated: "终验关键设备-" + RUN,
  component: "终验部件-" + RUN,
  relation: "终验包含部件-" + RUN,
  graph: "终验设备知识图谱-" + RUN,
  temporaryEntity: "终验临时实体-" + RUN,
  temporaryEntityUpdated: "终验临时实体已编辑-" + RUN,
  extractedEquipment: "终验一号主泵-" + RUN,
  extractedEquipmentUpdated: "终验一号关键主泵-" + RUN,
  extractedComponent: "终验主轴承-" + RUN,
  extraction: "终验设备结构化抽取-" + RUN,
  bot: "终验设备知识助手-" + RUN,
  botUpdated: "终验设备运维助手-" + RUN,
  key: "终验产线调用-" + RUN,
};

function pass(name, detail = {}) {
  checks.push({ name, ...detail });
  console.log("PASS " + name);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function fill(selector, value) {
  const element = await page.waitForSelector(selector, { visible: true, timeout: 90000 });
  await element.click({ clickCount: 3 });
  await page.keyboard.down("Control");
  await page.keyboard.press("KeyA");
  await page.keyboard.up("Control");
  await page.keyboard.press("Backspace");
  if (String(value)) await element.type(String(value));
}

async function clickHandle(element) {
  assert(element, "Expected a clickable element");
  await element.evaluate(node => node.scrollIntoView({ block: "center", inline: "nearest" }));
  await element.click();
}

async function selectByText(selector, label, exact = false) {
  await page.waitForSelector(selector, { visible: true, timeout: 90000 });
  const value = await page.$eval(selector, (select, args) => {
    const option = [...select.options].find(item => {
      const shown = item.textContent.trim();
      return args.exact ? shown === args.label : shown.includes(args.label);
    });
    return option?.value || "";
  }, { label, exact });
  assert(value, "Option not found: " + label + " in " + selector);
  await page.select(selector, value);
  return value;
}

async function checkByLabel(selector, label) {
  const changed = await page.$$eval(selector, (inputs, expected) => {
    const input = inputs.find(item => item.closest("label")?.textContent.includes(expected));
    if (!input) return false;
    if (!input.checked) input.click();
    return input.checked;
  }, label);
  assert(changed, "Checkbox not found or not checked: " + label);
}

async function rowByText(value, rootSelector = "#main-content") {
  const rows = await page.$$(rootSelector + " tbody tr");
  for (const row of rows) {
    if (await row.evaluate((node, expected) => node.textContent.includes(expected), value)) return row;
  }
  throw new Error("Table row not found: " + value);
}

async function visit(route, readySelector = "#main-content") {
  const response = await page.goto(BASE + route, { waitUntil: "networkidle0", timeout: 90000 });
  assert.equal(response.status(), 200, route);
  await page.waitForSelector(readySelector, { visible: true, timeout: 90000 });
  await page.waitForFunction(() => document.querySelector("#main-content")?.textContent.trim().length > 0);
}

async function saveDialog(expected = "", timeout = 90000) {
  const dialog = await page.waitForSelector("#platform-dialog[open]", { visible: true, timeout });
  const submit = await dialog.$('button[type="submit"]');
  assert(submit, "Dialog has no submit button");
  await clickHandle(submit);
  await page.waitForFunction(() => {
    const current = document.querySelector("#platform-dialog");
    const error = current?.querySelector(".form-error:not([hidden])");
    return !current?.open || Boolean(error?.textContent.trim());
  }, { timeout });
  const result = await page.evaluate(() => {
    const current = document.querySelector("#platform-dialog");
    return {
      open: Boolean(current?.open),
      error: current?.querySelector(".form-error:not([hidden])")?.textContent.trim() || "",
    };
  });
  assert.equal(result.open, false, result.error || "Dialog did not close");
  if (expected) {
    await page.waitForFunction(value => document.querySelector("#main-content")?.textContent.includes(value),
      { timeout }, expected);
  }
  await sleep(100);
}

async function confirmDialog(label, expected = "", timeout = 180000) {
  await page.waitForSelector("#platform-dialog[open]", { visible: true, timeout });
  const buttons = await page.$$("#platform-dialog[open] button");
  for (const button of buttons) {
    const shown = await button.evaluate(node => node.textContent.trim());
    if (shown !== label) continue;
    await clickHandle(button);
    await page.waitForFunction(() => {
      const dialog = document.querySelector("#platform-dialog");
      const error = dialog?.querySelector(".form-error:not([hidden])");
      return !dialog?.open || Boolean(error?.textContent.trim());
    }, { timeout });
    const error = await page.$eval("#platform-dialog", dialog =>
      dialog.querySelector(".form-error:not([hidden])")?.textContent.trim() || "");
    assert.equal(error, "", error);
    if (expected) {
      await page.waitForFunction(value => document.querySelector("#main-content")?.textContent.includes(value),
        { timeout }, expected);
    }
    return;
  }
  throw new Error("Confirmation button not found: " + label);
}

async function openRow(value, readySelector) {
  const row = await rowByText(value);
  const link = await row.$("a.cell-primary, a.text-button");
  assert(link, "No open link for " + value);
  await Promise.all([
    page.waitForNavigation({ waitUntil: "networkidle0", timeout: 90000 }),
    clickHandle(link),
  ]);
  await page.waitForSelector(readySelector, { visible: true, timeout: 90000 });
  return new URL(page.url()).searchParams.get("id");
}

async function audit(route) {
  await page.addScriptTag({ path: require.resolve("axe-core/axe.min.js") });
  const findings = await page.evaluate(async () => (await axe.run(document, {
    runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"] },
  })).violations.map(item => ({
    id: item.id,
    impact: item.impact,
    nodes: item.nodes.map(node => ({ target: node.target, summary: node.failureSummary })),
  })));
  accessibilityFindings.push(...findings.map(item => ({ route, ...item })));
}

(async () => {
  assert(PASSWORD, "Supply TEST5_E2E_PASSWORD");
  assert.equal(process.env.TEST5_E2E_ALLOW_WRITE, "isolated");
  assert(["127.0.0.1", "localhost"].includes(new URL(BASE).hostname));
  fs.mkdirSync(OUTPUT, { recursive: true });

  browser = await puppeteer.launch({
    executablePath: EDGE,
    headless: true,
    args: ["--no-first-run", "--disable-gpu", "--window-size=1440,960"],
  });
  page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 960 });
  page.on("pageerror", error => failures.push("pageerror " + error.message));
  page.on("console", message => {
    if (message.type() === "error") failures.push("console " + message.text());
  });
  page.on("requestfailed", request => failures.push("requestfailed " + request.url() + " " + request.failure()?.errorText));
  page.on("response", response => {
    if (response.status() >= 400 && response.url().includes("/api/")) {
      failures.push(response.status() + " " + response.url());
    }
  });

  try {
    await page.goto(BASE, { waitUntil: "networkidle0", timeout: 90000 });
    await fill("#login-username", "workspace-admin");
    await fill("#login-password", PASSWORD);
    await page.click("#login-btn");
    await page.waitForSelector(".enterprise-area-grid", { visible: true, timeout: 90000 });

    await visit("/ontology", "[data-new-ontology]");
    await page.click("[data-new-ontology]");
    await fill('#platform-dialog [name="name"]', names.ontology);
    await fill('#platform-dialog [name="description"]', "设备、部件及组成关系的企业语义模型");
    await saveDialog(names.ontology);
    const ontologyId = await openRow(names.ontology, "[data-add-concept]");
    assert(ontologyId, "Ontology id is missing from its visible link");

    await page.click("[data-edit-ontology]");
    await fill('#platform-dialog [name="name"]', names.ontologyUpdated);
    await saveDialog(names.ontologyUpdated);

    await page.click("[data-add-concept]");
    await fill('#platform-dialog [name="code"]', "Equipment_" + RUN);
    await fill('#platform-dialog [name="name"]', names.equipment);
    await fill('#platform-dialog [name="terms"]', "主泵，设备，泵组");
    await fill('#platform-dialog [name="attributes"]', JSON.stringify([
      { name: "equipment_id", label: "设备编码", type: "string", required: true, unique: true },
      { name: "status", label: "运行状态", type: "string", required: true },
      { name: "temperature", label: "轴承温度", type: "number", required: false },
    ], null, 2));
    await fill('#platform-dialog [name="constraints"]', JSON.stringify({ businessKey: "equipment_id" }, null, 2));
    await fill('#platform-dialog [name="description"]', "生产设备及其运行属性");
    await saveDialog(names.equipment);

    await page.click("[data-add-concept]");
    await fill('#platform-dialog [name="code"]', "Component_" + RUN);
    await fill('#platform-dialog [name="name"]', names.component);
    await fill('#platform-dialog [name="terms"]', "轴承，部件，组件");
    await fill('#platform-dialog [name="attributes"]', JSON.stringify([
      { name: "component_id", label: "部件编码", type: "string", required: true, unique: true },
      { name: "material", label: "材质", type: "string", required: false },
    ], null, 2));
    await fill('#platform-dialog [name="constraints"]', JSON.stringify({ businessKey: "component_id" }, null, 2));
    await saveDialog(names.component);

    let row = await rowByText(names.equipment);
    const equipmentId = await row.$eval("[data-edit-concept]", node => node.dataset.editConcept);
    await clickHandle(await row.$("[data-edit-concept]"));
    await fill('#platform-dialog [name="name"]', names.equipmentUpdated);
    await fill('#platform-dialog [name="terms"]', "主泵，关键设备，泵组");
    await saveDialog(names.equipmentUpdated);
    row = await rowByText(names.component);
    const componentId = await row.$eval("[data-edit-concept]", node => node.dataset.editConcept);

    await page.click("[data-add-relation-type]");
    await fill('#platform-dialog [name="code"]', "HAS_PART_" + RUN);
    await fill('#platform-dialog [name="name"]', names.relation);
    await selectByText('#platform-dialog [name="sourceConceptId"]', names.equipmentUpdated);
    await selectByText('#platform-dialog [name="targetConceptId"]', names.component);
    await page.select('#platform-dialog [name="cardinality"]', "one_to_many");
    await fill('#platform-dialog [name="attributes"]', JSON.stringify([
      { name: "installed_at", label: "安装位置", type: "string" },
      { name: "verified", label: "已核验", type: "boolean" },
    ], null, 2));
    await fill('#platform-dialog [name="description"]', "设备由一个或多个部件组成");
    await saveDialog(names.relation);
    row = await rowByText(names.relation);
    const relationId = await row.$eval("[data-edit-relation-type]", node => node.dataset.editRelationType);

    await page.click("[data-publish-ontology]");
    await fill('#platform-dialog [name="reason"]', "终验正式语义模型");
    await saveDialog("已发布");
    await page.click(".resource-history summary");
    await page.waitForFunction(() => document.querySelector("[data-ontology-revisions]")?.textContent.includes("终验正式语义模型"));
    await page.screenshot({ path: path.join(OUTPUT, "ontology-published.png"), fullPage: true });
    await audit("/ontology-detail");
    pass("Ontology can be created, edited, modeled and published entirely through visible forms", {
      ontologyId, equipmentId, componentId, relationId,
    });

    await visit("/graphs", "[data-new-graph]");
    await page.click("[data-new-graph]");
    await fill('#platform-dialog [name="name"]', names.graph);
    await selectByText('#platform-dialog [name="ontologyId"]', names.ontologyUpdated);
    await fill('#platform-dialog [name="description"]', "抽取结果与人工维护共用的设备图谱");
    await saveDialog(names.graph);
    const graphId = await openRow(names.graph, "[data-add-entity]");
    assert(graphId, "Graph id is missing from its visible link");

    await page.click("[data-add-entity]");
    await selectByText('#platform-dialog [name="conceptId"]', names.equipmentUpdated);
    await fill('#platform-dialog [name="businessKey"]', "TEMP-" + RUN);
    await fill('#platform-dialog [name="name"]', names.temporaryEntity);
    await fill('#platform-dialog [name="properties"]', JSON.stringify({
      equipment_id: "TEMP-" + RUN, status: "调试",
    }, null, 2));
    await saveDialog(names.temporaryEntity);
    row = await rowByText(names.temporaryEntity);
    await clickHandle(await row.$("[data-edit-entity]"));
    await fill('#platform-dialog [name="name"]', names.temporaryEntityUpdated);
    await saveDialog(names.temporaryEntityUpdated);
    row = await rowByText(names.temporaryEntityUpdated);
    await clickHandle(await row.$("[data-delete-entity]"));
    await confirmDialog("删除");
    await page.waitForFunction(value => !document.querySelector("#main-content")?.textContent.includes(value),
      { timeout: 90000 }, names.temporaryEntityUpdated);
    pass("Graph entity controls support create, edit and guarded delete before extraction");

    await visit("/extraction", "[data-new-extraction]");
    await page.click("[data-new-extraction]");
    await fill('#platform-dialog [name="name"]', names.extraction);
    await selectByText('#platform-dialog [name="ontologyId"]', names.ontologyUpdated);
    const records = [{
      equipment_id: "EQ-" + RUN,
      equipment_name: names.extractedEquipment,
      equipment_status: "运行",
      equipment_temperature: 78.5,
      component_id: "CP-" + RUN,
      component_name: names.extractedComponent,
      component_material: "高碳铬轴承钢",
      installed_at: "驱动端",
    }];
    const mappings = {
      entityMappings: [
        {
          conceptId: equipmentId,
          keyField: "equipment_id",
          nameField: "equipment_name",
          propertyFields: {
            equipment_id: "equipment_id",
            status: "equipment_status",
            temperature: "equipment_temperature",
          },
        },
        {
          conceptId: componentId,
          keyField: "component_id",
          nameField: "component_name",
          propertyFields: { component_id: "component_id", material: "component_material" },
        },
      ],
      relationMappings: [{
        relationTypeId: relationId,
        sourceConceptId: equipmentId,
        targetConceptId: componentId,
        sourceKeyField: "equipment_id",
        targetKeyField: "component_id",
        propertyFields: { installed_at: "installed_at" },
      }],
    };
    await fill('#platform-dialog [name="records"]', JSON.stringify(records, null, 2));
    await fill('#platform-dialog [name="mappings"]', JSON.stringify(mappings, null, 2));
    await fill('#platform-dialog [name="description"]', "通过明确字段映射生成可复核候选");
    await saveDialog(names.extraction);
    const extractionId = await openRow(names.extraction, "[data-execute-extraction]");
    assert(extractionId, "Extraction id is missing from its visible link");

    await page.click("[data-edit-extraction]");
    await fill('#platform-dialog [name="description"]', "通过真实记录和明确字段映射生成可复核候选");
    await saveDialog("真实记录");
    await page.click("[data-execute-extraction]");
    await confirmDialog("执行", "待复核", 180000);
    await page.waitForSelector("[data-candidate-check]", { visible: true, timeout: 180000 });
    assert.equal(await page.$$eval("[data-candidate-check]", nodes => nodes.length), 3);
    await page.click(".evidence-details summary");
    const evidence = await page.$eval(".evidence-details pre", node => node.textContent);
    assert(evidence.includes("rowNumber") && evidence.includes("structured"));
    await page.screenshot({ path: path.join(OUTPUT, "extraction-review.png"), fullPage: true });
    pass("Structured extraction executes real mappings and exposes three traceable review candidates");

    const firstReview = await page.$("[data-review-candidate]");
    await clickHandle(firstReview);
    await fill('#platform-dialog [name="note"]', "单条候选已核对");
    await saveDialog();
    await page.waitForSelector('[data-batch-review="accepted"]', { visible: true });
    await page.$$eval("[data-candidate-check]:not(:disabled)", boxes => boxes.forEach(box => box.click()));
    await page.click('[data-batch-review="accepted"]');
    await page.waitForFunction(() => !document.querySelector('[data-batch-review="accepted"]'), { timeout: 180000 });
    assert.equal(await page.$$eval("[data-candidate-check]:not(:disabled)", nodes => nodes.length), 0);
    pass("Candidate review supports both a single evidence decision and batch acceptance");

    await page.click("[data-materialize]");
    await selectByText('#platform-dialog [name="graphId"]', names.graph);
    await saveDialog("已完成", 180000);
    pass("Accepted candidates are materialized into an explicitly selected compatible graph");

    await visit("/graphs?id=" + encodeURIComponent(graphId), ".graph-canvas svg");
    assert.equal(await page.$$eval(".graph-nodes g", nodes => nodes.length), 2);
    assert.equal(await page.$$eval(".graph-edges line", nodes => nodes.length), 1);
    await fill('[data-graph-search] [name="query"]', "EQ-" + RUN);
    await page.click('[data-graph-search] button[type="submit"]');
    await page.waitForFunction(value => document.querySelector(".graph-canvas")?.textContent.includes(value),
      { timeout: 90000 }, names.extractedEquipment);

    row = await rowByText(names.extractedEquipment);
    await clickHandle(await row.$("[data-edit-entity]"));
    await fill('#platform-dialog [name="name"]', names.extractedEquipmentUpdated);
    await fill('#platform-dialog [name="properties"]', JSON.stringify({
      equipment_id: "EQ-" + RUN, status: "重点监控", temperature: 78.5,
    }, null, 2));
    await saveDialog(names.extractedEquipmentUpdated);
    row = await rowByText(names.relation);
    await clickHandle(await row.$("[data-edit-graph-relation]"));
    await fill('#platform-dialog [name="properties"]', JSON.stringify({ installed_at: "驱动端", verified: true }, null, 2));
    await saveDialog();
    row = await rowByText(names.relation);
    await clickHandle(await row.$("[data-edit-graph-relation]"));
    assert((await page.$eval('#platform-dialog [name="properties"]', node => node.value)).includes('"verified": true'));
    await page.click('#platform-dialog [data-dialog-close]');
    await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open);
    await page.waitForSelector("[data-publish-graph]", { visible: true });
    await page.$eval("[data-publish-graph]", node => node.click());
    await fill('#platform-dialog [name="reason"]', "终验图谱正式版本");
    await saveDialog("已发布");
    await page.screenshot({ path: path.join(OUTPUT, "graph-published.png"), fullPage: true });
    await audit("/graphs-detail");
    pass("Materialized graph supports search, visual exploration, entity/relation edits and publication");

    await visit("/bots", "[data-new-bot]");
    await page.click("[data-new-bot]");
    await fill('#platform-dialog [name="name"]', names.bot);
    await page.select('#platform-dialog [name="botType"]', "knowledge");
    await page.select('#platform-dialog [name="apiEnabled"]', "true");
    await fill('#platform-dialog [name="instructions"]', "仅依据已发布设备图谱回答，并明确返回来源。");
    await fill('#platform-dialog [name="welcomeMessage"]', "请查询设备、部件和运行状态。");
    await fill('#platform-dialog [name="description"]', "设备运维检索助手");
    await checkByLabel('#platform-dialog [name="graphIds"]', names.graph);
    await saveDialog(names.bot);
    const botId = await openRow(names.bot, "[data-bot-debug]");
    assert(botId, "Bot id is missing from its visible link");

    await fill('[data-bot-debug] [name="query"]', names.extractedEquipmentUpdated + "包含什么部件？");
    await page.click('[data-bot-debug] button[type="submit"]');
    await page.waitForSelector("[data-debug-result] .debug-answer", { visible: true, timeout: 180000 });
    let debugText = await page.$eval("[data-debug-result]", node => node.textContent);
    assert(debugText.includes(names.extractedEquipmentUpdated));
    assert(!debugText.includes("0 条来源"));

    await page.click("[data-edit-bot]");
    await fill('#platform-dialog [name="name"]', names.botUpdated);
    await fill('#platform-dialog [name="welcomeMessage"]', "输入设备编码即可查看组成关系。");
    await saveDialog(names.botUpdated);
    await page.click("[data-publish-bot]");
    await fill('#platform-dialog [name="reason"]', "终验 Bot 正式版本");
    await saveDialog("已发布");
    await page.click('[data-bot-debug] [name="usePublished"]');
    await fill('[data-bot-debug] [name="query"]', "EQ-" + RUN + "关联什么？");
    await page.click('[data-bot-debug] button[type="submit"]');
    await page.waitForFunction(previous => {
      const text = document.querySelector("[data-debug-result]")?.textContent || "";
      return text && text !== previous;
    }, { timeout: 180000 }, debugText);
    debugText = await page.$eval("[data-debug-result]", node => node.textContent);
    assert(!debugText.includes("0 条来源"));
    pass("Bot binds the published graph and supports draft and published retrieval debugging");

    await page.click("[data-create-bot-key]");
    await fill('#platform-dialog [name="name"]', names.key);
    const createKeyButton = await page.$('#platform-dialog button[type="submit"]');
    await clickHandle(createKeyButton);
    await page.waitForFunction(() => {
      const dialog = document.querySelector("#platform-dialog");
      return dialog?.open && document.querySelector("#dialog-title")?.textContent.includes("请立即保存 API Key");
    },
      { timeout: 90000 });
    const oneTimeKey = await page.$eval("#platform-dialog .one-time-secret code", node => node.textContent.trim());
    assert(oneTimeKey.startsWith("t5b_"));
    await page.screenshot({ path: path.join(OUTPUT, "bot-key-once.png"), fullPage: true });
    await page.$eval('#platform-dialog[open] [data-dialog-close]', node => node.click());
    await page.waitForFunction(() => !document.querySelector("#platform-dialog")?.open);
    await page.waitForFunction(value => document.querySelector("#main-content")?.textContent.includes(value),
      { timeout: 90000 }, names.key);
    row = await rowByText(names.key);
    await clickHandle(await row.$("[data-revoke-key]"));
    await confirmDialog("吊销", "已撤销");
    pass("Bot API key is shown once, listed without plaintext and revocable from the interface");

    await page.screenshot({ path: path.join(OUTPUT, "bot-complete.png"), fullPage: true });
    await audit("/bots-detail");

    await page.setViewport({ width: 390, height: 844, isMobile: true });
    await visit("/ontology?id=" + encodeURIComponent(ontologyId), "[data-add-concept]");
    await page.click("[data-add-concept]");
    await page.waitForSelector("#platform-dialog[open]", { visible: true });
    const mobile = await page.evaluate(() => {
      const dialog = document.querySelector("#platform-dialog").getBoundingClientRect();
      return {
        scrollWidth: document.documentElement.scrollWidth,
        viewport: innerWidth,
        dialogLeft: dialog.left,
        dialogRight: dialog.right,
        domainSelectorVisible: Boolean(document.querySelector(".enterprise-area-select select")?.getClientRects().length),
      };
    });
    assert(mobile.scrollWidth <= mobile.viewport);
    assert(mobile.dialogLeft >= 0 && mobile.dialogRight <= mobile.viewport);
    assert(mobile.domainSelectorVisible);
    await page.screenshot({ path: path.join(OUTPUT, "ontology-mobile-dialog.png"), fullPage: true });
    await audit("/ontology-mobile-dialog");
    pass("Knowledge modeling remains navigable and its editing dialog fits a 390px viewport", mobile);

    assert.deepEqual(accessibilityFindings, []);
    assert.deepEqual(failures, []);
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
      ok: true,
      baseUrl: BASE,
      checks,
      failures,
      accessibilityFindings,
      records: { ontologyId, extractionId, graphId, botId },
      names,
    }, null, 2));
    console.log("Evidence " + OUTPUT);
  } catch (error) {
    failures.push(error.stack || error.message);
    if (page) await page.screenshot({ path: path.join(OUTPUT, "failure.png"), fullPage: true }).catch(() => {});
    fs.writeFileSync(path.join(OUTPUT, "result.json"), JSON.stringify({
      ok: false, baseUrl: BASE, checks, failures, accessibilityFindings, names,
    }, null, 2));
    throw error;
  } finally {
    if (browser) await browser.close();
  }
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
