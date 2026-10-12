# 批次十四 KG-001–003：图谱属性约束实施证据

日期：2026-10-04。工作树：`E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-task-lifecycle`；分支：`codex/major-task-lifecycle`。开始基线：`057ce1fd7ec2137df732c6dd75c1ff90bf7119d1`。完成检查时共享分支 HEAD 已由其他参与者推进至 `782d346bc1e3df6a0e63dd199c0157c9ec47476b`；本代理没有提交。

本项已完成并冻结。产品只修改 `backend/app/modules/knowledge_graph/schemas.py`、`backend/app/modules/knowledge_graph/graphs.py`；新增 `tests/backend/test_graph_property_contracts.py`。仅当前 ignored 报告/日志/XML 为辅助产物。没有修改旧 major-capability-contracts 工作树、analytics 池、模型/迁移、正式 docs 或门禁，也没有回退其他参与者变更。KG-004–008 与图谱删除引用不属于本次实现。

## 结果

**最终 73 passed、0 failed、0 errors、0 skipped，69.582 秒。** 构成：新属性专项 43、既有知识图谱 6、既有 Bot HTTP 授权 18、既有离线 Bot 动作 6。进程退出码 0。`git diff --check` 通过，只提示仓库换行将由 LF 转为 CRLF。最终测试报告 11346 条已有 datetime.utcnow 等弃用告警，不影响断言。

红绿证据保留各阶段真实结果，不将后来扩充的用例说成最初全部取过红灯：

| 证据前缀（均在 .implementation） | 结果与意义 |
| --- | --- |
| graph-properties-red | 修改产品之前初始 22 failed，28.81 秒；唯一值、物化回滚、日期、原生/旧枚举、重复定义、固定修订全部按目标断言失败 |
| graph-properties-green-initial | 初始 22 passed，26.43 秒 |
| graph-properties-green | 扩充首轮 65 passed / 1 failed；新增跨工作区 fixture 漏 WorkspaceMember.id/created_at，尚未到产品断言 |
| graph-properties-green-final | 仅补齐 fixture 后 66 passed，79.83 秒 |
| graph-properties-history-red | 新增历史坏日期逐条修复边界，1 failed，2.74 秒；旧实现返回“历史记录...请先修复”422，导致多条坏记录互相阻塞 |
| graph-properties-verified | 修复该边界后 67 passed，75.16 秒 |
| graph-properties-precision-red | root 复核指出旧字符串 number 精度问题后补红：4 failed、2 passed、37 deselected，6.85 秒；两项通过是实体/关系真实 UPDATE 的 mapper 版本 WHERE |
| graph-properties-verified-final | 精度修复后的最终完整结果：73 passed，69.582 秒 |

## 产品行为与准确位置

### KG-001：唯一属性成为实际写入约束

`graphs.py:217` `_lock_graph_write` 使用现有 visible_records 动作/工作区/资源范围，按主键取得 KnowledgeGraph 的 FOR UPDATE 锁并 populate_existing；保留 require_resource 和 deleted 状态检查。`save_entity`（`:283`）和 `save_relation`（`:355`）统一先进入该入口，再解析固定本体定义、核对目标版本和检查唯一值。应用内 GraphEntity/GraphRelation 的构造写入均汇聚在这两个函数；抽取物化继续调用它们的 commit=False 路径。

`graphs.py:232` `_enforce_unique_properties` 在父锁内，按工作区 + 图谱 + 概念/关系类型查询未删除记录，排除本次更新自身，按主键顺序 FOR UPDATE + populate_existing 读取候选。不能将其改成普通 SELECT：请求可能早已建立 MySQL RR 一致性快照，父锁之后仍需当前读取才能看到先前持锁事务刚提交的唯一值。只在成功校验后添加/修改目标记录。

唯一比较口径：

- 缺失和 null 不占用唯一值；required 仍先拒绝缺失/null/空字符串。非必填的空字符串是实际唯一值。
- 字符串精确、区分大小写；number 使用 Decimal(str(value)) 比较，1 与 1.0 相等；布尔不冒充整数。
- date 使用合法日期；datetime 带时区时按同一时刻比较，无时区时按墙钟值比较，二者不混同。
- json 仅接受对象/数组，以排序键且禁止非有限数的 JSON 序列化作为比较键；对象键顺序不影响比较，数组顺序保留。JSON 内数值的文本表示（如 1 与 1.0）仍保留差别，不声称实现了完整 JSON 数学等价规范。
- 删除记录不占用属性唯一值；原数据库业务键/关系三元组唯一约束没有修改，软删后复用相同业务键仍受其既有规则限制。
- 旧版可能保存非法日期等坏值。已严格验证的新值不可能等于无法得到合法比较键的旧值，这种旧值跳过唯一比较，允许多条坏记录逐条修复；本次提交的非法值仍 422，合法值冲突仍 409。未批量修改旧数据。

物化时 commit=False 只 flush，父锁由外层物化事务持有到统一提交/回滚，未增加中间提交。专项通过 SQLAlchemy after_cursor_execute 观察到冲突之前确实有实体/关系 INSERT，再核对失败后实体/关系行数回到零、run 保持 review、无成功 materialization 摘要、图谱 version/updatedAt 不变。不是只在首项失败就声称验证整批回滚。

### KG-002：日期、原生类型与枚举一致

`schemas.py:19` `property_value_key` 是定义/执行/唯一性共用的纯比较规则。date 仅接受 YYYY-MM-DD 并实际检查日历；datetime 要求 T 和时分，可带秒、1–6 位小数、Z 或合法小时/分钟偏移。日期不存在、只有日期没有时间、25 点、偏移分钟 60 均拒绝。保持原业务属性值，不在保存时隐式转成字符串或替换时区文本。

`schemas.py:45` `normalize_property_enums`、`:81` AttributeDefinition 允许严格原生 string/int/float/bool 枚举；类型按属性声明校验。继承原枚举去空白、去空项口径，归一后重复值拒绝。数字与布尔不因 Python 的 True == 1 相混。

兼容旧字符串枚举：integer 接受严格十进制整数字符串；number 接受 JSON 数字语法；boolean 接受小写 true/false。旧快照只在新建的 AttributeDefinition 对象中解释，不改写 snapshot；新定义保存解释后的原生标量。无法解释或歧义定义明确 422，不回退为无枚举限制。

**精度补充：** number 字符串先 Decimal 解析；可支持的有限数中，整值转为精确 int，非整值只有 Decimal(str(float(value))) 与原十进制值相等才接受，否则 422。9007199254740993.0 不再经 json.loads 浮点舍入，允许精确整数 9007199254740993，拒绝相邻值 9007199254740992。0.123456789012345678901 无法无损转为当前原生小数表示，新定义拒绝，旧快照执行也拒绝且不改历史。字符串 number 超出有限浮点数量级仍明确拒绝，未设计任意精度小数存储协议；原生 JSON 数字在进入本模块之前已被解析的精度不由本项重新恢复。

`graphs.py:183` `_validate_properties` 按图谱固定 OntologyRevision 重新解析定义并校验值；抽取的既有结构化校验也沿用此函数，未另起一套类型规则。固定 v1 的 unique 即使当前本体 v2 去掉 unique 仍生效；新图谱绑定 v2 后按新规则运行。

### KG-003：关系定义拒绝重名，历史歧义不再末项覆盖

`schemas.py:142` RelationTypeInput 增加 attributes.name 去重。POST 失败无新增关系类型，PUT 失败原属性/版本不变；没有套用概念的 businessKey 规则，也没有改变字段名大小写语义。

`graphs.py:183` 在构造按 name 查找表之前显式检测重名，故旧的已发布歧义定义也会 422，不再以最后一项覆盖前一项。不会改写或偷偷修补历史定义。

## 目标更新版本与锁证据

`backend/app/modules/system/models.py:37–67` 的 WorkspaceRecord 用 declared_attr 注册 `version_id_col=cls.version`；OwnedRecord 继承该基类，GraphEntity 和 GraphRelation 均继承 OwnedRecord。父锁之后的 `_entity` / 目标 relation 读取仍可能来自本事务既有快照或 identity map，**没有把它们宣称为全部当前读**。

新增两个真实 HTTP 更新专项记录实际 SQL：实体/关系 UPDATE 的 WHERE 都包含主键和原 version，成功更新 version+1；以旧 version 重放返回 409。因此旧快照不会无条件覆盖已提交的新版本，ORM 的 StaleDataError 仍由原提交路径处理。该证据不等同于两个真实 MySQL 会话之间的竞态实验。

另一专项捕获真实 HTTP 路径的 ORM SELECT，再离线编译 MySQL 方言，确认顺序为 KnowledgeGraph FOR UPDATE → GraphEntity FOR UPDATE。这证明实现没有遗漏锁语句/顺序；SQLite 实际执行会忽略 MySQL 行锁语义，不能据此宣称 MySQL 并发通过。

## 隔离、执行命令与边界

所有命令工作目录均为新独立树，唯一解释器：

```powershell
$env:PYTHONIOENCODING='utf-8'
& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_graph_property_contracts.py --disable-warnings --junitxml=.implementation/graph-properties-red.xml *> .implementation/graph-properties-red.log

& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_graph_property_contracts.py::test_invalid_historical_values_do_not_block_each_others_repair --disable-warnings --junitxml=.implementation/graph-properties-history-red.xml *> .implementation/graph-properties-history-red.log

& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_graph_property_contracts.py -k 'number_enum or mapper_version' --disable-warnings --junitxml=.implementation/graph-properties-precision-red.xml *> .implementation/graph-properties-precision-red.log

& 'E:/code/Yun_tian/the_company_agent/test5/.venv_py312/Scripts/python.exe' tests/run_isolated_backend.py tests/backend/test_graph_property_contracts.py tests/backend/test_knowledge_graph.py tests/backend/test_bot_runtime_authorization.py tests/offline/test_knowledge_bot_actions.py --disable-warnings --junitxml=.implementation/graph-properties-verified-final.xml *> .implementation/graph-properties-verified-final.log
```

其他绿色中间轮次使用相同解释器/harness，初始只跑属性文件，扩充后跑上述四文件；仅 JUnit/log 前缀不同。各轮日志和 XML 原样保留。

Harness 强制 sqlite:///:memory:，fixture 断言数据库后端及库名，无主机/端口、无代理或转发；临时 storage/logs 使用一次性目录。MySQL/PostgreSQL 驱动与外网被阻止，回环 9/8011/8012 也被阻止。TestClient 是进程内协议请求，未启动监听端口。新属性专项不依赖模型或外部传输；兼容 Bot 测试仅在 provider 边界使用既有内存桩。未访问业务库、真实模型/8011，没有安装依赖。

本次未验证：真实 MySQL 并发、RR 跨事务行为、锁等待/死锁/重试或大图性能。父行统一序列化 + 锁定候选当前读取是实现机制，不是已完成并发验收。直接 SQL 或绕过两个服务入口的未来写入不受此应用约束保护；本次不加数据库属性约束表/迁移。当前按类型读取全部有效候选进行唯一比较，未做大规模性能承诺。

KG-004–008、基数检查并发保证、图谱/Bot 删除发布竞态、历史引用授权及前端数值传输协议均未扩展。不同工作区、概念、图谱隔离已通过本次 HTTP 正反例；同一图谱内不会因子记录创建人不同而漏查完整唯一性约束集。

## 最终 SHA-256

| 文件 | SHA-256 |
| --- | --- |
| backend/app/modules/knowledge_graph/schemas.py | `C81932E3C2DDCB41DB326D412AC81B418DDF10B98C084EFA34BDFB8CAC9747C1` |
| backend/app/modules/knowledge_graph/graphs.py | `15410F64B31F120E3973D7C0B9657DF0AB9044153E046011876FCDD6E6DC2003` |
| tests/backend/test_graph_property_contracts.py | `3962D5F8C1DA62C68F81D99E5F0B32C51C952283072A2B87CD673A8263C0802B` |
| .implementation/graph-properties-red.xml | `0554F2E66230AE1CF2619D32952F86A7289BD38715017C0E2B124B6CB12C0320` |
| .implementation/graph-properties-green-initial.xml | `780A57191388BE455B01706CE4EA0FB188F2A4968E68A3238E454342E89A7DD8` |
| .implementation/graph-properties-history-red.xml | `F8878444F43557C3C00EA51A6948DADDA0C92628C4E400013AE51A2E2AF5EB36` |
| .implementation/graph-properties-precision-red.xml | `01CC0912D1F38A65371C7C168BB2FC1BA0C102801161B6D5778C35D216080871` |
| .implementation/graph-properties-verified-final.xml | `9F8D4C1E17699AC24C6A0D39FC0E23C0C14B49C109EB289F94A4A663198E12FD` |
| .implementation/graph-properties-verified-final.log | `D4752C79970D0D9BCAEB170C1FB826928660E75333DC718508E4082A0AFBED4D` |

本报告不代替 root 的统一门禁、浏览器验证和正式大版本记录，也不表示在用服务已加载修复。
