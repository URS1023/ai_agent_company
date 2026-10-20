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
