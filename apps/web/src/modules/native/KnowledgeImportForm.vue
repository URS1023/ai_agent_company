<script setup lang="ts">
/** 复用平台文件上传与选择；导入只提交已授权文件ID。 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import {
  api,
  request,
  ApiError,
  type Entity,
  type ListResult,
} from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import { importFileError, textExtensions } from "./knowledge";
const props = defineProps<{ busy: boolean; errors: Record<string, string> }>();
const emit = defineEmits<{
  submit: [payload: { file_id: string; name?: string }];
  cancel: [];
}>();
const session = useSession();
const op = useOperation();
const fileOp = useOperation();
const files = ref<Entity[]>([]);
const selected = ref("");
const name = ref("");
const search = ref("");
const input = ref<HTMLInputElement>();
const current = computed(() =>
  files.value.find((file) => file.id === selected.value),
);
let active = true,
  queued = false;
async function load() {
  if (!active || !session.can("m17.read")) return;
  if (fileOp.busy.value) {
    queued = true;
    return;
  }
  await fileOp.run(async () => {
    const result = await api.get<ListResult<Entity>>(
      `/files?limit=50&q=${encodeURIComponent(search.value)}`,
    );
    if (!active) return;
    const retained = current.value;
    files.value = result.items.filter((file) => !importFileError(file));
    if (retained && !files.value.some((file) => file.id === retained.id))
      files.value.unshift(retained);
  });
  if (queued) {
    queued = false;
    await load();
  }
}
function remoteSearch(value: string) {
  search.value = value;
  void load();
}
function select(value: string) {
  name.value =
    files.value.find((file) => file.id === value)?.name?.slice(0, 200) ?? "";
}
async function upload(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0];
  if (!file) return;
  await op.run(async () => {
    const error = importFileError(file);
    if (error)
      throw new ApiError(422, "knowledge_file_invalid", error, {
        file_id: error,
      });
    try {
      new TextDecoder("utf-8", { fatal: true }).decode(
        await file.arrayBuffer(),
      );
    } catch {
      throw new ApiError(
        422,
        "knowledge_file_encoding",
        "请选择UTF-8编码文件",
        { file_id: "文件需要使用UTF-8编码" },
      );
    }
    const body = new FormData();
    body.append("file", file);
    const uploaded = await request<Entity>("/files", { method: "POST", body });
    files.value.unshift(uploaded);
    selected.value = uploaded.id;
    select(uploaded.id);
  }, "文件已上传");
  if (input.value) input.value.value = "";
}
onMounted(load);
onBeforeUnmount(() => {
  active = false;
});
function submit() {
  if (selected.value && !props.busy && !op.busy.value)
    emit("submit", {
      file_id: selected.value,
      name: name.value.trim() || undefined,
    });
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit">
    <el-alert
      v-if="fileOp.error.value"
      :title="fileOp.error.value"
      type="error"
      :closable="false"
      ><el-button link @click="load">重新加载</el-button></el-alert
    >
    <el-form-item
      label="平台文件"
      :error="op.fields.value.file_id || errors.file_id"
    >
      <div class="file-control">
        <el-select
          v-model="selected"
          :disabled="busy || op.busy.value || !session.can('m17.read')"
          filterable
          remote
          :remote-method="remoteSearch"
          :loading="fileOp.busy.value"
          placeholder="选择文件"
          @change="select"
          ><el-option
            v-for="file in files"
            :key="file.id"
            :value="file.id"
            :label="file.name"
        /></el-select>
        <el-button
          v-if="session.can('m17.write')"
          :disabled="busy"
          :loading="op.busy.value"
          @click="input?.click()"
          >上传文件</el-button
        >
        <input
          ref="input"
          type="file"
          hidden
          :accept="textExtensions.join(',')"
          @change="upload"
        />
      </div>
    </el-form-item>
    <el-form-item label="文档名称" :error="errors.name"
      ><el-input
        v-model="name"
        :disabled="busy || op.busy.value"
        maxlength="200"
    /></el-form-item>
    <div class="row-actions import-actions">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!selected || op.busy.value"
        >导入</el-button
      >
    </div>
  </el-form>
</template>
<style scoped>
.file-control {
  display: flex;
  gap: 10px;
  width: 100%;
}
.file-control .el-select {
  flex: 1;
}
.import-actions {
  justify-content: flex-end;
}
</style>
