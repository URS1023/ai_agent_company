# 需求分析文件索引

维护入口：本文件；范围仅限本轮需求分析，不替代各独立项目内部索引。

| 路径（相对本目录） | 用途与证据 | 引用/依赖 | 归属 | 是否仍需 | 能否删除 | 核查日期/任务 |
|---|---|---|---|---|---|---|
| 工业AI平台_需求梳理_v1.md | 已读取；同一豆包线程的既有需求与功能映射，原文和完成度待复核 | 七个同级项目 | 原作者；本轮只读 | 在用 | 不可删除 | 2026-10-20 / T-agent-1020-root-01 |
| 工业AI平台_功能矩阵看板.html | 已确认文件存在；既有看板，本轮未验证交互 | build_board.py、需求梳理 v1 | 原作者；本轮只读 | 待核实 | 暂不可删除（待核实） | 2026-10-20 / T-agent-1020-root-01 |
| PROJECT_STATUS.md | 本轮需求、任务与问题唯一入口 | 本文件 | agent-1020-root | 在用 | 不可删除 | 2026-10-20 / T-agent-1020-root-01 |
| FILE_INDEX.md | 本轮重要文件索引唯一入口 | PROJECT_STATUS.md | agent-1020-root | 在用 | 不可删除 | 2026-10-20 / T-agent-1020-root-01 |
| 工业AI平台_重写需求与实施建议_20261020.md | 本轮重写方向、需求、实现、效果与验收；已核对当前用户更正及官方资料 | 原会话、V2附件、源码、历史会话 | agent-1020-root | 在用 | 不可删除 | 2026-10-20 / T-agent-1020-root-01 |
| 豆包会话原文_20261020.txt | 公开分享页解析出的16条原始消息，区分user_type | 豆包线程 xtlnQmSMZunC1D576 | agent-1020-root | 在用 | 不可删除 | 2026-10-20 / T-agent-1020-root-01 |
| Test5全功能业务平台重构方案_V2_分享附件.md | 原分享会话附件；旧功能基线，技术与布局约束需重新确认 | 豆包原会话 | 原附件作者；本轮只读 | 在用 | 不可删除 | 2026-10-20 / T-agent-1020-root-01 |

| 工业AI平台_重写需求与实施建议_20261020.md | 最新修订：落实用户确认的OpenWebUI/Dify/qKnow分工，增加qKnow功能映射与Dify定时/Webhook证据 | 用户最新指令、两项只读补查、官方文档 | agent-1020-root | 在用 | 不可删除 | 2026-10-20 / T-agent-1020-root-02 |

## 2026-10-20 本轮完整复用盘点索引追加

| 相对路径 | 用途及关键引用/依赖 | 归属Agent | 是否仍需 | 能否删除 | 判断依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|
| 四项目复用核查与历史需求总表_20261020.md | 当前分析唯一主入口，关联四专项与历史归纳 | agent-1020-root | 在用 | 不可删除 | 用户最新R16及静态证据汇总 | 2026-10-20 / T-agent-1020-root-03 |
| Dify_功能与界面核查_20261020.md | Dify完整功能、节点、页面、依赖及复用建议 | agent-1020-dify | 在用 | 不可删除 | 对应dify-main源码及104页CSV | 2026-10-20 / T-agent-1020-dify-01 |
| Dify_界面源码索引_20261020.csv | 104个实际page.tsx逐文件清单 | agent-1020-dify | 在用 | 不可删除 | 物理文件枚举，含并行侧栏 | 2026-10-20 / T-agent-1020-dify-01 |
| OpenWebUI_功能与界面核查_20261020.md | 对话内核、Agent、工具、知识与完整复用边界 | agent-1020-webui | 在用 | 不可删除 | OpenWebUI源码、报告作者已纠正NoSQL术语 | 2026-10-20 / T-agent-1020-webui-01、02 |
| OpenWebUI_界面源码索引_20261020.csv | 48路由页和379子界面组件，427唯一路径 | agent-1020-webui | 在用 | 不可删除 | 全量路由和主要业务目录枚举 | 2026-10-20 / T-agent-1020-webui-01 |
| qKnow_功能与界面核查_20261020.md | 11业务域、预留接口/节点、视觉与功能参考 | agent-1020-qknow | 在用 | 不可删除 | qKnow源码与3张既有图片 | 2026-10-20 / T-agent-1020-qknow-01 |
| qKnow_界面源码索引_20261020.csv | views下194文件索引，含190Vue | agent-1020-qknow | 在用 | 不可删除 | 实际路径、用途、预留与验证状态 | 2026-10-20 / T-agent-1020-qknow-01 |
| Test5_功能界面与报表资产核查_20261020.md | 查询、设计器、组件模板和提取依赖 | agent-1020-root | 在用 | 不可删除 | Test5关键源码、JSON及1张预览 | 2026-10-20 / T-agent-1020-root-03 |
| Test5_界面源码索引_20261020.csv | 161Vue、37路由及目标后端共250条 | agent-1020-root | 在用 | 不可删除 | audit_test5_assets.py静态输出 | 2026-10-20 / T-agent-1020-root-03 |
| Test5_报表组件模板清单_20261020.csv | 397资产记录，含20模板、33来源报表 | agent-1020-root | 在用 | 不可删除 | 报表resources实际JSON记录 | 2026-10-20 / T-agent-1020-root-03 |
| Test5_静态盘点统计_20261020.json | 精确统计口径及目录meta | agent-1020-root | 在用 | 不可删除 | 与CSV核对通过，不代表动态验收 | 2026-10-20 / T-agent-1020-root-03 |
| Codex历史需求归纳_20261020.md | 历史需求、变更与当前有效含义，52条ID引用 | agent-1020-sessions | 在用 | 不可删除 | 引用均存在于脱敏原文 | 2026-10-20 / T-agent-1020-history-02 |
| Codex历史需求原文_脱敏_20261020.jsonl | 89条相关历史消息及稳定ID/源行号，凭据行省略 | agent-1020-root | 在用 | 不可删除 | 7主会话，只读解析并脱敏；常见模式复核0 | 2026-10-20 / T-agent-1020-root-03 |
| Codex历史会话覆盖_20261020.json | 94现存日志筛出7相关主会话的覆盖证据 | agent-1020-root | 在用 | 不可删除 | 不等于用户全部历史均保留可读 | 2026-10-20 / T-agent-1020-root-03 |
| audit_codex_history.py | 可重复只读提取相关用户历史并脱敏 | agent-1020-root | 在用 | 不可删除 | 不读凭据文件、不运行服务；AST检查通过 | 2026-10-20 / T-agent-1020-root-03 |
| audit_test5_assets.py | 可重复静态枚举源码/组件/模板 | agent-1020-root | 在用 | 不可删除 | 只读源码JSON，不导入业务模块；AST通过 | 2026-10-20 / T-agent-1020-root-03 |

## 2026-10-20 项目开发文档索引追加（关联R17—R20 / T-agent-1020-root-04）

| 相对路径 | 用途 | 关键引用/依赖 | 归属Agent | 是否仍需 | 能否删除 | 判断依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|---|
| ../项目文档/README.md | 开发文档唯一总入口、角色阅读、范围统计、聚合、实施与扩展概要 | 4核心正文、两结构化源与现有历史报告 | agent-1020-root | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/01_产品功能与UX.md | 角色任务、行业差异、逐类UX、上手与交互验收 | 范围基线.json、用户R18/R19/R20及qknow-frontend-design Skill | agent-1020-product；已交接协调维护 | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/02_架构与实现设计.md | 四方聚合、7项决策、权限、存储、ER、任务状态与扩展切片 | 原生源码核查、接口契约及平台领域规划 | agent-1020-architecture；已交接协调维护 | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/03_开发计划与验收.md | 团队与WBS、开发卡、质量、数据测试边界、CI、上线回滚 | 范围与接口事实源、产品/技术设计 | agent-1020-delivery；已交接协调维护 | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/范围基线.json | 17模块125功能75逻辑页面的可生成事实源；含角色输入输出实现验收和阶段 | build_docs.py读取；CSV不手工维护 | agent-1020-product；已交接协调维护 | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/contracts/接口基线.json | 221规划操作和125功能承载映射的事实源；字段权限与实现归属 | build_docs.py读取，未冒充已实现接口 | agent-1020-root | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/contracts/openapi.json | 生成的OpenAPI3.1.1项目草案，可用于需求Ready细化和契约评审 | 接口基线.json、范围基线.json、build_docs.py；官方Schema校验通过 | agent-1020-root | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/contracts/接口目录.csv | 按method+path统计的221行操作目录 | 两事实源自动生成；与原生全量API分开计数 | agent-1020-root | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/assets | 生成的功能/页面/接口映射CSV、统计/校验JSON和架构/UX展示资源 | 两事实源及生成工具；具体原型另条登记 | agent-1020-root，原型由agent-1020-architecture | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/tools/build_docs.py | 校验ID/引用与承载，生成目录和契约，并刷新正文数量及模块表 | 仅文档输入，不连接DB/模型/业务服务；AST和实际执行通过 | agent-1020-root | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/tools/render_diagrams.py | 生成架构SVG与PNG，旧文字UX绘制逻辑已移除 | 本机CairoSVG；仅文档图形 | agent-1020-root | 在用 | 不可删除 | 当前开发基线或关联生成物，更新须保持源与输出一致 | 2026-10-20 / T-agent-1020-root-04 |

## 最终基线与交互原型索引补充（2026-10-20，替代前条数量摘要）

| 相对路径 | 用途 | 关键引用/依赖 | 归属Agent | 是否仍需 | 能否删除 | 判断依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|---|
| ../项目文档/contracts/接口基线.json | 最终227规划操作，补批次/任务样本登记查询与归属 | 98业务聚合、129复用适配；125功能映射；生成工具 | agent-1020-root；六项补齐作者agent-1020-delivery | 在用 | 不可删除 | 当前事实源；本条数量替代前条221摘要 | 2026-10-20 / T-agent-1020-delivery-04、T-agent-1020-root-04 |
| ../项目文档/contracts/openapi.json | 最终227操作/173路径的生成项目契约 | 官方OpenAPI3.1 Schema校验0错误；细字段按Ready冻结 | agent-1020-root | 在用 | 不可删除 | 随事实源重建，不手改 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/assets/设备维护交互原型.html | 按指定qknow-frontend-design Skill的桌面交互样例 | 独立HTML/CSS/JS、示例数据、localStorage；无API/DB/外部资源 | agent-1020-architecture；已交接协调维护 | 在用 | 不可删除 | 三视图、筛选、明细、检查、状态/记录与滚动已浏览器核查 | 2026-10-20 / T-agent-1020-architecture-02、03 |
| ../项目文档/assets/ux-workbench.png | 当前原型1440x900实际浏览器截图 | 上条HTML；README和01引用；替代旧UX示意SVG | agent-1020-root | 在用 | 不可删除 | 已实际查看，无来源解释、过程旁白和重复结论区 | 2026-10-20 / T-agent-1020-root-04 |
| ../项目文档/assets/platform-architecture.svg | 聚合架构矢量示意 | render_diagrams.py；同名PNG供查看 | agent-1020-root | 在用 | 不可删除 | 架构说明用途，非产品页面内容 | 2026-10-20 / T-agent-1020-root-04 |

## 前端预览与全量发布索引（agent-1020-root，2026-10-20 / T-agent-1020-root-05）

以下路径相对工作区；发布库根目录为ai_agent_company/，发布后的同名子路径保持用途。进度文件以本项目需求梳理目录为唯一入口。

| 相对路径 | 用途 | 关键引用/依赖 | 归属Agent | 是否仍需 | 能否删除 | 判断依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|---|
| ../ai_agent_company/ | 指定GitHub仓库独立检出与完整工作区发布目录 | 原main父历史及用户R21全文件覆盖授权 | agent-1020-root | 在用 | 不可删除 | 当前推送工作目录，未授权清理 | 2026-10-20 / T05 |
| ../ai_agent_company/index.html | 可离线打开的制造业前端交互预览 | qknow-design-test源HTML、frontend样式与脚本、build_preview.py | agent-1020-root | 在用 | 不可删除 | 45入口及三核心页浏览器核查通过 | 2026-10-20 / T05 |
| ../ai_agent_company/frontend/ | 新增导航及设备/产品业务界面源文件 | 现有qKnow样例全局组件与本地样例数据 | agent-1020-root | 在用 | 不可删除 | 构建源，不直接修改生成HTML | 2026-10-20 / T05 |
| ../ai_agent_company/tools/ | 单HTML构建、本地静态服务、工作区复制工具 | Python、Node内置库；无DB依赖 | agent-1020-root | 在用 | 不可删除 | 静态服务只暴露预览首页，不暴露配置 | 2026-10-20 / T05 |
| ../ai_agent_company/screenshots/ | 实际桌面截图与本轮检查结果 | Playwright本地浏览器，示例数据 | agent-1020-root | 在用 | 不可删除 | 交付视觉与交互检查证据 | 2026-10-20 / T05 |
| ../ai_agent_company/workspace-manifest.json | 全文件镜像数量、大小与排除范围 | sync_workspace.py实际遍历生成 | agent-1020-root | 在用 | 不可删除 | 用户要求全量上传的范围证据 | 2026-10-20 / T05 |
| ../ai_agent_company/.gitattributes | 超大zip的Git LFS规则 | 本机Git LFS、430863679字节原文件 | agent-1020-root | 在用 | 不可删除 | 普通GitHub文件限制要求 | 2026-10-20 / T05 |
| ../ai_agent_company/licenses/、NOTICE.md | qKnow来源许可与各项目归属 | 原项目许可保留原样 | agent-1020-root | 在用 | 不可删除 | 复用素材及源码许可依据 | 2026-10-20 / T05 |

## R21发布范围更正（agent-1020-root，2026-10-20 / T05）
- 前端index.html、frontend/、tools/build_preview.py、tools/serve.mjs、screenshots/及项目文档继续在用/不可删除；build_preview.py改为从独立HTML刷新具名嵌入区，无原项目依赖。
- 原项目目录、设备包、workspace-manifest.json、.gitattributes及sync_workspace.py仍在本地，最新用户指令取消其发布。该变更不构成删除授权；仍需性与删除条件沿用前条，未把“取消上传”写成“可删除”。

## T05发布核验（agent-1020-root，2026-10-20）
- 远端main的9feac01d版本确认仅68个前端/文档/许可/截图文件，原始参考源码和数据包不在当前树；预览独立构建，无原项目依赖。
- 项目唯一进度和索引位置不变；本地原项目与镜像仍在用或待核实、不可据取消发布擅自删除。前端与文档路径继续在用/不可删除，用户取消的复制工具与清单只留本地。


## 实施目录增量索引（agent-1020-root，2026-10-20；T-root06/R22—R24）
以下路径均在实际Git项目 ai_agent_company 根下核查；不触发删除。作者范围以共享进度的有效认领为准。

| 项目相对路径 | 用途与关键依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|
| apps/web/src/ | Vue3真实平台、manifest/路由/领域工作区；调用/api/v1 | agent-1020-web | 在用 | 不可删除 | Vite生产构建与浏览器14项验收入口 | 2026-10-20/T-web02,03 |
| apps/web/package.json、package-lock.json | 固定前端安装依赖和构建脚本 | agent-1020-web | 在用 | 不可删除 | npm ci/typecheck/build | 2026-10-20/T-web02 |
| apps/web/public/ | 经过明确引用的报表皮肤/预览，非完整原资料 | agent-1020-web | 在用 | 不可删除 | 画布/模板静态资源引用 | 2026-10-20/T-web03 |
| apps/web/test-results/ | 浏览器测试JSON/临时截图，Git忽略 | agent-1020-web | 在用 | 暂不可删除（待核实） | 验收证据需转存脱敏摘要后再评估 | 2026-10-20/T-web02 |
| services/platform/agent_platform/main.py | 平台装配、事务/安全边界、静态生产前端 | agent-1020-root | 在用 | 不可删除 | uvicorn factory入口 | 2026-10-20/T-root06 |
| services/platform/agent_platform/registry.py | 10技术模块装配17业务模块，可配置启停 | agent-1020-root | 在用 | 不可删除 | 前后端权限/运行模块目录 | 2026-10-20/T-root06 |
| services/platform/agent_platform/core/ | 身份上下文/事务/配置仓储/任务/凭据/文件公共协议 | agent-1020-root | 在用 | 不可删除 | 业务模块共同依赖；身份作者已交接 | 2026-10-20/T-root06,T-native03 |
| services/platform/agent_platform/modules/identity/ | 登录/组织/角色/岗位/服务身份/对象授权/凭据轮换 | agent-1020-root（接管） | 在用 | 不可删除 | 19项真实API隔离测试；已释放认领 | 2026-10-20/T-native03 |
| services/platform/agent_platform/modules/workspace/ | 任务、通知、工作台、搜索、偏好 | agent-1020-root | 在用 | 不可删除 | 平台首页与跨模块任务关联 | 2026-10-20/T-root06 |
| services/platform/agent_platform/modules/files/ | 文件上传/授权下载、引用影响、真Office生成 | agent-1020-root | 在用 | 不可删除 | 报表/图谱/文档来源依赖 | 2026-10-20/T-root06 |
| services/platform/agent_platform/modules/catalog/ | 应用发布、能力/技能目录、行业包入口配置 | agent-1020-root | 在用 | 不可删除 | 不承担质检和工作流内核 | 2026-10-20/T-root06 |
| services/platform/agent_platform/modules/graph/ | 本体、实体关系、有限探索、抽取复核、发布快照 | agent-1020-root | 在用 | 不可删除 | 图谱关系及权限/版本回归 | 2026-10-20/T-root06 |
| services/platform/agent_platform/modules/operations/ | 实际任务/发布/用量/审计、平台本地备份及隔离恢复演练 | agent-1020-root | 在用 | 不可删除 | 管理台读取与自身SQLite备份，不含旧业务库 | 2026-10-20/T-root06 |
| services/platform/agent_platform/modules/manufacturing/ | 强模型质检、维护、预警、不可变证据与人工复核 | agent-1020-root（domain02稳定交接） | 在用 | 不可删除 | 数据域受控取数和统一Task | 2026-10-20/T-domain02 |
| services/platform/agent_platform/modules/data/ | 数据源/字段权限/SQL AST与预算/预览/处理/合成/NL2SQL | agent-1020-root（domain02稳定交接） | 在用 | 不可删除 | 未批准外部源不连接，异常源隔离 | 2026-10-20/T-domain02 |
| services/platform/agent_platform/modules/reports/ | 20模板、组件/绑定、画布、发布版本、真实多格式导出 | agent-1020-domain | 在用 | 不可删除 | 继续扩大原生组件支持，非全部326已实现 | 2026-10-20/T-domain03 |
| services/platform/agent_platform/modules/integrations/、adapters/ | Dify/OWUI实际协议及身份/运行/回调映射，MCP/OpenAPI接入 | agent-1020-root（native02交接） | 在用 | 不可删除 | Mock协议验收与后续原生服务联调 | 2026-10-20/T-native02 |
| services/platform/migrations/、alembic.ini | 固定初始结构与后续增量迁移 | agent-1020-root | 在用 | 不可删除 | 版本升级可复核，生产禁止隐式create_all | 2026-10-20/T-root06 |
| services/platform/requirements.txt、requirements.lock.txt | 直接依赖与本次实测固定环境 | agent-1020-root | 在用 | 不可删除 | 独立.venv安装，不改变原生Python环境 | 2026-10-20/T-root06 |
| services/platform/tests/ | 临时SQLite/Mock协议、权限和业务回归 | 各测试作者/以新认领为准 | 在用 | 不可删除 | 验收行为与失败边界证据 | 2026-10-20/R22 |
| deploy/native/ | 本机原生部署准备/版本来源/受控进程启动停止 | agent-1020-native | 在用 | 不可删除 | R24已取消Docker交付，正在改为Windows独立环境 | 2026-10-20/T-native04 |
| tools/project.mjs | setup/dev/start/build/check/test统一本机入口 | agent-1020-root | 在用 | 不可删除 | package.json直接引用 | 2026-10-20/T-root06 |
| tools/manage_database.py | 核验当前项目独立SQLite并显式升级/接管 | agent-1020-root | 在用 | 不可删除 | 拒绝外部/越界路径，初始迁移回归 | 2026-10-20/T-root06 |
| tools/render_report.mjs | 离线HTML→真PDF/PNG，无外部网络 | agent-1020-root | 在用 | 不可删除 | reports/pdf.py以受限子进程调用 | 2026-10-20/T-root06 |
| tools/configure_models.py、core/provider_settings.py | 本机加密模型配置、短连通与原生环境注入 | agent-1020-root | 在用 | 不可删除 | R23指定Go DS4.1/硅基bge-m3，密钥不入Git | 2026-10-20/R23 |
| .local/ | 本项目独立数据/密钥/测试库/运行日志，Git忽略 | 本机用户 | 在用 | 不可删除 | 含不可从Git恢复的本机密文及主密钥；未授权清理 | 2026-10-20/R22,R23 |
| .venv/ | 本项目平台Python依赖，不是Dify/OWUI共用环境 | agent-1020-root | 在用 | 满足条件后可删除 | 停止所属进程且可用lock文件重建；不删除.local数据 | 2026-10-20/T-root06 |
| index.html、frontend/、tools/build_preview.py、tools/serve.mjs | 历史独立样式原型，preview:legacy专用 | agent-1020-root | 在用 | 暂不可删除（待核实） | 保留已交付原型便于对照，不再作为真实系统默认入口 | 2026-10-20/R21,R22 |


## 新增契约/素材/验证索引（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 日期/任务 |
|---|---|---|---|---|---|---|
| modules/reports/resources/source/（位于services/platform/agent_platform） | 原widgetFactory/widgetSizing/模板种子精确提取，build_presets再生 | agent-1020-domain | 在用 | 不可删除 | 保留205素材原始初始config与尺寸，不含旧连接 | 2026-10-20/T-domain03 |
| services/platform/agent_platform/modules/reports/materials.py | 13边框/12装饰/真实图片图标及首帧离线渲染 | agent-1020-domain | 在用 | 不可删除 | PDF/PNG与画布一致性和引用权限 | 2026-10-20/T-domain03 |
| apps/web/src/modules/reports/reused/ | Test5原画布/图表/素材与业务renderer适配 | agent-1020-web | 在用 | 不可删除 | 205项实际浏览器验证，剩余变体继续补 | 2026-10-20/T-web03,04 |
| 项目文档/contracts/platform.openapi.json | 从实际代码导出的当前API，不覆盖原设计openapi.json | agent-1020-root | 在用 | 不可删除 | export_api.py可再生，联调使用真实协议 | 2026-10-20/T-root06 |
| 项目文档/contracts/实施验收.csv | 125项原需求逐项实现入口/证据/缺口 | agent-1020-root | 在用 | 不可删除 | 防止以接口和页面存在冒充验收完成 | 2026-10-20/R22 |
| tools/export_api.py | 无DB连接导出运行代码API及ID唯一性检查 | agent-1020-root | 在用 | 不可删除 | 与设计契约并存，禁止互相混记 | 2026-10-20/T-root06 |
| .github/workflows/check.yml | 远程仅前端构建/类型及Python静态编译 | agent-1020-root | 在用 | 不可删除 | 不执行远程DB/模型测试，本机验证独立 | 2026-10-20/T-root06 |
| licenses/Dify-LICENSE.txt、OpenWebUI-LICENSE.txt、OpenWebUI-LICENSE_NOTICE.txt | 原生运行/来源引用的原许可快照 | agent-1020-root | 在用 | 不可删除 | 提取自本机原参考项目，不改原品牌条款 | 2026-10-20/R22 |

## 文件索引补充（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/关键依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 日期/任务 |
|---|---|---|---|---|---|---|
| apps/web/src/modules/data/TargetApprovalForm.vue | 精确目标只读、用途/归属/时限/范围与异常恢复控件；DataWorkspace提交确认 | agent-1020-root | 在用 | 不可删除 | 外部连接仍需平台显式开关，批准本身不发连接；新UI类型检查通过，浏览器待验 | 2026-10-20/T-root07 |
| services/platform/agent_platform/modules/data/elasticsearch.py | 固定索引Mapping与SQL翻译/有限读取，不接DSL/脚本/任意URL | agent-1020-root | 在用 | 不可删除 | 6纯Mock测试；未实际连接ES | 2026-10-20/T-root07 |
| apps/web/src/modules/reports/seriesModelOption.ts | 消费服务端可信多系列/百分比/双轴/缺值模型，复用原图表主题 | agent-1020-web，已交接root | 在用 | 不可删除 | 8变体真实ECharts断言和10后端边界测试通过 | 2026-10-20/T-web04,05/T-domain06 |
| apps/web/tests/chart-variants.mjs | 一报表八图、有限取数或复用快照，仅本轮隔离平台 | agent-1020-web，已交接root | 在用 | 不可删除 | 源Response属性/下拉竞态已修；不得对外库自动运行 | 2026-10-20/T-web05 |
| apps/web/tests/data-source-controls.mjs | 离线源配置/审批控件白名单验收，严禁触发连接/查询 | agent-1020-web | 在用 | 不可删除 | 首验隐藏switch定位待修复后复验；不是ES联调 | 2026-10-20/T-web06,07 |
| deploy/native/windows_services.py | Windows原生隔离环境/监听归属、API-only预算与服务依赖 | agent-1020-native | 在用 | 不可删除 | 守卫35项Mock通过；API-only实际启动仍待验，T-native07仅env小处认领中 | 2026-10-20/T-native05,06,07 |


## 正式报告与受控验收补充（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/关键依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 日期/任务 |
|---|---|---|---|---|---|---|
| services/platform/agent_platform/modules/reports/inspection_source.py、inspection_document.py、resources/inspection_report_v1.json | 固定检验run/标准/复核证据，权限重查及流式正式文档 | agent-1020-root接管；文档排版T-domain08进行中 | 在用 | 不可删除 | 正式生成/刷新/发布/历史/7格式依赖；10后端+4浏览器通过，PDF排版复验中 | 2026-10-20/T-domain07,08 |
| apps/web/src/modules/manufacturing/InspectionReportAction.vue、apps/web/src/modules/reports/InspectionReportView.vue | 准确run生成正式报告、隔离预览与改名/刷新/发布/导出 | agent-1020-root，web08已交接 | 在用 | 不可删除 | 35 API/4浏览器检查通过，深链与无m11.write角色已验证 | 2026-10-20/T-web08 |
| apps/web/tests/inspection-report.mjs | 新CSV、两次以内业务取数、真实质检到正式报告闭环 | agent-1020-root，web08已交接 | 在用 | 不可删除 | 当前只核验本项目新SQLite归属后串行运行，不对旧/外部DB测试 | 2026-10-20/T-web08 |
| tools/verify_native_api.py、services/platform/tests/test_native_api_verifier.py | 有限原生API登录/隔离/唯一短SSE验收与密文检查点 | agent-1020-root，native07已交接 | 在用 | 不可删除 | 与Windows守卫合计56 Mock测试通过；实际原生尚未验 | 2026-10-20/T-native07 |
| deploy/native/linux-runtime.md | Agent V2/沙箱的本机Linux候选与系统变更计划 | agent-1020-root | 在用 | 不可删除 | 不含Docker，尚未执行WSL安装或系统组件变更 | 2026-10-20/R24 |
- 更正前索引证据状态：data-source-controls.mjs已修可见switch定位，4项/16 API通过，0 ES连接/查询；windows_services.py的T-native07已交接，56守卫/验证器Mock测试通过，实际API仍待验。


## 验收产物与原生故障数据（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/关键依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 日期/任务 |
|---|---|---|---|---|---|---|
| apps/web/test-results/inspection-report-25-samples.pdf | 25新合成样本、三维明细/标准/复核的5页分页验收样本，Git忽略 | agent-1020-root | 在用 | 暂不可删除（待核实） | 全页视觉与文本末行/精度通过；转存验收证据后再评估，不是客户现场数据或签名认证 | 2026-10-20/T-domain08 |
| .local/native_windows/openwebui/data/webui.db | 首次API-only冷启动生成的本项目独立原生SQLite | 本机用户/root核查 | 在用 | 不可删除 | 45秒未监听已停止进程，迁移日志及故障记录保留；不是可随意清除的旧库，无HTTP或模型验收 | 2026-10-20/I-root10 |
- T-domain08全部报告排版已交接root并通过12局部测试及最终5页视觉；tools/verify_native_api.py真实验收尚未执行，56 Mock结果不得替代原生成功。


## Docker授权变更索引（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/关键依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 日期/任务 |
|---|---|---|---|---|---|---|
| deploy/docker_desktop.ps1 | 固定官方包摘要/签名校验，D盘程序与WSL数据根定址安装，状态记录 | agent-1020-root | 在用 | 不可删除 | PowerShell parser/Check已过，安装尚未执行；R25最新Docker授权，不能再按旧no-Docker拒绝 | 2026-10-20/T-root09 |
- 部署状态更正：R25覆盖此前禁止Docker要求，现有Windows方案/失败数据保留；deploy/native/linux-runtime.md为此前独立Ubuntu候选，当前优先Docker随附WSL，不自动安装独立发行版。


## Docker安装与协议核验进展（agent-1020-root，2026-10-20）
- deploy/docker_desktop.ps1 已实际完成D盘Docker安装（exit0/注册表/CLI证明），新增PrepareWSL固定微软MSI、摘要/签名和/norestart边界；WSL MSI仍下载中，Linux引擎与实际VHDX位置尚未核验，仍在用/不可删除。
- tools/verify_native_api.py 与test_native_api_verifier.py 已交接root，合法原生事件模式/准确回复验证等51条Mock通过；本次实际回复另以已停本地库1索引读确认，不改历史unknown，不冒称完整HTTP SSE通过。


## 系统安装完成与验收边界（agent-1020-root，2026-10-20）
- deploy/docker_desktop.ps1 的D盘Docker和微软WSL3.0.3 MSI安装均已真实完成（两个exit0、双摘要/签名、无自动重启）。程序/注册表位于D:/Docker/Desktop，安装配置wslDefaultDataRoot指向D:/Docker/wsl-data；引擎实际启动与VHDX核验因I-root13资源预检未过而待验。
- deploy/native/prepare.py、control.py仅更新已过时的“不用Docker”拒绝原因，保留全栈入口暂停：需要新项目、精确镜像、累计资源及失败回收守卫，不能直接启动17服务；仍在用/不可删除。

## 独立Docker运行与验收入口（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/关键引用与依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|
| deploy/docker_desktop.ps1 | 固定Docker/WSL安装；Start为Docker进程设置D盘加密Roaming/Local目录，主机资源监测与所属进程回收 | agent-1020-root | 在用 | 不可删除 | 安装exit0/官方摘要签名已核；Windows PowerShell解析、Check及故障标记阻止启动已过；双目录修复实际启动待验 | 2026-10-20/T-root09,10 |
| deploy/docker/openwebui.py | 新D盘单容器prepare/pull/start/inspect/stop与digest/绑定/资源归属；被Docker API守卫复用 | agent-1020-native | 在用 | 不可删除 | 源码已实际检查，仍在补本次主机资源守卫；不视为实际容器验收 | 2026-10-20/T-native10 |
| deploy/docker/provider_entrypoint.py | 容器只读解密本次模型配置后exec原生start.sh；不把密钥写入Docker Env | agent-1020-native | 在用 | 不可删除 | 控制器校验并复制入口摘要，镜像候选仍待拉取/运行 | 2026-10-20/T-native10 |
| tools/verify_docker_api.py | 复用原生Verifier/CredentialLedger，固定13080/14HTTP/唯一模型；API+UI共享20请求和60秒 | agent-1020-domain | 在用 | 不可删除 | 36纯Mock已过，主机资源复核小处T-domain11继续；实际API未运行 | 2026-10-20/T-domain09,10,11 |
| services/platform/tests/test_docker_openwebui.py、test_docker_api_verifier.py | 纯Mock/临时加密文件/无DB的Docker归属与协议预算回归 | 原作者native/domain，协调者root验收 | 在用 | 不可删除 | API36条已过，控制器守卫待原作者交接后串行运行 | 2026-10-20/T-native10/T-domain11 |
| apps/web/tests/docker-openwebui.mjs | 原生登录表单真实可见与字段有效性，系统Chrome、不提交；消费先行API实际额度/UTC时刻 | agent-1020-root接管，web09已交接 | 在用 | 不可删除 | node --check过，实际浏览器未运行；不将登录UI当聊天UI通过 | 2026-10-20/T-web09 |
- 外部运行路径已核：D:/Docker/Desktop（程序）、D:/Docker/wsl-data/main/ext4.vhdx与disk/docker_data.vhdx（实际WSL磁盘）、D:/Docker/desktop-profile-20261020-01/Roaming（首次D盘新加密配置）、D:/Docker/installers/engine-resource-incident.json（资源故障标记）。均在仓库外，不提交、不清理；新profile-02和新OWUI runtime尚未创建。

## Docker工具验收状态更新（agent-1020-root，2026-10-20）
- 前条T-native10、T-domain09/10/11入口已全部交接root；组合97条Mock/临时文件测试通过（controller54/API43）。provider_entrypoint/两个验收工具Python编译通过，原生UI脚本node --check过；均未启动真实容器/浏览器或发模型请求。
- deploy/docker_desktop.ps1 双目录隔离已实现并通过Windows PowerShell5解析与故障标记拒绝；D盘profile-02与OpenWebUI runtime仍未创建，实际新启动未验。现有D盘程序/两个VHDX/故障标记继续保留，不自动清理。

## Windows 原生与 Docker 状态纠正（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/关键引用与依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|
| README.md、deploy/native/README.md | 平台与 Windows 原生启动说明；引用 windows.py、既有成功证据及 Dify Linux 边界 | agent-1020-root | 在用 | 不可删除 | 明确 Docker 非平台/OWUI 必需；镜像失败与未完成界面如实记录 | 2026-10-20/T-root13 |
- 外部 D:/Docker/desktop-profile-20261020-02 与 D:/Docker/agent-platform/openwebui-20261020-01 已实际创建：前者为成功启动的进程专用加密配置；后者为固定镜像失败记录及未启动的密文容器配置。均保留、不提交、不清理，当前无业务容器。deploy/docker_desktop.ps1 的 T-native11 已交接且 Start 实际执行成功；本轮已正常关闭 Desktop。

## Windows 双服务交付入口（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/关键引用与依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|
| deploy/native/windows_frontend.py | 固定官方wheel摘要校验、安全提取前端到已登记源码副本 | agent-1020-root | 在用 | 不可删除 | 真实5681文件提取及6项ZIP边界测试，不改Python依赖 | 2026-10-20/T-root14 |
| deploy/native/desktop.py | Windows平台/完整OWUI启动、复用、停止及本机账号显示 | agent-1020-root | 在用 | 不可删除 | 真实监听/浏览器通过；R32无内存门槛，保留超时/归属 | 2026-10-20/T-root14 |
| 启动系统.cmd、停止系统.cmd、查看本机账号.cmd | 用户双击入口，调用desktop.py，不显示模型密钥 | agent-1020-root | 在用 | 不可删除 | 固定仓库相对路径，运行凭据Git忽略 | 2026-10-20/T-root14 |
| apps/web/tests/windows-desktop.mjs | 平台登录/原生跳转/真实聊天UI一次短回复，可只验原生阶段 | agent-1020-root | 在用 | 不可删除 | 原生29HTTP/215静态/唯一模型通过，两张截图已查看 | 2026-10-20/T-root14 |
| services/platform/tests/test_windows_frontend.py、test_native_windows.py | 前端提取安全及原生启动确认/归属/预算回归 | agent-1020-root | 在用 | 不可删除 | 最新70项纯Mock/临时文件通过，无数据库连接 | 2026-10-20/T-root14 |
| .local/platform/、.local/desktop-access.enc、.local/desktop-access.key | 平台自有SQLite及加密交付账号/解密密钥 | 本机用户/root部署 | 在用 | 不可删除 | 新建本机数据，不包含企业旧数据，删除会丢失配置/账号 | 2026-10-20/T-root14 |
| .local/native_windows/sources/openwebui/build/、.local/native_windows/openwebui/frontend-artifact.json | 原生完整前端及制品摘要收据，后端FRONTEND_BUILD_DIR引用 | 本机用户/root部署 | 在用 | 不可删除 | 原生登录与对话真实依赖；原项目产物不提交 | 2026-10-20/T-root14 |

## 全项目剩余计划与状态口径（agent-1020-root，2026-10-20）
| 项目相对路径 | 用途/关键引用与依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|
| AGENTS.md | 当前实施协作入口，部署条款引用R31/R32及Dify兼容问题 | agent-1020-root | 在用 | 不可删除 | 纠正旧R25最新路线，供后续执行遵循 | 2026-10-20/T-root15 |
| 项目文档/README.md | 四份文档总入口、当前部署与六批交付摘要 | agent-1020-root（已交接后维护） | 在用 | 不可删除 | 链接03及125项验收；设计范围保留 | 2026-10-20/T-root15 |
| 项目文档/03_开发计划与验收.md | A—F交付、17模块缺口、8项近期任务、三业务链及企业验收 | agent-1020-root（已交接后维护） | 在用 | 不可删除 | 与现有源码/本机证据核对，旧估算明确历史口径 | 2026-10-20/T-root15 |
| 项目文档/contracts/实施验收.csv | 125项真实状态与主要后续批次；引用范围基线稳定ID | agent-1020-root | 在用 | 不可删除 | 全部ID与基线一致，人工证据表不由build_docs覆盖 | 2026-10-20/T-root15 |

## R34本轮制造业务、报表与WSL增量索引（agent-1020-root，2026-10-21）
| 项目相对路径 | 用途/关键引用与依赖 | 归属 | 是否仍需 | 能否删除 | 依据/前置条件 | 核查日期/任务 |
|---|---|---|---|---|---|---|
| services/platform/agent_platform/modules/manufacturing/quality_lifecycle.py、quality_statistics.py、categories.py、workflow.py | 复检/不合格品整改与独立复核、首检复检分母、分类绑定、状态流转；由制造router调用 | agent-1020-domain | 在用 | 不可删除 | 新表模型与实际API/UI闭环依赖；原运行不可覆盖 | 2026-10-21/T-domain12,13 |
| services/platform/agent_platform/modules/manufacturing/maintenance_work.py | 预警/分析派工、维修、新证据独立复核；依赖制造模型、数据权限、Task | agent-1020-domain | 在用 | 不可删除 | 业务校验测试与真实待复核UI已过，完整UI维修闭环仍待验 | 2026-10-21/T-domain12,13 |
| services/platform/agent_platform/modules/manufacturing/energy/ | 冻结输入与来源的能耗分析、产量加权同工况比较、历史基准节能空间；DataService/Task | agent-1020-domain | 在用 | 不可删除 | 当前T-domain14实现/测试中，不宣称企业节能效果 | 2026-10-21/T-domain14 |
| apps/web/src/modules/manufacturing/QualityCategoryWorkspace.vue、QualityCasePanel.vue、ReinspectionPanel.vue、QualityStatistics.vue | 分类、整改、复检表单与结果统计；接制造真实API，既有InspectionWorkspace/Detail调用 | agent-1020-domain | 在用 | 不可删除 | 本轮浏览器实际整改→复检→第二人关闭通过，首检50%/复检100%分开显示 | 2026-10-21/T-domain12,13 |
| apps/web/src/modules/manufacturing/MaintenanceWorkOrderWorkspace.vue、MaintenanceWorkOrderDetail.vue、WorkOrderCreateForm.vue、WorkflowActionForm.vue、WorkflowStatus.vue | 维修列表/详情/派工与共用动作状态；维护/预警入口调用 | agent-1020-domain | 在用 | 不可删除 | 真实待复核工单UI通过；仍需完整维修效果UI验证 | 2026-10-21/T-domain12,13 |
| services/platform/migrations/versions/20261021_02_制造业务复检与维修闭环.py | 新增7张制造闭环表，承接20261020_01；无旧业务列更新 | agent-1020-root | 在用 | 不可删除 | 版本化生产升级链及保留旧设备/幂等验证依赖 | 2026-10-21/T-root16 |
| services/platform/migrations/versions/20261021_03_设备能耗分析.py | 新增能源分析快照表，承接02 | agent-1020-root | 在用 | 不可删除 | 5项迁移验证通过4.00秒；不能删除已发布版本链节点 | 2026-10-21/T-root17 |
| services/platform/migrations/versions/20261021_04_系统辅助配置.py | 新增系统辅助配置与修订表，承接03 | agent-1020-root | 在用 | 不可删除 | 按web13冻结模型生成，等待其注册后运行迁移验证 | 2026-10-21/T-root19 |
| services/platform/agent_platform/modules/workspace/quality_overview.py | 工作台最近30个上海自然日首检统计、来源权限/计划样本分母 | agent-1020-root | 在用 | 不可删除 | workspace/router引用；4项边界测试，终态Task不再算待办 | 2026-10-21/T-root16 |
| services/platform/agent_platform/modules/integrations/business_tools.py | 按模块权限导出15个既有业务读取API的OpenAPI，不自动取企业库 | agent-1020-root | 在用 | 不可删除 | integrations/router引用，2契约测试；真实原生导入待联调 | 2026-10-21/T-root16 |
| apps/web/src/modules/native/NativeWorkspace.vue、services/platform/agent_platform/modules/integrations/service.py | 普通员工按目标权限进入原生引擎，管理连接与日常入口权限拆开 | agent-1020-root | 在用 | 不可删除 | 33项局部回归通过；不向普通入口返回密钥配置 | 2026-10-21/T-root16 |
| apps/web/src/modules/admin/ServiceIdentityWorkspace.vue、PermissionSelector.vue、permissionScopes.ts | 服务身份CRUD/授权矩阵/短期令牌、撤销与清空；RoleWorkspace局部tab接入 | agent-1020-web | 在用 | 不可删除 | 实际UI签发关闭撤销停用通过；复制只验内存替身，不宣称系统剪贴板实写 | 2026-10-21/T-web11,12 |
| services/platform/agent_platform/modules/reports/presentation.py | 服务端按真实数据生成排名/进度/表格等可信展示模型；service/rendering/exports调用 | agent-1020-web | 在用 | 不可删除 | 20项新测试；缺字段阻止发布，金额/精度不交给客户端随意造值 | 2026-10-21/T-web10 |
| apps/web/src/modules/reports/CanvasProperties.vue、DisplayProperties.vue、RichTextEditor.vue、TemplateDataBindings.vue | 画布/组件属性、富文本编辑、模板字段映射；Studio/Properties/Workspace引用 | agent-1020-web | 在用 | 不可删除 | 前端build已过，真实页面逐步验收中 | 2026-10-21/T-web10,12 |
| apps/web/src/modules/reports/presentation.ts、specialChartOption.ts、reused/DataPresentationWidget.vue | 保留来源类型语义的新增26类组件展示；JimuWidgetRenderer调用 | agent-1020-web | 在用 | 不可删除 | 326源条目已有254可渲染，其余72没有伪启用；4项Node检查通过 | 2026-10-21/T-web10 |
| apps/web/tests/manufacturing-lifecycle.mjs、platform-enhancements.mjs | 新本机SQLite/CSV的单浏览器验收：制造闭环、身份/报告；系统Chrome | domain/web原作者，root当前验收脚本 | 在用 | 不可删除 | 前者85HTTP/4查询/13.8秒通过；后者脚本定位问题修正中，失败收据保留 | 2026-10-21/T-domain13/T-web12/T-root18 |
| services/platform/tests/test_manufacturing_lifecycle.py、test_reports_presentation.py、test_workspace_quality.py、test_business_tools.py | 制造状态/可信展示/首检统计/工具契约边界回归 | 原作者domain/web/root | 在用 | 不可删除 | 已执行相关小范围临时本机/纯契约验证，禁止替代企业效果验收 | 2026-10-21/T-root16/T-domain12/T-web10 |
| services/platform/agent_platform/modules/operations/support_models.py | 系统辅助配置与修订冻结模型，04迁移依赖 | agent-1020-web | 在用 | 不可删除 | 字典/参数/公告/表单辅助T-web13实现中 | 2026-10-21/T-web13 |
| deploy/native/wsl.py、wsl-bootstrap.sh、wsl_setup.py、wsl_sources.py、wsl_oci.py | WSL桥接、基础安装、原锁准备、来源包核对和官方预构建前端提取 | agent-1020-native | 在用 | 不可删除 | 当前专属Ubuntu实际准备依赖；storage过滤缺陷已定位定向修复中，失败历史保留 | 2026-10-21/T-native12 |
| deploy/native/wsl_runtime.py、wsl_process.py、wsl_cgroups.py、wsl_network.py、wsl_sandbox_entry.py | Dify专属进程/数据、cgroup归属回收、任务网络和沙箱装配 | agent-1020-native | 在用 | 不可删除 | 作者静审/Mock中；真实完整启动/隔离/流程尚未验，不作已交付声明 | 2026-10-21/T-native12 |
| deploy/native/wsl-README.md、services/platform/tests/test_wsl_runtime.py、test_wsl_sources.py、test_wsl_oci.py | WSL维护说明及来源/状态/归属离线验证；实际动态证据另存 | agent-1020-native | 在用 | 不可删除 | 最新52纯测试通过；不等于原生业务通过 | 2026-10-21/T-native12 |
| .local/acceptance/r34-20261021/、.local/platform-backups/r34-before-20261021.sqlite3 | 本轮独立18080临时验收库/加密账号与8080升级前只读备份 | agent-1020-root | 在用 | 不可删除 | 验收与恢复证据，本轮仍使用；均Git忽略，不与原业务源混用 | 2026-10-21/T-root16,18 |
| .local/native-linux-artifacts/、.local/wsl-native12-source-audit.json | 官方前端层/源码归档/摘要、已审uv生成文件精确核对清单 | root/native部署 | 在用 | 不可删除 | 实际WSL准备依赖；确认安装完成和恢复方式前不清理，不提交 | 2026-10-21/T-native12 |
- 仓库外 D:/WSL/ai-agent-company 及对应Linux /opt/ai-agent-company 是本项目独立发行版与运行根。已核Ubuntu24.04、Landlock ABI7、官方Qdrant摘要；当前没有业务PG初始化/模型验收。默认Ubuntu包服务已停止禁自启，不接管默认PG数据，不删除旧Docker路径。

## 2026-10-21 本轮聚合及交付索引补充（agent-1020-root）

以下相对仓库根；前文历史用途保持，现状以本次关联任务和后续追加为准。所有记录均经实际读取或对应作者交接核验。

| 路径 | 用途与关键依赖 | 归属 Agent | 是否仍需 | 能否删除 | 判断依据/前置条件 | 日期/任务 |
|---|---|---|---|---|---|---|
| deploy/native/wsl_lifetime.py、desktop.py、wsl.py | Windows统一启动与WSL精确PID前台生命周期；runtime与已启用Dify状态 | root | 在用 | 不可删除 | 实际14角色启动与复用已验，完整业务另验 | 2026-10-21/T-root23,27 |
| deploy/native/wsl_runtime.py、wsl_process.py、wsl_sandbox_entry.py | PG/Redis迁移顺序、netns监听、前端父进程归属、私有passwd与沙箱路径适配；cgroups/network | root（native原作者已交接） | 在用 | 不可删除 | 进程归属和纯测试已验，Python/JS执行仍待验 | 2026-10-21/T-root22/native14 |
| services/platform/tests/test_wsl_lifetime.py、test_wsl_sandbox_entry.py | 生命周期归属及私有UID映射回归；纯Mock/临时文件 | root | 在用 | 不可删除 | 用于防止误认领/宿主UID冲突，不接数据库 | 2026-10-21/T-root23,22 |
| tools/verify_dify_native.py、apps/web/tests/dify-wsl.mjs、services/platform/tests/test_dify_native_verifier.py、apps/web/tests/dify-wsl.test.mjs | 原生setup/login/草稿/真实编辑运行验收，加密派发记录与受审续作 | native原作者；root接管 | 在用 | 不可删除 | 27Python+4Node纯测试，初始化10HTTP已通过，浏览器尚待验 | 2026-10-21/T-native13,17 |
| services/platform/agent_platform/modules/integrations/router.py、apps/web/src/modules/native/ | 应用凭据/Schema、按引擎入口；知识导入/状态/检索引用与原件保护 | web/native原作者；root接管 | 在用 | 不可删除 | 60前端断言/7SFC及5临时库回归已过，真实索引未验 | 2026-10-21/T-web16/native15 |
| apps/web/tests/native-integration-ui.mjs、services/platform/tests/test_knowledge_file_references.py | 原生目标/绑定Schema和文件删除引用回归 | web/native原作者；root接管 | 在用 | 不可删除 | 后续防回归依赖；不代表真实Dify调用 | 2026-10-21/T-web16/native15 |
| services/platform/agent_platform/modules/workspace/activity.py、quality_overview.py、apps/web/src/modules/home/Workbench.vue、Announcements.vue | 当前权限内中文动态、首检趋势与已发布公告；Task/目录/辅助配置 | domain/root | 在用 | 不可删除 | 15项临时库回归、公告真实UI已过；最新动态UI待验 | 2026-10-21/T-domain18/root20 |
| services/platform/tests/test_workspace_activity.py、test_workspace_quality.py | 有界动态查询/失权/首检趋势授权回归 | domain原作者；root接管 | 在用 | 不可删除 | 9+6项通过；旧质量夹具已补真实Task与固定标准 | 2026-10-21/T-domain18 |
| services/platform/agent_platform/modules/operations/、apps/web/src/modules/operations/ | 字典、参数版本、公告、表单配置与静态代码导出；04迁移 | web原作者；root接管 | 在用 | 不可删除 | 真实UI70HTTP无业务源查询通过，源HTML按纯文本 | 2026-10-21/T-web13,14/root25 |
| services/platform/agent_platform/modules/identity/client_certificate_models.py、client_certificate_crypto.py、client_certificate_service.py、client_certificate_router.py、client-certificate-README.md | 客户端公钥/轮换/撤销与标准JWT客户端认证；service_accounts、PyJWT、05迁移 | domain原作者；root接管 | 在用 | 不可删除 | 57独立用例和真实UI37HTTP通过；非mTLS或完整OAuth服务 | 2026-10-21/T-domain15,16/root21,24 |
| apps/web/src/modules/admin/ClientCertificateForm.vue、ClientCertificateWorkspace.vue、ServiceIdentityWorkspace.vue、PermissionSelector.vue、permissionScopes.ts | 客户端与服务身份管理；权限选择和短令牌 | domain/web原作者；root接管 | 在用 | 不可删除 | 真实登记/轮换/撤权UI及权限回归已验 | 2026-10-21/T-domain15,16/web11 |
| services/platform/migrations/versions/20261021_05_客户端证书接入.py | 3张证书/接入表与6索引，承接04；客户端模型 | root | 在用 | 不可删除 | 5项迁移验证通过，旧设备保留；不得删除已发布迁移 | 2026-10-21/T-root24 |
| tools/verify_client_certificates_ui.py、apps/web/tests/client-certificates.mjs、services/platform/tests/test_client_certificates.py | 临时本机服务身份+内存私钥实际UI/令牌与失权验收 | domain原作者；root接管 | 在用 | 不可删除 | 实际37HTTP/0业务源/0模型，失败静态白名单历史保留 | 2026-10-21/T-domain15,16 |
| services/platform/agent_platform/main.py、services/platform/tests/test_health_navigation.py | /health浏览器SPA与JSON探针内容协商，Vary Accept | root | 在用 | 不可删除 | 5无数据库HTTP测试及系统页真实UI通过 | 2026-10-21/T-root25 |
| services/platform/agent_platform/modules/manufacturing/energy/、apps/web/src/modules/manufacturing/EnergyDetail.vue、EnergyForm.vue、EnergyWorkspace.vue | 同工况能耗/基准/价格/冻结证据及CSV；DataService和03迁移 | domain原作者；root接管 | 在用 | 不可删除 | 实际2新CSV查询：20%变化、40kWh/40元计算正确；非企业节能证明 | 2026-10-21/T-domain14/web14 |
| apps/web/tests/energy-support.mjs、services/platform/tests/test_manufacturing_energy.py、test_operations_support.py | 能耗与辅助配置纯测试/真实UI验收 | domain/web原作者；root接管 | 在用 | 不可删除 | 两阶段85HTTP/2查询通过；首次health路由失败保留 | 2026-10-21/T-domain14/web13,14/root25 |
| 项目文档/contracts/platform.openapi.json | 由tools/export_api.py从代码导出的396操作/273路径契约 | root | 在用 | 不可删除 | 实际代码联调依赖，设计基线仍227操作，数量不代表验收 | 2026-10-21/T-root27 |
| .local/dify-native-verification/、.local/dify-delivery-connection.json | Dify加密账号/请求与派发历史、入口配置收据 | root | 在用 | 不可删除 | 在用本机证据与交付凭据，Git忽略；不能随意删除 | 2026-10-21/T-native13,17/root27 |
| .local/platform-backups/ | 本项目8080更新前只读SQLite备份；本轮多份独立时间戳 | root | 在用 | 不可删除 | 恢复所需，Git忽略；未开展备份清理 | 2026-10-21/T-root16,27 |

- 仓库外专属发行版当前已完成新PG迁移、原生初始化，14服务均运行；覆盖前文“尚未初始化”的当时状态。D:/WSL/ai-agent-company及Linux运行根属于当前部署数据，不触发任何删除。

| deploy/native/wsl_sandbox_patch.py、services/platform/tests/test_wsl_sandbox_patch.py | 固定来源JS路径单行补丁与幂等/未知源/并发修改回归；wsl_setup编译前调用 | domain作者；root接管 | 在用 | 不可删除 | 5通过1软链权限跳过；实际WSL制品构建及单code重启已过，保持原seccomp | 2026-10-21/T-domain20/root22 |
| README.md、deploy/native/README.md、deploy/native/wsl-README.md | 三套服务实际入口、账号和当前部署；旧失败统一链接共享历史 | root | 在用 | 不可删除 | 已移除把旧Docker/Windows Dify候选当当前入口的过期说明 | 2026-10-21/T-root27 |
| 项目文档/README.md、03_开发计划与验收.md、contracts/实施验收.csv | 0.5版当前证据与125稳定范围逐项差距；引用实际API快照 | web作者；root接管 | 在用 | 不可删除 | 125唯一ID/顺序/9列/本地链接校验通过；企业及原生未验内容明确保留 | 2026-10-21/T-web17 |

## 原生业务联调与行权限增量（agent-1020-root，2026-10-21）

| 项目相对路径 | 用途及关键引用/依赖 | 归属 Agent | 是否仍需 | 能否删除 | 判断依据/前置条件 | 核查日期及任务 |
|---|---|---|---|---|---|---|
| deploy/native/wsl_sandbox_patch.py、wsl_setup.py、wsl_runtime.py | 官方沙箱来源校验、Python stat默认表补充；独立Go1.24/Node20.11.1与Node24前端并存 | domain/native作者；root当前接管Node范围 | 在用 | 不可删除 | 纠正此前JS路径索引结论：官方双层路径正确，已恢复；Python/JS实际执行成功；所有旧收据保留 | 2026-10-21/T-domain25/root32 |
| services/platform/tests/test_wsl_sandbox_node.py | 独立Node来源/已改二进制/部分安装拒绝回归 | root | 在用 | 不可删除 | 4纯文件检查及114runtime回归通过 | 2026-10-21/T-root32 |
| deploy/native/wsl_http_bridge.py | WSL精确IP与路由到Windows所属OWUI/平台，启动快照绑定上游PID | native作者；root接管 | 在用 | 不可删除 | 模型列表与Dify模型配置实际成功；工具认证真实配置仍在实施 | 2026-10-21/T-native24/27 |
| deploy/native/wsl_tool_credentials.py、services/platform/tests/test_wsl_tool_credentials.py | 独立引擎证书材料密文、RFC7523短token、失败预约/撤销/轮换；依赖runtime.key及平台证书API | native作者；root接管 | 在用 | 不可删除 | 25凭据纯测试，首次认证须固定token与me核工作区，实际原生工具待验 | 2026-10-21/T-native27 |
| services/platform/agent_platform/modules/data/row_policies.py、policy_sql.py | 结构化行策略、物理表AST过滤和历史有效范围指纹；依赖DataService及SQLGlot | domain | 在用 | 不可删除 | 新tmp本机权限回归正在收口；不得移除后使受限查询开放 | 2026-10-21/T-domain26 |
| services/platform/migrations/versions/20261021_06_数据行权限与查询范围.py | 行策略与查询访问快照两张新表迁移 | domain | 待核实 | 暂不可删除（待核实） | 此具体文件名需最终与作者交接核准；迁移head06已通过新tmp升级检查，生产未升级 | 2026-10-21/T-domain26 |
| apps/web/src/modules/data/DataPermissionWorkspace.vue、RowPolicyPanel.vue、RowPolicyRuleForm.vue、RowPolicyConditionForm.vue、rowPolicy.ts、useRowPolicyEditor.ts | 数据权限现有页面行权限tab及字段校验；依赖row-policy API | web作者；root接管 | 在用 | 不可删除 | 81纯断言/4SFC与类型检查通过，真实页面待验 | 2026-10-21/T-web23 |
| services/platform/agent_platform/core/task_evidence.py、tasks.py、modules/workspace/router.py | 从不可变运行核历史任务来源权限，先过滤再分页/统计/通知 | root | 在用 | 不可删除 | 20初轮通过；领域403兼容修复后12定向含迁移通过 | 2026-10-21/T-root31 |
| services/platform/agent_platform/modules/reports/source_presentations.py、rendering.py、capabilities.py | 12来源组件精确能力与真实HTML导出，深色文字/月历明细/卡片布局修复 | domain/web作者；root接管 | 在用 | 不可删除 | 266/326来源项；3修复截图root已目检，保持剩60未完成口径 | 2026-10-21/T-domain21/web22 |
| tools/verify_knowledge_ui.py、apps/web/tests/knowledge-native.mjs、tests/helpers/knowledgeDownloadEvidence.mjs | 知识导入/检索/引用/真实下载验收；下载事件与route去重 | web作者；root接管 | 在用 | 不可删除 | 实际7业务检查通过，原计数失败不改写；独立review关联SHA | 2026-10-21/T-web22/root29 |
| tools/verify_dify_native.py、apps/web/tests/dify-wsl.mjs | 原生代码节点UI、SSE输出、归属与不重复派发账本 | native/root | 在用 | 不可删除 | Python/JS实际成功；大小写定位误报修复，旧收据及截图不删除 | 2026-10-21/T-root22/32 |
| .local/native_windows/、.local/dify-native-verification/、.local/acceptance/r34-20261021/ | 本机运行归属、加密账号/凭据、独立本机验收库与失败/成功证据 | root | 在用 | 不可删除 | 不提交Git；不是可按temp命名清理的缓存，保留回查/恢复依据 | 2026-10-21/T-root27–33 |

- 索引纠正（agent-1020-root，T-root31）：上表迁移文件名未经核实，现实际目录核验为下列路径；上表含“数据行权限与查询范围”的路径不存在，不作为文件/删除判断依据。
| services/platform/migrations/versions/20261021_06_数据行级权限.py | 新增data_row_policies/data_query_access_snapshots；依赖05迁移，不改旧表 | domain作者 | 在用 | 不可删除 | 实际文件名已核验，新tmp升级与原数据保留测试通过；本机在用库待更新 | 2026-10-21/T-domain26/root31 |

| tools/configure_native_business_tools.py、services/platform/tests/test_native_business_tools_configuration.py | 原生只读工具四阶段配置、独立证书与精确组授权、失败保留；依赖wsl_tool_credentials | native作者；root接管 | 在用 | 不可删除 | 15纯检查及实际26本机HTTP配置/探测通过，0模型/企业源 | 2026-10-21/T-native28/root34 |
| tools/verify_row_permissions_ui.py、apps/web/tests/row-permissions.mjs | 新CSV行权限UI与历史拒绝验收；复用18080隔离平台 | web作者；root接管 | 在用 | 不可删除 | 22HTTP/3CSV查询真实通过，截图已目检 | 2026-10-21/T-web24 |
| deploy/native/wsl.py、wsl_lifetime.py | WSL归属、生命周期及JSON元数据；系统stderr以bytes处理 | root | 在用 | 不可删除 | 56纯回归及真实工具配置无乱码异常，未改变进程权限 | 2026-10-21/T-root34 |

| deploy/native/wsl_bridge_startup.py、services/platform/tests/test_wsl_bridge_startup.py | desktop启动时重建所属工具代理精确规则、幂等复用与失败保留；复用runtime快照/解析/归属 | root | 在用 | 不可删除 | 6纯检查、1真实配置通过；0业务HTTP/模型，NAT原生URL仍待接入 | 2026-10-21/T-root35 |

| deploy/native/wsl_agent_patch.py、services/platform/tests/test_wsl_agent_patch.py | 官方V2四文件精确摘要补丁，沿原路径配置请求次数，默认500；setup_python调用 | native作者；root接管 | 在用 | 不可删除 | 20纯回归通过，已在所属Agent包应用；实际V2 cwd问题另记I-root43 | 2026-10-21/T-native30/root37 |
| services/platform/agent_platform/modules/data/values.py、processing.py、quality.py、metrics.py | 精提Test5 Decimal、8步处理/7类质量/固定指标；DataService授权快照 | domain作者；root接管 | 在用 | 不可删除 | 34数据联合回归通过，0实际企业源；UI待验 | 2026-10-21/T-domain27 |
| apps/web/src/modules/data/dataPreparation.ts、ProcessingWorkspace.vue、DataQualityWorkspace.vue、BusinessMetricWorkspace.vue及相关表单组件 | 处理/质量/指标三个既有入口，消费typed DTO，结构化控件与真实导出 | web作者；root接管 | 在用 | 不可删除 | 139纯断言/14SFC/2580模块正式build通过，真实UI待验 | 2026-10-21/T-web25 |
| services/platform/tests/test_data_transformations.py、apps/web/tests/data-preparation-controls.mjs | 数据处理/质量/指标行为与表单纯回归 | domain/web作者；root接管 | 在用 | 不可删除 | 新tmp CSV/SQLite与纯前端，不替代企业验收 | 2026-10-21/T-domain27/web25 |
| 项目文档/contracts/platform.openapi.json | 从当前代码导出403操作/278路径；设计227操作仍独立 | root | 在用 | 不可删除 | tools/export_api.py实际导出，API数不代表全部功能完成 | 2026-10-21/T-root38 |

| services/platform/agent_platform/core/local_mysql.py、core/settings.py、modules/data/connectors.py | 部署证明核本机mysqld进程/监听/pid文件，连接后再核实例；只开放用户两库，仍需目标批准 | agent-1021-root | 在用 | 不可删除 | 新14项纯检查通过；部署证明位于.local，不含密码 | 2026-10-21/T-root41 |
| tools/demo_data/platform.py、apps/web/src/modules/manufacturing/MaintenancePresetPicker.vue | 导入后按正式API登记数据源/设备/测点；维护表单选择监测方案自动映射 | agent-1021-root | 在用 | 不可删除 | 装配2项DTO/SQL纯验通过，实际注册待数据导入 | 2026-10-21/T-root41/42 |
| services/platform/migrations/versions/20261021_07_融合对话知识与流程.py、modules/knowledge/evidence.py | 融合六表迁移及不可变知识来源/撤权检查 | agent-1020-native | 在用 | 不可删除 | 作者29项纯回归通过，实际迁移待root | 2026-10-21/T-native33 |

| tools/demo_data/package.py、import_mysql.py、recover_mysql.py | 原包完整映射/批次事务导入/明确受审恢复；复用本机实例证明，依赖可选PyArrow | agent-1020-domain，root接管 | 在用 | 不可删除 | 11导入+15恢复纯检查；首次900秒停止保留1483000行，恢复待授权 | 2026-10-21/T-domain30/32 |
| tools/demo_data/quality.py、fixtures/食品产品质检.csv、apps/web/public/demo/quality/ | 16个合成食品三维质检样本、独立新表与5种外观示意；映射正式API | agent-1020-domain，root接管 | 在用 | 不可删除 | 12纯验证通过；不是企业真实样本，实际导入待恢复许可 | 2026-10-21/T-domain31 |
| tools/demo_data/platform.py、verify_business.py、requirements.txt | 导入收据校验、正式API资源登记、三个演示流程；PyArrow为可选导入依赖 | agent-1021-root | 在用 | 不可删除 | 2装配DTO纯检查；真实注册/业务验收尚未执行，不可标完成 | 2026-10-21/T-root41/44 |
| services/platform/agent_platform/modules/flows/execution.py、core/bounded_http.py | 运行预约与绝对HTTP预算；逐步commit、unknown与同键去重 | agent-1020-native | 在用 | 不可删除 | 82联合纯回归及7本机无模型API验收通过 | 2026-10-21/T-native34/root43 |
| services/platform/migrations/versions/20261021_08_流程运行预约与幂等.py | 流程运行快照/请求编号与任务绑定迁移，依赖07 | agent-1020-native | 在用 | 不可删除 | 实际备份→升级→完整结构比较通过 | 2026-10-21/T-native34/root43 |
| apps/web/src/modules/data/PreparationHistory.vue、PreparationQuerySelect.vue、usePreparationHistory.ts | 处理/指标历史与真实CSV；服务端查询分页、独立已选详情 | agent-1020-web | 在用 | 不可删除 | 83新断言/139既有断言通过；7业务SFC接入，实图待验 | 2026-10-21/T-web28/29 |
| apps/web/src/modules/chat/useScopedChatSessions.ts、useChatConversation.ts、ChatHistory.vue | 会话用户/工作区隔离、流迟到保护、本机历史管理 | agent-1020-web | 在用 | 不可删除 | 71纯断言通过；记忆管理另任务在做 | 2026-10-21/T-web27 |
| .local/mysql-demo/、.local/backups/fusion07-20261021.sqlite3、.local/backups/fusion08-20261021.sqlite3 | 本机实例证明、导入失败/停连证据、候选恢复计划和两次平台更新备份 | agent-1021-root | 在用 | 不可删除 | 归属与恢复依赖，Git忽略；不能因临时命名清理 | 2026-10-21/T-root39–44 |

| services/platform/agent_platform/modules/chat/memory_models.py、memories.py、memory_router.py | 个人记忆所属过滤/CRUD/搜索/CAS，适配OWUI手工记忆，router包含 | agent-1020-domain | 在用 | 不可删除 | 26纯回归及7真实本机管理API成功，默认无自动注入 | 2026-10-21/T-domain33/root43 |
| services/platform/migrations/versions/20261021_09_个人记忆.py | chat_user_memories独立表，依赖08 | agent-1020-domain | 在用 | 不可删除 | 09实际备份升级与结构比较通过，5完整迁移回归通过 | 2026-10-21/T-domain33/root43 |
| apps/web/src/modules/chat/MemoryWorkspace.vue、MemoryList.vue、MemoryEditor.vue、useMemories.ts | 当前用户工作区记忆管理与冲突/未知写入处理 | agent-1020-web | 在用 | 不可删除 | 96纯断言/71聊天回归、正式build和新增表单真实截图通过 | 2026-10-21/T-web30/root45 |
| apps/web/src/components/layout/NavigationSearch.vue、AppShell.vue | 按当前权限搜索功能入口、单系统外壳；删除冗余标题/无作用搜索 | agent-1021-root | 在用 | 不可删除 | 正式build及真实搜索跳转记忆页面成功 | 2026-10-21/T-root46 |
| tools/demo_data/requirements.txt | PyArrow23.0.1可选Parquet导入依赖 | agent-1021-root | 在用 | 不可删除 | 本机实际版本已核，不添加到日常平台启动依赖 | 2026-10-21/T-root44 |

| services/platform/agent_platform/core/datetimes.py、tests/test_utc_storage.py | SQLite已知UTC存储转带时区响应，flows/agents/knowledge共用 | agent-1021-root | 在用 | 不可删除 | 2纯回归通过；不转换原设备数据 | 2026-10-21/T-root47 |
| services/workflow_runtime/worker.py、README.md、requirements.txt、licenses/Graphon-LICENSE.txt | Graphon0.8原执行引擎独立Python3.13协议；依赖专属WSL与原Apache许可 | agent-1020-native；许可root | 在用 | 不可删除 | 37项纯图/协议已过；平台接入仍T37进行中 | 2026-10-21/T-native36/37 |

| services/platform/agent_platform/modules/knowledge/extractor.py、ingestion.py、file_sources.py、service.py、router.py、models.py、evidence.py | Test5解析提取、原件保护/定位、摄取预约及显式索引；依赖FileService和bge-m3 | agent-1020-domain | 在用 | 不可删除 | 29新+24旧纯检查，7真实API/2向量及27浏览器请求/1向量通过 | 2026-10-21/T-domain34/root48 |
| services/platform/migrations/versions/20261021_10_知识原件与来源定位.py | 文档4列与分块1列可空来源扩展，依赖09 | agent-1020-domain | 在用 | 不可删除 | 5全迁移纯回归及实际备份升级/结构比较通过 | 2026-10-21/T-domain34/root43 |
| apps/web/src/modules/knowledge/、apps/web/tests/knowledge-documents.mjs、request-timeouts.mjs | 知识空间/三种导入/分块详情/授权下载/检索、身份切换与可选等待时间 | agent-1020-web | 在用 | 不可删除 | 112+27纯断言/类型/正式build及真实上传下载截图通过 | 2026-10-21/T-web31/root48 |
| .local/mysql-demo/ui-knowledge10/、platform-knowledge10.json、platform-upgrade10.json、.local/backups/fusion10-20261021.sqlite3 | 本轮知识实际证据与平台升级备份，Git忽略 | agent-1021-root | 在用 | 不可删除 | 恢复及验收追溯依赖；路径在mysql-demo下不代表执行过MySQL查询 | 2026-10-21/T-root43/48 |

| services/platform/agent_platform/modules/flows/graph_runtime.py、tests/test_workflow_graph.py | 原生图输入/事件/双向任务绑定与纯回归；依赖Graphon worker及TaskEvent | agent-1020-native | 在用 | 不可删除 | 39图回归与2所属WSL纯图通过，平台实际调用待T-root50 | 2026-10-21/T-native37 |
| services/platform/agent_platform/core/embedding_vectors.py、tests/test_embedding_vectors.py、test_knowledge_text_ingestion.py | 向量顺序/有限数值/维度与稳定余弦、贴文摄取回归 | agent-1020-domain | 在用 | 不可删除 | 55新+70旧纯回归通过，实际贴文待部署 | 2026-10-21/T-domain35 |
| deploy/native/desktop.py、启动系统.cmd、services/platform/tests/test_desktop_workspace.py | 单8080日常入口及已配置纯图keeper启动，不启动14角色 | agent-1021-root | 在用 | 不可删除 | 6项纯检查通过，真实复用待验 | 2026-10-21/T-root51 |

| .local/mysql-demo/platform-update-graph.json、platform-graph-runtime.json、platform-paste-index.json、platform-workspace-start.json、.local/backups/fusion-graph-20261021.sqlite3 | 真实图运行/知识贴文/启动复用证据和更新备份；均Git忽略 | agent-1021-root | 在用 | 不可删除 | 单次有界实际验收通过，恢复/追溯需保留，未涉及MySQL续导 | 2026-10-21/T-root43/50/51 |

| apps/web/src/modules/flows/GraphWorkflowRunner.vue、GraphInputForm.vue、SchemaInput.vue、WorkflowJsonValue.vue、WorkflowRunEvents.vue及同目录graphInputs/workflowEvents/useWorkflowEvents/useWorkflowRequests | 结构化图运行/原JSON结果/真实事件分页；依赖native37 DTO | agent-1020-web | 在用 | 不可删除 | 117新断言/类型检查通过，实际UI待T-root52；不等于完整编辑器 | 2026-10-21/T-web33 |

| .local/mysql-demo/ui-graph-runtime.json、ui-graph-runtime-read.json、ui-graph-runtime-read/ | 真实图UI首轮定位失败及只读核验/截图，保留原已成功run | agent-1021-root | 在用 | 不可删除 | 未重复派发，30只读/登录请求通过，0模型业务库 | 2026-10-21/T-root52 |

| services/platform/agent_platform/modules/integrations/delegated_contract.py、delegation_auth.py、delegated_sources.py、delegated_tools.py、delegated_router.py、delegation_config.py及schemas.py | 原生签名用户委托15固定工具/当前人员权限/不可变来源/正式配置 | agent-1020-domain | 在用 | 不可删除 | 57新增纯回归分批通过，未真实授权或部署 | 2026-10-21/T-domain37 |
| services/platform/agent_platform/core/task_evidence.py、modules/integrations/service.py、tests/test_native_chat_public_guards.py | 聊天公共任务私有来源守卫与凭据删除引用；依赖native38接口 | agent-1021-root | 在用 | 不可删除 | 12新+40旧纯回归通过，尚未部署11 | 2026-10-21/T-root55 |

| deploy/native/desktop.py、services/platform/tests/test_desktop_workspace.py、.local/mysql-demo/platform-workspace-chat-start.json | 日常入口复用平台/本机聊天/keeper及归属证据，Windows服务原启动预算不变 | agent-1021-root | 在用 | 不可删除 | 10纯检查与实际现有进程复用通过，冷启动未验；收据Git忽略 | 2026-10-21/T-root60 |
| packages/dify-workflow/、apps/web/src/modules/flows/GraphWorkflowEditor.vue、apps/web/tests/workflow-editor.mjs | 原Dify3节点及条件函数的React编辑岛、Vue显式创建/CAS保存、隔离样式和来源清单 | agent-1020-web，root接管 | 在用 | 不可删除 | 71+47纯断言和2895模块正式build通过；实际画布待T-root61，完整节点未覆盖 | 2026-10-21/T-web34 |

| .local/mysql-demo/ui-workflow-canvas.json、ui-workflow-canvas/ | 原3节点编辑器首轮实际创建/更新/重开证据与截图，Git忽略 | agent-1021-root | 在用 | 不可删除 | 37请求通过，恢复/验收追溯依赖，原全节点仍有缺口 | 2026-10-21/T-root61 |

| services/platform/agent_platform/modules/chat/native_file_models.py、native_file_transport.py、native_files.py、native_file_router.py、tests/test_native_chat_files.py | 原生聊天附件预约/唯一上传/显式刷新、来源授权与副本隔离；复用FileService及原OWUI文件API | agent-1020-domain | 在用 | 不可删除 | 21纯检查通过，实际未部署；历史投影T-domain40继续窄改 | 2026-10-21/T-domain38/40 |
| services/platform/migrations/versions/20261021_12_原生聊天附件.py | native_chat_files独立表，依赖11；版本与意图/generation约束 | agent-1020-domain，root接管 | 在用 | 不可删除 | 单表upgrade/metadata/down通过，完整迁移及在用平台部署待root63 | 2026-10-21/T-domain38 |

| services/platform/agent_platform/modules/chat/native_models.py、native_sources.py、native_service.py、native_transport.py、native_router.py、native_turns.py、native_tasks.py、native_socket.py、native_execution.py、native_events.py | 原生普通身份/会话树/Turn预约/HTTP与Socket/准确终态和停止、Task来源，正文存原OWUI | agent-1020-native，root接管 | 在用 | 不可删除 | 71联合纯检查分批通过，实际部署验收T-root63/65待执行；工具及编辑分支仍缺 | 2026-10-21/T-native38 |
| services/platform/migrations/versions/20261021_11_原生聊天身份与运行.py、tests/test_native_chat_identity.py、test_native_chat_turns.py、requirements.txt、requirements.lock.txt | 4个映射/运行表与真实协议模拟、Socket依赖固定版本 | agent-1020-native，root接管 | 在用 | 不可删除 | 完整迁移5项、Socket安装/pip check通过；独立部署环境必需 | 2026-10-21/T-native38/root56/63 |

| tools/demo_data/platform.py、verify_business.py、services/platform/tests/test_demo_import_receipts.py | 计划与成功收据逐资产绑定、平台登记及三场景验收前置 | agent-1020-domain，root接管 | 在用 | 不可删除 | 34纯回归通过，真实续导待用户确认 | 2026-10-21/T-domain42 |
| services/platform/tests/test_native_chat_transport.py | h11真实头编码验证，防空Bearer登录失败 | agent-1021-root | 在用 | 不可删除 | 2纯回归0.21秒通过，0网络/数据库 | 2026-10-21/T-root68 |
| .local/mysql-demo/platform-update-native-chat.json、platform-native-chat-identity.json、platform-native-chat-attachment.json、native-chat-model-access.json | 12升级、原普通身份、附件下载和专属模型组实际证据，Git忽略 | agent-1021-root | 在用 | 不可删除 | 归属/授权/来源追溯依赖，未证明原生新对话回复成功 | 2026-10-21/T-root63/66/67 |

| packages/openwebui-chat/、apps/web/src/modules/chat/NativeChatWorkspace.vue、NativeChatIsland.vue及useNativeChat/Turn/Files/Transport | 原消息树及结构化输出Svelte同页复用，Vue持平台会话/来源/事件 | agent-1020-web | 在用 | 不可删除 | T36根check/build与112纯断言通过；真实UI发现滚动循环，T37修复中，尚未验可用 | 2026-10-21/T-web36/37 |
| packages/openwebui-chat/SOURCE_MANIFEST.json、licenses/、apps/web/REUSE.md | 固定原源码SHA/许可、完整组件与片段复用边界 | agent-1020-web | 在用 | 不可删除 | 构建不依赖原整仓；许可与来源追溯必需 | 2026-10-21/T-web36 |
| services/platform/agent_platform/modules/chat/native_access.py、modules/integrations/chat_model_config.py、tests/test_native_chat_model_access.py | 普通身份增量入专属组、管理员策略与并发CAS | agent-1020-native，root接管 | 在用 | 不可删除 | 28新纯检查通过，实际策略CAS/身份刷新/模型目录成功；上游回复429另记 | 2026-10-21/T-native40/root68 |

| services/platform/agent_platform/modules/integrations/delegated_data.py、delegated_knowledge.py及相关contract/tools/sources/router | 已保存查询及单空间知识工具，冻结行列范围/知识证据并受Turn时限 | agent-1020-domain，root接管 | 在用 | 不可删除 | 最终25新增及原委托/检索回归通过，生产工具调用未启用 | 2026-10-21/T-domain44 |
| services/platform/tests/test_delegated_data_knowledge.py | 查询分页/拒新SQL、知识原件撤权、等待中降权/取消/迟到与候选上限验证 | agent-1020-domain，root接管 | 在用 | 不可删除 | tmpSQLite/Mock通过，0实际业务源/模型 | 2026-10-21/T-domain44 |
| 项目文档/contracts/platform.openapi.json | 当前代码注册的468操作/325路径快照，与227操作设计契约分开 | agent-1021-root | 在用 | 不可删除 | tools/export_api.py导出并检查operationId唯一，通过 | 2026-10-21/T-root49 |

| packages/openwebui-chat/tests/mount-browser.mjs、apps/web/tests/native-island.mjs | 原组件真实微任务/挂载/卸载回归与宿主失败锁 | agent-1020-web，root接管 | 在用 | 不可删除 | 10纯断言及1次独立Chrome/0HTTP模型DB通过；对应根因已修 | 2026-10-21/T-web37 |
| .local/mysql-demo/ui-native-chat-ready-read.json、ui-native-chat-ready-read/ | 实际当前聊天两消息/模型/输入启用与刷新恢复截图，Git忽略 | agent-1021-root | 在用 | 不可删除 | 50只读/登录请求、0错误与新Turn，原回复是429失败，不能作为模型成功证据 | 2026-10-21/T-root69 |

| services/platform/agent_platform/modules/chat/native_branches.py、tests/test_native_chat_branches.py | 原生重新生成/编辑发送的追加计划、快照与协议/来源回归，依赖原Turn/Task及附件授权 | agent-1020-native，root接管 | 在用 | 不可删除 | 40新项及89既有纯回归通过，实际回复尚受上游429阻塞 | 2026-10-21/T-native43 |
| deploy/native/wsl_bridge_lifecycle.py、services/platform/tests/test_wsl_bridge_lifecycle.py | 已配置工具桥的日常快照更新、失败预约与精确launch停止，desktop日常入口调用 | agent-1021-root | 在用 | 不可删除 | 29生命周期/入口/代理纯回归通过，未进行真实重建 | 2026-10-21/T-root73 |

| deploy/native/owui_tool_forward_patch.py、services/platform/tests/test_owui_tool_forward_patch.py | OWUI0.12.3受管工具调用原签名函数的小补丁、SHA/CAS/环境pin及纯回归 | agent-1020-domain，root接管 | 在用 | 不可删除 | 27新纯回归通过，实际源码未应用；配合windows_services及独立密文key | 2026-10-21/T-domain46 |

| apps/web/src/modules/chat/nativeBranchDraft.ts、packages/openwebui-chat/src/components/MessageEditor.svelte、EditAttachments.svelte、apps/web/tests/native-branches.mjs | 原消息编辑/再生成、跨组件草稿与附件三态及准确保存确认，依赖T-native43 DTO | agent-1020-web，root接管 | 在用 | 不可删除 | 164纯断言/构建/组件Chrome及本机只读编辑取消通过；未实际重派模型 | 2026-10-21/T-web38/root74 |
| .local/mysql-demo/platform-update-native-branches.json、platform-workspace-bridge-rebind.json、ui-native-chat-branches-ready.json及截图、.local/backups/fusion-native-branches-20261021.sqlite3 | 分支部署、工具桥重绑定与只读UI证据/恢复备份，Git忽略 | agent-1021-root | 在用 | 不可删除 | 实际单次检查通过，Go失败证据保留，非数据库业务验收 | 2026-10-21/T-root73/74 |
| services/platform/tests/test_manufacturing_review_completeness.py | 不合格遮住缺证据时的人工复核回归，依赖manufacturing/service.review | agent-1020-domain，root接管 | 在用 | 不可删除 | 8新与2原定向回归通过；实际服务尚未更新 | 2026-10-21/T-domain49 |
| services/platform/agent_platform/modules/integrations/delegated_budget.py、tests/test_delegated_budget.py | 工具读取前冻结额度CAS与提交、失败不退及并发/取消回归，依赖native_tool_policy | agent-1021-root | 在用 | 不可删除 | 19新和61旧委托回归通过，待完整B部署验收 | 2026-10-21/T-root75 |

| apps/web/src/modules/manufacturing/InspectionCreateForm.vue、InspectionDetail.vue、InspectionObservation.vue、inspectionPresentation.ts、apps/web/tests/inspection-presentation.mjs | 抽样计划、服务端数量/结论原因、缺陷明细与固定演示图预览，依赖m11DTO及public/demo/quality五图 | agent-1020-web，root接管 | 在用 | 不可删除 | 139纯回归/类型/正式build通过；一般企业图片原件尚无受控File关联 | 2026-10-21/T-web39 |

| services/platform/agent_platform/modules/manufacturing/assessment.py、modules/reports/inspection_source.py、tests/test_report_review_completeness.py | 共用缺证据判定及报告发布守卫，兼容完整证据与不合格报告 | agent-1021-root | 在用 | 不可删除 | 25相关纯回归通过，实际服务待统一更新 | 2026-10-21/T-root76 |
| .local/ui-quality-controls-read.json、.local/ui-quality-controls-read/ | 实际质检表单样本数与取消证据，Git忽略 | agent-1020-web，root接管 | 在用 | 不可删除 | 37请求无写入/模型业务SQL，当前0任务故外观细节未验 | 2026-10-21/T-web40 |

| services/platform/agent_platform/modules/chat/native_tool_policy.py、native_tool_budget.py、tests/test_native_chat_tool_budget.py | 专属工具别名策略、Turn冻结与最终请求签名，依赖独立Vault凭据及原生capability | agent-1020-native，root接管 | 在用 | 不可删除 | 36新增/97联合纯回归通过，无新迁移，生产未启用 | 2026-10-21/T-native45 |

| deploy/native/owui_chat_budget_patch.py、owui_chat_budget_runtime.py、tests/test_owui_chat_budget_patch.py | OWUI固定源码预算微调、独立账本/截止控制和33新增纯回归，windows_services读取非密配置+密文key | agent-1020-domain，root接管 | 在用 | 不可删除 | 33新+5旧及内存语法通过，T-root77实际补丁启动/capability已通过，模型调用尚未验 | 2026-10-21/T-domain48/root77 |
| .local/mysql-demo/platform-native-business-tool-policy.json、native-tool-budget-install.json、platform-workspace-budget-bridge.json、.local/backups/owui-tool-budget-20261021/ | 实际配置证据和原生源码/SQLite恢复备份，Git忽略 | agent-1021-root | 在用 | 不可删除 | 本机归属、19工具与alias读回通过；恢复/追溯所需，不含导出密钥 | 2026-10-21/T-root77 |

| services/workflow_runtime/worker.py、services/platform/tests/test_workflow_runtime.py、test_workflow_graph.py | 原Graphon启动入口与平台纯图保存/运行边界；初始化写选择器恰2段、读取nested仍支持 | agent-1020-native，root接管 | 在用 | 不可删除 | T47定向10项通过，真实本轮图未运行 | 2026-10-21/T-native47 |

| tools/demo_data/recover_mysql.py、services/platform/tests/test_demo_recovery_arrow_batches.py | 按原Parquet实际批次恢复、真实行哈希回读及预算守卫；Arrow字典小批次纯回归 | agent-1021-root / agent-1020-native | 在用 | 不可删除 | 原计划与账本不可重分批；T-root79旧15回归通过、新回归进行中，待实际续导 | 2026-10-21/T-root79/native49 |
| .local/mysql-demo/recovery-20261021-authorized.json、import-stop-check-batch-fix-20261021.json | 错误ceil批数首次受审恢复失败及连接10/13停止证据，Git忽略 | agent-1021-root | 在用 | 不可删除 | 0新业务写入，保留原失败过程与恢复审核链 | 2026-10-21/T-root79 |

| packages/dify-workflow/、apps/web/tests/workflow-editor.mjs | 原Dify节点/面板编辑岛，追加聚合分组/连通类型变量/字典选择；依赖SOURCE_MANIFEST及Vue宿主 | agent-1020-web，root接管 | 在用 | 不可删除 | T42的39文件已交接，190纯断言/独立tsc通过，原来源SHA21项；待根build与实际画布验收 | 2026-10-21/T-web42 |

| tools/demo_data/requirements.txt | 演示包PyArrow及pytz精确依赖，供原流时间值转换 | agent-1021-root | 在用 | 不可删除 | 实际缺pytz重复import热点已定位；只补单wheel，运行连接与期限不变 | 2026-10-21/T-root79/native53 |
| apps/web/src/modules/manufacturing/MaintenanceWorkspace.vue、maintenanceExport.ts、apps/web/tests/maintenance-export.mjs | 维护分组CSV导出UI与下载回收/权限边界，依赖现有exportsAPI | agent-1020-web，root接管 | 在用 | 不可删除 | T43的35纯检查/类型通过，真实下载待验，不是正式报告 | 2026-10-21/T-web43 |

| tools/demo_data/import_mysql.py、services/platform/tests/test_demo_import_diagnostics.py | 所有演示导入共用的连接前依赖检查、异常脱敏种类/位置、事务及预算；恢复/食品复用 | agent-1021-root | 在用 | 不可删除 | 27联合纯回归通过，原异常证据保留，当前T81实测进行中 | 2026-10-21/T-root80/root81 |
| services/platform/agent_platform/modules/manufacturing/maintenance_work.py、services/platform/tests/test_maintenance_analysis_verification_freshness.py、test_manufacturing_lifecycle.py | 维修复核的受控源及recent采样时间证据；依赖DataService存储查询，不重查业务SQL | agent-1020-domain，root接管 | 在用 | 不可删除 | T52的19纯回归通过，严格来源与时间防历史重算冒充修后效果；待部署 | 2026-10-21/T-domain52 |

| deploy/native/wsl_identity.py、wsl_runtime.py、wsl_lifetime.py、services/workflow_runtime/worker.py及对应4个测试文件 | rawticks稳定启动/进程身份及Graphon一致proof，依赖原UID/PGID/cgroup/netns保护 | agent-1020-native，root接管 | 在用 | 不可删除 | T58的191纯检查/11显式真实跳过通过；T-root83实际14角色无重启迁移与桥复用成功 | 2026-10-21/T-native58/root83 |
| .local/mysql-demo/recovery-20261021-preflight-fixed.json、quality-import-20261021.json、platform-register.json、platform-verify-devices.json、platform-verify-products.json | 主数据2340740行与食品16样本的成功导入、来源登记和三业务结果，Git忽略 | agent-1021-root | 在用 | 不可删除 | 已通过，恢复/防重复导入/来源追溯依赖；不包含导出账号密钥 | 2026-10-21/T-root81/84 |
| .local/ui-manufacturing-demo/ | 实页6/16样本、外观细节/示意图、维护4分组CSV下载收据与5截图，Git忽略 | agent-1021-root | 在用 | 不可删除 | 41HTTP/10.525秒通过，数值显示与柱动画截图改进另T-web46，原证据保留 | 2026-10-21/T-root84 |
| .local/migrate_wsl_identity_v2.py、run_identity_migration.py及mysql-demo/wsl-identity-v2-migration.json、platform-workspace-identity-v2-bridge.json | 本次精确壁钟偏移迁移入口及无重启实证，原清单备份在独立WSL identity-migrations目录，Git忽略 | agent-1020-domain，root接管 | 在用 | 不可删除 | T59本轮专用，不能泛用到其它实例；14角色归属前后核对通过 | 2026-10-21/T-domain59/root83 |
| .local/verify_aggregation_graphon.py、aggregation-demo-definition.json、mysql-demo/platform-aggregation-graphon.json | 两条固定原Graphon聚合图创建/执行/事件与同键回查的本机证据，Git忽略 | agent-1020-native，root接管 | 在用 | 不可删除 | 18HTTP/2run成功且无模型业务源；原始失败/历史不得覆写 | 2026-10-21/T-native57/60/root86 |

| apps/web/src/modules/manufacturing/measurementPresentation.ts、InspectionDetail.vue、InspectionObservation.vue、MaintenanceWorkspace.vue、TrendChart.vue与tests/manufacturing-presentation.mjs | 概览格式与原精度分离、中文比较状态及真实图表绘制完成反馈；原bins仍用于计算/CSV | agent-1020-web，root接管 | 在用 | 不可删除 | 168定向纯检查/check/build及34请求改后真实UI通过；全部3新截图已目检 | 2026-10-21/T-web46/root84 |
| .local/ui-manufacturing-presentation/ | 改后显示与原始证据、finished柱图的独立真实收据/三图，Git忽略 | agent-1021-root | 在用 | 不可删除 | 与原41请求CSV收据分开保留；无额外源SQL模型/导出 | 2026-10-21/T-root84 |

- T-web46/root84索引路径补正：前条缩写tests/manufacturing-presentation.mjs的实际项目路径为apps/web/tests/manufacturing-presentation.mjs；相关改动另含apps/web/tests/inspection-presentation.mjs与apps/web/tests/maintenance-export.mjs，均已检查调用/测试，在用且不可删除。
