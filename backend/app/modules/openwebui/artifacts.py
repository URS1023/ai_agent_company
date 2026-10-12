"""兼容旧导入路径，工件转换和授权由公共聊天模块提供。"""
import sys

from ..chat_artifacts import artifacts as _implementation

# 保持同一模块对象，旧调用方的补丁仍作用于公共实现。
sys.modules[__name__] = _implementation
