import { onScopeDispose, ref, shallowRef } from "vue";
import { ApiError, type ListResult } from "../../api/client";
import { useKnowledgeRequests } from "./useKnowledgeRequests";
import {
  knowledgeFileError,
  canRebuild,
  type KnowledgeDocument,
  type KnowledgeChunk,
  type KnowledgeImport,
} from "./knowledge";

/** 文档状态以服务端事实为准；所有索引写入均由用户单次明确提交，不自动重试。 */
export function useKnowledgeDocuments(spaceId: string, invalidate: () => void) {
  const transport = useKnowledgeRequests();
  const items = ref<KnowledgeDocument[]>([]),
    total = shallowRef(0),
    page = shallowRef(1),
    query = shallowRef("");
  const size = 20;
  const loading = shallowRef(false),
    busy = shallowRef(false),
    downloading = shallowRef(false);
  const error = shallowRef(""),
    fields = ref<Record<string, string>>({});
  const importing = shallowRef(false),
    importError = shallowRef(""),
    uncertain = shallowRef(false);
  const detail = ref<KnowledgeDocument | null>(null),
    detailOpen = shallowRef(false),
    detailLoading = shallowRef(false);
  const chunks = ref<KnowledgeChunk[]>([]),
    chunkPage = shallowRef(1),
    chunkTotal = shallowRef(0),
    chunkLoading = shallowRef(false);
  const detailError = shallowRef("");
  const indexUncertain = shallowRef(false);
  let listSequence = 0,
    detailSequence = 0,
    generation = 0;
  const current = (ticket: number) =>
    transport.active() && ticket === generation;
  const urls = new Set<string>();
  const message = (failure: unknown) =>
    failure instanceof Error ? failure.message : "操作失败";
  function clearDetail() {
    detailSequence++;
    detail.value = null;
    detailOpen.value = false;
    detailError.value = "";
    chunks.value = [];
    chunkTotal.value = 0;
    chunkPage.value = 1;
    detailLoading.value = false;
    chunkLoading.value = false;
    indexUncertain.value = false;
  }
  function deny(failure: unknown) {
    if (!(failure instanceof ApiError) || ![403, 404].includes(failure.status))
      return false;
    generation++;
    listSequence++;
    items.value = [];
    total.value = 0;
    loading.value = false;
    busy.value = false;
    downloading.value = false;
    importing.value = false;
    clearDetail();
    error.value = message(failure);
    for (const url of urls) URL.revokeObjectURL(url);
    urls.clear();
    invalidate();
    return true;
  }
  async function load() {
    if (!transport.active()) return;
    const ticket = generation,
      sequence = ++listSequence;
    loading.value = true;
    error.value = "";
    items.value = [];
    total.value = 0;
    try {
      const result = await transport.send<ListResult<KnowledgeDocument>>(
        `/kb/spaces/${encodeURIComponent(spaceId)}/documents?q=${encodeURIComponent(query.value.trim())}&limit=${size}&offset=${(page.value - 1) * size}`,
      );
      if (!current(ticket) || sequence !== listSequence) return;
      items.value = result.items;
      total.value = result.total;
    } catch (failure) {
      if (current(ticket) && sequence === listSequence && !deny(failure))
        error.value = message(failure);
    } finally {
      if (current(ticket) && sequence === listSequence) loading.value = false;
    }
  }
  async function search() {
    page.value = 1;
    await load();
  }
  async function changePage(value: number) {
    if (loading.value || error.value || value === page.value) return;
    page.value = value;
    await load();
  }
  async function loadChunks() {
    if (!transport.active() || !detail.value) return;
    const ticket = generation,
      sequence = detailSequence,
      id = detail.value.id,
      requestedPage = chunkPage.value;
    chunkLoading.value = true;
    chunks.value = [];
    detailError.value = "";
    try {
      const result = await transport.send<ListResult<KnowledgeChunk>>(
        `/kb/documents/${encodeURIComponent(id)}/chunks?limit=20&offset=${(requestedPage - 1) * 20}`,
      );
      if (
        !current(ticket) ||
        sequence !== detailSequence ||
        requestedPage !== chunkPage.value
      )
        return;
      chunks.value = result.items;
      chunkTotal.value = result.total;
    } catch (failure) {
      if (current(ticket) && sequence === detailSequence && !deny(failure))
        detailError.value = message(failure);
    } finally {
      if (current(ticket) && sequence === detailSequence)
        chunkLoading.value = false;
    }
  }
  async function open(id: string) {
    if (!transport.active()) return;
    clearDetail();
    const ticket = generation,
      sequence = ++detailSequence;
    detailOpen.value = true;
    detailLoading.value = true;
    try {
      const result = await transport.send<KnowledgeDocument>(
        `/kb/documents/${encodeURIComponent(id)}`,
      );
      if (!current(ticket) || sequence !== detailSequence) return;
      detail.value = result;
      if (result.chunk_count > 0) await loadChunks();
    } catch (failure) {
      if (current(ticket) && sequence === detailSequence && !deny(failure))
        detailError.value = message(failure);
    } finally {
      if (current(ticket) && sequence === detailSequence)
        detailLoading.value = false;
    }
  }
  async function changeChunkPage(value: number) {
    if (chunkLoading.value || value === chunkPage.value) return;
    chunkPage.value = value;
    await loadChunks();
  }
  function beginImport() {
    if (!transport.active()) return;
    fields.value = {};
    importError.value = "";
    uncertain.value = false;
    importing.value = true;
  }
  async function importDocument(input: KnowledgeImport) {
    if (!transport.active() || busy.value || uncertain.value) return;
    fields.value = {};
    importError.value = "";
    if (input.kind === "upload") {
      const failure = knowledgeFileError(input.file);
      if (failure) {
        fields.value = { file: failure };
        return;
      }
    }
    const ticket = generation;
    busy.value = true;
    try {
      let result: KnowledgeDocument;
      if (input.kind === "upload") {
        const body = new FormData();
        body.append("file", input.file);
        if (input.name?.trim()) body.append("name", input.name.trim());
        result = await transport.send<KnowledgeDocument>(
          `/kb/spaces/${encodeURIComponent(spaceId)}/uploads`,
          { method: "POST", body, timeoutMs: 100000 },
        );
      } else {
        const path = input.kind === "file" ? "file-imports" : "documents";
        const body =
          input.kind === "file"
            ? { file_id: input.file_id, name: input.name?.trim() || undefined }
            : {
                name: input.name.trim(),
                text: input.text.trim(),
                source: "paste",
              };
        result = await transport.send<KnowledgeDocument>(
          `/kb/spaces/${encodeURIComponent(spaceId)}/${path}`,
          { method: "POST", body: JSON.stringify(body), timeoutMs: 100000 },
        );
      }
      if (!current(ticket)) return;
      importing.value = false;
      query.value = "";
      page.value = 1;
      await load();
      if (!current(ticket)) return;
      // 即使201的实际处理结果为failed，也展示已保存的失败文档，不宣称入库成功。
      clearDetail();
      detail.value = result;
      detailSequence++;
      detailOpen.value = true;
      chunks.value = [];
      chunkPage.value = 1;
      if (result.chunk_count > 0) await loadChunks();
      return result;
    } catch (failure) {
      if (!current(ticket) || deny(failure)) return;
      importError.value = message(failure);
      if (failure instanceof ApiError) {
        fields.value = failure.fields;
        uncertain.value =
          [0, 408].includes(failure.status) || failure.status >= 500;
        if (uncertain.value)
          importError.value = "导入结果待确认，请返回列表刷新状态";
      }
    } finally {
      if (current(ticket)) busy.value = false;
    }
  }
  async function rebuild(document: KnowledgeDocument) {
    if (
      !transport.active() ||
      busy.value ||
      indexUncertain.value ||
      !canRebuild(document)
    )
      return;
    const ticket = generation;
    busy.value = true;
    detailError.value = "";
    try {
      const result = await transport.send<KnowledgeDocument>(
        `/kb/documents/${encodeURIComponent(document.id)}/index-jobs`,
        { method: "POST", body: "{}", timeoutMs: 100000 },
      );
      if (!current(ticket)) return;
      await load();
      if (current(ticket) && detail.value?.id === result.id)
        detail.value = result;
      return result;
    } catch (failure) {
      if (current(ticket) && !deny(failure)) {
        detailError.value = message(failure);
        if (
          failure instanceof ApiError &&
          ([0, 408].includes(failure.status) || failure.status >= 500)
        ) {
          indexUncertain.value = true;
          detailError.value = "索引结果待确认，请刷新状态";
        }
      }
    } finally {
      if (current(ticket)) busy.value = false;
    }
  }
  async function remove(document: KnowledgeDocument) {
    if (!transport.active() || busy.value) return;
    const ticket = generation;
    busy.value = true;
    error.value = "";
    try {
      await transport.send(`/kb/documents/${encodeURIComponent(document.id)}`, {
        method: "DELETE",
      });
      if (!current(ticket)) return;
      if (detail.value?.id === document.id) clearDetail();
      invalidate();
      if (items.value.length === 1 && page.value > 1) page.value--;
      await load();
      return true;
    } catch (failure) {
      if (current(ticket) && !deny(failure)) error.value = message(failure);
    } finally {
      if (current(ticket)) busy.value = false;
    }
  }
  async function download(document: KnowledgeDocument) {
    if (!transport.active() || !document.source_file || downloading.value)
      return;
    const ticket = generation;
    downloading.value = true;
    detailError.value = "";
    try {
      const blob = await transport.send<Blob>(
        `/kb/documents/${encodeURIComponent(document.id)}/source`,
        { asBlob: true },
      );
      if (!current(ticket)) return;
      const url = URL.createObjectURL(blob);
      urls.add(url);
      const anchor = globalThis.document.createElement("a");
      anchor.href = url;
      anchor.download = document.source_file.name;
      anchor.click();
      setTimeout(() => {
        URL.revokeObjectURL(url);
        urls.delete(url);
      }, 1000);
    } catch (failure) {
      if (current(ticket) && !deny(failure))
        detailError.value = message(failure);
    } finally {
      if (current(ticket)) downloading.value = false;
    }
  }
  onScopeDispose(() => {
    clearDetail();
    items.value = [];
    total.value = 0;
    query.value = "";
    importing.value = false;
    importError.value = "";
    fields.value = {};
    busy.value = false;
    downloading.value = false;
    for (const url of urls) URL.revokeObjectURL(url);
    urls.clear();
  });
  return {
    items,
    total,
    page,
    query,
    size,
    loading,
    busy,
    downloading,
    error,
    fields,
    importing,
    importError,
    uncertain,
    detail,
    detailOpen,
    detailLoading,
    detailError,
    indexUncertain,
    chunks,
    chunkPage,
    chunkTotal,
    chunkLoading,
    load,
    search,
    changePage,
    open,
    clearDetail,
    loadChunks,
    changeChunkPage,
    beginImport,
    importDocument,
    rebuild,
    remove,
    download,
  };
}
