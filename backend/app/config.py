# -*- coding: utf-8 -*-
"""Application configuration.

Load order: environment variables > .env.local at project root > backend/.env

职责：全进程唯一的运行时配置入口。按分组声明数据库、Redis、Elasticsearch、对象存储、
模型供应商（Agnes / 硅基流动）、视觉解析、PPT 资源库、安全会话、运行时限额与 CORS 等
开关，并在导入阶段完成 dotenv 文件加载。

系统位置：几乎所有 backend/app 模块都会读取 `settings`，因此本模块必须最先加载，
且导入期只做文件读取与环境变量解析，不得发起网络或数据库调用（保持可导入即安全）。

约束：环境变量优先级最高，已存在的环境变量不会被 .env 文件覆盖；`settings` 为模块级
单例，进程内共享，运行期不提供热重载。危险默认值（主密钥、初始管理员口令）见下方标注。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List

from sqlalchemy.engine import URL

from app.branding import PRODUCT_NAME


def _load_dotenv(path: Path) -> None:
    """把单个 dotenv 文件写入进程环境变量（不存在的文件直接跳过）。

    参数：path 为 .env 文件路径。
    返回：无；副作用是修改 os.environ。
    约束：已存在的环境变量优先，不会被文件值覆盖；文件缺失或格式不合法时静默忽略，
    保证缺省部署下也能启动。仅供本模块导入时调用，不要在运行期反复调用。
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not os.environ.get(key):
            os.environ[key] = value


PROJECT_ROOT = Path(__file__).resolve().parents[2]  # project root
_locate = [
    PROJECT_ROOT / ".env.local",
    Path(__file__).resolve().parent / ".env",
    PROJECT_ROOT / "backend" / ".env",
]
for _p in _locate:
    _load_dotenv(_p)


def _get(key: str, default: str = "") -> str:
    """读取字符串环境变量，缺失时返回 default。

    约束：只做字符串读取与默认值兜底，不做类型转换；整型/布尔开关由调用处显式转换，
    因此新增配置项若使用默认空串，需自行确认下游能容忍空值。
    """
    return os.environ.get(key, default)


class Settings:
    """Central runtime settings.

    代表进程级配置快照：类属性在类定义（即首次导入本模块）时求值，之后不再变化。
    生命周期与进程绑定，没有实例级状态，也不支持运行时重新加载——需要改配置必须
    重启服务。关键不变式：所有字段都是"环境变量 + 安全默认值"的组合，缺省值必须让
    本地开发可启动，但不能让生产环境在未显式配置时获得可用凭据（见主密钥与管理员口令）。
    """

    # --- 应用与启动 ---
    APP_ENV: str = _get("APP_ENV", "local")
    APP_NAME: str = PRODUCT_NAME
    DEBUG: bool = _get("DEBUG", "0") == "1"
    # 启动时执行版本化建表/补齐；生产可置 0 交给独立迁移流程。
    AUTO_MIGRATE: bool = _get("AUTO_MIGRATE", "1") == "1"

    # --- Database (MySQL) ---
    # 平台库承载业务元数据；DEMO_DB 供演示/样例数据源使用，两者不要指向同一库。
    MYSQL_HOST: str = _get("MYSQL_HOST", "127.0.0.1")
    MYSQL_PORT: str = _get("MYSQL_PORT", "3306")
    MYSQL_USER: str = _get("MYSQL_USER", "root")
    MYSQL_PASSWORD: str = _get("MYSQL_PASSWORD", "")
    PLATFORM_DB: str = _get("PLATFORM_DB", "company_agent")
    DEMO_DB: str = _get("DEMO_DB", "demo_finance")
    MIGRATION_SOURCE_SCHEMAS: tuple[str, ...] = tuple(
        item.strip() for item in _get(
            "MIGRATION_SOURCE_SCHEMAS", "qknow_dev,lynx_ai_import_20260901",
        ).split(",") if item.strip()
    )
    MIGRATION_MAX_ROWS: int = int(_get("MIGRATION_MAX_ROWS", "100000"))

    @property
    def DATABASE_URL(self) -> str:
        """Resolve an explicit isolated database or safely encode MySQL credentials.

        SQLAlchemy 连接串：显式 DATABASE_URL 优先（用于隔离测试库），否则按 MYSQL_* 拼装
        并强制 utf8mb4（中文字段与 emoji 都依赖该字符集）。返回：可直接交给 create_engine
        的字符串。注意返回内容含明文口令，禁止写日志或回显给前端。
        """
        override = _get("DATABASE_URL")
        if override:
            return override
        return URL.create(
            "mysql+pymysql", username=self.MYSQL_USER, password=self.MYSQL_PASSWORD,
            host=self.MYSQL_HOST, port=int(self.MYSQL_PORT), database=self.PLATFORM_DB,
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)

    # --- Redis ---
    REDIS_URL: str = _get("REDIS_URL", "redis://127.0.0.1:6379/0")

    # --- Elasticsearch ---
    ES_URL: str = _get("ES_URL", "http://127.0.0.1:9200")
    ES_KNOWLEDGE_INDEX: str = _get("ES_KNOWLEDGE_INDEX", "company-agent-knowledge-v1-bge-m3-1024")

    # --- Object storage ---
    STORAGE_ROOT: str = _get("LOCAL_STORAGE_ROOT", str(PROJECT_ROOT / "storage"))
    STORAGE_PROVIDER: str = _get("STORAGE_PROVIDER", "local")

    # 运行日志默认放在存储目录下；每个进程单独轮转，避免多个监听服务争用文件。
    LOG_DIR: str = _get("LOG_DIR", "")
    LOG_LEVEL: str = _get("LOG_LEVEL", "INFO")
    LOG_MAX_BYTES: int = int(_get("LOG_MAX_BYTES", "10485760"))
    LOG_BACKUP_COUNT: int = int(_get("LOG_BACKUP_COUNT", "5"))

    # --- Secret master key (AES-GCM) ---
    # 仅限本地开发，部署必须覆盖：app.crypto 的所有密文都由该值派生的密钥加密，
    # 沿用默认值会让"加密存储的凭据"等同于明文；轮换主密钥后历史密文需要重新写入。
    SECRET_MASTER_KEY: str = _get("SECRET_MASTER_KEY", "dev-only-master-key-change-me-000")

    # --- Model providers (OpenAI compatible) ---
    # 供应商 base_url 统一去掉尾部斜杠，避免下游拼接出双斜杠路径。
    AGNES_BASE_URL: str = _get("AGNES_BASE_URL", "").rstrip("/")
    AGNES_API_KEY: str = _get("AGNES_API_KEY", "")
    AGNES_CHAT_MODEL: str = _get("AGNES_CHAT_MODEL", "agnes-2.5-flash")

    SILICONFLOW_BASE_URL: str = _get("SILICONFLOW_BASE_URL", "").rstrip("/")
    SILICONFLOW_API_KEY: str = _get("SILICONFLOW_API_KEY", "")
    SILICONFLOW_EMBED_MODEL: str = _get("SILICONFLOW_EMBED_MODEL", "BAAI/bge-m3")
    SILICONFLOW_EMBED_DIM: int = int(_get("SILICONFLOW_EMBED_DIM", "1024"))
    SILICONFLOW_VISION_MODEL: str = _get("SILICONFLOW_VISION_MODEL", "Qwen/Qwen3.5-4B")
    SILICONFLOW_VISION_API_KEY: str = _get(
        "SILICONFLOW_VISION_API_KEY", SILICONFLOW_API_KEY
    )
    # 视觉解析相关限额：限制单文档图片数、长边像素、最小边长与输出 token，
    # 目的是把多模态入库的耗时与费用压在可预期范围内。
    VISION_ENABLED: bool = _get("VISION_ENABLED", "1") == "1"
    VISION_DETAIL: str = _get("VISION_DETAIL", "high")
    VISION_MAX_IMAGES_PER_DOCUMENT: int = int(
        _get("VISION_MAX_IMAGES_PER_DOCUMENT", "16")
    )
    VISION_MAX_IMAGE_EDGE: int = int(_get("VISION_MAX_IMAGE_EDGE", "2200"))
    VISION_MIN_IMAGE_SIDE: int = int(_get("VISION_MIN_IMAGE_SIDE", "72"))
    VISION_MAX_OUTPUT_TOKENS: int = int(_get("VISION_MAX_OUTPUT_TOKENS", "1400"))
    VISION_PDF_DPI: int = int(_get("VISION_PDF_DPI", "144"))

    # --- Optional presentation resource vaults ---
    # 这些目录属于可选资源（本地模板/预览渲染），缺失时相关能力降级而不是启动失败。
    PRESENTATION_PPT_MASTER_ROOT: str = _get(
        "PRESENTATION_PPT_MASTER_ROOT",
        str(
            PROJECT_ROOT / "需求文档" / "细分需求" / "ppt生成"
            / "ppt-master" / "skills" / "ppt-master"
        ),
    )
    PRESENTATION_AIPPT_ROOT: str = _get(
        "PRESENTATION_AIPPT_ROOT",
        str(PROJECT_ROOT / "需求文档" / "细分需求" / "ppt生成" / "AiPPT"),
    )
    PRESENTATION_EXTERNAL_SERVICES_ENABLED: bool = (
        _get("PRESENTATION_EXTERNAL_SERVICES_ENABLED", "0") == "1"
    )
    PRESENTATION_PREVIEW_NODE: str = _get("PRESENTATION_PREVIEW_NODE", "")
    PRESENTATION_PREVIEW_NODE_MODULES: str = _get(
        "PRESENTATION_PREVIEW_NODE_MODULES", ""
    )

    # --- Security ---
    # 会话绝对有效期与空闲有效期分离：绝对 TTL 到期必须重新登录，空闲 TTL 到期则提前失效。
    SESSION_TTL_SECONDS: int = int(_get("SESSION_TTL_SECONDS", "604800"))  # 7d
    IDLE_TTL_SECONDS: int = int(_get("IDLE_TTL_SECONDS", "2592000"))  # 30d
    # 集成令牌强制夹在 [300, 86400] 秒：既避免签发瞬时过期，也避免长期有效的机器凭据。
    INTEGRATION_TOKEN_TTL_SECONDS: int = min(86400, max(300, int(_get("INTEGRATION_TOKEN_TTL_SECONDS", "3600"))))

    # --- Runtime ---
    # 运行时限额：工具循环轮数、单次 run 超时、对话任务超时共同构成智能体执行预算。
    MAX_TOOL_LOOP_ITERATIONS: int = int(_get("MAX_TOOL_LOOP_ITERATIONS", "20"))
    RUN_TIMEOUT_SECONDS: int = int(_get("RUN_TIMEOUT_SECONDS", "600"))
    # 0 disables the total chat deadline; database/sandbox budgets are independent.
    CHAT_TASK_TIMEOUT_SECONDS: int = max(0, int(_get("CHAT_TASK_TIMEOUT_SECONDS", "0")))
    # 对话引擎在建单时固定；试点失败不在同一任务中切回旧引擎。
    # 默认引擎已直接替换为 DSH（harness）；openwebui 仅作显式回退开关保留。
    CHAT_ENGINE: str = _get("CHAT_ENGINE", "harness").strip().lower()
    HARNESS_NODE: str = _get("HARNESS_NODE", "node")
    HARNESS_LAUNCHER: str = _get("HARNESS_LAUNCHER", str(PROJECT_ROOT / "services" / "deepseek_harness" / "launch.mjs"))
    HARNESS_UPSTREAM_ROOT: str = _get("HARNESS_UPSTREAM_ROOT", str(PROJECT_ROOT / ".implementation" / "harness_migration" / "upstream"))
    HARNESS_HOME_ROOT: str = _get("HARNESS_HOME_ROOT", str(PROJECT_ROOT / "storage" / "harness"))
    HARNESS_MAX_PROCESSES: int = min(2, max(1, int(_get("HARNESS_MAX_PROCESSES", "2"))))
    HARNESS_START_TIMEOUT_SECONDS: int = max(5, int(_get("HARNESS_START_TIMEOUT_SECONDS", "60")))
    HARNESS_CANCEL_TIMEOUT_SECONDS: int = max(5, int(_get("HARNESS_CANCEL_TIMEOUT_SECONDS", "30")))
    # 旧模型配置没有容量时采用保守值，可按实际供应商容量显式配置。
    # 输出预算必须被夹在上限 128k 与"窗口减去 1024 预留"之间，避免未声明预算的
    # profile 请求超大输出导致超预算或请求被供应商拒绝（见 RuntimeOptions 同款校验）。
    HARNESS_CONTEXT_WINDOW: int = max(8192, int(_get("HARNESS_CONTEXT_WINDOW", "32768")))
    HARNESS_MAX_OUTPUT_TOKENS: int = min(
        131072,
        max(512, int(_get("HARNESS_MAX_OUTPUT_TOKENS", "4096"))),
        (max(8192, int(_get("HARNESS_CONTEXT_WINDOW", "32768"))) - 1024),
    )
    HARNESS_EXTRA_TOOLS: tuple[str, ...] = tuple(name.strip() for name in _get("HARNESS_EXTRA_TOOLS").split(",") if name.strip())
    # OpenCode /zen/go 头注入代理：opencode.ai 要求每个请求带 x-opencode-session，
    # DSH 原生进程不带该头（平台 OpenAI 网关已带）；按需启动本地代理注入并流式转发。
    OPCODE_PROXY_ENABLED: bool = _get("OPCODE_PROXY_ENABLED", "1") == "1"
    OPCODE_PROXY_HOST: str = _get("OPCODE_PROXY_HOST", "127.0.0.1")
    OPCODE_PROXY_PORT: int = int(_get("OPCODE_PROXY_PORT", "8901"))
    OPCODE_PROXY_TARGET: str = _get("OPCODE_PROXY_TARGET", "https://opencode.ai/zen/go/v1").rstrip("/")
    # 自然语言→SQL 单次模型调用超时上限（秒）。正常 1-2s；慢端点排队时快速失败，
    # 避免单次调用拖 5-9 分钟拖垮整轮任务。
    ANALYTICS_NL_REQUEST_TIMEOUT_SECONDS: int = max(
        30, int(_get("ANALYTICS_NL_REQUEST_TIMEOUT_SECONDS", "150")))
    # NL→SQL 提示里的 schema 压缩（仅影响提示，授权校验仍用完整快照）。
    ANALYTICS_NL_MAX_TABLES: int = max(3, int(_get("ANALYTICS_NL_MAX_TABLES", "8")))
    ANALYTICS_NL_MAX_COLUMNS_PER_TABLE: int = max(5, int(_get("ANALYTICS_NL_MAX_COLUMNS_PER_TABLE", "40")))
    SCHEDULER_ENABLED: bool = _get("SCHEDULER_ENABLED", "1") == "1"
    SCHEDULER_POLL_SECONDS: int = max(5, int(_get("SCHEDULER_POLL_SECONDS", "30")))
    AGENT_CONTEXT_MAX_CHARS: int = int(_get("AGENT_CONTEXT_MAX_CHARS", "260000"))
    AGENT_TOOL_RESULT_MAX_CHARS: int = int(
        _get("AGENT_TOOL_RESULT_MAX_CHARS", "50000")
    )
    READ_FILE_DEFAULT_MAX_CHARS: int = int(
        _get("READ_FILE_DEFAULT_MAX_CHARS", "30000")
    )
    READ_FILE_MAX_CHARS: int = int(_get("READ_FILE_MAX_CHARS", "80000"))
    SANDBOX_TIMEOUT_SECONDS: int = int(_get("SANDBOX_TIMEOUT_SECONDS", "120"))
    SANDBOX_MEMORY_MB: int = int(_get("SANDBOX_MEMORY_MB", "1024"))

    # --- Auth bootstrap ---
    # 仅限本地开发，部署必须覆盖：BOOTSTRAP_ADMIN_PASSWORD 是初始管理员账号的口令，
    # 沿用默认值时任何能访问登录页的人都能拿到后台权限；首个管理员建好后应同时移除该值。
    BOOTSTRAP_ADMIN_PASSWORD: str = _get("BOOTSTRAP_ADMIN_PASSWORD", "admin123")
    BOOTSTRAP_LYNX_REPORTS: bool = _get("BOOTSTRAP_LYNX_REPORTS", "1") == "1"

    # --- CORS ---
    # 默认 "*" 只适合本地开发；部署需显式列出前端来源，否则浏览器侧的凭据请求会过度放开。
    CORS_ORIGINS: List[str] = _get("CORS_ORIGINS", "*").split(",")


settings = Settings()
