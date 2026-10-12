"""工具 provider 包：base（契约）、gateway（统一网关）、normalizer、validator、
compiler 五个通用模块，以及 http / database / mcp / builtin / sandbox 五种实现。

本包不导出符号（调用方按模块名显式导入），避免 provider 之间因包级导入形成环。
"""
