"""智慧云脑后端应用包。

本包是 FastAPI 模块化单体：`app/modules/<域>/` 按业务域拆分（各自含 router/service/models），
`app/common/` 放跨域支撑（认证、授权、契约、存储、审计、事件），`app/providers/` 放外部能力
（模型调用、工具网关及其 HTTP/数据库/MCP/沙箱 provider），`app/workers/` 放隔离运行的执行器。

入口：`app.main:app`（由 `backend/run.py` 启动）。
"""
