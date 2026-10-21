"""给锁定 Open WebUI 添加平台签名预算；只准备源码适配，不改原循环或全局上限。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from tempfile import NamedTemporaryFile

PATCH_ID = "openwebui-0.12.3-platform-turn-budget-v1"
CONFIG_FILE = "openwebui-chat-budget.json"
CONFIG_KIND = "ai-agent-company-owui-chat-budget-v1"
SECRET_KEY = "openwebui_turn_budget_secret"
HELPER_SOURCE = Path(__file__).with_name("owui_chat_budget_runtime.py")
HELPER_TARGET = "utils/aic_chat_budget.py"
HELPER_SHA256 = "fb5ebe8d6d558e3e8ad42c24dbd19fa4c00579ed21facc9c276d923ce3734fc3"

ORIGINAL_SHA256 = {
    "main.py": "1c304a6f0922e044d71b14640e08249dbdd53f16fcb2d15c69c572700aa62340",
    "utils/chat.py": "189fd407bc1197bc789fad37daee7972d81e0ce6cb850daf0616bf8c82092206",
    "utils/middleware.py": "f241322f2a463a8ad49085ad8727665f94f1e37c017736041f47b99e991dcbb8",
    "routers/openai.py": "af02463b799a817b307da5d03af25874ca14f477b883e913051b3b797d365955",
}
PATCHED_SHA256 = {
    "main.py": "fc73b3d085c7f6ced24db04d58d857da263b196853cf4e5c7dec58d064d97cc1",
    "utils/chat.py": "697ed1a94fa536324da7824d2989cb021022ff68de174422f71f4c7553606fe9",
    "utils/middleware.py": "c48ade453f5abf6f11f1ea6edd96ab66604fd4510ee3caffde2d290dac59ab33",
    "routers/openai.py": "bfad69b79a59497156cac75141df76a96134d0cce22ffd9c640a31b574fc7843",
}

CAPABILITY = '''@app.get('/api/aic-native/budget')
async def aic_native_budget_capability(user=Depends(get_verified_user)):
    # 只返回已就绪版本和固定pin，不暴露密钥或账本路径。
    from open_webui.utils.aic_chat_budget import capability
    return capability()


'''
ENTRY = '''    from open_webui.utils.aic_chat_budget import enter_completion
    await enter_completion(request, form_data, user)
'''
RESOLUTION = '''    from open_webui.utils.aic_chat_budget import enter_resolution
    await enter_resolution(request, id, message_id, form_data.model_dump(exclude_unset=True), user, db=db)
'''
MODEL_WRAPPER = '''# 平台预算仅包裹原函数，压缩、工具循环仍调用同一官方执行入口。
async def generate_chat_completion(request, form_data, user, bypass_filter=False, bypass_system_prompt=False):
    from open_webui.utils.aic_chat_budget import model_dispatch
    return await model_dispatch(request, form_data, user, _aic_original_generate_chat_completion,
                                bypass_filter=bypass_filter, bypass_system_prompt=bypass_system_prompt)


'''

REPLACEMENTS = {
    "main.py": [
        ("@app.post('/api/chat/completions')\n", CAPABILITY + "@app.post('/api/chat/completions')\n"),
        ("    if not request.app.state.MODELS:\n        await get_all_models(request, user=user)\n\n    model_id = form_data.get('model', None)",
         ENTRY + "    if not request.app.state.MODELS:\n        await get_all_models(request, user=user)\n\n    model_id = form_data.get('model', None)"),
        ("    async def process_chat(request, form_data, user, metadata, model, tasks=None):\n",
         "    from open_webui.utils.aic_chat_budget import bounded_process\n    @bounded_process\n    async def process_chat(request, form_data, user, metadata, model, tasks=None):\n"),
        ("    resolution = await resolve_tool_call_output(id, message_id, form_data, user, db=db)\n",
         RESOLUTION + "    resolution = await resolve_tool_call_output(id, message_id, form_data, user, db=db)\n"),
        ("    requested_model = form_data.get('model', '')\n    request.state.model_id = requested_model\n",
         "    from open_webui.utils.aic_chat_budget import provider_context\n    provider_context(request, form_data, user)\n\n    requested_model = form_data.get('model', '')\n    request.state.model_id = requested_model\n"),
    ],
    "utils/chat.py": [
        ("async def generate_chat_completion(\n", "async def _aic_original_generate_chat_completion(\n"),
        ("chat_completion = generate_chat_completion\n", MODEL_WRAPPER + "chat_completion = generate_chat_completion\n"),
    ],
    "utils/middleware.py": [
        ("                    tool_call_iterations += 1\n",
         "                    tool_call_iterations += 1\n                    from open_webui.utils.aic_chat_budget import consume_tool_iteration\n                    consume_tool_iteration(request, tool_calls[0])\n"),
    ],
    "routers/openai.py": [
        ("    # keep the model the client asked for when reached from /api/chat/completions\n",
         "    from open_webui.utils.aic_chat_budget import provider_context\n    provider_context(request, form_data, user)\n\n    # keep the model the client asked for when reached from /api/chat/completions\n"),
        ("    is_streaming_request = bool(payload.get('stream', False))\n    if not is_streaming_request:\n",
         "    from open_webui.utils.aic_chat_budget import provider_payload\n    provider_payload(request, payload, user)\n\n    is_streaming_request = bool(payload.get('stream', False))\n    if not is_streaming_request:\n"),
        ("    payload = form_data.model_dump(exclude_none=True)\n    is_streaming_request = bool(payload.get('stream', False))\n",
         "    payload = form_data.model_dump(exclude_none=True)\n    from open_webui.utils.aic_chat_budget import provider_context\n    provider_context(request, payload, user)\n    is_streaming_request = bool(payload.get('stream', False))\n"),
        ("    is_streaming_request = bool(payload.get('stream', False)) if isinstance(payload, dict) else False\n",
         "    from open_webui.utils.aic_chat_budget import provider_context\n    if isinstance(payload, dict):\n        provider_context(request, payload, user)\n    is_streaming_request = bool(payload.get('stream', False)) if isinstance(payload, dict) else False\n"),
    ],
}


def digest(content):
    return hashlib.sha256(content).hexdigest()


def transform(name, content):
    """所有替换锚点必须唯一，不用模糊查找尝试适配未知上游版本。"""
    changed = content
    for anchor, replacement in REPLACEMENTS[name]:
        before, after = anchor.encode("utf-8"), replacement.encode("utf-8")
        if changed.count(before) != 1:
            raise RuntimeError("owui_budget_patch_anchor_invalid")
        changed = changed.replace(before, after, 1)
    compile(changed, name, "exec")
    return changed


def prepare_patch(name, content):
    before = digest(content)
    if name not in ORIGINAL_SHA256:
        raise RuntimeError("owui_budget_patch_source_unknown")
    if before == PATCHED_SHA256[name]:
        return content, {"source": name, "applied": False, "before_sha256": before, "after_sha256": before}
    if before != ORIGINAL_SHA256[name]:
        raise RuntimeError("owui_budget_patch_source_unknown")
    changed = transform(name, content)
    if digest(changed) != PATCHED_SHA256[name]:
        raise RuntimeError("owui_budget_patch_digest_mismatch")
    return changed, {"source": name, "applied": True, "before_sha256": before, "after_sha256": digest(changed)}


def managed_sources(root):
    """只核受管 Windows 运行根内的固定源码，不接收任意源文件或业务库路径。"""
    root = Path(root).absolute()
    if root.resolve() != root:
        raise RuntimeError("owui_budget_patch_root_invalid")
    manifest = json.loads((root / "runtime.json").read_text("utf-8"))
    source = root / "sources/openwebui"
    backend = source / "backend/open_webui"
    if (manifest.get("kind") != "manufacturing-native-windows" or manifest.get("root") != str(root)
            or not isinstance(manifest.get("id"), str) or not manifest["id"]
            or manifest.get("sources", {}).get("openwebui") != str(source)
            or source.resolve() != source or backend.resolve() != backend
            or json.loads((source / "package.json").read_text("utf-8")).get("version") != "0.12.3"):
        raise RuntimeError("owui_budget_patch_source_invalid")
    for name in ORIGINAL_SHA256:
        path = backend / name
        if path.resolve() != path or not path.is_file() or path.stat().st_size > 1024 * 1024:
            raise RuntimeError("owui_budget_patch_source_invalid")
    return backend, manifest


def write_verified(target, content, original):
    """逐文件原子替换并做内容CAS；任何漂移停止，保留可追溯的已完成部分。"""
    before = target.stat() if target.exists() else None
    pending = None
    try:
        with NamedTemporaryFile(dir=target.parent, prefix=".aic-budget-", suffix=".pending", delete=False) as stream:
            pending = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if before is not None:
            pending.chmod(stat.S_IMODE(before.st_mode))
        if target.resolve() != target or (target.read_bytes() if target.exists() else None) != original:
            raise RuntimeError("owui_budget_patch_source_changed")
        if before is not None and target.stat().st_ino != before.st_ino:
            raise RuntimeError("owui_budget_patch_source_changed")
        pending.replace(target)
        pending = None
        if digest(target.read_bytes()) != digest(content):
            raise RuntimeError("owui_budget_patch_write_unconfirmed")
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def apply_patch(root):
    """先核全体已知摘要，再安装独立守卫和四处适配；不改凭据、不启动服务。"""
    backend, manifest = managed_sources(root)
    helper = HELPER_SOURCE.read_bytes()
    if digest(helper) != HELPER_SHA256:
        raise RuntimeError("owui_budget_patch_helper_changed")
    target = backend / HELPER_TARGET
    if target.resolve() != target:
        raise RuntimeError("owui_budget_patch_source_invalid")
    old_helper = target.read_bytes() if target.exists() else None
    if old_helper is not None and digest(old_helper) != HELPER_SHA256:
        raise RuntimeError("owui_budget_patch_helper_unknown")
    changes = []
    for name in ORIGINAL_SHA256:
        original = (backend / name).read_bytes()
        changed, receipt = prepare_patch(name, original)
        changes.append((backend / name, original, changed, receipt))
    if old_helper is None:
        write_verified(target, helper, None)
    for path, original, changed, receipt in changes:
        if receipt["applied"]:
            write_verified(path, changed, original)
    return {"patch_id": PATCH_ID, "runtime_id": manifest["id"], "helper_sha256": HELPER_SHA256,
            "files": [row[3] for row in changes], "global_iteration_limit_changed": False}


def budget_environment(runtime, paths, secret):
    """非密文开关只保存pin；独立密钥从现有加密凭据读取，不输出或写入收据。"""
    root = paths["data"].parent.parent
    config_file = root / CONFIG_FILE
    if not config_file.exists():
        return {}
    if config_file.resolve() != config_file or config_file.stat().st_size > 16 * 1024:
        raise RuntimeError("owui_budget_config_invalid")
    config = json.loads(config_file.read_text("utf-8"))
    backend, manifest = managed_sources(root)
    if (not isinstance(config, dict) or set(config) != {"kind", "runtime_id", "enabled", "alias_id", "server_id"}
            or config.get("kind") != CONFIG_KIND or config.get("runtime_id") != manifest["id"]
            or type(config.get("enabled")) is not bool):
        raise RuntimeError("owui_budget_config_invalid")
    if not config["enabled"]:
        return {}
    alias, server = config.get("alias_id"), config.get("server_id")
    value = secret.get(SECRET_KEY)
    if (not isinstance(alias, str) or not re.fullmatch(r"[^\s\x00-\x20]{1,300}", alias)
            or not isinstance(server, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", server)
            or not isinstance(value, str) or not 32 <= len(value) <= 4096 or "\x00" in value
            or value in {secret.get("webui_secret"), secret.get("openwebui_tool_forward_jwt_secret")}
            or any(digest((backend / name).read_bytes()) != expected for name, expected in PATCHED_SHA256.items())
            or not (backend / HELPER_TARGET).is_file() or (backend / HELPER_TARGET).resolve() != backend / HELPER_TARGET
            or digest((backend / HELPER_TARGET).read_bytes()) != HELPER_SHA256):
        raise RuntimeError("owui_budget_not_ready")
    from owui_tool_forward_patch import forwarding_environment
    forwarding = forwarding_environment(runtime, paths, secret)
    if forwarding.get("AIC_PLATFORM_TOOL_SERVER_ID") != server:
        raise RuntimeError("owui_budget_server_mismatch")
    return {"AIC_NATIVE_TURN_BUDGET_SECRET": value,
            "AIC_NATIVE_TURN_BUDGET_ALIAS_ID": alias, "AIC_NATIVE_TURN_BUDGET_SERVER_ID": server}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    print(json.dumps(apply_patch(parser.parse_args().root), ensure_ascii=False))


if __name__ == "__main__":
    main()
