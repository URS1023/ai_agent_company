"""Workspace-scoped product entry configuration, with current resource checks.

The catalogue is an explicit source-import snapshot. Execution still reads the
source database through the selected workflow's pinned read-values datasets.

（中文补充）职责：质检"产品入口"目录 —— 把产品、以及"产品 → 质检流程"的映射读出来给前端，
让用户从产品卡片进入对应的质检工作流。

位置与依赖：目录内容是**导入快照**，落盘在仓库 ``configurations/quality_catalog.json``
（路径由文件位置反推，``parents[4]``）；本模块只读该文件并按 ``workspaceId`` 过滤，
再逐个用 ``workflows.service.get_workflow`` 复核流程是否仍可用，用
``alerts.card_images.image_map`` 取产品图片。真正执行质检时读的是源数据库，
走所选工作流**固定的读值数据集**，与本目录无关（目录不缓存业务数据）。

对外：``catalogue`` 是唯一入口（REST 由 ``quality.router`` 暴露），需要
``quality.view`` 与 ``workflows.view``。

约束与已知行为：
- 文件不存在 → 返回空目录（正常的"未导入"状态，不报错）；文件存在但内容非法 → 503，
  提示管理员检查目录（不能把坏配置当"没有产品"，否则用户以为数据丢了）。
- 非本工作区的目录 → 直接返回空，且**不输出任何诊断信息**，避免泄露其它工作区的资源引用。
- 流程查不到或无权访问时**静默跳过该条目**（见 ``catalogue``），
  非管理员只能看到"该产品没有流程"，只有管理员能看到不可用计数与原因。
"""
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError as ModelValidationError, model_validator

from app.common.authorization import access_policy, require_permission
from app.errors import AppError, NotFoundError, PermissionErrorEx
from app.modules.analytics.sources import get_source
from app.modules.workflows.service import get_workflow


CATALOG_PATH = Path(__file__).resolve().parents[4] / 'configurations' / 'quality_catalog.json'


class CatalogueConfigurationError(AppError):
    """目录文件存在但不可用（语法/结构/引用非法）时对外返回的错误。

    刻意与"未导入"（返回空目录）区分：503 + ``quality_catalog_invalid`` 明确表示
    "这是配置问题，需要管理员修复"，避免前端把坏配置渲染成"暂无产品"。
    """
    status_code = 503
    code = 'quality_catalog_invalid'


class _Record(BaseModel):
    """目录记录的共同基类：``extra='allow'`` 保留未知字段，``strict=True`` 拒绝隐式类型转换。

    保留未知字段是为了让目录文件可以先行扩展（导入端加字段不会被本服务丢弃）；
    严格模式则避免 ``"123"`` 被当作 123 之类的静默修正，配置错误应尽早暴露。
    """
    model_config = ConfigDict(extra='allow', strict=True)


class _Product(_Record):
    """一个产品：``id`` 供映射与图片使用，``code`` 是对外业务编码。

    ``hasRequirements`` 表示该产品是否已有质检要求；缺要求的产品不应被当成"可直接质检"。
    """
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    code: str
    hasRequirements: bool


class _Entry(_Record):
    """"产品 → 流程"的一条映射。

    ``workflowId`` 唯一（``_Catalogue`` 会校验），指向真实工作流，由 ``catalogue``
    实时复核其存在性与权限；``category`` 为 incoming/process/finished（来料/过程/成品）。
    ``productId`` 允许为空（表示尚未绑定产品）。
    """
    workflowId: str = Field(min_length=1)
    productId: str | None
    category: Literal['incoming', 'process', 'finished']


class _Pending(_Record):
    """待处理项：模板尚未转成流程时的占位记录（仅管理员诊断用）。

    ``reason`` 必填且非空，因为这类条目本身就是"为什么还没配好"的说明。
    """
    templateName: str
    reason: str = Field(min_length=1)
    formCount: int = Field(ge=0)


class _Catalogue(_Record):
    """目录文件根结构：工作区标识、来源数据源、快照时间、产品、映射与待办。

    ``snapshotAt`` 是导入时刻（不是实时数据时间），前端应据此说明"数据快照"语义。
    """
    workspaceId: str = Field(min_length=1)
    datasourceId: str = Field(min_length=1)
    sourceLabel: str
    snapshotAt: str
    products: list[_Product]
    entries: list[_Entry]
    pendingEntries: list[_Pending] = Field(default_factory=list)

    @model_validator(mode='after')
    def check_references(self):
        """校验标识唯一性与引用完整性：产品 id 不重复、流程 id 不重复、映射的产品必须存在。

        任一不满足即视为配置错误（抛 ``ValueError``，最终转成 503），
        而不是"忽略坏条目"——重复标识会让映射产生歧义。
        """
        product_ids = {row.id for row in self.products}
        if len(product_ids) != len(self.products) or len({row.workflowId for row in self.entries}) != len(self.entries):
            raise ValueError('Duplicate catalogue identifiers')
        if any(row.productId is not None and row.productId not in product_ids for row in self.entries):
            raise ValueError('Unknown catalogue product')
        return self


def catalogue(ctx):
    """读取本工作区的产品目录视图，返回 ``{sourceLabel, snapshotAt, products, entries[, issues, pendingEntries]}``。

    权限：``quality.view`` + ``workflows.view``（目录会暴露流程名称与状态，故需要流程查看权）。

    返回值含义：
    - ``products``：产品（附加 ``image`` = 名片图文件标识与版本，无图为 ``None``）；
    - ``entries``：**已可用**的"产品 → 流程"映射，附加 ``workflowName`` / ``status`` /
      ``publishedRevision``（均为读取时刻的实时值，不是快照值）；
    - ``issues`` / ``pendingEntries``：**仅管理员**才会出现，用于诊断目录问题。

    已知边界：
    - 目录文件缺失 → 返回空视图（未导入，不报错）；
    - 目录不属于本工作区 → 返回空视图且不带诊断（不泄露他工作区信息）；
    - 目录内容非法 → 抛 ``CatalogueConfigurationError``（503）；
    - 映射里的流程被删除或超出当前账号权限 → **静默 ``continue``**：该条目不进入 ``entries``，
      仅计入管理员可见的 ``unavailable`` 计数。因此非管理员无法区分"流程不存在"与"自己无权"，
      这是刻意的降级策略（避免用目录当权限探测器），但也意味着**条目消失不等于流程被删**，
      排查时应以管理员视角的 ``issues`` 为准。
    - 绑定数据源也会校验可见性（``get_source``），数据源失权时整体报错而非静默跳过。
    """
    require_permission(ctx, 'quality.view')
    require_permission(ctx, 'workflows.view')
    empty = {'sourceLabel': '', 'snapshotAt': '', 'products': [], 'entries': []}
    if not CATALOG_PATH.exists():
        return empty
    try:
        config = json.loads(CATALOG_PATH.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('Invalid catalogue root')
        # Do not inspect another workspace's resource references or diagnostics.
        if config.get('workspaceId') and config['workspaceId'] != ctx.workspace_id:
            return empty
        config = _Catalogue.model_validate(config).model_dump()
    except (OSError, UnicodeError, ValueError, ModelValidationError) as error:
        raise CatalogueConfigurationError('产品目录配置无法读取，请联系管理员检查目录后重试。') from error
    if config.get('workspaceId') != ctx.workspace_id:
        return empty
    get_source(ctx, config['datasourceId'])
    entries = []
    unavailable = 0
    for entry in config['entries']:
        try:
            row = get_workflow(ctx, entry['workflowId'])
        except (NotFoundError, PermissionErrorEx):
            # 静默跳过：不报错、不暴露原因，只累计给管理员看的计数。
            unavailable += 1
            continue
        entries.append({**entry, 'workflowName': row.name, 'status': row.status,
                        'publishedRevision': row.published_revision})
    from app.modules.alerts.card_images import image_map
    photos = image_map(ctx, 'product', [product['id'] for product in config['products']])
    diagnostics = {}
    # 诊断信息只给管理员：普通用户看到"3 个流程不可用"会变成权限探测手段。
    if access_policy(ctx).administrator:
        issues = []
        if unavailable:
            issues.append({'code': 'workflow_unavailable', 'count': unavailable,
                           'message': f'{unavailable} 个产品流程暂不可用，请检查流程是否已导入当前工作区及其授权。'})
        diagnostics = {'issues': issues, 'pendingEntries': config['pendingEntries']}
    return {**empty, 'sourceLabel': config['sourceLabel'], 'snapshotAt': config['snapshotAt'],
            'products': [{**product, 'image': photos.get(product['id'])} for product in config['products']],
            'entries': entries, **diagnostics}
