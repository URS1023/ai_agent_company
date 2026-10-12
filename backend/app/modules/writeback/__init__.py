"""Explicit, authorized database result destinations shared by business workflows.

中文补充（数据库写回包）：targets 维护表/字段白名单与实时结构校验，values 负责
JSONPath 映射与严格类型转换，execution 在外部库执行事务写入并落幂等回执，
models / schemas / router 提供持久化、契约与 HTTP 入口。
核心约束：只写白名单内的真实普通表；提交结果不确定时如实标记 uncertain，绝不自动重试。
"""
