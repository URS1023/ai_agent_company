<script setup lang="ts">
/** Vue持有平台授权与版本，原React编辑岛只管理本次草稿；显式保存、无后台同步。 */
import {
  computed,
  nextTick,
  onMounted,
  onScopeDispose,
  ref,
  shallowRef,
  watch,
} from "vue";
import { ElMessageBox } from "element-plus";
import { ApiError } from "../../api/client";
import { useWorkflowRequests } from "./useWorkflowRequests";
import type { GraphDefinition } from "@dify-workflow";
const props = defineProps<{ workflowId: string; canWrite: boolean }>();
const emit = defineEmits<{
  close: [];
  saved: [id: string];
  denied: [message: string];
}>();
const transport = useWorkflowRequests();
const host = shallowRef<HTMLElement>();
const detail = shallowRef<{
  id: string;
  name: string;
  description: string;
  revision: number;
  definition: GraphDefinition;
}>();
const name = ref("");
const error = ref("");
const saving = ref(false),
  loading = ref(false),
  dirty = ref(false);
let editor:
  | ReturnType<(typeof import("@dify-workflow"))["mountWorkflowEditor"]>
  | undefined;
let active = true,
  sequence = 0;
const changed = computed(
  () => dirty.value || name.value !== detail.value?.name,
);
function fail(failure: unknown) {
  if (!active || failure === "cancel") return;
  if (failure instanceof ApiError && [401, 403, 404].includes(failure.status)) {
    editor?.unmount();
    editor = undefined;
    detail.value = undefined;
    emit("denied", failure.message);
  }
  error.value = failure instanceof Error ? failure.message : "操作失败";
}
async function load() {
  if (saving.value) return;
  if (changed.value && detail.value) {
    try {
      await ElMessageBox.confirm("放弃尚未保存的修改？", "重新读取", {
        type: "warning",
        confirmButtonText: "放弃修改",
      });
    } catch {
      return;
    }
  }
  loading.value = true;
  error.value = "";
  const id = detail.value?.id || props.workflowId;
  try {
    if (!id) {
      const library = await import("@dify-workflow");
      if (!active) return;
      detail.value = {
        id: "",
        name: "",
        description: "",
        revision: 0,
        definition: library.createInitialDefinition(),
      };
      name.value = "";
      dirty.value = false;
      return;
    }
    const value = await transport.send<NonNullable<typeof detail.value>>(
      `/workflows/${id}`,
    );
    if (!active) return;
    if (value.definition.format !== "graph")
      throw new Error("该流程使用步骤编辑，请关闭画布后重新打开。");
    detail.value = value;
    name.value = value.name;
    dirty.value = false;
  } catch (failure) {
    fail(failure);
  } finally {
    if (active) loading.value = false;
  }
}
async function save(definition: GraphDefinition) {
  if (!detail.value || !props.canWrite || saving.value) return;
  if (!name.value.trim()) {
    error.value = "请输入流程名称";
    return;
  }
  saving.value = true;
  error.value = "";
  const current = detail.value;
  try {
    const result = await transport.send<NonNullable<typeof detail.value>>(
      current.id ? `/workflows/${current.id}` : "/workflows",
      {
        method: current.id ? "PUT" : "POST",
        body: JSON.stringify({
          name: name.value.trim(),
          description: current.description,
          ...(current.id ? { revision: current.revision } : {}),
          definition,
        }),
      },
    );
    if (!active) return;
    detail.value = result;
    name.value = result.name;
    dirty.value = false;
    emit("saved", result.id);
  } catch (failure) {
    fail(failure);
  } finally {
    if (active) saving.value = false;
  }
}
async function close() {
  if (saving.value) return;
  if (changed.value) {
    try {
      await ElMessageBox.confirm("放弃尚未保存的修改？", "关闭编辑", {
        type: "warning",
        confirmButtonText: "放弃修改",
      });
    } catch {
      return;
    }
  }
  if (active) emit("close");
}
watch([host, detail, saving, name, () => props.canWrite], async () => {
  const ticket = ++sequence;
  if (!host.value || !detail.value) return;
  await nextTick();
  try {
    const library = await import("@dify-workflow");
    if (!active || ticket !== sequence || !host.value || !detail.value) return;
    const options = {
      definition: detail.value.definition,
      readOnly: !props.canWrite,
      saving: saving.value,
      externalDirty: !detail.value.id || name.value !== detail.value.name,
      onSave: save,
      onDirtyChange: (value: boolean) => {
        if (active) dirty.value = value;
      },
    };
    if (editor) editor.update(options);
    else editor = library.mountWorkflowEditor(host.value, options);
  } catch (failure) {
    fail(failure);
  }
});
onMounted(load);
onScopeDispose(() => {
  active = false;
  sequence++;
  editor?.unmount();
  editor = undefined;
  detail.value = undefined;
});
</script>
<template>
  <el-dialog
    :model-value="true"
    title="工作流画布"
    width="96vw"
    top="3vh"
    :before-close="close"
    :close-on-click-modal="false"
    :close-on-press-escape="!saving"
  >
    <div class="graph-editor-heading">
      <el-input
        v-model="name"
        aria-label="流程名称"
        maxlength="160"
        :disabled="!canWrite || saving || loading"
      /><el-button :loading="loading" :disabled="saving" @click="load"
        >重新读取</el-button
      >
    </div>
    <el-alert v-if="error" :title="error" type="error" :closable="false" />
    <div v-loading="loading" class="graph-editor-frame">
      <div v-if="detail" ref="host" class="graph-editor-host" />
    </div>
  </el-dialog>
</template>
<style scoped>
.graph-editor-heading {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
}
.graph-editor-heading .el-input {
  max-width: 420px;
}
.graph-editor-frame,
.graph-editor-host {
  height: 76vh;
  min-height: 520px;
}
.graph-editor-frame {
  position: relative;
}
</style>
