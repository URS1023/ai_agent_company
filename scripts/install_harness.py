"""校验仓库携带的 DSH 源码快照，安装固定依赖并构建宿主运行环境。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


ROOT = Path(__file__).resolve().parents[1]
SERVICE = ROOT / "services" / "deepseek_harness"
DEFAULT_TARGET = ROOT / ".implementation" / "harness_migration" / "upstream"


def verify(directory: Path, inventory: dict) -> None:
    """逐文件核对固定清单，路径和哈希不符立即中止，允许构建产物存在。"""
    for item in inventory["files"]:
        path = (directory / item["path"]).resolve()
        if not path.is_relative_to(directory):
            raise RuntimeError("校验清单包含越界路径")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise RuntimeError(f"上游快照不一致：{item['path']}")


def unpack_bundled_source(target: Path, inventory: dict) -> None:
    """按锁清单解包源码，确保全新克隆不依赖开发者本机的原始目录。"""
    vendor = ROOT / "vendor"
    receipt = json.loads((vendor / "deepseek-harness-source.json").read_text(encoding="utf-8"))
    archive_path = vendor / receipt["archive"]
    if not archive_path.is_file():
        raise RuntimeError("缺少随仓库交付的 DSH 源码包，请通过 --source 指定原始源码。")
    if hashlib.sha256(archive_path.read_bytes()).hexdigest() != receipt["sha256"]:
        raise RuntimeError("DSH 源码包校验失败。")
    expected = {item["path"]: item for item in inventory["files"]}
    seen = set()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            item = expected.get(member.name)
            if not member.isfile() or item is None or member.name in seen:
                raise RuntimeError(f"源码包包含非清单文件：{member.name}")
            destination = (target / member.name).resolve()
            if not destination.is_relative_to(target) or member.size != item["bytes"]:
                raise RuntimeError(f"源码包路径或大小不一致：{member.name}")
            stream = archive.extractfile(member)
            if stream is None:
                raise RuntimeError(f"源码文件不可读取：{member.name}")
            payload = stream.read()
            if hashlib.sha256(payload).hexdigest() != item["sha256"]:
                raise RuntimeError(f"源码包文件哈希不一致：{member.name}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(payload)
            seen.add(member.name)
    if seen != set(expected):
        raise RuntimeError("DSH 源码包不完整。")
    verify(target, inventory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="用户提供的原始源码目录")
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--unpack-only", action="store_true", help="只校验并解包，不安装依赖或构建")
    args = parser.parse_args()
    inventory = json.loads((SERVICE / "UPSTREAM.json").read_text(encoding="utf-8"))
    source = args.source.resolve() if args.source else None
    target = args.target.resolve()
    if source:
        if not source.is_dir():
            raise RuntimeError("未找到 --source 指定的原始源码目录。")
        verify(source, inventory)
        print(f"原始快照校验通过：{inventory['file_count']} 个文件，版本 {inventory['version']}", flush=True)
    if args.verify_only:
        verify(source or target, inventory)
        print("DSH 源码锁清单校验通过。", flush=True)
        return 0
    if source and (target == source or target.is_relative_to(source) or source.is_relative_to(target)):
        raise RuntimeError("安装目录必须与原始源码目录分离")
    # 不覆盖既有源码改动；允许重用锁文件匹配的已安装副本。
    if target.exists() and any(target.iterdir()):
        verify(target, inventory)
    elif source:
        for item in inventory["files"]:
            destination = target / item["path"]
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source / item["path"], destination)
        verify(target, inventory)
    else:
        unpack_bundled_source(target, inventory)
    if args.unpack_only:
        print(f"DSH 源码已就绪：{target}", flush=True)
        return 0
    node = shutil.which("node")
    npx = shutil.which("npx.cmd" if os.name == "nt" else "npx")
    if not node or not npx:
        raise RuntimeError("需要 Node 22.19+（22.x）或 Node 24+，以及 npm/npx")
    version = subprocess.check_output([node, "--version"], text=True).strip()
    major, minor, *_ = map(int, version.lstrip("v").split("."))
    if not (major >= 24 or (major == 22 and minor >= 19)):
        raise RuntimeError(f"Node 版本不受上游支持：{version}")
    env = dict(os.environ, LEFTHOOK="0", CI="true")
    # 禁止依赖安装器向父项目写入 Git hooks；保留必要的原生依赖安装脚本。
    commands = [
        ([npx, "--yes", "pnpm@11.7.0", "install", "--frozen-lockfile", "--network-concurrency=4"], target),
        # Linux/macOS 的会话持久化依赖原生文件锁；源码包不携带其他机器的编译产物。
        ([npx, "--yes", "pnpm@11.7.0", "run", "build:native-system"], target),
        ([npx, "--yes", "pnpm@11.7.0", "run", "build:lib:host"], target),
        ([npx, "--yes", "pnpm@11.7.0", "run", "build:lib:client"], target),
        ([node, str(SERVICE / "build.mjs")], SERVICE),
    ]
    for command, cwd in commands:
        subprocess.run(command, cwd=cwd, env=env, check=True)
    for path in (target / "apps/cli/lib/bin.js", SERVICE / "lib/company-runtime.js"):
        if not path.is_file():
            raise RuntimeError(f"构建缺少必需产物：{path}")
    if source:
        verify(source, inventory)
    receipt = {"upstream_version": inventory["version"], "node": version,
               "package_manager": inventory["package_manager"], "source": str(source) if source else "bundled-source",
               "target": str(target), "manifest_sha256": hashlib.sha256((SERVICE / "UPSTREAM.json").read_bytes()).hexdigest(),
               "original_source_unchanged": True, "status": "built"}
    (target.parent / "installation.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
    print("构建完成；请继续运行原生验收，安装成功不代表业务质量验收已通过。")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
