/**
 * 专属知识空间真实UI验收：100业务HTTP/180秒，1小文件、1导入、1语义检索、0LLM/业务源。
 * 仅接受verify_knowledge_ui.py核验本机归属后从stdin传入的账号；不在文件或截图保存密钥。
 * 状态只允许6次手动读取，不重建/重新导入/重试检索；入口次数不冒称上游实际向量HTTP次数。
 */
import { chromium } from "playwright";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { KnowledgeDownloadEvidence } from "./helpers/knowledgeDownloadEvidence.mjs";

const repo = resolve(dirname(fileURLToPath(import.meta.url)), "../../..");
const dataDirectory = resolve(repo, ".local/acceptance/r34-20261021");
const origin = "http://127.0.0.1:18080";
const started = performance.now();
const maximumTime = 180000;
const summary = {
  scope: "本轮新本机平台与专属Dify知识库；1小文本导入/1检索入口；0LLM/0业务源",
  origin,
  started_at: new Date().toISOString(),
  api_requests: 0,
  api_budget: 100,
  api_request_counting:
    "platform_requests_plus_one_reserved_download_http_upper_bound",
  static_requests: 0,
  document_upload_attempts: 0,
  document_import_attempts: 0,
  semantic_search_attempts: 0,
  embedding_operation_upper_bound: 0,
  upstream_embedding_http_count: null,
  entry_operations_limited_only: true,
  native_retry_policy_verified: false,
  observed_total_segments: null,
  manual_status_refreshes: 0,
  status_reads: 0,
  business_source_queries: 0,
  llm_requests: 0,
  requires_native_index_stop_check: false,
  checks: [],
  fixture_ids: {},
  screenshots: [],
  expected_rejections: [],
  blocked_requests: [],
  failures: [],
};
let browser, context, page, input, fixture, uploaded, imported, latestDocuments;
let csrf = "",
  phase = "validate_fixture",
  output = "",
  documentName = "";
let stopped = false,
  closing = false,
  indexComplete = false,
  searchComplete = false;
let downloadEvidence;
const citations = new Set();
const downloads = new Set();

function active() {
  if (stopped || performance.now() - started >= maximumTime) {
    stopped = true;
    throw new Error("verification_stopped");
  }
}
function timeout() {
  active();
  return Math.max(
    1,
    Math.min(10000, maximumTime - (performance.now() - started)),
  );
}
function countRequest() {
  active();
  assert.ok(summary.api_requests < summary.api_budget, "http_budget_exhausted");
  summary.api_requests++;
}
function base() {
  return `/knowledge-spaces/${fixture.space_id}`;
}
function fileBase() {
  return uploaded ? `/files/${uploaded.id}` : "";
}
function emptyBody(body) {
  return body && Object.keys(body).length === 0;
}
function sameKeys(body, keys) {
  return body && Object.keys(body).sort().join() === [...keys].sort().join();
}
function allowed(method, url, body, request) {
  const path = url.pathname.slice(7);
  if (method === "GET") {
    if (["/auth/status", "/me", "/permissions/navigation"].includes(path))
      return !url.search;
    if (["/integration-instances", "/knowledge-spaces"].includes(path))
      return [...url.searchParams].every(
        ([key, value]) => key === "limit" && value === "100",
      );
    if (
      [base(), `/integration-instances/${fixture.integration_id}`].includes(
        path,
      )
    )
      return !url.search;
    if (path === `${base()}/documents`)
      return (
        summary.status_reads < 10 &&
        url.searchParams.get("page") === "1" &&
        url.searchParams.get("limit") === "20" &&
        [...url.searchParams].length === 2
      );
    if (path === "/files")
      return (
        phase === "upload_text" &&
        url.searchParams.get("limit") === "50" &&
        (url.searchParams.get("q") ?? "") === "" &&
        [...url.searchParams].every(([key]) => ["limit", "q"].includes(key))
      );
    if (uploaded && path === fileBase()) return !url.search;
    if (uploaded && path === `${fileBase()}/content`)
      return downloadEvidence?.allowsRoute(url.href);
    if (imported && path === `/knowledge-documents/${imported.id}/citations`)
      return !url.search;
    return [...citations].some(
      (id) => path === `/knowledge-citations/${id}` && !url.search,
    );
  }
  if (method === "POST" && path === "/auth/login")
    return (
      !url.search &&
      sameKeys(body, ["username", "password"]) &&
      body.username === input.username &&
      body.password === input.password
    );
  if (method === "POST" && path === "/files") {
    // 文件只来自内存夹具；不允许其他附件、业务数据或第二次上传。
    const raw = request?.postDataBuffer();
    return (
      phase === "upload_text" &&
      !url.search &&
      !summary.document_upload_attempts &&
      /multipart\/form-data; boundary=/.test(
        request?.headers()["content-type"] ?? "",
      ) &&
      raw &&
      raw.length < 4096 &&
      raw.includes(Buffer.from(fixture.text)) &&
      raw.includes(Buffer.from(`filename="${fixture.run_id}.txt"`)) &&
      raw.toString("utf8").split('name="file"; filename=').length === 2
    );
  }
  if (method === "POST" && path === `${base()}/document-imports`)
    return (
      !url.search &&
      phase === "import_once" &&
      !summary.document_import_attempts &&
      uploaded &&
      sameKeys(body, ["file_id", "name"]) &&
      body.file_id === uploaded.id &&
      body.name === documentName
    );
  if (method === "POST" && path === "/knowledge-searches")
    return (
      !url.search &&
      phase === "search_once" &&
      !summary.semantic_search_attempts &&
      indexComplete &&
      sameKeys(body, ["space_ids", "query", "top_k", "score_threshold"]) &&
      JSON.stringify(body.space_ids) === JSON.stringify([fixture.space_id]) &&
      body.query === fixture.query &&
      body.top_k === 1 &&
      body.score_threshold === 0
    );
  if (
    uploaded &&
    method === "POST" &&
    [
      `${fileBase()}/download-links`,
      `${fileBase()}/deletion-previews`,
    ].includes(path)
  )
    return !url.search && emptyBody(body);
  if (uploaded && method === "DELETE" && path === fileBase())
    return (
      phase === "source_delete_protection" &&
      url.searchParams.get("revision") === String(uploaded.revision) &&
      [...url.searchParams].length === 1
    );
  return false;
}
function claim(method, path) {
  if (method === "GET" && path === `${fileBase()}/content`) return;
  countRequest();
  if (method === "GET" && path === `${base()}/documents`)
    summary.status_reads++;
  if (method === "POST" && path === "/files")
    summary.document_upload_attempts++;
  if (method === "POST" && path === `${base()}/document-imports`) {
    summary.document_import_attempts++;
    summary.embedding_operation_upper_bound++;
  }
  if (method === "POST" && path === "/knowledge-searches") {
    summary.semantic_search_attempts++;
    summary.embedding_operation_upper_bound++;
  }
}
function capture(method, path, body) {
  if (method === "POST" && path === "/files") {
    assert.ok(
      body.id && Number.isInteger(body.revision),
      "uploaded_file_identity_missing",
    );
    assert.equal(
      body.size,
      Buffer.byteLength(fixture.text),
      "uploaded_file_size_mismatch",
    );
    assert.equal(
      body.sha256,
      createHash("sha256").update(fixture.text).digest("hex"),
      "uploaded_file_hash_mismatch",
    );
    uploaded = body;
    downloadEvidence = new KnowledgeDownloadEvidence(
      origin,
      `/api/v1${fileBase()}/content`,
    );
    summary.fixture_ids.file = body.id;
  } else if (method === "POST" && path === `${base()}/document-imports`) {
    assert.ok(
      body.id && body.native_document_id,
      "native_document_identity_missing",
    );
    assert.equal(body.space_id, fixture.space_id, "wrong_import_space");
    assert.equal(body.file_id, uploaded.id, "wrong_import_file");
    assert.equal(
      body.source_sha256,
      uploaded.sha256,
      "import_source_hash_missing",
    );
    assert.equal(
      body.source_revision,
      uploaded.revision,
      "import_source_revision_mismatch",
    );
    imported = body;
    summary.fixture_ids.document = body.id;
    summary.fixture_ids.native_document = body.native_document_id;
  } else if (method === "GET" && path === `${base()}/documents`) {
    assert.ok(Array.isArray(body.data), "native_documents_missing");
    latestDocuments = body;
    if (imported) {
      const document = body.data.find(
        (row) => row.id === imported.native_document_id,
      );
      if (document) {
        const mapping = body.source_mappings?.[document.id];
        assert.equal(
          mapping?.id,
          imported.id,
          "document_source_mapping_missing",
        );
        assert.equal(
          mapping?.file_id,
          uploaded.id,
          "document_source_file_mismatch",
        );
        for (const key of [
          "total_segments",
          "completed_segments",
          "segment_count",
        ]) {
          if (document[key] !== undefined && document[key] !== null) {
            const count = Number(document[key]);
            assert.ok(
              Number.isInteger(count) && count >= 0 && count <= 1,
              "single_segment_budget_exceeded",
            );
            if (key !== "completed_segments" || count > 0)
              summary.observed_total_segments = count;
          }
        }
      }
    }
  } else if (method === "POST" && path === "/knowledge-searches") {
    assert.ok(
      Array.isArray(body.records) && body.records.length === 1,
      "retrieval_result_missing",
    );
    const result = body.records[0];
    assert.equal(result.space_id, fixture.space_id, "retrieval_wrong_space");
    assert.equal(
      result.segment?.document_id ?? result.segment?.document?.id,
      imported.native_document_id,
      "retrieval_wrong_document",
    );
    assert.ok(
      result.citation_id &&
        result.segment?.content?.includes(fixture.expected_phrase),
      "retrieval_original_text_missing",
    );
    if (result.segment.position !== undefined)
      assert.equal(result.segment.position, 1, "retrieval_not_single_segment");
    citations.add(result.citation_id);
    searchComplete = true;
    summary.fixture_ids.citation = result.citation_id;
  } else if (method === "POST" && path === `${fileBase()}/download-links`) {
    const url = new URL(body.url, origin);
    assert.equal(url.origin, origin, "download_outside_owned_origin");
    assert.equal(
      url.pathname,
      `/api/v1${fileBase()}/content`,
      "wrong_source_download",
    );
    assert.ok(
      url.searchParams.has("signature") && url.searchParams.has("expires"),
      "unsigned_source_download",
    );
    downloadEvidence.reserveTicket(url.href);
    // 先为附件预留1次HTTP；Chrome下载管理器可能不触发route，路由可见时也不重复计数。
    countRequest();
  }
}
async function apiCall(method, path, body, expectedCode) {
  const url = new URL(`/api/v1${path}`, origin);
  assert.ok(allowed(method, url, body), "api_outside_fixture_scope");
  claim(method, url.pathname.slice(7));
  let response;
  try {
    response = await context.request.fetch(url.href, {
      method,
      ...(body === undefined ? {} : { data: body }),
      headers: { "X-CSRF-Token": csrf, Origin: origin },
      timeout: timeout(),
      maxRetries: 0,
      maxRedirects: 0,
    });
  } catch {
    stopped = true;
    throw new Error("api_timeout_or_connection");
  }
  const status = response.status();
  if (expectedCode) {
    assert.equal(status, 409, "source_delete_not_rejected");
    const result = await response.json();
    assert.equal(result.error?.code, expectedCode, "wrong_delete_rejection");
    summary.expected_rejections.push({
      method,
      path: url.pathname.slice(7),
      status,
      code: expectedCode,
    });
    await response.dispose();
    return result;
  }
  if (!response.ok()) {
    stopped = true;
    summary.failures.push({
      phase,
      method,
      path: url.pathname.slice(7),
      status,
    });
    throw new Error("unexpected_api_failure");
  }
  const result = await response.json();
  capture(method, url.pathname.slice(7), result);
  await response.dispose();
  return result;
}
async function installGuards() {
  await context.route("**/*", async (route) => {
    const request = route.request(),
      url = new URL(request.url()),
      method = request.method();
    const apiPath = url.pathname.startsWith("/api/v1/")
      ? url.pathname.slice(7)
      : null;
    let body;
    try {
      body = request.postDataJSON();
    } catch {
      body = undefined;
    }
    const safeAsset =
      apiPath === null &&
      method === "GET" &&
      !url.search &&
      (url.pathname === "/bases" ||
        url.pathname.startsWith("/assets/") ||
        /^\/[A-Za-z0-9_./-]+\.(?:png|jpe?g|svg|ico|css|js|woff2?|ttf)$/.test(
          url.pathname,
        ));
    if (
      stopped ||
      url.origin !== origin ||
      url.protocol !== "http:" ||
      !(safeAsset || (apiPath && allowed(method, url, body, request)))
    ) {
      stopped = true;
      summary.blocked_requests.push({ phase, method, path: url.pathname });
      await route.abort("blockedbyclient");
      return;
    }
    try {
      if (apiPath) claim(method, apiPath);
      else {
        assert.ok(summary.static_requests < 400, "static_budget_exhausted");
        summary.static_requests++;
      }
      if (apiPath === `${fileBase()}/content`)
        downloadEvidence.recordRoute(url.href);
      const response = await route.fetch({
        timeout: timeout(),
        maxRetries: 0,
        maxRedirects: 0,
      });
      if (!response.ok()) throw new Error("unexpected_ui_http_status");
      if (apiPath && !apiPath.endsWith("/content"))
        capture(method, apiPath, await response.json());
      await route.fulfill({ response });
      await response.dispose();
    } catch {
      stopped = true;
      summary.failures.push({
        phase,
        method,
        path: url.pathname,
        code: "ui_http_or_contract_failed",
      });
      await route.abort("failed").catch(() => {});
    }
  });
  await context.routeWebSocket("**/*", (socket) => {
    stopped = true;
    summary.blocked_requests.push({ phase, method: "WEBSOCKET" });
    socket.close({ code: 1008, reason: "outside_e2e_scope" });
  });
  context.on("page", (created) => {
    if (page && created !== page) {
      stopped = true;
      summary.failures.push({ phase, code: "unexpected_second_page" });
      void created.close();
    }
  });
  page.on("pageerror", () => {
    stopped = true;
    summary.failures.push({ phase, code: "page_error" });
  });
  page.on("requestfailed", (request) => {
    // Chromium将附件导航转交下载管理器时可能报告ERR_ABORTED，下载字节仍须在后续逐字核对。
    if (
      !closing &&
      !(
        downloadEvidence?.matches(request.url()) &&
        request.failure()?.errorText === "net::ERR_ABORTED"
      )
    ) {
      stopped = true;
      summary.failures.push({ phase, code: "network_failure" });
    }
  });
  page.on("download", (download) => {
    downloads.add(download);
    try {
      active();
      downloadEvidence.recordDownload(download.url());
    } catch {
      stopped = true;
      summary.failures.push({
        phase,
        code: "download_event_outside_fixture_scope",
      });
      void download.cancel().catch(() => {});
    }
  });
}
function responseFor(method, path) {
  active();
  return page.waitForResponse(
    (response) =>
      response.request().method() === method &&
      new URL(response.url()).pathname === `/api/v1${path}`,
    { timeout: timeout() },
  );
}
async function uiResult(pending) {
  const response = await pending;
  assert.ok(response.ok(), "ui_operation_failed");
  active();
  return response.json();
}
function drawer() {
  return page.locator(".el-drawer:visible");
}
function documentRow() {
  return drawer().locator(".el-table__row").filter({ hasText: documentName });
}
function field(container, label) {
  return container
    .locator(".el-form-item")
    .filter({ has: page.getByText(label, { exact: true }) })
    .first();
}
async function visible(locator) {
  await locator.waitFor({ state: "visible", timeout: timeout() });
  active();
}
async function cleanView() {
  await page
    .locator(
      ".el-loading-mask:visible,.el-message:visible,.el-button.is-loading:visible",
    )
    .first()
    .waitFor({ state: "hidden", timeout: timeout() });
  active();
}
async function screenshot(name) {
  await cleanView();
  const safe = await page.evaluate(() => {
    const sensitive =
      /PRIVATE KEY|service_[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./;
    return (
      !sensitive.test(document.body.innerText) &&
      ![...document.querySelectorAll("input,textarea")].some((input) =>
        sensitive.test(input.value),
      ) &&
      ![localStorage, sessionStorage].some((storage) =>
        Object.values(storage).some((value) => sensitive.test(value)),
      )
    );
  });
  assert.ok(safe, "sensitive_browser_state");
  const path = resolve(output, `${name}.png`);
  await page.screenshot({ path, timeout: timeout() });
  summary.screenshots.push(path);
}
function documentState() {
  const document = latestDocuments?.data?.find(
    (row) => row.id === imported.native_document_id,
  );
  assert.ok(document, "imported_document_missing");
  assert.equal(document.name, documentName, "document_name_mismatch");
  const state = document.archived
    ? "archived"
    : document.enabled === false
      ? "disabled"
      : ["paused", "disabled", "archived"].includes(document.display_status)
        ? document.display_status
        : document.indexing_status || document.display_status;
  assert.ok(
    !["error", "failed", "paused", "stopped", "disabled", "archived"].includes(
      state,
    ),
    "native_indexing_failed",
  );
  assert.ok(
    [
      "completed",
      "available",
      "waiting",
      "queuing",
      "parsing",
      "cleaning",
      "splitting",
      "indexing",
    ].includes(state),
    "native_indexing_unknown",
  );
  return state;
}
async function manualRefresh() {
  assert.ok(
    summary.manual_status_refreshes < 6,
    "manual_status_budget_exhausted",
  );
  active();
  assert.ok(
    maximumTime - (performance.now() - started) > 3000,
    "deadline_before_status_read",
  );
  await new Promise((resolve) => setTimeout(resolve, 3000));
  active();
  summary.manual_status_refreshes++;
  const pending = responseFor("GET", `${base()}/documents`);
  await drawer().getByRole("button", { name: "刷新状态", exact: true }).click();
  await uiResult(pending);
}
async function verifyUi() {
  phase = "open_knowledge_workspace";
  const loaded = responseFor("GET", "/knowledge-spaces");
  await page.goto(`${origin}/bases`, { timeout: timeout() });
  await uiResult(loaded);
  const card = page.locator(".knowledge-card").filter({
    has: page.getByRole("heading", { name: fixture.space_name, exact: true }),
  });
  await visible(card);
  const documents = responseFor("GET", `${base()}/documents`);
  await card.getByRole("button", { name: "文档管理", exact: true }).click();
  const empty = await uiResult(documents);
  assert.equal(empty.total, 0, "dedicated_dataset_not_empty");
  assert.equal(empty.data.length, 0, "dedicated_dataset_contains_documents");
  summary.checks.push("专属空间初始为空且文档入口可用");

  phase = "upload_text";
  const filesLoaded = responseFor("GET", "/files");
  await drawer().getByRole("button", { name: "导入文件", exact: true }).click();
  await uiResult(filesLoaded);
  const dialog = page.getByRole("dialog", { name: "导入文档", exact: true });
  await visible(dialog);
  assert.ok(
    await dialog
      .getByRole("button", { name: "导入", exact: true })
      .isDisabled(),
    "missing_file_not_disabled",
  );
  const upload = responseFor("POST", "/files");
  await dialog.locator('input[type="file"]').setInputFiles({
    name: `${fixture.run_id}.txt`,
    mimeType: "text/plain",
    buffer: Buffer.from(fixture.text),
  });
  await uiResult(upload);
  await field(dialog, "文档名称").locator("input").fill(documentName);
  assert.ok(
    await dialog.getByRole("button", { name: "导入", exact: true }).isEnabled(),
    "uploaded_file_cannot_import",
  );
  summary.checks.push("缺少文件时导入禁用；UTF8小文件真实上传且SHA一致");

  phase = "import_once";
  const importedResponse = responseFor("POST", `${base()}/document-imports`);
  const afterImport = responseFor("GET", `${base()}/documents`);
  await dialog.getByRole("button", { name: "导入", exact: true }).click();
  await uiResult(importedResponse);
  await uiResult(afterImport);
  await dialog.waitFor({ state: "hidden", timeout: timeout() });
  phase = "indexing_status";
  while (
    !["completed", "available"].includes(documentState()) &&
    summary.manual_status_refreshes < 6
  )
    await manualRefresh();
  assert.ok(
    ["completed", "available"].includes(documentState()),
    "native_indexing_not_completed_within_read_budget",
  );
  if (summary.observed_total_segments !== null)
    assert.equal(
      summary.observed_total_segments,
      1,
      "completed_index_not_single_segment",
    );
  indexComplete = true;
  await visible(documentRow().getByText("可检索", { exact: true }));
  summary.checks.push("单次导入后真实索引状态完成，来源映射保留原件版本和SHA");
  await screenshot("knowledge-documents");

  phase = "search_once";
  await drawer().getByRole("tab", { name: "检索", exact: true }).click();
  const search = drawer().locator(".knowledge-search");
  assert.ok(
    await search
      .getByRole("button", { name: "检索", exact: true })
      .isDisabled(),
    "empty_query_not_disabled",
  );
  await search.getByRole("button", { name: "检索设置", exact: true }).click();
  await field(search, "返回条数").getByRole("spinbutton").fill("1");
  await search
    .getByRole("textbox", { name: "知识检索内容", exact: true })
    .fill(fixture.query);
  const searched = responseFor("POST", "/knowledge-searches");
  await search.getByRole("button", { name: "检索", exact: true }).click();
  await uiResult(searched);
  const result = search.locator(".search-result");
  await visible(result.first());
  assert.equal(await result.count(), 1, "unexpected_visible_search_count");
  assert.ok(
    (await result.locator("p").innerText()).includes(fixture.expected_phrase),
    "retrieval_text_not_visible",
  );
  summary.checks.push("一次语义检索返回专属原文，空查询禁用且返回条数为1");
  await screenshot("knowledge-search");

  phase = "citation_detail";
  const citationId = summary.fixture_ids.citation;
  const citationResponse = responseFor(
    "GET",
    `/knowledge-citations/${citationId}`,
  );
  await result.getByRole("button", { name: "引用详情", exact: true }).click();
  const citation = await uiResult(citationResponse);
  assert.equal(citation.space_id, fixture.space_id, "citation_wrong_space");
  assert.equal(
    citation.native_document_id,
    imported.native_document_id,
    "citation_wrong_document",
  );
  assert.ok(
    citation.content?.includes(fixture.expected_phrase),
    "citation_original_missing",
  );
  const citationDialog = page.getByRole("dialog", {
    name: "引用详情",
    exact: true,
  });
  await visible(citationDialog.locator(".citation"));
  assert.ok(
    (await citationDialog.locator(".citation p").innerText()).includes(
      fixture.expected_phrase,
    ),
    "citation_original_not_visible",
  );
  await screenshot("knowledge-citation");
  await citationDialog.locator(".el-dialog__headerbtn").click();
  await citationDialog.waitFor({ state: "hidden", timeout: timeout() });
  summary.checks.push("检索引用详情与真实原生文档一致，原文在页面显示");

  phase = "document_citations";
  const returning = responseFor("GET", `${base()}/documents`);
  await drawer().getByRole("tab", { name: "文档", exact: true }).click();
  await uiResult(returning);
  const documentCitations = responseFor(
    "GET",
    `/knowledge-documents/${imported.id}/citations`,
  );
  await documentRow()
    .getByRole("button", { name: "引用", exact: true })
    .click();
  const referenced = await uiResult(documentCitations);
  assert.ok(
    referenced.items?.some(
      (row) =>
        row.id === citationId && row.content?.includes(fixture.expected_phrase),
    ),
    "document_citation_not_registered",
  );
  const originalDialog = page.getByRole("dialog", {
    name: `${documentName} · 引用`,
    exact: true,
  });
  await visible(originalDialog.locator(".citation"));
  await originalDialog.locator(".el-dialog__headerbtn").click();
  await originalDialog.waitFor({ state: "hidden", timeout: timeout() });

  phase = "download_original";
  const downloadPending = page.waitForEvent("download", { timeout: timeout() });
  await documentRow()
    .getByRole("button", { name: "源文件", exact: true })
    .click();
  const download = await downloadPending;
  downloads.add(download);
  assert.equal(await download.failure(), null, "source_download_failed");
  const stream = await download.createReadStream();
  assert.ok(stream, "download_stream_missing");
  const chunks = [];
  let size = 0;
  for await (const chunk of stream) {
    active();
    size += chunk.length;
    assert.ok(size <= 600, "source_download_size_exceeded");
    chunks.push(chunk);
  }
  downloadEvidence.verifyBytes(
    Buffer.concat(chunks),
    Buffer.from(fixture.text),
  );
  await download.delete();
  downloads.delete(download);
  summary.checks.push("源文件按钮真实下载，UTF8字节与上传原件完全一致");

  phase = "source_delete_protection";
  const metadata = await apiCall("GET", fileBase());
  assert.equal(metadata.sha256, uploaded.sha256, "file_hash_changed");
  assert.equal(metadata.revision, uploaded.revision, "file_revision_changed");
  const impact = await apiCall("POST", `${fileBase()}/deletion-previews`, {});
  assert.equal(impact.can_delete, false, "referenced_source_deletable");
  assert.ok(
    impact.references?.some(
      (row) => row.type === "knowledge_document" && row.id === imported.id,
    ),
    "knowledge_reference_missing",
  );
  await apiCall(
    "DELETE",
    `${fileBase()}?revision=${uploaded.revision}`,
    undefined,
    "file_in_use",
  );
  const preserved = await apiCall("GET", fileBase());
  assert.equal(
    preserved.revision,
    uploaded.revision,
    "rejected_delete_mutated_source",
  );
  assert.equal(
    preserved.sha256,
    uploaded.sha256,
    "rejected_delete_changed_bytes",
  );
  summary.checks.push("原件删除预检发现知识引用，实际删除409拒绝且原文件不变");
}

const deadline = setTimeout(() => {
  stopped = true;
  summary.failures.push({ phase, code: "overall_deadline" });
  void browser?.close().catch(() => {});
}, maximumTime);
try {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of process.stdin) {
    bytes += chunk.length;
    assert.ok(bytes <= 32768, "stdin_budget_exhausted");
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks);
  input = JSON.parse(raw.toString("utf8"));
  raw.fill(0);
  chunks.length = 0;
  fixture = input.fixture;
  assert.ok(
    input.source === "verified-knowledge-ui-helper-v1" &&
      input.origin === origin &&
      input.ownership?.verified === true &&
      input.ownership.component === "r34-acceptance" &&
      Number.isInteger(input.ownership.platform_pid) &&
      input.ownership.platform_pid > 0,
    "verified_local_proof_required",
  );
  assert.equal(
    resolve(input.ownership.data_directory).toLowerCase(),
    dataDirectory.toLowerCase(),
    "wrong_acceptance_directory",
  );
  assert.ok(
    typeof input.username === "string" &&
      input.username &&
      typeof input.password === "string" &&
      input.password,
    "encrypted_access_required",
  );
  assert.ok(
    fixture?.schema_version === 1 &&
      /^knowledge-[a-f0-9]{12}$/.test(fixture.run_id),
    "invalid_fixture_identity",
  );
  for (const key of ["space_id", "integration_id", "native_dataset_id"])
    assert.ok(
      /^[A-Za-z0-9_-]{8,100}$/.test(fixture[key]),
      "invalid_fixture_resource_id",
    );
  assert.ok(
    typeof fixture.text === "string" &&
      fixture.text.length >= 10 &&
      [...fixture.text].length <= 180 &&
      Buffer.byteLength(fixture.text) <= 600 &&
      !/[\r\n\0]/.test(fixture.text) &&
      typeof fixture.query === "string" &&
      fixture.query.length >= 2 &&
      fixture.query.length <= 120 &&
      typeof fixture.expected_phrase === "string" &&
      fixture.expected_phrase.length >= 4 &&
      fixture.text.includes(fixture.expected_phrase),
    "invalid_single_paragraph_fixture",
  );
  const proof = fixture.native_proof;
  assert.ok(
    proof?.verified === true &&
      proof.origin === "http://127.0.0.1:13001" &&
      proof.runtime_id &&
      proof.embedding_model === "BAAI/bge-m3" &&
      /siliconflow/i.test(proof.embedding_provider) &&
      proof.dataset_empty === true &&
      proof.maximum_segments === 1 &&
      proof.indexing_technique === "high_quality" &&
      proof.retrieval_method === "semantic_search" &&
      proof.reranking_enabled === false &&
      proof.llm_enabled === false,
    "native_dataset_proof_required",
  );
  const age = Date.now() - Date.parse(proof.checked_at);
  assert.ok(
    Number.isFinite(age) && age >= 0 && age <= 600000,
    "native_dataset_proof_expired",
  );
  output = resolve(input.output_directory);
  assert.equal(
    output.toLowerCase(),
    resolve(dataDirectory, fixture.run_id).toLowerCase(),
    "output_outside_run_directory",
  );
  documentName = `${fixture.run_id}-设备维护规程`;
  summary.native_retry_policy_verified =
    proof.native_retry_policy_verified === true;
  summary.platform_pid = input.ownership.platform_pid;
  summary.fixture_ids.space = fixture.space_id;
  summary.fixture_ids.dataset = fixture.native_dataset_id;
  summary.receipt = resolve(output, "knowledge-ui.json");
  browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE,
  });
  context = await browser.newContext({
    viewport: { width: 1640, height: 1100 },
    reducedMotion: "reduce",
    serviceWorkers: "block",
    acceptDownloads: true,
  });
  phase = "login_owned_platform";
  const me = await apiCall("POST", "/auth/login", {
    username: input.username,
    password: input.password,
  });
  assert.ok(me.permissions?.includes("*"), "isolated_admin_required");
  csrf = me.csrf_token;
  input.username = "";
  input.password = "";
  phase = "verify_dedicated_space";
  const space = await apiCall("GET", base());
  assert.equal(space.name, fixture.space_name, "space_name_mismatch");
  assert.equal(
    space.native_id,
    fixture.native_dataset_id,
    "dataset_binding_mismatch",
  );
  assert.equal(
    space.integration_id,
    fixture.integration_id,
    "integration_binding_mismatch",
  );
  assert.equal(space.enabled, true, "space_disabled");
  const integration = await apiCall(
    "GET",
    `/integration-instances/${fixture.integration_id}`,
  );
  assert.equal(integration.engine, "dify", "wrong_native_engine");
  assert.equal(integration.enabled, true, "integration_disabled");
  assert.equal(
    integration.base_url.replace(/\/$/, ""),
    `${proof.origin}/v1`,
    "integration_outside_owned_native_origin",
  );
  page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.setDefaultNavigationTimeout(10000);
  await installGuards();
  await verifyUi();
  assert.ok(
    summary.document_upload_attempts === 1,
    "final_upload_count_invalid",
  );
  assert.ok(
    summary.document_import_attempts === 1,
    "final_import_count_invalid",
  );
  assert.ok(
    summary.semantic_search_attempts === 1,
    "final_search_count_invalid",
  );
  assert.ok(
    summary.embedding_operation_upper_bound === 2,
    "final_embedding_entry_count_invalid",
  );
  downloadEvidence.assertComplete();
  assert.ok(
    summary.blocked_requests.length === 0,
    "final_blocked_request_present",
  );
  assert.ok(summary.failures.length === 0, "final_request_failure_present");
  active();
  summary.passed = true;
} catch (error) {
  stopped = true;
  summary.passed = false;
  summary.error_code = /^[a-z][a-z0-9_]{1,99}$/.test(error?.message ?? "")
    ? error.message
    : "knowledge_ui_verification_failed";
  summary.failed_phase = phase;
  // 不输出断言差异、请求正文或失败现场截图，避免意外泄露账号/签名下载地址。
} finally {
  clearTimeout(deadline);
  summary.requires_native_index_stop_check =
    summary.document_import_attempts > 0 &&
    (!indexComplete ||
      (summary.semantic_search_attempts > 0 && !searchComplete));
  csrf = "";
  if (downloadEvidence) Object.assign(summary, downloadEvidence.snapshot());
  if (input) {
    input.username = "";
    input.password = "";
  }
  closing = true;
  for (const download of downloads) await download.delete().catch(() => {});
  await browser?.close().catch(() => {});
  summary.elapsed_seconds =
    Math.round((performance.now() - started) / 100) / 10;
  const serialized = JSON.stringify(summary, null, 2);
  if (
    /PRIVATE KEY|service_[A-Za-z0-9_-]{30,}|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\./.test(
      serialized,
    )
  ) {
    console.log(
      JSON.stringify({ passed: false, error_code: "unsafe_receipt_rejected" }),
    );
    process.exitCode = 1;
  } else {
    if (summary.receipt)
      await writeFile(summary.receipt, serialized, { flag: "wx" });
    console.log(serialized);
    if (!summary.passed) process.exitCode = 1;
  }
}
