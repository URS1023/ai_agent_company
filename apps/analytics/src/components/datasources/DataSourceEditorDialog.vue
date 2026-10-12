<script setup lang="ts">
/**
 * 数据源新增/编辑弹窗（数据源工作区 → 数据库 / 文件）。
 * 路由：无独立路由，由 DataSourceManage 在 /datasources 页签内以 v-model:open 控制显隐。
 * 依赖接口：api/request.ts 的 post/put（/api/v1/analytics/datasources、.../datasources/test）。
 * 关键交互：先"只读连接测试"再保存（测试成功提示"只读连接测试成功"）；保存成功后 emit('saved') 由父组件刷新列表；
 *   编辑时不回显密码（password 置空表示"不修改"），仅当填写了新密码才提交。
 * 约束：allowedTables 为逐行表名、allowedColumns 为"表名 → 字段数组"的 JSON（两处都做结构校验，非法直接报错）；
 *   sqlite 类型强制清空 host/port/username。密码属于敏感信息，禁止写入日志或提示文案。
 */
import { reactive, shallowRef, watch } from 'vue'
import { ElMessage, type FormInstance, type FormRules } from 'element-plus'
import { can, post, put } from '../../api/request'
import type { DataSourceSummary } from './types'

type SourceForm = {
  id: string | null
  name: string
  type: DataSourceSummary['type']
  host: string
  port: number | null
  databaseName: string
  username: string
  password: string
  description: string
  enabled: boolean
  version: number | null
  ssl: boolean
  timeoutSeconds: number
  maxRows: number
}

const props = defineProps<{ source: DataSourceSummary | null }>()
const emit = defineEmits<{ saved: [] }>()
const open = defineModel<boolean>({ default: false })
const formRef = shallowRef<FormInstance>()
const testing = shallowRef(false)
const saving = shallowRef(false)
const allowedTablesText = shallowRef('')
const allowedColumnsText = shallowRef('{}')

const form = reactive<SourceForm>({
  id: null,
  name: '',
  type: 'mysql',
  host: 'localhost',
  port: 3306,
  databaseName: '',
  username: '',
  password: '',
  description: '',
  enabled: true,
  version: null,
  ssl: false,
  timeoutSeconds: 15,
  maxRows: 1000,
})

const rules: FormRules<SourceForm> = {
  name: [{ required: true, message: '请输入数据源名称', trigger: 'blur' }],
  type: [{ required: true, message: '请选择数据源类型', trigger: 'change' }],
  host: [{ required: true, message: '请输入主机地址', trigger: 'blur' }],
  port: [{ required: true, message: '请输入端口', trigger: 'blur' }],
  databaseName: [{ required: true, message: '请输入数据库名', trigger: 'blur' }],
  username: [{ required: true, message: '请输入用户名', trigger: 'blur' }],
}

function reset() {
  const source = props.source
  Object.assign(form, source ? {
    id: source.id,
    name: source.name,
    type: source.type,
    host: source.host,
    port: source.port,
    databaseName: source.databaseName,
    username: source.username,
    password: '',
    description: source.configuredDescription || '',
    enabled: source.enabled,
    version: source.version,
    ssl: source.ssl,
    timeoutSeconds: source.timeoutSeconds,
    maxRows: source.maxRows,
  } : {
    id: null,
    name: '',
    type: 'mysql',
    host: 'localhost',
    port: 3306,
    databaseName: '',
    username: '',
    password: '',
    description: '',
    enabled: true,
    version: null,
    ssl: false,
    timeoutSeconds: 15,
    maxRows: 1000,
  })
  allowedTablesText.value = (source?.allowedTables || []).join('\n')
  allowedColumnsText.value = JSON.stringify(source?.allowedColumns || {}, null, 2)
  formRef.value?.clearValidate()
}

watch([open, () => props.source?.id], ([visible]) => {
  if (visible) reset()
}, { immediate: true })

function changeType(type: DataSourceSummary['type']) {
  const ports: Partial<Record<DataSourceSummary['type'], number>> = {
    mysql: 3306,
    postgres: 5432,
    elasticsearch: 9200,
  }
  if (!form.port || [3306, 5432, 9200].includes(form.port)) form.port = ports[type] || null
}

/**
 * 组装提交体：sqlite 清空连接字段；密码为空表示不修改（传 null，由后端保留原密码）；
 * allowedTables 按行解析，allowedColumns 必须通过"表名 → 字符串数组"的结构校验。
 * 失败表现：抛出业务错误（由调用方展示中文提示），不发出请求。
 */
function payload() {
  const allowedColumns = JSON.parse(allowedColumnsText.value || '{}') as Record<string, unknown>
  if (!allowedColumns || Array.isArray(allowedColumns) || typeof allowedColumns !== 'object' ||
      Object.values(allowedColumns).some((value) => !Array.isArray(value) || value.some((field) => typeof field !== 'string'))) {
    throw new Error('字段范围必须是表名到字段数组的 JSON 对象')
  }
  return {
    ...form,
    host: form.type === 'sqlite' ? '' : form.host,
    port: form.type === 'sqlite' ? null : form.port,
    username: form.type === 'sqlite' ? '' : form.username,
    password: form.type === 'sqlite' || !form.password ? null : form.password,
    allowedTables: allowedTablesText.value.split(/\r?\n/).map((value) => value.trim()).filter(Boolean),
    allowedColumns,
  }
}

/** 只读连接测试：POST /api/v1/analytics/datasources/test，仅验证连通性与权限，不落库、不写任何配置。 */
async function testConnection() {
  testing.value = true
  try {
    await post('/api/v1/analytics/datasources/test', payload())
    ElMessage.success('只读连接测试成功')
  } catch (caught) {
    ElMessage.error('测试失败：' + (caught as Error).message)
  } finally {
    testing.value = false
  }
}

/** 保存：先做表单必填校验，再按有无 id 选择 POST 新建或 PUT 更新（更新需带 version 乐观锁）。 */
async function save() {
  const valid = await formRef.value?.validate().catch(() => false)
  if (!valid) return
  saving.value = true
  try {
    const body = payload()
    if (form.id) await put('/api/v1/analytics/datasources/' + form.id, body)
    else await post('/api/v1/analytics/datasources', body)
    open.value = false
    emit('saved')
    ElMessage.success(form.id ? '数据源已更新' : '数据源已创建并同步元数据')
  } catch (caught) {
    ElMessage.error('保存失败：' + (caught as Error).message)
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <el-dialog
    v-model="open"
    :title="source ? '编辑数据源' : '添加数据源'"
    width="min(640px, 96vw)"
    :close-on-click-modal="false"
  >
    <el-form ref="formRef" :model="form" :rules="rules" label-width="118px">
      <el-form-item label="数据源名称" prop="name">
        <el-input v-model="form.name" placeholder="例如：财务生产库" />
      </el-form-item>
      <el-form-item label="数据源类型" prop="type">
        <el-select v-model="form.type" class="full-control" @change="changeType">
          <el-option label="MySQL" value="mysql" />
          <el-option label="PostgreSQL" value="postgres" />
          <el-option label="Elasticsearch" value="elasticsearch" />
          <el-option label="受管 SQLite" value="sqlite" />
        </el-select>
      </el-form-item>
      <el-form-item v-if="form.type !== 'sqlite'" label="主机地址" prop="host">
        <el-input v-model="form.host" placeholder="数据库主机名或 IP，不含协议" />
      </el-form-item>
      <el-form-item v-if="form.type !== 'sqlite'" label="端口" prop="port">
        <el-input-number v-model="form.port" :min="1" :max="65535" class="full-control" />
      </el-form-item>
      <el-form-item v-if="form.type !== 'elasticsearch'" :label="form.type === 'sqlite' ? '受管文件名' : '数据库名'" prop="databaseName">
        <el-input v-model="form.databaseName" :placeholder="form.type === 'sqlite' ? '先通过数据源页导入文件' : '业务数据库名称'" />
      </el-form-item>
      <el-form-item v-else label="索引范围">
        <el-input v-model="form.databaseName" placeholder="例如 orders-*；留空表示全部非隐藏索引" />
      </el-form-item>
      <el-form-item v-if="form.type !== 'sqlite'" label="用户名" :prop="['mysql', 'postgres'].includes(form.type) ? 'username' : undefined">
        <el-input v-model="form.username" placeholder="建议使用只读账号" />
      </el-form-item>
      <el-form-item v-if="form.type !== 'sqlite'" label="密码">
        <el-input v-model="form.password" type="password" autocomplete="new-password" show-password :placeholder="source ? '留空保留已加密凭据' : '无密码认证可留空'" />
      </el-form-item>
      <el-form-item v-if="form.type === 'elasticsearch'" label="HTTPS">
        <el-switch v-model="form.ssl" />
      </el-form-item>
      <el-collapse class="scope-collapse">
        <el-collapse-item title="访问范围与查询限制" name="scope">
          <el-form-item label="允许访问的表">
            <el-input v-model="allowedTablesText" type="textarea" :rows="2" placeholder="每行一个表名；留空表示全部非系统表" />
          </el-form-item>
          <el-form-item label="允许访问的字段">
            <el-input v-model="allowedColumnsText" type="textarea" :rows="3" placeholder='JSON，例如 {"设备":["编号","温度"]}' />
          </el-form-item>
          <el-form-item label="查询超时">
            <el-input-number v-model="form.timeoutSeconds" :min="1" :max="60" />
            <span class="unit">秒</span>
          </el-form-item>
          <el-form-item label="单次返回上限">
            <el-input-number v-model="form.maxRows" :min="1" :max="10000" />
            <span class="unit">行</span>
          </el-form-item>
        </el-collapse-item>
      </el-collapse>
      <el-form-item label="业务用途说明">
        <div class="description-control">
          <el-input v-model="form.description" type="textarea" :rows="3" placeholder="留空时根据表注释和字段注释自动生成" />
          <small>保存后自动同步元数据、关系和语义索引；管理员说明优先于自动说明。</small>
        </div>
      </el-form-item>
    </el-form>
    <template #footer>
      <el-button @click="open = false">取消</el-button>
      <el-button v-if="can('datasources.execute')" :loading="testing" @click="testConnection">测试连接</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<style scoped>
.full-control { width: 100%; }
.scope-collapse { margin: 4px 0 18px; border: 1px solid #e1e7ee; border-radius: 5px; padding: 0 12px; }
.description-control { width: 100%; }
.description-control small { display: block; margin-top: 6px; color: #6c7d90; line-height: 1.5; }
.unit { margin-left: 8px; color: #718296; font-size: 12px; }
</style>
