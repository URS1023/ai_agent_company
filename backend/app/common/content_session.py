"""Browser-only content authentication, never placed in links or page markup.

浏览器内容鉴权用的 HttpOnly Cookie：让"直接在地址栏/新标签页打开"的内容类 URL 也能通过鉴权。

用途：图片、文件下载、文档与演示预览这类内容地址由浏览器自身发起请求（``<img>``、``<a download>``、
iframe 等），无法附带 ``Authorization`` 头，因此在登录成功后把**同一令牌**写进 HttpOnly Cookie
（``path=/api``），由 ``deps.get_content_auth`` 读取后走常规的权限与数据范围校验。

只读限制（关键）：``deps.get_content_auth`` 仅在 ``GET``/``HEAD`` 请求时读取该 Cookie，
写请求（POST/PUT/PATCH/DELETE）仍必须携带 Bearer 头。因此该 Cookie 无法被用来触发任何变更操作，
这是"浏览器便利性"与"CSRF 面"之间的取舍——不要放宽这条限制，也不要为写接口读取该 Cookie。

约束：令牌只存在于 Cookie（HttpOnly，前端 JS 读不到），不写入链接、查询串或页面标记，
避免经日志/Referer 泄漏；``samesite="lax"`` + 仅 HTTPS 时 ``secure``；``path=/api`` 之外不要放开。
"""
from fastapi import Request, Response

# Cookie 名是前后端与部署（反向代理/CORS 白名单）共享的约定，改名等于让所有在线会话的内容
# 请求失效；deps.get_content_auth 直接引用该常量，不要在两处各写一份字面量。
COOKIE_NAME = "cloud_brain_content"


def set_content_session(response: Response, request: Request, token: str) -> None:
    """把会话令牌写入内容读取 Cookie（登录/回调/续期成功后调用）。

    参数：``response`` 本次响应；``request`` 用于判断协议（仅 HTTPS 请求才置 ``Secure``，本地
    HTTP 调试仍可用）；``token`` 与 Bearer 完全相同的会话令牌——不要为 Cookie 另发新令牌，
    否则登出/失效会出现两套状态。

    副作用：设置 ``Set-Cookie``（HttpOnly + lax + path=/api）并把响应头改为
    ``Cache-Control: private, no-store``，防止代理或浏览器缓存带凭据的响应。返回 None。
    本函数不验令牌有效性（由登录逻辑保证），也不写库。
    """
    response.set_cookie(
        COOKIE_NAME, token, path="/api", httponly=True,
        secure=request.url.scheme == "https", samesite="lax",
    )
    response.headers["Cache-Control"] = "private, no-store"


def clear_content_session(response: Response) -> None:
    """登出时删除内容读取 Cookie。

    删除必须使用与设置时一致的属性（``path``/``httponly``/``samesite``），否则浏览器会保留旧值；
    这里不吊销令牌本身——服务端令牌失效由 ``modules/auth`` 负责。返回 None。
    """
    response.delete_cookie(COOKIE_NAME, path="/api", httponly=True, samesite="lax")
