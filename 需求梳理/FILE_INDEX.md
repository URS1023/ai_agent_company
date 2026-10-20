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
