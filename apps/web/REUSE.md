# 前端复用与模块职责

本应用采用 Vue 3、TypeScript、Composition API 和模块路由。根目录 `index.html` 是上一阶段的独立预览，本应用不依赖其全局脚本。

| 路径                                         | 职责与来源                                                                                                                             |
| -------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `src/modules/manifest.ts`                    | 菜单、路由入口与权限；新增/移除模块首先在这里登记                                                                                      |
| `src/components/layout`                      | qKnow 210px侧栏、60px导航、34px标签；业务区和菜单独立滚动                                                                              |
| `src/api/client.ts`、`src/stores/session.ts` | 统一会话、CSRF、工作区与服务端错误，不在浏览器存密码或令牌                                                                             |
| `src/modules/manufacturing`                  | 设备/产品、标准、检测样本/证据/复核、数据库导入和维护分析；判断全部由后端完成                                                          |
| `src/modules/data`                           | CSV本地文件导入、服务端数据目录和受控查询；不连接旧项目数据源                                                                          |
| `src/modules/reports/reused`                 | 精确复用 Test5 的 JimuCanvas、JimuChart、JimuReferenceWidget、ScrollingBoard、JimuBusinessWidget；MaterialWidget精提原边框/装饰SVG与CSS，JimuWidgetRenderer 适配新平台数据绑定契约，移除旧API依赖 |
| `src/utils`                                  | Test5 图表精度、数据映射、主题、旋转缩放、内容安全与参考模板工具；来自 `apps/analytics/src/utils`                                      |
| `public/assets/analytics/jimu-screen`        | 仅提取20模板实际引用的40张皮肤/预览，未复制原项目包与依赖                                                                              |
| `src/modules/native`                         | 真实 Dify/Open WebUI 集成配置与原生控制台入口，不自建聊天循环或工作流执行引擎                                                          |
| `src/modules/catalog`                        | 目录类资源表单，按资源声明字段；不用于替代检测、报表或工作流领域功能                                                                   |

Test5来源：本机 `the_company_agent-codex-python-snapshot-20261020/apps/analytics`。qKnow素材来源为用户指定 `qknow-frontend-design` Skill 提取快照，保留qKnow标识；许可快照位于 `public/licenses/`，项目根NOTICE保留上游归属。

开发：`npm install` → `npm run dev`；后端同源代理目标为 `127.0.0.1:8080`。构建：`npm run build`。页面使用后端真实返回；缺连接或权限时呈现可操作错误，不填充随机数据。新组件使用中文说明，复制的上游工具保留其原有逻辑和接口。

报表扩展保留源目录ID：`POST /report-component-instances` 返回每个源素材的完整配置，前端不按canonical类型重建。`public/jimu-screen/iconlib/`仅提取目录实际引用的177张PNG；205素材包含178图片、13边框、12装饰、1轮播与1图标。`useCanvasActions.ts`接原画布选区、组合、复制剪贴、层级与快捷键，保存及修订由ReportStudio统一管理。

用户上传的图片只长期保存`config.file_id`，`AuthorizedImage.vue`使用当前授权读取；签名URL和data_uri不写回布局。报表历史恢复、另存模板、数据刷新与发布均等待服务端成功。

验证入口：`tests/verify.mjs`、`tests/materials.mjs`、`tests/report-interactions.mjs`分别覆盖本机隔离平台业务闭环、205素材和授权图片/版本行为。账号、密码、Cookie与CSRF只在环境及内存中；产物输出到已忽略的`test-results/`。

`seriesModelOption.ts`只消费服务端可信多系列模型，保留Decimal原值、缺值断点、按分类百分比和显式双轴；`tests/chart-variants.mjs`核验实际ECharts实例。正式质检报告使用`InspectionReportAction.vue`与`InspectionReportView.vue`，正文来自受授权的HTML导出，不把正式文档挤入固定大屏画布；普通报表仍使用原设计器。`tests/inspection-report.mjs`和`tests/data-source-controls.mjs`分别验证有限本机报告闭环和禁止连接的离线审批控件，均不自动启动服务。
