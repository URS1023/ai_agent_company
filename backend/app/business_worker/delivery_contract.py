"""从用户原话编译可验证的交付义务；模型参数不能创建或删掉这些义务。"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from datetime import datetime

from app.modules.presentation.request_constraints import _PAGES, _number, infer_page_constraint


_DECK = r"pptx?|powerpoint|幻灯片|演示文稿"
_WORD = r"word|docx|文档"
_OTHER = rf"{_WORD}|excel|xlsx|pdf|大屏|看板"
_QUOTED = re.compile(r"《[^》]*》|“[^”]*”|‘[^’]*’|\"[^\"]*\"|'[^']*'|`[^`]*`")
_MODIFICATION_OBJECT = re.compile(rf"(?P<action>(?:把|将|修改|调整|更改)\s*)?(?P<object>{_QUOTED.pattern})")
_COMMAND_PREFIX = r"(?:(?:请帮我|帮我|请|现在|然后|这次|本次)\s*)*"


def task_intent_text(content: str) -> str:
    """只在任务意图判定中忽略标题和引语；用户原文与交付合同仍使用完整正文。"""
    return _QUOTED.sub(" ", content).strip()


def task_modification_text(content: str) -> str:
    """外部修改动作紧邻的引号内容是操作对象；单独引用的整条指令仍不参与判断。"""
    return _MODIFICATION_OBJECT.sub(lambda match: match["action"] + match["object"][1:-1]
        if match["action"] else " ", content).strip()


def blocks_implicit_task(content: str) -> bool:
    """明确另开或放弃原任务的命令同时约束续办和修改，不扫描全文孤立关键词。"""
    clauses = [part.strip() for part in re.split(r"[，,；;。！!？?\n]", task_intent_text(content)) if part.strip()]
    if not clauses:
        return True
    leading = re.sub(r"^" + _COMMAND_PREFIX, "", clauses[0])
    if re.match(r"(?:不要|不用|无需|别|不必|暂不|先不)\s*(?:再|先)?\s*(?:把|将|修改|调整|更改|继续|接着|重试)", leading):
        return True
    # 纯继续后的咨询仍不是执行要求；实际续办后附带的查询限制不参与此判断。
    if re.fullmatch(r"(?:继续|接着|重试)(?:一下|吧)?", leading) and len(clauses) > 1:
        leading = re.sub(r"^" + _COMMAND_PREFIX, "", clauses[1])
    if re.match(r"(?:如何|怎么|怎样|是否|能否|问|(?:说明|解释|告诉我)(?:如何|怎么|怎样))", leading):
        return True
    if re.match(r"(?:继续|接着)\s*(?:解释|说明|讲解|告诉我)", leading) and re.search(r"如何|怎么|怎样|是否|能否", leading):
        return True
    for clause in clauses:
        command = re.sub(r"^" + _COMMAND_PREFIX, "", clause)
        if re.match(r"(?:另外|另行)?(?:新建|创建|开启|开始|另开|另起|另做|另建)\s*(?:一[个项])?\s*(?:全新|新的?|独立的?)?任务", command):
            return True
        if re.match(r"(?:是|作为)?\s*(?:一[个项])?\s*(?:新的?|独立的?)任务", command):
            return True
        if re.match(r"(?:不要|不用|无需|不再|别|停止|放弃|取消)\s*(?:继续|沿用|接着|执行)?\s*(?:原来的|原|之前的?|上次的?|刚才的?|本|该|当前)?任务", command):
            return True
        if re.fullmatch(r"(?:不要|不用|无需|不再|别)\s*(?:继续|接着|重试)(?:了|吧)?|重新开始", command):
            return True
    return False


def is_continuation(content: str) -> bool:
    """只接受动作开头且明确回指的长句，咨询、否定和另开任务不参与继承。"""
    text = task_intent_text(content)
    if blocks_implicit_task(text):
        return False
    if re.fullmatch(r"(?:请|帮我|再)?\s*(?:继续|继续生成|继续完成|重试|重新生成|接着做)(?:一下|吧|一次)?[。！!\s]*", text):
        return True
    reference = re.search(r"上一条|上一个|上次|刚才|原任务|本任务|该任务|未完成|尚未|剩余|剩下的|剩下部分|原来的", text)
    saved_result = re.search(r"(?:使用|沿用|复用|基于|用)\s*(?:已有|已保存|现有|之前)(?:的)?\s*(?:查询)?结果", text)
    return bool(re.match(r"(?:请帮我|帮我|请)?\s*(?:继续|接着|重试)", text) and (reference or saved_result))


def _identity(kind: str, target: str, ordinal: int = 0) -> str:
    return hashlib.sha256(f"{kind}:{target.casefold()}:{ordinal}".encode()).hexdigest()[:20]


def normalize_target(value: str) -> str:
    """只移除称谓与标点，业务名本身仍用于唯一匹配。"""
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE).casefold()


def _deck_target(clause: str, mention) -> str:
    from .tasks import _DELIVERY_ACTION
    preceding = list(re.finditer(rf"{_DECK}|{_OTHER}", clause[:mention.start()], re.I))
    prefix = _PAGES.sub("", clause[(preceding[-1].end() if preceding else 0):mention.start()])
    actions = list(re.finditer(_DELIVERY_ACTION, prefix, re.I))
    if actions:
        prefix = prefix[actions[-1].end():]
    prefix = re.sub(r"^(?:请|帮我|再|另外|并|然后|一份|一套|的|和|及|与|把|将|\s)*(?:生成|制作|做|导出|创建|给我)?", "", prefix)
    prefix = re.sub(r"[一二两三四五六七八九十\d]+\s*(?:份|套|个)|(?:共|总共|总页数|控制在|的)$", "", prefix)
    return "" if re.fullmatch(r"这份|该份|原来的|上一份", prefix) else normalize_target(prefix)


def compile_constraints(content: str, required: list[dict]) -> list[dict]:
    """只编译明确总页数与嵌图关系；正文页码、模糊页数不冒充总页数。"""
    from .tasks import _delivery_creation_clauses, _DELIVERY_ACTION, requirements

    output = []
    kinds = {row["type"] for row in required}
    if "presentation" in kinds:
        # 沿用实际制作意图过滤；修改页数由调用者保留原合同，另行明确修改。
        clauses = _delivery_creation_clauses(content)
        # “生成 ERP PPT，总共 15 页”只回接紧邻的单一对象；跨格式或正文要求不猜测。
        for sentence in re.split(r"[。；;\n]", content):
            segments = re.split(r"[，,]", sentence)
            for prior, following in zip(segments, segments[1:]):
                if prior not in clauses or len(list(re.finditer(_DECK, prior, re.I))) != 1 or re.search(_OTHER, prior, re.I):
                    continue
                bare = re.sub(r"^\s*(?:要求|总共|一共|共|控制在|总页数为|总页数|页数为|页数|为)?\s*", "", following)
                if _PAGES.fullmatch(bare.strip()):
                    clauses.append(prior + " " + bare.strip())
        clauses += [part for part in re.split(r"[，,；;。\n]", content)
                    if re.match(r"(?:请)?(?:把|将|修改|调整|更改)", part.strip())]
        for clause in clauses:
            mentions = list(re.finditer(_DECK, clause, re.I))
            formats = list(re.finditer(rf"{_DECK}|{_OTHER}", clause, re.I))
            for match in _PAGES.finditer(clause):
                if (re.search(r"正文|至少|至多|最多|最少|不超过|超过|大约|约|第|前|后", clause[max(0, match.start()-8):match.start()])
                        or re.match(r"\s*(?:以上|以下|左右)", clause[match.end():])):
                    continue
                if not mentions:
                    continue
                # 页数归属相邻格式，不能因同句还要 Word 就丢掉 PPT 页数，也不能反过来套用 Word 页数。
                nearest = min(formats, key=lambda item: min(abs(item.end()-match.start()), abs(item.start()-match.end())))
                if not re.fullmatch(_DECK, nearest.group(), re.I):
                    continue
                lower, upper = _number(match.group("lower")), _number(match.group("upper") or match.group("lower"))
                if lower is None or upper is None or not 1 <= lower <= upper <= 99:
                    continue
                target = _deck_target(clause, nearest)
                output.append({"requirement_id": _identity("page_count", target), "kind": "page_count",
                    "type": "presentation", "minimum": lower, "maximum": upper, "target": target,
                    "count": 1 if target else next((row["count"] for row in requirements(clause) if row["type"] == "presentation"), 1),
                    "source_text": clause, "includes_cover_and_closing": True})
        # 复用既有具名 PPT 合同：后文“ERP 介绍为通用介绍，六到八页”仍属于前面明确创建的该份。
        named = {_deck_target(clause, mention) for clause in clauses for mention in re.finditer(_DECK, clause, re.I)} - {""}
        known = {row["requirement_id"] for row in output}
        for target in sorted(named):
            bound = infer_page_constraint(content, target, natural_page_count=6)
            identity = _identity("page_count", target)
            if bound and identity not in known:
                output.append({"requirement_id": identity, "kind": "page_count", "type": "presentation",
                    "minimum": bound["minimum"], "maximum": bound["maximum"], "target": target, "count": 1,
                    "source_text": bound["scope"], "includes_cover_and_closing": True})
    if "document" in kinds and re.search(r"大屏|看板|dashboard", content, re.I):
        for clause in re.split(r"[，,；;。\n]", content):
            if re.search(r"不要|不用|无需|怎么|如何|是否", clause):
                continue
            relation = re.search(rf"(?:把|将)?(?P<target>[^，。;；]*?(?:大屏|看板|dashboard)).*?(?:嵌入|放到|放入|放进|插入|放在|放).*?(?:{_WORD})", clause, re.I)
            reverse = re.search(rf"(?:{_WORD}).*?(?:嵌入|放入|插入|包含)(?P<target>.*?(?:大屏|看板|dashboard))", clause, re.I)
            relation = relation or reverse
            if relation:
                target = relation.group("target")
                actions = list(re.finditer(_DELIVERY_ACTION, target, re.I))
                if actions:
                    target = target[actions[-1].end():]
                target = re.sub(r"^(?:然后|再|并|把|将|生成|制作|做|创建|这张|这个|该|指定的|刚才的|数字|一份|一个)+", "", target)
                target = re.sub(r"(?:数字)?(?:大屏|看板|dashboard)$", "", target, flags=re.I)
                target = normalize_target(target)
                each = bool(re.search(rf"(?:每(?:一)?份|各份)\s*(?:{_WORD})|(?:{_WORD}).{{0,3}}(?:均|都)", clause, re.I))
                row = {"requirement_id": _identity("embedded_dashboard", target), "kind": "embedded_dashboard",
                    "type": "document", "target": target, "source_text": clause, "each_document": each,
                    "count": next(item["count"] for item in required if item["type"] == "document") if each else 1}
                # 仅识别紧跟大屏对象的明确版本，不将报告标题或正文中的 v1 当成版本要求。
                suffix = clause[relation.end("target"):]
                version = re.match(r"\s*(?:的)?\s*(?:版本\s*(\d+)|[vV](\d+)(?![\d.])|第([一二三四五六七八九十\d]+)版)", suffix)
                if version and re.match(r"\s*(?:版)?\s*(?:嵌入|放|插入|包含|的(?:图片|快照)|[，,、])", suffix[version.end():]):
                    row["requested_report_version"] = _number(next(value for value in version.groups() if value))
                output.append(row)
    return list({row["requirement_id"]: row for row in output}.values())


def update_contract(task: dict, content: str, explicit=None, *, replace: bool = False) -> dict:
    """续办/补充只合并义务，队列中新任务的编辑才可重编；显式清单保留既有 API 替换语义。"""
    from .tasks import requirements

    requested = requirements(content, explicit)
    old = [] if replace or explicit is not None else task.get("required_deliveries", [])
    merged = {row["type"]: dict(row) for row in old}
    for row in requested:
        merged[row["type"]] = {**row, "count": max(row["count"], merged.get(row["type"], {}).get("count", 0))}
    constraints = {} if replace else {row["requirement_id"]: dict(row) for row in task.get("delivery_constraints", [])}
    if explicit is not None:
        # 显式清单继续保留原 API 的整体替换语义；已移除格式不得遗留隐藏义务。
        constraints = {key: row for key, row in constraints.items() if row["type"] in merged}
        for row in constraints.values():
            if row["kind"] == "page_count" and not row.get("target"):
                row["count"] = merged["presentation"]["count"]
            elif row["kind"] == "embedded_dashboard" and row.get("each_document"):
                row["count"] = merged["document"]["count"]
    for row in compile_constraints(content, list(merged.values())):
        pages = [item for item in constraints.values() if item["kind"] == "page_count"]
        if row["kind"] == "page_count" and not row.get("target") and len(pages) == 1 and re.search(r"改成|改为|调整|修改|更改", content):
            row.update(requirement_id=pages[0]["requirement_id"], target=pages[0].get("target", ""))
        prior = constraints.get(row["requirement_id"], {})
        if row["kind"] == "page_count" and not row.get("target") and explicit is None:
            row["count"] = max(row["count"], prior.get("count", 1))
        if row["kind"] == "embedded_dashboard" and prior.get("each_document"):
            row.update(each_document=True, count=merged["document"]["count"])
        constraints[row["requirement_id"]] = {**prior, **row}
    previous_pages = [row for row in constraints.values() if row["kind"] == "page_count"]
    changed_pages = re.fullmatch(r"(?:请)?(?:把总页数)?(?:改成|改为|调整为|总页数改为)\s*(.+?)[。.!！\s]*", content.strip())
    if changed_pages and len(previous_pages) == 1 and _PAGES.fullmatch(changed_pages.group(1)):
        match = _PAGES.fullmatch(changed_pages.group(1))
        lower, upper = _number(match.group("lower")), _number(match.group("upper") or match.group("lower"))
        if lower is not None and upper is not None and 1 <= lower <= upper <= 99:
            previous_pages[0].update(minimum=lower, maximum=upper, source_text=content)
    result = {**task, "required_deliveries": list(merged.values()), "delivery_contract_version": 1,
              "delivery_constraints": list(constraints.values()), "constraint_failures": [], "constraint_receipts": []}
    if "delivery_objects" in result:
        result = revise_objects(result, content)
    return result


def revision_request(content: str, objects: list[dict]) -> dict | None:
    """只解析有明确动作的交付修改；不能识别的具体修改保留为待核验要求。"""
    clauses = [part.strip() for part in re.split(r"[，,；;。\n]", content) if part.strip()]
    active = [part for part in clauses if re.search(r"修改|调整|更改|改成|改为|替换|补充|重做|重新生成", part)
              and not re.match(r"(?:不要|不用|无需|别|不必|暂不|先不)", part)
              and not re.match(r"(?:同时|并且|并)?保留(?:上次|原有|已有)", part)]
    if not active:
        return None
    formats = {"presentation": _DECK, "document": _WORD, "spreadsheet": r"Excel|xlsx|电子表格",
               "dashboard": r"大屏|看板|dashboard"}
    mentioned = [(kind, part, re.search(pattern, part, re.I)) for part in active
                 for kind, pattern in formats.items() if re.search(pattern, part, re.I)]
    kinds = {kind for kind, _, _ in mentioned}
    if not kinds:
        kinds = {row["type"] for row in objects}
    if len(kinds) != 1:
        return {"error": "修改对象无法唯一确定，请明确要修改哪份成果", "source_text": content}
    kind = next(iter(kinds))
    prefix = next((part[:match.start()] for item, part, match in mentioned if item == kind), "")
    selector = re.sub(r"^(?:(?:请|只|仅|把|将|修改|调整|更改|补充|重做|重新生成|刚才的?|上面的?|上一份|这份|这个|这张|的)\s*)+", "", prefix)
    all_objects = bool(re.search(r"全部|所有|每份|两份|2份", selector))
    selector = normalize_target(re.sub(r"(?:全部|所有|每份|两份|2份|一份|该份|原来的)", "", selector))
    title_pattern = r"第([一二三四五六七八九十\d]+)页(?:的)?标题\s*(?:(?:修改|调整|更改)(?:成|为)|改成|改为|为|替换为)\s*(.+)$"
    checks = []
    for part in active:
        title = re.search(title_pattern, part) if kind == "presentation" else None
        pages = _PAGES.search(part) if kind == "presentation" else None
        if title:
            page, value = _number(title.group(1)), title.group(2).strip().strip('《》“”\"\'')
            checks.append({"kind": "slide_title", "page": page, "value": value} if page and value
                          else {"kind": "unsupported", "source_text": part})
            remainder = part[:title.start()]
        elif pages and re.search(r"改成|改为|调整为|总页数", part[:pages.start()]) and not re.search(r"第\s*$", part[:pages.start()]):
            lower, upper = _number(pages.group("lower")), _number(pages.group("upper") or pages.group("lower"))
            checks.append({"kind": "page_count", "minimum": lower, "maximum": upper} if lower and upper
                          else {"kind": "unsupported", "source_text": part})
            remainder = part[pages.end():]
        else:
            remainder = part
        if re.search(r"标题|正文|段落|数字|金额|单元格|排序|颜色|字体|列|行|页|改成|改为|替换为", remainder):
            # 每条具体修改都保留；识别了前项也不能静默丢弃后项未知要求。
            checks.append({"kind": "unsupported", "source_text": part})
    if not checks:
        checks = [{"kind": "content_changed"}]
    return {"type": kind, "selector": selector, "all": all_objects, "checks": checks, "source_text": content}


def revision_checks(change: dict) -> list[dict]:
    """兼容已有单项对象；新修订使用列表保存同句及跨轮未完成的全部要求。"""
    checks = change.get("checks", [])
    return checks if isinstance(checks, list) else [checks]


def revise_objects(task: dict, content: str) -> dict:
    """每份成果独立修订；继续不升号，队列重复保存相同修改也不重复升号。"""
    result = deepcopy(task)
    required = {row["type"]: row["count"] for row in result.get("required_deliveries", [])}
    objects = [row for row in result.get("delivery_objects", []) if row["type"] in required
               and row["ordinal"] <= required[row["type"]]]
    from app.ids import new_id
    for kind, count in required.items():
        for ordinal in range(1, count + 1):
            if not any(row["type"] == kind and row["ordinal"] == ordinal for row in objects):
                objects.append({"object_id": new_id(), "type": kind, "ordinal": ordinal, "revision": 1,
                                "label": "", "fulfillment": None, "constraint_ids": []})
    result["delivery_objects"] = objects
    request = revision_request(content, objects)
    if request is None:
        return result
    if request.get("error"):
        result["revision_unresolved"] = request
        return result
    targets = [row for row in objects if row["type"] == request["type"]]
    if request["selector"]:
        targets = [row for row in targets if request["selector"] in normalize_target(row.get("label", ""))]
    if not targets or any(row.get("baseline_ambiguous") for row in targets) or (len(targets) != 1 and not request["all"]):
        result["revision_unresolved"] = {"source_text": content, "error": "修改对象无法唯一确定，请明确原成果名称"}
        return result
    result.pop("revision_unresolved", None)
    for row in targets:
        previous = row.get("change")
        if previous and previous.get("source_text") == request["source_text"] and not row.get("fulfillment"):
            continue
        change = deepcopy(request)
        if previous and not row.get("fulfillment"):
            # 只让同一字段的新明确值替换旧值；另一页或未知项不会被后续修改覆盖。
            merged = {}
            for check in revision_checks(previous) + revision_checks(change):
                key = (check["kind"], check.get("page"), check.get("source_text") if check["kind"] == "unsupported" else None)
                merged[key] = check
            change["checks"] = list(merged.values())
        row.update(revision=row["revision"] + 1, change=change,
                   base=row.get("fulfillment") or row.get("base"), fulfillment=None,
                   requested_at=datetime.utcnow().isoformat())
    return result
