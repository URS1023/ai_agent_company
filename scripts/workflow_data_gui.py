"""Launch the native local workflow test-data tool, without starting the web backend."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]


def main():
    """启动本地工作流测试数据生成器的原生窗口。

    需要 Windows + tkinter，以及本机 MySQL 凭证与已发布的业务工作流（真实连接在 workflow_testdata 内校验）；
    不启动 Web 后端、不加载 FastAPI 应用。DPI 感知设置失败时忽略（仅影响显示清晰度）。
    """
    import ctypes
    import tkinter as tk
    from scripts.workflow_testdata.gui import DataGeneratorApp
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    root = tk.Tk()
    DataGeneratorApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
