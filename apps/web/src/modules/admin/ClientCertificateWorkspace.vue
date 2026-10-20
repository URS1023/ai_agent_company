<script setup lang="ts">
/** 沿用qKnow证书列表/表单组织，令牌与私钥不在证书管理页产生或持久化。 */
import { computed, onBeforeUnmount, ref, shallowRef, watch } from "vue";
import { ElMessageBox } from "element-plus";
import { api, request, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import ClientCertificateForm from "./ClientCertificateForm.vue";

interface Certificate {
  id: string;
  revision: number;
  name: string;
  fingerprint_sha256: string;
  subject_text: string;
  issuer_text: string;
  serial_number: string;
  algorithm: string;
  not_before: string;
  not_after: string;
  enabled: boolean;
  status: string;
  revoked_at: string | null;
  revocation_reason: string;
}
interface AuthInfo {
  client_id: string;
  audience: string;
  token_endpoint: string;
  grant_type: string;
  client_assertion_type: string;
  token_auth_method: string;
}
const props = defineProps<{
  identity: { id: string; name: string; enabled: boolean };
  canManage: boolean;
}>();
const open = defineModel<boolean>({ required: true });
const listOp = useOperation();
const op = useOperation();
const infoOp = useOperation();
const records = shallowRef<Certificate[]>([]);
const editDialog = ref(false);
const mode = ref<"register" | "edit" | "rotate">("register");
const selected = shallowRef<Certificate | null>(null);
const infoDialog = ref(false);
const info = shallowRef<AuthInfo | null>(null);
const infoCertificate = shallowRef<Certificate | null>(null);
const base = computed(
  () =>
    `/service-identities/${encodeURIComponent(props.identity.id)}/certificates`,
);
const title = computed(
  () =>
    ({
      register: "登记客户端证书",
      edit: "编辑证书名称",
      rotate: "轮换客户端证书",
    })[mode.value],
);
const statuses: Record<
  string,
  { label: string; type: "info" | "success" | "warning" }
> = {
  active: { label: "有效", type: "success" },
  revoked: { label: "已撤销", type: "info" },
  disabled: { label: "已停用", type: "info" },
  expired: { label: "已到期", type: "warning" },
  pending: { label: "未生效", type: "warning" },
};
const formatter = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
function date(value: string) {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : formatter.format(parsed);
}
let generation = 0;
let disposed = false;
async function load() {
  if (!open.value || listOp.busy.value) return;
  const expected = generation;
  await listOp.run(async () => {
    const result = await api.get<ListResult<Certificate>>(base.value);
    if (!disposed && open.value && expected === generation)
      records.value = result.items;
  });
}
function edit(row: Certificate | null, action: "register" | "edit" | "rotate") {
  selected.value = row;
  mode.value = action;
  op.fields.value = {};
  editDialog.value = true;
}
async function save(value: { name: string; certificate_pem: string }) {
  if (!props.canManage) return;
  const row = selected.value;
  const expected = generation;
  await op.run(async () => {
    if (mode.value === "edit" && row) {
      await api.patch(`${base.value}/${row.id}`, {
        name: value.name,
        revision: row.revision,
      });
    } else if (mode.value === "rotate" && row) {
      await ElMessageBox.confirm(
        `替换“${row.name}”？原证书及其接入令牌将失效。`,
        "轮换证书",
        {
          confirmButtonText: "轮换",
          cancelButtonText: "取消",
          type: "warning",
        },
      );
      if (disposed || expected !== generation) return;
      await api.post(`${base.value}/${row.id}/rotations`, {
        ...value,
        revision: row.revision,
      });
    } else {
      await api.post(base.value, value);
    }
    if (disposed || expected !== generation) return;
    editDialog.value = false;
    await load();
  });
}
async function changeEnabled(row: Certificate) {
  await op.run(async () => {
    const expected = generation;
    if (row.enabled) {
      await ElMessageBox.confirm(
        `停用“${row.name}”？已有接入令牌将失效。`,
        "停用证书",
        {
          confirmButtonText: "停用",
          cancelButtonText: "取消",
          type: "warning",
        },
      );
    }
    if (disposed || expected !== generation || !props.canManage) return;
    await api.patch(`${base.value}/${row.id}`, {
      enabled: !row.enabled,
      revision: row.revision,
    });
    if (!disposed && expected === generation) await load();
  });
}
async function revoke(row: Certificate) {
  await op.run(async () => {
    const expected = generation;
    const result = await ElMessageBox.prompt(
      `撤销“${row.name}”？该证书及其接入令牌将失效。`,
      "撤销证书",
      {
        confirmButtonText: "撤销",
        cancelButtonText: "取消",
        type: "warning",
        inputType: "textarea",
        inputPlaceholder: "撤销原因（选填）",
        inputValidator: (value: string) =>
          !value || value.length <= 500 || "最多500字",
      },
    );
    if (disposed || expected !== generation || !props.canManage) return;
    await api.post(`${base.value}/${row.id}/revocations`, {
      reason: result.value ?? "",
      revision: row.revision,
    });
    if (!disposed && expected === generation) await load();
  });
}
async function download(row: Certificate) {
  const expected = generation;
  await op.run(async () => {
    const blob = await request<Blob>(
      `${base.value}/${row.id}/public-certificate`,
      { asBlob: true },
    );
    if (disposed || expected !== generation) return;
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `client-${row.id}.pem`;
    anchor.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  });
}
async function showInfo(row: Certificate) {
  const expected = generation;
  await infoOp.run(async () => {
    const result = await api.get<AuthInfo>(
      `/service-identities/${encodeURIComponent(props.identity.id)}/client-auth`,
    );
    if (disposed || expected !== generation) return;
    info.value = result;
    infoCertificate.value = row;
    infoDialog.value = true;
  });
}
async function copy(value: string) {
  await infoOp.run(() => navigator.clipboard.writeText(value), "已复制");
}
function command(action: string, row: Certificate) {
  if (action === "download") void download(row);
  if (!props.canManage) return;
  if (action === "edit" || action === "rotate") edit(row, action);
  if (action === "enabled") void changeEnabled(row);
  if (action === "revoke") void revoke(row);
}
watch(
  open,
  (value) => {
    generation++;
    records.value = [];
    editDialog.value = false;
    infoDialog.value = false;
    info.value = null;
    if (value) void load();
  },
  { immediate: true },
);
watch(
  () => props.canManage,
  (allowed) => {
    if (!allowed) editDialog.value = false;
  },
);
onBeforeUnmount(() => {
  disposed = true;
  generation++;
});
</script>

<template>
  <el-drawer
    v-model="open"
    :title="`${identity.name} · 客户端证书`"
    size="1040px"
    destroy-on-close
  >
    <div class="certificate-toolbar">
      <el-tag v-if="!identity.enabled" type="info">服务身份已停用</el-tag>
      <span class="spacer" />
      <el-button :loading="listOp.busy.value" @click="load">刷新</el-button>
      <el-button
        v-if="canManage"
        type="primary"
        :disabled="op.busy.value"
        @click="edit(null, 'register')"
        >登记证书</el-button
      >
    </div>
    <el-table :data="records" stripe v-loading="listOp.busy.value">
      <el-table-column type="expand">
        <template #default="{ row }">
          <el-descriptions :column="1" border class="certificate-details">
            <el-descriptions-item label="主体">{{
              row.subject_text
            }}</el-descriptions-item>
            <el-descriptions-item label="签发者">{{
              row.issuer_text
            }}</el-descriptions-item>
            <el-descriptions-item label="序列号">{{
              row.serial_number
            }}</el-descriptions-item>
            <el-descriptions-item label="生效时间">{{
              date(row.not_before)
            }}</el-descriptions-item>
            <el-descriptions-item label="SHA-256指纹"
              ><span class="mono">{{
                row.fingerprint_sha256
              }}</span></el-descriptions-item
            >
            <el-descriptions-item v-if="row.revoked_at" label="撤销时间">{{
              date(row.revoked_at)
            }}</el-descriptions-item>
            <el-descriptions-item
              v-if="row.revocation_reason"
              label="撤销原因"
              >{{ row.revocation_reason }}</el-descriptions-item
            >
          </el-descriptions>
        </template>
      </el-table-column>
      <el-table-column prop="name" label="名称" min-width="170" />
      <el-table-column label="状态" width="100"
        ><template #default="{ row }"
          ><el-tag :type="statuses[row.status]?.type ?? 'warning'">{{
            statuses[row.status]?.label ?? "待核对"
          }}</el-tag></template
        ></el-table-column
      >
      <el-table-column prop="algorithm" label="签名算法" width="105" />
      <el-table-column label="到期时间" width="175"
        ><template #default="{ row }">{{
          date(row.not_after)
        }}</template></el-table-column
      >
      <el-table-column label="指纹" min-width="150"
        ><template #default="{ row }"
          ><el-tooltip :content="row.fingerprint_sha256"
            ><span class="mono"
              >{{ row.fingerprint_sha256.slice(0, 16) }}…</span
            ></el-tooltip
          ></template
        ></el-table-column
      >
      <el-table-column label="操作" width="140" fixed="right"
        ><template #default="{ row }">
          <el-button
            link
            type="primary"
            :disabled="infoOp.busy.value"
            @click="showInfo(row)"
            >接入</el-button
          >
          <el-dropdown
            trigger="click"
            :disabled="op.busy.value"
            @command="(action: string) => command(action, row)"
          >
            <el-button link type="primary" :disabled="op.busy.value"
              >更多</el-button
            >
            <template #dropdown
              ><el-dropdown-menu>
                <el-dropdown-item command="download"
                  >下载公钥证书</el-dropdown-item
                >
                <template v-if="canManage && !row.revoked_at">
                  <el-dropdown-item command="edit">编辑名称</el-dropdown-item>
                  <el-dropdown-item command="enabled">{{
                    row.enabled ? "停用" : "启用"
                  }}</el-dropdown-item>
                  <el-dropdown-item command="rotate">轮换证书</el-dropdown-item>
                  <el-dropdown-item command="revoke" divided
                    >撤销证书</el-dropdown-item
                  >
                </template>
              </el-dropdown-menu></template
            >
          </el-dropdown>
        </template></el-table-column
      >
    </el-table>
  </el-drawer>
  <el-dialog
    v-model="editDialog"
    :title="title"
    width="680px"
    destroy-on-close
    append-to-body
    :close-on-click-modal="false"
  >
    <ClientCertificateForm
      v-if="editDialog"
      :name="selected?.name"
      :certificate-required="mode !== 'edit'"
      :submit-label="
        mode === 'rotate' ? '轮换' : mode === 'edit' ? '保存' : '登记'
      "
      :busy="op.busy.value"
      :fields="op.fields.value"
      @submit="save"
      @cancel="editDialog = false"
    />
  </el-dialog>
  <el-dialog
    v-model="infoDialog"
    title="客户端接入"
    width="780px"
    destroy-on-close
    append-to-body
  >
    <el-descriptions
      v-if="info && infoCertificate"
      :column="1"
      border
      class="certificate-info"
    >
      <el-descriptions-item label="客户端ID"
        ><div class="copy-value">
          <code>{{ info.client_id }}</code
          ><el-button link type="primary" @click="copy(info.client_id)"
            >复制</el-button
          >
        </div></el-descriptions-item
      >
      <el-descriptions-item label="令牌地址"
        ><div class="copy-value">
          <code>{{ info.token_endpoint }}</code
          ><el-button link type="primary" @click="copy(info.token_endpoint)"
            >复制</el-button
          >
        </div></el-descriptions-item
      >
      <el-descriptions-item label="接收方 aud"
        ><div class="copy-value">
          <code>{{ info.audience }}</code
          ><el-button link type="primary" @click="copy(info.audience)"
            >复制</el-button
          >
        </div></el-descriptions-item
      >
      <el-descriptions-item label="证书 kid"
        ><div class="copy-value">
          <code>{{ infoCertificate.id }}</code
          ><el-button link type="primary" @click="copy(infoCertificate.id)"
            >复制</el-button
          >
        </div></el-descriptions-item
      >
      <el-descriptions-item label="签名算法">{{
        infoCertificate.algorithm
      }}</el-descriptions-item>
      <el-descriptions-item label="认证方式">{{
        info.token_auth_method
      }}</el-descriptions-item>
      <el-descriptions-item label="grant_type"
        ><code>{{ info.grant_type }}</code></el-descriptions-item
      >
      <el-descriptions-item label="client_assertion_type"
        ><div class="copy-value">
          <code>{{ info.client_assertion_type }}</code
          ><el-button
            link
            type="primary"
            @click="copy(info.client_assertion_type)"
            >复制</el-button
          >
        </div></el-descriptions-item
      >
    </el-descriptions>
  </el-dialog>
</template>

<style scoped>
.certificate-toolbar,
.copy-value {
  display: flex;
  align-items: center;
  gap: 12px;
}
.certificate-toolbar {
  margin-bottom: 16px;
}
.spacer {
  flex: 1;
}
.certificate-details {
  margin: 10px 20px;
}
.mono,
code {
  font-family: ui-monospace, monospace;
  overflow-wrap: anywhere;
}
.copy-value code {
  flex: 1;
  min-width: 0;
}
.copy-value :deep(.el-button) {
  flex-shrink: 0;
}
.certificate-info :deep(.el-descriptions__label) {
  width: 160px;
}
</style>
