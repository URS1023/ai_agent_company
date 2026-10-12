"""业务技能清单同步（路线图 P2.5）：采集受控目录技能，发布/核对版本化 manifest。

用法（在项目根目录）：
  .\\.venv_py312\\Scripts\\python.exe -X utf8 scripts\\sync_business_skills.py           # 发布/更新 manifest
  .\\.venv_py312\\Scripts\\python.exe -X utf8 scripts\\sync_business_skills.py --verify  # 只读核对漂移

说明：执行路径始终是受控目录（octop_bridge），本脚本只做清单/版本/发布记录；
平台只读接口：GET /api/skills/business-inventory。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", action="store_true", help="只核对漂移，不写 manifest")
    parser.add_argument("--manifest", default=None, help="manifest 路径（默认 storage/business_skills_inventory.json）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)

    from app.modules.skill import inventory

    items = inventory.collect_inventory()
    manifest_path = Path(args.manifest) if args.manifest else inventory.MANIFEST_PATH
    if args.verify:
        result = inventory.drift(items, path=manifest_path)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"漂移技能: {result['drifted'] or '无'}｜manifest={result['manifest_version']}｜当前={result['expected_version']}")
        return 0
    result = inventory.sync_manifest(items, path=manifest_path, write=True)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"同步完成: 变更 {result['changed']}｜移除 {result['removed']}｜版本 {result['version']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())