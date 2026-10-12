"""Fetch the authorized Lynx report list and write the direct-report catalog."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.modules.reports.lynx_report_import import extract_catalog  # noqa: E402


DEFAULT_OUTPUT = BACKEND / "app" / "modules" / "reports" / "resources" / "lynx_source_reports.json"


def arguments() -> argparse.Namespace:
    """解析命令行参数：--url 为 Lynx 报表列表接口（必填）、--output 为输出 JSON 路径、
    --token-env 指定存放授权令牌的环境变量名（令牌只从环境变量读取，避免出现在命令行与进程列表里）。"""
    parser = argparse.ArgumentParser(description="Import every Lynx report_screen record as a direct Test5 report")
    parser.add_argument("--url", required=True, help="Lynx GET /api/report/list endpoint")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--token-env",
        default="TEST5_LYNX_REPORT_TOKEN",
        help="Environment variable containing the temporary Lynx authorization token",
    )
    return parser.parse_args()


def main() -> int:
    """拉取 Lynx 报表目录并写入输出文件，返回进程退出码。

    副作用：只写本地 JSON 文件（默认 backend/app/modules/reports/resources/lynx_source_reports.json），
    不访问本项目数据库；缺令牌或请求/解析失败时以 SystemExit 退出并只回报异常类型（不回显响应内容，避免泄露令牌）。
    """
    args = arguments()
    token = os.environ.get(args.token_env, "").strip()
    if not token:
        raise SystemExit(f"missing authorization token in environment variable {args.token_env}")
    request = Request(
        args.url,
        method="GET",
        headers={"Accept": "application/json", "Authorization": token},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"failed to read Lynx report catalog: {type(exc).__name__}") from exc
    catalog = extract_catalog(payload, source_url=args.url)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"imported {catalog['reportCount']} Lynx reports "
        f"({catalog['enabledReportCount']} enabled), catalog sha256={catalog['catalogSha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
