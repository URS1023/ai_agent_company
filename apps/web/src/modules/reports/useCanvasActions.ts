import {
  computed,
  onBeforeUnmount,
  onMounted,
  ref,
  watch,
  type Ref,
} from "vue";
import { cloneDto } from "../../utils/cloneDto";
type Widget = Record<string, any>;
/** 管理画布选区与图层动作，所有修改仍由报表统一保存及修订。 */
export function useCanvasActions(options: {
  widgets: Ref<Widget[]>;
  selectedId: Ref<string>;
  checkpoint: () => void;
  preview: Ref<boolean>;
  showData: (widgetId: string) => void;
}) {
  const { widgets, selectedId, checkpoint, preview, showData } = options;
  const selectedIds = ref<string[]>([]);
  const clipboard = ref<Widget[]>([]);
  const selected = computed(() =>
    widgets.value.filter((widget) => selectedIds.value.includes(widget.id)),
  );
  watch(selectedId, (id) => {
    if (!selectedIds.value.includes(id)) selectedIds.value = id ? [id] : [];
  });
  watch(widgets, (value) => {
    selectedIds.value = selectedIds.value.filter((id) =>
      value.some((widget) => widget.id === id),
    );
  });
  function areaSelect(ids: string[]) {
    selectedIds.value = ids;
    selectedId.value = ids[0] ?? "";
  }
  function select(value: string | { id: string; toggle?: boolean }) {
    const id = typeof value === "string" ? value : value.id;
    if (typeof value !== "string" && value.toggle) {
      areaSelect(
        selectedIds.value.includes(id)
          ? selectedIds.value.filter((item) => item !== id)
          : [...selectedIds.value, id],
      );
      return;
    }
    const target = widgets.value.find((widget) => widget.id === id);
    areaSelect(
      target?.groupId
        ? widgets.value
            .filter((widget) => widget.groupId === target.groupId)
            .map((widget) => widget.id)
        : id
          ? [id]
          : [],
    );
  }
  function remove() {
    const ids = selected.value
      .filter((widget) => !widget.locked)
      .map((widget) => widget.id);
    if (!ids.length) return;
    checkpoint();
    widgets.value = widgets.value.filter((widget) => !ids.includes(widget.id));
    areaSelect([]);
  }
  function copy() {
    clipboard.value = cloneDto(selected.value);
  }
  function paste(point?: { x: number; y: number }) {
    if (!clipboard.value.length) return;
    checkpoint();
    const left = Math.min(...clipboard.value.map((widget) => widget.x));
    const top = Math.min(...clipboard.value.map((widget) => widget.y));
    const groupMap = new Map<string, string>();
    const next = cloneDto(clipboard.value).map((widget, index) => {
      const id = crypto.randomUUID();
      if (widget.groupId && !groupMap.has(widget.groupId))
        groupMap.set(widget.groupId, crypto.randomUUID());
      return {
        ...widget,
        id,
        i: id,
        x: widget.x - left + (point?.x ?? left + 20),
        y: widget.y - top + (point?.y ?? top + 20),
        orderNum: widgets.value.length + index,
        ...(widget.groupId ? { groupId: groupMap.get(widget.groupId) } : {}),
      };
    });
    widgets.value.push(...next);
    areaSelect(next.map((widget) => widget.id));
  }
  function duplicate() {
    copy();
    paste();
  }
  function reorder(action: string, targetId: string) {
    const ordered = [...widgets.value].sort(
      (a, b) => (a.orderNum ?? 0) - (b.orderNum ?? 0),
    );
    const index = ordered.findIndex((widget) => widget.id === targetId);
    if (index < 0) return;
    checkpoint();
    const [target] = ordered.splice(index, 1);
    const next =
      action === "top"
        ? ordered.length
        : action === "bottom"
          ? 0
          : Math.max(
              0,
              Math.min(ordered.length, index + (action === "move-up" ? 1 : -1)),
            );
    ordered.splice(next, 0, target!);
    ordered.forEach((widget, orderNum) => (widget.orderNum = orderNum));
  }
  function contextAction(event: {
    action: string;
    id: string;
    stageX?: number;
    stageY?: number;
    blank?: boolean;
  }) {
    if (event.id && !selectedIds.value.includes(event.id)) select(event.id);
    const target = widgets.value.find((widget) => widget.id === event.id);
    if (event.action === "copy") copy();
    else if (event.action === "cut") {
      copy();
      remove();
    } else if (event.action === "paste")
      paste(
        event.blank
          ? { x: event.stageX ?? 0, y: event.stageY ?? 0 }
          : undefined,
      );
    else if (event.action === "remove") remove();
    else if (["top", "bottom", "move-up", "move-down"].includes(event.action))
      reorder(event.action, event.id);
    else if (event.action === "preview") preview.value = true;
    else if (event.action === "drilldown") showData(event.id);
    else if (event.action === "modify-group") areaSelect([event.id]);
    else if (event.action === "edit") {
      areaSelect([event.id]);
      document
        .querySelector<HTMLInputElement>(".report-properties input")
        ?.focus();
    } else if (target && event.action === "lock") {
      checkpoint();
      target.locked = !target.locked;
    } else if (target && event.action === "clear-linkage") {
      checkpoint();
      delete target.config?.linkageConfig;
    } else if (event.action === "group" && selected.value.length > 1) {
      checkpoint();
      const id = crypto.randomUUID();
      selected.value.forEach((widget) => (widget.groupId = id));
    } else if (target && event.action === "ungroup") {
      checkpoint();
      widgets.value
        .filter((widget) => widget.groupId === target.groupId)
        .forEach((widget) => delete widget.groupId);
    }
  }
  function onKey(event: KeyboardEvent) {
    if (
      preview.value ||
      event.defaultPrevented ||
      (event.target instanceof Element &&
        event.target.closest(
          'input,textarea,[contenteditable="true"],.el-select',
        ))
    )
      return;
    const control = event.ctrlKey || event.metaKey;
    let action = "";
    if (control)
      action =
        (
          {
            c: "copy",
            x: "cut",
            v: "paste",
            l: "lock",
            m: "modify-group",
            ArrowUp: "top",
            ArrowDown: "bottom",
          } as Record<string, string>
        )[event.key] ?? "";
    else if (event.shiftKey && ["ArrowUp", "ArrowDown"].includes(event.key))
      action = event.key === "ArrowUp" ? "move-up" : "move-down";
    else if (event.key === "Delete" || event.key === "Backspace")
      action = "remove";
    if (action) {
      event.preventDefault();
      contextAction({ action, id: selectedId.value });
    }
  }
  onMounted(() => window.addEventListener("keydown", onKey));
  onBeforeUnmount(() => window.removeEventListener("keydown", onKey));
  return {
    selectedIds,
    clipboard,
    areaSelect,
    select,
    remove,
    duplicate,
    contextAction,
  };
}
