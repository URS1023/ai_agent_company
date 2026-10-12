# -*- coding: utf-8 -*-
"""Model profile management and capability-aware connectivity probes.

中文补充（模型治理服务层）：本文件是"模型角色配置 + 连通性探测 + 计价/用量账本 + 预算闸门"
的唯一实现处，路由（``router.py``，前缀 ``/api/models``）只是它的薄封装；``providers/model.py``
（Model Gateway）在每次真实调用前后分别调用 ``enforce_budget`` 与 ``record_usage``，因此本文件的
这两个函数与 ``model_profiles`` 的 ``capabilities_json`` 结构是对外契约。

角色（profile）解析与探测：
- "生效配置" = 工作区数据库行（``model_profiles``，status=active）优先，否则环境默认值
  （``_environment_defaults``：reasoning/embedding/vision，见 ``settings.AGNES_*`` / ``SILICONFLOW_*``）；
- 探测按角色能力选择通道（向量/视觉/对话），**会真实出网**并消耗供应商额度，结果落
  ``model_probe_results``（有配置行）或 ``environment_model_probes``（纯环境默认）。

密钥引用方式：本文件只保存/读取"引用"，明文交给 ``modules.secret``，引用 id 存在
``capabilities_json["secret_id"]``；保存新密钥时吊销旧密钥（软删除）而不是删除行；
没有引用 id 的老数据按名字（profile id / profile 名 / ``model-key-<角色>``）回退查找，
这与运行时 ``providers/model.py._resolve_key_for_profile`` 的回退顺序必须保持一致。

计价快照与用量账本：单价随 profile 一起存在 ``capabilities_json["pricing"]``，每次调用把当时的
单价快照写进 ``model_usage_records``（``record_usage``，独立短事务），历史费用不因事后改价而变化；
未配置单价时 ``priced=False``、``cost`` 为空，用量页把它们统计为"未计价调用"。

**已知约束（务必牢记）**：预算硬限制只累加"``priced=True`` 且币种等于策略币种"的行，所以
**未配置价格时"超额阻止调用"永远不会触发**（spend 恒为 0，界面上的勾选项只是个意图声明）；
同样，与策略币种不同的计价也不计入当月花费。详见 ``enforce_budget`` 的说明。

其他边界：模型 ``endpoint`` 只校验 ``http(s)://`` 前缀，探测/调用**不做 SSRF 校验**
（不拦内网、回环、云元数据地址），且会把工作区密钥发往该地址。
"""
from __future__ import annotations

import io
import hashlib
import json
import re
import time
from datetime import date, datetime, time as datetime_time, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...common.authorization import require_permission
from ...common.data_scope import bind_data_scope
from ...common.deps import AuthContext
from ...config import settings
from ...errors import ModelError, NotFoundError, PermissionErrorEx, ValidationError
from ...ids import new_id
from ...models import ModelProbeResult, ModelProfile, Secret, Workspace
from .models import EnvironmentModelProbe, ModelUsageRecord
from .runtime_options import normalize_runtime_options, public_runtime_options

PROFILE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,99}$")
ROLE_ORDER = {"reasoning": 0, "embedding": 1, "vision": 2}
ALLOWED_CURRENCIES = {"CNY", "USD", "EUR", "JPY"}
_PROBE_ERRORS = {
    "balance": "模型账户额度不足，请检查供应商账户。",
    "rate_limit": "模型服务当前限流，请稍后重新测试。",
    "incomplete": "模型响应不完整，请检查模型协议与能力配置。",
    "authentication": "模型认证或访问权限校验失败，请检查专用密钥和账户权限。",
    "unavailable": "模型服务暂不可用，请检查端点和供应商状态。",
    "connection": "模型连接未完成，请检查端点和网络状态。",
    "generic": "模型探测失败，请检查端点、密钥、模型名称及能力配置。",
}


def _environment_defaults() -> dict[str, dict]:
    """返回三种内置模型角色的环境默认配置（不读库，只读 ``settings``）。

    键名 ``reasoning`` / ``embedding`` / ``vision`` 是平台约定的角色名，必须与
    ``providers/model.py`` 的调用方（如 chat_for_run("reasoning")）以及前端档位一致，改名等于换契约。
    ``key_configured`` 只表示"部署环境是否给了密钥"，与工作区数据库里的专用密钥互不相干；
    ``endpoint`` 是建议值，管理员保存 profile 时会按需覆盖。
    用途：作为列表/市场的兜底值；探测时若端点与这里的端点**逐字符相同**，运行时才允许复用环境密钥。
    """
    return {
        "reasoning": {
            "provider": "agnes",
            "model": settings.AGNES_CHAT_MODEL,
            "endpoint": settings.AGNES_BASE_URL,
            "capabilities": ["chat", "reasoning", "tools"],
            "key_configured": bool(settings.AGNES_API_KEY),
        },
        "embedding": {
            "provider": "siliconflow",
            "model": settings.SILICONFLOW_EMBED_MODEL,
            "endpoint": settings.SILICONFLOW_BASE_URL,
            "capabilities": ["embedding", "semantic-search"],
            "key_configured": bool(settings.SILICONFLOW_API_KEY),
        },
        "vision": {
            "provider": "siliconflow",
            "model": settings.SILICONFLOW_VISION_MODEL,
            "endpoint": settings.SILICONFLOW_BASE_URL,
            "capabilities": ["vision", "ocr", "charts", "documents"],
            "key_configured": bool(settings.SILICONFLOW_VISION_API_KEY),
        },
    }


def _environment_fingerprint(profile_name: str) -> str:
    """算环境默认配置的稳定指纹，用于判断历史探测是否还代表"当前这套部署配置"。

    对默认配置字典做 ``sort_keys`` 的 JSON + sha256，避免字典顺序影响结果；
    部署改了模型名/端点/能力后指纹变化，旧探测记录自然不再被展示（无需清理历史行）。
    """
    default = _environment_defaults().get(profile_name, {})
    return hashlib.sha256(json.dumps(default, sort_keys=True).encode()).hexdigest()


def list_effective_profiles(db: Session, workspace_id: str) -> list[dict]:
    """列出工作区"当前真正生效"的模型角色（数据库覆盖优先，环境默认兜底）。

    参数：``db`` 为请求/调用方会话（只读查询，本函数不 commit）；``workspace_id`` 必填，
    所有查询都按它过滤，绝不返回其他工作区的配置。

    返回：按 ``ROLE_ORDER``（reasoning → embedding → vision → 其他按名）排序的 dict 列表，
    每项含 ``id/source/api_key_configured/secret_source/last_probe/pricing`` 等展示字段。
    只暴露"是否已配置密钥"，不含密钥值、密文或引用 id。

    关键语义：
    - ``source=database`` 时以最新 active 行为准（按 ``updated_at`` 倒序取第一条）；
      没有引用 id 的老数据回退按名字找密钥（profile id / profile 名 / ``model-key-<角色>``），
      该回退可能命中"任意一条同名密钥"，因此 ``api_key_configured`` 是尽力而为的可用性提示；
    - ``secret_source=environment`` 只在"该行端点与环境默认端点一模一样"时成立（复用环境密钥），
      与运行时 ``_environment_key_for_profile`` 的判定保持一致，避免页面显示"已配置"但实际发不出请求；
    - ``source=environment`` 表示没有数据库行，探测历史改读 ``environment_model_probes``
      并要求 fingerprint 与当前环境配置一致，避免展示过期结论；
    - 消费方：``GET /api/models/profiles`` 与 ``modules/chat/model_selection.py``（对话模型选择器），
      后者据此过滤出可用于对话的档位。
    """
    rows = db.execute(
        select(ModelProfile)
        .where(
            ModelProfile.workspace_id == workspace_id,
            ModelProfile.status == "active",
        )
        .order_by(ModelProfile.updated_at.desc())
    ).scalars().all()
    latest: dict[str, ModelProfile] = {}
    for row in rows:
        # 已按 updated_at 倒序，setdefault 保证同名角色取最新那一行。
        latest.setdefault(row.profile_name, row)
    defaults = _environment_defaults()
    names = list(defaults)
    names.extend(name for name in latest if name not in defaults)
    items = []
    for name in names:
        row = latest.get(name)
        default = defaults.get(name, {})
        if row:
            capabilities_json = row.capabilities_json or {}
            secret_id = capabilities_json.get("secret_id")
            secret_ready = _secret_is_ready(db, workspace_id, secret_id)
            environment_ready = False
            if not secret_id:
                # Keep legacy references and deployment endpoint restrictions in
                # agreement with the actual provider credential resolver.
                #
                # 中文补充：与 providers/model.py 的回退顺序一致；只要端点与环境默认端点在字面上
                # 不同，运行时就不会复用环境密钥，这里也必须显示"未配置密钥"，否则会给管理员
                # 一个发不出请求的"已配置"假象。
                legacy_secret = db.scalar(select(Secret).where(
                    Secret.workspace_id == workspace_id,
                    Secret.name.in_([row.id, row.profile_name, f"model-key-{row.profile_name}"]),
                    Secret.status == "active", Secret.deleted_at.is_(None),
                ))
                secret_ready = legacy_secret is not None
                from ...providers.model import _environment_key_for_profile
                environment_ready = bool(_environment_key_for_profile(row))
            key_configured = secret_ready or environment_ready
            last_probe = db.execute(
                select(ModelProbeResult)
                .where(
                    ModelProbeResult.workspace_id == workspace_id,
                    ModelProbeResult.profile_id == row.id,
                )
                .order_by(ModelProbeResult.created_at.desc())
            ).scalars().first()
            items.append(
                {
                    "id": row.id,
                    "profile_name": row.profile_name,
                    "provider": row.provider,
                    "model": row.model,
                    "endpoint": row.endpoint,
                    "enabled": row.enabled,
                    "capabilities": capabilities_json.get("capabilities") or [],
                    "runtime_options": public_runtime_options(capabilities_json),
                    "status": row.status,
                    "source": "database",
                    "api_key_configured": key_configured,
                    "secret_source": "profile" if secret_ready else (
                        "environment" if environment_ready else "missing"
                    ),
                    "last_probe": _probe_to_dict(last_probe),
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                    "pricing": _pricing_out(capabilities_json.get("pricing")),
                }
            )
        else:
            # 环境默认角色的探测历史带配置指纹，只有指纹一致（即部署配置没变）才回显。
            last_probe = db.scalar(select(EnvironmentModelProbe).where(
                EnvironmentModelProbe.workspace_id == workspace_id,
                EnvironmentModelProbe.profile_name == name,
                EnvironmentModelProbe.config_fingerprint == _environment_fingerprint(name),
            ).order_by(EnvironmentModelProbe.created_at.desc()).limit(1))
            items.append(
                {
                    "id": None,
                    "profile_name": name,
                    "provider": default.get("provider", ""),
                    "model": default.get("model", ""),
                    "endpoint": default.get("endpoint", ""),
                    "enabled": True,
                    "capabilities": default.get("capabilities", []),
                    "runtime_options": {},
                    "status": "active",
                    "source": "environment",
                    "api_key_configured": bool(default.get("key_configured")),
                    "secret_source": (
                        "environment" if default.get("key_configured") else "missing"
                    ),
                    "last_probe": _probe_to_dict(last_probe),
                    "updated_at": None,
                    "pricing": _pricing_out(default.get("pricing")),
                }
            )
    # 固定角色顺序（reasoning/embedding/vision 在前），其余自定义角色按名字排在后面，
    # 让前端的档位顺序在多次请求间保持稳定。
    return sorted(
        items,
        key=lambda item: (
            ROLE_ORDER.get(item["profile_name"], 99),
            item["profile_name"],
        ),
    )


def upsert_profile(
    db: Session,
    *,
    ctx: AuthContext,
    workspace_id: str,
    owner_user_id: str,
    profile_name: str,
    provider: str,
    model: str,
    endpoint: str,
    api_key: str = "",
    capabilities: Optional[list[str]] = None,
    runtime_options: Optional[dict] = None,
    pricing: Optional[dict] = None,
    enabled: bool = True,
) -> ModelProfile:
    """新增或覆盖保存一个模型角色配置（写入方：POST/PUT ``/api/models/profiles*``）。

    幂等 upsert：同一 ``profile_name`` 已存在 active 行时更新它，不存在则新建；
    调用方（路由层）负责最终 ``commit`` 与写审计，本函数只 ``flush`` 让 `row.id` 可用。
    ``ctx`` 用于锁内校验已有名称的 edit 权限和数据范围，创建权不能覆盖既有配置。

    参数要点：
    - ``profile_name`` 必须匹配 ``PROFILE_NAME_RE``（字母开头，2–100 位），因为它同时被用作
      运行时调用名与前端档位名；``endpoint`` 只校验 ``http(s)://`` 前缀并去掉尾部斜杠，
      **不校验私网/回环/云元数据地址**（已知边界：具备配置权者可让服务器请求任意地址）；
    - ``api_key`` 为空串表示"保持现有密钥不变"；非空则新建密钥并吊销该角色旧密钥（软删除，
      保留行便于追溯），新引用 id 写回 ``capabilities_json["secret_id"]``；
    - ``capabilities`` 去重且保序；``pricing=None`` 或两档价格全空会被规范成 ``{}``，即"未计价"，
      这会让该角色的调用全部记成 ``priced=False``，预算硬限制因此永远不会被触发；

    并发与一致性：先对 ``Workspace`` 行 ``with_for_update()`` 加锁（工作区行在第一个 profile 之前
    就存在），把"写 profile + 换密钥"串行化，避免两个管理员交叉保存导致活跃引用指向被吊销的密钥。
    查询同角色 active 行时用 ``populate_existing=True`` 读库内最新值（绕开会话标识映射缓存），
    并把历史重复行标为 ``superseded``（``enabled=False``），保证"一个角色只有一行生效"。

    异常：角色名/模型名/端点/供应商/价格不合法抛 ValidationError；工作区行不存在抛 NotFoundError。
    副作用：写 ``model_profiles``（含 capabilities_json 快照）与 ``secrets``（新建/吊销）。
    """
    clean_name = profile_name.strip()
    if not PROFILE_NAME_RE.match(clean_name):
        raise ValidationError("Profile 名仅支持字母开头的字母、数字、下划线和连字符")
    clean_model = model.strip()
    clean_endpoint = endpoint.strip().rstrip("/")
    clean_provider = provider.strip().lower()
    if not clean_model:
        raise ValidationError("模型名称不能为空")
    if not clean_endpoint.startswith(("http://", "https://")):
        raise ValidationError("模型端点必须是 http:// 或 https:// 地址")
    if not clean_provider:
        raise ValidationError("模型供应商不能为空")
    normalized_pricing = normalize_pricing(pricing)

    # A workspace row also exists before the first profile. Hold this lock
    # until the caller commits both the profile and its new secret, so two
    # administrators cannot overwrite the reference and leave an active orphan.
    #
    # 中文补充：锁是覆盖"写 profile + 建/吊销密钥"整个调用方事务的，因此路由层必须在本函数
    # 返回后立刻 commit（中间不要再插入长耗时操作），否则同一工作区的保存会互相排队等待。
    if db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update()) is None:
        raise NotFoundError("工作区不存在")

    # 名称占用检查只读取本工作区的 ID，不能让查询范围把隐藏配置误判成新配置。
    # 在工作区锁内切换到 edit 的独立数据范围，create 的宽权限不能替代 edit。
    table = ModelProfile.__table__
    existing_ids = set(db.scalars(select(table.c.id).where(
        table.c.workspace_id == workspace_id,
        table.c.profile_name == clean_name,
        table.c.status == "active",
    )).all())
    if existing_ids:
        require_permission(ctx, "models.edit")
        bind_data_scope(ctx, "models.edit")

    rows = db.execute(
        select(ModelProfile)
        .where(
            ModelProfile.workspace_id == workspace_id,
            ModelProfile.profile_name == clean_name,
            ModelProfile.status == "active",
        )
        .order_by(ModelProfile.updated_at.desc())
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalars().all()
    if existing_ids != {item.id for item in rows}:
        raise PermissionErrorEx("模型配置不在当前修改权限范围内")
    row = rows[0] if rows else None
    # 历史重复行（并发保存或旧数据遗留）统一作废，只保留最新一行参与运行时解析。
    for duplicate in rows[1:]:
        duplicate.enabled = False
        duplicate.status = "superseded"
        duplicate.updated_at = datetime.utcnow()

    current_capabilities = (row.capabilities_json or {}) if row else {}
    # 旧管理页尚未提交高级参数时保留它们；空对象才表示明确重置。
    normalized_runtime = normalize_runtime_options(
        current_capabilities.get("runtime_options") if runtime_options is None else runtime_options, clean_endpoint)
    secret_id = current_capabilities.get("secret_id")
    if api_key.strip():
        # 只有显式传了非空 api_key 才动密钥；空串是"不改动"而不是"清空"。
        from ..secret.service import create_secret

        secret = create_secret(
            db,
            workspace_id=workspace_id,
            owner_user_id=owner_user_id,
            name=f"model-key-{clean_name}-{new_id()}",
            value=api_key.strip(),
            provider="api_key",
        )
        # 旧密钥吊销而非删除：保留密文行以便审计和解密历史引用，同时确保运行时按引用 id
        # 找不到它（_resolve_key_for_profile 要求 status=active 且未软删除）。
        previous_secret = db.get(Secret, secret_id) if secret_id else None
        if previous_secret and previous_secret.workspace_id == workspace_id:
            previous_secret.status = "revoked"
            previous_secret.deleted_at = datetime.utcnow()
            previous_secret.updated_at = datetime.utcnow()
        secret_id = secret.id

    # capabilities_json 同时承载"能力清单 + 密钥引用 + 计价快照"三件事，运行时读的就是这份结构，
    # 改键名等于改 providers/model.py 的解析契约。
    payload = {
        "capabilities": list(dict.fromkeys(capabilities or [])),
        "secret_id": secret_id,
        "pricing": normalized_pricing,
        "runtime_options": normalized_runtime,
    }
    now = datetime.utcnow()
    if row:
        row.provider = clean_provider
        row.model = clean_model
        row.endpoint = clean_endpoint
        row.enabled = bool(enabled)
        row.capabilities_json = payload
        row.updated_at = now
    else:
        row = ModelProfile(
            id=new_id(),
            workspace_id=workspace_id,
            profile_name=clean_name,
            provider=clean_provider,
            model=clean_model,
            endpoint=clean_endpoint,
            enabled=bool(enabled),
            capabilities_json=payload,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(row)
    db.flush()
    return row


def disable_profile(db: Session, *, workspace_id: str, profile_id: str) -> None:
    """停用模型配置行并吊销其专用密钥（实现 ``DELETE /api/models/profiles/{id}``）。

    参数：``profile_id`` 必须是本工作区的 active 行，否则抛 NotFoundError（跨工作区 id 同样 404，
    不区分"不存在"与"不属于当前工作区"）。调用方负责 commit。

    语义（界面文案"模型配置已停用"与之对应）：只把 ``enabled`` 置 false，**不删除行、不改 status**，
    该行会继续遮蔽环境默认配置 —— 运行时 ``_profile_config`` 命中这行后会直接抛
    "此模型配置已停用"，环境默认值不会自动接管；要恢复环境默认需重新保存该角色或清理该行。
    先对 ``Workspace`` 行加锁并与 ``upsert_profile`` 互斥，避免"停用"和"保存"交叉执行后留下
    指向已吊销密钥的活跃行。

    副作用：写 ``model_profiles``（enabled/updated_at）与 ``secrets``（吊销 + 软删除时间），
    并刷新 ``updated_at`` 让这一行继续是"该角色的最新行"（否则旧行可能被解析为生效配置）。
    """
    db.scalar(select(Workspace).where(Workspace.id == workspace_id).with_for_update())
    row = db.scalar(select(ModelProfile).where(
        ModelProfile.id == profile_id, ModelProfile.workspace_id == workspace_id,
    ).with_for_update().execution_options(populate_existing=True))
    if not row or row.workspace_id != workspace_id or row.status != "active":
        raise NotFoundError("模型配置不存在")
    row.enabled = False
    # The UI action is "disable", including when served via DELETE. Retain
    # the effective workspace override so defaults cannot restart the model;
    # saving this profile name later updates it and can install a fresh key.
    row.updated_at = datetime.utcnow()
    capabilities = row.capabilities_json or {}
    secret_id = capabilities.get("secret_id")
    secret = db.get(Secret, secret_id) if secret_id else None
    if secret and secret.workspace_id == workspace_id:
        secret.status = "revoked"
        secret.deleted_at = datetime.utcnow()
        secret.updated_at = datetime.utcnow()
    db.flush()


def probe_effective_profile(
    db: Session,
    *,
    workspace_id: str,
    profile_name: str,
) -> dict:
    """对某个角色的"生效配置"做一次真实连通性探测，并把结论落库。

    探测通道按角色名/能力选择（能力来自 ``_profile_config`` 解析结果）：
    - ``embedding``（或能力含 embedding）→ ``embed_texts`` 生成一次向量，返回维度数；
    - ``vision``（或能力含 vision/ocr）→ 用本地生成的 PNG 调 ``analyze_image``；
    - 其他（对话类）→ ``chat_for_run`` 发一句"只回复：ok"，返回回复前 200 字。
    ``embed_texts``/``analyze_image``/``chat_for_run`` 都带 ``usage_context={"source": "model_probe"}``，
    因此探测消耗也会记进用量账本（来源可区分），并受调用前预算闸门约束。

    **已知边界（安全）**：endpoint 完全由管理员配置，本函数（以及所有真实调用）不做 SSRF 校验 ——
    不拦私网/回环/云元数据地址、不做 DNS 重绑定防护，且会把该工作区的模型密钥发往目标地址；
    探测结果里的回复摘要（前 800/200 字）会回显给调用者，构成信息回传通道。

    返回：``{"status": "passed"|"failed", "details": {...}}``。异常被转成
    status=failed + details.error（有限中文分类，不含上游原文），因此探测接口恒为 200，
    调用方不能用状态码判断连通性；配置缺失（端点或密钥未配）也走失败分支。

    副作用：真实出网请求供应商；写 ``model_probe_results``（有 active profile 行时，按行 id 关联）
    或 ``environment_model_probes``（纯环境默认角色，按配置指纹关联）；两者都只 flush，由调用方 commit。
    """
    from ...providers.model import (
        _profile_config,
        analyze_image,
        chat_for_run,
        embed_texts,
    )

    # provider 另开会话解析配置；派发前必须按请求的 execute 范围核对真实生效行，
    # 不能把隐藏行当成环境默认，也不能用被授权的历史同名行替代最新配置。
    table = ModelProfile.__table__
    effective_id = db.scalar(select(table.c.id).where(
        table.c.workspace_id == workspace_id, table.c.profile_name == profile_name,
        table.c.status == "active",
    ).order_by(table.c.updated_at.desc()).limit(1))
    if effective_id is not None:
        _require_visible_probe_profile(db, workspace_id, effective_id)

    started = time.perf_counter()
    status = "passed"
    error_code = None
    details: dict
    cfg = {}
    try:
        cfg = _profile_config(profile_name, workspace_id=workspace_id)
        if cfg.get("profile_id"):
            _require_visible_probe_profile(db, workspace_id, cfg["profile_id"])
        capabilities = set((cfg.get("capabilities") or {}).get("capabilities") or [])
        if not cfg.get("base_url"):
            raise RuntimeError("模型端点未配置")
        if not cfg.get("api_key"):
            raise RuntimeError("模型密钥未配置")
        if profile_name == "embedding" or "embedding" in capabilities:
            # 唯一不需要模型"说话"的通道：只看返回维度，因此对稳定性要求最低。
            vectors = embed_texts(
                ["企业知识库模型连通测试"], workspace_id=workspace_id,
                profile_name=profile_name,
                usage_context={"source": "model_probe", "source_id": profile_name},
            )
            details = {
                "probe_type": "embedding",
                "dimensions": len(vectors[0]) if vectors else 0,
                "reply": "向量生成成功",
            }
        elif profile_name == "vision" or {"vision", "ocr"} & capabilities:
            image_bytes = _vision_probe_image()
            result = analyze_image(
                image_bytes,
                "image/png",
                context="模型配置页视觉连通测试",
                workspace_id=workspace_id,
                profile_name=profile_name,
                usage_context={"source": "model_probe", "source_id": profile_name},
            )
            details = {
                "probe_type": "vision",
                "reply": result["content"][:800],
            }
        else:
            response = chat_for_run(
                profile_name, [{"role": "user", "content": "只回复：ok"}],
                temperature=0.0, workspace_id=workspace_id,
                usage_context={"source": "model_probe", "source_id": profile_name},
            )
            details = {
                "probe_type": "chat",
                "reply": (response.get("content") or "")[:200],
            }
        details.update(
            {
                "model": cfg["model"],
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        )
    except (NotFoundError, PermissionErrorEx):
        # 授权拒绝仍是 HTTP 拒绝，不能伪装成一次已执行但失败的模型探测。
        raise
    except Exception as exc:
        # 上游异常可能回显请求头和凭据；只保存稳定分类，不记录或输出异常原文。
        status = "failed"
        error_code = "probe_error"
        details = {
            "probe_type": profile_name,
            "model": cfg.get("model"),
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "error": _safe_probe_error(exc),
        }

    row = db.execute(
        select(ModelProfile)
        .where(
            ModelProfile.workspace_id == workspace_id,
            ModelProfile.profile_name == profile_name,
            ModelProfile.status == "active",
        )
        .order_by(ModelProfile.updated_at.desc())
    ).scalars().first()
    if row:
        db.add(
            ModelProbeResult(
                id=new_id(),
                workspace_id=workspace_id,
                profile_id=row.id,
                status=status,
                details_json=details,
                error_code=error_code,
                created_at=datetime.utcnow(),
            )
        )
        db.flush()
    elif profile_name in _environment_defaults():
        # 只有内置角色才留环境探测记录：自定义角色没有数据库行时无法定位，探测结论不落库。
        db.add(EnvironmentModelProbe(
            workspace_id=workspace_id, profile_name=profile_name,
            config_fingerprint=_environment_fingerprint(profile_name),
            status=status, details_json=details, error_code=error_code,
        ))
        db.flush()
    return {"status": status, "details": details}


def _require_visible_probe_profile(db: Session, workspace_id: str, profile_id: str) -> None:
    """查询沿用请求会话的数据范围，越界统一返回不存在且不调用外部模型。"""
    visible = db.scalar(select(ModelProfile.id).where(
        ModelProfile.id == profile_id, ModelProfile.workspace_id == workspace_id,
        ModelProfile.status == "active",
    ))
    if visible is None:
        raise NotFoundError("模型配置不存在或不在当前执行范围内")


def _safe_probe_error(exc: Exception) -> str:
    """把探测失败压成有限中文类别，不信任上游消息、请求 URL 或附加字段。"""
    code = getattr(exc, "code", None)
    if code == "model_balance_exhausted":
        return _PROBE_ERRORS["balance"]
    if code == "model_rate_limited":
        return _PROBE_ERRORS["rate_limit"]
    if code == "model_response_incomplete":
        return _PROBE_ERRORS["incomplete"]
    status = getattr(exc, "status_code", None)
    if status in (401, 403):
        return _PROBE_ERRORS["authentication"]
    if status == 429:
        return _PROBE_ERRORS["rate_limit"]
    if isinstance(status, int) and 500 <= status <= 599:
        return _PROBE_ERRORS["unavailable"]
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return _PROBE_ERRORS["connection"]
    return _PROBE_ERRORS["generic"]


def _secret_is_ready(
    db: Session, workspace_id: str, secret_id: Optional[str]
) -> bool:
    """判断密钥引用当前是否可用（存在、属于本工作区、active、未软删除）。

    与 ``providers/model.py._resolve_key_for_profile`` 的判定条件保持字面一致：
    任何一条不满足，运行时都会因找不到密钥而失败，因此这里也必须报 false，
    否则页面会显示"已配置密钥"却发不出请求。
    """
    if not secret_id:
        return False
    row = db.get(Secret, secret_id)
    return bool(
        row
        and row.workspace_id == workspace_id
        and row.status == "active"
        and row.deleted_at is None
    )


def _probe_to_dict(row: Optional[ModelProbeResult]) -> Optional[dict]:
    """把探测结果行转成接口返回结构；无记录返回 None（前端据此显示"未测试"）。"""
    if not row:
        return None
    details = dict(row.details_json or {})
    if row.status == "failed" and details.get("error") not in _PROBE_ERRORS.values():
        # 历史失败记录可能已存入原始异常；只在展示时收敛，不擅自改写历史数据库。
        details["error"] = _PROBE_ERRORS["generic"]
    return {
        "status": row.status,
        "error_code": row.error_code,
        "details": details,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _vision_probe_image() -> bytes:
    """生成一张带文字和柱状图的探测用 PNG（内存中合成，不落盘、不发外网）。

    为什么要自己画图：视觉通道必须"图 + 文"一起验证才能说明 OCR/图表理解确实可用，
    固定文字（标题 + 两个数字）与三根柱子让探测结果可在页面上人工核对。
    字体在部分容器里可能没有 arial，因此回退到 PIL 内置位图字体（字形难看但能出图，
    保证探测不会因为缺字体而失败）。
    """
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (960, 540), "#f7f8fa")
    draw = ImageDraw.Draw(image)
    try:
        title_font = ImageFont.truetype("arial.ttf", 46)
        body_font = ImageFont.truetype("arial.ttf", 34)
    except OSError:
        title_font = ImageFont.load_default()
        body_font = ImageFont.load_default()
    draw.rounded_rectangle((45, 40, 915, 500), radius=28, fill="white", outline="#d7dce2", width=3)
    draw.text((85, 75), "VISION MODEL CHECK", fill="#182230", font=title_font)
    draw.text((85, 155), "Revenue 2026: 42.5M", fill="#182230", font=body_font)
    draw.text((85, 215), "Year-on-year growth: 18%", fill="#182230", font=body_font)
    draw.rectangle((90, 330, 310, 440), fill="#86a8e7")
    draw.rectangle((365, 280, 585, 440), fill="#4f7ad1")
    draw.rectangle((640, 205, 860, 440), fill="#234f9b")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def normalize_pricing(value: Optional[dict]) -> dict:
    """Validate an explicit per-million-token price; empty means unpriced.

    中文补充：把接口传入的计价字段规范成可入库的快照结构，只在"保存配置"和"记账"两处调用。
    - 空值/两档全空 → 返回 ``{}``，语义是**未计价**（不是 0 元、不是免费）：
      该角色的调用会记成 ``priced=False``，费用列为空，预算硬限制因此永不触发；
    - 币种只允许 ``ALLOWED_CURRENCIES``（大小写不敏感，统一存大写）；
    - 单价必须是非负数字且不超过 1,000,000（按"每百万 token"计），越界或非数字抛 ValidationError（422）。

    参数 ``value``：``{"input_price_per_million", "output_price_per_million", "currency"}``，缺键按未配置处理。
    返回：``{currency, input_price_per_million, output_price_per_million}``，或 ``{}``。
    """
    if not value:
        return {}
    currency = str(value.get("currency") or "CNY").strip().upper()
    if currency not in ALLOWED_CURRENCIES:
        raise ValidationError("计价币种仅支持 CNY、USD、EUR 或 JPY")
    result = {"currency": currency}
    for source, target in (("input_price_per_million", "input_price_per_million"),
                           ("output_price_per_million", "output_price_per_million")):
        raw = value.get(source)
        if raw is None or raw == "":
            # 显式置空也要保留键：None 表示"这一档没配价"，与 0 元是不同事实。
            result[target] = None
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValidationError("模型价格必须是非负数字") from exc
        if number < 0 or number > 1_000_000:
            raise ValidationError("模型价格必须介于 0 和 1,000,000 之间")
        result[target] = number
    if result.get("input_price_per_million") is None and result.get("output_price_per_million") is None:
        # 两档都没价 → 落库为 {}（等价于未计价），避免留下"有币种没价格"的半截结构。
        return {}
    return result


def _pricing_out(value: Optional[dict]) -> dict:
    """把库内计价快照转成接口返回结构，附带 ``configured`` 便于前端区分"未配置"与"0 元"。"""
    normalized = value or {}
    return {
        "input_price_per_million": normalized.get("input_price_per_million"),
        "output_price_per_million": normalized.get("output_price_per_million"),
        "currency": normalized.get("currency") or "CNY",
        "configured": normalized.get("input_price_per_million") is not None
        or normalized.get("output_price_per_million") is not None,
    }


def marketplace() -> list[dict]:
    """Return role-oriented provider templates; prices are never invented.

    中文补充：给管理页的"接入模板"：三条来自当前部署环境变量（reasoning/embedding/vision），
    外加一条内置的 rerank 占位（自定义兼容端点，模型名与端点留空等人填）。
    模板里的价格只回显环境里已配置的值，**绝不臆造价格或默认单价** —— 未配置就显示未计价，
    这样"是否计价"的决定权始终在管理员手里（也决定了预算硬限制能不能生效）。
    返回：只读静态列表，不查库、不出网。
    """
    defaults = _environment_defaults()
    items = []
    for profile_name, value in defaults.items():
        items.append({
            "id": f"environment-{profile_name}", "profile_name": profile_name,
            "provider": value.get("provider", "openai-compatible"),
            "model": value.get("model", ""), "endpoint": value.get("endpoint", ""),
            "capabilities": value.get("capabilities", []),
            "name": {"reasoning": "推理与工具调用", "embedding": "向量检索",
                     "vision": "视觉与文档识别"}.get(profile_name, profile_name),
            "description": "读取当前部署环境的建议值；保存前仍需管理员确认端点、密钥与价格。",
            "pricing": _pricing_out(value.get("pricing")), "source": "environment",
        })
    items.append({
        "id": "custom-rerank", "profile_name": "rerank", "provider": "openai-compatible",
        "model": "", "endpoint": "", "capabilities": ["rerank"], "name": "结果重排",
        "description": "用于知识检索和召回结果排序的自定义兼容端点。",
        "pricing": _pricing_out(None), "source": "builtin",
    })
    return items


def record_usage(*, workspace_id: Optional[str], profile_name: str, provider: str,
                 model: str, capability: str, usage: Optional[dict], pricing: Optional[dict],
                 source: str = "model_gateway", source_id: str | None = None,
                 status: str = "completed", latency_ms: int | None = None,
                 error_code: str | None = None, metadata: Optional[dict] = None) -> None:
    """Persist a model-call fact in an independent short transaction.

    中文补充：用量账本的唯一写入点，由 ``providers/model.py._persist_usage`` 在每次调用后调用
    （成功与失败都记），**开自己的短事务**而不是复用业务会话：模型回答可能已经返回给用户，
    账本不能因为业务事务回滚而丢失，也不会把业务事务拖长。

    计价语义（与 ``ModelUsageRecord`` 的列语义一一对应）：
    - ``pricing`` 传的是"调用当时的 profile 计价快照"，两档单价都空 → ``priced=False``、``cost=NULL``；
    - 只要有一档有价就 ``priced=True``，``cost = 输入token×输入价 + 输出token×输出价``（按每百万 token 折算，
      缺的那一档按 0 参与计算）—— 因此"只配了输入价"的调用也会产生费用；
    - 币种取快照的 currency（默认 CNY），与预算策略币种不一致时不会计入当月预算（见 ``enforce_budget``）。

    其他参数：``usage`` 为供应商返回的 token 统计（缺失按 0 处理，负数会被夹到 0，
    ``total_tokens`` 至少取 ``prompt+completion``）；``source``/``source_id`` 用于归因
    （如 ``model_probe``、run_id），其余上下文进 ``metadata_json``；``workspace_id`` 为空时**直接返回不记账**
    （启动期预热、无工作区上下文的调用属已知不记账路径）。

    副作用：向 ``model_usage_records`` 插入一行并提交；字段按列长度截断（profile/provider/model/capability/source）。
    异常：本函数会抛数据库异常，调用方 ``_persist_usage`` 以 try/except 包住并只打 warning ——
    即"记账失败不影响业务回答"，因此账本可能缺失行，用量页不能当作强一致的对账依据。
    """
    if not workspace_id:
        return
    from app.db import new_session
    payload = usage or {}
    # 供应商偶尔返回负数或缺失字段；账本只存非负整数，否则汇总曲线会出现负数。
    prompt = max(0, int(payload.get("prompt_tokens") or 0))
    completion = max(0, int(payload.get("completion_tokens") or 0))
    total = max(prompt + completion, int(payload.get("total_tokens") or 0))
    normalized = normalize_pricing(pricing)
    input_price = normalized.get("input_price_per_million")
    output_price = normalized.get("output_price_per_million")
    priced = input_price is not None or output_price is not None
    cost = None
    if priced:
        # 单价是"每百万 token"，因此除以 1_000_000；缺失档按 0.0 处理。
        cost = prompt * (input_price or 0.0) / 1_000_000 + completion * (output_price or 0.0) / 1_000_000
    with new_session() as db:
        db.add(ModelUsageRecord(
            id=new_id(), workspace_id=workspace_id, profile_name=(profile_name or "default")[:100],
            provider=(provider or "openai-compatible")[:64], model=(model or "unknown")[:200],
            capability=(capability or "chat")[:32], source=(source or "model_gateway")[:64],
            source_id=(source_id or None), status=status, prompt_tokens=prompt,
            completion_tokens=completion, total_tokens=total,
            input_price_per_million=input_price, output_price_per_million=output_price,
            cost=cost, currency=normalized.get("currency") or "CNY", priced=priced,
            latency_ms=latency_ms, metadata_json=metadata or {}, error_code=error_code,
            # 调用方只给"完成时间 + 耗时"，开始时间由完成时间回推，仅用于展示与排序。
            started_at=datetime.utcnow() - timedelta(milliseconds=max(0, latency_ms or 0)),
            completed_at=datetime.utcnow(),
        ))
        db.commit()


def _date_bounds(start: str | None, end: str | None) -> tuple[datetime, datetime, date, date]:
    """把 ``YYYY-MM-DD`` 查询参数解析成**左闭右开**的时间区间，供用量汇总与导出共用。

    缺省为"最近 30 天（今天往前 29 天）到今天"；``end`` 含当天，因此上界取次日零点。
    格式错误抛 ValidationError("日期必须使用 YYYY-MM-DD 格式")；``end < start`` 或跨度 > 366 天
    抛 ValidationError —— 上限存在的原因是汇总在 Python 侧全量扫描区间内的行，不设上限会拖垮请求。
    返回：``(下界, 上界, start_date, end_date)``，日期用 UTC（与 ``completed_at`` 的写入口径一致）。
    """
    today = datetime.utcnow().date()
    try:
        start_date = date.fromisoformat(start) if start else today - timedelta(days=29)
        end_date = date.fromisoformat(end) if end else today
    except ValueError as exc:
        raise ValidationError("日期必须使用 YYYY-MM-DD 格式") from exc
    if end_date < start_date or (end_date - start_date).days > 366:
        raise ValidationError("查询日期范围必须为 0–366 天")
    return (datetime.combine(start_date, datetime_time.min),
            datetime.combine(end_date + timedelta(days=1), datetime_time.min),
            start_date, end_date)


def usage_summary(db: Session, workspace_id: str, *, start: str | None = None,
                  end: str | None = None, profile_name: str = "") -> dict:
    """汇总区间内的模型用量与费用（实现 ``GET /api/models/usage``），纯读、无副作用。

    参数：``start``/``end`` 为 ``YYYY-MM-DD``（见 ``_date_bounds``，上限 366 天，非法抛 ValidationError）；
    ``profile_name`` 非空时只统计该角色。

    返回结构：``range``、``totals``（调用数/成功失败数/各类 token/计价与未计价调用数、
    分币种费用合计）、``daily``（区间内**逐日补齐**，缺失日期补 0，便于前端画连续曲线）、
    ``models``（按角色+供应商+模型+能力聚合，按 token 倒序）、``sources``（按调用来源聚合）、
    ``budget``（策略 + ``month_spend``/``usage_percent``/``warning``/``exceeded``）。

    口径说明（重要）：
    - 费用按行内快照币种分别累计，不做汇率换算；未计价行只累加 ``unpriced_*``，不按 0 元计入费用；
    - ``month_spend``/``exceeded`` 只取"计价行 + 币种等于策略币种 + 本月"的集合，
      因此与 ``totals.costs`` 中的其他币种金额可能不一致（切换策略币种会让历史花费看起来"消失"）；
    - 未设置 ``monthly_budget`` 时 ``usage_percent=None``，``warning``/``exceeded`` 恒为 false。
    """
    lower, upper, start_date, end_date = _date_bounds(start, end)
    statement = select(ModelUsageRecord).where(
        ModelUsageRecord.workspace_id == workspace_id,
        ModelUsageRecord.completed_at >= lower, ModelUsageRecord.completed_at < upper,
    )
    if profile_name:
        statement = statement.where(ModelUsageRecord.profile_name == profile_name)
    rows = db.scalars(statement.order_by(ModelUsageRecord.completed_at, ModelUsageRecord.id)).all()
    daily: dict[str, dict] = {}
    models: dict[tuple, dict] = {}
    sources: dict[str, dict] = {}
    currencies: dict[str, float] = {}
    totals = {"calls": 0, "successful_calls": 0, "failed_calls": 0,
              "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
              "priced_calls": 0, "unpriced_calls": 0, "unpriced_tokens": 0}
    for row in rows:
        day = row.completed_at.date().isoformat()
        day_item = daily.setdefault(day, {"date": day, "calls": 0, "total_tokens": 0,
                                          "failed_calls": 0, "costs": {}})
        model_key = (row.profile_name, row.provider, row.model, row.capability)
        model_item = models.setdefault(model_key, {
            "profile_name": row.profile_name, "provider": row.provider, "model": row.model,
            "capability": row.capability, "calls": 0, "total_tokens": 0,
            "failed_calls": 0, "priced_calls": 0, "unpriced_calls": 0, "costs": {},
        })
        source_item = sources.setdefault(row.source, {"source": row.source, "calls": 0,
                                                       "total_tokens": 0, "costs": {}})
        totals["calls"] += 1
        success = row.status == "completed"
        totals["successful_calls" if success else "failed_calls"] += 1
        for target in (totals,):
            target["prompt_tokens"] += row.prompt_tokens
            target["completion_tokens"] += row.completion_tokens
            target["total_tokens"] += row.total_tokens
        for item in (day_item, model_item, source_item):
            item["calls"] += 1
            item["total_tokens"] += row.total_tokens
        if not success:
            day_item["failed_calls"] += 1
            model_item["failed_calls"] += 1
        if row.priced and row.cost is not None:
            totals["priced_calls"] += 1
            model_item["priced_calls"] += 1
            # 同一页面可能出现多种币种，因此费用按币种分桶累计，不做汇率折算。
            for item in (day_item, model_item, source_item):
                item["costs"][row.currency] = item["costs"].get(row.currency, 0.0) + row.cost
            currencies[row.currency] = currencies.get(row.currency, 0.0) + row.cost
        else:
            # 未计价调用单列，避免被误读成 "0 元"（也就无法据此判断真实开销）。
            totals["unpriced_calls"] += 1
            totals["unpriced_tokens"] += row.total_tokens
            model_item["unpriced_calls"] += 1
    all_days = []
    cursor = start_date
    while cursor <= end_date:
        key = cursor.isoformat()
        all_days.append(daily.get(key, {"date": key, "calls": 0, "total_tokens": 0,
                                        "failed_calls": 0, "costs": {}}))
        cursor += timedelta(days=1)
    policy = get_cost_policy(db, workspace_id)
    month_prefix = datetime.utcnow().strftime("%Y-%m")
    # 当月已花费：只算"已计价 + 币种等于策略币种"的行，未计价与异币种都不进预算口径
    # （这也正是"未配置单价时预算永远是 0、硬限制不触发"的同一处口径）。
    month_spend = sum(
        row.cost or 0 for row in db.scalars(select(ModelUsageRecord).where(
            ModelUsageRecord.workspace_id == workspace_id,
            ModelUsageRecord.currency == policy["currency"], ModelUsageRecord.priced.is_(True),
            ModelUsageRecord.completed_at >= datetime.fromisoformat(month_prefix + "-01"),
        )).all()
    )
    budget = policy["monthly_budget"]
    return {
        "range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "totals": {**totals, "costs": {key: round(value, 6) for key, value in currencies.items()}},
        "daily": all_days,
        "models": sorted(models.values(), key=lambda item: (-item["total_tokens"], item["profile_name"], item["model"])),
        "sources": sorted(sources.values(), key=lambda item: (-item["total_tokens"], item["source"])),
        "budget": {**policy, "month_spend": round(month_spend, 6),
                   "usage_percent": round(month_spend / budget * 100, 2) if budget else None,
                   "warning": bool(budget and month_spend >= budget * policy["warn_percent"] / 100),
                   "exceeded": bool(budget and month_spend >= budget)},
    }


def get_cost_policy(db: Session, workspace_id: str) -> dict:
    """读取工作区的模型月度预算策略（存 ``SystemSetting``，key = ``models.cost_policy``）。

    复用系统设置表而不是自建表：预算属于工作区级配置，跟随 SystemSetting 的版本号做乐观锁。
    返回：``{monthly_budget, currency, warn_percent, hard_limit, version}``；
    从未保存过时给默认值（``monthly_budget=None`` 即只统计不设额度，``warn_percent=80``，
    ``hard_limit=False``，``version=0``）。``value`` 若不是 dict（历史数据/被别处写脏）按空配置处理。
    副作用：无（纯读）。被用量汇总与预算闸门共用，因此必须保持廉价、可重复调用。
    """
    from app.modules.system.models import SystemSetting
    row = db.scalar(select(SystemSetting).where(
        SystemSetting.workspace_id == workspace_id, SystemSetting.key == "models.cost_policy",
    ))
    value = row.value if row and isinstance(row.value, dict) else {}
    return {"monthly_budget": value.get("monthly_budget"),
            "currency": value.get("currency") or "CNY",
            "warn_percent": int(value.get("warn_percent") or 80),
            "hard_limit": bool(value.get("hard_limit", False)),
            "version": row.version if row else 0}


def update_cost_policy(db: Session, workspace_id: str, *, actor_user_id: str,
                       monthly_budget: float | None, currency: str,
                       warn_percent: int, hard_limit: bool, version: int) -> dict:
    """保存月度预算策略（实现 ``PUT /api/models/cost-policy``），带乐观锁与审计。

    参数：``version`` 必须是界面读到的当前版本；行已存在且版本不符时抛 ConflictError（409，
    携带 ``current_version``），避免两个管理员互相覆盖。首次创建允许 ``version`` 为 0 或 1。
    ``monthly_budget=None`` 表示只统计不设额度；``currency`` 必须在 ``ALLOWED_CURRENCIES`` 内；
    金额上限 10 亿、``warn_percent`` 在 1–100，越界抛 ValidationError（422）。

    返回值：保存后重新读取的策略（含新的 ``version``），保证界面拿到的是库内真实状态。
    副作用：写 ``SystemSetting`` 并写审计（``models.cost_policy_update``，记录币种、
    是否设置了额度、是否开启硬限制），两者在同一事务内提交。
    注意：``hard_limit=True`` 只对"已计价且币种一致"的调用生效，未配置单价时不会拦截任何调用
    （见 ``enforce_budget``）。
    """
    from app.common.audit import audit
    from app.modules.system.models import SystemSetting
    clean_currency = currency.strip().upper()
    if clean_currency not in ALLOWED_CURRENCIES:
        raise ValidationError("预算币种仅支持 CNY、USD、EUR 或 JPY")
    if monthly_budget is not None and (monthly_budget < 0 or monthly_budget > 1_000_000_000):
        raise ValidationError("月度预算必须介于 0 和 1,000,000,000 之间")
    if not 1 <= warn_percent <= 100:
        raise ValidationError("预算提醒比例必须介于 1% 和 100% 之间")
    row = db.scalar(select(SystemSetting).where(
        SystemSetting.workspace_id == workspace_id, SystemSetting.key == "models.cost_policy",
    ))
    if row is None:
        # 新建时兼容 version=0（后端返回的默认）与 1（旧前端首次保存会传 1），其他值视为过期请求。
        if version not in {0, 1}:
            raise ValidationError("预算策略版本无效，请刷新后重试")
        row = SystemSetting(id=new_id(), workspace_id=workspace_id, key="models.cost_policy",
                            value={}, description="模型月度预算与提醒策略")
        db.add(row)
    elif row.version != version:
        from app.errors import ConflictError
        raise ConflictError("预算策略已被修改，请刷新后重试", current_version=row.version)
    row.value = {"monthly_budget": monthly_budget, "currency": clean_currency,
                 "warn_percent": warn_percent, "hard_limit": hard_limit}
    audit(db, workspace_id=workspace_id, actor_user_id=actor_user_id,
          action="models.cost_policy_update", resource_type="model_cost_policy",
          resource_id=row.id, metadata={"currency": clean_currency,
                                       "budget_configured": monthly_budget is not None,
                                       "hard_limit": hard_limit})
    db.commit()
    return get_cost_policy(db, workspace_id)


def usage_csv(db: Session, workspace_id: str, *, start: str | None = None,
              end: str | None = None) -> bytes:
    """导出区间内逐次调用的用量明细 CSV（实现 ``GET /api/models/usage/export``），纯读。

    与 ``usage_summary`` 共用 ``_date_bounds``（同样受 366 天上限与格式校验约束，非法抛 ValidationError），
    但**不做聚合**：一次调用一行，便于线下对账与追责。
    返回 UTF-8 字节流并在开头加 BOM（U+FEFF），否则 Excel 打开中文表头会乱码；
    未计价的费用列输出空串而不是 0，避免被当成免费调用；耗时缺失同样留空。
    不返回文件名/响应头，响应封装在路由层完成。
    """
    import csv
    lower, upper, _, _ = _date_bounds(start, end)
    rows = db.scalars(select(ModelUsageRecord).where(
        ModelUsageRecord.workspace_id == workspace_id,
        ModelUsageRecord.completed_at >= lower, ModelUsageRecord.completed_at < upper,
    ).order_by(ModelUsageRecord.completed_at, ModelUsageRecord.id)).all()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["完成时间", "角色", "供应商", "模型", "能力", "来源", "状态",
                     "输入Token", "输出Token", "总Token", "是否计价", "费用", "币种", "耗时毫秒"])
    for row in rows:
        writer.writerow([row.completed_at.isoformat(), row.profile_name, row.provider, row.model,
                         row.capability, row.source, row.status, row.prompt_tokens,
                         row.completion_tokens, row.total_tokens, "是" if row.priced else "否",
                         "" if row.cost is None else f"{row.cost:.6f}", row.currency,
                         "" if row.latency_ms is None else row.latency_ms])
    return ("\ufeff" + output.getvalue()).encode("utf-8")


def enforce_budget(workspace_id: Optional[str]) -> None:
    """Reject a new call only when an explicit hard limit is already exhausted.

    中文补充：模型调用的**预算闸门**，由 ``providers/model.py._governed_call`` 在每次真实调用前
    调用（chat/embedding/vision 都走这条路径）。只读、无副作用、开独立会话，不写任何状态。

    触发条件（三个条件同时满足才可能拦截）：
    1. 策略里 ``hard_limit=True``；
    2. 设置了 ``monthly_budget``（未设置额度的策略永远不拦）；
    3. 本月"``priced=True`` 且币种等于策略币种"的费用合计 ``>= budget``。

    **已知约束（必须明确告知使用者）**：``priced`` 只有当模型角色配置了单价才为 true，
    而默认部署下模型价格是空的（``pricing={}``），此时费用恒为 0，条件 3 永不成立 ——
    也就是说**未配置价格时，界面上的"超额后阻止调用"不会触发**，平台会继续无限调用模型；
    同理，与策略币种不同的计价（如 USD）也不计入当月花费，切换币种会让历史花费不参与拦截。
    因此本函数只保证"已计价且币种一致"前提下的拦截，不是成本控制的完备手段。

    异常：达到硬限制时抛 ``ModelError``（携带 ``budget``/``currency``/``month_spend`` 供界面展示），
    由调用方转换成用户可见提示并阻止本次调用；``workspace_id`` 为空（无工作区上下文的调用）
    直接放行，不记账也不拦。
    """
    if not workspace_id:
        return
    from app.db import new_session
    with new_session() as db:
        policy = get_cost_policy(db, workspace_id)
        budget = policy.get("monthly_budget")
        if not policy.get("hard_limit") or budget is None:
            return
        month_start = datetime.combine(datetime.utcnow().date().replace(day=1), datetime_time.min)
        rows = db.scalars(select(ModelUsageRecord).where(
            ModelUsageRecord.workspace_id == workspace_id,
            ModelUsageRecord.currency == policy["currency"],
            ModelUsageRecord.priced.is_(True), ModelUsageRecord.completed_at >= month_start,
        )).all()
        spend = sum(row.cost or 0 for row in rows)
        if spend >= budget:
            raise ModelError(
                "本工作区模型月度预算已达到硬限制，请联系管理员调整预算或计价策略",
                budget=budget, currency=policy["currency"], month_spend=round(spend, 6),
            )
