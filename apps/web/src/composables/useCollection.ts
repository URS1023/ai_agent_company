import { computed, ref, shallowRef } from "vue";
import { api, type Entity, type ListResult } from "../api/client";
import { useOperation } from "./useOperation";

/** 列表只管理分页与读取，不决定各领域的业务动作。 */
export function useCollection<T extends Entity = Entity>(
  getPath: () => string,
) {
  const items = ref<T[]>([]);
  const total = shallowRef(0);
  const page = shallowRef(1);
  const size = shallowRef(20);
  const query = shallowRef("");
  const op = useOperation();
  const offset = computed(() => (page.value - 1) * size.value);
  async function load() {
    await op.run(async () => {
      const path = getPath();
      const data = await api.get<ListResult<T>>(
        `${path}${path.includes("?") ? "&" : "?"}q=${encodeURIComponent(query.value)}&limit=${size.value}&offset=${offset.value}`,
      );
      items.value = data.items as any;
      total.value = data.total;
    });
  }
  return {
    items,
    total,
    page,
    size,
    query,
    loading: op.busy,
    error: op.error,
    load,
  };
}
