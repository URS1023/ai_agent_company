<script setup lang="ts">
/** 平台插件页：登记本平台可用的工具连接（OpenAPI / MCP），不跳转外部系统。 */
import { onMounted, ref } from "vue";
import { Delete, Plus } from "@element-plus/icons-vue";
import { api } from "../../api/client";
import { useOperation } from "../../composables/useOperation";

interface ToolBinding {
  id: string;
  name: string;
  protocol: string;
  endpoint: string;
  integration_id: string;
  enabled: boolean;
  revision: number;
}
interface Connection {
  id: string;
  name: string;
  engine: string;
}

const op = useOperation();
const tools = ref<ToolBinding[]>([]);
const connections = ref<Connection[]>([]);
const dialog = ref(false);
const form = ref({ name: "", protocol: "openapi", integration_id: "", endpoint: "" });

async function loadAll() {
  const [toolData, connData] = await Promise.all([
    api.get<{ items: ToolBinding[] }>("/tool-bindings"),
    api.get<{ items: Connection[] }>("/integration-instances"),
  ]);
  tools.value = toolData.items;
  connections.value = connData.items;
}

async function save() {
  const payload = { ...form.value, name: form.value.name.trim() };
  if (!payload.name || !payload.endpoint.trim() || !payload.integration_id) return;
  await op.run(async () => {
    await api.post("/tool-bindings", payload);
    dialog.value = false;
    form.value = { name: "", protocol: "openapi", integration_id: "", endpoint: "" };
    await loadAll();
  }, "工具已登记");
}

async function remove(row: ToolBinding) {
  await op.run(async () => {
    await api.delete(`/tool-bindings/${row.id}`, row.revision);
    await loadAll();
  }, "工具已删除");
}

function connectionName(id: string) {
  return connections.value.find((c) => c.id === id)?.name ?? id;
}

onMounted(async () => {
  await op.run(loadAll);
});
</script>

<template>
  <div class="page-stack">
    <div class="panel kb-toolbar">
      <div class="kb-title">
        <h2>插件与工具</h2>
        <p class="sub">把本平台可调用的 OpenAPI / MCP 工具登记进来，供智能体与工作流使用</p>
      </div>
      <div class="row-actions">
        <el-button type="primary" :icon="Plus" @click="dialog = true">登记工具</el-button>
      </div>
    </div>

    <div class="panel">
      <div class="section-title">
        已登记工具
        <span class="tail">{{ tools.length }} 个</span>
      </div>
      <el-table v-if="tools.length" :data="tools" style="width: 100%">
        <el-table-column prop="name" label="名称" min-width="180" show-overflow-tooltip />
        <el-table-column label="协议" width="110">
          <template #default="{ row }">
            <span class="badge tone-cyan">{{ row.protocol.toUpperCase() }}</span>
          </template>
        </el-table-column>
        <el-table-column label="连接" width="180" show-overflow-tooltip>
          <template #default="{ row }">{{ connectionName(row.integration_id) }}</template>
        </el-table-column>
        <el-table-column prop="endpoint" label="地址" min-width="240" show-overflow-tooltip />
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="status-chip" :class="row.enabled ? 'passed' : 'draft'">
              {{ row.enabled ? "启用" : "停用" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="80" align="right">
          <template #default="{ row }">
            <el-button :icon="Delete" text @click="remove(row)" />
          </template>
        </el-table-column>
      </el-table>
      <div v-else class="empty-state">
        <div class="glyph"><i class="fa-solid fa-plug" /></div>
        <div class="title">还没有登记工具</div>
        <div class="hint">登记后可被智能体应用与工作流调用。</div>
      </div>
    </div>

    <el-dialog v-model="dialog" title="登记工具" width="560px">
      <el-form label-position="top">
        <el-form-item label="名称">
          <el-input v-model="form.name" maxlength="100" placeholder="例如：设备台账查询" />
        </el-form-item>
        <el-form-item label="协议">
          <el-select v-model="form.protocol" style="width: 100%">
            <el-option label="OpenAPI" value="openapi" />
            <el-option label="MCP" value="mcp" />
          </el-select>
        </el-form-item>
        <el-form-item label="所属连接">
          <el-select v-model="form.integration_id" style="width: 100%" placeholder="选择已登记的服务连接">
            <el-option v-for="c in connections" :key="c.id" :label="c.name" :value="c.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="接口地址">
          <el-input v-model="form.endpoint" maxlength="1000" placeholder="例如：http://127.0.0.1:8080/api/v1/devices" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialog = false">取消</el-button>
        <el-button type="primary" :loading="op.busy.value" @click="save">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>
