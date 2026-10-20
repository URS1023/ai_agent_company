/** 纯内存覆盖Chromium路由可见/不可见两条附件链，不启动浏览器或HTTP。 */
import assert from "node:assert/strict";
import test from "node:test";
import { KnowledgeDownloadEvidence } from "./helpers/knowledgeDownloadEvidence.mjs";

const origin = "http://127.0.0.1:18080";
const pathname = "/api/v1/files/test-file/content";
const ticket = `${origin}${pathname}?expires=1900000000&signature=opaque-test-value`;
const original = Buffer.from("检查前先停止设备，确认断电后记录温度。");
function evidence() {
  const result = new KnowledgeDownloadEvidence(origin, pathname);
  result.reserveTicket(ticket);
  return result;
}

for (const routed of [false, true]) {
  test(`一次实际下载可验收，网络路由${routed ? "可见" : "不可见"}`, () => {
    const result = evidence();
    if (routed) result.recordRoute(ticket);
    result.recordDownload(ticket);
    result.verifyBytes(original, original);
    result.assertComplete();
    assert.deepEqual(result.snapshot(), {
      download_events: 1,
      routed_download_requests: Number(routed),
      reserved_download_http_upper_bound: 1,
      source_download_bytes_verified: true,
    });
  });
}

test("票据限本机具体原件，且签名URL不能被替换或重复使用", () => {
  for (const url of [
    ticket.replace("127.0.0.1", "192.0.2.1"),
    ticket.replace("test-file", "other-file"),
    `${origin}${pathname}?expires=1`,
    `${ticket}&extra=1`,
    `${ticket}#fragment`,
  ]) {
    const result = new KnowledgeDownloadEvidence(origin, pathname);
    assert.throws(
      () => result.reserveTicket(url),
      /^Error: source_download_ticket_invalid$/,
    );
  }
  const result = evidence();
  assert.throws(
    () => result.reserveTicket(ticket),
    /source_download_ticket_repeated/,
  );
  assert.throws(
    () =>
      result.recordDownload(
        ticket.replace("opaque-test-value", "different-value"),
      ),
    /source_download_url_mismatch/,
  );
});

test("重复路由/下载、缺少事件和字节不一致均返回固定错误码", () => {
  const result = evidence();
  result.recordRoute(ticket);
  assert.throws(
    () => result.recordRoute(ticket),
    /source_download_route_repeated/,
  );
  assert.throws(
    () => result.assertComplete(),
    /source_download_event_count_invalid/,
  );
  result.recordDownload(ticket);
  assert.throws(
    () => result.assertComplete(),
    /source_download_bytes_unverified/,
  );
  assert.throws(
    () => result.verifyBytes(Buffer.from("不相同"), original),
    /source_download_not_original/,
  );
  assert.throws(
    () => result.recordDownload(ticket),
    /source_download_event_repeated/,
  );
});
