/** 行权限的本地编辑会话；只读取平台元数据，保存使用策略revision，不执行数据源查询。 */
import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  shallowRef,
  watch,
} from "vue";
import { ElMessageBox } from "element-plus";
import { api, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import {
  newRule,
  policyDraft,
  policyErrors,
  policyFingerprint,
  policyPayload,
  type PolicySubjects,
  type PolicyTable,
  type RowPolicy,
  type RowPolicyDraft,
} from "./rowPolicy";

export function useRowPolicyEditor(
  sourceId: string,
  getTables: () => PolicyTable[],
) {
  const loading = useOperation(),
    saving = useOperation();
  const policies = shallowRef<RowPolicy[]>([]);
  const subjects = shallowRef<PolicySubjects>({ roles: [], users: [] });
  const table = shallowRef("");
  const ready = shallowRef(false);
  const draft = reactive<RowPolicyDraft>({ mode: "all", rules: [] });
  const baseline = shallowRef(policyFingerprint(draft));
  const columns = computed(
    () => getTables().find((item) => item.name === table.value)?.columns ?? [],
  );
  const current = computed(() =>
    policies.value.find((item) => item.table_name === table.value),
  );
  const dirty = computed(() => policyFingerprint(draft) !== baseline.value);
  const busy = computed(() => loading.busy.value || saving.busy.value);
  const errors = computed(() => {
    const fields = saving.fields.value;
    const server =
      typeof fields.path === "string"
        ? { [fields.path]: saving.error.value }
        : fields;
    return { ...server, ...policyErrors(draft, columns.value, subjects.value) };
  });
  const canSave = computed(
    () =>
      ready.value &&
      !!table.value &&
      dirty.value &&
      !busy.value &&
      !Object.keys(errors.value).length,
  );
  let active = true;
  function restoreDraft() {
    Object.assign(draft, policyDraft(current.value, columns.value));
    baseline.value = policyFingerprint(draft);
    saving.fields.value = {};
    saving.error.value = "";
  }
  async function mayDiscard() {
    if (!dirty.value) return true;
    try {
      await ElMessageBox.confirm("放弃当前表未保存的行权限？", "切换确认", {
        confirmButtonText: "放弃修改",
        cancelButtonText: "继续编辑",
        type: "warning",
      });
      return true;
    } catch {
      return false;
    }
  }
  async function selectTable(value: string) {
    if (value === table.value || busy.value || !(await mayDiscard())) return;
    if (!getTables().some((item) => item.name === value)) return;
    table.value = value;
    restoreDraft();
  }
  async function load() {
    if (busy.value) return;
    ready.value = false;
    await loading.run(async () => {
      const [list, available] = await Promise.all([
        api.get<ListResult<RowPolicy>>(`/datasources/${sourceId}/row-policies`),
        api.get<PolicySubjects>(`/datasources/${sourceId}/row-policy-subjects`),
      ]);
      if (!active) return;
      policies.value = list.items;
      subjects.value = available;
      table.value =
        getTables().find((item) => item.name === table.value)?.name ??
        getTables()[0]?.name ??
        "";
      restoreDraft();
      ready.value = true;
    });
  }
  async function reload() {
    if (await mayDiscard()) await load();
  }
  async function save() {
    if (!canSave.value) return;
    const previous = current.value;
    if (draft.mode === "all" && previous?.mode === "rules") {
      try {
        await ElMessageBox.confirm(
          `开放“${table.value}”的全部记录？`,
          "权限变更确认",
          {
            confirmButtonText: "开放全部记录",
            cancelButtonText: "取消",
            type: "warning",
          },
        );
      } catch {
        return;
      }
    }
    const payload = policyPayload(draft);
    const saved = await saving.run(
      () =>
        previous
          ? api.patch<RowPolicy>(`/row-policies/${previous.id}`, {
              ...payload,
              revision: previous.revision,
            })
          : api.post<RowPolicy>(`/datasources/${sourceId}/row-policies`, {
              ...payload,
              table_name: table.value,
            }),
      "行权限已保存",
    );
    if (saved && active) {
      policies.value = [
        ...policies.value.filter((item) => item.id !== saved.id),
        saved,
      ];
      restoreDraft();
    }
  }
  function setScope(value: string | number | boolean | undefined) {
    if (busy.value) return;
    draft.mode = value === "all" ? "all" : "rules";
    if (value === "none") draft.rules = [];
    if (value === "rules" && !draft.rules.length)
      draft.rules.push(newRule(columns.value[0]));
  }
  const scope = computed(() =>
    draft.mode === "all" ? "all" : draft.rules.length ? "rules" : "none",
  );
  watch(
    () => policyFingerprint(draft),
    () => {
      saving.fields.value = {};
      saving.error.value = "";
    },
  );
  onMounted(load);
  onBeforeUnmount(() => {
    active = false;
  });
  return {
    loading,
    saving,
    subjects,
    table,
    columns,
    draft,
    dirty,
    busy,
    ready,
    errors,
    canSave,
    scope,
    selectTable,
    setScope,
    reload,
    save,
  };
}
