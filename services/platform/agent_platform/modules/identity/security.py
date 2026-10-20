"""权限委派、服务令牌边界与原生入口的共用校验。"""

import re

from ...core.context import Actor
from ...core.errors import DomainError

PERMISSION_RE = re.compile(r"^(?:\*|m(?:0[1-9]|1[0-7])\.(?:read|write|delete|publish|admin|audit|\*))$")
ACTIONS = ("read", "write", "delete", "publish", "admin", "audit")
PERMISSION_UNIVERSE = tuple(f"m{index:02}.{action}" for index in range(1, 18) for action in ACTIONS)


def validate_delegation(actor: Actor, permissions: list[str]) -> list[str]:
    """授予通配符要求持有同等通配权限，不能用少数具体权限放大成未来全权。"""
    if not isinstance(permissions, list) or len(permissions) > 100 or any(not isinstance(p, str) or not PERMISSION_RE.fullmatch(p) for p in permissions):
        raise DomainError(422, "invalid_permissions", "请选择有效的模块权限")
    for permission in permissions:
        if permission == "*":
            allowed = "*" in actor.permissions
        elif permission.endswith(".*"):
            allowed = "*" in actor.permissions or permission in actor.permissions
        else:
            allowed = actor.can(permission)
        if not allowed:
            raise DomainError(403, "permission_delegation_denied", "不能授予当前账号未拥有的权限")
    return sorted(set(permissions))
