"""兼容旧导入路径，业务实现统一由公共聊天工件模块提供。"""
import sys

from ..chat_artifacts import query_spreadsheet as _implementation

# 共用模块对象，旧路径的 monkeypatch 与锁状态仍作用于同一份实现。
sys.modules[__name__] = _implementation
