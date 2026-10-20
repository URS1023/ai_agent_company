/** 仅配置目录资源的字段；执行、质量、图谱、报表使用各自领域页面。 */
export interface ResourceField {
  key: string;
  label: string;
  type?:
    | "text"
    | "textarea"
    | "password"
    | "boolean"
    | "number"
    | "select"
    | "multi"
    | "json";
  required?: boolean;
  options?: { value: string; label: string }[];
  readonlyOnEdit?: boolean;
}
export interface ResourceConfig {
  title: string;
  path: string;
  fields: ResourceField[];
  columns: { key: string; label: string }[];
  readonly?: boolean;
  deletable?: boolean;
  createOnly?: boolean;
}
const name = {
  key: "name",
  label: "名称",
  required: true,
} satisfies ResourceField;
const description = {
  key: "description",
  label: "描述",
  type: "textarea",
} satisfies ResourceField;
const status = {
  key: "enabled",
  label: "启用",
  type: "boolean",
} satisfies ResourceField;
export const resources: Record<string, ResourceConfig> = {
  notifications: {
    title: "消息通知",
    path: "/notifications",
    fields: [],
    columns: [
      { key: "title", label: "消息" },
      { key: "message", label: "内容" },
      { key: "created_at", label: "时间" },
    ],
    readonly: true,
  },
  "business-metrics": {
    title: "指标定义",
    path: "/business-metrics",
    fields: [
      name,
      { key: "datasource_id", label: "数据源ID" },
      { key: "sql", label: "取数语句", type: "textarea" },
      { key: "definition", label: "指标定义", type: "json" },
    ],
    columns: [
      { key: "name", label: "指标名称" },
      { key: "datasource_id", label: "数据源" },
      { key: "sql", label: "取数定义" },
    ],
    deletable: true,
  },
  "resource-grants": {
    title: "数据权限",
    path: "/resource-grants",
    fields: [
      { key: "resource_type", label: "资源类型", required: true },
      { key: "resource_id", label: "资源ID", required: true },
      { key: "user_id", label: "用户ID", required: true },
      {
        key: "actions",
        label: "授权操作",
        type: "multi",
        options: [
          { value: "read", label: "查看" },
          { value: "write", label: "修改" },
          { value: "delete", label: "删除" },
          { value: "publish", label: "发布" },
        ],
      },
    ],
    columns: [
      { key: "resource_type", label: "资源类型" },
      { key: "resource_id", label: "资源" },
      { key: "user_id", label: "用户" },
      { key: "actions", label: "操作" },
    ],
    deletable: true,
    createOnly: true,
  },
  ontologies: {
    title: "本体管理",
    path: "/ontologies",
    fields: [
      name,
      description,
      { key: "schema", label: "本体结构", type: "json" },
    ],
    columns: [
      { key: "name", label: "本体名称" },
      { key: "description", label: "描述" },
      { key: "updated_at", label: "更新时间" },
    ],
    deletable: true,
  },
  "extraction-jobs": {
    title: "抽取任务",
    path: "/extraction-jobs",
    fields: [
      name,
      { key: "ontology_id", label: "本体ID", required: true },
      { key: "file_ids", label: "源文件ID", type: "json" },
    ],
    columns: [
      { key: "name", label: "任务名称" },
      { key: "status", label: "状态" },
      { key: "created_at", label: "时间" },
    ],
  },
  triggers: {
    title: "触发器",
    path: "/triggers",
    fields: [
      name,
      { key: "automation_id", label: "自动化ID", required: true },
      {
        key: "kind",
        label: "触发方式",
        type: "select",
        options: [
          { value: "manual", label: "手动" },
          { value: "webhook", label: "Webhook" },
          { value: "schedule", label: "定时" },
        ],
      },
      { key: "expression", label: "调度表达式" },
      status,
    ],
    columns: [
      { key: "name", label: "名称" },
      { key: "kind", label: "触发方式" },
      { key: "enabled", label: "启用" },
    ],
    deletable: true,
  },
  "tool-bindings": {
    title: "工具",
    path: "/tool-bindings",
    fields: [
      name,
      {
        key: "protocol",
        label: "协议",
        type: "select",
        options: [
          { value: "openapi", label: "OpenAPI" },
          { value: "mcp", label: "MCP" },
        ],
        required: true,
        readonlyOnEdit: true,
      },
      {
        key: "integration_id",
        label: "集成连接ID",
        required: true,
        readonlyOnEdit: true,
      },
      {
        key: "endpoint",
        label: "服务地址",
        required: true,
        readonlyOnEdit: true,
      },
      { key: "credential_ref", label: "凭据引用" },
      { key: "schema_document", label: "接口定义", type: "json" },
      status,
    ],
    columns: [
      { key: "name", label: "工具名称" },
      { key: "protocol", label: "协议" },
      { key: "endpoint", label: "地址" },
    ],
    deletable: true,
  },
  "skill-bindings": {
    title: "Skills",
    path: "/skill-bindings",
    fields: [
      name,
      { key: "content", label: "技能内容", type: "textarea", required: true },
      description,
      status,
    ],
    columns: [
      { key: "name", label: "技能名称" },
      { key: "description", label: "描述" },
      { key: "updated_at", label: "更新时间" },
    ],
    deletable: true,
  },
  "industry-packages": {
    title: "行业方案",
    path: "/industry-packages",
    fields: [
      name,
      { key: "industry", label: "行业" },
      { key: "application_ids", label: "应用ID", type: "json" },
      description,
    ],
    columns: [
      { key: "name", label: "方案名称" },
      { key: "industry", label: "适用行业" },
      { key: "description", label: "描述" },
    ],
    deletable: true,
  },
  members: {
    title: "成员管理",
    path: "/members",
    fields: [
      { key: "username", label: "账号", required: true, readonlyOnEdit: true },
      {
        key: "display_name",
        label: "姓名",
        required: true,
        readonlyOnEdit: true,
      },
      {
        key: "password",
        label: "初始密码",
        type: "password",
        readonlyOnEdit: true,
      },
      { key: "role_ids", label: "角色", type: "multi" },
      { key: "department_id", label: "部门ID" },
      { key: "disabled", label: "停用账号", type: "boolean" },
    ],
    columns: [
      { key: "username", label: "账号" },
      { key: "display_name", label: "姓名" },
      { key: "role_ids", label: "角色" },
      { key: "disabled", label: "停用" },
    ],
  },
  roles: {
    title: "角色权限",
    path: "/roles",
    fields: [
      name,
      {
        key: "permissions",
        label: "权限",
        type: "multi",
        options: Array.from({ length: 17 }, (_, index) => ({
          value: `m${String(index + 1).padStart(2, "0")}.*`,
          label: [
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
          ][index]!,
        })),
      },
    ],
    columns: [
      { key: "name", label: "角色名称" },
      { key: "permissions", label: "权限" },
    ],
  },
  workspaces: {
    title: "工作区",
    path: "/workspaces",
    fields: [
      name,
      { key: "factory_id", label: "工厂编码" },
      { key: "industry", label: "行业" },
    ],
    columns: [
      { key: "name", label: "工作区名称" },
      { key: "factory_id", label: "工厂编码" },
      { key: "industry", label: "行业" },
    ],
  },
  "audit-logs": {
    title: "操作审计",
    path: "/audit-events",
    fields: [],
    columns: [
      { key: "action", label: "操作" },
      { key: "resource_type", label: "资源类型" },
      { key: "resource_id", label: "资源ID" },
      { key: "user_id", label: "操作人" },
      { key: "created_at", label: "时间" },
    ],
    readonly: true,
  },
  usage: {
    title: "使用统计",
    path: "/model-usage",
    fields: [],
    columns: [
      { key: "name", label: "项目" },
      { key: "count", label: "调用次数" },
      { key: "tokens", label: "Token数" },
      { key: "cost", label: "费用" },
    ],
    readonly: true,
  },
  runs: {
    title: "运行记录",
    path: "/operations/runs",
    fields: [],
    columns: [
      { key: "title", label: "任务" },
      { key: "engine", label: "引擎" },
      { key: "status", label: "状态" },
      { key: "created_at", label: "开始时间" },
    ],
    readonly: true,
  },
  releases: {
    title: "发布版本",
    path: "/releases",
    fields: [],
    columns: [
      { key: "name", label: "名称" },
      { key: "resource_type", label: "资源类型" },
      { key: "version", label: "版本" },
      { key: "status", label: "状态" },
      { key: "created_at", label: "时间" },
    ],
    readonly: true,
  },
  backups: {
    title: "备份管理",
    path: "/backups",
    fields: [name, description],
    columns: [
      { key: "name", label: "备份" },
      { key: "status", label: "状态" },
      { key: "created_at", label: "时间" },
    ],
    readonly: true,
  },
};
