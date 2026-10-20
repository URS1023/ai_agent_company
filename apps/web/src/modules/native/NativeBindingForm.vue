<script setup lang="ts">
import { computed, reactive, ref, watch } from "vue";
import type { Entity } from "../../api/client";
import SecretPicker from "./SecretPicker.vue";
import { applicationFields } from "./bindingSchema";
const props = defineProps<{
  entity?: Entity | null;
  kind: string;
  connections: Entity[];
  busy: boolean;
  errors: Record<string, string>;
}>();
const emit = defineEmits<{
  submit: [payload: Record<string, unknown>];
  cancel: [];
}>();
const form = reactive<Record<string, any>>({
  name: "",
  integration_id: "",
  native_id: "",
  native_model_id: "",
  release_id: "",
  role: "chat",
  app_mode: "workflow",
  credential_ref: "",
  description: "",
  enabled: true,
  parameters: {},
});
const knowledge = computed(() => props.kind === "knowledge-spaces");
const models = computed(() => props.kind === "model-profiles");
const schemaText = ref("");
const schemaError = ref("");
const availableConnections = computed(() =>
  props.connections.filter(
    (connection) =>
      (!knowledge.value && props.kind !== "automations") ||
      connection.engine === "dify",
  ),
);
const selectedEngine = computed(
  () =>
    props.connections.find(
      (connection) => connection.id === form.integration_id,
    )?.engine ??
    (props.entity?.app_mode === "openwebui_model" ? "openwebui" : undefined),
);
const applicationModes = computed(() =>
  selectedEngine.value === "openwebui"
    ? [{ value: "openwebui_model", label: "Open WebUI模型" }]
    : props.kind === "automations"
      ? [{ value: "workflow", label: "工作流" }]
      : [
          { value: "workflow", label: "工作流" },
          { value: "chatflow", label: "对话流" },
          { value: "agent-chat", label: "Agent" },
          { value: "agent_v2", label: "Agent工作流" },
        ],
);
watch(
  () => props.entity,
  (value) => {
    Object.assign(form, {
      name: value?.name ?? "",
      integration_id: value?.integration_id ?? "",
      native_id: value?.native_id ?? "",
      native_model_id: value?.native_model_id ?? "",
      release_id: value?.release_id ?? "",
      role: value?.role ?? "chat",
      app_mode: value?.app_mode ?? "workflow",
      credential_ref: value?.credential_ref ?? "",
      description: value?.description ?? "",
      enabled: value?.enabled ?? true,
      parameters: value?.parameters ?? {},
    });
    schemaText.value = value?.input_schema
      ? JSON.stringify(value.input_schema, null, 2)
      : "";
    schemaError.value = "";
  },
  { immediate: true },
);
watch(applicationModes, (modes) => {
  if (!props.entity && !modes.some((mode) => mode.value === form.app_mode))
    form.app_mode = modes[0]!.value;
});
function submit() {
  const data: Record<string, unknown> = {
    name: form.name,
    enabled: form.enabled,
  };
  if (!props.entity) data.integration_id = form.integration_id;
  if (models.value)
    Object.assign(data, {
      native_model_id: form.native_model_id,
      ...(props.entity ? {} : { role: form.role }),
      parameters: form.parameters,
      credential_ref: form.credential_ref || null,
    });
  else if (knowledge.value)
    Object.assign(data, {
      ...(props.entity ? {} : { native_id: form.native_id }),
      description: form.description,
      credential_ref: form.credential_ref || null,
    });
  else {
    try {
      Object.assign(
        data,
        applicationFields(
          {
            native_id: form.native_id,
            app_mode: form.app_mode,
            release_id: form.release_id,
            credential_ref: form.credential_ref,
          },
          schemaText.value,
          !!props.entity,
        ),
      );
      schemaError.value = "";
    } catch (error) {
      schemaError.value =
        error instanceof Error ? error.message : "输入字段定义无效";
      return;
    }
  }
  emit("submit", data);
}
</script>
<template>
  <el-form label-position="top" @submit.prevent="submit"
    ><div class="form-grid">
      <el-form-item label="名称" :error="errors.name"
        ><el-input v-model="form.name" required maxlength="120" /></el-form-item
      ><el-form-item label="连接" :error="errors.integration_id"
        ><el-select
          v-model="form.integration_id"
          :disabled="!!entity"
          filterable
          ><el-option
            v-for="connection in availableConnections"
            :key="connection.id"
            :value="connection.id"
            :label="connection.name" /></el-select></el-form-item
      ><el-form-item
        v-if="!models"
        :label="knowledge ? '知识库ID' : '应用ID'"
        :error="errors.native_id"
        ><el-input
          v-model="form.native_id"
          :disabled="!!entity && knowledge"
          required /></el-form-item
      ><el-form-item v-if="!models && !knowledge" label="应用类型"
        ><el-select v-model="form.app_mode" :disabled="!!entity"
          ><el-option
            v-for="mode in applicationModes"
            :key="mode.value"
            :value="mode.value"
            :label="mode.label" /></el-select></el-form-item
      ><el-form-item
        v-if="!models && !knowledge && form.app_mode === 'workflow'"
        label="发布版本ID"
        required
        :error="errors.release_id"
        ><el-input v-model="form.release_id" required /></el-form-item
      ><el-form-item
        v-if="models"
        label="模型ID"
        :error="errors.native_model_id"
        ><el-input v-model="form.native_model_id" required /></el-form-item
      ><el-form-item v-if="models" label="模型用途"
        ><el-select v-model="form.role" :disabled="!!entity"
          ><el-option
            v-for="role in [
              { value: 'chat', label: '对话' },
              { value: 'reasoning', label: '推理' },
              { value: 'embedding', label: '向量化' },
              { value: 'reranker', label: '重排' },
              { value: 'vision', label: '视觉' },
              { value: 'speech', label: '语音合成' },
              { value: 'transcription', label: '语音识别' },
            ]"
            :key="role.value"
            :value="role.value"
            :label="role.label" /></el-select></el-form-item
      ><el-form-item
        v-if="
          models ||
          knowledge ||
          !['agent_v2', 'openwebui_model'].includes(form.app_mode)
        "
        :label="models || knowledge ? '凭据引用' : '应用凭据'"
        :error="errors.credential_ref"
        ><SecretPicker v-model="form.credential_ref" /></el-form-item
      ><el-form-item label="启用"
        ><el-switch v-model="form.enabled" /></el-form-item
      ><el-form-item
        v-if="
          !models &&
          !knowledge &&
          !['agent_v2', 'openwebui_model'].includes(form.app_mode)
        "
        class="full-width"
        label="输入字段（JSON Schema）"
        :error="schemaError || errors.input_schema"
        ><el-input
          v-model="schemaText"
          type="textarea"
          :rows="7"
          spellcheck="false"
          placeholder='{"type":"object","properties":{},"additionalProperties":false}'
          @input="schemaError = ''" /></el-form-item
      ><el-form-item v-if="knowledge" class="full-width" label="描述"
        ><el-input v-model="form.description" type="textarea" maxlength="1000"
      /></el-form-item>
    </div>
    <div class="row-actions" style="justify-content: flex-end">
      <el-button @click="emit('cancel')">取消</el-button
      ><el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!form.integration_id"
        >保存</el-button
      >
    </div></el-form
  >
</template>
