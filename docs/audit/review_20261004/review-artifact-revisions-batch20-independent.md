# B20 逐对象修订独立只读审查

审查时间：2026-10-04 15:04:45 +08:00。工作树 `major-artifact-revisions`，HEAD `faaa6de447c9af7811ec6fc8a69d56e2de5224e5`。本轮只读产品、测试和实施报告，只新增本 ignored 审查文件；没有执行 Python/Node 产品代码、pytest、浏览器、数据库或网络请求，没有读取 dotenv。B22 两个源码文件保持冻结，未触碰 B19/B21。

以下为有完整静态数据流支持、尚未动态复现的两个缺陷。已先发送 root，是否修复及真实红例由实现代理处理。已知“首次 prepare 时 files.view 撤权导致 baseline 丢失”没有重复列为本轮发现。

## P1：一个对象的多个明确修订要求没有累计，会误判完成

位置：`backend/app/business_worker/delivery_contract.py:234–249`、`:278–284`；`delivery_evidence.py:208–233`（本报告末次读取版本）；`tasks.py:217–229`。

`revision_request` 使用一次 `re.search` 提取首个页标题要求，返回单个 `checks`；同一请求其余明确页标题修改被忽略。对象已经有 pending change 时，`revise_objects` 只对完全相同 request 去重；不同 request 直接覆盖 `row.change`，且 `fulfillment=None` 时沿用旧 `base`，没有保留尚未完成的前一项修改。最终验收仅检查最新的单个 check，因此没有其它失败就可变为 completed。

最小单轮红例：

1. 用现有 `completed_decks` 建立真实三页旧 PPT 并完成。
2. 同 task_id 请求：`修改刚才的PPT第1页标题为A，修改第2页标题为B`。
3. 从已存最终旧文件生成新 PPT，仅把第 1 页标题改成 A，第 2 页维持旧值，通过真实 `native_deliveries.present` 交付。
4. 独立读取 PPT 确认第 2 页仍旧；`chat.settle(..., "completed", ...)` 应保持 partial。按当前代码，只有第 1 页 check，其它内容相等反而满足 title_context，因此会满足修订验收。

最小跨轮累计红例：

1. 三页旧 PPT 完成后，要求第 1 页标题改 A；交旧文件，settle 为 partial。
2. 同 task_id 再请求：`修改刚才的PPT第2页标题为B，同时保留上次第1页修改要求`。
3. 新交付仅把第 2 页改成 B，第 1 页仍旧。最新 change 覆盖 page1 要求，base 仍为最初文件；屏蔽 page2 后其余内容完全等于旧 base，因此可错误 completed。

这里特意不用“再修改”作为首句前缀，避免当前 selector 前缀解析对“再”的单独限制掩盖该缺陷。现有测试 `test_same_revision_can_be_completed_across_two_new_runs` 验证同一要求在两份对象之间补齐，`test_queue_edit_cannot_clear_an_unfulfilled_revision` 验证完全相同请求保存去重；它们都不覆盖同一对象的两项不同未完成修改。

最小安全修复建议：同一请求识别出多项修改时，必须全部保存并核验；暂不支持则明确留下 unresolved/partial，不能只验第一项。新一轮修改不能默默替换旧 pending change；在累计校验尚未实现前，保留旧义务并请求明确取代/取消同样比隐式覆盖安全。正对照需保证两页全部正确才完成；反对照需保证只改一页仍 partial。

## P1：未引用的 Office 包成员变化会被当作真实内容修订

位置：`backend/app/business_worker/delivery_evidence.py:31–45` 的 `_revision_content` 与 `:214–233` 的 `_satisfies_revision`；真实交付链 `native_deliveries.py:139–147` → `modules/artifact/service.py:138–139` → `common/artifact_fonts.py:73–145`；实际 Office 校验 `modules/chat_artifacts/artifact_inspection.py:114–139`。

指纹遍历 `archive.namelist()`，只按 `word/`、`ppt/` 或 `xl/` 前缀过滤，未限定主文档实际使用的关系可达部件。`content_changed` 要求仅比较该摘要与 base 不同。真实 create_artifact 字体规范化遍历并写回每个 ZIP 成员，未引用的合法 XML 不会被清除；实际 docx 检查则验证主文档及可达图片/图表，并不会因额外未引用部件而拒绝文件。

最小真实路径红例：

1. 沿 `test_word_and_excel_regeneration_requires_real_content_change` 创建真实 Word，正文固定“原始正文”，并完成旧任务。
2. 同 task_id 请求 `重新生成刚才的Word`。
3. 复制旧最终 ZIP 各成员，只新增 `word/revision-marker.xml`，内容 `<marker>changed</marker>`；不添加任何指向它的关系、不改 `word/document.xml` 或其它原成员。
4. 经现有 `present_bytes` 真实交付并独立用 python-docx 读取最终正文，必须仍为“原始正文”。新增部件被原生交付保留并进入内容指纹，当前 `_satisfies_revision` 可把它视为真实修订并完成。

最小修复建议：从主文档和实际使用的内部引用构建内容证据，未引用的包成员变化不应改变修订资格。不能只把目录前缀当作“可达正文”。仅 ZIP 压缩/时间、docProps 的现有反对照不足以覆盖此情况。新增 XML 成员反对照应与真正正文变更正对照配对，确保后者继续可完成。

## 已核对的其它边界与局限

- `task_runs` 限定工作区、发起人、会话和 task_id；Artifact proof 再校验 owner/workspace。`run_actor` 对当前身份与服务客户端 scope 重新解析，本轮未发现一个可具体复现的新增跨工作区/跨 owner 放行路径。
- 新版 `verify` 从 accepted objects 计数后仍执行完整原合同；逐对象 count=1 没有直接替代总量校验。已有绑定未通过当前授权/字节验证时保留 prior 身份但不计 accepted，不能仅凭已存 fulfillment 完成。
- 多份同类具名选择采用匹配后唯一性检查；未明确选择、或旧候选多于合同份数时保持失败。本轮没有证据支持“同名多产物被任意当作一份”的新增缺陷。
- 这些是定位阅读结论，不是对全部自然语言解析、所有 Office 语义、并发窗口或全部权限矩阵的证明。实施代理正在修改 delivery_evidence 和测试；以下 SHA 固定本轮实际读取的版本，不能自动代表后续版本。

## 读取字节 SHA256

`delivery_evidence.py` 首次读取为 `9437C451EF3A23E6C4F2241D57D18E4BA31347B3BD895D8D386D91644C69AE93`，末次读取已含 strict prepare 与 opaque_file 兼容修复；上述两个发现涉及的逻辑未变。测试首读为 `545D82C8794922D9C03B8E2CBAE29A61BC59FAD1BED8B13DD7F7756C26858CEE`，末次如下。其余核心产品字节在本轮读取期间相同。

| 文件 | 末次读取 SHA256 |
|---|---|
| `backend/app/business_worker/tasks.py` | `C18F0ED22EF5D2461D3A65A42E14CF721B1D2AC650427AAF05EFB4C1CF8C96A4` |
| `backend/app/business_worker/delivery_contract.py` | `E4ACE8590588411DD9BB57B42448945563ED9C06D84CD5E319D15004C2E41AB0` |
| `backend/app/business_worker/delivery_evidence.py` | `75F76B749254F5F17136C9A8EFA87D98C722F39A42747790FD3112DB5EF656EB` |
| `backend/app/business_worker/native_deliveries.py` | `8AD140D8D42046178A93EC54929CE9C817DBAF08A7C347D92074FDE948142B10` |
| `backend/app/business_worker/dashboard_deliveries.py` | `6B7A107E4E7767E211549B2ADE83C0F1077611A426213AC32EC06F319A416115` |
| `backend/app/business_worker/chat.py` | `B9E65D49313212763A4F8FA92FEE4CAC8116976AD347482B0A9AA19A26E36004` |
| `backend/app/common/artifact_fonts.py` | `069353E234CF049B587E8260C9671CF8FB4C0DAC04F40EFB029356680142ED67` |
| `backend/app/modules/artifact/service.py` | `990C63F8357B1175F79C1AF9F8F6AB03B0281B3884BEB3786A1A0EF99AA8A4B6` |
| `backend/app/modules/chat_artifacts/artifact_inspection.py` | `D4374C7D3F4690368CB266589733E84B7628F89FF150BC7EDC7C3D52A026C9E1` |
| `tests/backend/test_dsh_artifact_revisions.py` | `CCB24B563735B0A667D562D046509F54541389E968A36987FB63EAAD7C1110B8` |
| `tests/backend/test_dsh_compound_contract.py` | `42E3138A1CEB8DC9D1995D28FE546FA3D2E6F695800C2F8A94885077A01FE1FC` |
| `.implementation/fix-artifact-revisions-batch20.md` | `323AB1AF10075183562356505C7B9C24761B7C409B6102F232FB2235FC8C02DE` |
