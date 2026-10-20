<script setup lang="ts">
/** 复用角色页权限矩阵；选择范围是当前管理者与可选身份授权的交集。 */
import { computed } from "vue";
import {
  permissionActions,
  permissionModules,
  scopeAllows,
  scopeIntersectionAllows,
  toggleScope,
} from "./permissionScopes";

const scopes = defineModel<string[]>({ required: true });
const props = withDefaults(
  defineProps<{ grants: string[][]; disabled?: boolean; max?: number }>(),
  { disabled: false, max: 100 },
);
const all = computed(() => scopes.value.includes("*"));
const mayGrantAll = computed(() => scopeIntersectionAllows(props.grants, "*"));
const readSelection = computed(() => [
  ...new Set([
    ...scopes.value,
    ...permissionModules
      .map((module) => `${module.id}.read`)
      .filter(
        (permission) =>
          allowed(permission) && !scopeAllows(scopes.value, permission),
      ),
  ]),
]);
function allowed(permission: string) {
  return scopeIntersectionAllows(props.grants, permission);
}
function toggle(permission: string, checked: boolean) {
  if (!allowed(permission) || props.disabled || all.value) return;
  const next = toggleScope(scopes.value, permission, checked);
  if (next.length <= props.max) scopes.value = next;
}
function readAll() {
  if (readSelection.value.length <= props.max)
    scopes.value = readSelection.value;
}
function disabled(permission: string) {
  if (props.disabled || all.value || !allowed(permission)) return true;
  return (
    toggleScope(
      scopes.value,
      permission,
      !scopeAllows(scopes.value, permission),
    ).length > props.max
  );
}
</script>
<template>
  <div class="permission-selector">
    <div class="scope-toolbar">
      <el-checkbox
        v-if="mayGrantAll || all"
        :model-value="all"
        :disabled="props.disabled || !mayGrantAll"
        @change="scopes = $event ? ['*'] : []"
        >全部权限</el-checkbox
      >
      <span class="scope-spacer" />
      <el-button
        :disabled="props.disabled || all || readSelection.length > props.max"
        @click="readAll"
        >选择全部查看权限</el-button
      >
      <el-button
        :disabled="props.disabled || !scopes.length"
        @click="scopes = []"
        >清空</el-button
      >
    </div>
    <el-table :data="permissionModules" border max-height="360">
      <el-table-column prop="name" label="模块" min-width="110" />
      <el-table-column
        v-for="action in permissionActions"
        :key="action.key"
        :label="action.label"
        align="center"
        min-width="90"
      >
        <template #default="{ row }"
          ><el-checkbox
            :model-value="scopeAllows(scopes, `${row.id}.${action.key}`)"
            :disabled="disabled(`${row.id}.${action.key}`)"
            :aria-label="`${row.name}·${action.label}`"
            @change="toggle(`${row.id}.${action.key}`, Boolean($event))"
        /></template>
      </el-table-column>
    </el-table>
  </div>
</template>
<style scoped>
.permission-selector {
  width: 100%;
}
.scope-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.scope-spacer {
  flex: 1;
}
.permission-selector :deep(.el-table) {
  font-size: 14px;
}
</style>
