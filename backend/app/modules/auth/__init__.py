# -*- coding: utf-8 -*-
"""Authentication domain: local credentials, enterprise SSO and session lifecycle.

中文补充（认证业务域包）：本平台唯一的登录入口。包含本地账号口令登录、企业 SSO（OIDC）、
认证客户端令牌，以及会话生命周期（logout / me）与个人资料自助维护（profile / password）。

对外路由只有 ``router.py`` 暴露的 ``/api/auth/*``（``tags=["auth"]``）：
login、sso/providers、sso/{provider_id}/authorize、sso/callback、client-token、
logout、me、profile（GET/PATCH）、password。``schemas.py`` 定义请求/响应契约，
``service.py`` 实现本地口令登录、登出与改密。本文件仅作说明、不导出符号。

关键约束：
- ``/api/auth/`` 前缀在 ``common.authorization.enforce_request_permission`` 中被整体跳过，
  这组路由不做域名级权限码校验，权限边界必须由 service 层与「仅限本人」语义自行保证；
  新增接口时不要误以为挂上登录依赖就等于做了授权。
- 除 login、sso/providers、sso/{provider_id}/authorize、sso/callback、client-token 外，
  其余路由都要求已登录（``get_current_context``）。
- 持临时口令（``must_change_password``）的会话只能访问 me / logout / password，
  访问 profile 等路由会被 ``common.deps._check_password_change`` 以 403 拒绝并带
  ``password_change_required`` 标记。
- 响应体一律不得包含口令哈希、令牌摘要或客户端密钥。
"""
