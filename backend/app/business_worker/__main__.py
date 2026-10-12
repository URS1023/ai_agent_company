"""通过私有标准输入输出运行 Worker，不打开 HTTP 监听端口。"""
from __future__ import annotations

import logging
import sys


def main():
    """先保存协议输出，再把应用诊断统一引向 stderr。"""
    source, sink = sys.stdin, sys.stdout
    if hasattr(source, "reconfigure"):
        source.reconfigure(encoding="utf-8")
        sink.reconfigure(encoding="utf-8", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", line_buffering=True)
    sys.stdout = sys.stderr
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    from .protocol import JsonRpc
    from .server import Worker
    rpc = JsonRpc(source, sink)
    worker = Worker(rpc)
    # Windows 首次导入 NumPy 等扩展需在主线程完成；主线程阻塞读管道后
    # 再由业务线程首次加载可能卡在扩展初始化。这里只装配路由，不触发建表。
    from .application import application
    application()
    try:
        rpc.serve(worker.handle)
    finally:
        worker.shutdown()


if __name__ == "__main__":
    main()
