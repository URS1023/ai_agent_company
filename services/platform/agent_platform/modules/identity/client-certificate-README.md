# 客户端证书接入

面向集成开发和平台运维。前端入口：组织权限 → 服务身份 → 证书。沿用平台服务身份、当前角色范围、短期Bearer和审计，不另外创建一套业务授权。

## 登记与轮换

管理员登记调用方的单张PEM公钥证书。支持RSA 2048—8192位/RS256、P-256/ES256；私钥只留调用方，不上传平台。证书是管理员明确固定信任的公钥，不自动联网获取CA链、CRL、OCSP或JWKS。CA证书、已到期证书及禁止数字签名的证书不能登记；未来生效证书可提前登记。

轮换创建新证书记录，原子撤销旧证书，并保留旧证书和关联审计。停用、撤销及轮换均撤销旧令牌，重新启用不会使旧令牌复活；新证书尚未生效时不能替换当前证书。

## 调用方接入

从“接入”窗口取得客户端ID、证书kid、签名算法与令牌地址。服务端`AGENT_CLIENT_ASSERTION_AUDIENCE`固定接收方，必须为HTTPS；本机调试允许loopback HTTP。路径为`/api/v1/client-auth/token`，不能靠Host、Forwarded或X-Forwarded-Host改变接收方。

以下示例只在调用方读取私钥并签名。将参数替换为接入窗口的真实值；每次认证使用新jti，不重放已有断言。需要Python的`PyJWT[crypto]`和HTTP客户端。

```python
from pathlib import Path
import secrets
import time
import jwt
import httpx

client_id = "从接入窗口复制"
certificate_id = "从接入窗口复制kid"
endpoint = "https://your-platform.example/api/v1/client-auth/token"
now = int(time.time())
assertion = jwt.encode(
    {"iss": client_id, "sub": client_id, "aud": endpoint,
     "iat": now, "nbf": now, "exp": now + 120, "jti": secrets.token_hex(24)},
    Path("caller-private-key.pem").read_bytes(),
    algorithm="ES256",  # 按登记结果选择ES256或RS256。
    headers={"kid": certificate_id},
)
response = httpx.post(endpoint, data={
    "grant_type": "client_credentials", "client_id": client_id,
    "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
    "client_assertion": assertion, "scope": "m11.read",
}, timeout=10, follow_redirects=False)
response.raise_for_status()
access_token = response.json()["access_token"]
# 仅在调用方内存中使用Authorization: Bearer；不要打印或写入日志。
```

断言窗口最多5分钟，要求iss/sub/aud/iat/nbf/exp/jti；允许15秒时钟偏差，但已到期断言不接受。签发令牌最多10分钟且不超过证书有效期。省略scope时使用当前角色与服务身份的具体权限交集；显式scope不能越权。没有refresh token。

## 事务和安全边界

`client_certificate_router`解析32KiB以内标准表单，`client_certificate_crypto`用cryptography/PyJWT解析和验签，`client_certificate_service`处理最新授权与重放账本，再复用`service_accounts.mint_service_token`签发。断言摘要、会话、服务令牌及证书关联在同一事务提交。身份+jti摘要唯一约束承担并发防重放，轮换证书也不能重复使用同一jti；不保存断言原文。失败回滚整个交换，不自动重试。

每次Bearer请求通过原`resolve_service_actor`再读证书关联，检查同工作区、原身份、有效期和撤销；最新用户/成员/角色、身份范围和令牌范围继续相交。手动签发的既有服务令牌保持原行为。

本实现是RFC7523客户端签名认证加短期Bearer，不是mTLS、RFC8705证书持钥绑定或完整OAuth授权码服务器。认证后的Bearer仍须保密。生产按平台部署策略在入口配置TLS和请求速率限制；公钥证书之外的私钥/CA签发不属于此入口。

参考：[RFC7523](https://www.rfc-editor.org/rfc/rfc7523.html)、[PyJWT官方API](https://pyjwt.readthedocs.io/en/stable/api.html)。qKnow客户端/CA管理的列表与表单结构作为界面参考，没有复制其明文secretKey展示或私钥文件下载方式。
