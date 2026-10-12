"""保存分阶段演示草稿，支持原生任务跨轮续写与同任务多稿独立编辑。"""
from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
import re

from ...common.storage import get_storage
from ...errors import ToolExecutionError
from .spec_normalizer import normalize_presentation_spec, normalize_slide


def _task_identity(workspace_id: str, run_id: str) -> str | None:
    """仅信执行器绑定的当前任务，不从模型参数接收任务归属。"""
    from app.modules.runtime.task_control import current
    control = current.get()
    if (control and getattr(control, "business_run_id", None) == run_id
            and getattr(control, "business_workspace_id", None) == workspace_id
            and getattr(control, "business_task_id", None)):
        return control.business_task_id
    return None


def _key(workspace_id: str, run_id: str) -> str:
    """DSH 按工作区和稳定任务存当前草稿；历史或独立调用仍按运行 ID 存取。"""
    task_id = _task_identity(workspace_id, run_id)
    if task_id:
        return f"presentation_tasks/{workspace_id}/{task_id}.json"
    return f"presentation_drafts/{workspace_id}/{run_id}.json"


def _named_key(workspace_id: str, run_id: str, draft_id: str) -> str:
    """草稿标识只在当前任务目录内解析，不能指向其它任务或服务器路径。"""
    if not isinstance(draft_id, str) or not re.fullmatch(r"deck-[0-9a-f]{20}", draft_id):
        raise ToolExecutionError("draft_id 无效，请使用本任务大纲回执中的草稿标识")
    return _key(workspace_id, run_id).removesuffix(".json") + "/" + draft_id + ".json"


def _title_identity(title: str) -> str:
    """标题只决定任务内的幂等键；不同任务仍拥有独立目录。"""
    return "deck-" + sha256(title.strip().encode("utf-8")).hexdigest()[:20]


def draft_exists(workspace_id: str, run_id: str) -> bool:
    """Check a staged draft without converting its absence into an execution error."""
    return get_storage().exists(_key(workspace_id, run_id))


def delete_draft(workspace_id: str, run_id: str) -> None:
    """删除当前稿及其命名副本；同任务其它稿保留并切换到最近一份。"""
    storage = get_storage()
    key = _key(workspace_id, run_id)
    if storage.exists(key):
        active = json.loads(storage.read_text(key))
        identity = active.get("draft_id")
        catalog = [item for item in active.get("available_drafts") or [] if item["draft_id"] != identity]
        if identity:
            named = _named_key(workspace_id, run_id, identity)
            if storage.exists(named):
                storage.delete(named)
        for item in reversed(catalog):
            named = _named_key(workspace_id, run_id, item["draft_id"])
            if storage.exists(named):
                replacement = json.loads(storage.read_text(named))
                replacement["available_drafts"] = catalog
                storage.put_bytes(key, json.dumps(replacement, ensure_ascii=False, indent=2).encode("utf-8"))
                return
        storage.delete(key)


def _save(workspace_id: str, run_id: str, draft: dict) -> None:
    """整份覆盖写入草稿（对象存储，非数据库）；调用方需自备并发保护。"""
    storage = get_storage()
    key = _key(workspace_id, run_id)
    if _task_identity(workspace_id, run_id):
        previous = json.loads(storage.read_text(key)) if storage.exists(key) else None
        catalog = list((previous or {}).get("available_drafts") or [])
        for saved in [previous, draft]:
            if not saved:
                continue
            saved["draft_id"] = saved.get("draft_id") or _title_identity(saved["title"])
            if not any(item["draft_id"] == saved["draft_id"] for item in catalog):
                catalog.append({"draft_id": saved["draft_id"], "title": saved["title"]})
            # 切换当前稿前也归档旧版单文件草稿，兼容已有任务继续编辑第一份 PPT。
            storage.put_bytes(_named_key(workspace_id, run_id, saved["draft_id"]),
                json.dumps(saved, ensure_ascii=False, indent=2).encode("utf-8"))
        draft["available_drafts"] = catalog
    storage.put_bytes(key, json.dumps(draft, ensure_ascii=False, indent=2).encode("utf-8"))


def load_draft(workspace_id: str, run_id: str, *, draft_id: str | None = None) -> dict:
    """读取草稿；不存在时抛 ``ToolExecutionError`` 引导先建大纲。

    这里的报错属于工具调用契约错误（模型应当先调 create_presentation_plan），
    因此用 ToolExecutionError 而不是 NotFoundError，让调用方看到可修正的提示。
    """

    storage = get_storage()
    key = _key(workspace_id, run_id)
    if draft_id and draft_id not in {run_id, _task_identity(workspace_id, run_id)}:
        key = _named_key(workspace_id, run_id, draft_id)
    if not storage.exists(key):
        raise ToolExecutionError("当前任务没有该 PPT 大纲，请先调用 create_presentation_plan 并使用返回的 draft_id")
    draft = json.loads(storage.read_text(key))
    active_key = _key(workspace_id, run_id)
    if key != active_key and storage.exists(active_key):
        draft["available_drafts"] = json.loads(storage.read_text(active_key)).get("available_drafts") or []
    return draft


def resume_draft(workspace_id: str, source_run_id: str, target_run_id: str, *, template_id=None) -> dict:
    """Called only after the chat service verifies both runs' owner and conversation.

    把源运行的草稿续写到目标运行（新建会话续做 PPT）：目标已存在草稿时不覆盖，直接返回
    其状态（幂等）。换过模板则拒绝续写——大纲里的页面角色是按模板能力排的，套到另一套
    模板上会得到错误构图，必须重新出大纲。返回 ``draft_status`` 结果。
    """
    if draft_exists(workspace_id, target_run_id):
        return draft_status(load_draft(workspace_id, target_run_id), target_run_id)
    draft = deepcopy(load_draft(workspace_id, source_run_id))
    if template_id and template_id != draft.get("template_id"):
        raise ToolExecutionError("已更换 PPT 模板，需要按新模板创建大纲")
    draft["resumed_from_run_id"] = source_run_id
    _save(workspace_id, target_run_id, draft)
    return draft_status(draft, target_run_id)


def create_draft(
    workspace_id: str,
    run_id: str,
    args: dict,
    *,
    template_id: str,
    aspect_ratio: str,
    selected_page_count=None,
    user_request: str = "",
) -> dict:
    """创建/复用大纲并返回进度；DSH 同任务同标题幂等，不同标题独立存稿。

    显式 ``draft_id`` 只选择已存在稿；历史独立调用保持按 ``run_id`` 幂等。

    页数语义：封面、结尾默认启用，可分别用显式布尔值关闭。
    - ``selected_page_count``/``page_count`` 为空或 ``"auto"``：保留大纲页数，
      预计总页数为正文页加上启用的封面和结尾；当前用户原话有明确绑定到本稿的
      总页数时先应用该约束，范围页数尽量保留现有内容并限制在范围内；
    - 显式给出数字：夹到 3–50，扣除启用的封面和结尾后对齐正文页数。**页数不足时会自动补
      「补充分析 N」的 content 占位页凑够数量**——这是已知行为：它保证页数达标，但这些
      占位页没有真实论据，必须由后续 write_draft_slides 用真实内容重写，不能当作成品页。
    副作用：写草稿文件。抛 ``ToolExecutionError`` 表示大纲为空或结构不可用。
    """
    storage = get_storage()
    title = str(args.get("title") or args.get("name") or "演示文稿").strip()
    named_id = _title_identity(title) if _task_identity(workspace_id, run_id) else None
    previous = None
    if args.get("draft_id"):
        previous = load_draft(workspace_id, run_id, draft_id=args["draft_id"])
    elif named_id and storage.exists(_named_key(workspace_id, run_id, named_id)):
        previous = load_draft(workspace_id, run_id, draft_id=named_id)
    elif storage.exists(_key(workspace_id, run_id)):
        active = load_draft(workspace_id, run_id)
        if not named_id or active.get("title") == title:
            previous = active
    if previous is not None:
        _save(workspace_id, run_id, previous)
        status = draft_status(previous, run_id)
        status["already_created"] = True
        return status
    include_cover = args.get("include_cover", True)
    include_closing = args.get("include_closing", True)
    if type(include_cover) is not bool or type(include_closing) is not bool:
        raise ToolExecutionError("include_cover 和 include_closing 必须是布尔值")
    raw_outline = args.get("slides") or args.get("outline") or []
    if not isinstance(raw_outline, list) or not raw_outline:
        raise ToolExecutionError("PPT 大纲必须包含正文页面列表")
    outlines = []
    statement_count = 0
    for index, raw in enumerate(raw_outline, start=1):
        slide = normalize_slide(raw, index)
        if slide and slide.get("layout") != "closing":
            requested_layout = (
                str(raw.get("layout") or raw.get("page_role") or "").strip().lower()
                if isinstance(raw, dict) else ""
            )
            if requested_layout in {
                "statement", "agenda", "section", "content", "two_column",
                "cards", "metrics", "comparison", "timeline", "table", "chart",
                "quote", "image", "matrix",
            }:
                slide["layout"] = requested_layout
            # 结论页最多一页：statement 版式在引擎里是大字号单论点页，
            # 连续出现会被渲染器降级，这里提前把第 2 页起换成内容版式。
            if slide.get("layout") == "statement":
                statement_count += 1
                if statement_count > 1:
                    slide["layout"] = "cards" if index % 2 else "content"
            outlines.append({
                "title": slide["title"],
                "layout": slide.get("layout") or "content",
                "section": slide.get("section") or "",
                "key_message": slide.get("key_message") or "",
                "written": False,
            })
    raw_count = selected_page_count
    if raw_count in (None, "", "auto"):
        raw_count = args.get("page_count")
    page_constraint = None
    if raw_count in (None, "", "auto") and user_request:
        from .request_constraints import infer_page_constraint
        page_constraint = infer_page_constraint(user_request, title,
            natural_page_count=len(outlines) + int(include_cover) + int(include_closing))
        if page_constraint:
            raw_count = page_constraint["selected"]
    exact_count = None
    if raw_count not in (None, "", "auto"):
        exact_count = max(3, min(50, int(raw_count)))
        desired = exact_count - int(include_cover) - int(include_closing)
        if len(outlines) > desired:
            outlines = outlines[:desired]
        while len(outlines) < desired:
            # 已知边界：补页只为满足用户指定的总页数，内容是占位文案，
            # 必须靠后续正文写入替换；不得把它当作"已完成的正文页"。
            outlines.append({
                "title": f"补充分析 {len(outlines) + 1}",
                "layout": "content",
                "section": "补充",
                "key_message": "补足故事线所需的证据或行动",
                "written": False,
            })
    draft = {
        "schema": "presentation_draft.v1",
        "title": title,
        "subtitle": str(args.get("subtitle") or ""),
        "author": str(args.get("author") or "智慧云脑 Cloud Brain"),
        "footer": str(args.get("footer") or title),
        "template_id": template_id,
        "aspect_ratio": aspect_ratio,
        "page_count": exact_count or "auto",
        "page_count_constraint": page_constraint,
        "include_cover": include_cover,
        "include_closing": include_closing,
        "closing_title": str(args.get("closing_title") or "总结与下一步"),
        "closing_subtitle": str(args.get("closing_subtitle") or "将共识转化为可执行行动"),
        "slides": outlines,
    }
    if named_id:
        draft["draft_id"] = named_id
    _save(workspace_id, run_id, draft)
    return draft_status(draft, run_id)


def _slide_number(raw: dict, draft: dict) -> int | None:
    """解析正文索引或显式物理页码；保留历史 page_number 的正文索引语义。"""
    supplied = next((raw.get(key) for key in ("slide_number", "page_number", "index")
                     if raw.get(key) not in (None, "")), None)

    def integer(value, name):
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ToolExecutionError(f"{name} 必须是从 1 开始的整数") from None
        if isinstance(value, bool) or isinstance(value, float) and value != number:
            raise ToolExecutionError(f"{name} 必须是从 1 开始的整数")
        return number

    number = integer(supplied, "slide_number") if supplied is not None else None
    physical = raw.get("physical_page_number")
    if physical not in (None, ""):
        mapped = integer(physical, "physical_page_number") - int(draft.get("include_cover", True))
        if not 1 <= mapped <= len(draft.get("slides") or []):
            raise ToolExecutionError("physical_page_number 对应封面、结尾或超出总稿范围；请按 pages 映射选择正文页")
        if number is not None and number != mapped:
            raise ToolExecutionError("slide_number 与 physical_page_number 指向不同页面，请按 outline 页码映射修正")
        number = mapped
    return number


def write_draft_slides(
    workspace_id: str,
    run_id: str,
    batch: list,
    *, draft_id: str | None = None,
) -> dict:
    """把一批正文页写入草稿并标记 ``written=True``，返回含 ``written_now`` 的状态。

    契约：每批 ≤ 4 页，且必须给出 ``slide_number``（从 1 开始，落在大纲范围内），否则抛
    ``ToolExecutionError``；合并时以本次传入内容覆盖大纲字段，但标题为空时沿用大纲标题。
    写明为封面的页会被拒绝（正文批次不能覆盖封面/结尾页，它们由渲染阶段固定生成）。
    副作用：整份草稿重新落盘。
    """
    if not isinstance(batch, list) or not batch:
        raise ToolExecutionError("本批次没有页面内容")
    if len(batch) > 4:
        raise ToolExecutionError("每批最多写 4 页，请拆成多个批次")
    draft = load_draft(workspace_id, run_id, draft_id=draft_id)
    written_now = []
    for raw in batch:
        if not isinstance(raw, dict):
            continue
        number = _slide_number(raw, draft)
        if number is None:
            raise ToolExecutionError("每页必须提供 slide_number（从 1 开始）")
        if not 1 <= number <= len(draft["slides"]):
            raise ToolExecutionError(f"slide_number={number} 超出正文大纲范围 1–{len(draft['slides'])}")
        outline = draft["slides"][number - 1]
        merged = {**outline, **raw}
        merged["title"] = str(raw.get("title") or outline["title"])
        merged["layout"] = raw.get("layout") or outline.get("layout") or "content"
        merged.pop("written", None)
        normalized = normalize_slide(merged, number)
        if not normalized:
            raise ToolExecutionError(f"第 {number} 页被识别为封面；正文批次不能写封面")
        normalized["written"] = True
        draft["slides"][number - 1] = normalized
        written_now.append(number)
    _save(workspace_id, run_id, draft)
    status = draft_status(draft, run_id)
    status["written_now"] = written_now
    return status


def write_draft_slide_batches(
    workspace_id: str,
    run_id: str,
    batch: list,
    *, draft_id: str | None = None,
) -> dict:
    """接收模型正文批次，保留有效页，并逐项反馈可纠正的页码与内容错误。"""
    if not isinstance(batch, list) or not batch:
        raise ToolExecutionError("本批次没有页面内容")
    if len(batch) > 50:
        raise ToolExecutionError("单次最多接收 50 页正文内容")

    draft = load_draft(workspace_id, run_id, draft_id=draft_id)
    outlines = draft.get("slides") or []
    missing_numbers = [
        index for index, slide in enumerate(outlines, start=1)
        if not slide.get("written")
    ]
    prepared: list[dict] = []
    rejected: list[dict] = []
    assigned: set[int] = set()
    reserved: set[int] = set()
    for raw in batch:
        if not isinstance(raw, dict):
            continue
        try:
            number = _slide_number(raw, draft)
        except ToolExecutionError:
            continue
        if number is not None and 1 <= number <= len(outlines):
            reserved.add(number)

    for position, original in enumerate(batch, start=1):
        if not isinstance(original, dict):
            rejected.append({"batch_position": position, "reason": "每页必须是页面对象"})
            continue
        raw = deepcopy(original)
        number = None
        try:
            number = _slide_number(raw, draft)
            if number is not None and not 1 <= number <= len(outlines):
                raise ToolExecutionError(f"slide_number={number} 超出正文大纲范围 1–{len(outlines)}；请按 outline 页码映射提交")
            if number is None:
                title = str(raw.get("title") or "").strip().casefold()
                matches = [
                    index for index, outline in enumerate(outlines, start=1)
                    if index not in assigned and index not in reserved and title
                    and str(outline.get("title") or "").strip().casefold() == title
                ]
                available = (
                    [value for value in matches if value in missing_numbers]
                    or matches
                    or [value for value in missing_numbers if value not in assigned and value not in reserved]
                )
                if not available:
                    raise ToolExecutionError("缺少 slide_number，且没有可明确匹配的待写正文页")
                number = available[0]
            if number in assigned:
                raise ToolExecutionError(f"本批次重复提交正文第 {number} 页，请每页只提交一次")
            raw["slide_number"] = number
            outline = outlines[number - 1]
            merged = {**outline, **raw, "title": str(raw.get("title") or outline["title"])}
            merged.pop("written", None)
            normalized = normalize_slide(merged, number)
            if not normalized or normalized.get("layout") == "closing":
                raise ToolExecutionError("正文批次不能写封面或结尾页；这些页面由系统自动生成")
            # 正文缺失通常来自模型参数结构错误，必须就地反馈，不能等最后才让全部页面重写。
            layout = str(raw.get("layout") or outline.get("layout") or "content")
            has_body = any(normalized.get(key) for key in ("items", "left", "right"))
            if not has_body and layout not in {"statement", "section", "quote"}:
                raise ToolExecutionError(
                    f"第 {number} 页缺少可渲染正文；{layout} 页不能只传 key_message、notes 或空 series。"
                    "请在 items 中提供带 title/description 的正文；图表传 chart.categories 和 chart.series，"
                    "表格传 table.headers/rows，对比页传 left/right"
                )
            prepared.append(raw)
            assigned.add(number)
        except (ToolExecutionError, TypeError, ValueError) as exc:
            rejected.append({
                "batch_position": position,
                "slide_number": number,
                "reason": getattr(exc, "message", "slide_number 必须是从 1 开始的整数"),
            })

    written_now: list[int] = []
    status = draft_status(draft, run_id)
    for offset in range(0, len(prepared), 4):
        status = write_draft_slides(workspace_id, run_id, prepared[offset:offset + 4], draft_id=draft_id)
        written_now.extend(status.get("written_now") or [])
    status["written_now"] = written_now
    status["internal_batch_count"] = (len(prepared) + 3) // 4
    status["inferred_slide_numbers"] = [int(raw["slide_number"]) for raw in prepared]
    status["rejected_slides"] = rejected
    status["batch_status"] = "partial" if rejected else "complete"
    if rejected:
        status["instruction"] += (
            " 本批有效页面已保存；rejected_slides 列出未接受的输入。"
            "仅修正缺失正文页，越界的封面/结尾页无需补写。"
        )
    if not prepared:
        raise ToolExecutionError(
            "本批次没有可写入的正文：" + "；".join(item["reason"] for item in rejected[:4]),
            presentation_progress=status,
        )
    return status


def draft_status(draft: dict, run_id: str) -> dict:
    """汇总草稿进度，并给出下一步动作与提示语；不写库、无副作用。

    ``total_page_count`` 按正文与启用的封面、结尾计算，物理页码与正文索引分别返回；
    ``next_action``/``instruction`` 是给模型的显式下一步（继续写页或收尾），
    避免模型重复创建大纲。
    """

    missing = [
        index for index, slide in enumerate(draft.get("slides") or [], start=1)
        if not slide.get("written")
    ]
    from app.modules.runtime.task_control import current
    control = current.get()
    draft_id = (getattr(control, "business_task_id", None)
                if control and getattr(control, "business_run_id", None) == run_id else None) or run_id
    include_cover = bool(draft.get("include_cover", True))
    include_closing = bool(draft.get("include_closing", True))
    outline = [
        {"slide_number": index, "page_number": index + int(include_cover),
         "title": slide.get("title"), "layout": slide.get("layout")}
        for index, slide in enumerate(draft.get("slides") or [], start=1)
    ]
    total = len(outline) + int(include_cover) + int(include_closing)
    pages = ([{"page_number": 1, "slide_number": None, "role": "cover", "title": draft.get("title")}]
             if include_cover else [])
    pages += [{**item, "role": "body"} for item in outline]
    if include_closing:
        pages.append({"page_number": total, "slide_number": None, "role": "closing", "title": draft.get("closing_title")})
    status = {
        "draft_id": draft.get("draft_id") or draft_id,
        "available_drafts": draft.get("available_drafts") or [],
        "source_run_id": run_id,
        "title": draft.get("title"),
        "body_page_count": len(draft.get("slides") or []),
        "total_page_count": total,
        "page_count_constraint": draft.get("page_count_constraint"),
        "include_cover": include_cover,
        "include_closing": include_closing,
        "written_count": len(draft.get("slides") or []) - len(missing),
        "missing_slide_numbers": missing,
        "missing_page_numbers": [number + int(include_cover) for number in missing],
        "page_numbering": "slide_number 是从1开始的正文索引；page_number 是总稿物理页码。修改用户指定第N页时传 physical_page_number=N，或按 outline 映射为 slide_number；自动封面和结尾不能作为正文改写。",
        "outline": outline,
        "pages": pages,
    }
    status["next_action"] = (
        "write_presentation_slides"
        if missing else "finalize_presentation"
    )
    status["instruction"] = (
        "请按 missing_slide_numbers 分批写入，每批最多 4 页；不要再次创建大纲。"
        if missing else "全部正文页已经写完；请立即调用 finalize_presentation，不要再次创建大纲。"
    )
    return status


def _density_issues(slides: list[dict]) -> list[tuple[int, str]]:
    """按页判断"内容过薄"，返回 ``[(页码, 说明)]`` 供重写提示使用。

    ``section``/``quote``/``closing`` 天然短，不参与判定；``statement`` 页单独按"论点 + 解释"
    长度阈值（80 字）判断，其余版式按结构化载荷 JSON 长度（120 字）判断。这是启发式阈值，
    只用来触发重写，不代表内容正确的证明。
    """

    issues: list[tuple[int, str]] = []
    exempt = {"section", "quote", "closing"}
    for index, slide in enumerate(slides, start=1):
        if slide.get("layout") in exempt:
            continue
        if slide.get("layout") == "statement":
            thesis = (
                str(slide.get("statement") or "").strip()
                + str(slide.get("key_message") or "").strip()
                + json.dumps(slide.get("items") or [], ensure_ascii=False)
            )
            if len(thesis) < 80:
                issues.append((index, f"第 {index} 页《{slide.get('title')}》核心判断缺少解释"))
            continue
        payload = json.dumps(
            {
                "key_message": slide.get("key_message"),
                "statement": slide.get("statement"),
                "items": slide.get("items"),
                "left": slide.get("left"),
                "right": slide.get("right"),
            },
            ensure_ascii=False,
        )
        if len(payload) < 120:
            issues.append((index, f"第 {index} 页《{slide.get('title')}》解释和证据不足"))
    return issues


def final_spec(workspace_id: str, run_id: str, *, draft_id: str | None = None) -> dict:
    """把已写满的草稿归一成最终 ``PresentationSpec``（渲染前的最后一道门）。

    前置条件：所有正文页都已 ``written``，否则抛 ``ToolExecutionError`` 并列出缺页。
    质量门在归一之后执行 ``_density_issues`` 与 ``_presentation_quality_issues``；不通过时
    **把相关页重新标记为未写并落盘**，再抛错要求重写指定页——即失败会退回到"待重写"状态，
    不会渲染出半成品。返回的 spec 会带页数、模板与比例信息供渲染器使用。
    """

    draft = load_draft(workspace_id, run_id, draft_id=draft_id)
    missing = [
        index for index, slide in enumerate(draft["slides"], start=1)
        if not slide.get("written")
    ]
    if missing:
        raise ToolExecutionError(
            "以下正文页尚未写入：" + ", ".join(str(value) for value in missing),
            presentation_progress=draft_status(draft, run_id),
        )
    raw = deepcopy(draft)
    for slide in raw["slides"]:
        slide.pop("written", None)
    spec = normalize_presentation_spec(
        raw,
        title_fallback=draft["title"],
        template_id=draft["template_id"],
        aspect_ratio=draft["aspect_ratio"],
        page_count=draft.get("page_count"),
    )
    density = _density_issues(spec["slides"])
    from ...providers.tool.builtin_provider import _presentation_quality_issues

    quality = _presentation_quality_issues(spec)
    rewrite_indices = {index for index, _message in density}
    for message in quality:
        matched = False
        for index, slide in enumerate(spec["slides"], start=1):
            title = str(slide.get("title") or "")[:28]
            if title and f"《{title}》" in message:
                rewrite_indices.add(index)
                matched = True
        if not matched and message.startswith("statement 结论页过多"):
            statement_indices = [
                index for index, slide in enumerate(spec["slides"], start=1)
                if slide.get("layout") == "statement"
            ]
            rewrite_indices.update(statement_indices[1:])
    if density or quality:
        if not rewrite_indices:
            rewrite_indices.update(range(1, len(draft["slides"]) + 1))
        for index in rewrite_indices:
            draft["slides"][index - 1]["written"] = False
        _save(workspace_id, run_id, draft)
        messages = [message for _index, message in density] + quality
        raise ToolExecutionError(
            "PPT 页面内容仍不够丰富，请用 write_presentation_slides 重写第 "
            + "、".join(str(index) for index in sorted(rewrite_indices))
            + " 页："
            + "；".join(messages[:6]),
            presentation_progress={**draft_status(draft, run_id), "quality_issues": messages},
        )
    return spec
