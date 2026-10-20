<script setup lang="ts">
/** 服务身份与短期令牌只通过现有身份API维护；原文令牌不进入列表、全局状态或持久化。 */
import {
  computed,
  onBeforeUnmount,
  onMounted,
  reactive,
  ref,
  shallowRef,
  watch,
} from "vue";
import { ElMessageBox } from "element-plus";
import { api, type Entity, type ListResult } from "../../api/client";
import { useOperation } from "../../composables/useOperation";
import { useSession } from "../../stores/session";
import PermissionSelector from "./PermissionSelector.vue";
import ClientCertificateWorkspace from "./ClientCertificateWorkspace.vue";
import { permissionLabel, scopeAllows } from "./permissionScopes";

interface ServiceIdentity extends Entity {
  name: string;
  description: string;
  caller_kind: "external" | "automation" | "integration";
  permissions: string[];
  enabled: boolean;
  created_at: string;
}
interface ServiceToken {
  id: string;
  identity_id: string;
  name: string;
  scopes: string[];
  expires_at: string;
  revoked: boolean;
  created_at: string;
}
interface IssuedToken extends ServiceToken {
  token: string;
  token_type: string;
}

const session = useSession();
const listOp = useOperation();
const op = useOperation();
const tokensOp = useOperation();
const copyOp = useOperation();
const certificateIdentity = shallowRef<ServiceIdentity | null>(null);
const certificateDrawer = ref(false);
const records = ref<ServiceIdentity[]>([]);
const search = ref("");
const canRead = computed(() => session.can("m14.read"));
const canManage = computed(() => session.can("m14.admin"));
const actorPermissions = computed(() => session.value?.permissions ?? []);
const filtered = computed(() =>
  records.value.filter((record) =>
    `${record.name} ${record.description}`
      .toLocaleLowerCase()
      .includes(search.value.trim().toLocaleLowerCase()),
  ),
);
const kinds = [
  { value: "external", label: "外部调用" },
  { value: "automation", label: "自动化" },
  { value: "integration", label: "系统集成" },
];
const format = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});
function date(value?: string) {
  if (!value) return "—";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? "—" : format.format(parsed);
}
function manageable(identity: ServiceIdentity) {
  return (
    canManage.value &&
    identity.permissions.every((permission) =>
      scopeAllows(actorPermissions.value, permission),
    )
  );
}
function replaceIdentity(identity: ServiceIdentity) {
  const index = records.value.findIndex((item) => item.id === identity.id);
  if (index < 0) records.value.unshift(identity);
  else records.value.splice(index, 1, identity);
  if (certificateIdentity.value?.id === identity.id)
    certificateIdentity.value = identity;
  if (tokenIdentity.value?.id === identity.id) {
    tokenIdentity.value = identity;
    if (!identity.enabled) {
      tokens.value = tokens.value.map((token) => ({ ...token, revoked: true }));
      closeSensitive();
    }
  }
}

// 工作区切换、关闭或离开后，迟到响应不得重新展示上一工作区的结果或原文令牌。
let epoch = 0;
let issueEpoch = 0;
let disposed = false;
async function load() {
  if (!canRead.value || listOp.busy.value) return;
  const expected = epoch;
  await listOp.run(async () => {
    const result = await api.get<ListResult<ServiceIdentity>>(
      "/service-identities",
    );
    if (!disposed && expected === epoch) {
      records.value = result.items;
      if (certificateIdentity.value) {
        const current = result.items.find(
          (item) => item.id === certificateIdentity.value?.id,
        );
        if (current) certificateIdentity.value = current;
        else {
          certificateDrawer.value = false;
          certificateIdentity.value = null;
        }
      }
      if (tokenIdentity.value) {
        const current = result.items.find(
          (item) => item.id === tokenIdentity.value?.id,
        );
        if (current) replaceIdentity(current);
        else tokenDrawer.value = false;
      }
    }
  });
  // 仅因用户切换工作区而读取新范围；失败不会重试同一请求。
  if (!disposed && expected !== epoch) void load();
}

const dialog = ref(false);
const selected = ref<ServiceIdentity | null>(null);
const form = reactive({
  name: "",
  description: "",
  caller_kind: "external" as ServiceIdentity["caller_kind"],
  permissions: [] as string[],
});
function edit(identity: ServiceIdentity | null) {
  selected.value = identity;
  Object.assign(form, {
    name: identity?.name ?? "",
    description: identity?.description ?? "",
    caller_kind: identity?.caller_kind ?? "external",
    permissions: [...(identity?.permissions ?? [])],
  });
  dialog.value = true;
}
async function save() {
  if (!canManage.value || !form.name.trim() || !form.permissions.length) return;
  const expected = epoch;
  const current = selected.value;
  await op.run(async () => {
    const payload = {
      name: form.name.trim(),
      description: form.description.trim(),
      permissions: [...form.permissions],
    };
    const result = current
      ? await api.patch<ServiceIdentity>(
          `/service-identities/${encodeURIComponent(current.id)}`,
          { ...payload, revision: current.revision },
        )
      : await api.post<ServiceIdentity>("/service-identities", {
          ...payload,
          caller_kind: form.caller_kind,
        });
    if (disposed || expected !== epoch) return;
    replaceIdentity(result);
    dialog.value = false;
  });
}
async function setEnabled(identity: ServiceIdentity) {
  if (!manageable(identity)) return;
  await op.run(async () => {
    const expected = epoch;
    if (identity.enabled)
      await ElMessageBox.confirm(
        `停用“${identity.name}”后，已签发令牌将立即失效。`,
        "停用服务身份",
        {
          confirmButtonText: "停用",
          cancelButtonText: "取消",
          type: "warning",
        },
      );
    if (disposed || expected !== epoch) return;
    const result = await api.patch<ServiceIdentity>(
      `/service-identities/${encodeURIComponent(identity.id)}`,
      { revision: identity.revision, enabled: !identity.enabled },
    );
    if (!disposed && expected === epoch) replaceIdentity(result);
  });
}

const tokenIdentity = ref<ServiceIdentity | null>(null);
const tokenDrawer = ref(false);
const tokens = ref<ServiceToken[]>([]);
const now = shallowRef(Date.now());
let clock: ReturnType<typeof setInterval> | undefined;
function tokenStatus(token: ServiceToken) {
  if (token.revoked) return { label: "已撤销", type: "info" as const };
  const expiry = new Date(token.expires_at).getTime();
  if (!Number.isFinite(expiry))
    return { label: "待核对", type: "warning" as const };
  if (expiry <= now.value) return { label: "已过期", type: "warning" as const };
  return { label: "生效中", type: "success" as const };
}
function tokenMetadata(token: ServiceToken): ServiceToken {
  return {
    id: token.id,
    identity_id: token.identity_id,
    name: token.name,
    scopes: [...token.scopes],
    expires_at: token.expires_at,
    revoked: token.revoked,
    created_at: token.created_at,
  };
}
async function loadTokens() {
  const identity = tokenIdentity.value;
  if (!identity || !canRead.value) return;
  const expected = epoch;
  await tokensOp.run(async () => {
    const result = await api.get<ListResult<ServiceToken>>(
      `/service-identities/${encodeURIComponent(identity.id)}/tokens`,
    );
    if (
      !disposed &&
      expected === epoch &&
      tokenIdentity.value?.id === identity.id &&
      tokenDrawer.value
    )
      tokens.value = result.items.map(tokenMetadata);
  });
}
async function openTokens(identity: ServiceIdentity) {
  clearIssued();
  tokens.value = [];
  tokenIdentity.value = identity;
  tokenDrawer.value = true;
  await loadTokens();
}
async function revoke(token: ServiceToken) {
  const identity = tokenIdentity.value;
  if (
    !identity ||
    !manageable(identity) ||
    tokenStatus(token).label !== "生效中"
  )
    return;
  await op.run(async () => {
    const expected = epoch;
    await ElMessageBox.confirm(`撤销“${token.name}”？`, "撤销令牌", {
      confirmButtonText: "撤销",
      cancelButtonText: "取消",
      type: "warning",
    });
    if (disposed || expected !== epoch) return;
    const result = await api.delete<{ id: string; revoked: boolean }>(
      `/service-identities/${encodeURIComponent(identity.id)}/tokens/${encodeURIComponent(token.id)}`,
    );
    if (
      !disposed &&
      expected === epoch &&
      tokenIdentity.value?.id === identity.id &&
      result.revoked
    )
      tokens.value = tokens.value.map((item) =>
        item.id === result.id ? { ...item, revoked: true } : item,
      );
  });
}

const issueDialog = ref(false);
const tokenForm = reactive({
  name: "",
  ttl_minutes: 30,
  scopes: [] as string[],
});
const issueGrants = computed(() => [
  actorPermissions.value,
  tokenIdentity.value?.permissions ?? [],
]);
function startIssue() {
  if (
    !tokenIdentity.value ||
    !manageable(tokenIdentity.value) ||
    !tokenIdentity.value.enabled
  )
    return;
  clearIssued();
  Object.assign(tokenForm, {
    name: "",
    ttl_minutes: 30,
    scopes: [...tokenIdentity.value.permissions],
  });
  issueDialog.value = true;
}
const issuedDialog = ref(false);
const issuedToken = shallowRef("");
const issuedMetadata = shallowRef<ServiceToken | null>(null);
function clearIssued() {
  issuedToken.value = "";
  issuedMetadata.value = null;
  issuedDialog.value = false;
}
async function issue() {
  const identity = tokenIdentity.value;
  if (
    !identity ||
    !identity.enabled ||
    !manageable(identity) ||
    !tokenForm.name.trim() ||
    !tokenForm.scopes.length
  )
    return;
  const expected = epoch;
  const expectedIssue = ++issueEpoch;
  await op.run(async () => {
    const result = await api.post<IssuedToken>(
      `/service-identities/${encodeURIComponent(identity.id)}/tokens`,
      {
        name: tokenForm.name.trim(),
        ttl_minutes: tokenForm.ttl_minutes,
        scopes: [...tokenForm.scopes],
      },
    );
    if (
      disposed ||
      expected !== epoch ||
      tokenIdentity.value?.id !== identity.id ||
      !tokenDrawer.value
    )
      return;
    const metadata = tokenMetadata(result);
    tokens.value = [
      metadata,
      ...tokens.value.filter((item) => item.id !== metadata.id),
    ];
    if (expectedIssue !== issueEpoch || !issueDialog.value) return;
    issueDialog.value = false;
    issuedToken.value = result.token;
    issuedMetadata.value = metadata;
    issuedDialog.value = true;
  });
}
async function copy() {
  if (!issuedToken.value) return;
  await copyOp.run(async () => {
    await navigator.clipboard.writeText(issuedToken.value);
  }, "令牌已复制");
}
function closeSensitive() {
  issueEpoch++;
  issueDialog.value = false;
  clearIssued();
}
function onVisibility() {
  if (document.visibilityState === "hidden") closeSensitive();
}
watch(
  issuedDialog,
  (value) => {
    if (!value) {
      issuedToken.value = "";
      issuedMetadata.value = null;
    }
  },
  { flush: "sync" },
);
watch(
  issueDialog,
  (value) => {
    if (!value) issueEpoch++;
  },
  { flush: "sync" },
);
watch(
  tokenDrawer,
  (value) => {
    if (!value) {
      closeSensitive();
      tokens.value = [];
      tokenIdentity.value = null;
    }
  },
  { flush: "sync" },
);
watch(
  () => session.value?.workspace.id,
  () => {
    epoch++;
    closeSensitive();
    dialog.value = false;
    tokenDrawer.value = false;
    certificateDrawer.value = false;
    certificateIdentity.value = null;
    records.value = [];
    void load();
  },
);
watch(canRead, (allowed) => {
  if (!allowed) {
    epoch++;
    closeSensitive();
    dialog.value = false;
    tokenDrawer.value = false;
    certificateDrawer.value = false;
    certificateIdentity.value = null;
    records.value = [];
  }
});
watch(canManage, (allowed) => {
  if (!allowed) {
    closeSensitive();
    dialog.value = false;
  }
});
onMounted(() => {
  clock = setInterval(() => {
    now.value = Date.now();
  }, 15000);
  window.addEventListener("pagehide", closeSensitive);
  document.addEventListener("visibilitychange", onVisibility);
  void load();
});
onBeforeUnmount(() => {
  disposed = true;
  epoch++;
  closeSensitive();
  if (clock) clearInterval(clock);
  window.removeEventListener("pagehide", closeSensitive);
  document.removeEventListener("visibilitychange", onVisibility);
});
</script>

<template>
  <div class="service-identities">
    <el-result v-if="!canRead" icon="warning" title="暂无访问权限" />
    <template v-else>
      <div class="service-toolbar">
        <el-input
          v-model="search"
          placeholder="搜索服务身份"
          clearable
          class="service-search"
          aria-label="搜索服务身份"
        /><span class="spacer" /><el-button
          :loading="listOp.busy.value"
          @click="load"
          >刷新</el-button
        ><el-button
          v-if="canManage"
          type="primary"
          :disabled="op.busy.value"
          @click="edit(null)"
          >新增服务身份</el-button
        >
      </div>
      <el-table :data="filtered" stripe v-loading="listOp.busy.value">
        <el-table-column prop="name" label="名称" min-width="190"
          ><template #default="{ row }"
            ><strong>{{ row.name }}</strong>
            <div v-if="row.description" class="identity-description">
              {{ row.description }}
            </div></template
          ></el-table-column
        >
        <el-table-column label="用途" width="110"
          ><template #default="{ row }">{{
            kinds.find((kind) => kind.value === row.caller_kind)?.label ??
            row.caller_kind
          }}</template></el-table-column
        >
        <el-table-column label="状态" width="95"
          ><template #default="{ row }"
            ><el-tag :type="row.enabled ? 'success' : 'info'">{{
              row.enabled ? "已启用" : "已停用"
            }}</el-tag></template
          ></el-table-column
        >
        <el-table-column label="权限" min-width="220"
          ><template #default="{ row }"
            ><el-tooltip
              :content="
                row.permissions.map(permissionLabel).join('；') || '未分配权限'
              "
              ><span class="scope-summary"
                >{{
                  row.permissions.slice(0, 2).map(permissionLabel).join("；") ||
                  "未分配权限"
                }}{{
                  row.permissions.length > 2
                    ? ` 等${row.permissions.length}项`
                    : ""
                }}</span
              ></el-tooltip
            ></template
          ></el-table-column
        >
        <el-table-column label="创建时间" width="180"
          ><template #default="{ row }">{{
            date(row.created_at)
          }}</template></el-table-column
        >
        <el-table-column label="操作" width="240" fixed="right"
          ><template #default="{ row }"
            ><el-button
              link
              type="primary"
              :disabled="tokensOp.busy.value"
              @click="openTokens(row)"
              >令牌</el-button
            ><el-button
              link
              type="primary"
              @click="
                certificateIdentity = row;
                certificateDrawer = true;
              "
              >证书</el-button
            ><el-button
              v-if="manageable(row)"
              link
              type="primary"
              :disabled="op.busy.value"
              @click="edit(row)"
              >编辑</el-button
            ><el-button
              v-if="manageable(row)"
              link
              :type="row.enabled ? 'danger' : 'primary'"
              :disabled="op.busy.value"
              @click="setEnabled(row)"
              >{{ row.enabled ? "停用" : "启用" }}</el-button
            ></template
          ></el-table-column
        >
      </el-table>
    </template>

    <ClientCertificateWorkspace
      v-if="certificateIdentity"
      :key="`${session.value?.workspace.id}-${certificateIdentity.id}`"
      v-model="certificateDrawer"
      :identity="certificateIdentity"
      :can-manage="manageable(certificateIdentity)"
    />

    <el-dialog
      v-model="dialog"
      :title="selected ? '编辑服务身份' : '新增服务身份'"
      width="960px"
      destroy-on-close
    >
      <el-form label-position="top" @submit.prevent="save">
        <div class="identity-form-row">
          <el-form-item label="名称" :error="op.fields.value.name" required
            ><el-input
              v-model="form.name"
              maxlength="120"
              required /></el-form-item
          ><el-form-item label="用途"
            ><el-select v-model="form.caller_kind" :disabled="!!selected"
              ><el-option
                v-for="kind in kinds"
                :key="kind.value"
                :label="kind.label"
                :value="kind.value" /></el-select
          ></el-form-item>
        </div>
        <el-form-item label="说明" :error="op.fields.value.description"
          ><el-input
            v-model="form.description"
            type="textarea"
            :rows="2"
            maxlength="2000"
        /></el-form-item>
        <el-form-item label="权限" :error="op.fields.value.permissions" required
          ><PermissionSelector
            v-model="form.permissions"
            :grants="[actorPermissions]"
            :disabled="op.busy.value"
        /></el-form-item>
        <div class="dialog-actions">
          <el-button @click="dialog = false">取消</el-button
          ><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            :disabled="!form.name.trim() || !form.permissions.length"
            >保存</el-button
          >
        </div>
      </el-form>
    </el-dialog>

    <el-drawer
      v-model="tokenDrawer"
      :title="`${tokenIdentity?.name ?? ''} · 令牌`"
      size="960px"
      destroy-on-close
    >
      <div class="service-toolbar">
        <span class="spacer" /><el-button
          :loading="tokensOp.busy.value"
          @click="loadTokens"
          >刷新</el-button
        ><el-button
          v-if="tokenIdentity && manageable(tokenIdentity)"
          type="primary"
          :disabled="!tokenIdentity.enabled || op.busy.value"
          @click="startIssue"
          >签发令牌</el-button
        >
      </div>
      <el-table :data="tokens" stripe v-loading="tokensOp.busy.value"
        ><el-table-column
          prop="name"
          label="名称"
          min-width="150"
        /><el-table-column label="状态" width="95"
          ><template #default="{ row }"
            ><el-tag :type="tokenStatus(row).type">{{
              tokenStatus(row).label
            }}</el-tag></template
          ></el-table-column
        ><el-table-column label="失效时间" width="180"
          ><template #default="{ row }">{{
            date(row.expires_at)
          }}</template></el-table-column
        ><el-table-column label="权限" min-width="200"
          ><template #default="{ row }"
            ><el-tooltip :content="row.scopes.map(permissionLabel).join('；')"
              ><span class="scope-summary"
                >{{ row.scopes.slice(0, 2).map(permissionLabel).join("；")
                }}{{
                  row.scopes.length > 2 ? ` 等${row.scopes.length}项` : ""
                }}</span
              ></el-tooltip
            ></template
          ></el-table-column
        ><el-table-column label="创建时间" width="180"
          ><template #default="{ row }">{{
            date(row.created_at)
          }}</template></el-table-column
        ><el-table-column
          v-if="tokenIdentity && manageable(tokenIdentity)"
          label="操作"
          width="80"
          ><template #default="{ row }"
            ><el-button
              v-if="tokenStatus(row).label === '生效中'"
              link
              type="danger"
              :disabled="op.busy.value"
              @click="revoke(row)"
              >撤销</el-button
            ></template
          ></el-table-column
        ></el-table
      >
    </el-drawer>

    <el-dialog
      v-model="issueDialog"
      title="签发令牌"
      width="960px"
      destroy-on-close
    >
      <el-form label-position="top" @submit.prevent="issue"
        ><div class="identity-form-row">
          <el-form-item label="名称" :error="op.fields.value.name" required
            ><el-input
              v-model="tokenForm.name"
              required
              maxlength="120" /></el-form-item
          ><el-form-item
            label="有效期（分钟）"
            :error="op.fields.value.ttl_minutes"
            ><el-input-number
              v-model="tokenForm.ttl_minutes"
              :min="1"
              :max="60"
              :precision="0"
          /></el-form-item>
        </div>
        <el-form-item label="权限" :error="op.fields.value.scopes" required
          ><PermissionSelector
            v-model="tokenForm.scopes"
            :grants="issueGrants"
            :disabled="op.busy.value"
        /></el-form-item>
        <div class="dialog-actions">
          <el-button @click="issueDialog = false">取消</el-button
          ><el-button
            type="primary"
            native-type="submit"
            :loading="op.busy.value"
            :disabled="
              !tokenForm.name.trim() ||
              !tokenForm.scopes.length ||
              !tokenIdentity?.enabled
            "
            >签发</el-button
          >
        </div></el-form
      >
    </el-dialog>

    <el-dialog
      v-model="issuedDialog"
      title="令牌已签发"
      width="640px"
      :close-on-click-modal="false"
      destroy-on-close
    >
      <div v-if="issuedMetadata" class="issued-metadata">
        <strong>{{ issuedMetadata.name }}</strong
        ><span>失效时间 {{ date(issuedMetadata.expires_at) }}</span>
      </div>
      <el-input
        :model-value="issuedToken"
        type="textarea"
        :rows="3"
        readonly
        aria-label="新签发令牌"
        autocomplete="off"
        spellcheck="false"
      />
      <div class="dialog-actions">
        <el-button @click="clearIssued">完成</el-button
        ><el-button
          type="primary"
          :loading="copyOp.busy.value"
          :disabled="!issuedToken"
          @click="copy"
          >复制令牌</el-button
        >
      </div>
    </el-dialog>
  </div>
</template>

<style scoped>
.service-identities {
  min-width: 0;
  font-size: 14px;
}
.service-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 16px;
}
.service-search {
  max-width: 300px;
}
.spacer {
  flex: 1;
}
.identity-description {
  color: var(--text-muted);
  font-weight: normal;
  margin-top: 5px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
}
.scope-summary {
  display: block;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.identity-form-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 230px;
  gap: 20px;
}
.identity-form-row :deep(.el-select) {
  width: 100%;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 20px;
}
.dialog-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}
.issued-metadata {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 14px;
}
.issued-metadata span {
  color: var(--text-muted);
}
@media (max-width: 760px) {
  .identity-form-row {
    grid-template-columns: 1fr;
  }
  .service-toolbar {
    flex-wrap: wrap;
  }
  .issued-metadata {
    flex-direction: column;
    gap: 8px;
  }
}
</style>
