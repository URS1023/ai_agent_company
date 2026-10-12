"""Bounded JSON/text HTTP transport with pinned DNS, TLS and no redirects."""
from __future__ import annotations

import http.client
import ipaddress
import json
import math
import re
import socket
import ssl
import time
from threading import Event, Timer
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import parse_qsl, quote, urlsplit

from app.errors import AppError, ToolExecutionError, ValidationError

# SSRF 防护常量（改动前请先读 validate_url / validate_address / resolve_destination）：
#   MAX_RESPONSE_BYTES 是**单条响应**的读取上限，超过即拒绝（不截断后当成功）；
#   MAX_BODY_BYTES 是单条请求体（含凭据展开后）的上限；
#   SECRET_REFERENCE 是唯一允许出现在请求头/请求体里的凭据写法 ${secret.名称}；
#   SENSITIVE_NAMES 用来识别“看起来存凭据”的头部或字段名，命中后必须整体引用凭据；
#   FORBIDDEN_HEADERS 交回给传输层管理（Host/长度/编码/转发头），防止绕过地址校验；
#   _DNS_EXECUTOR 让域名解析带超时（解析可能被恶意 DNS 拖延）；
#   _PRIVATE_NETWORKS 仅在管理员显式授权 allow_private 时才允许访问。
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_BODY_BYTES = 1024 * 1024
SECRET_REFERENCE = re.compile(r"\$\{secret\.([A-Za-z][A-Za-z0-9_]{0,59})\}")
SENSITIVE_NAMES = frozenset({
    "authorization", "proxyauthorization", "cookie", "setcookie", "apikey", "xapikey",
    "password", "passwd", "secret", "token", "accesstoken", "refreshtoken", "clientsecret",
    "xauthtoken", "accesskey", "secretkey",
})
FORBIDDEN_HEADERS = frozenset({
    "host", "connection", "content-length", "transfer-encoding", "te", "trailer", "upgrade",
    "proxy-authorization", "proxy-connection", "forwarded", "x-forwarded-for", "x-forwarded-host",
    "accept-encoding", "content-encoding",
})
_DNS_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="collection-dns")
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7",
))


def sensitive_name(value: str) -> bool:
    """判断名称是否属于“存凭据”的那一类（先去掉非字母数字再比较，兼容 X-Api-Key 等写法）。"""
    return re.sub(r"[^a-z0-9]", "", value.lower()) in SENSITIVE_NAMES


def validate_url(url: str):
    """Reject ambiguous URLs and credential-bearing query strings before DNS."""

    # 采集地址是**固定目标**，因此这里在解析域名之前就拒绝一切含糊或可变的写法：
    # 控制字符/空白/反斜杠、非 http(s) 协议、URL 里内嵌用户名密码、片段、含 %/$/{}/ 
    # （变量或模板）、非法端口、缺主机；并明确拒绝基础设施元数据域名。
    # 查询串里出现敏感参数名（token/apikey 等）且有值时也拒绝——凭据只能通过
    # ${secret.名称} 引用，避免明文写入配置或被日志记录。
    # 抛出：任一条件不满足抛 ValidationError。返回 urlsplit 结果供调用方复用。
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url) or "\\" in url:
        raise ValidationError("HTTP 地址不能含空白、控制字符或反斜杠")
    try:
        parsed = urlsplit(url)
        port = parsed.port
        host = parsed.hostname or ""
    except ValueError as exc:
        raise ValidationError("HTTP 地址或端口格式不正确") from exc
    if parsed.scheme not in {"http", "https"} or not host or parsed.username is not None or parsed.password is not None:
        raise ValidationError("只允许不带用户名密码的 HTTP(S) 地址")
    if parsed.fragment or "%" in host or "$" in url or "{" in url or "}" in url:
        raise ValidationError("采集地址必须固定，不能含片段、变量或 IPv6 区域标识")
    if port is not None and not 1 <= port <= 65535:
        raise ValidationError("HTTP 端口必须为 1–65535")
    try:
        host.encode("idna")
    except UnicodeError as exc:
        raise ValidationError("HTTP 主机名不正确") from exc
    if host.lower().rstrip(".") in {"metadata.google.internal", "metadata.azure.internal", "instance-data"}:
        raise ValidationError("不能访问基础设施元数据服务")
    if any(sensitive_name(key) and value for key, value in parse_qsl(parsed.query, keep_blank_values=True)):
        raise ValidationError("URL 查询参数不能保存凭据；请使用加密请求头或 JSON 请求体引用")
    return parsed


def validate_headers(headers: dict[str, str]) -> None:
    """校验请求头：数量/总大小有上限，名称合法且不重复，禁止由传输层管理的头部。

    FORBIDDEN_HEADERS（Host/Content-Length/Transfer-Encoding/Accept-Encoding/
    X-Forwarded-* 等）交回给传输层决定，避免调用方伪造 Host 或长度绕过地址校验。
    敏感头部（Authorization/Token 等）的值必须**完整**写成 ${secret.名称}，
    允许带 Bearer/Basic 前缀，但不允许在引号外夹带任何明文值。
    抛出：违反任一条抛 ``ValidationError``；本函数不返回内容，只做校验。
    """
    if len(headers) > 40 or len(json.dumps(headers, ensure_ascii=False).encode("utf-8")) > 16384:
        raise ValidationError("请求头最多 40 项、合计 16 KB")
    names = set()
    for key, value in headers.items():
        if not re.fullmatch(r"[A-Za-z0-9!#$%&'*+.^_`|~-]{1,100}", key):
            raise ValidationError("请求头名称格式不正确")
        lower = key.lower()
        if lower in names or lower in FORBIDDEN_HEADERS:
            raise ValidationError("请求头重复或包含由安全传输层管理的字段")
        names.add(lower)
        if any(ord(c) < 32 or ord(c) == 127 for c in value) or len(value) > 8192:
            raise ValidationError("请求头值含控制字符或过长")
        if sensitive_name(key) and value:
            template = re.sub(r"^(?:Bearer|Basic)\s+", "", value, flags=re.IGNORECASE)
            if not SECRET_REFERENCE.fullmatch(template):
                raise ValidationError("认证请求头必须完整引用加密凭据，可加 Bearer/Basic 前缀，不能夹带明文值")


def validate_body_secrets(value, *, depth: int = 0) -> None:
    """校验请求体：拒绝原型污染键名，且敏感字段值必须是完整的凭据引用。

    递归检查所有层级（深度上限 32，防止恶意深嵌套），命中 ``__proto__``/
    prototype/constructor 直接拒绝；字段名看起来是凭据（password/token/…）时，
    值只能是 ${secret.名称} 或留空，不允许明文。
    抛出：违反任一条抛 ``ValidationError``。
    """
    if depth > 32:
        raise ValidationError("请求体嵌套不能超过 32 层")
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {"__proto__", "prototype", "constructor"}:
                raise ValidationError("请求体包含不安全的键")
            if sensitive_name(key) and item not in (None, "") and (
                not isinstance(item, str) or not SECRET_REFERENCE.fullmatch(item)
            ):
                raise ValidationError("敏感请求体字段必须使用完整的加密凭据引用")
            validate_body_secrets(item, depth=depth + 1)
    elif isinstance(value, list):
        for item in value:
            validate_body_secrets(item, depth=depth + 1)


def referenced_names(value) -> set[str]:
    """收集配置中引用的凭据名称；出现残缺占位符（如 ``${secret.`` 未闭合）即拒绝。

    用于保存前校验“引用的凭据是否都已配置”，避免运行期才发现缺凭据。
    抛出：含无效占位符时抛 ``ValidationError``。
    """
    if isinstance(value, str):
        names = set(SECRET_REFERENCE.findall(value))
        if "${secret." in SECRET_REFERENCE.sub("", value):
            raise ValidationError("存在无效的加密凭据占位符")
        return names
    if isinstance(value, dict):
        return set().union(*(referenced_names(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(referenced_names(item) for item in value))
    return set()


def resolve_references(value, credentials: dict[str, str], *, max_bytes: int = MAX_BODY_BYTES):
    """Expand references under a shared budget before allocating an oversized body."""
    remaining = max_bytes
    def charge(text: str) -> str:
        """按展开后的字节数扣减**共享预算**，超支即拒绝。

        必须在分配字符串之前记账，否则多个大图片/大凭据引用会在真正超限后才被发现。
        """
        nonlocal remaining
        remaining -= len(text.encode("utf-8"))
        if remaining < 0:
            raise ValidationError("凭据展开后的请求超过大小限制，请减少请求内容")
        return text
    def resolve(item):
        """递归展开 ${secret.名称}；字段名/键也要记账（键同样是可被填充的文本）。"""
        if isinstance(item, str):
            parts, start = [], 0
            for match in SECRET_REFERENCE.finditer(item):
                parts.extend((charge(item[start:match.start()]), charge(credentials[match.group(1)])))
                start = match.end()
            parts.append(charge(item[start:]))
            return "".join(parts)
        if isinstance(item, dict):
            return {charge(key): resolve(child) for key, child in item.items()}
        if isinstance(item, list):
            return [resolve(child) for child in item]
        charge(str(item))
        return item
    return resolve(value)


def redact_values(value, credentials: dict[str, str]):
    """An upstream echo must not turn a write-only credential into a result field."""
    secrets = sorted(set(credentials.values()), key=len, reverse=True)
    def redact(item):
        """递归替换响应中的所有凭据值；键名脱敏后冲突则报错而不是覆盖。"""
        if isinstance(item, str):
            for secret in secrets:
                if secret:
                    item = item.replace(secret, "[REDACTED]")
            return item
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                safe_key = redact(key)
                if safe_key in result:
                    raise ValidationError("响应字段名脱敏后冲突，请调整接口字段名")
                result[safe_key] = redact(child)
            return result
        if isinstance(item, list):
            return [redact(child) for child in item]
        if item is not None and str(item) in secrets:
            return "[REDACTED]"
        return item
    return redact(value)


def validate_address(value: str, allow_private: bool) -> None:
    """SSRF 核心判定：只允许公网地址，除非管理员显式授权本机/内网。

    逐条排除：无效地址、IPv4 映射的 IPv6、198.18.0.0/15（代理 Fake-IP 测试网段，
    得到该地址说明 DNS 不可信，必须修 DNS 而不是放开内网）、云元数据地址
    （100.100.100.200 / 168.63.129.16）、链路本地、多播、未指定与保留地址；
    最后要求地址是全局可路由，或 allow_private 且属于回环/私有网段。
    抛出：任一命中抛 ``ValidationError``（消息里给出可执行的修复建议）。
    """
    try:
        address = ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValidationError("DNS 返回了无效目标地址") from exc
    address = address.ipv4_mapped if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped else address
    if address.version == 4 and address in ipaddress.ip_network("198.18.0.0/15"):
        raise ValidationError(
            "目标解析到 198.18.0.0/15 测试网段，可能是代理的 Fake-IP DNS；"
            "请让服务端使用返回真实公网地址的 DNS，或为该域名关闭 Fake-IP 后重试。"
            "开启内网访问不能解决此问题"
        )
    if str(address) in {"100.100.100.200", "168.63.129.16"} or address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
        raise ValidationError("不能访问元数据、链路本地、多播、未指定或保留地址")
    private = address.is_loopback or any(address.version == network.version and address in network for network in _PRIVATE_NETWORKS)
    if not address.is_global and not (allow_private and private):
        raise ValidationError("目标不是公网地址；本机和内网设备需管理员明确授权")


@dataclass(frozen=True)
class Destination:
    """一次请求的最终目标：原始 Host（用于 Host 头与 TLS SNI）+ 已校验的字面 IP。

    冻结对象，保证 DNS 校验结果不会被后续代码改动（校验与连接必须针对同一地址）。
    """

    host: str
    port: int
    ip: str
    path: str
    tls: bool


def resolve_destination(url: str, *, allow_private: bool, deadline: float) -> Destination:
    """解析并**冻结**目标地址：先校验 URL，再解析 DNS，逐条校验全部返回地址。

    防 DNS 重绑定/混合解析：不只看第一条结果，任何一条返回地址不合规都拒绝；
    解析本身走带超时的线程池，受本批 deadline 约束（恶意 DNS 无法拖住整批采集）。
    返回首个可用地址构成的 Destination；连接阶段只连这个字面 IP，不再二次解析。
    抛出：超时/解析失败抛 ``ToolExecutionError``；地址不合规抛 ``ValidationError``。
    """
    parsed = validate_url(url)
    host = parsed.hostname.encode("idna").decode("ascii")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ToolExecutionError("采集已达到整批超时限制")
    future = _DNS_EXECUTOR.submit(socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM)
    try:
        records = future.result(timeout=min(5, remaining))
    except FutureTimeout as exc:
        future.cancel()
        raise ToolExecutionError("解析 HTTP 目标主机超时") from exc
    except OSError as exc:
        raise ToolExecutionError("无法解析 HTTP 目标主机", error_type=type(exc).__name__) from exc
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise ToolExecutionError("HTTP 目标没有可用地址")
    # Reject mixed public/private DNS, not merely the first result. Every request
    # is revalidated and then connects to the chosen literal IP (no second DNS).
    for address in addresses:
        validate_address(address, allow_private)
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~") + ("?" + quote(parsed.query, safe="=&?/%:@!$'()*+,;-._~") if parsed.query else "")
    return Destination(host, port, addresses[0], path, parsed.scheme == "https")


class PinnedConnection(http.client.HTTPConnection):
    """Keep the original HTTP Host/TLS SNI, but connect only to a checked IP."""

    def __init__(self, destination: Destination, timeout: float):
        """记录目标与超时；``expired`` 事件用于让看门狗线程中断正在进行的握手/读取。"""
        super().__init__(destination.host, destination.port, timeout=timeout)
        self.destination = destination
        self.expired = Event()
        self.transport_socket = None

    def abort(self):
        """A total deadline also bounds slow header and chunk streams."""
        self.expired.set()
        sock = self.transport_socket
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def connect(self):
        """连接**已校验的字面 IP**，但 TLS SNI 与 Host 头仍用原始域名。

        这样既满足虚拟主机/证书校验，又不会发生第二次 DNS 解析（防重绑定）。
        每一阶段前后都检查 ``expired``，确保整批超时能真正中断慢握手。
        """
        if self.expired.is_set():
            raise TimeoutError()
        sock = socket.create_connection((self.destination.ip, self.destination.port), timeout=self.timeout)
        self.transport_socket = sock
        try:
            if self.destination.tls:
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=self.destination.host, do_handshake_on_connect=False)
                self.transport_socket = sock
                if self.expired.is_set():
                    raise TimeoutError()
                sock.do_handshake()
            if self.expired.is_set():
                raise TimeoutError()
            self.sock = sock
        except Exception:
            sock.close()
            raise


def _reject_json_constant(_value):
    """JSON 里的 NaN/Infinity 属于非标准扩展，解析时直接失败而不是接受为浮点。"""
    raise ValueError("Non-finite JSON numbers are not supported")


def validate_response_tree(value) -> None:
    """限制响应 JSON 的规模与形状：节点数、嵌套深度与键名安全性。

    迭代式遍历（不递归）避免深嵌套触发解释器栈溢出；拒绝 __proto__/constructor
    /prototype 键名，避免把不可信 JSON 直接透传给前端时造成原型污染。
    抛出：节点数 > 150000、深度 > 48、非有限浮点或危险键名时抛 ``ValidationError``。
    """
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > 150000 or depth > 48:
            raise ValidationError("HTTP JSON 响应节点过多或嵌套过深，请缩小查询范围")
        if isinstance(item, dict):
            if any(key in {"__proto__", "constructor", "prototype"} for key in item):
                raise ValidationError("HTTP JSON 响应含不安全的字段名")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif isinstance(item, float) and not math.isfinite(item):
            raise ValidationError("HTTP JSON 不能包含非有限数")


def request_json(config, body, credentials: dict[str, str], *, deadline: float, max_body_bytes: int = MAX_BODY_BYTES,
                 response_redactions: dict[str, str] | None = None, before_request=None) -> tuple[object, dict]:
    """发送一次请求，不继承代理、不跟随重定向、不自动重试。

    完成本地校验后、实际发送前调用 before_request，供调用方持久记录派发状态；
    该回调失败时请求不会发出。
    """

    # 单条请求的全部约束集中在一次调用里：固定目标（先解析并校验地址）、凭据只以
    # ${secret.名称} 展开（共享字节预算）、拒绝跳转与代理继承、不压缩响应、
    # 响应类型/编码/长度必须与所选格式一致、整批 deadline 内看门狗强制中断。
    # 返回 (payload, metadata)：payload 已按凭据脱敏（含调用方额外指定的
    # response_redactions），metadata 含 httpStatus 与 responseBytes 供逐条状态使用。
    # 抛出：地址/头部/请求体不合规抛 ValidationError；超时、非 2xx、长度不符、
    # 解码失败等抛 ToolExecutionError（含 http_status 等附加字段，不含响应原文）。
    request_deadline = min(deadline, time.monotonic() + config.timeout_seconds)
    destination = resolve_destination(config.url, allow_private=config.allow_private, deadline=request_deadline)
    headers = resolve_references(config.headers, credentials, max_bytes=16384)
    headers = {key: value for key, value in headers.items() if key.lower() != "accept"}
    text_response = getattr(config, "response_format", "json") == "text"
    headers = {**headers, "Accept": "text/html, text/plain, application/xhtml+xml" if text_response else "application/json", "Accept-Encoding": "identity"}
    for value in headers.values():
        if len(value) > 8192 or any(ord(c) < 32 or ord(c) > 255 or ord(c) == 127 for c in value):
            raise ValidationError("解析后的请求头含不支持的字符")
    encoded = None
    if config.method in {"POST", "PUT", "PATCH", "DELETE"}:
        resolved = resolve_references(body, credentials, max_bytes=max_body_bytes)
        encoded = json.dumps(resolved, ensure_ascii=False, allow_nan=False).encode("utf-8")
        if len(encoded) > max_body_bytes:
            raise ValidationError("解析凭据后的单条请求体超过本次执行大小上限")
        headers = {key: value for key, value in headers.items() if key.lower() != "content-type"}
        headers["Content-Type"] = "application/json; charset=utf-8"
    remaining = request_deadline - time.monotonic()
    if remaining <= 0:
        raise ToolExecutionError("采集已达到整批超时限制")
    connection = PinnedConnection(destination, timeout=remaining)
    watchdog = Timer(remaining, connection.abort)
    watchdog.daemon = True
    watchdog.start()
    response = None
    # 置于发送尝试之前：连接在写入过程中断开时无法判断远端是否已收到请求，
    # 对 POST 必须按未知副作用处理，不能把错误交给自动重试。
    request_attempted = False
    try:
        # 所有目标、头部及请求体预检已完成；持久派发登记失败时不得继续发包。
        if before_request is not None:
            before_request()
        request_attempted = True
        connection.request(config.method, destination.path, body=encoded, headers=headers)
        response = connection.getresponse()
        if not 200 <= response.status < 300:
            raise ToolExecutionError("HTTP 采集接口返回非成功状态；未跟随跳转且未自动重试", http_status=response.status)
        content_type = (response.getheader("Content-Type") or "").split(";")[0].strip().lower()
        if text_response:
            if not (content_type.startswith("text/") or content_type in {"application/xhtml+xml", "application/xml", "application/json"} or content_type.endswith("+json")):
                raise ValidationError("文本采集收到非文本响应，请检查目标地址或响应格式")
        elif content_type != "application/json" and not content_type.endswith("+json"):
            raise ValidationError("此接口返回网页或文本，请将响应格式改为“网页 / 文本”；JSON 模式要求 application/json")
        if (response.getheader("Content-Encoding") or "identity").lower() != "identity":
            raise ValidationError("采集接口忽略了未压缩响应约定；请配置为 identity 响应")
        length = response.getheader("Content-Length")
        if length and response.getheader("Transfer-Encoding"):
            raise ValidationError("HTTP 响应同时声明长度与分块传输，请修正接口配置")
        if length and (not length.isdigit() or int(length) > MAX_RESPONSE_BYTES):
            raise ValidationError("HTTP 响应超过 4 MB 限制或长度不正确")
        chunks = []
        size = 0
        while True:
            if response.isclosed():
                break
            remaining = request_deadline - time.monotonic()
            if remaining <= 0:
                raise ToolExecutionError("HTTP 响应读取超时")
            if connection.transport_socket:
                connection.transport_socket.settimeout(remaining)
            chunk = response.read1(min(65536, MAX_RESPONSE_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > MAX_RESPONSE_BYTES:
                raise ValidationError("HTTP 响应超过 4 MB，请减少请求字段或分页范围")
        if connection.expired.is_set():
            raise ToolExecutionError("HTTP 连接或响应读取超时；未自动重试")
        if length and size != int(length):
            raise ToolExecutionError("HTTP 响应未完整接收；未将残缺结果作为成功数据")
        try:
            if text_response:
                from email.message import Message
                media = Message()
                media["content-type"] = response.getheader("Content-Type") or ""
                encoding = (media.get_content_charset() or "utf-8-sig").lower()
                if encoding not in {"utf-8", "utf8", "utf-8-sig", "gbk", "gb2312", "gb18030", "big5", "iso-8859-1", "windows-1252", "us-ascii", "ascii"}:
                    raise ValidationError("文本响应使用了不支持的字符编码，请改用 UTF-8")
                payload = {"body": b"".join(chunks).decode(encoding), "contentType": content_type, "url": config.url}
            else:
                payload = json.loads(b"".join(chunks).decode("utf-8-sig"), parse_float=Decimal, parse_constant=_reject_json_constant)
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ValidationError("HTTP 响应无法按所选格式和字符编码解析；未记录响应原文") from exc
        validate_response_tree(payload)
        return redact_values(payload, {**credentials, **(response_redactions or {})}), {"httpStatus": response.status, "responseBytes": size}
    except AppError as exc:
        if request_attempted and config.method == "POST":
            extra = getattr(exc, "extra", None)
            if isinstance(extra, dict):
                extra.setdefault("request_sent", True)
                # 5xx、响应解析/读取异常可能伴随远端已执行；4xx 保留已拒绝语义。
                status = extra.get("http_status")
                if isinstance(exc, ValidationError) or not isinstance(status, int) or status >= 500:
                    extra.setdefault("operation_outcome", "uncertain")
                else:
                    extra.setdefault("operation_outcome", "rejected")
        raise
    except (TimeoutError, socket.timeout) as exc:
        extra = {"request_sent": request_attempted}
        if request_attempted and config.method == "POST":
            extra["operation_outcome"] = "uncertain"
        raise ToolExecutionError("HTTP 连接或响应读取超时；未自动重试", **extra) from exc
    except Exception as exc:
        extra = {"error_type": type(exc).__name__, "request_sent": request_attempted}
        if request_attempted and config.method == "POST":
            extra["operation_outcome"] = "uncertain"
        raise ToolExecutionError("HTTP 连接、证书或响应读取失败", **extra) from exc
    finally:
        watchdog.cancel()
        if response:
            response.close()
        connection.close()
