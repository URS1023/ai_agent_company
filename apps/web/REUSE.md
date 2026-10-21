# 前端复用与模块职责

本应用采用 Vue 3、TypeScript、Composition API 和模块路由。根目录 `index.html` 是上一阶段的独立预览，本应用不依赖其全局脚本。

| 路径                                         | 职责与来源                                                                                                                                                                                        |
| -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `src/modules/manifest.ts`                    | 菜单、路由入口与权限；新增/移除模块首先在这里登记                                                                                                                                                 |
| `src/components/layout`                      | qKnow 210px侧栏、60px导航、34px标签；业务区和菜单独立滚动                                                                                                                                         |
| `src/api/client.ts`、`src/stores/session.ts` | 统一会话、CSRF、工作区与服务端错误，不在浏览器存密码或令牌                                                                                                                                        |
| `src/modules/manufacturing`                  | 设备/产品、标准、检测样本/证据/复核、数据库导入和维护分析；判断全部由后端完成                                                                                                                     |
| `src/modules/data`                           | CSV本地文件导入、服务端数据目录和受控查询；不连接旧项目数据源                                                                                                                                     |
| `src/modules/reports/reused`                 | 精确复用 Test5 的 JimuCanvas、JimuChart、JimuReferenceWidget、ScrollingBoard、JimuBusinessWidget；MaterialWidget精提原边框/装饰SVG与CSS，JimuWidgetRenderer 适配新平台数据绑定契约，移除旧API依赖 |
| `src/utils`                                  | Test5 图表精度、数据映射、主题、旋转缩放、内容安全与参考模板工具；来自 `apps/analytics/src/utils`                                                                                                 |
| `public/assets/analytics/jimu-screen`        | 仅提取20模板实际引用的40张皮肤/预览，未复制原项目包与依赖                                                                                                                                         |
| `src/modules/native`                         | 真实 Dify/Open WebUI 集成配置与原生控制台入口，不自建聊天循环或工作流执行引擎                                                                                                                     |
| `src/modules/catalog`                        | 目录类资源表单，按资源声明字段；不用于替代检测、报表或工作流领域功能                                                                                                                              |

Test5来源：本机 `the_company_agent-codex-python-snapshot-20261020/apps/analytics`。qKnow素材来源为用户指定 `qknow-frontend-design` Skill 提取快照，保留qKnow标识；许可快照位于 `public/licenses/`，项目根NOTICE保留上游归属。

开发：`npm install` → `npm run dev`；后端同源代理目标为 `127.0.0.1:8080`。构建：`npm run build`。页面使用后端真实返回；缺连接或权限时呈现可操作错误，不填充随机数据。新组件使用中文说明，复制的上游工具保留其原有逻辑和接口。

报表扩展保留源目录ID：`POST /report-component-instances` 返回每个源素材的完整配置，前端不按canonical类型重建。`public/jimu-screen/iconlib/`仅提取目录实际引用的177张PNG；205素材包含178图片、13边框、12装饰、1轮播与1图标。`useCanvasActions.ts`接原画布选区、组合、复制剪贴、层级与快捷键，保存及修订由ReportStudio统一管理。

用户上传的图片只长期保存`config.file_id`，`AuthorizedImage.vue`使用当前授权读取；签名URL和data_uri不写回布局。报表历史恢复、另存模板、数据刷新与发布均等待服务端成功。

验证入口：`tests/verify.mjs`、`tests/materials.mjs`、`tests/report-interactions.mjs`分别覆盖本机隔离平台业务闭环、205素材和授权图片/版本行为。账号、密码、Cookie与CSRF只在环境及内存中；产物输出到已忽略的`test-results/`。

`seriesModelOption.ts`只消费服务端可信多系列模型，保留Decimal原值、缺值断点、按分类百分比和显式双轴；`tests/chart-variants.mjs`核验实际ECharts实例。正式质检报告使用`InspectionReportAction.vue`与`InspectionReportView.vue`，正文来自受授权的HTML导出，不把正式文档挤入固定大屏画布；普通报表仍使用原设计器。`tests/inspection-report.mjs`和`tests/data-source-controls.mjs`分别验证有限本机报告闭环和禁止连接的离线审批控件，均不自动启动服务。

## Dify工作流编辑岛（第一批）

`../../packages/dify-workflow/`精提本机Dify 1.17.1的Start、End、IfElse节点展示、三种原属性面板外层及原条件修改纯函数。11个源文件的原始SHA256、来源路径和适配说明在`SOURCE_MANIFEST.json`，原许可在`licenses/Dify-LICENSE.txt`；画布保留Dify原Logo及版权。不是完整原生编辑器，未提取的节点、容器、文件子条件与复杂字段配置保留整个原定义并只读。

`src/controls`与`src/adapters`是平台适配，替换原账号、console请求、工具目录及调试变量依赖；变量候选从本次图的连通上游计算。React岛不发HTTP、不保存本机数据、不自动保存、不重试。原条件分组/增删排序和变量引用随草稿变更；本地撤销/重做保留40个修改快照。输入区阻断画布删除快捷键，防止Shadow DOM重定向导致编辑文字时误删连线。

Vue宿主`GraphWorkflowEditor.vue`负责授权详情读取、显式POST创建、PUT和revision CAS、409保留草稿、失权清空及卸载取消。“新建→可视化流程”装配原Start/End输入输出骨架，填写名称后保存，继续使用画布编辑；“分步流程”保留旧编辑器。身份/工作区变化由既有WorkflowWorkspace键销毁整个实例。React岛在Shadow DOM中挂载，样式不注入全局；平台现有DSH外壳不变。已有图不会转写成steps；本轮未接图复制。

React/ReactDOM19.3.1、ReactFlow11.11.4、Immer11.1.21及React类型19.3.0均按原pnpm目录锁定值安装，未引入Next。`public.ts`为Vue/React唯一公共类型合同，Vue从`index.d.ts`引用，React包独立`tsconfig.json`检查，Vite解析真实TSX。纯检查入口：`packages/dify-workflow/tests/editor.mjs`（源组件SSR/图修改/来源SHA）与`apps/web/tests/workflow-editor.mjs`（Mock平台宿主），不代表实际浏览器/执行器验收。

## Open WebUI聊天编辑岛（持久对话第一批）

`../../packages/openwebui-chat/`从锁定Open WebUI 0.12.3精提完整`structuredOutput`算法、结构化输出/连续详情/折叠组件和图标，适配原Messages、Message、UserMessage、ResponseMessage与MessageInput的消息树和展示分区。`SOURCE_MANIFEST.json`逐项登记完整组件与片段的原路径/SHA256，原许可保留于`licenses/OpenWebUI-LICENSE.txt`，界面显示Open WebUI归属。没有复制原整站、账号store或模型/工具循环；Markdown使用原marked和DOMPurify，首批未提取原复杂代码编辑、公式、语音、图片/外部嵌入、重生或编辑并发送。

Vue宿主负责平台HttpOnly Cookie、CSRF、普通原生身份、真实可用模型、服务端分页会话目录、原生消息树、多轮父消息、单意图幂等、停止/审批预约与显式核对。Svelte只消费共享`public.ts`和回调，通过Shadow DOM在同页挂载；不是聊天系统iframe。原`FullHeightIframe`调用仅保留结构化输出兼容props，平台适配器从不创建iframe或加载URL，`allowEmbeds`关闭。Markdown不加载远程媒体、脚本或样式。

实时事件从平台SSE读取，经原eventsource-parser和原结构化输出算法还原；不随机分字、不自动重连、不自动重发。断流保留原Turn编号/幂等意图，用户显式核对；202停止仅显示正在停止，终态以服务端读取为准。原件只接受平台附件映射，历史附件按用户消息ID精确装配并授权下载。当前支持单文件上传、就绪状态、显式刷新/核对、选择最多10附件及原件下载；原生副本清理尚未提供前端操作。

消息不再写本机localStorage。`/history`为本人服务端目录，旧本机记录通过独立只读入口保留；用户、工作区、权限或路由切换会卸载实例、取消请求并清理正文/原件入口，不接管原生后台模型任务。个人记忆管理仍独立，不自动采集或注入。

独立库按原版本使用Svelte5.56.0、Vite5.4.21、Svelte插件4.0.4、marked9.1.6、DOMPurify3.4.11、eventsource-parser1.1.2及Tailwind4.2.1。根`npm run setup`安装局部锁定依赖，`npm run check`独立检查Svelte和Vue，`npm run build`先生成库再构建宿主；构建不依赖原源码目录或已有dist。纯回归：`node packages/openwebui-chat/tests/messages.mjs`、`node apps/web/tests/native-chat.mjs`，覆盖树分支、分帧、幂等/未知、停止预约、附件与失权卸载；不表示已完成实际模型/浏览器验收。
