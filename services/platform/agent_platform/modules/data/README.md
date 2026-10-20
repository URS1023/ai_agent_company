# 数据接入与分析模块

读者为数据接入、后端开发与测试人员。入口`router.py`，执行边界`service.DataService`，静态SQL策略`sql_safety.py`。

## 当前可运行边界

平台通过`POST /datasources`新建专用SQLite文件，通过`POST /datasets`导入CSV。文件位于当前平台`data_dir/imports`，只识别数据库记录中登记的`source_<uuid>.sqlite3`，不接收用户任意文件路径、原系统配置或远程连接串。

默认只启用可执行的隔离接入。MySQL、PostgreSQL与固定GET的HTTP JSON连接器已提取组装，必须独立配置凭据引用、开启外部连接开关，并由管理员按实际主机/端口/库名/服务归属/有效期/范围批准；回环地址也不自动获准。未配置、未批准或已阻断时不连接，不自动回退。外部协议只做Mock验证，未连接任何已有业务数据库。CSV表全按TEXT保存原始精度，查询可显式CAST，质检通过Decimal判定。

Elasticsearch适配已追加源码，4项Mock协议验证通过，**尚未连接真实ES验收**。只接受单个非隐藏索引；同AST权限后先`/_sql/translate`，再有限`/_sql`，不执行任意DSL/脚本或游标翻页；未知代价聚合拒绝，已分配游标仅关闭释放。Mapping固定读取登记索引。没有启动、连接或测试现有ES。协议依据Test5的`analytics/execution.py`原执行链与[Elastic官方SQL](https://www.elastic.co/docs/api/doc/elasticsearch/operation/operation-sql-query)、[Translate](https://www.elastic.co/docs/reference/query-languages/sql/sql-translate)。

## 接口与预算

所有路径均以`/api/v1`开头。

| 路径 | 方法 | 作用 |
|---|---|---|
| `/datasources`、`/{id}` | GET/POST、GET/PATCH/DELETE | 新建、修改、软删受控源；字段权限在PATCH的field_roles中配置 |
| `/datasources/{id}/connection-checks` | POST | 对登记的隔离文件执行一次小量读取，不自动重试 |
| `/datasources/{id}/metadata`、`/metadata-syncs` | GET、POST | 返回平台登记的表/字段快照，并按当前角色过滤 |
| `/datasets`、`/{id}` | GET/POST、GET/PATCH/DELETE | CSV导入与数据集元数据维护，不修改原业务数据 |
| `/queries/natural-language` | POST | 单次配置OpenWebUI补全生成候选，再走同一AST/字段/预算；无模型返回503 |
| `/datasources/{id}/target-approvals` | POST | 管理员明确目标与最多24小时范围；配置变更即失效，异常后需显式重新批准 |
| `/datasources/{id}/recoveries` | POST | 管理员核查异常后显式恢复隔离源，恢复动作不派发查询 |
| `/queries`、`/{id}`、`/{id}/exports` | GET/POST、GET、POST | AST授权、计划预检、有限执行、持久结果与CSV导出 |
| `/business-metrics`、`/data-relations`、`/query-templates` | CRUD | 指标口径、关联与查询模板元数据；不执行任意脚本 |
| `/data-quality-checks` | POST | 对完整查询快照检查必填与唯一性，截断结果不宣称完整通过 |
| `/data-processing-jobs`、`/{id}` | POST、GET | 字段选择、空值剔除、按字段去重；拒绝截断输入和未登记操作 |

- 单次CSV最多100列、10000行、5MB文本；字段长度最多10000字符。
- 查询只允许一条只读SELECT/非递归WITH、函数白名单、授权表/字段与命名参数。拒绝写入、系统表、文件、扩展、跨库、变量和管理指令。
- AST后再做EXPLAIN QUERY PLAN；最多3张表，关联估算不超过50万行组合。单条最多2秒/20万SQLite虚拟机指令，返回最多10000行，多取1行检测截断。
- 发生执行错误或预算终止，不重试、不扩大超时、不切换源。原业务事务回滚后通过`after_rollback`独立标记该源blocked；重新接入应先调查异常。
- 当前角色权限撤销后，旧查询、处理、制造与报表快照也按当次已展开字段重新检查，不能借历史SELECT星号访问被撤销列。

## 提取来源与修改位置

`sql_safety.py`提取自用户Test5快照`backend/app/modules/analytics/sql_safety.py`，保留AST字段限定与驱动参数绑定，错误适配为平台DomainError。增加数据库类型时应新增明确适配器、连接边界与方言测试，不绕过此安全层。

连接边界参考并精提自Test5 `analytics/connections.py`、精度与限量返回参考`execution.py`；不复制旧连接池、配置与密钥。PostgreSQL使用只读事务/statement_timeout/lock_timeout和EXPLAIN JSON，MySQL使用只读事务/MAX_EXECUTION_TIME和EXPLAIN JSON；HTTP只允许固定GET、不跟随重定向、不继承代理，响应限8MB/10000行，随后在本次内存表中执行已授权SQL。

`tests/test_data_controlled.py`和`test_data_connectors.py`只使用新建临时SQLite、小量单条查询、HTTP MockTransport或驱动工厂Mock。自然语言用Mock验证一次补全、schema和危险SQL拒绝，不把Mock当真实模型效果。模拟超时测试不涉及真实数据库超时或外部连接。
