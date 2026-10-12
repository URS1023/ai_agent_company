"""Legacy MySQL/Postgres datasource compatibility package.

兼容层：仅保留旧版数据源接口所需的入口，新的数据源能力统一由
app.modules.analytics（数据库/搜索引擎）与 app.modules.collection（固定 HTTP）
提供。本包不做独立授权，所有调用都会转发到 analytics 的服务与权限校验，
因此不应在此模块中实现新的取数逻辑。
"""
