# -*- coding: utf-8 -*-
"""Object storage abstraction.

Storage keys are unique and derived from content: like
  {category}/{workspace_short}/{yyyymm}/{id}_{safe_name}
The local provider persists under settings.STORAGE_ROOT. A S3-compatible
provider can be dropped in later without touching callers.

本地对象存储：文件/方案/预览图等二进制均落在这里，业务侧只持有 key 而不持有绝对路径。

路径约定：``{category}/{workspace_short}/{yyyymm}/{id}_{safe_name}``，由 ``build_key`` 生成
（工作区前 8 位 + 年月分桶，避免单目录堆文件；``id`` 保证唯一，``safe_name`` 只作可读后缀）。
key 不是任意路径：所有读写删都必须经 ``LocalStorage._full_path`` 归一化，越界一律拒绝
（防目录穿越），因此**不要**在业务代码里自行拼接 root 与 key。

写入方式：``put_bytes`` 直接覆盖写目标文件，**不是原子写**（没有临时文件 + rename，也没有
fsync），所以同 key 并发写或写入中途崩溃可能留下不完整/交错的文件。需要"要么完整要么没有"
的调用方应自行用唯一 key 落盘后再引用，或用不同 key + 数据库指针切换。

约束：本模块是同步阻塞 IO，勿在 async 请求路径里对大文件直接调用；``get_storage()``
每次返回新实例（无缓存、无连接池），实现无状态，可安全在多线程中各自持有。
"""
from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime
from pathlib import Path

from ..config import settings


def sha256_bytes(data: bytes) -> str:
    """计算字节内容的 SHA-256 指纹。

    用途：产物去重与完整性校验（例如判断同一份文件是否已入库）。
    返回 64 位小写十六进制字符串；无副作用，不写库、不碰磁盘。
    """
    return hashlib.sha256(data).hexdigest()


def safe_name(name: str) -> str:
    """把用户可控文件名清洗成可安全拼进 key 的一段文本。

    规则：把非 ``\\w``/点/横线的字符（空白、``/``、``\\``、``*`` 等）替换为下划线，
    压缩连续下划线，截断到 120 字符；清洗后为空时回落为 ``"file"``。
    注意：``\\w`` 对 str 是 Unicode 语义，因此中文保留原样（如"报告 2026.pdf" → "报告_2026.pdf"）。
    返回值只用于可读性与扩展名，唯一性由调用方传入的 ``file_id`` 保证。
    """
    name = re.sub(r"[^\w.\-]", "_", name)
    name = re.sub(r"_+", "_", name)
    return name[:120] or "file"


def build_key(category: str, workspace_id: str, file_id: str, name: str) -> str:
    """拼装存储 key：``{category}/{workspace_short}/{yyyymm}/{file_id}_{safe_name}``。

    参数：``category`` 业务分类（如 ``artifacts``）；``workspace_id`` 用于隔离与分桶；
    ``file_id`` 必须是全局唯一标识（同 key 会被覆盖写）；``name`` 仅经 ``safe_name`` 作为
    可读后缀，不参与唯一性。返回相对 key（相对 ``settings.STORAGE_ROOT``），不含前导斜杠。
    """
    yyyymm = datetime.utcnow().strftime("%Y%m")
    ws_short = workspace_id[:8]
    return f"{category}/{ws_short}/{yyyymm}/{file_id}_{safe_name(name)}"


class LocalStorage:
    """本地文件系统存储实现（``provider = "local"``）。

    生命周期：无状态、可随时构造（``root`` 在构造时 resolve 为绝对路径），不持有文件句柄，
    因此不存在"关闭"语义；调用方按需 ``get_storage()`` 即可，不要缓存跨请求的实例做缓存语义。

    关键不变式：任何 key 经 ``_full_path`` 解析后必须仍在 ``root`` 之内，
    越界（含恰好等于 root 本身）抛 ``ValueError("invalid storage key")``。
    """

    provider = "local"

    def __init__(self, root: str | None = None):
        """``root`` 省略时取 ``settings.STORAGE_ROOT``；传入自定义 root 主要用于测试与预览脚本。

        构造时即 resolve 为绝对路径（含符号链接解析），因此之后比较 key 的绝对路径是可靠的。
        """
        self.root = Path(root or settings.STORAGE_ROOT).resolve()

    def _full_path(self, key: str) -> Path:
        """把 key 解析为 root 下的绝对路径，并拒绝越界。

        参数 ``key`` 为 ``build_key`` 生成的相对 key（允许带前导 ``/``，会先去掉）。
        越界或指向 root 自身时抛 ``ValueError``；这是防目录穿越的唯一关卡，
        新增存储方法必须复用它，不要直接用 ``self.root / key``。
        """
        normalized = Path(key).as_posix().lstrip("/")
        p = (self.root / normalized).resolve()
        if p == self.root or not p.is_relative_to(self.root):
            raise ValueError("invalid storage key")
        return p

    def put_bytes(self, key: str, data: bytes) -> str:
        """把整块字节写入 key 对应文件，返回原 key（便于链式传回业务层）。

        副作用：创建父目录（``parents=True, exist_ok=True``）并**直接覆盖写**目标文件。
        写入**非原子**：无临时文件 + rename、无 fsync，同 key 并发写会出现内容交错；
        破坏性覆盖不备份旧内容。异常（权限、磁盘满、key 越界）直接上抛，不吞、不重试。
        """
        p = self._full_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return key

    def save_fileobj(self, key: str, stream) -> str:
        """把类文件对象整体读入内存后落盘（``stream.read()`` 一次读完）。

        调用方需注意：上传大文件时会把全部内容驻留内存（这是本实现的已知边界，
        而非流式拷贝）；其余语义与 ``put_bytes`` 相同，返回 key。
        """
        data = stream.read()
        return self.put_bytes(key, data)

    def read_bytes(self, key: str) -> bytes:
        """读取 key 的全部字节；key 不存在时抛 ``FileNotFoundError``，越界抛 ``ValueError``。"""
        return self._full_path(key).read_bytes()

    def read_text(self, key: str, encoding="utf-8") -> str:
        """按文本读取 key 的内容，默认 UTF-8；解码失败抛 ``UnicodeDecodeError``（不做容错猜测）。"""
        return self._full_path(key).read_text(encoding=encoding)

    def get_path(self, key: str) -> str:
        """返回 key 对应的**绝对路径字符串**，供必须用真实路径的调用方使用（如外部进程/本地工具）。

        注意：返回值已脱离存储抽象与 root 约束（调用方可以用它访问 root 之外），
        只应在受信任的本地代码里使用；HTTP 响应不要直接暴露该路径。
        """
        return str(self._full_path(key))

    def delete(self, key: str) -> None:
        """删除文件或目录（目录递归删除），不可恢复。

        幂等：key 不存在时静默返回（调用方无需先 ``exists``）。
        越界 key 抛 ``ValueError``；删除失败（占用、权限）上抛 OSError。
        """
        p = self._full_path(key)
        if p.exists():
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()

    def exists(self, key: str) -> bool:
        """判断 key 是否存在（文件或目录均算存在）；越界 key 抛 ``ValueError`` 而非返回 False。"""
        return self._full_path(key).exists()

    def stat(self, key: str) -> dict:
        """返回 ``{"size": 字节数, "modified": mtime 秒}``。

        键名是调用方依赖的约定（如产物列表展示大小与时间），改名需同步调用方；
        key 不存在时抛 ``FileNotFoundError``（不做静默 0 值兜底）。
        """
        p = self._full_path(key)
        return {"size": p.stat().st_size, "modified": p.stat().st_mtime}


def get_storage() -> LocalStorage:
    """存储工厂：每次返回**新的** ``LocalStorage`` 实例（无缓存、无连接池）。

    ``root`` 每次从 ``settings.STORAGE_ROOT`` 读取，因此改配置后新实例立即生效；
    实例无状态，并发调用安全。
    """
    return LocalStorage()
