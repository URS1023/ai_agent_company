# 大版本更新：工具参数与连接器专项复核

审查基线：主工作树 `0cc7d2d`，2026-10-04；修复位于独立分支 `codex/major-capability-contracts`，避免改变正在进行的九域百轮验收。本文中的源码确认、隔离复现与浏览器验收分别记账。

## 实际阅读范围

全文通读连接器 `router.py/service.py/derive.py`、工具 `validator.py`。按调用链阅读能力中心 `router.py` 前 220 行、`schemas.py` 前 120 行，`service.py` 的 Schema、配置规范化、工具可见性及创建/发布函数；阅读 `builtin_provider.py` 工具声明与注册实现、`gateway.py` 身份/参数校验与派发部分、`compiler.py` 编译参数入口及相关测试。未把这些片段阅读登记成上述大文件的全量人工审查。

主树四个全文文件的 SHA256：

| 文件 | SHA256 |
| --- | --- |
| backend/app/modules/connector/router.py | afd0c9f1f3502e8db7d6659ec910c10bd3394e3e9f62b14e7448e6be00dd635d |
| backend/app/modules/connector/service.py | 5002ce2cf65183fac9fb2d10d76074b4ea5316c506365b3ac26324e80d985a88 |
| backend/app/modules/connector/derive.py | e38fa33eace50d1081c7e0abdfd8dba1828f2f3a5ea235c3124def532190f98c |
| backend/app/providers/tool/validator.py | 8f512000b842726af62b896018f4de3358c2ce07e84ecbf6769854ae2182f594 |

## 确认问题及修复

### TC-001 · P1 · 保存的工具约束没有全部执行

基线位置：`capabilities/service.py::_schema_contract`、`providers/tool/validator.py::validate_value`。

配置允许 `allOf/not/exclusiveMinimum/exclusiveMaximum/multipleOf/uniqueItems`，执行器没有对应判定；数字、布尔及 null 的枚举不检查；Python 等号将布尔值与 0/1 混同；`anyOf` 命中即返回，绕过同级 required/额外字段/数值约束。`additionalProperties` 中的 Schema 未递归校验和执行。无效类型、未知业务规则、畸形约束也能保存，导致调用失败或静默失效。

修复：保存与运行共享受控 Schema 契约，以本机已安装的 `jsonschema 4.26.0` 做元结构检查，并在 requirements 显式声明依赖；不解析远程引用。运行期执行全部已允许约束，按 JSON 语义区分布尔值与数字；十进制倍数避免浮点误差；数组去重用有类型的等价键。Schema 成功校验缓存按完整内容和规则目录失效，不按对象身份信任可变字典。

隔离复现：首组 42 项中 32 失败、10 通过；包括实际连接器试运行接口的 provider 派发计数。修复后同级 required/额外字段不合格时，provider 调用数为 0。完整内置工具声明也纳入结构检查，后续新增边界及最终数量见本批统一验收记录。

### TC-002 · P1/P2 · 错误回执复制原值，极端 JSON 可逃为服务器错误

基线类型错误调用 `_value_preview`，合成秘密作为错误类型的参数时完整进入 SchemaValidationError；网关包装后继续向调用方返回。5000 位整数的 JSON 触发 ValueError，根参数和容器解包只捕获 JSONDecodeError，不能转换为稳定业务错误。Schema 的不成对 Unicode 代理字符也曾逃出编码错误处理范围。

修复：类型错误只返回字段路径、期望类型与实际类型，不复制原值；解析范围、递归、非有限数值和无效 Unicode 均归为明确参数错误。真实 HTTP 保存无效 Unicode Schema 返回 422。缓存不会记录用户参数。交叉复核新增 5 项先得到 3 失败、2 通过，修复后 5 项通过。

### TC-003 · P2 · 连接器推导的合法文本和脏类型处理不稳定

基线 `connector/derive.py::_extract_json` 用花括号深度扫描，不区分字符串内部；JSON 数组可被直接返回后触发 `.get` 异常，字符串超时、非对象工具等可形成 500。`router.py::derive` 将不支持的 PUT/PATCH/DELETE 默改 POST，form 默改 json，改变了原接口含义。提示中还把日期格式写成 `format=YYYY-MM-DD`，与 Schema 的 `format=date` 冲突。

修复：用 JSONDecoder 读取第一个完整容器并要求对象，Pydantic 严格校验模型输出的字段类型及范围；不支持的方法和映射明确拒绝，创建前完成检查，不遗留四张注册表的半成品。日期提示使用 `format=date`，输入形态放入描述或示例。模型权限、取消和服务异常保留原语义。

证据：新增 39 项合同通过，原能力中心与治理 12 项通过，日期提示新增 1 项先失败后通过。JUnit 原件保留在独立树 `.implementation/connector-derive-*.xml` 与 `connector-date-prompt-*.xml`。

排除疑点：无创建权限的 HTTP 推导请求原本就由认证依赖提前拒绝，真实回归证明模型调用为 0。因此路由内增加前置权限检查仅记为加固，不冒称修复既有 HTTP 越权。

### TC-004 · P1 · 对话注册工具绕过统一治理且留下半成品

基线 `builtin_provider.py::_add_tool` 使用旧版内部服务，先提交创建记录，再发布；未调用严格配置和名称冲突检查。实际 Gateway 复现表明，只有 home.view 和 connectors.view 的角色可创建并发布；缺少查看权等失败路径仍可在四张注册表各留下 1 行。管理台拒绝的 URL、方法、外部引用和内置工具重名也曾成功注册。

修复由同一能力服务负责创建和发布，沿统一权限与严格配置检查，一次事务提交；原管理台创建入口仍只创建草稿。最终故障注入、权限及固定 URL 查询参数的验证结果在本批交付记录中登记。

### TC-005 · P2 · 测试依赖本机部署配置且测试了错误执行器

独立工作树没有部署 `.env.local`，旧 runtime_recovery 用例先因缺少模型配置失败；补假配置后又走向 Open WebUI 服务，而其 mock 实际作用于休眠的历史 Agent。

修复：隔离入口固定非秘密模型假配置，地址为禁止连接的 127.0.0.1:9，不能借用真实部署密钥；历史专项显式绑定被测试的 Agent 并禁用模型追问。7 项历史回归通过，但它们不算当前 DSH 的端到端验收。

## 保留的范围说明

- `date-time` 保留既有无时区本地时间兼容，但不接受仅有日期；URL 格式检查不代表网络目标已获授权，网络与资源授权仍由 provider 执行。
- 本轮没有对不受信任正则的最坏执行时间作完整证明，也未把一次注册事务称为跨进程精确一次执行。
- 全部验证使用临时 SQLite、内存参数和本地桩，没有连接 szh_new 或外部模型。主树百轮结果和本分支验证不能混算为同一版本全功能通过。
