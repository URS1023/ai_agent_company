<script setup lang="ts">
/** 复用qKnow系统字典/参数/公告的表单组织方式，保留类型各自的编辑控件。
 * 来源：qKnow-develop/qknow-ui/src/views/system/system/{dict,config,notice}/；许可见licenses/qKnow-LICENSE.txt。
 */
import { computed, reactive, ref } from "vue";
import type { FormInstance } from "element-plus";
import { ApiError, api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import DictionaryEntries from "./DictionaryEntries.vue";
import FormFieldsEditor from "./FormFieldsEditor.vue";
import {
  resourcePath,
  sections,
  type SupportKind,
  type SupportResource,
} from "./support";
const props = defineProps<{
  kind: SupportKind;
  record?: SupportResource;
  editable: boolean;
}>();
const emit = defineEmits<{ close: []; saved: [record: SupportResource] }>();
const op = useOperation();
const form = ref<FormInstance>();
const defaults = {
  dictionary: { entries: [], description: "" },
  parameter: { value_type: "string", value: "", description: "" },
  announcement: {
    category: "announcement",
    content: "",
    publish_at: null,
    expires_at: null,
  },
  form: { fields: [], description: "" },
};
const draft = reactive({
  code: props.record?.code ?? "",
  name: props.record?.name ?? "",
  scope: props.record?.scope ?? "workspace",
  enabled: props.record?.enabled ?? true,
  definition: JSON.parse(
    JSON.stringify(props.record?.definition ?? defaults[props.kind]),
  ) as Record<string, any>,
});
const jsonValue = ref(
  draft.definition.value_type === "json"
    ? JSON.stringify(draft.definition.value, null, 2)
    : "{}",
);
const title = computed(
  () =>
    `${props.editable ? (props.record ? "编辑" : "新增") : "查看"}${sections.find((item) => item.kind === props.kind)!.label}`,
);
const rules = {
  name: [{ required: true, message: "请填写名称", trigger: "blur" }],
  code: [
    { required: true, message: "请填写编码", trigger: "blur" },
    {
      pattern: /^[a-z][a-z0-9_.-]*$/,
      message: "以小写字母开头，可使用数字、点、下划线和连字符",
      trigger: "blur",
    },
  ],
};
function changeType(value: string) {
  draft.definition.value =
    value === "boolean"
      ? false
      : value === "number"
        ? 0
        : value === "json"
          ? {}
          : "";
  jsonValue.value = "{}";
}
async function save() {
  if (
    !props.editable ||
    !form.value ||
    !(await form.value.validate().catch(() => false))
  )
    return;
  const result = await op.run(async () => {
    const definition = JSON.parse(JSON.stringify(draft.definition));
    if (props.kind === "parameter" && definition.value_type === "json") {
      try {
        definition.value = JSON.parse(jsonValue.value);
      } catch {
        throw new ApiError(422, "parameter_json_invalid", "请输入有效的JSON", {
          value: "JSON格式无效",
        });
      }
    }
    if (props.kind === "announcement") {
      definition.publish_at = definition.publish_at
        ? new Date(definition.publish_at).toISOString()
        : null;
      definition.expires_at = definition.expires_at
        ? new Date(definition.expires_at).toISOString()
        : null;
    }
    const payload = {
      name: draft.name,
      scope: draft.scope,
      enabled: draft.enabled,
      definition,
    };
    return props.record
      ? api.patch<SupportResource>(resourcePath(props.kind, props.record.id), {
          ...payload,
          revision: props.record.revision,
        })
      : api.post<SupportResource>(resourcePath(props.kind), {
          ...payload,
          code: draft.code,
        });
  }, "已保存");
  if (result) emit("saved", result);
}
</script>
<template>
  <el-dialog
    :model-value="true"
    :title="title"
    :width="
      kind === 'dictionary' || kind === 'form'
        ? 'min(1000px, 95vw)'
        : 'min(650px, 95vw)'
    "
    :close-on-click-modal="false"
    @close="emit('close')"
  >
    <el-form
      ref="form"
      :model="draft"
      :rules="rules"
      label-position="top"
      :disabled="!editable || op.busy.value"
      @submit.prevent="save"
    >
      <div class="base-fields">
        <el-form-item label="名称" prop="name" :error="op.fields.value.name"
          ><el-input v-model="draft.name" maxlength="160"
        /></el-form-item>
        <el-form-item label="编码" prop="code" :error="op.fields.value.code"
          ><el-input v-model="draft.code" :disabled="!!record" maxlength="80"
        /></el-form-item>
        <el-form-item label="作用域" :error="op.fields.value.scope"
          ><el-select v-model="draft.scope"
            ><el-option label="当前工作区" value="workspace" /><el-option
              label="个人"
              value="personal" /></el-select
        ></el-form-item>
        <el-form-item label="启用"
          ><el-switch v-model="draft.enabled"
        /></el-form-item>
      </div>
      <el-form-item
        v-if="op.fields.value.definition"
        :error="op.fields.value.definition"
      />
      <DictionaryEntries
        v-if="kind === 'dictionary'"
        v-model="draft.definition.entries"
        :disabled="!editable || op.busy.value"
      />
      <template v-else-if="kind === 'parameter'">
        <el-form-item label="值类型"
          ><el-select v-model="draft.definition.value_type" @change="changeType"
            ><el-option
              v-for="item in [
                { value: 'string', label: '文本' },
                { value: 'number', label: '数值' },
                { value: 'boolean', label: '开关' },
                { value: 'json', label: 'JSON' },
              ]"
              :key="item.value"
              :label="item.label"
              :value="item.value" /></el-select
        ></el-form-item>
        <el-form-item label="值" :error="op.fields.value.value">
          <el-switch
            v-if="draft.definition.value_type === 'boolean'"
            v-model="draft.definition.value"
          />
          <el-input-number
            v-else-if="draft.definition.value_type === 'number'"
            v-model="draft.definition.value"
          />
          <el-input
            v-else-if="draft.definition.value_type === 'json'"
            v-model="jsonValue"
            type="textarea"
            :rows="8"
            class="code-field"
          />
          <el-input
            v-else
            v-model="draft.definition.value"
            type="textarea"
            :rows="3"
          />
        </el-form-item>
      </template>
      <template v-else-if="kind === 'announcement'">
        <el-form-item label="类型"
          ><el-radio-group v-model="draft.definition.category"
            ><el-radio-button value="announcement">公告</el-radio-button
            ><el-radio-button value="notice"
              >通知</el-radio-button
            ></el-radio-group
          ></el-form-item
        >
        <el-form-item label="内容" required
          ><el-input
            v-model="draft.definition.content"
            type="textarea"
            :rows="8"
            maxlength="20000"
            show-word-limit
        /></el-form-item>
        <div class="base-fields">
          <el-form-item label="发布时间"
            ><el-date-picker
              v-model="draft.definition.publish_at"
              type="datetime"
              clearable
              placeholder="立即发布" /></el-form-item
          ><el-form-item label="失效时间" :error="op.fields.value.expires_at"
            ><el-date-picker
              v-model="draft.definition.expires_at"
              type="datetime"
              clearable
          /></el-form-item>
        </div>
      </template>
      <FormFieldsEditor
        v-else
        v-model="draft.definition.fields"
        :disabled="!editable || op.busy.value"
      />
      <el-form-item
        v-if="kind !== 'announcement'"
        label="备注"
        class="description-field"
        ><el-input
          v-model="draft.definition.description"
          type="textarea"
          :rows="2"
          maxlength="2000"
      /></el-form-item>
    </el-form>
    <template #footer
      ><el-button @click="emit('close')">关闭</el-button
      ><el-button
        v-if="editable"
        type="primary"
        :loading="op.busy.value"
        @click="save"
        >保存</el-button
      ></template
    >
  </el-dialog>
</template>
<style scoped>
.base-fields {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 0 16px;
}
.base-fields :deep(.el-select),
.base-fields :deep(.el-date-editor) {
  width: 100%;
}
.description-field {
  margin-top: 18px;
}
.code-field :deep(textarea) {
  font-family: Consolas, monospace;
}
@media (max-width: 600px) {
  .base-fields {
    grid-template-columns: 1fr;
  }
}
</style>
