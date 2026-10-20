/** 模块入口与权限集中声明；新增模块只需注册路由、菜单及权限。 */
export interface MenuItem {
  key: string;
  label: string;
  path: string;
  permission?: string;
  view: string;
  config?: string;
}
export interface MenuSection {
  label: string;
  items: MenuItem[];
}
export interface ModuleGroup {
  id: string;
  label: string;
  sections: MenuSection[];
}
function item(
  key: string,
  label: string,
  view: string,
  permission: string,
  config?: string,
): MenuItem {
  return { key, label, path: `/${key}`, view, permission, config };
}
export const modules: ModuleGroup[] = [
  {
    id: "home",
    label: "工作台",
    sections: [
      {
        label: "我的工作",
        items: [
          item("overview", "工作概览", "home", "m01.read"),
          item("tasks", "任务中心", "tasks", "m01.read"),
          item("notices", "消息通知", "catalog", "m01.read", "notifications"),
        ],
      },
    ],
  },
  {
    id: "ai",
    label: "AI工作台",
    sections: [
      {
        label: "智能助手",
        items: [
          item("chat", "AI对话", "native", "m02.read", "openwebui"),
          item("history", "会话记录", "native", "m02.read", "openwebui"),
          item("memory", "记忆管理", "native", "m02.read", "openwebui"),
        ],
      },
    ],
  },
  {
    id: "business",
    label: "业务应用",
    sections: [
      {
        label: "设备与维护",
        items: [
          item("devices", "设备台账", "assets", "m10.read", "devices"),
          item("maintenance", "预测性维护", "maintenance", "m10.read"),
          item("alerts", "预警与处置", "incidents", "m10.read"),
        ],
      },
      {
        label: "质量检测",
        items: [
          item("inspections", "检测任务", "inspections", "m11.read"),
          item("device-check", "设备质检", "inspections", "m11.read", "device"),
          item(
            "product-check",
            "产品质检",
            "inspections",
            "m11.read",
            "product",
          ),
          item("standards", "检测标准", "standards", "m11.read"),
          item("products", "产品与批次", "products", "m11.read", "products"),
        ],
      },
    ],
  },
  {
    id: "data",
    label: "数据分析",
    sections: [
      {
        label: "数据接入",
        items: [
          item("sources", "数据源", "data", "m05.read", "sources"),
          item(
            "metrics",
            "指标定义",
            "catalog",
            "m05.read",
            "business-metrics",
          ),
          item(
            "access",
            "数据权限",
            "data-access",
            "m05.read",
            "resource-grants",
          ),
        ],
      },
      {
        label: "数据处理",
        items: [
          item("analysis", "数据查询", "data", "m06.read", "query"),
          item(
            "jobs",
            "处理任务",
            "processing",
            "m06.read",
            "data-processing-jobs",
          ),
          item("quality", "数据质量", "quality", "m06.read", "quality"),
        ],
      },
      {
        label: "报告与看板",
        items: [
          item("reports", "报表设计", "reports", "m07.read"),
          item("templates", "组件与模板", "reports", "m07.read", "templates"),
        ],
      },
    ],
  },
  {
    id: "knowledge",
    label: "知识管理",
    sections: [
      {
        label: "企业知识",
        items: [
          item("bases", "知识库", "native", "m08.read", "dify-knowledge"),
        ],
      },
      {
        label: "知识图谱",
        items: [
          item("ontology", "本体管理", "ontology", "m09.read", "ontologies"),
          item(
            "extraction",
            "抽取任务",
            "extraction",
            "m09.read",
            "extraction-jobs",
          ),
          item("relations", "图谱探索", "graph", "m09.read"),
        ],
      },
    ],
  },
  {
    id: "dev",
    label: "开发工作台",
    sections: [
      {
        label: "智能体",
        items: [item("agents", "Agents", "native", "m03.read", "dify-agents")],
      },
      {
        label: "流程编排",
        items: [
          item("workflows", "工作流", "native", "m04.read", "dify-workflows"),
          item("triggers", "触发器", "native", "m04.read", "dify-triggers"),
        ],
      },
      {
        label: "能力扩展",
        items: [
          item("tools", "工具", "catalog", "m12.read", "tool-bindings"),
          item("skills", "Skills", "catalog", "m12.read", "skill-bindings"),
          item("plugins", "插件", "native", "m12.read", "dify-plugins"),
        ],
      },
    ],
  },
  {
    id: "apps",
    label: "应用中心",
    sections: [
      {
        label: "应用与方案",
        items: [
          item("applications", "应用中心", "applications", "m13.read"),
          item(
            "packages",
            "行业方案",
            "industry",
            "m13.read",
            "industry-packages",
          ),
        ],
      },
    ],
  },
  {
    id: "results",
    label: "成果中心",
    sections: [
      {
        label: "文件与成果",
        items: [
          item("files", "文件管理", "files", "m17.read"),
          item("generate", "成果生成", "documents", "m17.read"),
        ],
      },
    ],
  },
  {
    id: "admin",
    label: "系统管理",
    sections: [
      {
        label: "组织与权限",
        items: [
          item("users", "成员管理", "catalog", "m14.read", "members"),
          item("roles", "角色权限", "roles", "m14.read", "roles"),
          item("workspaces", "工作区", "catalog", "m14.read", "workspaces"),
          item("audit", "操作审计", "catalog", "m14.read", "audit-logs"),
        ],
      },
      {
        label: "模型与连接",
        items: [
          item("models", "模型配置", "native", "m15.read", "models"),
          item(
            "credentials",
            "集成连接",
            "native-admin",
            "m15.read",
            "connections",
          ),
          item("usage", "使用统计", "catalog", "m15.read", "usage"),
        ],
      },
      {
        label: "运行与运维",
        items: [
          item("runs", "运行记录", "catalog", "m16.read", "runs"),
          item("health", "系统状态", "health", "m16.read"),
          item("releases", "发布版本", "catalog", "m16.read", "releases"),
          item("backups", "备份管理", "backups", "m16.read", "backups"),
        ],
      },
    ],
  },
];
export const menuItems = modules.flatMap((group) =>
  group.sections.flatMap((section) => section.items),
);
