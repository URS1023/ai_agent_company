<script setup lang="ts">
/** 只读取本地公钥证书到表单；私钥校验在发送前阻断，服务端仍独立验证。 */
import { computed, reactive, ref } from "vue";

const props = defineProps<{
  name?: string;
  certificateRequired: boolean;
  busy: boolean;
  fields: Record<string, string>;
  submitLabel?: string;
}>();
const emit = defineEmits<{
  submit: [value: { name: string; certificate_pem: string }];
  cancel: [];
}>();
const form = reactive({ name: props.name ?? "", certificate_pem: "" });
const fileInput = ref<HTMLInputElement | null>(null);
const fileError = ref("");
const certificateError = computed(() => {
  if (fileError.value) return fileError.value;
  if (form.certificate_pem.includes("PRIVATE KEY"))
    return "请选择公钥证书，不能上传私钥";
  return props.fields.certificate_pem ?? "";
});
const valid = computed(
  () =>
    !!form.name.trim() &&
    (!props.certificateRequired ||
      (form.certificate_pem.includes("-----BEGIN CERTIFICATE-----") &&
        !fileError.value &&
        !form.certificate_pem.includes("PRIVATE KEY"))),
);
async function chooseFile(event: Event) {
  const input = event.target as HTMLInputElement;
  const file = input.files?.[0];
  input.value = "";
  if (!file) return;
  fileError.value = "";
  if (file.size > 24000) {
    fileError.value = "证书文件过大，请重新选择";
    return;
  }
  try {
    form.certificate_pem = await file.text();
    if (!form.name.trim())
      form.name = file.name.replace(/\.[^.]+$/, "").slice(0, 120);
  } catch {
    fileError.value = "无法读取所选文件";
  }
}
function submit() {
  if (!valid.value || props.busy) return;
  emit("submit", {
    name: form.name.trim(),
    certificate_pem: form.certificate_pem.trim(),
  });
}
</script>

<template>
  <el-form label-position="top" @submit.prevent="submit">
    <el-form-item label="名称" :error="fields.name" required>
      <el-input v-model="form.name" maxlength="120" required />
    </el-form-item>
    <el-form-item
      v-if="certificateRequired"
      label="公钥证书"
      :error="certificateError"
      required
    >
      <div class="certificate-field">
        <input
          ref="fileInput"
          type="file"
          accept=".pem,.crt,.cer"
          hidden
          aria-label="选择公钥证书文件"
          @change="chooseFile"
        />
        <el-button :disabled="busy" @click="fileInput?.click()"
          >选择证书</el-button
        >
        <el-input
          v-model="form.certificate_pem"
          type="textarea"
          :rows="9"
          :maxlength="24000"
          placeholder="-----BEGIN CERTIFICATE-----"
          spellcheck="false"
          autocomplete="off"
          aria-label="公钥证书PEM"
          @input="fileError = ''"
        />
      </div>
    </el-form-item>
    <div class="certificate-actions">
      <el-button :disabled="busy" @click="emit('cancel')">取消</el-button>
      <el-button
        type="primary"
        native-type="submit"
        :loading="busy"
        :disabled="!valid"
      >
        {{ submitLabel ?? (certificateRequired ? "登记" : "保存") }}
      </el-button>
    </div>
  </el-form>
</template>

<style scoped>
.certificate-field {
  display: grid;
  width: 100%;
  gap: 12px;
  justify-items: start;
}
.certificate-field :deep(textarea) {
  font-family: ui-monospace, monospace;
  font-size: 13px;
}
.certificate-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}
</style>
