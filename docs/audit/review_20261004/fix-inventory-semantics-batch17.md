# B17 库存语义修复简明验收记录

2026-10-04。唯一工作树 `E:/code/Yun_tian/the_company_agent/test5/.implementation/worktrees/major-recovery-continuation`；产品起点 `7ed8f58b9fd81a151bc0d078216e822f9670aa15`。专项完成后根代理仅快进基线图例测试/验收文档至 `d94c8b61fd184285ff84e4d420175f14b1a17124`，本次六个产品/测试文件字节复核未变。本代理已冻结产品与测试，没有提交、推送、发布或改在用服务/数据源绑定。

## 结果与根因

最终四文件专项 **69 passed，0 failures/errors/skips，81.40 秒**。其中新增 `test_inventory_semantic_counts.py` 18 项；另含原库存 API、结算和业务口径相邻测试。不是整门禁或全部功能验收。

| 问题 | 修复与真实验证 |
|---|---|
| INV17-01：生产来源发现名被误当定制结果 | 结算辅助工具清单加入真实 `list_business_data_sources`。真实 Worker 发现→库存查询→结算，错误模型文字被当前库存事实替换；已有实际 `query_business_data` 的定制回答仍保留。 |
| INV17-02：复合身份按裸 ID 少算 | 同一次库存 SELECT 投影完整、已授权、可证明的物料主键引用分量；独立纯函数统一计算回执与短答。原红例两个组织同 ID 返回 1，修复后同页计 2。单物料多仓、多批仍计 1。 |
| INV17-03：缺失/不可见身份误称零种 | `materialCount` 在非空页存在未知身份时为 null；另给 `materialCountKnown`、`knownMaterialCount`、`unknownMaterialRows` 和本页口径。原缩列红例“6 条库存、0 种物料”变为身份未知。NULL、空串、纯空白未知；数值 0 有效；孤立和软删引用的库存保留。 |
| 库存历史/导出既有拒绝 | 原 `_resolve_units` 在 SQL 后追加单位列，超出留存 SQL 投影，受 SnapshotAccess 拒绝。单位标签改为原单次 SELECT 中的参数化 CASE；没有放松 SnapshotAccess 或 SQL 白名单。公开 history GET 与真实 Excel 下载均通过，值与原 Q 一致，无额外源查询。 |

历史/导出拒绝**不是本轮 B16 的 34 文件新增问题**：`git log/show` 确认批次十三 `057ce1fd7ec2137df732c6dd75c1ff90bf7119d1` 已同时有 `SnapshotAccess.query_result` 行键核验及库存后追加单位列。查询与历史两处日志关联同一成功 Q `01M42MMF8F9C0XNG1BEQKTEV1K`，原长报告行 2245–2256 保存成功 6 行与随后的 403。该旧测试未打印此 Q 的完整 JSON，临时库随夹具结束清理；不能把其他 Q 的完整回执冒充此 Q。三项主问题的其他实际 API/Worker 回执均按各次运行留在长报告。

## 身份与单位边界

- 物理外键计数要求非空且可证明完整的目标主键。目标物料表配置 `allowedColumns` 时，现有快照只有裁列后的主键标记，无法证明完整性，因此保守降为未知；即便该清单实际上包含所有主键字段也不推定。已核验的独立低代码业务键 reference 可作为另外的明确证据。
- 不读取隐藏组织列，不用名称拼身份。新增真实临时表覆盖“合法备用 UNIQUE 外键仍可见、但复合主键一个分量隐藏”及无主键场景。查询数据、原 QueryHistory、公开历史 API、Worker 回执和最终短答使用同一口径。
- 单位代码及标签均为绑定参数，原编码保留。重复且冲突的编码、未知编码、缺少关联均保留 NULL；带引号和 SQL 样式内容的标签按普通值返回。SQLite 在 NOCASE 列上验证大小写与尾空格精确区分。
- 每单位字段最多投影 **100 个无冲突选项**。超限整字段标签为 NULL，不截取字典前缀；`retrievalEvidence.unitDictionaries` 写明 unavailable 和预算原因。用户可能看到更多“单位未知”，不能宣称所有单位已解析。100 项×2 字典静态 SQL 长度：SQLite 14879、MySQL 12455、Postgres 24171 字符，低于 QueryInput 30000 字限制。
- 精确匹配不扩 SQL 白名单：SQLite 双侧 TEXT→BLOB，MySQL 双侧 BINARY；Postgres 双侧 TEXT→参数化 REPLACE 转义反斜杠→BYTEA。**实际执行只验证 SQLite。** MySQL 仅生成及现有授权解析通过；Postgres 语法白名单通过，但所用混合大小写列目录在当前与 `git show 7ed8f58` 基线均出现 qualify 字段授权失败，保留为既有未通过边界，没有声称 Postgres 已验证可运行。
- 一个只读子代理复核完整主键、孤立/软删、未知计数、单位精确匹配及容量边界；发现项修复后复核未见新增确定问题。该代理没有运行测试或数据库。

## 红绿执行与证据索引

所有 pytest 经 MAIN `.venv_py312/Scripts/python.exe` 调用原 `tests/run_isolated_backend.py`，仅内存平台 SQLite、每例临时 SQLite 和本机受控 Worker 桩；模型 0，非本地库 0。四文件组始终单进程、180 秒总预算、100 次源 SELECT 硬上界、`--maxfail=1`；失败停止后先诊断再修复。没有提高预算、并发不同 pytest、自动重试源异常或访问 8011。

| 执行 | 结果 / 耗时 | 实际源 SELECT | 原长报告物理行 |
|---|---|---:|---|
| 三个原始单例 | 发现来源失败 3.16s；复合键夹具用量断言失败 3.01s；隐藏身份失败 2.83s | 1 + 2 + 2 | 10–522 |
| 复合键纠正用量断言后，明确授权追加一次 | 正确产品红例 `1 == 2`，2.94s | 2 | 524–620 |
| 修后三主单例与一次措辞调整 | 发现来源过 2.00s；复合键过 2.00s；隐藏身份“已知身份涉及0种”措辞失败 2.74s，改成“尚无可核实的完整物料身份”后过 2.05s | 1 + 2 + 2 + 2 | 622–890 |
| 四文件第一轮 | 13 passed / 1 failed，18.93s；旧 `modelUsage=={}` 未兼容既有确定性 businessSemantics 元信息 | 26，全部完成 | 892–1546 |
| 第二轮 | 15 passed / 1 failed，22.01s；旧单位追加列导致 history GET 403 | 30，全部完成 | 1548–2300 |
| 第三轮 | 36 passed / 1 failed，46.02s；旧定制 SQL 测试未传既有 DSH 必需 task，仅修测试明确意图 | 50，全部完成 | 2302–3766 |
| 第四轮 | 1 failed，3.97s；新增 COLLATE 被原 SQL 函数白名单拒绝，改用原已允许字节转换 | 0 | 3768–3851 |
| 第五轮最终 | **69 passed，81.40s** | **69，全部完成，0 源失败** | **3859–5696** |

本轮累计实际业务 SELECT 189 次（单例 14 + 五组 175）；组统计包裹正式 execute_read 入口，注入的预期错误替身不算真实源访问。没有实际源超时、断连或查询异常。源物理连接复用次数未单独计量；所有源目标为受控临时 SQLite，元数据同步/夹具建库在同一临时路径，本报告的 SELECT 数不包含这些准备操作。静态 SQL/AST 检查均 0 数据库连接。

第一次来源发现红例的 PowerShell 管道中文解码损坏，保留原字节与状态/ID，不重写或重造；后续实际回执使用 Unicode 转义 JSON 保存。第一次复合键失败停在过严的用量元信息断言，**不计作产品断言红例**；随后明确授权的基线重复才得到 `materialCount 1 != 2`。所有中间失败及警告保留。

## 冻结指纹

SHA256 为当前工作树实际文件字节；`git diff --check` 无错误（仅 CRLF 提示）。产品修复阶段没有修改 chat.py、SnapshotAccess、通用 SQL 权限、正式 docs 或其他代理文件。本次归档仅新增本目录指定的简明记录与 JSON 证据索引，没有修改源码或测试。

| 相对路径 | SHA256 |
|---|---|
| `backend/app/modules/analytics/inventory.py` | `0496A5F4440B56F67D878326FF662699D63823B1FDA639AA2AA675B17C3BF74E` |
| `backend/app/modules/analytics/inventory_identity.py` | `CDAD8D40F23E060CEFEC0279890CE77F9792A52113C11E39CE4F33F178267259` |
| `backend/app/modules/business_ai/inventory_evidence.py` | `5F5FF9F776C6FC9E63C12F61B9F9922C9130A90289E96CBB640511F2639EA41D` |
| `tests/backend/test_inventory_semantic_counts.py` | `25BFC7D26B8C85C77EB5C1D59D4E01D6DE496F4EB1335A929C5082F465FE4DD5` |
| `tests/backend/test_inventory_query.py` | `71010119641475F9F544521805324026C78B82F71B655253CC4E9A85163A6DF3` |
| `tests/backend/test_inventory_evidence.py` | `6C1514BDB4AA5ECB860A5FA074AE3D820921A2B64606E1FA666B4140A9A0411F` |

原始长报告 `.implementation/fix-inventory-semantics-batch17.md`：**39B98FF385E24B8541549F13C016757CC761A3175B0D892CBB252E27264373A0**。原件保持，约 3.85 MB；不要把完整日志复制到正式版本记录。物理行号按原件 LF 分隔计数，避免 Windows CRCRLF 在某些读取器中的空行折算不同。本文仅为简明索引与边界说明。

## 根代理独立插件合同验收

根代理另行执行 tests/backend/test_inventory_plugin_contract.py；本次归档读取既有 XML 和日志，没有重跑测试。

| 独立执行 | 用例结果 | 耗时 | 结果依据 |
|---|---|---|---|
| 四文件库存专项 | 69 passed；0 failures/errors/skips | 81.40 秒 | 原始库存报告最终 pytest 摘要及 SELECT 计数 |
| 库存插件合同专项 | 8 passed；0 failures/errors/skips | 9.841 秒 | inventory-plugin-batch17-first.xml 的 testsuite 与 8 个 testcase |

这两项属于两次独立执行，不合并宣称同一轮 77 项通过。插件 XML 记录时间为 2026-10-04T13:14:19.967597+08:00；日志只有 8 个通过标记，具体统计以解析后的 XML 为准。插件这次执行没有源 SELECT 计数器，不能把库存四文件的 69 次或修复累计 189 次源查询数套用到插件专项。

插件八项是 5 个技能选择参数化场景、1 个网关参数与回执场景、1 个大屏来源/范围场景、1 个澄清错误映射场景。网关场景替换了实际 inventory.query_inventory，大屏场景构造了库存回执；这部分只证明相应合同，不是 8 次真实库存取数，也不是实际 Excel、PPT 或大屏端到端交付。日志没有警告、退出码或源查询次数的统计，不能从通过点扩展出这些结论。

所有证据对应隔离验收与本机临时 SQLite，不是真实业务库验收。实际 XML、日志、原始长报告、原简明记录和当前六个源码/测试文件的 SHA256 均见同目录 [inventory-evidence-batch17.json](inventory-evidence-batch17.json)。本次仅读取并整理既有文件，没有新测试、构建、数据库访问、提交或修改 README/大版本总记录。
