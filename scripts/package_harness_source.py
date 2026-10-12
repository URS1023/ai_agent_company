"""把已锁定的上游源码封装成可随仓库交付的可重现压缩包。"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
from pathlib import Path
import tarfile

from install_harness import ROOT, SERVICE, verify


def main() -> None:
    """只打包锁清单内的源码，不包含依赖、运行数据或生成文件。"""
    inventory = json.loads((SERVICE / "UPSTREAM.json").read_text(encoding="utf-8"))
    source = Path(inventory["source_directory"]).resolve()
    verify(source, inventory)
    vendor = ROOT / "vendor"
    vendor.mkdir(exist_ok=True)
    destination = vendor / f"deepseek-harness-{inventory['version']}.tar.gz"
    with destination.open("wb") as output:
        with gzip.GzipFile(filename="", mode="wb", fileobj=output, mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode="w|") as archive:
                for item in sorted(inventory["files"], key=lambda row: row["path"]):
                    payload = (source / item["path"]).read_bytes()
                    entry = tarfile.TarInfo(item["path"])
                    entry.size = len(payload)
                    entry.mode = 0o644
                    entry.mtime = 0
                    archive.addfile(entry, io.BytesIO(payload))
    receipt = {"version": inventory["version"], "archive": destination.name,
               "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
               "file_count": inventory["file_count"], "source_bytes": inventory["total_bytes"],
               "archive_bytes": destination.stat().st_size,
               "manifest": "services/deepseek_harness/UPSTREAM.json"}
    (vendor / "deepseek-harness-source.json").write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
