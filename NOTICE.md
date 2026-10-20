# 来源与许可

当前仓库发布新增制造业平台、必要提取组件及项目文档，复用素材保持原有来源和许可。

- qKnow标志与素材归 Jiangsu Qiantong Technology Co., Ltd.，许可副本见 `licenses/qKnow-LICENSE.txt`。
- Test5提取范围包括纯质检判定、SQL安全、连接边界、合成算法、20套结构化报表模板及画布/图表/素材组件。具体适配位置见各模块README和apps/web/REUSE.md。用户本地快照根目录未发现独立LICENSE；提取不意味着为其重新授予开源许可。
- 知识原件解析提取Test5的`backend/app/modules/files/extractor.py`格式读取方法，适配于平台`modules/knowledge/extractor.py`；新增格式/大小预算、来源定位与独立解析进程。未整包复制Dify/Open WebUI的loader依赖，扫描PDF/OCR与旧Office二进制格式不属于已实现范围。
- Dify及Open WebUI的原生部署保留，版本与来源由deploy/native记录，许可副本见licenses/。平台正按最新需求融合界面与业务能力；新增基础对话、检索及三类步骤不代表完整原生内核已等价迁入。每次复制的实际范围与适配见对应模块说明。
- 演示食品质检CSV及包装SVG为本项目合成素材，位于fixtures/食品产品质检.csv及apps/web/public/demo/quality/，不得当作企业真实检测证据或识别准确率。
- 个人记忆管理适配Open WebUI的`backend/open_webui/models/memories.py`手工CRUD及`utils/memory.py`内容清理/关键词匹配语义，位于平台`modules/chat/memory_models.py`、`memories.py`、`memory_router.py`。新增工作区隔离、revision保护和SQL分页；未复制自动回顾或向量路径。现有OpenWebUI许可及NOTICE副本继续适用。
- 内部图执行器`services/workflow_runtime/`调用Graphon 0.8.0原生Graph、Engine、VariablePool和节点实现，版本及制品摘要见该目录requirements.txt；本目录是协议适配，没有另写DAG调度器。Graphon按Apache-2.0分发，原许可证副本见`licenses/Graphon-LICENSE.txt`。该许可不替代Dify前端或API扩展源码的附加许可条件；支持节点以实际测试及模块说明为准。
- 四个原项目整库、设备资料和数据包不在本次发布范围内；独立本机运行副本、依赖和数据保留于已忽略目录，不纳入提交。
- 参考组件接入状态以实际渲染和验收为准。源码/模型连通/原生健康检查分别记录，不声明未经验证的生产能力。
