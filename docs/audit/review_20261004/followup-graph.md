# 下一批待修：本体、图谱与抽取审查

基线：077e6e7。仅静态审查，未改代码、未连数据库、未运行模型。以下不是批次十二已修复或已验收项，需先补隔离复现。

## 阅读范围

全文读取：knowledge_graph/graphs.py（552行）、ontology.py（350行）、schemas.py（239行）、extraction.py（882行），common/owned_records.py、common/data_scope.py。models.py 与 router.py 第一次组合读取输出截断，故仅登记片段，不计全文。contracts.py 1–85、test_knowledge_graph.py 261–329 为相关片段。Bot 由独立审查者负责。

| 文件 | SHA256 |
| --- | --- |
| backend/app/modules/knowledge_graph/graphs.py | e195b5d187b4d4952a79d791a7ab4d3a2624518e7469a51229465131a3497d7e |
| backend/app/modules/knowledge_graph/ontology.py | 9e84fcf125778a89223e9bdef1070ade1ea85f4c712b7fde0bf85a66f92d3157 |
| backend/app/modules/knowledge_graph/schemas.py | ade7955b17e24c6ebc0e043a396d691e55cee0d97adfa555f37347ac7d01cd53 |
| backend/app/modules/knowledge_graph/extraction.py | 0b6716564ce3c642f17d54aadf6982d24aa17c1f29f9831ea522a88a457ca802 |
| backend/app/common/owned_records.py | 373205d9fec10ba3a5f7a730649eff4668572f863bd51f045efdb38d52bf473a |
| backend/app/common/data_scope.py | 910673325e7033998390973077c45b178846aab2470a9be571f35c39ef2c1703 |

## 静态确认的问题

| 编号 | 级别 | 证据、触发条件与用户后果 | 下一步复核 |
| --- | --- | --- | --- |
| KG-001 | P2 | schemas.py:21 保存属性 unique；graphs.py:181–208 只查必填、类型与枚举，save_entity/save_relation 不查询该属性重复。不同 businessKey 可具有重复的“唯一”属性，声明没有执行。 | 实际HTTP定义唯一序列号，创建两个不同业务键但相同序列号实体；再验证批量抽取物化与并发。 |
| KG-002 | P2 | graphs.py:200 将 date/datetime 仅判为字符串；schemas.py:23 枚举只能为字符串，而整数/布尔属性校验仍要求原生整数/布尔。因此无效日期可保存；设置数字类型并配置字符串枚举会让所有合法数值被拒绝。 | API定义date后传“不存在的日期”；数字/布尔枚举分别验证保存契约与执行，不能以强制转成字符串绕过类型。 |
| KG-003 | P2 | RelationTypeInput 没有 ConceptInput 的属性重名校验；graphs.py:185 用字典按name折叠，重复关系属性最后一项覆盖前一项。 | 发布两项同名但不同类型/required的关系属性，验证目前最后一项覆盖；修复前保存应422且无记录。 |
| KG-004 | P2 | graphs.py:443–466 首先加入entity_id种子，扩散后按全图创建顺序截断。新建种子与大量更早实体相连时，返回结果可能反而没有用户选中的中心点；truncated在恰好limit且没有遗漏时也恒为true。 | 明确选最后创建的实体，邻居数超过limit；断言种子必留、分层顺序稳定及真实截断标志。 |
| KG-005 | P1 | extraction.py:514–522 直接读KnowledgeChunk；不核对File软删/状态、KnowledgeDocument状态或KnowledgeChunkMetadata.enabled。仅显式选documentIds时_knowledge_source检查文档ready；整库模式无此检查。data_scope的Chunk仅继承KB授权，不能补这些状态条件。管理员停用的分段或软删源文件残留分块仍可成为新抽取候选及证据。 | 临时SQLite创建已停用分段、软删源文件及未ready文档后，通过真实抽取入口分别测试整库/显式文档；同时确保合法共享KB不会因File默认范围误拒。 |
| KG-006 | P1 | extraction.py:802–805、842–844 对同业务键实体/同端点关系直接计为reused，不比较已复核候选的名称/属性与图谱当前值，随后标记completed。候选值与现有图谱冲突时，用户确认的内容可能未入图却收到完成状态。 | 已存在实体/关系与接受候选同键不同值，要求明确冲突或经确认更新，不能静默计为成功；相同值重放仍应幂等。 |
| KG-007 | P2 | extraction.py:767 对运行固定本体的核对取当前job.ontology_id/revision，输入虽在run.input_snapshot保存，却未用于此处；job.target_graph_id也只记最后一次目标。任务编辑后，旧运行的固定修订/已完成目标会受新配置影响。 | 完成run1后编辑任务本体或运行run2到另一个目标；重新物化run1应按run1固定快照与目标判断，不能拿当前job替代历史证据。 |
| KG-008 | P2 | extraction.py:677–701 复核只查payload外形，未按固定本体校验属性；已接受的非法属性仅在物化新增实体时才被发现，复用现有实体还会跳过。 | 复核阶段接受错误属性类型/额外字段；应在接受前返回字段错误且整批不变。 |

KG-005所述源文件/分段停用是运行来源状态问题；是否需要同步收回既有已发布图谱的引用应另定历史保留策略，不能借修复删除历史审计。

## 需要故障复现，尚不记作已证实运行故障

- graphs.py:276–293 的基数检查为先查后写，两个不同关系插入不会更新同一父记录；未看到锁或针对基数的唯一约束。需并发隔离复现一对一/一对多，不以单请求冲突测试代替并发保证。
- 非有向关系允许反向输入，但基数按存储source/target检查，且重复键也按有向端点记录。需确认产品对无向关系的规范方向，再测试反向重复及基数。
- 概念/关系草稿编辑不修改父本体版本，手工实体/关系编辑也不修改图谱版本；发布快照在多次SELECT之间没有父范围锁。需故障注入验证并发发布是否得到内部不一致快照。
- graphs.py:107、478 使用当前概念名称/颜色展示固定修订图谱，元数据会随草稿变化；应按产品约定明确哪些展示属性也必须固定。
- 图谱探索的limit只限最终节点，先读取全部实体/边；不能据此声称服务端工作量已有边界，尚未做大数据或性能测试。
- 软删除后的唯一键仍由数据库保留，但未见恢复入口；重建同业务键可能409。需先确认保留/恢复语义，不擅自删除旧记录。
- 抽取执行在running提交后若进程硬退出，无租约/owner恢复；与知识入库FK-002同类，需要可证明执行者退出的接管设计，不能按时间盲重置。

## 与Bot审查的交叉项

graphs.py:537–549 删除保护只检查BusinessBot当前草稿graph_ids。独立Bot审查确认运行先get_graph验证当前published，再读GraphRevision；所以正式Bot仍引用旧图谱时，仅改草稿解绑就可能使删图谱后正式调用失败。该项由Bot报告登记，避免重复计数。

旧test_knowledge_graph.py:267起的用例确实验证固定修订的顺序基数冲突，不能说固定基数完全没检查；本次发现集中在唯一属性、反向/并发、状态传播及复用结果等额外边界。以上均待新增隔离回归，未算入683项门禁或114组浏览器通过。
