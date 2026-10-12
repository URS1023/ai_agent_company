# 大版本批次十五：图谱探索中心与真实截断

KG-004 在隔离真实 HTTP 中已复现并修复，产品改动仅限 `backend/app/modules/knowledge_graph/graphs.py` 的 `explore()`。本节不代表图谱全部功能或真实数据库并发验证完成。

## 原因与结果

原实现先在集合里扩展邻居，再按全部实体的创建时间裁剪；较晚创建的查询中心可能被较早创建的远端实体挤掉。`truncated` 使用 `len(selected) >= limit`，即使恰好返回全部合格节点也会错误提示结果被截断。

现在先保留明确中心或检索命中的种子，再按距离逐层展开；同一层按创建时间及实体主键排序。只有用户允许的深度内实际省略合格节点，才返回截断。软删节点及软删关系不作为通往其他节点的桥梁，响应中的边两端均必须存在于返回节点中。

## 执行证据

- 最初 13 项真实 HTTP 用例得到 9 失败、4 通过，JUnit 17.487 秒；其中包含中心被裁掉、按层顺序错误及满额误报截断。原始证据 `.implementation/graph-explore-before.xml/log`。
- 修复后同 13 项通过，JUnit 14.996 秒；这是对照复测，不额外累计为新用例。
- 加入空图、孤立中心、其他图/软删中心、软删桥接节点/边、环和同创建时间等边界后，20 项全部通过，JUnit 38.142 秒；证据 `.implementation/graph-explore-final.xml/log`。
- 所有调用经 `tests/run_isolated_backend.py`，临时 SQLite，无真实业务库、无外部模型。

## 冻结指纹

| 文件 | SHA256 |
| --- | --- |
| `backend/app/modules/knowledge_graph/graphs.py` | `de49efa41cc1c7b9ab481f0f10751af309abad705a2abc21766db455f69d73bb` |
| `tests/backend/test_graph_explore_contract.py` | `67e155d2b8cd9a59955ca32dd6048cc4c55cab0d084aec4b02c4551ca83e0cbc` |
| `.implementation/graph-explore-before.xml` | `421aee7094b095bf07c72c79f856f20b0262291247ff516df795c89985378b7c` |
| `.implementation/graph-explore-final.xml` | `a088275acd4ebf3c5c24b88f68ce7724295d3830f31b415da4756e07e1369b76` |

## 未覆盖

仍从当前图加载全部活动节点与边，大图性能及流式探索不在本修复范围。没有把 API 的确定性顺序当成浏览器坐标不变的保证，没有运行真实 MySQL 并发，也未改变图谱统计或发布行为。尚待本批统一门禁与完整浏览器验收。
