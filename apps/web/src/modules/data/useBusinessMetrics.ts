import { computed, ref, shallowRef } from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useCollection } from "../../composables/useCollection";
import { useOperation } from "../../composables/useOperation";
import {
  metricDraft,
  metricPayload,
  PreparationError,
  preparationErrors,
  type Fields,
  type MetricDraft,
} from "./dataPreparation";
import { usePreparationQueries } from "./usePreparationQueries";
/** 指标编辑保留旧SQL/参数元数据；执行始终引用服务端已保存口径和查询快照。 */
export function useBusinessMetrics() {
  const list = useCollection(() => "/business-metrics");
  const op = useOperation();
  const queries = usePreparationQueries();
  const datasources = ref<Entity[]>([]);
  const selected = ref<Entity | null>(null);
  const dialog = shallowRef(false);
  const name = shallowRef("");
  const datasourceId = shallowRef("");
  const definition = ref<MetricDraft>(metricDraft());
  const localErrors = ref<Fields>({});
  const errors = computed(() => ({
    ...preparationErrors(op.fields.value, op.error.value),
    ...localErrors.value,
  }));
  const executing = ref<Entity | null>(null);
  const result = ref<Entity | null>(null);
  async function load(canReadQueries = true) {
    // 顺序读取并在首个错误停止，不为目录自动重试或执行查询。
    await list.load();
    await op.run(async () => {
      datasources.value = (
        await api.get<ListResult<Entity>>("/datasources?limit=100")
      ).items;
      if (canReadQueries) await queries.load();
    });
  }
  async function edit(row: Entity | null) {
    await op.run(async () => {
      selected.value = row
        ? await api.get(`/business-metrics/${row.id}`)
        : null;
      name.value = selected.value?.name ?? "";
      datasourceId.value = selected.value?.datasource_id ?? "";
      definition.value = metricDraft(selected.value?.definition);
      localErrors.value = {};
      dialog.value = true;
    });
  }
  async function save() {
    localErrors.value = {};
    let value;
    try {
      value = metricPayload(definition.value, queries.columns.value);
    } catch (error) {
      if (error instanceof PreparationError) localErrors.value = error.fields;
      return;
    }
    if (!name.value.trim()) {
      localErrors.value.name = "请输入指标名称";
      return;
    }
    const payload = {
      name: name.value.trim(),
      datasource_id: datasourceId.value || null,
      definition: value,
      sql: selected.value?.sql ?? null,
      parameters: selected.value?.parameters ?? {},
    };
    const saved = await op.run(async () => {
      const entity = selected.value
        ? await api.patch(`/business-metrics/${selected.value.id}`, {
            ...payload,
            revision: selected.value.revision,
          })
        : await api.post("/business-metrics", payload);
      dialog.value = false;
      return entity;
    }, "指标已保存");
    if (saved) await list.load();
  }
  async function remove(row: Entity) {
    try {
      await ElMessageBox.confirm(`删除“${row.name}”？`, "删除确认", {
        type: "warning",
      });
    } catch {
      return;
    }
    const removed = await op.run(async () => {
      await api.delete(`/business-metrics/${row.id}`, row.revision);
      return true;
    }, "指标已删除");
    if (removed) await list.load();
  }
  function startExecution(row: Entity) {
    executing.value = row;
    localErrors.value = {};
  }
  async function execute() {
    localErrors.value = {};
    if (!queries.queryId.value) {
      localErrors.value.query_id = "请选择数据查询";
      return;
    }
    if (!executing.value) return;
    await op.run(async () => {
      result.value = await api.post(
        `/business-metrics/${executing.value!.id}/executions`,
        {
          query_id: queries.queryId.value,
          revision: executing.value!.revision,
        },
      );
      executing.value = null;
    }, "指标计算已完成");
  }
  return {
    list,
    op,
    queries,
    datasources,
    selected,
    dialog,
    name,
    datasourceId,
    definition,
    errors,
    executing,
    result,
    load,
    edit,
    save,
    remove,
    startExecution,
    execute,
  };
}
