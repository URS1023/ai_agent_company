"""跨轮业务任务、稳定草稿和真实交付核验；持久化复用现有运行快照。"""
from __future__ import annotations

import re
from collections import Counter
from datetime import datetime

from sqlalchemy import select

from app.ids import new_id
from app.models import Artifact, Message, Run
from app.common.ordered_rows import ordered_payload_rows
from .delivery_contract import blocks_implicit_task, is_continuation, task_intent_text, task_modification_text, update_contract


_DELIVERY_ACTION = (r"生成|制作|做|导出|交付|输出|整理成|写成|创建|保存为|给我|转成|转为|转换为|"
                    r"\b(?:make|create|generate|export|produce|build|deliver|convert|save)\b")
_SUBSTANTIVE_ACTION = _DELIVERY_ACTION.replace("|给我", "")
_DELIVERY_FORMAT = (r"pptx?|powerpoint|幻灯片|演示文稿|\bslides?\b|\bpresentations?\b|excel|xlsx|电子表格|"
                    r"\bspreadsheets?\b|word|docx|文档|\bpdf\b|\bcsv\b|大屏|看板|dashboard")


def _delivery_creation_clauses(content):
    """区分实际制作与流程咨询、拒绝，问号本身不否定明确的请求。"""
    clauses = re.split(r"[，,；;。\n]|(?:然后|另外|再)(?=做|制作|生成|给我|导出)", content)
    output = []
    active_creation = False
    for part in clauses:
        action = re.search(_SUBSTANTIVE_ACTION, part, re.I) or re.search(_DELIVERY_ACTION, part, re.I)
        if action is None:
            # 仅纯产物枚举沿用上一句动作；解释、拒绝或其他句子不会传递制作意图。
            remainder = re.sub(_DELIVERY_FORMAT, "", part, flags=re.I)
            remainder = re.sub(r"\b(?:and|a|an)\b", "", remainder, flags=re.I)
            if (active_creation and re.search(_DELIVERY_FORMAT, part, re.I)
                    and re.fullmatch(r"[\s、和及与并一二两三四五六七八九十\d份套个]*", remainder)):
                output.append("生成" + part)
            else:
                active_creation = False
            continue
        active_creation = False
        if re.search(r"(?:不要|别|不用|无需|暂不|先不|不必|不需要|不想)\s*(?:再|先|立即|马上)?\s*(?:" + _DELIVERY_ACTION + r")", part, re.I):
            continue
        if re.search(r"\b(?:do\s+not|don['’]?t|never|no\s+need\s+to|stop)\s+(?:(?:want\s+)?(?:you\s+)?to\s+)?(?:" + _DELIVERY_ACTION + r")", part, re.I):
            continue
        if re.search(r"(?:确认|批准|同意)后.{0,8}(?:生成|制作|导出)|\b(?:after\s+approval|once\s+approved)\b", part, re.I):
            continue
        before = part[:action.start()]
        if (re.search(r"先|只|仅", before)
                and re.search(r"(?:" + _DELIVERY_FORMAT + r")\s*(?:的)?(?:制作|生成)?(?:方案|计划|流程|步骤)", part, re.I)):
            continue
        if re.search(r"怎么|如何|怎样|\bhow\b", before, re.I):
            continue
        if (re.search(r"说明|解释|讲解|告诉我|教我|\b(?:explain|describe|teach)\b", before, re.I)
                and re.search(r"流程|步骤|方法|教程|原理|\b(?:process|steps|method|tutorial)\b", part, re.I)):
            continue
        if re.search(r"(?:流程|步骤|方法|原理).{0,6}(?:是什么|有哪些)", part):
            continue
        output.append(part)
        active_creation = True
    return output


def requirements(content, explicit=None):
    """只把明确制作请求转成交付约束，咨询类提问不强制生成附件。"""
    allowed = {"presentation", "document", "spreadsheet", "pdf", "image", "chart", "dashboard", "json", "csv"}
    if explicit is not None:
        from app.errors import ValidationError
        result = []
        for item in explicit:
            if not isinstance(item, dict) or item.get("type") not in allowed or type(item.get("count", 1)) is not int or not 1 <= item.get("count", 1) <= 20:
                raise ValidationError("交付清单类型或数量无效")
            result.append({"type": item["type"], "count": int(item.get("count", 1))})
        return result
    clauses = _delivery_creation_clauses(content)
    if not clauses:
        return []
    patterns = {"presentation": r"ppt|幻灯片|演示文稿|powerpoint|\bslides?\b|\bpresentations?\b", "spreadsheet": r"excel|xlsx|电子表格|\bspreadsheets?\b",
                "document": r"word|docx|文档", "pdf": r"\bpdf\b", "csv": r"\bcsv\b",
                "dashboard": r"大屏|看板|dashboard"}
    result = []
    for kind, pattern in patterns.items():
        if not any(re.search(pattern, part, re.I) for part in clauses):
            continue
        # 分句和明确另要的同类成果分别验收；单纯重复提及文件类型不增加份数。
        requested = [part for part in clauses if re.search(pattern, part, re.I)]
        counts = []
        for part in requested:
            for occurrence, mention in enumerate(re.finditer(pattern, part, re.I)):
                earlier = [match.end() for match in re.finditer("|".join(patterns.values()), part[:mention.start()], re.I)]
                # 数量只归属紧随其后的格式，防止“2份Excel和1份PPT”被一律算成两份。
                prefix = part[(earlier[-1] if earlier else 0):mention.start()]
                # “生成大屏放到 Word”中的 Word 是嵌入目标，不能与前面已要求的 Word 重复计数。
                if kind == "document" and re.search(r"(?:嵌入|放到|放入|放进|插入|放在).{0,6}$", prefix):
                    continue
                quantities = list(re.finditer(r"([一二两三四五六七八九十]|\d+)\s*(?:份|套|个)", prefix))
                action = _DELIVERY_ACTION
                if not quantities and not re.search(action, part[:mention.start()]):
                    continue
                if occurrence and not quantities and not re.search(action, prefix):
                    continue
                raw = quantities[-1].group(1) if quantities else "1"
                counts.append(int(raw) if raw.isdigit() else {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}[raw])
        result.append({"type": kind, "count": min(20, sum(counts) or 1)})
    return result


def clarification_choices(content):
    """只识别回答中真实列出的字母选项，避免把任意短消息当成续办授权。"""
    if not re.search(r"确认|选择|选项|请选择|哪种|哪一|指什么", content or ""):
        return set()
    return set(re.findall(r"(?m)^\s*(?:[-*]\s*)?(?:\*\*)?([A-H])(?:\*\*)?\s*(?=[（(:：、.．])", content or ""))


def choose_task(db, ctx, conversation_id, payload):
    """显式任务或延续修改沿用同资源任务，历史 PPT 草稿可迁移到稳定任务。"""
    from app.errors import NotFoundError
    previous = ordered_payload_rows(db, Run, (Run.workspace_id == ctx.workspace_id,
        Run.started_by_user_id == ctx.user_id, Run.conversation_id == conversation_id),
        (Run.created_at.desc(), Run.id.desc()))
    explicit = payload.task_id
    choice_reply = False
    if previous and re.fullmatch(r"(?:选|选择|我选)?\s*[A-Ha-h][。.!！\s]*", payload.content.strip()):
        snapshot = previous[0].context_snapshot or {}
        prior_message = db.get(Message, snapshot.get("assistant_message_id")) if snapshot.get("assistant_message_id") else None
        choice = re.search(r"[A-Ha-h]", payload.content).group().upper()
        choice_reply = (previous[0].status == "partial" and prior_message is not None
                        and choice in clarification_choices(prior_message.content))
    intent = task_intent_text(payload.content)
    continuing = is_continuation(intent)
    modifying = bool(re.search(r"(?:把|将|修改|调整|更改).{0,12}(?:第[一二三四五六七八九十\d]+页|这份|刚才|上面|上一份|这张|这个图)|(?:第[一二三四五六七八九十\d]+页|刚才的|上面的).{0,15}(?:改|调整|修改|补充)", task_modification_text(payload.content)))
    # 明确另开任务不能被后句修改词绕过；显式 task_id 和已提供的澄清选项仍沿用原合同。
    implicit = (continuing or modifying) and not blocks_implicit_task(intent)
    candidates = [run for run in previous if (run.context_snapshot or {}).get("task_id") == explicit] if explicit else previous[:1] if implicit or choice_reply else []
    if explicit and not candidates:
        raise NotFoundError("业务任务不存在")
    if candidates:
        snapshot = candidates[0].context_snapshot or {}
        saved = snapshot.get("business_task") or {}
        same_scope = all((getattr(payload, key) in (None, [], "") or getattr(payload, key) == snapshot.get(key))
                         for key in ("datasource_ids", "file_ids", "knowledge_base_ids", "template_id", "dashboard_template_id"))
        if saved and (explicit or same_scope) and (explicit or modifying or saved.get("status") != "completed"):
            revising = modifying or bool(explicit and re.search(r"修改|调整|更改|改成|替换|补充|重做|重新生成", payload.content))
            saved = ensure_contract(db, candidates[0], saved)
            merged = update_contract(saved, payload.content, payload.required_deliveries)
            if revising:
                from .delivery_evidence import prepare_revision
                merged = prepare_revision(db, candidates[0], merged, payload.content, actor=ctx)
            return {**merged, "status": "queued", "requires_new_delivery": revising or
                    bool(saved.get("requires_new_delivery") and saved.get("status") != "completed"),
                    "continuation_of_run_id": candidates[0].id, "missing_deliveries": [],
                    "updated_at": datetime.utcnow().isoformat()}
        if not saved and same_scope and (continuing or modifying):
            from app.modules.presentation.draft import draft_exists
            if draft_exists(ctx.workspace_id, candidates[0].id):
                identity = new_id()
                return update_contract({"id": identity, "task_id": identity, "draft_id": identity,
                        "title": (snapshot.get("user_request") or payload.content)[:120], "status": "queued",
                        "required_deliveries": [{"type": "presentation", "count": 1}], "missing_deliveries": [],
                        "resume_from_run_id": candidates[0].id, "requires_new_delivery": modifying,
                        "created_at": datetime.utcnow().isoformat(), "updated_at": datetime.utcnow().isoformat()},
                        snapshot.get("submitted_content") or snapshot.get("user_request") or payload.content)
    identity = new_id()
    return update_contract({"id": identity, "task_id": identity, "title": payload.content[:120], "status": "queued",
            "draft_id": identity, "required_deliveries": requirements(payload.content, payload.required_deliveries),
            "missing_deliveries": [], "created_at": datetime.utcnow().isoformat(), "updated_at": datetime.utcnow().isoformat()},
            payload.content, payload.required_deliveries)


def task_runs(db, run):
    identity = (run.context_snapshot or {}).get("task_id")
    candidates = ordered_payload_rows(db, Run, (Run.workspace_id == run.workspace_id,
        Run.started_by_user_id == run.started_by_user_id, Run.conversation_id == run.conversation_id),
        (Run.created_at, Run.id))
    source = ((run.context_snapshot or {}).get("business_task") or {}).get("resume_from_run_id")
    return [row for row in candidates if (row.context_snapshot or {}).get("task_id") == identity or row.id == source] if identity else [run]


def ensure_contract(db, run, task):
    """历史合同只从同任务保存的用户原话补证，不能从模型答复或文件名补造。"""
    if task.get("delivery_contract_version") == 1:
        return task
    result = dict(task)
    for source in task_runs(db, run):
        snapshot = source.context_snapshot or {}
        result = update_contract(result, snapshot.get("submitted_content") or snapshot.get("user_request") or "")
        for item in snapshot.get("steering_messages") or []:
            if item.get("accepted") is True:
                result = update_contract(result, item.get("content") or "")
    return result


def deliveries(db, run):
    """从真实工件表和存储核验，不能仅信模型返回的文件名称。"""
    from app.common.storage import get_storage
    from app.modules.artifact.service import artifact_reference
    source_runs = task_runs(db, run)
    rows = db.scalars(select(Artifact).where(Artifact.workspace_id == run.workspace_id,
        Artifact.owner_user_id == run.started_by_user_id, Artifact.run_id.in_([item.id for item in source_runs]))).all()
    output = []
    for row in rows:
        exists = bool(row.storage_key and get_storage().exists(row.storage_key))
        state = "ready" if row.status == "ready" and exists else "missing" if not exists else row.status
        output.append({**artifact_reference(row), "id": row.id, "artifact_id": row.id, "task_id": (run.context_snapshot or {}).get("task_id"),
                       "source_run_id": row.run_id, "status": state, "type": row.type,
                       "name": row.name, "size": row.size, "mime_type": row.mime_type})
    from .dashboard_deliveries import dashboard_deliveries
    return output + dashboard_deliveries(db, run, source_runs)


def verify(db, run, native_status):
    """原生轮次结束后验证业务成果，可返回 partial，保留已完成文件。"""
    task = ensure_contract(db, run, dict((run.context_snapshot or {}).get("business_task") or {}))
    produced = deliveries(db, run)
    from .delivery_evidence import verify_constraints, verify_revision_objects
    revision_failures, eligible_ids = [], None
    if "delivery_objects" in task:
        accepted, revision_failures = verify_revision_objects(db, run, task, produced, task_runs(db, run))
        task["revision_receipts"] = accepted
        eligible_ids = {item["id"] for item in accepted}
        counts = Counter(item["type"] for item in accepted)
    else:
        counts = Counter(item["type"] for item in produced if item["status"] == "ready"
                         and (not task.get("requires_new_delivery") or item["source_run_id"] == run.id))
    missing = [{**item, "missing": max(0, item["count"] - counts[item["type"]])}
               for item in task.get("required_deliveries", []) if counts[item["type"]] < item["count"]]
    failures, receipts = verify_constraints(db, run, task, produced, task_runs(db, run), eligible_ids=eligible_ids)
    failures = revision_failures + failures
    status = "partial" if native_status == "completed" and (missing or failures) else native_status
    task.update(status=status, native_status=native_status, missing_deliveries=missing,
                constraint_failures=failures, constraint_receipts=receipts,
                run_ids=[item.id for item in task_runs(db, run)], updated_at=datetime.utcnow().isoformat())
    run.context_snapshot = {**(run.context_snapshot or {}), "business_task": task, "deliveries": produced, "native_status": native_status}
    return status, task, produced
