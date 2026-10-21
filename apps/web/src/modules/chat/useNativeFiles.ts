import { shallowRef } from "vue";
import { ApiError } from "../../api/client";
import { nativePrefix, type NativeFile } from "./nativeTypes";
import type { useNativeTransport } from "./useNativeTransport";
export function useNativeFiles(
  transport: ReturnType<typeof useNativeTransport>,
  identity: () => string,
  canWrite: () => boolean,
  fail: (error: unknown) => void,
) {
  const files = shallowRef<NativeFile[]>([]),
    selected = shallowRef<string[]>([]),
    busy = shallowRef(false);
  let generation = 0;
  const clear = () => {
    generation++;
    files.value = [];
    selected.value = [];
    busy.value = false;
  };
  async function load() {
    const id = identity(),
      ticket = ++generation;
    if (!id) {
      clear();
      return;
    }
    try {
      const value = await transport.send<{ items: NativeFile[] }>(
        `${nativePrefix}/files?identity_id=${encodeURIComponent(id)}&limit=100&offset=0`,
      );
      if (transport.active() && ticket === generation && id === identity()) {
        files.value = value.items;
        selected.value = selected.value.filter((key) =>
          files.value.some(
            (file) => file.id === key && file.status === "ready",
          ),
        );
      }
    } catch (error) {
      if (ticket === generation) fail(error);
    }
  }
  async function upload(file: File) {
    if (!canWrite() || !identity() || busy.value) return;
    if (
      file.size > 20 * 1024 * 1024 ||
      !/\.(txt|md|csv|json|log|pdf)$/i.test(file.name)
    ) {
      fail(
        new Error("请选择20MiB以内的文本、Markdown、CSV、JSON、日志或PDF文件"),
      );
      return;
    }
    busy.value = true;
    const ticket = generation,
      id = identity(),
      body = new FormData();
    body.set("file", file);
    try {
      const value = await transport.send<NativeFile>(
        `${nativePrefix}/identities/${encodeURIComponent(id)}/uploads`,
        {
          method: "POST",
          body,
          headers: { "Idempotency-Key": crypto.randomUUID() },
          timeoutMs: 70000,
        },
      );
      if (ticket !== generation || id !== identity()) return;
      files.value = [
        value,
        ...files.value.filter((item) => item.id !== value.id),
      ];
      if (value.status === "ready" && selected.value.length < 10)
        selected.value = [...selected.value, value.id];
      else if (["failed", "unknown", "quarantined"].includes(value.status))
        fail(
          new Error(
            value.status === "unknown"
              ? "附件结果待核对，请勿重复上传"
              : "附件未就绪，请查看当前状态",
          ),
        );
    } catch (error) {
      if (ticket === generation) fail(error);
    } finally {
      if (transport.active() && ticket === generation) busy.value = false;
    }
  }
  async function action(id: string, kind: "refreshes" | "reconciliations") {
    const file = files.value.find((item) => item.id === id);
    if (!file || !canWrite() || busy.value) return;
    busy.value = true;
    const ticket = generation;
    try {
      const value = await transport.send<NativeFile>(
        `${nativePrefix}/files/${encodeURIComponent(id)}/${kind}`,
        {
          method: "POST",
          body: JSON.stringify({ revision: file.revision }),
          timeoutMs: 70000,
        },
      );
      if (ticket === generation)
        files.value = files.value.map((item) =>
          item.id === id ? value : item,
        );
    } catch (error) {
      if (ticket === generation) fail(error);
    } finally {
      if (transport.active() && ticket === generation) busy.value = false;
    }
  }
  async function download(id: string, knownName?: string) {
    try {
      const blob = await transport.send<Blob>(
        `${nativePrefix}/files/${encodeURIComponent(id)}/source`,
        { asBlob: true },
      );
      if (!transport.active()) return;
      const name =
        knownName ??
        files.value.find((item) => item.id === id)?.source_file.name ??
        "附件";
      const url = URL.createObjectURL(blob),
        anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = name;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (error) {
      if (error instanceof ApiError && [403, 404].includes(error.status)) {
        files.value = files.value.filter((item) => item.id !== id);
        selected.value = selected.value.filter((item) => item !== id);
      }
      fail(error);
    }
  }
  function attach(ids: string[]) {
    if (!canWrite() || ids.length > 10) return;
    selected.value = ids.filter(
      (id, index) =>
        ids.indexOf(id) === index &&
        files.value.some((file) => file.id === id && file.status === "ready"),
    );
  }
  return {
    files,
    selected,
    busy,
    clear,
    load,
    upload,
    action,
    download,
    attach,
  };
}
