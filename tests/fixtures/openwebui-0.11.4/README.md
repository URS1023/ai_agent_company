# Open WebUI 0.11.4 历史兼容性检查样本

此目录仅供离线 schema 兼容性回归，不是当前 DSH 执行引擎，也不代表完整 Open WebUI 源码分发。

`tools.py.txt` 是原本机固定快照 `services/openwebui/upstream/backend/open_webui/utils/tools.py` 的完整原始字节，SHA256 为 `ae4fc82448f914863a770bb105591f5c161a372b16bcbf40014f22b28c8bc9b9`。原始出处、版本及校验值在已跟踪的 `services/openwebui/UPSTREAM.json`；本目录未修改第三方源码，保留原 LICENSE、LICENSE_HISTORY 与 LICENSE_NOTICE。文件属性禁用换行转换，避免不同平台检出改变证据字节。

测试先校验原始字节摘要，再解析 AST，仅执行原文件中的 `clean_properties` 与 `clean_openai_tool_schema` 两个函数，沿用业务参数形状、验证器及旧客户端传递断言。不会 import 整个模块、安装或启动旧引擎，也不访问数据库或模型。这样新克隆的隔离测试无需依赖本机 ignored upstream 目录；当前 DSH 的真实 schema/调用合同由 Host/Worker 和原生浏览器流程另行验证。
