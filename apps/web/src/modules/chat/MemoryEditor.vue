<script setup lang="ts">
import { memoryTypes, type MemoryType } from "./useMemories";
defineProps<{
  visible: boolean;
  editing: boolean;
  readonly: boolean;
  saving: boolean;
  error: string;
  fields: Record<string, string>;
  conflict: boolean;
  uncertain: boolean;
}>();
const content = defineModel<string>("content", { required: true });
const type = defineModel<MemoryType>("type", { required: true });
const emit = defineEmits<{ close: []; save: []; latest: []; check: [] }>();
</script>
<template>
  <el-dialog
    :model-value="visible"
    :title="readonly ? '查看记忆' : editing ? '编辑记忆' : '新增记忆'"
    width="640px"
    :close-on-click-modal="false"
    :close-on-press-escape="!saving"
    :show-close="!saving"
    @update:model-value="!$event && emit('close')"
  >
    <el-alert
      v-if="error"
      :title="error"
      type="error"
      show-icon
      :closable="false"
      class="memory-editor-error"
    />
    <el-form label-position="top" @submit.prevent="emit('save')">
      <el-form-item label="类型" :error="fields.type">
        <el-select
          v-model="type"
          aria-label="类型"
          :disabled="saving || readonly"
          ><el-option
            v-for="item in memoryTypes"
            :key="item.value"
            :value="item.value"
            :label="item.label"
        /></el-select>
      </el-form-item>
      <el-form-item label="内容" required :error="fields.content">
        <el-input
          v-model="content"
          type="textarea"
          aria-label="内容"
          :rows="8"
          :maxlength="6000"
          :show-word-limit="!readonly"
          resize="vertical"
          :disabled="saving"
          :readonly="readonly"
        />
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button :disabled="saving" @click="emit('close')">{{
        readonly ? "关闭" : "取消"
      }}</el-button>
      <el-button v-if="conflict" @click="emit('latest')">读取最新</el-button>
      <el-button v-else-if="uncertain" @click="emit('check')"
        >核对列表</el-button
      >
      <el-button
        v-else-if="!readonly"
        type="primary"
        :loading="saving"
        :disabled="!content.trim()"
        @click="emit('save')"
        >保存</el-button
      >
    </template>
  </el-dialog>
</template>
<style scoped>
.memory-editor-error {
  margin-bottom: var(--sp-4);
}
</style>
