# -*- coding: utf-8 -*-
"""Sandbox worker — executed as a separate process by the Sandbox provider.

Safety envelope applied in-process (V1 local dev):
  * chdir into the per-run working directory
  * outbound network disabled unless the caller explicitly grants it
  * os.system / os.popen / subprocess disabled
  * Python audit hooks confine imported file APIs (io.open/os.open/pathlib)
  * breakpoint/input removed

This is a process-level boundary, not a hostile-code security boundary.  A
production deployment must run this worker in a disposable container or VM
with a read-only image, a private filesystem, a non-root UID, seccomp/AppArmor
or an equivalent policy, and an egress firewall.  In-process Python cannot
guarantee isolation from a determined native-extension or interpreter exploit.
Output protocol: writes result.json with {status, result, stdout, stderr,
output_files, error}. Any file created in the cwd is treated as output.

中文补充（隔离策略与治理约束，改动前必读）：
- 本进程由 providers/tool/sandbox_provider.invoke() 以子进程方式拉起，入参只有
  job.json（code / cwd / allow_network / timeout_seconds / inputs），出参只有
  stdout 与 cwd 下的 result.json；provider 只解析这两个通道。
- 网络开关判定链：provider 写入 job["allow_network"] → 本模块 main() 读取 →
  _harden(allow_network) 在关闭时替换 socket.create_connection/getaddrinfo 与
  socket.connect/connect_ex/send/sendall/sendto。开关只可能"变严"，工作进程
  无法自行开启网络。
- 文件访问审计：_install_file_audit 注册 sys.addaudithook，覆盖 open、
  os.listdir/os.scandir/os.walk 与 os.chmod/rename/remove/symlink 等写事件；
  写只允许落在运行目录，读额外允许解释器与 site-packages 根（保证 pandas 等
  可正常导入）；另有 _restricted_builtins 把内建 open 限制在 cwd 并移除
  input/breakpoint/exec/eval。审计钩子在可信渲染器初始化**之后**安装。
- 字体：代码里出现 matplotlib/seaborn 时，先用 app.common.artifact_fonts
  .install_chart_fonts 把项目字体的私有副本装进运行目录，再进入读边界——
  因此不会为了画图而放开宿主字体目录。
- 产物枚举跳过 ``.render_support``（渲染器/字体支持文件不属于业务产物，
  不该回传给用户或写回数据库）。
- 进程执行入口由 _harden 统一替换，并由 Python audit hook 再拦截一次；
  ctypes、_ctypes、_winapi 等原生进程/动态库入口也禁止从用户代码导入。
  这仍然不是恶意代码的安全边界：native 扩展和解释器漏洞必须由一次性容器/VM、
  非 root UID、seccomp/AppArmor 与出口防火墙兜底。
  文件边界同理依赖审计钩子与内建 open，native 扩展不受其约束；生产部署必须
  叠加一次性容器/VM、只读镜像、非 root UID、seccomp/AppArmor 与出口防火墙。
"""
from __future__ import annotations

import contextlib
import ast
import io as _io
import json
import os
import sys
import traceback


_PORTABLE_OUTPUT_ROOTS = ("/tmp", "/mnt/data")
_SANDBOX_ENV_KEYS = frozenset({
    "LANG", "LC_ALL", "LC_CTYPE", "MPLBACKEND", "PATH", "PYTHONIOENCODING",
    "SYSTEMROOT", "TEMP", "TMP", "TMPDIR", "TZ", "WINDIR",
})


class _PortableOutputPathRewriter(ast.NodeTransformer):
    """Map common Linux artifact paths into this run's output directory.

    中文补充：模型生成的代码经常写死 /tmp 或 /mnt/data 这类"看起来可写"的路径，
    在 Windows 宿主上会直接失败。这里在编译前改写字符串常量（只改字面量，
    不改语义），把这类路径映射进本次运行目录；含 ".." 的路径直接拒绝，
    避免借改写越出沙箱目录。
    """

    def __init__(self, cwd: str):
        """记录本次运行目录（改写目标）；cwd 一律取绝对路径，避免随进程工作目录漂移。"""
        self.cwd = os.path.abspath(cwd)

    def visit_Constant(self, node):  # noqa: N802 - ast visitor API
        """处理字符串字面量：命中 /tmp、/mnt/data 前缀时改写为运行目录下的等价路径。"""
        value = node.value
        if not isinstance(value, str):
            return node
        normalized = value.replace("\\", "/")
        for root in _PORTABLE_OUTPUT_ROOTS:
            if normalized != root and not normalized.startswith(root + "/"):
                continue
            relative = normalized[len(root):].lstrip("/")
            parts = [part for part in relative.split("/") if part not in ("", ".")]
            if any(part == ".." for part in parts):
                raise ValueError("临时输出路径不能包含 '..'")
            target = os.path.join(self.cwd, *parts) if parts else self.cwd
            parent = target if not parts else os.path.dirname(target)
            os.makedirs(parent, exist_ok=True)
            return ast.copy_location(ast.Constant(value=target), node)
        return node


class _MatplotlibCompatibilityRewriter(ast.NodeTransformer):
    """Keep harmless model-generated Matplotlib styling mistakes executable.

    中文补充：只做"丢弃无害装饰参数"这一种改写（当前是 Rectangle 的 radius），
    目的是让风格小错不至于让整段业务分析失败；不得在此加入任何会改变计算结果
    的改写，否则同一段代码在沙箱内外的行为会不一致。
    """

    def visit_Call(self, node):  # noqa: N802 - ast visitor API
        """处理函数调用：只做"丢弃无害关键字"的兼容性改写（当前为 Rectangle.radius）。"""
        node = self.generic_visit(node)
        function_name = ""
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            function_name = node.func.attr
        if function_name == "Rectangle":
            # ``radius`` only changes decoration and is not accepted by
            # matplotlib.patches.Rectangle.  Dropping it preserves the chart
            # instead of failing an otherwise valid business analysis.
            node.keywords = [
                keyword for keyword in node.keywords if keyword.arg != "radius"
            ]
        return node


def _compile_user_code(code: str, cwd: str):
    """解析并改写用户代码后编译为 code 对象（尚未执行）。

    改写顺序固定：兼容性改写 → 路径改写 → fix_missing_locations。
    先做兼容性改写是为了让路径改写面对的是原始语义；语法错误在这里抛出，
    由 main() 统一转成中文友好提示。
    """
    tree = ast.parse(code, filename="main.py", mode="exec")
    tree = _MatplotlibCompatibilityRewriter().visit(tree)
    tree = _PortableOutputPathRewriter(cwd).visit(tree)
    ast.fix_missing_locations(tree)
    return compile(tree, "main.py", "exec")


def _friendly_error(exc: Exception) -> str:
    """把执行期异常转成面向用户的中文提示（不返回堆栈）。

    只做措辞转换，不改变失败与否；完整堆栈写入 stderr 供运维排查。
    常见情况：语法错误、路径不存在、以及模型惯用的参数错误（如 radius）。
    """
    if isinstance(exc, SyntaxError):
        location = f"第 {exc.lineno} 行" if exc.lineno else "代码中"
        return f"Python 语法错误（{location}）：{exc.msg}"
    if isinstance(exc, FileNotFoundError):
        target = getattr(exc, "filename", None) or str(exc)
        return f"文件或目录不存在：{target}。输出文件请保存到 OUTPUT_DIR 或使用相对路径。"
    message = str(exc).strip() or type(exc).__name__
    if "Rectangle.set() got an unexpected keyword argument 'radius'" in message:
        return (
            "Matplotlib Rectangle 不支持 radius 参数；请移除 radius，"
            "或改用 FancyBboxPatch(..., boxstyle='round,pad=0.02')。"
        )
    return f"{type(exc).__name__}: {message}"


def _harden(allow_network: bool):
    """在用户代码执行前削弱网络与进程执行能力（原地替换模块属性）。

    参数：``allow_network=False`` 时额外封禁 socket；进程执行能力**始终**封禁
    （无论网络开关如何），因为拉起子进程等价于绕过本进程的全部策略。
    注意：这里替换的是模块属性，并配合 _install_execution_audit 与受限导入；
    生产环境仍必须叠加操作系统级沙箱，不能把 Python 层策略当作强隔离。
    """
    if not allow_network:
        import socket as _socket
        def _deny(*args, **kwargs):
            raise OSError("network access is disabled in the sandbox")
        _socket.create_connection = _deny
        _socket.getaddrinfo = _deny
        socket_cls = _socket.socket
        try:
            socket_cls.connect = _deny
            socket_cls.connect_ex = _deny
            socket_cls.send = _deny
            socket_cls.sendall = _deny
            socket_cls.sendto = _deny
        except Exception:
            class _Blocked(socket_cls):
                """socket 子类兜底（当无法直接改写 socket.connect 等方法时使用）。"""

                def connect(self, *a, **k): raise OSError("network disabled")
                def connect_ex(self, *a, **k): raise OSError("network disabled")
                def sendall(self, *a, **k): raise OSError("network disabled")
                def send(self, *a, **k): raise OSError("network disabled")
                def sendto(self, *a, **k): raise OSError("network disabled")
            _socket.socket = _Blocked
    # 阻止全部常见进程/命令入口。用户代码可能先取函数别名，因此还要配合
    # audit hook；两层都只在当前子进程内生效，不影响宿主服务。
    import subprocess as _subprocess
    def _deny_exec(*args, **kwargs):
        raise RuntimeError("subprocess execution is disabled in the sandbox")
    _subprocess.Popen = _deny_exec
    _subprocess.run = _deny_exec
    _subprocess.call = _deny_exec
    _subprocess.check_output = _deny_exec
    _subprocess.check_call = _deny_exec
    _subprocess.getoutput = _deny_exec
    _subprocess.getstatusoutput = _deny_exec
    import os as _os_mod
    _process_entrypoints = (
        "system", "popen", "startfile",
        "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
        "execv", "execve", "execvp", "execvpe",
        "execl", "execle", "execlp", "execlpe",
        "posix_spawn", "posix_spawnp",
    )
    for _name in _process_entrypoints:
        if hasattr(_os_mod, _name):
            setattr(_os_mod, _name, _deny_exec)
    # Windows 的 os 实际由 nt 模块提供，用户可以显式 import nt 后绕过 os 别名。
    for _module_name in ("nt", "posix"):
        _module = sys.modules.get(_module_name)
        if _module is None:
            continue
        for _name in _process_entrypoints:
            if hasattr(_module, _name):
                setattr(_module, _name, _deny_exec)

    _install_execution_audit()


def _install_execution_audit() -> None:
    """安装不可绕过模块别名的进程与动态库审计拦截。

    动态库事件需要允许 numpy 等受信任依赖在导入时初始化；进程事件则无论
    调用方是谁都拒绝。用户代码直接调用 ctypes 时由受限导入器和调用栈判定拦截。
    """
    blocked_events = {
        "os.system", "os.spawn", "os.posix_spawn", "os.posix_spawnp",
        "os.exec", "subprocess.Popen", "_winapi.CreateProcess",
    }
    dynamic_events = {"ctypes.dlopen", "ctypes.dlsym"}
    blocked_import_roots = frozenset({"ctypes", "_ctypes", "_winapi"})

    def _user_frame(frame):
        """找到第一个业务调用方；site-packages 调用属于受信任依赖。"""
        stdlib_root = os.path.realpath(os.path.dirname(os.__file__))
        package_roots = []
        try:
            import site
            package_roots.extend(site.getsitepackages())
            user_site = site.getusersitepackages()
            if user_site:
                package_roots.append(user_site)
        except Exception:
            pass
        package_roots = tuple(os.path.realpath(path) for path in package_roots if path)
        while frame is not None:
            raw_filename = frame.f_code.co_filename
            # importlib 的 frozen frame 只是加载器实现，不是业务调用方。
            if str(raw_filename).startswith("<"):
                frame = frame.f_back
                continue
            filename = os.path.realpath(raw_filename)
            if filename == os.path.realpath(__file__):
                frame = frame.f_back
                continue
            if os.path.basename(filename) == "main.py":
                return True
            if any(_is_path_inside(filename, root) for root in package_roots):
                return False
            if _is_path_inside(filename, stdlib_root):
                frame = frame.f_back
                continue
            return True
        return False

    def _audit(event, _args):
        if event == "import" and _args:
            imported_root = str(_args[0]).split(".", 1)[0]
            if imported_root in blocked_import_roots and _user_frame(sys._getframe(1)):
                raise PermissionError(f"sandbox 禁止导入高风险模块：{imported_root}")
        if (
            event in blocked_events
            or event.startswith("os.exec")
            or event.startswith("os.spawn")
            or event.startswith("os.posix_spawn")
        ):
            raise PermissionError("sandbox 禁止进程执行和动态库加载")
        if event in dynamic_events and _user_frame(sys._getframe(1)):
            raise PermissionError("sandbox 禁止用户代码加载动态库")

    sys.addaudithook(_audit)


def _is_path_inside(path: str, root: str) -> bool:
    """判断绝对路径是否位于指定目录内，跨盘符时返回 False。"""
    try:
        return os.path.commonpath((path, root)) == root
    except ValueError:
        return False


def _install_file_audit(sandbox_root: str) -> None:
    """Constrain real file APIs, including ``io.open`` and ``os.open``.

    Replacing ``builtins.open`` alone is insufficient because imported
    modules retain their own module globals.  CPython emits the ``open`` audit
    event for builtins, ``io.open``, ``pathlib`` and ``os.open``.  Package and
    standard-library reads remain available from interpreter installation
    roots; user writes and all reads outside those roots must stay in the run
    directory.  The hook is a defense in depth layer and is intentionally
    paired with an OS-level sandbox in production.

    中文补充：这是文件访问的主要审计钩子，调用时机必须在可信初始化
    （字体安装、环境变量清理）之后、用户代码执行之前。判定规则：
    - 写类事件（open 带写模式/写 flag，以及 chmod/rename/remove/symlink 等）
      只能落在运行目录 root 内；
    - 读类事件允许运行目录 + 解释器/site-packages 根，保证第三方库可导入；
    - 目录列举（listdir/scandir/walk）按读规则处理。
    钩子一旦注册无法移除，会同时约束后续所有子模块的 I/O；违规一律抛 PermissionError。
    """
    import site
    import sysconfig

    root = os.path.realpath(os.path.abspath(sandbox_root))
    read_roots = {root}
    read_roots.update(
        os.path.realpath(os.path.abspath(path))
        for path in (sys.prefix, sys.base_prefix)
        if path and os.path.isdir(path)
    )
    config_paths = sysconfig.get_paths()
    read_roots.update(
        os.path.realpath(os.path.abspath(path))
        for path in config_paths.values()
        if path and os.path.isdir(path)
    )
    for path in (*getattr(site, "getsitepackages", lambda: [])(),
                 getattr(site, "getusersitepackages", lambda: "")()):
        if path and os.path.isdir(path):
            read_roots.add(os.path.realpath(os.path.abspath(path)))

    def _path(value):
        if isinstance(value, (str, bytes, os.PathLike)):
            try:
                return os.path.realpath(os.path.abspath(os.fsdecode(os.fspath(value))))
            except (OSError, TypeError, ValueError):
                return None
        return None

    def _inside(path, allowed):
        if path is None:
            return False
        try:
            return os.path.commonpath((allowed, path)) == allowed
        except ValueError:
            return False

    def _write_mode(mode, flags=0):
        if isinstance(mode, str) and any(token in mode for token in ("w", "a", "x", "+")):
            return True
        if isinstance(flags, int):
            return bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
        return False

    write_events = {
        "os.chmod", "os.chown", "os.link", "os.mkdir", "os.mkfifo", "os.mknod",
        "os.remove", "os.rename", "os.replace", "os.rmdir", "os.symlink",
        "os.truncate", "os.utime",
    }

    def _audit(event, args):
        if event == "open":
            path = _path(args[0] if args else None)
            mode = args[1] if len(args) > 1 else "r"
            flags = args[2] if len(args) > 2 else 0
            allowed = {root} if _write_mode(mode, flags) else read_roots
            if not any(_inside(path, candidate) for candidate in allowed):
                raise PermissionError("file access outside sandbox boundary is forbidden")
            return
        if event in {"os.listdir", "os.scandir", "os.walk"}:
            path = _path(args[0] if args else root) or root
            if not any(_inside(path, candidate) for candidate in read_roots):
                raise PermissionError("directory access outside sandbox boundary is forbidden")
            return
        if event in write_events:
            paths = [_path(value) for value in args]
            if not all(_inside(path, root) for path in paths if path is not None):
                raise PermissionError("filesystem mutation outside sandbox boundary is forbidden")

    sys.addaudithook(_audit)


def main():
    """工作进程主流程：读 job.json → 加固 → 执行用户代码 → 输出 result.json。

    协议：入参只有 argv[1]（任务文件路径）；结果同时写 cwd/result.json 并打印到
    stdout，provider 以 stdout 为准解析。退出码始终为 0（失败通过 status=failed
    表达），避免 provider 把业务失败误判成进程崩溃。
    关键顺序约束：目录/环境准备 → 图表字体安装（可信，需在读到边界之前）
    → _harden → _install_file_audit → 限制 builtins → exec。顺序调换会破坏隔离。
    SystemExit 处理：带 inputs 的工作流代码节点把任何提前退出都视为失败
    （包括 exit(0)），因为此时结果尚未产出；独立工具路径才按退出码判断。
    """
    if len(sys.argv) < 2:
        print(json.dumps({"status": "failed", "error": "missing job file"}))
        return
    job_file = sys.argv[1]
    with _io.open(job_file, "r", encoding="utf-8") as fh:
        job = json.load(fh)
    code = job.get("code", "")
    cwd = job.get("cwd")
    allow_network = bool(job.get("allow_network", False))
    sandbox_root = os.path.abspath(cwd or os.getcwd())
    if cwd:
        os.makedirs(sandbox_root, exist_ok=True)
        os.chdir(sandbox_root)
    _sanitize_environment(sandbox_root)
    for variable in ("TMP", "TEMP", "TMPDIR"):
        os.environ[variable] = sandbox_root
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    # Trusted renderer setup precedes the read boundary; only a private font copy
    # is reachable by generated code. No host font directory is whitelisted.
    if "matplotlib" in code or "seaborn" in code:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        from app.common.artifact_fonts import install_chart_fonts
        install_chart_fonts(sandbox_root)
    _harden(allow_network)
    _install_file_audit(sandbox_root)
    namespace = {
        "__name__": "__main__",
        "__builtins__": _restricted_builtins(sandbox_root),
        "OUTPUT_DIR": sandbox_root,
    }
    if "inputs" in job:
        namespace["inputs"] = job["inputs"]
        if set(job["inputs"]) == {"input"}:
            namespace["input"] = job["inputs"]["input"]
    stdout_buf = _io.StringIO()
    result_value = None
    status = "success"
    error = ""
    error_traceback = ""
    try:
        compiled = _compile_user_code(code, sandbox_root)
        with contextlib.redirect_stdout(stdout_buf):
            exec(compiled, namespace)
            if "result" in namespace:
                result_value = namespace["result"]
            elif "inputs" in job and callable(namespace.get("main")):
                result_value = namespace["main"](**job["inputs"])
            elif "inputs" in job:
                raise ValueError("代码节点没有输出：请设置 result，或定义 main 函数并返回结果")
            if "inputs" in job:
                # Workflow consumers need the complete typed output, not the
                # abbreviated preview historically used by standalone tools.
                try:
                    serialized = json.dumps(result_value, ensure_ascii=False, allow_nan=False)
                except (TypeError, ValueError, RecursionError) as exc:
                    raise ValueError("代码节点输出必须能转换为 JSON；请返回字典、列表、字符串或有限数值") from exc
                if len(serialized.encode("utf-8")) > 1024 * 1024:
                    raise ValueError("代码节点输出超过 1 MB，请减少结果或将大文件作为文件输出")
                result_value = json.loads(serialized)
    except SystemExit as exc:
        if exc.code not in (None, 0) or "inputs" in job:
            status = "failed"
            result_value = None
            error = "Python 代码提前退出，未完成执行" if "inputs" in job else f"Python 代码异常退出（退出码 {exc.code}）"
    except Exception as exc:
        status = "failed"
        result_value = None
        error = _friendly_error(exc)
        error_traceback = traceback.format_exc()
    output_files = []
    if os.path.isdir(sandbox_root):
        for root, directories, filenames in os.walk(sandbox_root):
            directories[:] = [d for d in directories if d != ".render_support"]
            for fname in sorted(filenames):
                p = os.path.join(root, fname)
                relative = os.path.relpath(p, sandbox_root)
                if relative.replace("\\", "/") not in ("job.json", "result.json"):
                    output_files.append(relative)
    out = {
        "status": status,
        "result": result_value if "inputs" in job else _safe(result_value),
        "stdout": stdout_buf.getvalue(),
        "stderr": error_traceback if status == "failed" else "",
        "output_files": output_files,
        "error": error if status == "failed" else None,
    }
    with _io.open(os.path.join(sandbox_root, "result.json"), "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, default=str)
    print(json.dumps(out, ensure_ascii=False, default=str))


def _safe(value, depth=0):
    """把任意返回值裁剪成可 JSON 序列化的安全形态（独立工具路径使用）。

    限制深度 3、每层最多 200 个元素，超限转字符串：独立工具只需要预览，
    不能让一个大对象把 stdout 撑爆；工作流代码节点（带 inputs）走的是完整
    序列化 + 1 MB 校验，不走本函数。
    """
    if depth > 3:
        return str(value)
    if isinstance(value, dict):
        return {str(k): _safe(v, depth + 1) for k, v in list(value.items())[:200]}
    if isinstance(value, (list, tuple)):
        return [_safe(v, depth + 1) for v in list(value)[:200]]
    if hasattr(value, "to_dict"):
        try:
            return _safe(value.to_dict(), depth + 1)
        except Exception:
            pass
    try:
        json.dumps(value)
        return value
    except Exception:
        return str(value)


def _sanitize_environment(cwd: str) -> None:
    """Remove inherited credentials and service configuration from user code.

    中文补充：采用白名单而非黑名单——只保留语言/编码/临时目录等安全变量
    （_SANDBOX_ENV_KEYS 与 LC_*/LANG 前缀），其余（含数据库密码、API Key、
    模型服务配置）全部清除，防止用户代码通过 os.environ 读取宿主机密。
    副作用：原地清空并覆盖本进程的 os.environ。
    """
    root = os.path.abspath(cwd)
    inherited = dict(os.environ)
    safe = {
        key: value for key, value in inherited.items()
        if key.upper() in _SANDBOX_ENV_KEYS or key.upper().startswith(("LC_", "LANG"))
    }
    safe.update({"TMP": root, "TEMP": root, "TMPDIR": root})
    os.environ.clear()
    os.environ.update(safe)


def _restricted_builtins(cwd: str) -> dict:
    """构造用户代码可见的 builtins 命名空间。

    移除 input/breakpoint（避免阻塞无交互进程）与 exec/eval（减少动态构造代码
    绕过静态改写的可能）；内建 open 替换为限定 cwd 的 safe_open，并显式拒绝
    ``dir_fd`` 参数（否则可借文件描述符绕过路径判断）。``__import__`` 保留，
    但对原生进程/动态库模块采用显式拒绝；常规 pandas/matplotlib 导入不受影响。
    返回：可直接作为 ``__builtins__`` 的 dict（注意是副本，不影响宿主进程）。
    """
    import builtins as _b
    b = dict(vars(_b))
    b.pop("input", None)
    b.pop("breakpoint", None)
    b.pop("exec", None)
    b.pop("eval", None)
    b.pop("__import__", None)
    # Keep open for normal document generation, but confine both reads and
    # writes.  Package imports use their own module globals and therefore are
    # unaffected; user-level file access cannot read host secrets by path.
    _orig_open = b["open"]
    def safe_open(name, mode="r", *args, **kwargs):
        if kwargs.get("dir_fd") is not None:
            raise PermissionError("sandbox open 不允许使用 dir_fd 绕过路径边界")
        try:
            n = os.path.abspath(os.fspath(name))
        except TypeError as exc:
            raise TypeError("sandbox open 需要文件路径") from exc
        root = os.path.abspath(cwd) if cwd else os.getcwd()
        try:
            inside = os.path.commonpath((root, n)) == root
        except ValueError:
            inside = False
        if not inside:
            raise PermissionError("file access outside sandbox cwd is forbidden")
        return _orig_open(name, mode, *args, **kwargs)
    b["open"] = safe_open
    # 只禁止会绕过 Python 层进程边界的原生模块。普通业务库仍使用原始导入器，
    # 避免为了防护而破坏 pandas、matplotlib、文档与图表生成。
    _blocked_import_roots = frozenset({"ctypes", "_ctypes", "_winapi"})
    _orig_import = _b.__import__

    def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        root = str(name).split(".", 1)[0]
        caller = sys._getframe(1)
        caller_file = os.path.realpath(caller.f_code.co_filename)
        user_call = (
            os.path.basename(caller_file) == "main.py"
            or _is_path_inside(caller_file, os.path.realpath(cwd))
        )
        if level == 0 and root in _blocked_import_roots and user_call:
            raise ImportError(f"sandbox 禁止导入高风险模块：{root}")
        return _orig_import(name, globals, locals, fromlist, level)

    b["__import__"] = safe_import
    return b


if __name__ == "__main__":
    main()
