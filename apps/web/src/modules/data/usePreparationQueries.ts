import { computed, ref, shallowRef } from "vue";
import { api, type Entity, type ListResult } from "../../api/client";
import { queryColumns } from "./dataPreparation";
/** 查询目录只读取已保存且当前仍获授权的结果，不在表单加载时重新取数。 */
export function usePreparationQueries() {
  const queries = ref<Entity[]>([]);
  const queryId = shallowRef("");
  const query = computed(() =>
    queries.value.find((x) => x.id === queryId.value),
  );
  const columns = computed(() => queryColumns(query.value));
  async function load() {
    queries.value = (
      await api.get<ListResult<Entity>>("/queries?limit=100")
    ).items;
  }
  return { queries, queryId, query, columns, load };
}
