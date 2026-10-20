/** 精提角色页的模块/动作定义，服务身份与令牌使用相同权限名称。 */
export const permissionActions = [
  { key: "read", label: "查看" },
  { key: "write", label: "新增/修改" },
  { key: "delete", label: "删除" },
  { key: "publish", label: "发布" },
  { key: "admin", label: "管理" },
  { key: "audit", label: "审计" },
];
export const permissionModules = [
  "工作台",
  "AI对话",
  "智能体",
  "工作流",
  "数据接入",
  "数据分析",
  "报告",
  "知识库",
  "图谱",
  "设备维护",
  "质量",
  "工具与技能",
  "应用",
  "组织",
  "模型",
  "运维",
  "成果",
].map((name, index) => ({
  id: `m${String(index + 1).padStart(2, "0")}`,
  name,
}));

export function permissionLabel(permission: string) {
  if (permission === "*") return "全部权限";
  const [module, action] = permission.split(".");
  return `${permissionModules.find((item) => item.id === module)?.name ?? module} · ${action === "*" ? "全部" : (permissionActions.find((item) => item.key === action)?.label ?? action)}`;
}

/** 通配授予必须持有相同通配；具体权限才可由模块通配覆盖。 */
export function scopeAllows(grants: readonly string[], permission: string) {
  if (grants.includes("*")) return true;
  if (permission === "*") return false;
  if (grants.includes(permission)) return true;
  return (
    !permission.endsWith(".*") &&
    grants.includes(`${permission.split(".")[0]}.*`)
  );
}

export function scopeIntersectionAllows(
  sets: readonly (readonly string[])[],
  permission: string,
) {
  return (
    sets.length > 0 && sets.every((grants) => scopeAllows(grants, permission))
  );
}

/** 修改模块通配中的单项时明确收窄为具体动作，其他模块和已有范围保持原样。 */
export function toggleScope(
  scopes: readonly string[],
  permission: string,
  checked: boolean,
) {
  const module = permission.split(".")[0];
  const expanded = scopes.flatMap((scope) =>
    scope === `${module}.*`
      ? permissionActions.map((action) => `${module}.${action.key}`)
      : [scope],
  );
  return checked
    ? [...new Set([...expanded, permission])]
    : expanded.filter((scope) => scope !== permission);
}
