import { ref, shallowRef, onScopeDispose } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
/** 历史只读服务端授权结果；每次查看与导出重新核权，不重执行来源SQL。 */
export function usePreparationHistory() {
  const visible = shallowRef(false);
  const query = shallowRef("");
  const metricId = shallowRef("");
  const page = shallowRef(1);
  const size = 20;
  const total = shallowRef(0);
  const items = ref<Entity[]>([]);
  const detail = ref<Entity | null>(null);
  const loading = shallowRef(false);
  const reading = shallowRef(false);
  const error = shallowRef("");
  let generation = 0,
    detailGeneration = 0;
  async function load() {
    const current = ++generation;
    loading.value = true;
    error.value = "";
    detail.value = null;
    detailGeneration++;
    reading.value = false;
    items.value = [];
    try {
      const result = await api.get<ListResult<Entity>>(
        `/data-processing-jobs?q=${encodeURIComponent(query.value)}&limit=${size}&offset=${(page.value - 1) * size}${metricId.value ? `&metric_id=${encodeURIComponent(metricId.value)}` : ""}`,
      );
      if (current === generation) {
        items.value = result.items;
        total.value = result.total;
      }
    } catch (failure) {
      if (current === generation) {
        items.value = [];
        total.value = 0;
        error.value =
          failure instanceof Error ? failure.message : "无法读取历史结果";
      }
    } finally {
      if (current === generation) loading.value = false;
    }
  }
  async function open(id = "") {
    metricId.value = id;
    query.value = "";
    page.value = 1;
    items.value = [];
    detail.value = null;
    visible.value = true;
    await load();
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
  async function read(id: string) {
    const current = ++detailGeneration;
    detail.value = null;
    reading.value = true;
    error.value = "";
    try {
      const row = await api.get<Entity>(
        `/data-processing-jobs/${encodeURIComponent(id)}`,
      );
      if (current === detailGeneration) detail.value = row;
    } catch (failure) {
      if (current === detailGeneration) {
        items.value = [];
        total.value = 0;
        error.value =
          failure instanceof Error ? failure.message : "无法查看历史结果";
      }
    } finally {
      if (current === detailGeneration) reading.value = false;
    }
  }
  function close() {
    generation++;
    detailGeneration++;
    visible.value = false;
    detail.value = null;
    items.value = [];
    total.value = 0;
    loading.value = false;
    reading.value = false;
  }
  function deny(message: string) {
    generation++;
    detailGeneration++;
    detail.value = null;
    items.value = [];
    total.value = 0;
    loading.value = false;
    reading.value = false;
    error.value = message;
  }
  onScopeDispose(close);
  return {
    visible,
    query,
    metricId,
    page,
    size,
    total,
    items,
    detail,
    loading,
    reading,
    error,
    open,
    load,
    search,
    changePage,
    read,
    close,
    deny,
  };
}
