"""只读核对原始 DSH、项目内副本与固定清单，不导入应用或连接数据库。"""
from pathlib import Path
import hashlib
import json
import time


def main():
    """输出完整核对数量和差异路径，避免把版本号一致误当作源码一致。"""
    root = Path(__file__).resolve().parents[2]
    manifest = json.loads((root / "services/deepseek_harness/UPSTREAM.json").read_text(encoding="utf-8"))
    targets = {
        "original": Path(manifest["source_directory"]),
        "embedded": root / ".implementation/harness_migration/upstream",
    }
    started = time.monotonic()
    result = {"manifest_version": manifest["version"], "manifest_files": len(manifest["files"]), "targets": {}}
    for name, directory in targets.items():
        detail = {"directory": str(directory), "matched": 0, "changed": [], "missing": []}
        for entry in manifest["files"]:
            path = directory / entry["path"]
            if not path.is_file():
                detail["missing"].append(entry["path"])
                continue
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest == entry["sha256"]:
                detail["matched"] += 1
            else:
                detail["changed"].append(entry["path"])
        result["targets"][name] = detail
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    (Path(__file__).parent / "snapshot-comparison.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
