<script setup lang="ts">
/**
 * 写入目标表单（v-model 绑定 WriteTarget）：选择目标数据源、普通表、允许写入的字段与允许的写入方式。
 * 路由：无独立路由，由 WriteTargetsManager（/datasources 页签）以弹窗形式渲染。
 * 依赖接口：无请求；数据源（仅 sqlite/mysql/postgres）与表结构由父组件传入（表结构来自 tables/info）。
 * 关键交互：切换数据源会重置表名与字段并同步 sourceVersion；切换表会清空已勾选字段（表变了字段必然失效）；
 *   当数据源当前版本与已确认版本不一致时给出"确认当前连接版本"按钮（必须由人确认，不自动跟随）；
 *   目标已存在（model.id）时禁止修改数据源（换源等于换目标，应新建）。
 * 约束：只对勾选字段开放写入；写库权限仍由数据库账号决定（提示文案已说明）。
 */
import { computed } from 'vue'
import type { WriteTarget } from '../../api/writeback'
const model = defineModel<WriteTarget>({ required: true })
const props = defineProps<{ sources: any[]; tables: any[]; disabled: boolean }>()
const source = computed(() => props.sources.find(item => item.id === model.value.datasourceId))
const table = computed(() => props.tables.find(item => item.tableName === model.value.tableName))
function patch(values: Partial<WriteTarget>) { model.value = { ...model.value, ...values } }
/** 切换数据源：重置表名与字段，并把 sourceVersion 对齐到该数据源当前版本（版本必须与已确认连接一致）。 */
function chooseSource(id: string) { const selected = props.sources.find(item => item.id === id); patch({ datasourceId: id, sourceVersion: selected?.version || 1, tableName: '', columns: [] }) }
/** 多选字段/方式的勾选切换：统一通过 patch 整对象赋值，保证 v-model 变更被父组件感知。 */
function check(key: 'columns' | 'modes', value: string, on: boolean) { patch({ [key]: on ? [...model.value[key], value] : model.value[key].filter(item => item !== value) }) }
</script>
<template>
  <fieldset :disabled="disabled" class="write-target-form">
    <label>目标名称<input :value="model.name" required maxlength="160" @input="patch({ name: ($event.target as HTMLInputElement).value })" /></label>
    <label>目标数据源<select :value="model.datasourceId" :disabled="!!model.id" required @change="chooseSource(($event.target as HTMLSelectElement).value)"><option value="">请选择数据库</option><option v-for="item in sources" :key="item.id" :value="item.id">{{ item.name }} · {{ item.type }}</option></select></label>
    <p v-if="source">已确认连接 v{{ model.sourceVersion }} · 当前连接 v{{ source.version }} <button v-if="source.version !== model.sourceVersion" type="button" @click="patch({ sourceVersion: source.version })">确认当前连接版本</button></p>
    <label>目标业务表<select :value="model.tableName" required @change="patch({ tableName: ($event.target as HTMLSelectElement).value, columns: [] })"><option value="">请选择已存在的普通表</option><option v-for="item in tables" :key="item.tableName" :value="item.tableName">{{ item.tableName }} {{ item.tableComment }}</option></select></label>
    <fieldset><legend>允许写入的字段（必须勾选）</legend><div class="write-field-grid"><label class="write-check" v-for="column in table?.columns || []" :key="column.columnName"><input type="checkbox" :checked="model.columns.includes(column.columnName)" @change="check('columns', column.columnName, ($event.target as HTMLInputElement).checked)" />{{ column.columnName }} · {{ column.dataType }}</label></div><p v-if="!table">选择表后显示真实字段。</p></fieldset>
    <fieldset><legend>明确允许的操作</legend><label class="write-check"><input type="checkbox" :checked="model.modes.includes('insert')" @change="check('modes', 'insert', ($event.target as HTMLInputElement).checked)" />追加记录</label><label class="write-check"><input type="checkbox" :checked="model.modes.includes('update')" @change="check('modes', 'update', ($event.target as HTMLInputElement).checked)" />按完整唯一键更新</label></fieldset>
    <label>授权状态<select :value="model.status" @change="patch({ status: ($event.target as HTMLSelectElement).value })"><option value="active">启用</option><option value="disabled">停用</option></select></label>
    <p class="wf-note">仅对勾选的表字段开放写入。数据库账号仍须具备相应权限；更改目标后，工作流必须重新确认目标版本。SQLite 精确小数请使用 TEXT 字段或缩放后的整数。</p>
  </fieldset>
</template>
