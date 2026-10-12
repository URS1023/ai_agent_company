"""Rerank only authorized candidates; unavailable models preserve retrieval order."""
import math
import time
import httpx
from sqlalchemy import select
from app.errors import ValidationError
from app.models import ModelProfile
from app.modules.knowledge.models import KnowledgeProfile


def validate_profile(db, workspace_id, name):
    """校验某个模型配置可用于重排：必须在本工作区、已启用、状态 active 且声明 rerank 能力。

    由 governance.update_profile 在开启 rerank 时调用，把"配置错了"提前到保存设置这一步，
    而不是等到用户检索时才失败。
    异常：ValidationError —— 配置不存在/未启用/不支持 rerank（文案面向用户，不暴露内部原因）。
    """
    profile = db.scalar(select(ModelProfile).where(ModelProfile.workspace_id == workspace_id,
        ModelProfile.profile_name == name, ModelProfile.enabled.is_(True), ModelProfile.status == "active"))
    if not profile or "rerank" not in (profile.capabilities_json or {}).get("capabilities", []):
        raise ValidationError("请选择已启用且支持结果重排的模型配置")


def rank_candidates(workspace_id, profile, query, ranked):
    """调用外部 /rerank 接口对已授权的候选重排，返回带 rerank_score 的新列表。

    入参 ranked 是 [{src: {...}, score: float}, ...]；返回同结构但顺序按新分数排列，
    未被打分的候选追加在末尾并带 rerank_score=None（不能丢弃：融合阶段的分可能仍有效）。
    参数：
        profile：模型配置名（数据库来源）；非数据库来源或未声明 rerank 能力时直接报错。
        query：原始检索串；文档截断到 6000 字符以控制请求体积与计费。
    异常：ValidationError —— 模型不具备 rerank 能力；其余（HTTP 错误、响应结构非法、
        分数非有限值、索引越界或重复）会向上抛出，由 rerank() 统一降级。
    副作用：发起外部 HTTP 调用（超时 10s、不跟随重定向）、占用治理配额（_governed_call），
        并在 finally 中写入用量记录（无论成功失败都记，保证配额与账单口径一致）。
    """
    from app.providers.model import _profile_config, _governed_call, _persist_usage
    cfg = _profile_config(profile, workspace_id=workspace_id)
    if cfg.get("source") != "database" or "rerank" not in (cfg.get("capabilities") or {}).get("capabilities", []):
        raise ValidationError("所选模型未提供结果重排能力")
    _governed_call(workspace_id)
    started = time.perf_counter()
    status, usage = "failed", {}
    try:
        with httpx.Client(timeout=10, follow_redirects=False) as client:
            response = client.post(cfg["base_url"].rstrip("/") + "/rerank",
                headers={"Authorization": "Bearer " + cfg["api_key"]}, json={"model": cfg["model"],
                    "query": query, "documents": [r["src"]["content"][:6000] for r in ranked],
                    "top_n": len(ranked), "return_documents": False})
            response.raise_for_status()
            body = response.json()
        scores = {}
        for row in body.get("results", []):
            index, score = row.get("index"), row.get("relevance_score")
            if type(index) is not int or not 0 <= index < len(ranked) or index in scores or isinstance(score, bool):
                raise ValueError("Invalid rerank identity")
            score = float(score)
            if not math.isfinite(score):
                raise ValueError("Invalid rerank score")
            scores[index] = score
        if not scores:
            raise ValueError("Empty rerank response")
        usage, status = body.get("usage") or {}, "completed"
        order = sorted(scores, key=lambda i: (-scores[i], i))
        order.extend(i for i in range(len(ranked)) if i not in scores)
        return [{**ranked[i], "rerank_score": scores.get(i)} for i in order]
    finally:
        _persist_usage(workspace_id=workspace_id, profile_name=profile, cfg=cfg, capability="rerank",
            usage=usage, started=started, status=status, usage_context={"source": "knowledge"})


def rerank(db, workspace_id, query, ranked):
    """检索结果的统一重排入口，返回 (结果列表, 状态)。

    状态语义（会被下发到检索响应的 rerank_status 字段，前端据此解释"为什么没重排"）：
        - "disabled"：命中的知识库都没开 rerank，或都没配模型名；原样返回，不调用外部服务。
        - "mixed_profiles"：命中的库之间 rerank 模型不一致（或有的库没有 profile 行），
          此时不对结果做任何重排——用一个模型给多库混排会让排序口径变得不可解释。
        - "applied"：重排成功，结果带 rerank_score。
        - "fallback"：重排过程抛出任何异常（超时、5xx、响应非法等），静默回退到原顺序，
          检索主流程不受影响（重排是增强而非必需）。
    注意：只把前 60 条送去重排（ranked[:60]），是成本与延迟的折中。
    """
    ids = {r["src"].get("knowledge_base_id") for r in ranked}
    profiles = db.scalars(select(KnowledgeProfile).where(KnowledgeProfile.workspace_id == workspace_id,
        KnowledgeProfile.knowledge_base_id.in_(ids), KnowledgeProfile.rerank_enabled.is_(True))).all() if ids else []
    names = {p.rerank_model for p in profiles if p.rerank_model}
    if not names:
        return ranked, "disabled"
    if len(names) != 1 or len(profiles) != len(ids):
        return ranked, "mixed_profiles"
    try:
        return rank_candidates(workspace_id, next(iter(names)), query, ranked[:60]), "applied"
    except Exception:
        return ranked, "fallback"
