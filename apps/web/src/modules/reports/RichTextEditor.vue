<script setup lang="ts">
/** 富文本只输出允许的排版标签；粘贴和失焦均清理属性及嵌入内容。 */
import { onMounted, ref, watch } from "vue";
import { safeRichText } from "./presentation";

const value = defineModel<string>({ default: "" });
const editor = ref<HTMLDivElement>();
const commands = [
  { command: "bold", label: "加粗", text: "B" },
  { command: "italic", label: "斜体", text: "I" },
  { command: "underline", label: "下划线", text: "U" },
  { command: "insertUnorderedList", label: "项目列表", text: "• 列表" },
  { command: "insertOrderedList", label: "编号列表", text: "1. 列表" },
  { command: "removeFormat", label: "清除格式", text: "清除" },
];
function sync() {
  if (!editor.value || document.activeElement === editor.value) return;
  editor.value.innerHTML = safeRichText(value.value);
}
function update() {
  if (editor.value) value.value = safeRichText(editor.value.innerHTML);
}
function format(command: string) {
  editor.value?.focus();
  document.execCommand(command, false);
  update();
}
function paste(event: ClipboardEvent) {
  event.preventDefault();
  const html = event.clipboardData?.getData("text/html");
  if (html) document.execCommand("insertHTML", false, safeRichText(html));
  else
    document.execCommand(
      "insertText",
      false,
      event.clipboardData?.getData("text/plain") ?? "",
    );
  update();
}
function blur() {
  update();
  sync();
}
watch(value, sync);
onMounted(sync);
</script>

<template>
  <div class="rich-editor">
    <div class="rich-toolbar" role="toolbar" aria-label="文字格式">
      <button
        v-for="item in commands"
        :key="item.command"
        type="button"
        :title="item.label"
        :aria-label="item.label"
        @mousedown.prevent
        @click="format(item.command)"
      >
        {{ item.text }}
      </button>
    </div>
    <div
      ref="editor"
      class="rich-body"
      contenteditable="true"
      role="textbox"
      aria-label="内容"
      aria-multiline="true"
      @input="update"
      @blur="blur"
      @paste="paste"
    />
  </div>
</template>

<style scoped>
.rich-editor {
  width: 100%;
  border: 1px solid #dcdfe6;
  border-radius: 4px;
  overflow: hidden;
}
.rich-toolbar {
  display: flex;
  flex-wrap: wrap;
  gap: 3px;
  padding: 5px;
  background: var(--surface-sunken);
  border-bottom: 1px solid #e4e7ed;
}
.rich-toolbar button {
  border: 0;
  border-radius: 3px;
  background: transparent;
  color: #303b50;
  padding: 4px 7px;
  cursor: pointer;
}
.rich-toolbar button:hover,
.rich-toolbar button:focus-visible {
  background: #e6efff;
}
.rich-body {
  min-height: 150px;
  max-height: 320px;
  overflow: auto;
  padding: 10px;
  line-height: 1.6;
  outline: none;
}
.rich-body:focus {
  box-shadow: inset 0 0 0 1px #3978f6;
}
.rich-body :deep(p) {
  margin: 0 0 8px;
}
.rich-body :deep(ul),
.rich-body :deep(ol) {
  padding-left: 20px;
}
</style>
