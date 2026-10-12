# 批次十三：FK-004 知识检索作用域修复

- 日期：2026-10-04。
- 独立工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-capability-contracts`。
- 分支：`codex/major-authorization-followup`。
- 基线：`112bdd3efdc242a95f174e497922b42e56d6f1a9`。
- 本记录在 ignored `.implementation` 内；未修改主工作树，未提交，未启动或重启在用服务。

## 问题与最小修复

HTTP `/api/knowledge/search` 绑定 `knowledge.view`。该作用域允许检索已授权知识库所引用的同工作区源文件，用于分段正文和来源复核。实际 ToolGateway 已验证 `knowledge.view` 动作，但 `_search_knowledge` 原先直接调用检索服务，Session 仍处于通用作用域：没有 `files.view`，或只有 `files.view/self` 的共享成员会在 File 联表中被过滤，得到空片段。

产品修改严格限于 `backend/app/providers/tool/builtin_provider.py::_search_knowledge`：

1. 在身份补建之前保存 `Session.info` 三字段的原值和原先是否存在：`resource_access_criteria`、`actor_user_id`、`actor_workspace_id`。
2. 复用网关重建的 AuthContext；缺失时使用既有 `auth_context_from_tool`。提供的 actor 若与 Session、用户或工作区不一致则拒绝，客户端 scope/id 沿用现有身份。
3. 显式验证 `knowledge.view`，临时调用既有 `bind_data_scope(actor, "knowledge.view")`，然后执行原检索服务。
4. `finally` 恢复三字段；原来不存在的字段删除，不修改 `request_permission`，不清空其它 Session.info 状态。成功、搜索异常、绑定中途失败及嵌套调用均覆盖。

没有修改公共 data_scope、File 联表、SQL 当前正文复核、知识库/文档/文件工作区和状态条件、分段启停条件、原文/下载/Artifact 服务或 Bot/analytics。没有新建第二套授权规则或锁/租约系统。

## 红绿与验证证据

全部使用主树绝对 Python：

`E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe`

由独立树的 `tests/run_isolated_backend.py` 启动。每例使用本机一次性 SQLite、临时存储与内存 ES/嵌入桩，禁止 MySQL/PostgreSQL 与外网连接，未请求真实模型或在用的 8011/8012 服务。

| 阶段 | 结果 | 证据 |
| --- | --- | --- |
| 产品未修改，新增专项初稿 | 22 项：15 失败、7 通过；31.40 秒 | `knowledge-scope-batch13-red.xml` |
| 首轮局部修复 | 22 项：21 通过、1 失败；29.43 秒 | `knowledge-scope-batch13-green.xml` |
| 修正测试源文件断言、补充 3 项边界 | **25 项全部通过；36.17 秒** | `knowledge-scope-batch13-final.xml` |
| 既有四文件相邻回归 | **18 项全部通过；18.96 秒** | `knowledge-scope-batch13-related.xml` |

初始失败确认为行为断言：HTTP 返回已授权共享片段，真实 ToolGateway 却返回 `[]`；已有文件范围的直接入口也未建立知识临时范围。初始 XML 中 15 个失败没有依赖缺失或夹具创建错误。

首轮绿灯中的唯一失败是新测试断言不准确：手工分段的物理文件正文为“由知识库分段管理维护。”，分段正文保存在 KnowledgeChunk 中，二者本来就不同。已将独立授予文件权限后的成功断言改为 `read_file.text` 与真实 HTTP `/text` 返回正文精确一致；没有改产品去伪造原文件正文。原失败 XML 保留。

最终专项命令：

```powershell
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_knowledge_tool_scope.py --disable-warnings --show-capture=no --junitxml=.implementation/knowledge-scope-batch13-final.xml
```

相邻回归命令：

```powershell
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_knowledge.py tests/backend/test_knowledge_governance.py tests/backend/test_knowledge_search_authority.py tests/backend/test_knowledge_file_integrity.py --disable-warnings --show-capture=no --junitxml=.implementation/knowledge-scope-batch13-related.xml
```

既有四文件合计 18 项；本次实际完成 **43 项通过**，不是统一全量门禁。`git diff --check` 无错误；环境仍有 datetime/Pydantic 等兼容性警告，未在本次范围内治理。

## 新专项覆盖

| 范围 | 参数展开后项数 | 核实内容 |
| --- | ---: | --- |
| 他人拥有的共享 KB/File，HTTP 与实际 ToolGateway 一致 | 6 | 无文件动作、文件动作 self；当前索引、空索引 SQL 回退、旧正文索引回退；片段正文和来源完全相同 |
| 用户/角色显式授权与撤销 | 2 | 原 grant 可用，撤销提交后下一网关调用不再返回；残留 ES 候选不能阻止仍授权库的 SQL 回退；显式请求撤权库 404 |
| 未授权资源、跨工作区及伪造来源 | 1 | 默认检索不泄漏；显式请求拒绝；合法 chunk 的伪造 ES file/kb/document 引用由真实 SQL 关系纠正 |
| 原文、下载、read_file 与 Artifact 独立授权 | 2 | 检索后同 Session 的 File/Artifact SELECT 仍拒绝；无动作 403、有 self 动作但无资源范围 404；单独授予文件 view/export 后 HTTP 与工具原文一致，附件仍不可见 |
| 实际签发的服务客户端 scope | 2 | HTTP 与网关均遵守当前解析 scope；缺 knowledge.view 拒绝；仅 knowledge.view 能取片段，不能读/下载原文件；client_id/scope 保留 |
| 同 Session 三字段恢复 | 4 | 预绑定/原字段不存在 × 成功/异常；缺 actor 时身份补建也不遗留字段；其它 info 状态和 request_permission 保持 |
| 嵌套调用恢复 | 1 | 内层恢复外层知识范围，外层恢复进入前通用范围 |
| 绑定中途异常 | 1 | 两字段已修改后抛错，三个授权字段精确恢复 |
| 直接入口授权边界 | 2 | 缺动作权限、actor 与执行身份不匹配均在检索前拒绝，范围不变 |
| 当前生命周期 | 4 | 源文件软删除、文件非 ready、KB 非 active、分段停用均不返回 |
| 合计 | **25** | 检索服务与最终 SQL 条件均真实执行，只替换外部索引及模型为内存桩 |

## 精确边界与待办

- 撤权保证为下一次能够观察到撤销提交的新请求/新事务；没有证明同一次搜索在重排期间发生撤权也能即时收回已送出的内容，也没有改变 MySQL RR 快照语义。
- 客户端 scope 测试使用现有令牌解析后的上限；本函数没有新增令牌实时撤销或客户端心跳逻辑，不替代 Worker 已有的后台客户端授权复核。
- 本次保留原文/下载/附件的独立授权，不声称知识 grant 赋予整份源文件读取权限。
- FK-002 入库硬中断接管仍只有设计，未实施进程锁/租约迁移。
- 统一聊天交付门禁、完整浏览器验收及部署加载由根代理集中执行；本报告不把单域隔离通过写成全功能通过。

## 本批文件 SHA256（相邻回归完成后）

| 文件 | SHA256 |
| --- | --- |
| `backend/app/providers/tool/builtin_provider.py` | `C5986E694DA9A624B5587A387B4EB0EF45E0AEC746E33B99A0340CD6DAED9EF0` |
| `tests/backend/test_knowledge_tool_scope.py` | `8EDA65544F013C84717246B6AE38F13972B1D8B7207B104638195062258DB9E6` |

本代理只修改以上产品函数、新专项测试与本 ignored 记录。其他代理在同树中的 analytics / bots 修改不属于本报告覆盖范围，也未被回滚。
