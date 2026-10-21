"""复制进锁定 Open WebUI 的小型预算守卫；复用原聊天循环和官方认证。

仅专属别名要求平台签名。独立账本只存摘要、计数和恢复编号，不保存密钥、正文或 JWT。
首次派发和批准恢复先消费 nonce；模型及工具轮次在真正执行之前原子消费，不自动重试。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException
import jwt

PATCH_ID = "openwebui-0.12.3-platform-turn-budget-v1"
HEADER = "X-AIC-Native-Turn-Budget"
TOKEN_TYPE = "aic-native-turn-budget-v1"
APPLICATION_ID = 0x41494342
ID = re.compile(r"[A-Za-z0-9_-]{1,160}")
CLAIMS = {
    "iss", "aud", "v", "jti", "sub", "workspace_id", "integration_id", "chat_id",
    "user_message_id", "message_id", "model_id", "server_id", "policy_revision",
    "max_tool_calls", "max_tool_iterations", "max_model_requests", "max_tokens", "max_seconds",
    "deadline", "iat", "exp", "dispatch_kind", "dispatch_nonce", "request_path", "body_sha256",
}
DISPATCH_FIELDS = {"iat", "dispatch_kind", "dispatch_nonce", "request_path", "body_sha256"}


def reject(code="aic_native_budget_invalid", status=403):
    """只返回固定错误码，不把签名、请求正文或本地路径放入原生错误消息。"""
    raise HTTPException(status_code=status, detail=code)


def canonical(value):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        reject()
    if len(encoded) > 2 * 1024 * 1024:
        reject()
    return encoded


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


@dataclass(frozen=True)
class Configuration:
    alias: str
    server: str
    secret: str = field(repr=False)
    ledger: Path


def configuration():
    """密钥仅由受管子进程环境提供，路径固定在其独立 DATA_DIR 内。"""
    alias = os.environ.get("AIC_NATIVE_TURN_BUDGET_ALIAS_ID", "")
    server = os.environ.get("AIC_NATIVE_TURN_BUDGET_SERVER_ID", "")
    secret = os.environ.get("AIC_NATIVE_TURN_BUDGET_SECRET", "")
    directory = Path(os.environ.get("DATA_DIR", "")).absolute()
    if (not alias or len(alias) > 300 or any(char.isspace() for char in alias)
            or not ID.fullmatch(server) or not 32 <= len(secret) <= 4096 or "\x00" in secret
            or secret in {os.environ.get("WEBUI_SECRET_KEY"), os.environ.get("FORWARD_USER_INFO_HEADER_JWT_SECRET")}
            or not os.environ.get("DATA_DIR") or directory.resolve() != directory or not directory.is_dir()):
        reject("aic_native_budget_not_ready", 503)
    return Configuration(alias, server, secret, directory / "aic-native-turn-budget.sqlite3")


@contextmanager
def ledger(config):
    """只建立本守卫的新账本；已有未知文件、链接、锁冲突均拒绝，不接管或重试。"""
    path = config.ledger
    connection = None
    try:
        if path.resolve() != path:
            reject("aic_native_budget_store_unavailable", 503)
        created = not path.exists()
        if created:
            with path.open("xb"):
                pass
        connection = sqlite3.connect(path, timeout=0.05, isolation_level=None)
        connection.execute("BEGIN IMMEDIATE")
        if created:
            connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
            connection.execute("CREATE TABLE turns (id TEXT PRIMARY KEY, scope_sha256 TEXT NOT NULL, models INTEGER NOT NULL DEFAULT 0, iterations INTEGER NOT NULL DEFAULT 0, calls INTEGER NOT NULL DEFAULT 0)")
            connection.execute("CREATE TABLE nonces (turn_id TEXT NOT NULL, nonce TEXT NOT NULL, PRIMARY KEY(turn_id, nonce))")
            connection.execute("CREATE TABLE tool_calls (turn_id TEXT NOT NULL, call_id TEXT NOT NULL, iteration INTEGER NOT NULL, call_sha256 TEXT NOT NULL, PRIMARY KEY(turn_id, call_id))")
        elif connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            reject("aic_native_budget_store_unavailable", 503)
        yield connection
        connection.commit()
    except HTTPException:
        raise
    except (OSError, sqlite3.Error):
        reject("aic_native_budget_store_unavailable", 503)
    finally:
        if connection is not None:
            connection.close()


def capability():
    config = configuration()
    with ledger(config) as connection:
        connection.execute("SELECT id, scope_sha256, models, iterations, calls FROM turns LIMIT 0")
        connection.execute("SELECT turn_id, nonce FROM nonces LIMIT 0")
        connection.execute("SELECT turn_id, call_id, iteration, call_sha256 FROM tool_calls LIMIT 0")
    return {"version": 1, "patch_id": PATCH_ID, "alias_model_id": config.alias,
            "server_id": config.server, "ledger_ready": True}


@dataclass
class Budget:
    config: Configuration
    claims: dict
    monotonic_deadline: float

    def remaining(self):
        left = min(self.monotonic_deadline - time.monotonic(), self.claims["deadline"] - time.time())
        if left <= 0:
            reject("aic_native_budget_deadline", 504)
        return left


def state_budget(request):
    value = getattr(request.state, "aic_native_turn_budget", None)
    if value is not None and not isinstance(value, Budget):
        reject()
    if value is not None:
        value.remaining()
    return value


def claims_for(request, payload, user, kind):
    config = configuration()
    token = request.headers.get(HEADER)
    if not isinstance(token, str) or not 1 <= len(token) <= 16384:
        reject()
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "HS256" or header.get("typ") != TOKEN_TYPE:
            reject()
        claims = jwt.decode(token, config.secret, algorithms=["HS256"], issuer="ai-agent-company",
                            audience="openwebui-native-turn-budget-v1", options={"require": sorted(CLAIMS)})
    except jwt.PyJWTError:
        reject()
    if (set(claims) != CLAIMS or type(claims["v"]) is not int or claims["v"] != 1
            or claims["aud"] != "openwebui-native-turn-budget-v1"
            or claims["sub"] != getattr(user, "id", None)
            or claims["model_id"] != config.alias or claims["server_id"] != config.server
            or claims["dispatch_kind"] != kind or claims["request_path"] != request.url.path
            or claims["body_sha256"] != digest(payload)):
        reject()
    for key in ("jti", "sub", "workspace_id", "integration_id", "chat_id", "user_message_id", "message_id", "server_id", "dispatch_nonce"):
        if not isinstance(claims[key], str) or not ID.fullmatch(claims[key]):
            reject()
    for key, upper in (("policy_revision", 2**31 - 1), ("max_tool_calls", 32), ("max_tool_iterations", 16),
                       ("max_model_requests", 32), ("max_tokens", 4096), ("max_seconds", 90)):
        if type(claims[key]) is not int or not 1 <= claims[key] <= upper:
            reject()
    if (any(type(claims[key]) is not int for key in ("iat", "exp", "deadline"))
            or claims["exp"] != claims["deadline"] or not 0 < claims["deadline"] - claims["iat"] <= claims["max_seconds"]
            or not 0 < claims["deadline"] - time.time() <= claims["max_seconds"]
            or (kind == "completion" and claims["dispatch_nonce"] != claims["jti"])
            or (kind == "resolution" and claims["dispatch_nonce"] == claims["jti"])):
        reject()
    return Budget(config, claims, time.monotonic() + claims["deadline"] - time.time())


def consume_dispatch(budget):
    """恢复共享首次的不可变上限，重复请求在产生任何原生副作用前拒绝。"""
    budget.remaining()
    claims = budget.claims
    scope = digest({key: value for key, value in claims.items() if key not in DISPATCH_FIELDS})
    with ledger(budget.config) as connection:
        row = connection.execute("SELECT scope_sha256 FROM turns WHERE id=?", (claims["jti"],)).fetchone()
        if claims["dispatch_kind"] == "completion":
            if row is not None:
                reject("aic_native_budget_replayed", 409)
            connection.execute("INSERT INTO turns(id,scope_sha256) VALUES (?,?)", (claims["jti"], scope))
        elif row is None or row[0] != scope:
            reject()
        if connection.execute("SELECT 1 FROM nonces WHERE turn_id=? AND nonce=?", (claims["jti"], claims["dispatch_nonce"])).fetchone():
            reject("aic_native_budget_replayed", 409)
        connection.execute("INSERT INTO nonces(turn_id,nonce) VALUES (?,?)", (claims["jti"], claims["dispatch_nonce"]))
        budget.remaining()


def bind_payload(request, payload, user, budget):
    """首次与原生批准恢复都核准确消息、别名和唯一服务，不信任客户端工具集合。"""
    claims = budget.claims
    user_message = payload.get("user_message") or {}
    if (claims["sub"] != getattr(user, "id", None) or payload.get("model") != claims["model_id"]
            or payload.get("chat_id") != claims["chat_id"] or payload.get("id") != claims["message_id"]
            or user_message.get("id") != claims["user_message_id"]
            or payload.get("tool_ids") != ["server:" + claims["server_id"]]
            or payload.get("message_ids") or payload.get("model_item") or payload.get("tool_servers")
            or payload.get("tools") is not None or payload.get("skill_ids") or payload.get("filter_ids")
            or payload.get("terminal_id") or any((payload.get("features") or {}).values())
            or any((payload.get("background_tasks") or {}).values())):
        reject()
    payload.setdefault("params", {})["function_calling"] = "native"
    request.state.aic_native_turn_budget = budget
    request.state.max_tool_call_iterations = claims["max_tool_iterations"]
    budget.remaining()


async def enter_completion(request, payload, user):
    prior = state_budget(request)
    if prior is not None:
        if prior.claims["dispatch_kind"] != "resolution":
            reject("aic_native_budget_replayed", 409)
        bind_payload(request, payload, user, prior)
        return
    if (not request.headers.get(HEADER)
            and payload.get("model") != os.environ.get("AIC_NATIVE_TURN_BUDGET_ALIAS_ID", "")):
        return
    budget = claims_for(request, payload, user, "completion")
    bind_payload(request, payload, user, budget)
    consume_dispatch(budget)


async def enter_resolution(request, chat_id, message_id, payload, user, db=None):
    """先读官方已授权消息再校签名；未签名的专属别名不能先批准再补验证。"""
    from open_webui.models.chats import Chats
    chat = await Chats.get_accessible_chat_by_id(chat_id, user, db=db, permission="write")
    message = await Chats.get_message_by_id_and_message_id(chat_id, message_id) if chat else None
    model = message.get("model") if isinstance(message, dict) else None
    if not request.headers.get(HEADER) and model != os.environ.get("AIC_NATIVE_TURN_BUDGET_ALIAS_ID", ""):
        return
    budget = claims_for(request, payload, user, "resolution")
    claims = budget.claims
    if (chat is None or chat.user_id != user.id or not isinstance(message, dict)
            or claims["chat_id"] != chat_id or claims["message_id"] != message_id
            or claims["model_id"] != model or message.get("parentId") != claims["user_message_id"]
            or (message.get("user_id") or chat.user_id) != user.id):
        reject()
    consume_dispatch(budget)
    request.state.aic_native_turn_budget = budget


def consume_tool_iteration(request, calls):
    """同一批及其审批后剩余子集只计一次；新调用不得冒用旧 call_id。"""
    budget = state_budget(request)
    if budget is None:
        return
    if not isinstance(calls, list) or not 1 <= len(calls) <= 128:
        reject()
    fingerprints = {}
    for call in calls:
        if not isinstance(call, dict):
            reject()
        call_id = call.get("id") or "fc_" + uuid4().hex
        if not isinstance(call_id, str) or not ID.fullmatch(call_id) or call_id in fingerprints:
            reject()
        call["id"] = call_id
        fingerprints[call_id] = digest({"function": call.get("function"), "provider_function": call.get("provider_function")})
    with ledger(budget.config) as connection:
        claims = budget.claims
        existing = {}
        for call_id in fingerprints:
            row = connection.execute("SELECT iteration,call_sha256 FROM tool_calls WHERE turn_id=? AND call_id=?", (claims["jti"], call_id)).fetchone()
            if row:
                existing[call_id] = row
        if existing:
            if (set(existing) != set(fingerprints) or len({row[0] for row in existing.values()}) != 1
                    or any(row[1] != fingerprints[key] for key, row in existing.items())):
                reject()
        else:
            row = connection.execute("SELECT iterations,calls FROM turns WHERE id=?", (claims["jti"],)).fetchone()
            if row is None:
                reject()
            if row[0] >= claims["max_tool_iterations"] or row[1] + len(calls) > claims["max_tool_calls"]:
                reject("aic_native_budget_tool_limit", 429)
            connection.execute("UPDATE turns SET iterations=iterations+1,calls=calls+? WHERE id=?", (len(calls), claims["jti"]))
            connection.executemany("INSERT INTO tool_calls(turn_id,call_id,iteration,call_sha256) VALUES (?,?,?,?)",
                                   [(claims["jti"], key, row[0] + 1, value) for key, value in fingerprints.items()])
        budget.remaining()


def provider_context(request, payload, user):
    """原生兼容 API 也先核专属别名，不能等别名已转成基础模型后才判断。"""
    budget = state_budget(request)
    if budget is None:
        if payload.get("model") == os.environ.get("AIC_NATIVE_TURN_BUDGET_ALIAS_ID", ""):
            reject()
    elif budget.claims["sub"] != getattr(user, "id", None):
        reject()


def provider_payload(request, payload, user):
    """在原 OpenAI 适配最终编码前消费一次实际派发，并覆盖最终 token 上限。"""
    budget = state_budget(request)
    if budget is None:
        return
    if budget.claims["sub"] != getattr(user, "id", None):
        reject()
    with ledger(budget.config) as connection:
        result = connection.execute("UPDATE turns SET models=models+1 WHERE id=? AND models<?",
                                    (budget.claims["jti"], budget.claims["max_model_requests"]))
        if result.rowcount != 1:
            reject("aic_native_budget_model_limit", 429)
        budget.remaining()
    keys = [key for key in ("max_tokens", "max_completion_tokens", "max_output_tokens") if key in payload]
    for key in keys or ["max_output_tokens" if payload.get("input") is not None else "max_tokens"]:
        value = payload.get(key, budget.claims["max_tokens"])
        payload[key] = min(value, budget.claims["max_tokens"]) if type(value) is int and value > 0 else budget.claims["max_tokens"]
    payload["n"] = 1


async def model_dispatch(request, payload, user, function, **kwargs):
    """沿原模型调用与流逐块消费，仅给受管 Turn 施加同一绝对截止。"""
    budget = state_budget(request)
    if budget is None:
        if payload.get("model") == os.environ.get("AIC_NATIVE_TURN_BUDGET_ALIAS_ID", ""):
            reject()
        return await function(request, payload, user, **kwargs)
    from starlette.responses import StreamingResponse
    try:
        async with asyncio.timeout(budget.remaining()):
            response = await function(request, payload, user, **kwargs)
        if isinstance(response, StreamingResponse):
            original = response.body_iterator

            async def bounded_stream():
                try:
                    while True:
                        try:
                            async with asyncio.timeout(budget.remaining()):
                                chunk = await anext(original)
                        except StopAsyncIteration:
                            return
                        yield chunk
                except TimeoutError:
                    reject("aic_native_budget_deadline", 504)
                finally:
                    close = getattr(original, "aclose", None)
                    if close is not None:
                        await close()

            response.body_iterator = bounded_stream()
        return response
    except TimeoutError:
        reject("aic_native_budget_deadline", 504)


def bounded_process(function):
    """原后台聊天任务沿其取消与清理分支退出，不重建执行循环或延长运行期限。"""
    async def limited(request, *args, **kwargs):
        budget = state_budget(request)
        if budget is None:
            return await function(request, *args, **kwargs)
        try:
            async with asyncio.timeout(budget.remaining()):
                return await function(request, *args, **kwargs)
        except TimeoutError:
            reject("aic_native_budget_deadline", 504)
    return limited
