<script setup lang="ts">
import { computed, onScopeDispose, ref, shallowRef, useTemplateRef } from "vue";
import {
  knowledgeExtensions,
  knowledgeFileError,
  fileSize,
  type KnowledgeImport,
} from "./knowledge";
import KnowledgeFilePicker from "./KnowledgeFilePicker.vue";
defineProps<{
  busy: boolean;
  fields: Record<string, string>;
  error: string;
  uncertain: boolean;
  canFiles: boolean;
}>();
const emit = defineEmits<{
  submit: [input: KnowledgeImport];
  cancel: [];
  check: [];
}>();
const mode = shallowRef<"upload" | "paste" | "file">("upload");
const name = shallowRef(""),
  text = shallowRef(""),
  fileId = shallowRef(""),
  localError = shallowRef("");
const file = ref<File>();
const input = useTemplateRef<HTMLInputElement>("input");
const ready = computed(() =>
  mode.value === "upload"
    ? Boolean(file.value && !localError.value)
    : mode.value === "file"
      ? Boolean(fileId.value)
      : Boolean(name.value.trim() && text.value.trim()),
);
function choose(event: Event) {
  const selected = (event.target as HTMLInputElement).files?.[0];
  file.value = undefined;
  localError.value = "";
  if (selected) {
    localError.value = knowledgeFileError(selected);
    if (!localError.value) {
      file.value = selected;
      name.value = selected.name.slice(0, 240);
    }
  }
  if (input.value) input.value.value = "";
}
function submit() {
  if (!ready.value) return;
  if (mode.value === "upload" && file.value)
    emit("submit", {
      kind: "upload",
      file: file.value,
      name: name.value.trim() || undefined,
    });
  else if (mode.value === "file")
    emit("submit", {
      kind: "file",
      file_id: fileId.value,
      name: name.value.trim() || undefined,
    });
  else
    emit("submit", {
      kind: "paste",
      name: name.value.trim(),
      text: text.value.trim(),
    });
}
onScopeDispose(() => {
  file.value = undefined;
  name.value = "";
  text.value = "";
  fileId.value = "";
});
</script>
<template>
  <el-alert
    v-if="error"
    :title="error"
    type="error"
    show-icon
    :closable="false"
    class="import-error"
  />
  <el-form
    label-position="top"
    @submit.prevent="!busy && !uncertain && submit()"
  >
    <el-form-item
      ><el-radio-group
        v-model="mode"
        :disabled="busy || uncertain"
        aria-label="导入方式"
      >
        <el-radio-button value="upload">上传文件</el-radio-button>
        <el-radio-button value="paste">粘贴文本</el-radio-button>
        <el-radio-button v-if="canFiles" value="file">平台文件</el-radio-button>
      </el-radio-group></el-form-item
    >
    <el-form-item
      v-if="mode === 'upload'"
      label="文件"
      required
      :error="localError || fields.file"
    >
      <div class="file-choice">
        <el-button :disabled="busy || uncertain" @click="input?.click()"
          >选择文件</el-button
        ><span v-if="file" class="file-name"
          >{{ file.name }} · {{ fileSize(file.size) }}</span
        >
      </div>
      <input
        ref="input"
        type="file"
        hidden
        :accept="knowledgeExtensions.join(',')"
        @change="choose"
      />
    </el-form-item>
    <el-form-item
      v-if="mode === 'file'"
      label="平台文件"
      required
      :error="fields.file_id"
      ><KnowledgeFilePicker
        v-model="fileId"
        @name="name = $event.slice(0, 240)"
    /></el-form-item>
    <el-form-item
      label="文档名称"
      :required="mode === 'paste'"
      :error="fields.name"
      ><el-input
        v-model="name"
        aria-label="文档名称"
        :maxlength="240"
        :disabled="busy || uncertain"
    /></el-form-item>
    <el-form-item
      v-if="mode === 'paste'"
      label="正文"
      required
      :error="fields.text"
      ><el-input
        v-model="text"
        type="textarea"
        aria-label="正文"
        :rows="10"
        :maxlength="200000"
        :disabled="busy || uncertain"
    /></el-form-item>
    <div class="row-actions import-actions">
      <el-button :disabled="busy" @click="emit('cancel')">取消</el-button
      ><el-button v-if="uncertain" @click="emit('check')">核对列表</el-button
      ><el-button
        v-else
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!ready"
        >导入</el-button
      >
    </div>
  </el-form>
</template>
<style scoped>
.import-error {
  margin-bottom: var(--sp-4);
}
.file-choice {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}
.file-name {
  overflow-wrap: anywhere;
}
.import-actions {
  justify-content: flex-end;
}
</style>
