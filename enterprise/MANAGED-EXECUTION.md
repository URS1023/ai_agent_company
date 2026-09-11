# 受管设备执行：S3 操作说明

本文对应当前工作树中的企业独立 API、原生执行身份桥与设备评估插件源码。目标链路是：**已有 DB / HTTP 记录 → 原生工作流中的受管工具节点 → 冻结读取证据 → 确定性预警或质检 → End.result → 企业运行写回**。

这是一份配置和验收说明，不是部署完成证明。本轮未启动服务、未执行数据库迁移、未安装插件，也未用真实业务数据跑通端到端。插件声明与 SDK 包装源码已具备；安装包构建 / 签名、安装、网络连通和发布版本适配仍需部署环境验收。本文不包含未经实际验证的插件安装命令。

> 示例中的主机、ID、UUID、口令和密钥全部是占位值。**不要拷贝示例凭据上线。** 生产密钥应独立生成、受控分发并限制文件读取权限。数据库 URL、Service API key 和 HMAC secret 只进入各自服务器配置，别放进 Git、浏览器、工作流参数、提示词或日志。

## 1. 先区分三个运行边界

| 边界                       | 职责                                                                       | 配置所有者     |
| -------------------------- | -------------------------------------------------------------------------- | -------------- |
| 企业独立 API / 单次 worker | 企业设备、绑定、排队、原生运行关联、实际读取、快照、评估与结果写回         | 企业服务运维   |
| 原生 Dify 工作流执行进程   | 核实被登记的工作流、插件、凭据与节点，把真实执行身份放入本次调用的私有凭据 | 原生 Dify 运维 |
| 插件进程                   | 读取私有执行身份，签名访问固定企业私有源站，把完整评估封装作为工具文本返回 | 插件运维       |

正常顺序：

```text
用户以原生账号提交企业运行（冻结绑定版本与参数）
  → 单次 worker claim；同设备 / 场景保持单执行通道
  → 固定发布 workflow UUID 的 Service API streaming 调用
  → workflow_started：持久化企业 run ↔ 原生 native_run_id 关联
  → 已登记工具节点：原生桥注入本次真实执行身份
  → 插件签名 POST 企业私有 /enterprise/internal/v1/evaluate
  → 核对关联与执行状态；首次实际读取并冻结，或恢复已有快照
  → 按登记的 specification_revision 计算
  → 工具 text → End 输出 result
  → worker 校验完整封装、快照摘要和身份，再持久化业务结果
```

模型参数不是执行身份。企业 `dispatch_nonce` 留在服务内部，不传给工作流或插件。`enterprise_context` 虽随工作流输入发送，但也不是私有接口的身份凭据。

## 2. 企业服务环境变量：四份 JSON 文件

### 2.1 必需基础环境

| 环境变量                      | 形状 / 示例                                                                                                                      | 实际约束                                                                                                                                                                   |
| ----------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ENTERPRISE_DATABASE_URL`     | `postgresql+psycopg://ENTERPRISE_USER:REPLACE_DB_PASSWORD@enterprise-db.internal.example/enterprise_devices?sslmode=verify-full` | 独立 PostgreSQL；驱动精确为 `postgresql+psycopg`；库名匹配 `enterprise_[a-z0-9_]{1,48}`；有 host、username；URL query 仅接受 `sslmode`、`sslrootcert`、`sslcert`、`sslkey` |
| `ENTERPRISE_DIFY_CONSOLE_URL` | `https://dify.internal.example/console/api`                                                                                      | 固定原生 Console API base，保留 `/console/api`；无 URL 凭据、query、fragment                                                                                               |
| `ENTERPRISE_DIFY_SERVICE_URL` | `https://dify.internal.example/v1`                                                                                               | 固定原生 Service API base，保留 `/v1`；无 URL 凭据、query、fragment                                                                                                        |
| `ENTERPRISE_ALLOWED_ORIGINS`  | `https://dify.example.com`                                                                                                       | 浏览器实际源站；多项用逗号分隔。每项是完整 origin，端口按实际填写；无路径、尾斜杠、通配符或重复项                                                                          |

数据库 schema 固定为 `public`，连接设置和 ORM schema 映射一致。应用工厂只组合依赖，既不回退到原生 Dify 数据库，也不自动建表；部署前需单独审查并执行企业迁移。本文不执行迁移。

### 2.2 四份文件的环境名

| 环境变量                                 | 文件内容                                                         | 缺省值                   |
| ---------------------------------------- | ---------------------------------------------------------------- | ------------------------ |
| `ENTERPRISE_WORKFLOW_CREDENTIALS_FILE`   | `WorkflowCredential[]`，工作流 Service API 凭据注册              | 空数组语义               |
| `ENTERPRISE_READ_CATALOG_FILE`           | `RegisteredRead[]`，连接、读取定义、设备范围及输入声明合并登记   | 空数组语义               |
| `ENTERPRISE_MANAGED_EXECUTION_KEYS_FILE` | `ExecutionKey[]`，私有执行签名密钥及节点范围                     | 空数组语义；私有路由关闭 |
| `ENTERPRISE_SPECIFICATIONS_FILE`         | `(AlertSpecification \| QualitySpecification)[]`，版本化判定规格 | 空数组语义               |

环境变量的值是**进程内可读取的文件路径**，不是 JSON 正文；容器部署时使用容器内挂载路径，不是宿主机路径。四个文件都以 JSON 数组为顶层，每份最大 **1 MiB**。路径缺失、内容超限或类型校验失败，配置加载失败，公开错误只标明对应环境名，不回显内容。

这些配置在 runtime 创建时读取，当前没有文件热更新监视器。更新时使用受控发布 / 重启流程；保留仍被排队或运行中任务引用的旧版本配置。后文的原生 `ENTERPRISE_MANAGED_TOOLS_JSON` 是**另一进程的 JSON 正文环境配置**，不属于这四份文件。

## 3. 文件一：工作流凭据注册

对应 `ENTERPRISE_WORKFLOW_CREDENTIALS_FILE`：

```json
[
  {
    "workspace_id": "WORKSPACE_ID",
    "app_id": "APP_ID",
    "secret_ref": "workflow-service-key-v1",
    "api_key": "REPLACE_WITH_SCOPED_DIFY_SERVICE_API_KEY"
  }
]
```

- 四个字段均必需。匹配键是 `(workspace_id, app_id, secret_ref)`，重复注册被拒绝。
- `api_key` 是该原生应用的 **Service API key**，非空、最多 4096 个无空白可打印 ASCII 字符。
- 企业绑定仅保存 `secret_ref`；worker 按冻结的 workspace / app / secret_ref 取出实际 key。
- 无匹配凭据时，在实际原生派发前以 `workflow_configuration_unavailable` 终止企业运行，不会用其他应用凭据兜底。

## 4. 文件二：连接与读取目录

对应 `ENTERPRISE_READ_CATALOG_FILE`。下例包含一个 HTTP 预警读取与一个 DB 已有质检记录读取。**同一绑定选择其中一个明确读取定义，不会自动轮询或合并两个来源。**

```json
[
  {
    "connection": {
      "source": {
        "workspace_id": "WORKSPACE_ID",
        "source_id": "equipment-http",
        "revision": "source-v1"
      },
      "url": "https://source.internal.example/measurements",
      "allowed_hosts": ["source.internal.example"],
      "headers": [["Authorization", "REPLACE_WITH_SOURCE_AUTHORIZATION_VALUE"]],
      "allow_plain_http": false,
      "limits": { "max_rows": 1000, "max_bytes": 2097152, "max_pages": 20, "timeout_seconds": 15.0 }
    },
    "read": {
      "source": {
        "workspace_id": "WORKSPACE_ID",
        "source_id": "equipment-http",
        "revision": "source-v1"
      },
      "read_id": "device-alert-rows",
      "revision": "read-v1",
      "method": "GET",
      "read_only": true,
      "parameter_names": ["device_code", "department", "batch_id"],
      "rows_path": ["data", "rows"],
      "pagination": {
        "parameter": "cursor",
        "next_cursor_path": ["data", "next_cursor"],
        "has_more_path": ["data", "has_more"]
      }
    },
    "device_ids": ["DEVICE_ID"],
    "device_parameter": "device_code",
    "device_column": "device_code",
    "scope_attribute": "device_code",
    "department_parameter": "department",
    "parameters": [
      { "input_key": "batch_id", "parameter_name": "batch_id", "kind": "string", "nullable": false }
    ]
  },
  {
    "connection": {
      "source": { "workspace_id": "WORKSPACE_ID", "source_id": "lab-db", "revision": "source-v1" },
      "dialect": "postgresql",
      "connection_url": "postgresql+psycopg://SOURCE_READ_USER:REPLACE_SOURCE_PASSWORD@source-db.internal.example/SOURCE_DB",
      "allowed_tables": ["public.measurements"],
      "read_only_role": true,
      "tls": true,
      "limits": { "max_rows": 1000, "max_bytes": 2097152, "max_pages": 20, "timeout_seconds": 15.0 }
    },
    "read": {
      "source": { "workspace_id": "WORKSPACE_ID", "source_id": "lab-db", "revision": "source-v1" },
      "read_id": "device-quality-rows",
      "revision": "read-v1",
      "sql": "SELECT id AS record_id, sample_id, revision AS measurement_revision, measured_at, device_code, width_mm FROM public.measurements WHERE device_code = :device_code AND department = :department AND batch_id = :batch_id ORDER BY id"
    },
    "device_ids": ["DEVICE_ID"],
    "device_parameter": "device_code",
    "device_column": "device_code",
    "scope_attribute": "device_code",
    "department_parameter": "department",
    "parameters": [
      { "input_key": "batch_id", "parameter_name": "batch_id", "kind": "string", "nullable": false }
    ]
  }
]
```

### 4.1 注册身份和范围

- 目录唯一键为 `(workspace_id, source_id, source_revision, read_id, read_revision)`。配置里的 `connection.source.revision` 和 `read.source.revision` 是 **source_revision**；`read.revision` 是 **read_revision**。
- `connection.source` 与 `read.source` 必须完全一致；HTTP 连接配 HTTP read，数据库连接配 SQL read。相同五元键重复登记被拒绝。
- `device_ids` 填企业设备主键，不是设备编码；`scope_attribute` 决定注入设备主键 `id` 还是 `device_code`，默认后者。编码如 `"000123"` 保持字符串。
- `device_parameter` 是服务注入的读取参数名；`device_column` 是结果中逐行校验的设备列。每行返回的设备值必须与实际注入范围相符。
- `department_parameter` 可省略或为 `null`；配置后由服务注入该设备的当前部门，业务参数不覆盖设备或部门范围。
- `parameters` 只声明可由业务运行提交的输入：`input_key` 是业务参数键，`parameter_name` 是来源查询键。`kind` 支持 `string`、`integer`、`decimal`、`boolean`、`date`、`datetime`，`nullable` 默认 `false`。参数名与保护范围键不重名。
- HTTP 的 `parameter_names` 必须完整覆盖上述注入参数和声明参数；分页游标单独由读取器管理，不放入业务参数。

### 4.2 来源约束

- HTTP：固定 URL / host / path；默认 HTTPS；URL 无 query、fragment 或内嵌凭据。`headers` 是 `[名称, 值]` 的二元数组列表，不是对象。转发路由类 header、Cookie、重复 header 被拒绝。读取器不跟随重定向，也不使用环境代理。
- HTTP read 的 `method` 支持 `GET` / `POST`；`read_only: true` 表示该端点是读取用途，不等于服务替上游实现只读。POST 的实际业务行为应由上游接口所有者确认。
- `rows_path` 指向行数组；每行应为列名到标量的对象。分页示例要求 `data.has_more` 为布尔值，后续游标位于 `data.next_cursor`。不分页时 `pagination` 省略或为 `null`。
- DB：支持 PostgreSQL `postgresql+psycopg`、MySQL `mysql+pymysql`、本地 SQLite 文件读取。生产连接由运维登记；`read_only_role: true` 是确认声明，不创建只读账号或权限。还应在源数据库实际限制账号权限。
- SQL 使用固定 SELECT 与命名绑定 `:parameter`，白名单表精确匹配。语法、函数、CTE 与参数集合由读取器校验，模型不拼接 SQL。数据库源与企业业务存储是两个配置边界。
- 默认上限为 1000 行、2 MiB、20 页、15 秒；可配范围分别为 1–100000 行、1 字节至 100 MiB、1–100 页、0.01–120 秒。DB 是一个游标结果集，成功也产生一页实际证据，不是 HTTP 式翻页。
- 超行数、字节、分页、截止时间、重复游标或半页失败都返回读取错误，不把部分结果当作完整正常数据。HTTP 总期限和 DB 驱动 / 查询超时共同约束读取；取消异步等待不等于已经取消 DB 工作线程。

### 4.3 已有快照与重算

首次 fresh 运行排队时快照为 `null`，只有受管节点真正读取后才持久化。服务验证来源四元组、实际参数指纹、设备范围、逐页证据和行数；不是接受模型宣称“已读取”。

已有快照或重算先恢复原始 typed cells、设备范围和参数摘要，不再查询当前目录、当前设备编码或源数据库。新重算应显式引用 `recompute_of`；来源四元组保持一致，空参数继承旧参数，非空不同参数被拒绝。若确需换数据，应建立新的 fresh 运行，而非给旧报告换输入。

## 5. 文件三：受管执行签名密钥

对应 `ENTERPRISE_MANAGED_EXECUTION_KEYS_FILE`：

```json
[
  {
    "key_id": "managed-key-v1",
    "workspace_id": "WORKSPACE_ID",
    "app_id": "APP_ID",
    "secret": "REPLACE_WITH_RANDOM_PRIVATE_HMAC_SECRET_AT_LEAST_32_CHARS",
    "node_ids": ["NODE_ID"]
  }
]
```

- `key_id` 全目录唯一；`node_ids` 至少一项。密钥绑定 workspace、app 以及明确的节点 ID 集合。
- `secret` 为 32–256 个无空白可打印 ASCII 字符。长度合格不是随机性证明；上线时换成独立随机密钥。
- 插件配置必须使用相同的 `key_id` 与 `secret`。HMAC key 与 Service API key 分开管理，也不是原生插件凭据记录 UUID。
- 签名覆盖请求原始字节，使用 HMAC-SHA256；插件按当前时间签发 30 秒有效请求。服务接受最长 60 秒有效期，未来签发偏差最多 5 秒。部署机器需要可靠时钟同步。

## 6. 文件四：判定规格目录

对应 `ENTERPRISE_SPECIFICATIONS_FILE`。下例同时展示预警与**已落库测量记录**的宽表质检；不包含仪器操作或采集任务。

```json
[
  {
    "workspace_id": "WORKSPACE_ID",
    "specification_revision": "alert-spec-v1",
    "scenario": "alert",
    "variables": [{ "name": "temperature", "column": "temperature_c", "kind": "decimal" }],
    "rules": [
      {
        "rule_id": "temperature-high",
        "revision": "rule-v1",
        "expression": "temperature > 85",
        "severity": "high",
        "message": "温度超限"
      }
    ]
  },
  {
    "workspace_id": "WORKSPACE_ID",
    "specification_revision": "quality-spec-v1",
    "scenario": "quality",
    "standard": {
      "standard_id": "width-standard",
      "revision": "standard-v1",
      "metrics": [
        {
          "metric_id": "width-check",
          "source_metric": "width",
          "unit": "mm",
          "lower_bound": "9.95",
          "upper_bound": "10.05",
          "lower_inclusive": true,
          "upper_inclusive": true,
          "required": true,
          "calculation": "value",
          "rounding_places": null,
          "requires_review": false
        }
      ]
    },
    "mapping": {
      "format": "wide",
      "identity": {
        "record_id": "record_id",
        "sample_id": "sample_id",
        "measurement_revision": "measurement_revision",
        "measured_at": "measured_at"
      },
      "metrics": [{ "metric": "width", "value": "width_mm", "unit": "mm" }]
    },
    "conversions": [],
    "expected_samples": null
  }
]
```

### 6.1 公共规则

- 目录唯一键为 `(workspace_id, scenario, specification_revision)`。运行读取冻结版本；更改计算、映射或标准时发布新版本并更新绑定，别原地替换旧版本含义。
- 规格来自服务配置，不执行工作流输出或 `manifest` 携带的规则。当前表达式支持受限算术、比较、Python 风格 `and` / `or` / `not`；没有任意函数调用、属性访问、下标或幂运算。
- `Decimal` 阈值和换算系数用 JSON **字符串或精确整数**，例如 `"9.95"`；不要写浮点 JSON `9.95`。测量标量保留原始值与计算值；缺值不补零，布尔值不当数字。
- 规则 / 字段映射本身由配置校验，计算扩展项最多 100000，超限为 `assessment_size_limit`；完整返回封装上限 **512 KiB UTF-8**，超限为 `assessment_output_limit`。不截断证据后宣称完整。来源读取上限和评估输出上限是两个不同预算。

### 6.2 预警

`variables[]` 为表达式变量、来源列、类型三元映射，类型为 `decimal` / `string` / `boolean`。`rules[]` 每项需 `rule_id`、`revision`、`expression`、`severity`、`message`。变量及规则 ID 需各自唯一；变量和规则各 1–256 项。

预警变量配置当前**没有独立 unit 字段或自动单位换算字段**；来源列 `temperature_c` 的单位应在登记的数据契约中确认，阈值也按同单位书写。需要转换时应提供明确、经审查的来源视图 / 读取定义，别让模型猜单位。

空结果为 `no_data` 且 `complete: false`；缺字段 / 缺值保留不完整证据。规则短路能确定超限时可以得到 `issues`，但遗漏的必需证据仍使 `complete: false`，不误报“完整正常”。

### 6.3 质检长表 / 宽表

- `mapping.identity` 的四个值都是**来源列名**，并非实际 record / sample / revision / time。`measured_at` 值应有时区，例如 `2026-09-08T10:00:00+08:00`；ID 与 revision 保留字符串。
- 宽表 `mapping.metrics[]`：`metric` 对应标准的 `source_metric`；`value` 是值列；`unit` 固定单位与 `unit_column` 来源单位列**二选一**。源 record + metric 用结构化身份组合，避免分隔符拼接碰撞。
- 长表可将整个 `mapping` 替换为下述形状，来源查询也应返回这些列；一行代表一项测量。

```json
{
  "format": "long",
  "identity": {
    "record_id": "record_id",
    "sample_id": "sample_id",
    "measurement_revision": "measurement_revision",
    "measured_at": "measured_at"
  },
  "metric": "metric_code",
  "value": "measurement_value",
  "unit": "measurement_unit"
}
```

- 显式换算项形状为 `{"source_unit":"cm","target_unit":"mm","factor":"10","offset":"0"}`；归一化公式为原始数值 × factor + offset，factor 须大于零。缺少所需换算不猜测单位。
- `calculation` 只接收单位归一化后的 `value`。`rounding_places` 省略 / `null` 时不加最终取位；指定 0–28 时按 half-even 取位比较。
- `expected_samples` 可为 `[{"sample_id":"SAMPLE_ID","measurement_revision":"measurement-v1"}]`，用于声明预期集合并发现整个样本缺失。省略或 `null` 时完整性仅覆盖**已观察到的样本版本**，不是整批采集完整性保证。目录是版本化静态配置，当前没有动态生成预期样本清单的管理界面。
- 同设备的 `sample_id + measurement_revision` 分开判定；重复测量身份、错误行映射被拒绝。`failed` 与 `complete: false` 可以并存；`review` 不等于 `passed`。没有数据不产生合格结论。

## 7. 原生服务器 allowlist：默认关闭、逐发布版本登记

在**实际执行原生工作流的服务进程**配置 `ENTERPRISE_MANAGED_TOOLS_JSON`，值是下列 JSON 的正文，不是路径。默认空字符串；空配置不启用身份注入，原生工具走原有路径。它与原生计费 / enterprise licensing 开关无关。

```json
[
  {
    "workspace_id": "WORKSPACE_ID",
    "app_id": "APP_ID",
    "workflow_id": "00000000-0000-4000-8000-000000000001",
    "provider_id": "enterprise/enterprise_device_assessment/enterprise_device",
    "tool_name": "evaluate_device",
    "credential_id": "00000000-0000-4000-8000-000000000002",
    "node_id": "NODE_ID"
  }
]
```

- 七个字段全必需，不接受额外字段、重复条目或 JSON 重复键。最多 256 条、65536 UTF-8 字节，无通配符。
- `workflow_id` 是**已发布工作流版本 UUID**，不是 app ID、草稿 ID、native run ID 或字符串 `latest`。`credential_id` 是**该节点明确选择的原生插件凭据记录 UUID**；二者均需规范小写、非零 UUID 文本。
- `provider_id` 按当前源码声明组合为 `enterprise/enterprise_device_assessment/enterprise_device`；部署后仍以实际安装 / 节点解析到的 provider ID 核对。`tool_name` 为 `evaluate_device`；`node_id` 从实际发布图中取得。
- workspace / app / tool / node ID 使用 1–128 位字母、数字、下划线或连字符；provider ID 必须是三个斜杠分隔的非空标识段。
- 配置匹配的不仅是节点 JSON，还会核对实际解析的 `PluginTool`、原生租户、provider 和 tool identity。显式 credential UUID 防止偷偷落到默认凭据。
- 只有精确匹配且来自 `service-api` 的运行接收元数据；已匹配节点缺少真实 workflow / native run / node execution 身份时失败，不从业务参数补造。编辑器试运行不替代这条链的验收。
- 原生桥在调用时复制本次插件 runtime，把 `__enterprise_execution` 放入私有 credentials，包含 workspace、app、workflow、native run、node、node execution、invoke_from。它不放进动态参数发现或共享工具对象。
- 发布新 workflow UUID 后，显式更新绑定与 allowlist。不存在自动跟随最新发布或未登记版本回退。配置变更按各原生执行进程的配置装载流程发布，无热更新承诺。

## 8. 插件凭据与工具输出

当前源码声明：插件 `enterprise_device_assessment`，provider `enterprise_device`，工具 `evaluate_device`。工具 `parameters: []`；身份、SQL、设备和来源都不由工具参数输入。凭据形状如下，**配置在原生插件 provider 的凭据表单 / 管理边界，不是企业第五份文件**：

```json
{
  "origin": "https://enterprise-api.internal.example",
  "key_id": "managed-key-v1",
  "secret": "REPLACE_WITH_RANDOM_PRIVATE_HMAC_SECRET_AT_LEAST_32_CHARS",
  "expected_app_id": "APP_ID",
  "allow_insecure_http": false
}
```

| 字段                  | 操作要求                                                                                                                            |
| --------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| `origin`              | 固定企业 API 源站，仅 scheme + host + 可选 port；不带接口路径、query 或 URL 凭据。客户端自己追加 `/enterprise/internal/v1/evaluate` |
| `key_id` / `secret`   | 精确对应企业 ExecutionKey；secret 仅在 secret-input 中输入                                                                          |
| `expected_app_id`     | 与企业 key、绑定及原生 session.app_id 一致；缺 session app 身份或不匹配都失败                                                       |
| `allow_insecure_http` | 默认 `false`。HTTPS 场景保持该开关为 `false`；私有网络确需 HTTP 才显式设 `true`，此设置本身不建立私有网络隔离                       |

凭据校验只验证格式，不发起读取或评估。因此“凭据保存成功”不等于路由、密钥匹配或数据源已连通。不要手工配置 `__enterprise_execution`；该字段仅由原生服务器为本次真实调用生成。多余工具参数导致 `unexpected_tool_parameters`。

当前 SDK 包装通过 `create_text_message` 输出完整 `BusinessEnvelope` JSON 字符串，**工具输出是 `text`，不是一个名为 result 的自定义插件输出变量**。工作流 End 节点新增输出字段 **`result`**，直接选择该受管工具节点的 **`text`**。

可审查的[插件源码 ZIP](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/plugin/dist/enterprise-device-assessment-0.1.0-source.zip)已生成；它不是 `.difypkg` 或已签名安装包，也不代表插件已经装入 Dify。

## 9. 绑定、Start 与 End 的操作核对

1. 先确认设备属于当前原生 workspace，取得企业设备 ID；目录 `device_ids` 登记该 ID，并确认设备编码 / 部门正确。存在活跃运行时，设备编码和部门变更受并发保护。
2. 创建带 `evaluate_device` 节点的原生工作流，选择明确插件凭据，记录 node ID。Start 声明实际运行参数和 `enterprise_context` 文本输入；这些变量只服务工作流输入校验，受管工具不以它们作为身份。
3. End 新增 `result`，直接引用该节点 `text`；不要串入 LLM 改写、字符串模板包裹、只抽取 conclusion，或手写假结果。
4. 发布工作流，取得真实发布 UUID；同步登记原生 allowlist、企业 key 的 node_ids、目录与规格版本。
5. 在企业设备对应 `alert` 或 `quality` 场景下建立稳定绑定。后端接口为 `PUT /enterprise/api/v1/devices/{device_id}/bindings/{scenario}`，接收完整 `BindingCommand`，其 `binding` 字段才是 `BindingWrite`。下面是 **HTTP 预警绑定创建** 正文；workspace、device、scenario 来自经过身份校验的作用域 / 路由，不放入正文。

```json
{
  "binding": {
    "app_id": "APP_ID",
    "workflow_id": "00000000-0000-4000-8000-000000000001",
    "specification_revision": "alert-spec-v1",
    "secret_ref": "workflow-service-key-v1",
    "source_id": "equipment-http",
    "source_revision": "source-v1",
    "read_id": "device-alert-rows",
    "read_revision": "read-v1",
    "manifest": {}
  },
  "expected_revision": null
}
```

创建时 `expected_revision: null`；更新时先 GET 同一路径取得当前绑定，再把其真实 revision 正整数填进 `expected_revision`，仍提交完整 `binding`。绑定写入不使用 `If-Match`；该 header 用于设备更新 / 删除，别混用两种并发协议。版本冲突时重新核对绑定，不覆盖并发修改。

质检绑定改用 `scenario=quality` 路由、`quality-spec-v1`、`lab-db`、`device-quality-rows` 以及其对应版本；其他实际身份仍需逐项核对。`manifest` 是非秘密附加配置，不是运行规则来源。当前后端与 BFF 已有绑定写入协议，普通配置向导与完整 operator 配置 GUI 尚未实现；这份请求形状不代表已有可直接完成全套配置的界面。

6. 提交运行时，`expected_binding_revision` 用刚读取的真实整数 revision；给请求一个稳定 `Idempotency-Key`。正文例如：

```json
{
  "expected_binding_revision": 1,
  "parameters": { "batch_id": "000042" },
  "recompute_of": null
}
```

这里的 `1` 是形状示例，不是当前系统 revision 的断言。运行参数匹配目录声明，不传 API key、source URL 或节点身份。

### End.result 必须保留的封装

下段只说明返回结构，ID、摘要和结论均非真实执行结果；**不要把它粘进 End 当静态输出**：

```json
{
  "run_id": "RUN_ID",
  "device_id": "DEVICE_ID",
  "specification_revision": "alert-spec-v1",
  "input_snapshot_digest": "0000000000000000000000000000000000000000000000000000000000000000",
  "result": { "scenario": "alert", "conclusion": "no_data", "complete": false, "evidence": {} }
}
```

外层是 `BusinessEnvelope`，内层 `result` 才是 `BusinessResult`。worker 接受 End 顶层 `outputs.result` 中的完整 JSON 字符串或对象，核对 run / device / specification / scenario / 持久化快照摘要后写回。没快照则 `missing_input_snapshot`；封装缺失或错配则 `invalid_business_output`。原生状态 `succeeded` 或 CLI 返回 0 都不表示质检合格。

## 10. 私有路由部署与短暂关联等待

`POST /enterprise/internal/v1/evaluate` 仅在企业 runtime 配置了非空 keys 后挂载，排除在公开 OpenAPI 之外。它接收 `application/json`，请求体最多 8192 字节，响应 `Cache-Control: private, no-store`；认证使用 `x-enterprise-key-id` 与 `x-enterprise-signature`，不是浏览器 Cookie 或普通登录会话。

部署要求：

- 插件进程经受控内网 / TLS 源站访问该路径；私有入口限制来源与路由。仅隐藏 OpenAPI 不等于网络隔离。
- **该私有路径不经过 Next.js 浏览器 BFF**，也不加入公开 `/enterprise/api/v1/*` 的代理通配规则。公开设备 / 运行 API 仍使用原生账号身份与业务权限。
- 反向代理不改写被签名的 body；不跟随重定向；该插件客户端要求响应不压缩（identity），私有响应不要强制 gzip。超时预算需覆盖实际节点读取，而非提前切断后盲目重发。
- 源站、签名密钥、来源连接与访问策略只配置在服务端。本文没有创建上述网关、网络或防火墙规则。

**短暂 `execution_association_pending` 是唯一自动重试的例外：** 原生节点可能比 worker 的 `workflow_started` 关联提交更快到达企业 API。查不到关联时返回 HTTP 409 与精确 `{"code":"execution_association_pending"}`，且此前没有读取源数据。插件只对此响应做最多 20 次尝试、间隔 0.25 秒、约 5 秒关联等待，整体调用预算 60 秒。

其他 409、鉴权失败、传输错误、5xx 或错误响应格式不触发自动重试；插件不重新派发工作流。超过等待窗口先核查原生 started 关联、发布版本、运行状态、密钥 scope 与时钟。进程断线后的恢复也不等于重新发起读取：已有快照复用；首次 capture 的跨进程并发读取当前没有独立持久化读取租约，应避免给该节点额外设置并行 / 自动重试路径。

## 11. 默认关闭与缺配行为表

| 状态                                                 | 真实行为 / 排查点                                                                                                          |
| ---------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------- |
| 原生 `ENTERPRISE_MANAGED_TOOLS_JSON` 未设或空        | 原生桥不注入；普通工具保持原路径。单独安装本插件也不会凭空获得受管执行身份                                                 |
| 企业 keys 未设 / 空数组                              | 私有 evaluate 路由不挂载；只有 specs 也不会开启                                                                            |
| 企业 keys 非空、specifications 空                    | runtime 构造失败：`Managed execution requires a specification catalog`，发生在 engine 构造前                               |
| 某份 JSON 文件路径错误、格式或模型不合法、超过 1 MiB | 配置加载失败，只指出 `Invalid ENV_NAME`；修正对应挂载与形状                                                                |
| 工作流凭据不匹配                                     | dispatch 在原生请求前终止，`workflow_configuration_unavailable`                                                            |
| fresh 运行没有精确目录版本                           | `registered_read_unavailable`；不回退到其他版本。已有合法快照的重算不依赖 live 目录                                        |
| spec 文件非空但没有本运行规格                        | 评估阶段 `assessment_specification_unavailable`；此前 capture 可能已经成功，应核对持久化快照而非认定未读。不会猜测最新版本 |
| allowlist 不匹配 / 私有 metadata 缺失                | 本插件报身份错误；不从 tool 参数或 enterprise_context 恢复身份                                                             |
| HMAC / 有效期不正确                                  | 401；key scope 与 workspace / app / node 不符为 403；不反复派发                                                            |
| 关联尚未提交                                         | 仅精确 association-pending 409 有有界等待；等待前不读源                                                                    |
| private 请求超 8 KiB / 内容类型错误 / 超时           | 分别 413 / 415 / 504；通用内部异常为脱敏 503，不伪装成功                                                                   |
| 源读取不完整 / 评估输出超限                          | 不写“正常完整”报告；保留失败 / 不确定边界，核对明确 reason 与审计                                                          |

## 12. 单次 worker 与失联核对

以下是已实现 CLI 的**手工操作示例**，本轮没有执行。前提是部署依赖、四份配置、企业迁移、插件与原生 allowlist 均已在目标环境完成验收；从项目根目录使用相同配置运行：

```powershell
uv run --project enterprise/api --no-sync python -m enterprise_platform.worker dispatch --workspace-id WORKSPACE_ID --run-id RUN_ID
uv run --project enterprise/api --no-sync python -m enterprise_platform.worker reconcile --workspace-id WORKSPACE_ID --run-id RUN_ID
```

- `dispatch` 仅处理指定排队运行：先 claim / 固定 nonce，再调用一次原生工作流；不扫描队列、不开守护进程、不自带调度或失败重派。
- `reconcile` 仅查询已经关联的原生运行；不 POST 一个替代工作流。`uncertain` 保留单设备 / 场景执行通道；缺 native run ID 的不确定情况需要核对，重复 dispatch 并非恢复办法。
- 资源关闭后才输出 `{"run_id":"...","status":"..."}`；错误只输出公开 code。退出码：`0` 本次操作完成（仍须看状态 / 业务结论）、`1` 配置或操作失败、`2` 用法错误、`3` uncertain、`4` failed / cancelled。
- 同一终态再次回传只允许相同摘要，不覆盖既有报告。明确执行失败与业务 `failed` 是两回事：前者没有可信业务结论，后者是正常完成了不合格判定。

浏览器提交失联时，先用当前账号 / workspace 的权威查询 `GET /enterprise/api/v1/run-requests/lookup?request_key=...` 核对原 key，不用“最近 5 条运行”推断。查到记录后核对冻结设备 / 场景 / 绑定 / 参数；404 只表示此刻未查到，原 key、参数和 revision 仍保留。同 key 的真实原请求即使绑定已更新也可恢复，不同请求内容冲突。设备 UI 已有“核对运行”，但未实现整页刷新后的 attempt 持久化恢复。

## 13. 上线前验收清单与现存边界

- [ ] 四份文件在目标进程可读，生产秘密已替换并限制权限；不同密钥职责没有混用。
- [ ] 企业库与源库分开，企业 schema 已独立迁移；源账号实际只读；没有改动原生账号或原生表。
- [ ] workspace / app / 发布 workflow UUID / node / 原生 credential UUID / secret_ref / key_id / 来源四元组 / spec revision 逐项对齐。
- [ ] 原生固定发布 UUID 的 Service API 路径在目标版本、plan / billing 条件下实际可用，非“只看源码即已部署”。
- [ ] 私有路由仅插件网络可达，不经过 BFF；TLS、时钟、body、压缩与超时策略符合客户端边界。
- [ ] 用受控测试记录检查：零行、缺字段、前导零、精确小数、错误单位、时区、质检 sample revision 分离、超限、已知失败与不完整并存。
- [ ] 核对一次真实 started 关联、一次捕获、End 原文输出、终态摘要；再测试失联查询、只读 reconcile 与终态重复回传。

目前有源码级、单元及模拟传输契约验证；真实数据库集成测试仅 CI 运行，本机不把 skip 计作通过。仍待部署环境实际验收：插件打包 / 安装、原生进程配置发布、网络连通、真实 DB 权限与生产端到端。

当前没有一键生成 / 导入的完整工作流模板，也没有读取目录、HMAC keys、判定规格、原生 allowlist 的统一配置 GUI；provider 凭据表单不等于这些管理功能。尚无自动队列消费调度、capture 持久化读取租约或整页刷新后的 UI 未决请求恢复。本说明只覆盖当前受管设备纵向链路，不替代企业全部模块建设方案。

## 源码核对入口

以下链接对应本次隔离工作树；搬迁仓库后按同名路径查找：

- [企业配置工厂](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/api/src/enterprise_platform/bootstrap.py)
- [读取注册 / 实际捕获](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/api/src/enterprise_platform/application/input_capture.py)、[来源类型](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/api/src/enterprise_platform/domain/data_sources.py)
- [版本化判定规格](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/api/src/enterprise_platform/application/assessments.py)
- [受管执行认证](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/api/src/enterprise_platform/application/managed_execution.py)、[私有 HTTP 路由](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/api/src/enterprise_platform/http/internal.py)
- [原生 allowlist 纯策略](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/core/workflow/enterprise_execution.py)、[原生工具调用桥](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/core/workflow/node_runtime.py)
- [插件 provider 配置](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/plugin/provider/enterprise_device.yaml)、[工具 SDK 包装](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/plugin/tools/evaluate_device.py)、[签名客户端](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/plugin/managed_device_plugin/client.py)
- [派发 / End.result 校验](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/api/src/enterprise_platform/application/dispatcher.py)、[单次 worker](F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/api/src/enterprise_platform/worker.py)

## 受管数据源配置（增量 0002）

数据源管理使用独立企业数据库里的 `enterprise_source_heads` / `enterprise_source_versions`，不修改 Dify 原生表。已有 `0001` 原样保留；`0002` 只在检查既有四张企业表的实际结构后添加两张源表。保存数据源不连接源数据库或 HTTP 服务，返回 `connection_status=not_tested`；实际读取仍发生在已关联的原生工作流节点执行时。

### 部署侧配置

- `ENTERPRISE_SOURCE_ENCRYPTION_KEYS_JSON`：结构为 `{active_key_id, keys:[{key_id,key}]}`。每个 `key` 为 32 字节密钥的 Base64/Base64url 文本（含填充，共 44 字符），最多 16 把密钥。配置由部署密钥管理提供，页面只提交数据源凭据，不接触这组加密密钥。
- `ENTERPRISE_SOURCE_ALLOWED_ENDPOINTS_JSON`：管理员明确登记允许的源端点，按类型、主机、端口及数据库方言/HTTP scheme 匹配；不使用通配符。数据库项形如 `{kind:"db",host:"db.internal",port:5432,dialect:"postgresql",allow_insecure:false}`；HTTP 项形如 `{kind:"http",host:"api.internal",port:443,scheme:"https",allow_insecure:false}`。两个环境配置分别限 64 KiB。明文 HTTP 或禁用数据库 TLS 另需部署侧 `allow_insecure:true` 与页面明确选择同时满足。
- 缺加密密钥或端点策略时，写入禁用并返回可识别原因；现有静态读取目录仍保留原行为。已保存的密文使用其原始 key ID 解密，轮换时要保留历史密钥；移除旧密钥会阻止使用相应未采集历史配置进行新读取，不自动改用新版本。
- 每次受管历史源重新解析都会检查当前端点策略；已固化的数据快照重算不触发外部读取。

查看增量 SQL（不执行迁移）：

```powershell
uv run --project enterprise/api --no-sync python -m enterprise_platform.persistence.migrate_sources --print-sql
```

实际 `--apply` 必须由部署步骤明确给出企业数据库环境变量及 `--expected-database enterprise_<name>`，先完成备份与 `0001`。本地开发验证没有执行此命令；真实事务与迁移验证列入 CI 独立数据库任务。

### 页面与接口约定

- `GET /enterprise/api/v1/sources/capabilities` 返回 `can_manage`、`write_enabled` 和未就绪原因；源写入角色为原生 workspace owner/admin，普通编辑者不因设备管理权限自动获得源凭据修改权限。
- 列表/详情只返回公开配置及凭据是否已配置，不返回密码、Authorization 值或密文。
- 创建使用 `Idempotency-Key`；更新使用详情 ETag 的 `If-Match`，冲突不覆盖他人的修改。每次更新产生新的不可变 source/read 版本，已排队运行继续引用原版本。
- 数据库创建需用户名/密码同时提供；编辑时两项同为 null 保留原凭据。修改主机、端口、数据库、方言或 TLS 设置时重新提供；不是把掩码星号当真实密码发送。
- HTTP headers：创建 null 表示无头；编辑 null 表示保留；空数组表示明确清空。目标变更时不悄悄复用旧认证头。
- 设备/部门约束与声明参数继续进入现有只读 SQL/HTTP 读取合同。保存成功不代表 SQL 字段或数据已通过现场连通验证。

默认工作流资产位于 `enterprise/workflows/default-alert.yml` 与 `enterprise/workflows/default-quality.yml`；设备缺少绑定时可下载对应模板。它们没有插件凭据，导入后仍需选择凭据、发布、登记受管版本并完成绑定。一键配置向导与真实联调仍是后续交付项。

## 默认工作流草稿创建（S5，未发布、未绑定）

设备的预警/质检面板新增创建默认草稿入口。选择当前设备可用的数据源后，服务器冻结 source/read 版本和预期绑定版本；页面保留同一次操作的请求键，历史列表支持查找已持久化操作。浏览器中的待请求键只存在内存中，不承诺跨重载持久保存。源配置更新或移出设备后，新的操作必须重新选择当前版本，已有请求的快照保持不变。

### 部署条件

1. 在独立企业数据库完成 0001、0002 和备份，再通过守卫 CLI 应用 0003。服务启动不会自动执行迁移。
2. 原生 API 与企业 API 同时配置 `ENTERPRISE_WORKFLOW_SETUP_ENABLED=true`；两端默认均为 false。仅配置企业侧时，原生能力预检失败，不调用创建。
3. 保持原生登录、CSRF、账单、账号初始化和导入权限检查。企业后端只向固定 Console 地址转发经过过滤的认证信息，认证内容不进入操作表或 DSL。

只查看 SQL：

```powershell
uv run --project enterprise/api --no-sync python -m enterprise_platform.persistence.migrate_workflow_setups --print-sql
```

真正的部署步骤需显式使用 `--apply --url-env <数据库环境变量名> --expected-database enterprise_<名称>`，先核对备份、目标数据库和先决表结构。本轮未执行数据库迁移。CI 先运行原有六表 smoke 检查，再应用 0003，最后运行 setup 集成用例。

### 状态和恢复

- `queued` → `importing` 使用版本 CAS 和导入 nonce，只有领取者发起原生导入。相同请求键、actor 和内容恢复原操作，不创建新应用。
- `draft_ready`：原生导入完成，可以打开原生编辑器；有警告时保留警告原因。此状态不是已发布或可执行。
- `confirmation_required`：等待原生导入确认，不自动确认。
- `uncertain`：导入响应丢失、作用域确认缺失或协议异常；禁止自动重复原生 POST。
- `failed`：明确失败；保留记录和原因码。
- 崩溃留下的 `importing` 不重新领取。完整自动对账仍待后续开发。

创建和领取要求设备仍有效；领取后设备被软删除时，原领取者仍可持久化已发生的原生副作用结果和 app/import ID，以免丢失追溯信息。该收尾仍校验工作区、nonce、状态、版本并原子写审计，不触发新的原生调用。常规服务读取和请求恢复继续校验设备有效性。

公开 API 仅返回创建记录，不返回 cookie、密钥、请求摘要或 nonce。原生 managed 导入只增加严格限定的 expected-workspace header 分支；无该 header 的普通导入保持原有行为。自动发布、插件凭据配置、精确版本登记、规则与最终绑定仍是后续步骤，创建草稿不代表整条设备业务流程就绪。

## 精确发布与执行凭据库（S6）

本阶段提供完整配置链路所需的原生发布接口、调用适配器和加密执行凭据存储；尚未将所有后续步骤接成设备页面上的一键发布绑定。不要以这里的发布响应替代实际插件就绪、版本登记、规则绑定或运行验收。

### 精确发布

原生 Console 的既有 publish POST 保持普通调用的原有行为。managed 调用同时提供 `X-Enterprise-Expected-Workspace` 和 `X-Enterprise-Expected-Draft-Hash`，要求原生 `ENTERPRISE_WORKFLOW_SETUP_ENABLED=true`；仅有一个 header 会被拒绝。使用本次已登录 Account 的工作区并与应用工作区相互核对，随后在同一原生事务内锁定并刷新指定应用的草稿、比较 hash、执行原有凭据策略/图结构/Agent/计费检查并发布。

managed 成功响应包含本次实际创建的 `workflow_id`、`app_id`、`accepted_draft_hash`，以及 `X-Enterprise-Workspace` 响应头；不通过 GET latest 推断版本。**原生 unique_hash 仅覆盖 graph**，不覆盖 features 和变量值，这些字段仍使用锁定草稿的值。原生事务真实并发仍待 CI/部署联调证明。

企业 `DifyWorkflowPublishClient` 先要求能力响应中的 `publish_enabled=true`，再向固定 Console 地址发送一次 POST。工作区/应用/操作使用规范非空 UUID，响应还须匹配应用、工作区和 hash。丢失响应、协议变化、未确认的错误保持 uncertain，不自动重新发布。operation marker 用于追溯，不等于原生接口具备幂等语义。调用者仍须先持久化操作领取；后续持久化发布编排尚待接入。

### 执行凭据库部署与生命周期

- 独立企业数据库按 0001→0002→0003→0004 迁移；0004 CLI 校验七张先决表的结构与实际目标数据库，服务启动不建表。
- 显式设置企业 API 的 `ENTERPRISE_WORKFLOW_CREDENTIAL_KEYS_JSON`，结构为 `active_key_id` 和 `keys:[{key_id,key}]`；每个 key 是 32 字节随机材料的 URL-safe Base64 表示。通过部署密钥管理渠道注入，不提交实际值。
- 此设置与 source encryption keyring 独立；AES-256-GCM 的认证数据包含用途、key ID、workspace/app/ref、版本、活动状态和时间。篡改状态或换租户/应用/ref 后解密失败。
- 未配置此项时保留现有 `ENTERPRISE_WORKFLOW_CREDENTIALS_FILE` 静态模式。两种模式同时配置会明确报错；凭据缺失、撤销、密文损坏或旧加密 key 缺失时，不回退到静态 token。
- `SqlAlchemyWorkflowCredentialVault.register` 由服务端配置编排调用：相同 workspace/app/ref 和相同 token 可重放；同 ref 的不同 token 冲突。token 轮换使用新的 ref，已有运行继续引用原 ref。
- `revoke` 和 `reseal` 需要 expected_revision，更新和审计同一事务提交。reseal 只轮换加密 key、不改变 token、不重新激活已撤销的 ref。移除旧 key 前应完成逐 ref reseal 并验证旧数据可读。
- 解析凭据同时限定 workspace/app/ref；执行和运行恢复都重新解析，不在进程内无限缓存已撤销 token。已发出的 HTTP 请求不因后续撤销而被追溯取消。
- 公开 API 不提供凭据库读写或 token 导出入口；调用 vault 的未来配置服务必须保留业务权限与原生账号/应用资格检查。底层 repository 接收 actor 仅用于审计，不自行替代授权层。

查看迁移 SQL（不连接数据库）：

```powershell
uv run --project enterprise/api --no-sync python -m enterprise_platform.persistence.migrate_workflow_credentials --print-sql
```

正式部署仍需备份，并显式指定 `--apply --url-env <企业数据库环境变量名> --expected-database enterprise_<名称>`。本轮只收集七项凭据事务 CI 用例，未执行数据库迁移、创建真实原生 token 或启动后台服务。

### 插件凭据准备边界（S7）

DifyWorkflowPluginCredentialClient 仅在 credential_setup_enabled 和匹配工作区成立时操作固定 Console。provider 必须匹配已配置的 plugin_unique_identifier，并提供 evaluate_device 与 api-key；四个 provider 路由要求工作区响应确认。该能力仍受默认关闭的 ENTERPRISE_WORKFLOW_SETUP_ENABLED 控制。

插件 HMAC 签名配置独立于 Service API token 凭据库。客户端用每次操作的固定名称创建一次凭据；成功后的归属、公开配置和唯一 UUID 回读才返回 credential_created。同名旧记录、响应丢失和模糊结果保持 uncertain，不通过掩码值证明秘密一致。原生 100 项限制不被绕过。

prepare_credential_patch 仅准备完整草稿请求体，保留其他节点、样式与变量标识；没有执行原生同步。由于原生 hash 只覆盖 graph，下一环节必须防止用旧 features/variables 快照覆盖并发编辑，且处理 GET 图的 Agent 响应投影。当前没有自动部署 HMAC key、登记新发布版本或最终绑定设备，不能把 credential_created 当作可执行或已发布状态。

### 凭据增量绑定（S8）

原生 draft POST 的 managed 分支要求 `X-Enterprise-Expected-Workspace` 与 `X-Enterprise-Draft-Operation: bind-assessment-credential`，只接受 JSON `{draft_id,hash,credential_id}`。能力字段 `draft_credential_bind_enabled` 与现有 setup flag 同时默认关闭；普通保存与 GET 行为保留。

事务锁定并刷新精确工作区、应用和草稿 ID/version，比较旧 graph hash，再从数据库当前 graph 修改唯一 assessment 节点的 credential_id。features、环境变量和会话变量不重新赋值。原生图/功能/Agent 校验与同步事件保留，成功回执在 commit 失效 ORM 前捕获精确 app/draft/credential ID、accepted_draft_hash 和新 hash。

DifyWorkflowDraftClient 只发一次增量 POST，响应须精确匹配作用域和标识；未确认的 POST 保持 uncertain。draft_bound 只表示这次草稿凭据修改得到确认，不表示插件密钥已部署、已发布或已绑定业务设备。后续发布使用回执的新 graph hash，不从重新获取的 latest 版本推测成功。

该锁不改变普通编辑器写入策略；已经读取旧图的普通保存可能在之后覆盖 graph。后续发布的 hash 冲突必须展示，不自动重写用户图。受作用域校验的草稿 ID/hash 获取、持久化阶段领取/恢复、签名 key 部署、版本登记及最终设备绑定仍待接入。

### 受作用域校验的草稿元数据（S9）

DifyWorkflowDraftReadClient 先验证 draft_read_enabled 和工作区，再 GET 原有 draft 地址，发送配对 expected-workspace 与 operation=read-assessment-draft。原生仅返回 app_id/draft_id/hash/version=draft，附工作区确认。客户端禁止快照额外字段，只保留不可变的工作区/应用/草稿 ID 与 hash。只读观察不等于后续写入权利；绑定与发布继续执行各自的 hash/身份检查。

普通 GET 编辑器投影、权限装饰器及缺失草稿 404 保持原样。该读取没有创建凭据、部署密钥、绑定设备或恢复丢失的发布回执；这些步骤需独立持久化编排与精确核对。

### 配置日志存储与推进（S10/S11）

新增独立enterprise*workflow_provisioning表，不改写0003导入状态。显式0005迁移仅接受已验证0001–0004八表结构及独立enterprise*\*数据库；先检查实际database，再在advisory事务锁与超时内创建日志表。服务启动不自动迁移。CI编排在credential八表smoke之后执行0005，再执行provisioning事务/九表smoke；本地只收集集成用例。

WorkflowProvisioningService的start冻结setup/config引用，advance每次只执行一个阶段：持久化claim返回并精确校验后调用执行器，之后按原nonce与revision完成收尾。取消与无法确认最终写入不会触发自动重发；明确的rejected/uncertain阶段停止推进，需要后续恢复策略。收尾仍可记录设备删除前已发出的结果。

这套服务尚未在bootstrap/HTTP/UI启用，配置客户端解析器、动态签名key、原生Service API token与精确执行登记仍待实现。新表和unit doubles通过不等于正式环境已迁移或流程已可运行。

### S12 用户 API 与按应用签名配置

已把 WorkflowProvisioningService 接入 create_runtime/create_app，默认关闭。启用需同时设置 ENTERPRISE_WORKFLOW_SETUP_ENABLED=true、ENTERPRISE_WORKFLOW_PROVISIONING_ENABLED=true，并通过 ENTERPRISE_WORKFLOW_PLUGIN_PROFILES_FILE 指向服务器端 UTF-8 JSON 数组文件。启动只组装依赖，不执行迁移、数据库连接或原生 HTTP 操作。

每个 profile 包含 workspace_id、config_ref、config_revision、origin、expected_plugin_unique_identifier、master_key_id、master_secret，另有默认 false 的 allow_insecure_http。master_secret 为服务器密钥，禁止放进浏览器、Git 或公开响应；配置文件应由部署密钥挂载并限制读取权限。按工作区、应用、配置引用/版本和 master_key_id，以独立版本标签派生插件 key_id 与签名 secret。同 master_key_id 必须对应同一密钥。配置版本部署后保持不可变；轮换使用新 master_key_id/config_revision 并保留旧版本供既有日志解析。当前不支持热更新或密钥材料持久化快照。

接口（已有身份、Origin/CSRF 与管理权限校验继续生效）：

- POST /enterprise/api/v1/workflow-setups/{setup_id}/provisioning：Idempotency-Key + {config_ref, config_revision}，创建冻结操作。
- GET /enterprise/api/v1/workflow-provisioning/{provisioning_id}：读取公开阶段状态。
- POST /enterprise/api/v1/workflow-provisioning/{provisioning_id}/advance：{expected_revision}，推进一次；原生 session 仅从当前已认证请求临时取得。

响应 private,no-store；错误只返回公开错误代码。前端代理只允许上述三个精确方法/路径组合，生成契约客户端直接调用，不对失败写入自动重试。尚未加入配置向导 UI 或 profile 选择列表。

重要：派生执行 key 不等于登记执行 key。ExecutionAuthenticator 仍使用既有显式配置；Service API token 创建、精确应用/工作流/节点执行登记与最终设备绑定仍待实现。四阶段完成状态仍是 published_pending_enrollment，不作为可运行或部署成功证明。

### S13 公开配置选择与持久化历史

GET /enterprise/api/v1/workflow-setups/{setup_id}/provisioning-profiles 在管理权限、setup/workspace、可访问设备和draft_ready检查后返回具名配置版本。服务器profile支持可选display_name（非空，最多128字符），旧配置缺省使用config_ref；修改名称不更改派生密钥。公开字段固定为config_ref/config_revision/display_name。

GET /enterprise/api/v1/workflow-setups/{setup_id}/provisioning?offset=0&limit=20 返回当前actor在该setup下的Page。workspace/setup/actor过滤在数据库count与分页之前；响应再核对完整作用域。最大limit100。用于刷新后找回操作，不把分页当作配置全局互斥或原生操作重试授权。两个GET保持private,no-store；不会读取或写入原生凭据。

真实执行仍需后续三项激活：原生持久化精确tuple登记及运行时解析、企业侧基于已激活登记的派生key解析、受作用域校验的Service API token获取并入加密vault。最终设备BindingWrite必须核对冻结source/read/spec和expected_binding_revision CAS。当前静态登记与key字典不会因profile公开选择而自动改变。

### S14 精确作用域的原生 Service API token

新增service_api_token_issue_enabled能力，仍由默认关闭的ENTERPRISE_WORKFLOW_SETUP_ENABLED控制。企业客户端先检查enabled/该能力/当前workspace，再向原有apps/{app_id}/api-keys发一次无body POST，带X-Enterprise-Expected-Workspace和X-Enterprise-Key-Operation: issue-workflow-key。原生先校验配对header、非空canonical UUID与原生tenant；之后沿用原资源归属、权限装饰器、10-key上限和生成器。普通app/dataset的key功能不变。

managed成功201返回原ApiKeyItem字段加app_id、X-Enterprise-Workspace与private,no-store。客户端只接受精确app、workspace确认、canonical token ID、type=app与原生app-加24字符格式。token以SecretStr临时持有并排除repr/公开序列化；预检失败抛TokenIssueRejected，POST后任何未确认结果保留uncertain且不保留返回的token/IDs，不GET列表认领、不自动重试。取消继续传播。operation header只用于关联，不代表原生幂等性。

客户端尚未接入持久化领取和vault注册：调用前需先提交阶段claim，确认返回后立即以新不可变secret_ref入既有加密vault；丢响应/写入不确定不能据此重复创建key。这一步也不证明执行登记、spec/source/read核验或最终设备绑定已完成。

### S15 精确已发布版本核验

既有publish GET新增managed只读分支，需配齐X-Enterprise-Expected-Workspace、X-Enterprise-Expected-Workflow、X-Enterprise-Expected-Graph-Hash、X-Enterprise-Publication-Operation: read-assessment-publication。capability publication_read_enabled沿用默认关闭的setup开关。原生按精确tenant/app/workflow ID读取非draft版本并核对graph-only hash，不以当前latest指针代替指定版本。

核验要求graph节点ID唯一、恰好一个固定builtin evaluate_device工具且node_id=assessment、credential_id为canonical非零UUID。响应只有app/workflow/hash/provider/tool/credential/node与workspace确认，private,no-store；不返回graph、features、环境变量、token或执行秘密。企业reader同时比较预期credential UUID，任一不匹配或读失败即停止，不重试、不POST、不回退latest。

该结果是某个已发布图的快照证据，不证明它仍是当前发布指针、插件凭据仍可用、执行登记已激活或最终设备已绑定；业务运行继续使用精确workflow UUID。后续登记仍存企业独立数据库，不向Dify原生库塞业务表。
