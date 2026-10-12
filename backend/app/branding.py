"""Single source of truth for the public product identity.

职责：全后端产品对外名称的唯一来源，并提供把历史品牌名替换为新名称的文本清洗函数。

系统位置：被静态页信息（/api/webinfo）、前端标题与需要对外输出文案的模块引用；
不要在别处硬编码产品名或再写一份品牌映射。

关键约束：PRODUCT_NAME 由中英文两段拼接而成，改动会同时影响 API 元信息与前端展示；
public_brand_text 只替换"展示用文案"，历史兼容标识符（库名/包名/环境变量等）必须原样保留，
否则会破坏既有数据与配置的对应关系。
"""

from __future__ import annotations

import re

PRODUCT_NAME_ZH = "智慧云脑"
PRODUCT_NAME_EN = "Cloud Brain"
PRODUCT_NAME = f"{PRODUCT_NAME_ZH} {PRODUCT_NAME_EN}"

# 旧品牌词表：仅用于把界面/文案里的历史名称替换为当前产品名，大小写不敏感。
_LEGACY_PUBLIC_BRANDS = re.compile(
    r"企业\s*AI\s*工作平台|企业智能平台|qknow|test5|lynx",
    flags=re.IGNORECASE,
)


def public_brand_text(value: object) -> str:
    """Replace former public brand labels while leaving compatibility IDs alone.

    参数：value 任意值，内部会转成字符串（None/空值按空串处理）。
    返回：替换过旧品牌名的文本；仅做字面替换，不改变其余内容。
    约束：只应用于展示文案，禁止用于文件名、数据库名、配置键等兼容标识符。
    """

    return _LEGACY_PUBLIC_BRANDS.sub(PRODUCT_NAME, str(value or ""))
