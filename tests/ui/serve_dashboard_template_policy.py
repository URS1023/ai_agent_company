"""Both template policies, real local SQLite, deterministic model, isolated app.

中文说明（大屏模板策略的浏览器夹具服务）：
用途：在 tests/ui/serve_dashboard_generation.py 的基础上再包一层断言，覆盖「手动固定模板」与
「自动参考模板」两种策略，供 tests/ui/dashboard_template_policy.js 在 9147 端口验收。
运行方式：需先有 serve_dashboard_generation 的临时环境，直接 python tests/ui/serve_dashboard_template_policy.py。
关键约束：手动模式下必须保持模板视觉指纹一致（visual_fingerprint 相等、画布尺寸不变），
自动模式下必须保留 templateReference；落库前用 service.save_report 的包装做断言，违反即失败。
影响范围：把断言结果写入临时目录的 template-policy.json，只影响隔离实例。
"""
import json
from copy import deepcopy

import serve_dashboard_generation as fixture
from app.modules.reports import service, templates
from app.modules.reports.schemas import TemplateInput
from app.modules.reports.template_lock import visual_fingerprint

with fixture.new_session() as db:
    workspace = db.scalar(fixture.select(fixture.Workspace))
    ctx = fixture.AuthContext(db, db.get(fixture.User, fixture.identity["admin_user_id"]), workspace, "admin", "fixture")
    template = templates.save_template(ctx, TemplateInput(name="财务设备固定模板验证", category="验证", content={
        "canvas": {"width": 1280, "height": 720, "backgroundColor": "#081a30"},
        "widgets": [
            {"id": "heading", "component": "JText", "componentName": "固定标题", "x": 60, "y": 20, "w": 1160, "h": 70,
             "config": {"option": {"body": {"text": "财务与设备固定布局", "fontSize": 30, "color": "#ffffff", "textAlign": "center"}}}},
            {"id": "finance", "component": "JStatsSummary", "componentName": "应收金额", "unit": "元",
             "x": 60, "y": 180, "w": 520, "h": 300, "config": {"option": {"title": {"text": "应收金额"}}}},
            {"id": "equipment", "component": "JStatsSummary", "componentName": "设备总数", "unit": "台",
             "x": 700, "y": 180, "w": 520, "h": 300, "config": {"option": {"title": {"text": "设备总数"}}}},
        ],
    }))

policy = {"templateId": template["id"], "designs": 0, "reviews": 0, "saved": []}
state_file = fixture.HERE / "template-policy.json"


def write_state():
    state_file.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")


def reply(profile, messages, **kwargs):
    context = json.loads(messages[1]["content"])
    if "candidates" in context:
        return {"content": json.dumps({"templateId": template["id"]})}
    if context.get("task") == "review_report_semantics":
        policy["reviews"] += 1
        return {"content": json.dumps({"aligned": True, "reason": "本机验证数据", "widgets": [
            {"widgetId": w["widgetId"], "aligned": True, "reason": "对应应收金额和设备数量"} for w in context["widgets"]]})}
    policy["designs"] += 1
    queries = {"finance": "SELECT SUM(amount) AS total FROM receivables", "equipment": "SELECT COUNT(*) AS total FROM devices"}
    if context.get("templateSlots"):
        assert context["templateId"] == template["id"]
        candidate = {"title": "不应出现的模型标题", "canvas": {"width": 1920, "height": 1080, "backgroundColor": "red"},
                     "bindings": [{"widgetId": key, "datasourceId": fixture.source_id, "sql": sql} for key, sql in queries.items()]}
    else:
        assert context["templateReference"]["id"] == template["id"]
        candidate = {"title": "财务与设备自动参考", "widgets": [
            {**deepcopy(w), "query": {"datasourceId": fixture.source_id, "sql": queries[w["id"]]}}
            for w in template["content"]["widgets"] if w["id"] in queries]}
    write_state()
    return {"content": json.dumps(candidate, ensure_ascii=False)}


original_save = service.save_report


def save_report(ctx, payload, *args, **kwargs):
    result = original_save(ctx, payload, *args, **kwargs)
    content = json.loads(result.content) if isinstance(result.content, str) else result.content
    fixed = payload.template_mode == "manual"
    if fixed:
        assert visual_fingerprint(content) == visual_fingerprint(template["content"])
        assert (result.width, result.height) == (1280, 720)
    else:
        assert content["canvas"]["templateReference"]["id"] == template["id"]
    policy["saved"].append({"id": result.id, "manual": fixed, "visualPreserved": fixed,
                           "width": result.width, "height": result.height})
    write_state()
    return result


fixture.model.chat_for_run = reply
fixture.generation.list_templates = lambda ctx: [deepcopy(template)]
service.save_report = save_report
write_state()
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(fixture.app, host="127.0.0.1", port=9147, log_level="warning")
