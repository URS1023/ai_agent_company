import { computed, ref, shallowRef, watch, onScopeDispose } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { queryColumns } from "./dataPreparation";
/** 已保存查询采用服务端名称搜索和分页；选中详情独立保存，翻页不丢字段。 */
export function usePreparationQueries() {
  const queries = ref<Entity[]>([]);
  const queryId = shallowRef("");
  const selected = ref<Entity | null>(null);
  const searchText = shallowRef("");
  const page = shallowRef(1);
  const size = shallowRef(100);
  const total = shallowRef(0);
  const loading = shallowRef(false);
  const selecting = shallowRef(false);
  const error = shallowRef("");
  let listGeneration = 0,
    detailGeneration = 0;
  const query = computed(() =>
    selected.value?.id === queryId.value
      ? selected.value
      : queries.value.find((x) => x.id === queryId.value),
  );
  const columns = computed(() =>
    selecting.value ? [] : queryColumns(query.value),
  );
  const options = computed(() =>
    selected.value && !queries.value.some((x) => x.id === selected.value!.id)
      ? [selected.value, ...queries.value]
      : queries.value,
  );
  async function load() {
    const generation = ++listGeneration;
    loading.value = true;
    error.value = "";
    try {
      const data = await api.get<ListResult<Entity>>(
        `/queries?q=${encodeURIComponent(searchText.value)}&limit=${size.value}&offset=${(page.value - 1) * size.value}`,
      );
      if (generation !== listGeneration) return;
      queries.value = data.items;
      total.value = data.total;
    } catch (failure) {
      if (generation !== listGeneration) return;
      queries.value = [];
      total.value = 0;
      selected.value = null;
      queryId.value = "";
      error.value =
        failure instanceof Error ? failure.message : "无法读取查询目录";
      throw failure;
    } finally {
      if (generation === listGeneration) loading.value = false;
    }
  }
  async function search(value: string) {
    searchText.value = value;
    page.value = 1;
    await load();
  }
  async function changePage(value: number) {
    if (loading.value || error.value || value === page.value) return;
    page.value = value;
    await load();
  }
  const stopWatch = watch(
    queryId,
    async (id) => {
      const generation = ++detailGeneration;
      selected.value = null;
      if (!id) {
        selecting.value = false;
        return;
      }
      selecting.value = true;
      error.value = "";
      try {
        const detail = await api.get<Entity>(
          `/queries/${encodeURIComponent(id)}`,
        );
        if (generation === detailGeneration) selected.value = detail;
      } catch (failure) {
        if (generation === detailGeneration) {
          queryId.value = "";
          queries.value = [];
          total.value = 0;
          error.value =
            failure instanceof Error ? failure.message : "无法读取查询结果";
        }
      } finally {
        if (generation === detailGeneration) selecting.value = false;
      }
    },
    { flush: "sync" },
  );
  onScopeDispose(() => {
    listGeneration++;
    detailGeneration++;
    stopWatch();
    queries.value = [];
    selected.value = null;
  });
  return {
    queries,
    queryId,
    query,
    columns,
    options,
    searchText,
    page,
    size,
    total,
    loading,
    selecting,
    error,
    load,
    search,
    changePage,
  };
}
