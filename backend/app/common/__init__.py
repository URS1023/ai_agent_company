"""``app.common``：跨模块共享的基础设施包（不含业务逻辑）。

包内职责划分：
- ``contracts``        统一响应契约（Envelope / MutationResult / PagedEnvelope）与请求模型基类；
- ``deps`` / ``authorization`` / ``data_scope`` / ``owned_records``
                       登录上下文、权限校验、数据范围与归属校验；
- ``storage``          本地对象存储（key 约定、读写删）；
- ``audit``            审计写入口（随调用方事务提交）；
- ``events`` / ``redis_sync``  Run 事件落库与 Redis 推送（Redis 故障静默降级）；
- ``idempotency``      带副作用入口的原子幂等占位；
- ``pagination``       有界分页；
- ``content_session``  浏览器内容读取用 HttpOnly Cookie（只读）；
- ``artifact_fonts``   生成产物与图表的宋体策略。

约定：这里只放**被多个模块复用**的横切能力。单个业务域专用的工具应留在 ``modules/<feature>/``；
不要在本包内写业务规则或直接依赖具体业务模型，以免反向依赖（``common`` 只依赖 ``models``/``config``/
``errors`` 这类底座）。
"""
