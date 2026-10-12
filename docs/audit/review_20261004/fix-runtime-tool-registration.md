# 对话注册工具治理修复记录

日期：2026-10-04。仅修改独立工作树 `E:\code\Yun_tian\the_company_agent\test5\.implementation\worktrees\major-capability-contracts`；主树源码及构建未改，未启动或重启在用服务，未提交。

## 已复现的问题

复现使用真实 `ToolGateway.execute`、HTTP 管理接口创建的自定义角色与成员、内存 SQLite。没有替换权限判定或连接器创建/发布实现。

1. **仅有查看权限仍可注册并发布。** 角色只有 `home.view`、`connectors.view`，旧 `add_tool` 成功返回 `registered: true`，无需 `connectors.create` 或 `connectors.publish`。
2. **失败也会留下未授权写入。** 仅有 `home.view`、仅额外拥有 `connectors.create`、仅额外拥有 `connectors.publish` 的成员得到“发布阶段：Connector 不存在”，但 `connectors`、`connector_versions`、`tools`、`tool_versions` 四表各增加一行。原因是先提交创建，再由旧发布函数查询；ORM 可见范围只阻止了后半段读取。
3. **绕过管理台的严格配置与重名检查。** 旧注册路径接受字面量私网/元数据 IP、URL 明文凭据、非法端口、PUT/PATCH/DELETE、非法工具名、内置工具同名、非对象输入 Schema、外部 `$ref`、重复注册同名工具和未声明请求头参数。
4. **发布提交失败留下草稿。** 在 `before_commit` 注入发布故障后，旧代码四张注册表各保留一行。
5. **固定查询参数丢失。** 旧代码使用 `urlparse(url).path` 构造配置，遗漏 `query`；本轮通过注册后真实 HTTP provider 的内存传输桩验证新行为，而非仅断言已保存配置。

## 最终修改

| 文件 | 修改 |
| --- | --- |
| `backend/app/providers/tool/builtin_provider.py` | `add_tool` 声明 create、publish 两项权限；仅声明 GET/POST，拒绝额外字段。`_add_tool` 使用 URL 严格校验、`ToolDefinition`、`ConnectorInput` 与能力中心原子服务，不再调用旧 connector 服务。不扩张私网访问权限，保留固定 query，不回显契约校验中的原始值。 |
| `backend/app/modules/capabilities/service.py` | 抽取私有 `_create_connector_record`、`_publish_connector_record`，复用原配置、Schema、重名、资源范围检查。新增内部注册入口 `create_and_publish_connector`，创建与发布分别鉴权；创建审计、四类记录、发布审计一次提交，任何提交前异常整体回滚。原管理台 `create_connector` 仍只提交草稿，`publish_connector` 仍独立鉴权及提交。 |
| `tests/backend/test_runtime_tool_registration.py` | 新增 29 项隔离回归，覆盖权限不足、严格配置、重名保护、单次提交、发布阶段/提交阶段故障、真实 provider 的 GET/POST 参数保持与原管理台合同。 |

注意：SQLAlchemy 会话使用 `autoflush=False`，创建 helper 在全部工具版本写入后显式 `flush`，使同事务发布能查询到最后一个版本；没有因此提前提交。

`capabilities/service.py` 的 `_schema_contract` 及 `providers/tool/validator.py` 由 root 同时负责，本次未改它们；文件整体 diff 中包含 root 的先前修改。

## 测试与证据

执行器始终使用主树绝对路径：

```powershell
E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe tests/run_isolated_backend.py tests/backend/test_runtime_tool_registration.py tests/backend/test_capabilities_center.py tests/backend/test_capability_governance.py tests/backend/test_connector_derivation_contract.py tests/backend/test_tool_schema_contract.py --disable-warnings --tb=short --show-capture=no
```

工作目录为独立树。该轮 **140 项通过、2 项失败**；两项为新增 GET/POST 派发用例，失败原因是测试夹具保留 `allow_network=False`，被 provider 正确拒绝，未发出请求。修正为先把 `request_json` 替换成纯内存桩，再设置执行上下文的派发授权；产品源代码没有因此变化。

随后定向复测：

```powershell
E:\code\Yun_tian\the_company_agent\test5\.venv_py312\Scripts\python.exe tests/run_isolated_backend.py tests/backend/test_runtime_tool_registration.py -k registered_tool_keeps_fixed_query --disable-warnings --tb=short --show-capture=no --junitxml=.implementation/runtime-tool-registration-dispatch-after.xml
```

结果 **2 passed, 27 deselected**，退出码 0。综合两轮，5 个文件的 **142 个用例均已验证通过**；不把失败的首次合并执行写成单轮全绿。

额外证据：此前 26 项注册专项全部通过；`git diff --check` 通过（仅 Git 的换行风格提示）。独立只读交叉复核检查真实网关、当前身份重建、RBAC、事务 helper、`autoflush=False`、预提交视图与 HTTP provider，未发现本轮新增阻断问题。

## 边界

- 使用临时 SQLite，禁用业务数据库驱动、真实模型和外网；正常注册不会执行外部工具。派发测试使用纯内存传输桩，没有 DNS 或套接字连接。
- 发布故障包括真实事务的提交前事件故障和发布检查后的故障；没有模拟物理数据库提交已成功但客户端丢失确认的故障。
- 本轮复用现有重名检查，验证串行重名和内置名冲突；未增加数据库唯一约束或声称验证并发同名注册。
- 本记录不宣称旧 connector 服务全部入口已迁移，也不扩大到其它内置工具的治理审查。

## 记录时文件 SHA256

| 文件 | SHA256 |
| --- | --- |
| `backend/app/providers/tool/builtin_provider.py` | `3765c188a55cae0615fed4329baf9932702814e5c6c38ab9d70976aa3456479a` |
| `backend/app/modules/capabilities/service.py` | `b6cc57032fcc12ab7b851ba77dc1fff0dc6ec64b2a8f07a6a4156df0f4a68a01` |
| `tests/backend/test_runtime_tool_registration.py` | `83f762a41c9a5f3da177bec9e5244e46206a62b95ebe947b7b6f5a5c55746544` |

记录后其他代理可能继续修改共享独立树，复核时应重新计算哈希。
