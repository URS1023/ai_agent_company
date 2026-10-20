/** 附件下载可能绕过浏览器路由；统一以票据、下载事件和实际字节登记一次证据。 */
export class KnowledgeDownloadEvidence {
  constructor(origin, pathname) {
    this.origin = origin;
    this.pathname = pathname;
    this.ticket = "";
    this.routedRequests = 0;
    this.events = 0;
    this.bytesVerified = false;
  }

  reserveTicket(value) {
    if (this.ticket) throw new Error("source_download_ticket_repeated");
    const url = new URL(value, this.origin);
    if (
      url.origin !== this.origin ||
      url.pathname !== this.pathname ||
      url.username ||
      url.password ||
      url.hash ||
      [...url.searchParams].length !== 2 ||
      !url.searchParams.has("expires") ||
      !url.searchParams.get("signature")
    )
      throw new Error("source_download_ticket_invalid");
    this.ticket = url.href;
  }

  matches(value) {
    return !!this.ticket && value === this.ticket;
  }

  allowsRoute(value) {
    return (
      this.matches(value) && this.routedRequests === 0 && this.events === 0
    );
  }

  recordRoute(value) {
    if (!this.allowsRoute(value))
      throw new Error("source_download_route_repeated");
    this.routedRequests++;
  }

  recordDownload(value) {
    this.events++;
    if (this.events !== 1) throw new Error("source_download_event_repeated");
    if (!this.matches(value)) throw new Error("source_download_url_mismatch");
  }

  verifyBytes(actual, expected) {
    if (
      this.events !== 1 ||
      !Buffer.isBuffer(actual) ||
      !actual.equals(expected)
    )
      throw new Error("source_download_not_original");
    this.bytesVerified = true;
  }

  assertComplete() {
    if (this.events !== 1)
      throw new Error("source_download_event_count_invalid");
    if (!this.bytesVerified)
      throw new Error("source_download_bytes_unverified");
  }

  snapshot() {
    return {
      download_events: this.events,
      routed_download_requests: this.routedRequests,
      reserved_download_http_upper_bound: this.ticket ? 1 : 0,
      source_download_bytes_verified: this.bytesVerified,
    };
  }
}
