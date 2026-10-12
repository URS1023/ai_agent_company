# 批次十三：Bot 运行身份、固定版本与执行范围修复

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。分支：`codex/major-authorization-followup`。基线：`112bdd3efdc242a95f174e497922b42e56d6f1a9`。

本项实现已冻结，未提交。只修改 `backend/app/modules/knowledge_graph/bots.py`，新增 `tests/backend/test_bot_runtime_authorization.py`；证据保存在 Git 忽略的 `.implementation` 下。共享工作树内其他代理的 analytics、reports、builtin_provider 等修改没有回退或纳入本项修改声明。未修改主树、graphs.py、通用 data_scope、knowledge、builtin_provider 或 analytics。

## 实际结果

原实现通过真实 ASGI HTTP 管理、令牌签发和公开 Key 链路复现 **5 失败、1 通过**；修复后初始 6 项全过。扩充原服务令牌撤销/到期/归属变化/权限收窄、禁止调用中扩权、源资源停用与 Key 错配回归后，最终 **30 通过、0 失败、0 错误、0 跳过，27.252 秒**。

最终构成：本次 Bot 专项 18 项，既有 `tests/backend/test_knowledge_graph.py` 6 项，既有 `tests/offline/test_knowledge_bot_actions.py` 6 项。`git diff --check` 通过；只有仓库已有的 LF/CRLF 提示。最终测试产生 4347 条已有 `datetime.utcnow()` 等弃用告警，不影响断言结果。

## 修复内容和位置

1. **BOTS-N01 / P1：正式版 HTTP 服务调试保留原客户端约束。** `bots.py:806` 的 `_bot_context` 接受并校验原 AuthContext，要求同 Session、同工作区和同用户。服务调用按原 Bearer 摘要重新定位原 IntegrationAccessToken，复用 `resolve_service_principal` 核对原令牌和客户端当前状态、归属及 scope；继续保留原 `client_id`，有效 scope 是初始请求范围与当前令牌/客户端范围的交集。原始明文只在本次请求已有上下文中读取，不写入运行、审计、快照或执行字典。调试入口、知识、图谱、Skill、动作目录、工作流和工具调用逐层传递原身份；ToolGateway 也获得带有限制的 AuthContext。不会把底层管理员重新解释为无限制管理员。

2. **BOTS-N02 / P1：公开 Key 明确要求所有者的 Bot 执行权限和资源范围。** `_bot_context` 明确要求 `bots.execute` 和当前阶段动作，并重读用户、成员和工作区活动状态。`_run_bot`（`bots.py:1533`）在创建 BotDebugRun 之前 `require_resource(..., "bots.execute", bot)`。公开 Key 沿用产品既有的 Bot 所有者身份，不复制管理员权限。各能力执行之后、写引用和生成最终答案之前再次建立身份并核验 Bot 范围（约 `bots.py:1650`），保留了最终阶段门禁。

3. **BOTS-N03 / P2：固定历史 Skill/Tool 版本仍可执行。** `_bound_tool_spec`（`bots.py:1205`）通过 ToolVersion.tool_id 与目标工具归属解析快照 versionId，补充 ConnectorVersion.connector_id 与 Connector.id 的一致性，移除“必须还是当前发布指针”的约束。`_skill_instructions`（`bots.py:1384`）同样按固定 SkillVersion 身份/归属加载，并显式检查源 Skill 权限。固定历史版本和源资源仍须保持正式/可用状态；源资源归档继续阻止执行。正常发布 v2 后旧 Bot 仍使用 v1，工具传输桩实际收到 v1 路径 `/v1/original`。

4. **BOTS-N04 / P2：先绑定执行范围，再查询工具。** `_bound_tool_spec` 在联表 SQL 之前建立 `connectors.execute` 上下文，随后保留资源级、版本、归属和状态检查。查看权限撤销但执行权限仍存在时能执行；撤销执行权限后不再传输。

共享 Session 的阶段顺序另已复核：`_knowledge_references`（`bots.py:600`）在查询知识库和原 File 之前重绑 `knowledge.view`；`_graph_references`（`bots.py:700`）在图谱查询之前重绑 `graphs.view`。因此 `_run_bot` 先构建 knowledge_ctx 再构建 graph_ctx，并不会让后建的图谱范围覆盖实际知识检索阶段。没有改变通用 File 授权，也没有扩大 files.view。

## 隔离与命令

所有动态检查仅由主树已有解释器绝对路径启动独立树 `tests/run_isolated_backend.py`：

```powershell
& 'E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe' tests/run_isolated_backend.py tests/backend/test_bot_runtime_authorization.py --junitxml=.implementation/bots-batch13-red.xml *> .implementation/bots-batch13-red.log

$env:PYTHONIOENCODING='utf-8'
& 'E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe' tests/run_isolated_backend.py tests/backend/test_bot_runtime_authorization.py --disable-warnings --junitxml=.implementation/bots-batch13-green-initial.xml *> .implementation/bots-batch13-green-initial.log

& 'E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe' tests/run_isolated_backend.py tests/backend/test_bot_runtime_authorization.py tests/backend/test_knowledge_graph.py tests/offline/test_knowledge_bot_actions.py --disable-warnings --junitxml=.implementation/bots-batch13-green.xml *> .implementation/bots-batch13-green.log
```

工作目录均为上述独立树。Harness 强制 `sqlite:///:memory:`，fixtures 断言数据库驱动与库名；无主机/端口，也无转发、代理或真实数据库连接。临时存储、日志、原生会话目录都在一次性 TemporaryDirectory。MySQL/PostgreSQL 驱动被拒绝，外部网络被拒绝，回环 9/8011/8012 也被拒绝。HTTP 是 FastAPI TestClient 的进程内协议请求，没有启动监听服务。新专项仅在模型 provider 和 HTTP transport 边界使用内存桩，保持身份认证、资源授权、动作选择和 ToolGateway 实现真实执行。没有访问真实模型、业务库或外网，没有安装依赖。

## 断言覆盖

| 路径 | 实际验证 |
| --- | --- |
| 正式调试、底层管理员服务令牌 | 不带 knowledge.view 返回 403 且无知识正文；带该 scope 返回知识引用；检索模式不调用模型/工具 |
| 公开 Key、普通所有者 | 保持 Key 与知识权限，只撤 bots.execute，返回 403，不新增 BotDebugRun |
| 固定 Skill/工具版本 | Bot 固定 v1，源发布 v2 后仍引用 v1；工具实发 v1 路径；源归档后拒绝该能力且不新增传输 |
| 查看和执行权限不同 | 普通所有者仅撤 connectors.view 仍执行；继续撤 connectors.execute 后无工具执行引用、无新增传输 |
| 选择与执行之间服务撤权 | 客户端停用、原令牌撤销/到期/删除、令牌工作区/用户改变、客户端 owner/client_id 改变均 401；令牌或客户端收窄 scope 均 403；共 10 种，均无 ToolCall 和实际传输 |
| 调用中扩权 | 同时给客户端和令牌新增 devices.view 仍不能突破原请求 scope；ToolGateway 拒绝，ToolCall.error_code=forbidden，无传输 |
| Key 错配与显式撤销 | 其他 Bot 不接受当前 Key；撤销后原 Bot 也不接受；均不新增运行 |
| 既有链路 | 本体、结构化/非结构化抽取、图谱发布、Bot Key 调用/撤销、归档资源拒绝及离线动作选择/网关接口 12 项通过 |

## 已验证范围与剩余边界

- 本次动态撤权发生在隔离 SQLite 中，由单独 fixture Session 于内存模型返回前顺序提交，再进入下一能力阶段。**这不证明 MySQL REPEATABLE READ 的在途事务能看到另一个事务刚提交的撤权**；`populate_existing` 或新 AuthContext 不能突破数据库快照。若要求在途立即撤权，需要单独设计新事务/新会话复核与取消语义，本项没有作此承诺。
- 原令牌跨工作区/跨用户变更的失败分支已覆盖；本次未新增完整双工作区用户、显式 ResourceGrant 授予/撤销矩阵。Bot 资源授权与各源能力既有工作区/权限检查被保留，并不把现有静态检查计作新增动态验收。
- 正常发布新版本与源归档的区别已验证。源处于新草稿状态时的既有 `status == published` 要求未改；本次不定义新的草稿/正式并存状态契约。
- 最终答案之前重验 Bot 与原服务令牌，但并未实现模型已经开始生成之后的取消、所有源权限的独立新事务最终再验，或在途公开 Key 吊销的即时中断。
- 真实 HTTP 的知识 scope 和工具执行 scope 已动态覆盖；工作流及图谱/Skill scope 保留传递通过源码路径和既有兼容回归复核，尚非全部服务 scope 组合的动态矩阵。
- 历史运行引用读取策略、错误内容脱敏、图谱删除与正式 BotRevision 依赖保护不属于本项修改。root 统一处理图谱删除保护和后续门禁；本报告不把下一批只读研究当成已修复/已验证。

## 冻结 SHA-256

| 文件 | SHA-256 |
| --- | --- |
| backend/app/modules/knowledge_graph/bots.py | `1A409D2566F7AADA515CBBD6F58C7CA1E50F0665FD452CF576660FAF139E79C6` |
| tests/backend/test_bot_runtime_authorization.py | `2715E4B744D9AEFF2FA312F5A3E146933AF8452FC2486A4C931D4538E3E3CB07` |
| .implementation/bots-batch13-red.log | `E40557F2A3624B5431824CA916B7AD352C88CC94B7C74A48BE61C534EE28497F` |
| .implementation/bots-batch13-red.xml | `885E64CEC6AFDEB9F7E2A28A0ED8551B60431AC056CD460C4022D399D3EB13EE` |
| .implementation/bots-batch13-green-initial.xml | `DBA9F03CFE858BA85DCFAA3AFF6B1F692E03EDE4F62B03F153ED43DF77A9E14B` |
| .implementation/bots-batch13-green.log | `24BA018CEE3C345790B4AC3176C9BE61463094E79A0969E3DD62FD087920C378` |
| .implementation/bots-batch13-green.xml | `D66E8EE48C49DD057A734A2836955FF32D24488B5F6DFEDAC01098650CA7F9A6` |

红灯记录保留最初 6 项的失败断言，不以扩充后的 18 项文件声称完成了全部红灯。绿色 XML 是最终 30 项完整结果。红灯日志的部分中文由当时终端默认编码显示失真，原始断言和 JUnit 文件仍保留；后续统一 PYTHONIOENCODING=utf-8。
