# 文件与知识库专项源码审查（2026-10-04）

本报告针对主工作树 `E:/code/Yun_tian/the_company_agent/test5`，由 `history_window_correctness` 执行只读源码审查。登记时 HEAD：`0cc7d2defcd016bb45ffa917b985762a2c76d9bd`。并行浏览器验收仍在主工作树运行；本审查没有修改产品代码、启动/重启服务、连接业务数据库、请求网络、调用真实模型或 Elasticsearch。下述“确认”指源码控制流确认，不冒充运行复现。后续隔离工作树修复及测试结果另记。

## 结论与范围

- 逐文件通读 `backend/app/modules/files` 的 7 个 Python 文件和 `backend/app/modules/knowledge` 的 6 个 Python 文件；并核对 HTTP 认证、动作映射、ORM 数据范围、显式资源授权，以及 AI/工作流调用的必要片段。文件和 SHA256 清单见末尾。
- 确认问题 7 条：P1 两条、P2 五条。另列 3 条未充分验证或需要产品口径确认的边界。没有将“服务函数缺少显式 require_permission”直接认定为 HTTP 越权：两个模块的旧 API 在认证依赖内已经完成动作映射及 ORM 数据范围绑定。
- 此次未确认可直接凭其他工作区的文件/知识库 ID 越权取得原文。普通 HTTP 新请求会读取当前用户、成员、客户端状态，并按当前角色与资源授权重建数据范围。

## 确认问题

### FK-001 · P1 · 修改分段后仍可能返回已被替换的旧正文

位置：`knowledge/governance.py:396-457,530-568`；`knowledge/service.py:1196-1235,1297-1345`；`knowledge/reranking.py:41-45`。

证据与调用链：

1. `update_segment` 在原 `chunk.id` 上执行 `chunk.text = content`，调用 `_index_segment`，最终提交新 SQL 正文。
2. 嵌入失败时 `_index_segment` 只设置 `embedding_status="failed"`，没有删除或更新旧 ES 文档。ES 写入失败也只返回 `None`；旧文档仍可能保留在 ES。
3. `search` 用 SQL 查询得到的仅是 `valid_ids`，确认分块仍存在、资源可见、未停用等；最终 `content/file_name/location/document_id/knowledge_base_id` 仍从 ES `_source` 返回，未用 SQL 当前正文和关联记录重建。
4. 重排也使用这些候选的旧 `content`，所以旧文案可能再次发送到配置的重排接口。

可复现条件：创建并索引“温度上限 80℃”；更新同一分段为“温度上限 70℃”，令嵌入或 ES 更新失败，而 ES 查询仍能返回旧命中；查询“温度上限”会得到旧 80℃。同理，人工删除一段敏感旧句而保留该分段 ID，旧句仍可能返回。该问题不是跨工作区泄漏，属于已修改内容未实际撤回及答案正确性问题。

建议：ES 仅提供候选 ID/排序，输出内容和引用必须来自当前已授权 SQL 联表。增加正文/版本一致性判定，不再把旧向量分数当作当前新正文的相关性；过期候选剔除后需要补充当前关键词结果。回归同时检查返回内容和送入 rerank 的内容。

### FK-002 · P1 · 硬中断留下永久 processing 文档，无法导入或重建

位置：`knowledge/service.py:469-508,709-724`；`knowledge/governance.py:714-737`。

证据：`add_document` 先将文档置 `processing` 并 `db.commit()`，随后才解析、识图、嵌入和写索引。任何后续导入/重建在 `if any(row.status == "processing" for row in docs)` 无条件抛 409。异常分支能处理正常抛出的 Python 异常，但进程退出、机器重启、Worker 被终止不会进入该分支。当前应用内 `add_document/reindex_document/sync_files` 调用点只有同步 HTTP 与内部同步循环，没有文档租约、执行者 ID、心跳或恢复入口。

复现方案：在隔离执行器中让 processing 提交后硬退出；重新启动隔离实例，对相同 file/kb 再导入、重建和同步，均仍遇到 processing 冲突。

影响：初次导入永久不可用；重建中断也无法再重建。修复不能简单按固定超时抢占，否则会和仍活跃的索引并发写。建议记录任务身份/租约并在明确失去执行者时安全收尾，保留旧 ready 分块，提供可追溯的恢复路径。

### FK-003 · P2 · 搜索索引有无效命中时不会启用数据库回退，合法结果被隐藏

位置：`knowledge/service.py:1226-1235,1297-1329`。

证据：仅在原始 ES `hits_bm25` 为空时调用 `_db_keyword_hits`；授权、删除、禁用等 SQL 复核发生在之后。若 ES 返回的前 N 个候选全部因撤权、删除或停用被过滤，返回空列表，永远不会补查 SQL。对 AI 未指定库范围的查询，跨多个同工作区知识库的无权限候选更容易占满候选窗口。

复现方案：ES 返回一个已删除 chunk 或未授权 KB 的 chunk；SQL 同时存在可见且与查询匹配的新分块。期望返回合法分块，当前为空。授权过滤本身有效，没有放回无权限内容；缺陷是召回可靠性。

建议：在权威 SQL 过滤及内容一致性检查后，若关键词有效候选不足，受同样权限条件约束地补查关键词候选并去重；不得为补足数量放宽授权范围。

### FK-004 · P2 · 相同知识库授权在 HTTP 搜索与 AI 工具搜索中结果不一致

位置：`common/data_scope.py:112-150`；`modules/business_ai/context.py:51`；`providers/tool/gateway.py:236-256`；`providers/tool/builtin_provider.py:1189-1202`；`knowledge/service.py:1310-1323`。

证据：HTTP `/api/knowledge/search` 将 `request_permission` 设为 `knowledge.view`，触发“被可见知识库引用的源文件”读取例外。AI `auth_context_from_tool` 调用无动作参数的 `bind_data_scope(ctx)`，随后 `search_knowledge` 直接调用 `service.search`；不触发该例外。最终 SQL 联表还要命中 File，文件只按 `files.view` 的普通范围过滤。

复现方案：用户具有 self 范围的 knowledge.view 和 files.view，获显式共享他人 KB.view，未获其中他人源文件的单独授权。HTTP 知识库搜索可命中该库原文，AI search_knowledge 则返回空。若缺 files.view，差异更明显。

建议：明确知识库授权本就允许返回已入库分段的产品口径；让 AI/工作流检索使用与 HTTP 同源的知识库作用域。不得因此使普通 `/api/files` 下载获得知识库之外的全文件权限。该修改涉及公共上下文或工具入口，未擅自扩大修复所有权。

### FK-005 · P2 · 手工分段序号计算把合法 0 当成空值

位置：`knowledge/governance.py:477-485`；`models.py:303-322`。

证据：`ordinal = int(db.scalar(select(func.max(...))) or -1) + 1`。第一条分段 ordinal=0 后，max=0 被 `or -1` 替代，第二条仍为 0；之后最大值一直为 0，顺序创建的手工分段可全部停留在 ordinal=0。模型没有 `(document_id, ordinal)` 唯一约束兜底。

复现方案：同一新 KB 连续创建 3 条手工分段；查看 SQL ordinal 与 location.ordinal，当前都为 0。影响引用序号与同文档排序，违背 KnowledgeChunk“文档内唯一递增”的模型约定。

建议：仅将 `None` 视为空集合，再加 1；并单独评估并发序号分配，不能声称简单修正空值即可解决并发问题。

### FK-006 · P2 · DOCX 模板字体大小直接按 EMU 写入，常用字号变为几乎不可见

位置：`files/template_fill.py:194-206`；调用入口 `providers/tool/builtin_provider.py:1567-1571`。

证据：`run.font.size = font["size"]` 将用户常见的 12/14 直接传给 python-docx 长度属性，没有 `Pt(...)` 换算。传整数 12 会被解释为 EMU，写入字号约为 0；浮点还可能触发属性类型校验。模板工具已声明接收 font 字典，故可被真实调用触发。

复现方案：隔离临时 DOCX 包含占位符，调用 fill_docx(...font={"size":12})，检查生成 XML `w:sz` 与重新载入的 `.font.size.pt`；应为 12 pt。

建议：严格校验有限、正数的 point 值并使用 `docx.shared.Pt`。中文 font.name 还需同步 East Asia 字体属性才能保证宋体生效；应使用与既有文档字体工具一致的方法，不盲目重置模板其他样式。

### FK-007 · P2 · 已软删源文件仍在知识文档列表提供必然失败的下载链接

位置：`knowledge/service.py:783-827`；`files/service.py:566-593,309-323`。

证据：文件删除只置 status/deleted_at 并保留 File 行。知识文档列表 outerjoin File 未加删除条件，只以 `if file_row` 决定名称和 download_url，因此仍显示正常源文件名和下载地址；实际下载 get_file 会按 `deleted_at IS NULL` 拒绝。

复现方案：导入文件到 KB→软删资料库文件→列出 KB.documents；当前仍返回下载 URL，但访问 404。索引搜索已正确过滤 deleted 文件，不应把这条描述为搜索泄漏。

建议：对展示层判断文件是否当前可下载，保留历史名称或明确“源文件已删除”，同时将 download_url 置空。

## 已核验有效的保护

1. `files/service.py:get_file` 显式 workspace + deleted_at 过滤，HTTP 依赖还绑定 File 所属用户/部门/显式授权条件；下载要求 files.export，正文预览要求 files.view。仅凭其他工作区 ID 不会命中。
2. `get_content_auth` 的内容 Cookie 只限 GET/HEAD，不接受 URL token；服务令牌不能切换 workspace。download 返回 `private, no-store`。
3. KnowledgeDocument/KnowledgeChunk 在 `data_scope.CHILD_RESOURCES` 继承知识库条件，HTTP 新请求撤 KB 授权后不能靠 chunk/document ID 绕过；search 对候选做 SQL 复核，停用/删除的分块和删除的文件不会直接从残留 ES 放出。
4. KnowledgeProfile/ChunkMetadata/SyncRun 未直接登记 ORM 父范围，但本次通读的公开入口均先通过 get_kb/get_segment 校验目标 KB。RecallEvent 只存查询、过滤条件和计数，不保存命中正文，普通用户仅看本人日志；因此不能将日志误报为已撤权正文缓存泄漏。
5. 上传名剥离正反斜杠路径，存储 key 包含独立 ID，LocalStorage.resolve + is_relative_to 限制在存储根目录。单文件 20MB，批量最多 20 件/50MB。旧版 .doc/.xls/.ppt 明确拒绝正文入库，UTF-8/UTF-16 文本严格解码，不再 errors=replace。
6. 正常解析失败写 failed 记录；重建正常异常保留旧 ready 内容，SQL 新旧分块切换在同次提交内。上述保护不能覆盖 FK-002 的硬中断。
7. 资源授权新增由 admin_service 逐动作 require_resource 校验“不能授予自己没有的动作”；知识库角色授权接口没有单独绕过此链路。

## 需要进一步核实的边界（不算已运行确认缺陷）

- **私有 KB 语义遗漏**：`models.py:273-287` 声明 KnowledgeBase.visibility 支持“仅创建者”，`service.create_kb` 也接受 visibility，但 `authorization.py:289` 的 PRIVATE_RESOURCE_TABLES 仅含 skills/plugins/connectors。若库中已有 private KB，同工作区 workspace 范围角色不会被 private 限制；当前 HTTP KBCreate/KBUpdate 不提供 visibility 字段，本轮没有连接库确认是否存在这样的历史记录。需确定是否仍支持该语义，再统一在公共权限层补齐，不能只给某个 HTTP 列表打补丁。
- **长同步操作的撤权时点**：入库/批量同步使用同一请求中的身份、权限和已加载的源文件列表；请求内策略明确缓存，模型/ES 每阶段不重新鉴权。没有独立后台索引队列；模块命名“后台”不能当作已存在持续撤权保护的证据。当前工作流和聊天在其他边界会重建身份，但不能将其覆盖能力自动外推至本模块长同步请求。
- **解压/解析工作量**：20MB 是压缩上传字节上限；DOCX/PPTX/XLSX/PDF 解析没有在本模块设置解压总字节、页数、行数、文本总量或模型输入总量边界。vision 的图片张数上限发生在部分完整解包/载入之后。未制作压缩炸弹或进行资源压力实验，不能声称已经测得拒绝服务。

## 未覆盖与复核限制

- 未运行真实数据库、模型、ES、文件上传请求或浏览器；本报告没有把推导条件写成真实生产复现。
- 未全量通读知识图谱/Bot 独立检索管线、DSH 附件上传转发/旧消息缓存/产物下载模块、所有工作流恢复授权与 sandbox 路径；只读了必要工具和工作流知识节点片段。它们不在本次两个目录逐文件审查的完成范围内。
- 未审计第三方解析器的底层格式漏洞或操作系统符号链接并发攻击；LocalStorage 的正常路径归一化已检查。
- 未因公开方法没有单独 require_permission 而误判：普通 HTTP 数据边界依赖统一认证与 ORM 过滤；脱离该上下文直接调用服务属于内部调用方责任，须单独核对。

## 实际阅读文件指纹

下面的 SHA256 直接读取登记时主工作树文件计算。全文阅读与片段阅读分开登记，搜索命中不计作全文通读。

| 文件 | 实际阅读范围 | 总行数 | SHA256 |
| --- | --- | ---: | --- |
| AGENTS.md | 全文 | 48 | c6948250a8e090f4bda243bad123e9f063f96381503b916bfe12fddcc44e032b |
| backend/app/common/authorization.py | 全文 | 420 | 0e59bd64f1d87a8db08051385eb2b9df35b7893309e1435810be9442831411ca |
| backend/app/common/data_scope.py | 全文 | 186 | b65a2867ea6de69d67dbe0d96db30b20ce8d8115e5ee3d225b87d426c2eec643 |
| backend/app/common/deps.py | 全文 | 420 | 9bb500c6de6ea5e49b05705b5162ac695da6df97f5877f9beb72d86682442c1f |
| backend/app/common/owned_records.py | 全文 | 113 | 2444953ffdad10c936e2e69b9e9556f20a9fabe3167191f96ff76a1b41414a01 |
| backend/app/common/storage.py | 全文 | 170 | 17ea60d9910e77200e1b80ff01fb736e837962539fb134443b688728f24aa109 |
| backend/app/models.py | 246-332 | 824 | 75a6be3332b165637751461a2b11789bf96225d4dbe42177dae2dfeb694f2f27 |
| backend/app/modules/business_ai/context.py | 全文 | 52 | 9d8de04426afc278d9d6b19e8a9595b7a8605f535181f2be7056ea0afd4c9e0c |
| backend/app/modules/files/__init__.py | 全文 | 1 | 682bbb6b01e2ea970b7ce8d7536a700f3ae575fbb2bdb71f24356c04289deaf7 |
| backend/app/modules/files/extractor.py | 全文 | 168 | 5d0cb23329fec2f45251bee0eb12f767aaf2bcc5df50cfa25356757551d41a0f |
| backend/app/modules/files/models.py | 全文 | 83 | d08b3a87a12c5aa769946392293d55e671161bdbe7573e3ed1e3e8aa31191e7c |
| backend/app/modules/files/router.py | 全文 | 482 | ffe5eaa91592771113e988cc1869dbd0a1eaa67674f88d722726b6264e661277 |
| backend/app/modules/files/service.py | 全文 | 832 | 50c10e47d43c2320ce05b9e9e4918f730958d18cdc18a2507377c2ec47eb4161 |
| backend/app/modules/files/template_fill.py | 全文 | 297 | 6ca9046be1d8883426c439071629fb57344433997708af0c706cc6d64a7a6efe |
| backend/app/modules/files/vision.py | 全文 | 377 | 323e27937a28144a34bb9a67a3da50db1bda818e6bc58206e6ad16d180ecf328 |
| backend/app/modules/knowledge/__init__.py | 全文 | 1 | 4a2239d80e2b418cdec67f04b27fd015674966dec14cda22daaf18b1e6f79bec |
| backend/app/modules/knowledge/governance.py | 全文 | 783 | bf06321083db76b3a1d2262fe959157e61a0e821a6d155575523ddb6d63d1b52 |
| backend/app/modules/knowledge/models.py | 全文 | 104 | dc6e6b89b7a37c3ef5275e3208703c835e58dd06331fd81d2a79ce2471304c1d |
| backend/app/modules/knowledge/reranking.py | 全文 | 95 | c978bd88fa5136fd2e4107fe5413f6ce8616332e7768e5c1fe814f6f2420949f |
| backend/app/modules/knowledge/router.py | 全文 | 799 | bcdccc7eff4a7d47d257903cf5ba1e2ad5925da372f647c3130c911228cfa5b5 |
| backend/app/modules/knowledge/service.py | 全文 | 1461 | bc7263d6a7051a9fc1e8e2bc131eaa26447a9711e94df5c3241edaa3653ba7da |
| backend/app/modules/system/admin_service.py | 1-155 | 375 | b800fa31acf8d3f4aa189cda023cbca97d97ea36f1b9251595eec2929f7c307e |
| backend/app/modules/system/permissions.py | 全文 | 105 | 92c19e3a2fa56778867e64d586dda8e6268f507c65253f8547e0db2554d72823 |
| backend/app/modules/workflows/graph.py | 311-405 | 483 | d10b9cd613b4c162f860c1456005e62254a127825237616bbf9504dd24ff0a38 |
| backend/app/modules/workflows/nodes.py | 1061-1145 | 1404 | e018d8ffbc5cf089cadc4149cc70965db3dc9ab887d5de3ccb382b4d8a5300b6 |
| backend/app/providers/tool/builtin_provider.py | 321-385、1141-1240、1461-1584 | 1584 | c3dcc77254ae34ccfab6faedcd0ad2cf6b2949aa26dbf109d753ff732a025c60 |
| backend/app/providers/tool/gateway.py | 1-220、236-375 | 538 | d885f0a7b6ccd861e675ef3bd8d353c8593a5fdc52b4488b6b1f82f11dcbe7e5 |
| tests/backend/test_knowledge_file_integrity.py | 全文 | 33 | be66e05f093f218a2e5b5da6a96a486fc07b12a892a61c4717ea97de1f59e83f |
| tests/backend/test_knowledge_governance.py | 1-160 | 290 | 537db241b2488c92b81c6b44e7a475a2ec437b79a16431660cbc4602002a0186 |
| tests/backend/test_knowledge_recovery.py | 全文 | 99 | bbf79b88c7d36a78c82700df136a7be4c02639778daf9341a8701f139ffda5e9 |
