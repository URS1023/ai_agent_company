"""Atomic XLSX user import/export compatible with the normal administration page.

职责：用户批量导入（模板下载、校验预览、确认导入）与导出，规则与单条创建/修改接口完全一致。

位置与依赖：对外路由前缀 /api/v1/system（router.py）—— 用户导入相关接口（模板、校验、提交）
与导出接口；写库逻辑复用 user_service 的 prepare/apply/create 函数，保证单条与批量不会出现
两套业务规则；事务约束复用 service_helpers（lock_workspace、乐观锁与冲突翻译）。

关键约束：
- 两阶段提交：先 validate_import 全量校验并返回文件 SHA-256，再 import_users 用同一 checksum
  核对“提交的就是校验过的文件”，防止校验后换文件；任一行错误则整批不写入（单一事务）。
- 导入不允许改密码：更新既有用户必须留空密码列，密码只能在单用户操作中重置。
- 跨工作区保护：登录名已属于其他工作区的账号不能通过导入自动建立关系。
- 导出上限 10000 条（100 页 × 100），并要求 system.users.export 权限；导出文件不含密码。
- 公式注入防护：所有写入单元格的文本都经 _safe_excel 转义。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from zipfile import BadZipFile

from openpyxl import Workbook, load_workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from app.common.audit import audit
from app.common.authorization import require_permission
from app.common.deps import AuthContext
from app.errors import AppError, ConflictError, ValidationError
from app.ids import new_id
from app.models import AuthIdentity, User, WorkspaceMember
from app.modules.system.models import Department, Position, Role, UserRole
from app.modules.system.schemas import UserCreate, UserUpdate
from app.modules.system.service_helpers import lock_workspace
from app.modules.system.user_service import (
    apply_user_update,
    authorization_snapshot,
    create_user_records,
    list_users,
    prepare_user_create,
    prepare_user_update,
    user_out,
)


MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_ROWS = 1000
MAX_ISSUES = 200
# 导入模板的列顺序与 HEADERS 强绑定：读取时按列名定位而不是按位置，因此可以调整顺序，
# 但不能改名（改名会被当作“缺少/不支持的表头”拒绝）。HEADER_HINTS 同时用于单元格批注与说明页。
HEADERS = [
    "登录账号", "显示姓名", "初始临时密码", "账号状态", "部门编码",
    "岗位编码", "角色编码", "员工编号", "联系邮箱", "联系电话",
]
HEADER_HINTS = {
    "登录账号": "必填；3–80 位字母、数字、下划线、点或短横线，全平台唯一。",
    "显示姓名": "必填；1–160 个字符。",
    "初始临时密码": "新增用户必填，至少 10 位且包含字母和数字；更新用户必须留空。",
    "账号状态": "启用或停用；留空按启用处理。",
    "部门编码": "可空；填写系统管理中的部门编码。",
    "岗位编码": "可空；多个编码使用中文或英文逗号分隔。",
    "角色编码": "可空；多个编码使用中文或英文逗号分隔，空值表示不分配角色。",
    "员工编号": "可空；最多 80 个字符。",
    "联系邮箱": "可空；最多 254 个字符。",
    "联系电话": "可空；最多 40 个字符。",
}
STATUS_VALUES = {"": "active", "启用": "active", "active": "active", "停用": "disabled", "disabled": "disabled"}
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,80}$")
# 多值列（岗位编码、角色编码）的分隔符：中英文逗号、分号与换行都接受，
# 因为管理员常从 Excel 其它表格直接粘贴，分隔符不统一。
SPLIT_PATTERN = re.compile(r"[,，;；\n]+")


@dataclass
class ImportRow:
    """一行导入数据的解析与处理状态（校验阶段就地填充，提交阶段直接复用）。

    action 由 validate_import 决定：create/update/unchanged/invalid；
    payload 与 roles/prepared_update 是准备阶段的产物，供 import_users 无重复校验地落库；
    注意 prepared_update 保存的是 user_service.prepare_user_update 的返回值（含版本），
    因此校验与提交必须使用同一行对象，不能序列化后再用。
    """
    row: int
    username: str
    display_name: str
    password: str
    status: str
    department_code: str
    position_codes: list[str]
    role_codes: list[str]
    employee_code: str
    email: str
    phone: str
    action: str = "invalid"
    payload: UserCreate | UserUpdate | None = None
    roles: list[Role] = field(default_factory=list)
    user_id: str = ""
    prepared_update: object | None = None


def _text(value) -> str:
    """统一把单元格值转成去掉首尾空白的字符串。

    兼容性约束：Excel 会把“工号/电话”这类纯数字读成 float，此处把整数值还原为不带 .0 的字符串，
    否则导出的编码会多出小数位、再导入时匹配不上。
    """
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _codes(value: str) -> list[str]:
    """把多值单元格拆成去重排序的编码列表（空串产出空列表）。"""
    return sorted({item.strip() for item in SPLIT_PATTERN.split(value) if item.strip()})


def _issue(issues: list[dict], row: int, field_name: str, message: str) -> None:
    """记录一条校验问题，条数封顶 MAX_ISSUES（避免超大文件把响应体撑爆）。

    field_name 用中文列名；整行级错误固定用“整行”，前端据此决定展示位置。
    """
    if len(issues) < MAX_ISSUES:
        issues.append({"row": max(2, row), "field": field_name, "message": message})


def _load_rows(data: bytes, file_name: str) -> tuple[list[ImportRow], list[dict]]:
    """解析 XLSX 字节流：校验文件与表头，逐行读取并做“单元格级”格式校验。

    参数：data 上传内容；file_name 原始文件名（只用于扩展名与错误提示）。
    返回：(解析出的行, 问题列表)；行级解析问题不抛异常，而是累积到 issues 里供预览展示。
    异常：ValidationError —— 扩展名不支持、文件为空、超过 5MB、不是可读的 xlsx、表头缺失/多列/重复。
    安全与兼容：禁止公式单元格（data_type == "f"），避免把外部工作簿的公式当数据带入；
    read_only=False 是为了能判断公式类型；读取时按列名定位，允许模板列顺序调整。
    """
    issues: list[dict] = []
    if not file_name.lower().endswith(".xlsx"):
        raise ValidationError("仅支持 .xlsx 用户文件，请先下载系统模板")
    if not data:
        raise ValidationError("上传文件为空")
    if len(data) > MAX_FILE_BYTES:
        raise ValidationError("用户导入文件不能超过 5 MB")
    try:
        workbook = load_workbook(BytesIO(data), read_only=False, data_only=False)
    except (BadZipFile, OSError, ValueError, KeyError) as exc:
        raise ValidationError("无法读取 Excel 文件，请确认文件未损坏且格式为 .xlsx") from exc
    sheet = workbook["用户导入"] if "用户导入" in workbook.sheetnames else workbook.active
    header_cells = list(sheet.iter_rows(min_row=1, max_row=1))[0] if sheet.max_row else []
    headers = [_text(cell.value) for cell in header_cells]
    if len(headers) != len(set(headers)):
        raise ValidationError("表头包含重复列，请重新下载模板")
    missing = [name for name in HEADERS if name not in headers]
    unknown = [name for name in headers if name and name not in HEADERS]
    if missing or unknown:
        parts = []
        if missing:
            parts.append("缺少：" + "、".join(missing))
        if unknown:
            parts.append("不支持：" + "、".join(unknown))
        raise ValidationError("用户导入表头不正确（" + "；".join(parts) + "）")
    indexes = {name: headers.index(name) + 1 for name in HEADERS}
    rows: list[ImportRow] = []
    seen: dict[str, int] = {}
    for row_number in range(2, sheet.max_row + 1):
        cells = {name: sheet.cell(row_number, column) for name, column in indexes.items()}
        if not any(_text(cell.value) for cell in cells.values()):
            continue
        if len(rows) >= MAX_ROWS:
            _issue(issues, row_number, "整行", f"单次最多导入 {MAX_ROWS} 行，请拆分文件")
            break
        formula_columns = [name for name, cell in cells.items() if cell.data_type == "f"]
        if formula_columns:
            for name in formula_columns:
                _issue(issues, row_number, name, "不允许使用 Excel 公式，请填写固定值")
        values = {name: _text(cell.value) for name, cell in cells.items()}
        username = values["登录账号"].lower()
        status_key = values["账号状态"].lower() if values["账号状态"].isascii() else values["账号状态"]
        row = ImportRow(
            row=row_number,
            username=username,
            display_name=values["显示姓名"],
            password=values["初始临时密码"],
            status=STATUS_VALUES.get(status_key, ""),
            department_code=values["部门编码"],
            position_codes=_codes(values["岗位编码"]),
            role_codes=_codes(values["角色编码"]),
            employee_code=values["员工编号"],
            email=values["联系邮箱"],
            phone=values["联系电话"],
        )
        rows.append(row)
        if not USERNAME_PATTERN.fullmatch(row.username):
            _issue(issues, row_number, "登录账号", "必须为 3–80 位字母、数字、下划线、点或短横线")
        if not row.display_name:
            _issue(issues, row_number, "显示姓名", "不能为空")
        if not row.status:
            _issue(issues, row_number, "账号状态", "仅支持“启用”或“停用”")
        if len(row.employee_code) > 80:
            _issue(issues, row_number, "员工编号", "不能超过 80 个字符")
        if len(row.email) > 254 or (row.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", row.email)):
            _issue(issues, row_number, "联系邮箱", "请填写有效邮箱，或留空")
        if len(row.phone) > 40:
            _issue(issues, row_number, "联系电话", "不能超过 40 个字符")
        if username in seen:
            _issue(issues, row_number, "登录账号", f"与第 {seen[username]} 行重复")
        else:
            seen[username] = row_number
    return rows, issues


def _row_has_issue(issues: list[dict], row_number: int) -> bool:
    """判断某行是否已存在问题（用于把整行标记为 invalid，不再进入准备阶段）。"""
    return any(item["row"] == row_number for item in issues)


def _model_message(exc: Exception) -> str:
    """把业务/校验异常转成可展示的中文单句（业务异常优先，其次是 pydantic 的首条错误）。"""
    if isinstance(exc, AppError):
        return exc.message
    if isinstance(exc, PydanticValidationError):
        first = exc.errors()[0]
        return str(first.get("msg") or "字段格式不正确")
    return "字段校验失败"


def _changed_update(ctx: AuthContext, row: ImportRow, user_id: str, role_ids: list[str], department_id: str | None, position_ids: list[str]) -> dict:
    """比较“导入行期望值”与“现有资料”，只返回真正变化的字段（附加 version）。

    返回的字典可直接构造 UserUpdate；当没有任何字段变化时只含 version，
    调用方据此判定为 unchanged，避免产生无意义的审计与乐观锁自增。
    列表字段比较前会排序，保证“顺序不同”不被当成变更。
    """
    current = user_out(ctx, user_id)
    desired = {
        "display_name": row.display_name,
        "status": row.status,
        "role_ids": sorted(role_ids),
        "department_id": department_id,
        "position_ids": sorted(position_ids),
        "employee_code": row.employee_code,
        "email": row.email,
        "phone": row.phone,
    }
    changed = {
        key: value for key, value in desired.items()
        if (sorted(current[key]) if isinstance(current[key], list) else current[key]) != value
    }
    return {"version": current["version"], **changed}


def validate_import(ctx: AuthContext, data: bytes, file_name: str, update_existing: bool) -> tuple[dict, list[ImportRow]]:
    """Validate every row without writes and return a reusable in-memory plan.

    用途：导入预览接口（校验阶段）。只读校验，全部通过后才允许提交，返回的行对象可直接交给
    import_users 落库（prepare_user_* 的产物不能再序列化传递）。
    参数：ctx；data 文件内容；file_name 文件名；update_existing 是否允许更新已存在成员
    （false 时已存在账号会被标记为问题）。
    返回：(预览字典, 行列表)；预览含 valid、checksum、各类计数、issues（最多 200）与前 50 行脱敏预览。
    异常：PermissionErrorEx —— 需要同时具备 system.users.import、system.users.manage、
    system.users.assign_roles（导入会建账号并分配角色）。
    权限与数据范围：逐行调用 prepare_user_create/prepare_user_update，因此部门范围、角色委派边界、
    最后一个管理员等约束与单条接口完全一致。
    副作用：校验阶段本身不写库，但 prepare_* 会读取数据库（查询最优化为按 username 批量取
    AuthIdentity/WorkspaceMember，避免逐行查询）。
    """
    require_permission(ctx, "system.users.import")
    require_permission(ctx, "system.users.manage")
    require_permission(ctx, "system.users.assign_roles")
    rows, issues = _load_rows(data, file_name)
    if not rows:
        _issue(issues, 2, "整行", "文件中没有可导入的用户数据")
    roles = {item.code: item for item in ctx.db.scalars(select(Role).where(Role.workspace_id == ctx.workspace_id))}
    departments = {item.code: item for item in ctx.db.scalars(select(Department).where(Department.workspace_id == ctx.workspace_id))}
    positions = {item.code: item for item in ctx.db.scalars(select(Position).where(Position.workspace_id == ctx.workspace_id))}
    usernames = [row.username for row in rows]
    identities = {
        item.username_normalized: item for item in ctx.db.scalars(select(AuthIdentity).where(
            AuthIdentity.provider == "local", AuthIdentity.username_normalized.in_(usernames),
        ))
    } if usernames else {}
    memberships = {
        item.user_id: item for item in ctx.db.scalars(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ctx.workspace_id,
            WorkspaceMember.user_id.in_([identity.user_id for identity in identities.values()]),
        ))
    } if identities else {}
    for row in rows:
        row_issues_before = len(issues)
        department = departments.get(row.department_code) if row.department_code else None
        if row.department_code and department is None:
            _issue(issues, row.row, "部门编码", "当前工作区不存在该部门编码")
        elif department is not None and not department.enabled:
            _issue(issues, row.row, "部门编码", "不能选择已停用的部门")
        role_rows = [roles.get(code) for code in row.role_codes]
        for code, role in zip(row.role_codes, role_rows):
            if role is None:
                _issue(issues, row.row, "角色编码", f"不存在角色 {code}")
            elif not role.enabled:
                _issue(issues, row.row, "角色编码", f"角色 {code} 已停用")
        position_rows = [positions.get(code) for code in row.position_codes]
        for code, position in zip(row.position_codes, position_rows):
            if position is None:
                _issue(issues, row.row, "岗位编码", f"不存在岗位 {code}")
            elif not position.enabled:
                _issue(issues, row.row, "岗位编码", f"岗位 {code} 已停用")
        identity = identities.get(row.username)
        if identity and identity.user_id not in memberships:
            _issue(issues, row.row, "登录账号", "该账号已属于其他工作区，不能通过批量导入自动建立跨工作区关系")
        elif identity and not update_existing:
            _issue(issues, row.row, "登录账号", "当前工作区已存在；勾选“更新已有用户”后再校验")
        elif identity and row.password:
            _issue(issues, row.row, "初始临时密码", "更新已有用户时必须留空；密码只能在单用户操作中重置")
        elif not identity and not row.password:
            _issue(issues, row.row, "初始临时密码", "新增用户必须填写临时密码")
        if len(issues) > row_issues_before or _row_has_issue(issues, row.row):
            row.action = "invalid"
            continue
        role_ids = [item.id for item in role_rows if item is not None]
        position_ids = [item.id for item in position_rows if item is not None]
        department_id = department.id if department else None
        try:
            if identity:
                row.user_id = identity.user_id
                changes = _changed_update(ctx, row, row.user_id, role_ids, department_id, position_ids)
                if len(changes) == 1:
                    row.action = "unchanged"
                    continue
                row.payload = UserUpdate(**changes)
                row.prepared_update = prepare_user_update(ctx, row.user_id, row.payload)
                row.action = "update"
            else:
                row.payload = UserCreate(
                    username=row.username,
                    display_name=row.display_name,
                    password=row.password,
                    role_ids=role_ids,
                    department_id=department_id,
                    position_ids=position_ids,
                    employee_code=row.employee_code,
                    email=row.email,
                    phone=row.phone,
                )
                row.roles = prepare_user_create(ctx, row.payload)
                row.action = "create"
        except (AppError, PydanticValidationError) as exc:
            _issue(issues, row.row, "整行", _model_message(exc))
            row.action = "invalid"
    create_count = sum(row.action == "create" for row in rows)
    update_count = sum(row.action == "update" for row in rows)
    unchanged_count = sum(row.action == "unchanged" for row in rows)
    preview = [{
        "row": row.row,
        "username": row.username,
        "display_name": row.display_name,
        "department_code": row.department_code,
        "role_codes": row.role_codes,
        "action": row.action,
    } for row in rows[:50]]
    return ({
        "valid": not issues and bool(rows),
        "file_name": file_name,
        "checksum": hashlib.sha256(data).hexdigest(),
        "total_rows": len(rows),
        "create_count": create_count,
        "update_count": update_count,
        "unchanged_count": unchanged_count,
        "issues": sorted(issues, key=lambda item: (item["row"], item["field"]))[:MAX_ISSUES],
        "preview": preview,
    }, rows)


def import_users(ctx: AuthContext, data: bytes, file_name: str, update_existing: bool, expected_checksum: str) -> dict:
    """Revalidate and apply an entire workbook in one transaction, or write nothing.

    用途：导入提交接口。先核对 checksum，再在同一事务内重新校验并落库，任一行失败整体回滚。
    参数：ctx；data 与 file_name 必须与预览时一致（校验和不同直接拒绝）；update_existing 语义同
    validate_import；expected_checksum 来自预览结果，必须是 64 位十六进制。
    返回：{ok, checksum, total_rows, created_count, updated_count, unchanged_count, message}。
    异常：ValidationError（checksum 格式非法、重新校验未通过 —— 附带 issues 与 checksum）；
    ConflictError（提交文件与预览文件不一致；导入期间出现唯一约束冲突或资料被并发修改，全部回滚）；
    PermissionErrorEx（同 validate_import）。
    副作用：加工作区行锁（串行化并发的导入与单条写入→），逐行创建/更新账号与成员关系，
    并为新增/更新各写一条 source="bulk_import" 的审计，最后写一条汇总审计（含计数与文件校验和）后
    统一提交；异常路径全部回滚，保证“部分导入”不会发生。
    """
    actual_checksum = hashlib.sha256(data).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_checksum or ""):
        raise ValidationError("必须先校验文件并提交校验结果中的 SHA-256")
    if actual_checksum != expected_checksum:
        raise ConflictError("提交文件与已校验文件不一致，请重新校验")
    lock_workspace(ctx)
    preview, rows = validate_import(ctx, data, file_name, update_existing)
    if not preview["valid"]:
        raise ValidationError(
            "导入文件包含错误，未写入任何用户",
            import_issues=preview["issues"],
            checksum=preview["checksum"],
        )
    created = updated = unchanged = 0
    try:
        for row in rows:
            if row.action == "create":
                user_id = create_user_records(ctx, row.payload, row.roles)
                user = ctx.db.get(User, user_id)
                if row.status == "disabled":
                    user.status = "disabled"
                after = authorization_snapshot(ctx, user_id)
                audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                      action="user.create", resource_type="user", resource_id=user_id, metadata={
                          "before": {}, "after": after, "source": "bulk_import", "label": after["label"], "row": row.row,
                      })
                created += 1
            elif row.action == "update":
                before = authorization_snapshot(ctx, row.user_id)
                apply_user_update(ctx, row.user_id, row.prepared_update)
                after = authorization_snapshot(ctx, row.user_id)
                audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
                      action="user.update", resource_type="user", resource_id=row.user_id, metadata={
                          "before": before, "after": after, "source": "bulk_import", "label": after["label"], "row": row.row,
                      })
                updated += 1
            else:
                unchanged += 1
        audit(ctx.db, workspace_id=ctx.workspace_id, actor_user_id=ctx.user_id,
              action="user.bulk_import", resource_type="user", resource_id=None, metadata={
                  "source": "bulk_import", "label": file_name, "checksum": actual_checksum,
                  "created_count": created, "updated_count": updated, "unchanged_count": unchanged,
              })
        ctx.db.commit()
    except IntegrityError as exc:
        ctx.db.rollback()
        raise ConflictError("导入期间账号或关联发生冲突，全部数据已回滚") from exc
    except StaleDataError as exc:
        ctx.db.rollback()
        raise ConflictError("导入期间用户资料已被其他操作修改，全部数据已回滚") from exc
    except Exception:
        ctx.db.rollback()
        raise
    return {
        "ok": True,
        "checksum": actual_checksum,
        "total_rows": len(rows),
        "created_count": created,
        "updated_count": updated,
        "unchanged_count": unchanged,
        "message": f"导入完成：新增 {created}、更新 {updated}、未变更 {unchanged}",
    }


def _safe_excel(value) -> str:
    """导出单元格的公式注入防护：以 = + - @ 开头的文本前加单引号，防止 Excel 当作公式执行。"""
    text = _text(value)
    return "'" + text if text.startswith(("=", "+", "-", "@")) else text


def _style_sheet(sheet) -> None:
    """统一表头样式、冻结首行、加筛选并设置列宽（列宽数量与 HEADERS 一一对应，改列需同步改这里）。"""
    fill = PatternFill("solid", fgColor="EAF1FF")
    for cell in sheet[1]:
        cell.fill = fill
        cell.font = Font(bold=True, color="173B68")
        cell.alignment = Alignment(vertical="center")
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(HEADERS))}{max(sheet.max_row, 1)}"
    widths = [20, 18, 22, 12, 16, 24, 28, 16, 28, 20]
    for index, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(index)].width = width
    sheet.row_dimensions[1].height = 28


def _instruction_sheet(workbook: Workbook) -> None:
    """追加“填写说明”页：每列是否必填、填写规则，以及导入/密码安全的两条总体约定。

    文案与后端校验保持一致（如临时密码 72 小时、更新用户不得填密码），避免管理员按说明填写却报错。
    """
    sheet = workbook.create_sheet("填写说明")
    sheet.append(["列名", "是否必填", "填写规则"])
    for name in HEADERS:
        required = "新增时必填" if name == "初始临时密码" else ("必填" if name in {"登录账号", "显示姓名"} else "可选")
        sheet.append([name, required, HEADER_HINTS[name]])
    sheet.append([])
    sheet.append(["导入规则", "", "请先在页面执行校验；提交时文件 SHA-256 必须与校验结果一致。任一行错误时整批不写入。"])
    sheet.append(["密码安全", "", "导出文件不包含密码；更新已有用户不得填写密码，密码重置必须逐个操作。"])
    for cell in sheet[1]:
        cell.fill = PatternFill("solid", fgColor="EAF1FF")
        cell.font = Font(bold=True, color="173B68")
    sheet.column_dimensions["A"].width = 22
    sheet.column_dimensions["B"].width = 16
    sheet.column_dimensions["C"].width = 90
    sheet.freeze_panes = "A2"


def import_template(ctx: AuthContext) -> bytes:
    """生成导入模板（含“用户导入”“_选项”“填写说明”三个工作表）。

    用途：模板下载接口（需要 system.users.import）。
    参数：ctx（用于读取本工作区启用中的部门/岗位/角色编码）。返回：xlsx 字节流。
    异常：PermissionErrorEx（缺少导入权限）。
    副作用：只读；“_选项”页隐藏并作为状态下拉的数据源，因此模板必须按工作区实时生成 ——
    不同工作区的编码不同，不能用一份静态模板文件代替。
    """
    require_permission(ctx, "system.users.import")
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "用户导入"
    sheet.append(HEADERS)
    for index, name in enumerate(HEADERS, 1):
        sheet.cell(1, index).comment = Comment(HEADER_HINTS[name], "智慧云脑 Cloud Brain")
    _style_sheet(sheet)
    options = workbook.create_sheet("_选项")
    options.append(["账号状态", "部门编码", "岗位编码", "角色编码"])
    status_rows = ["启用", "停用"]
    department_codes = list(ctx.db.scalars(select(Department.code).where(
        Department.workspace_id == ctx.workspace_id, Department.enabled.is_(True),
    ).order_by(Department.code)))
    position_codes = list(ctx.db.scalars(select(Position.code).where(
        Position.workspace_id == ctx.workspace_id, Position.enabled.is_(True),
    ).order_by(Position.code)))
    role_codes = list(ctx.db.scalars(select(Role.code).where(
        Role.workspace_id == ctx.workspace_id, Role.enabled.is_(True),
    ).order_by(Role.code)))
    for index in range(max(len(status_rows), len(department_codes), len(position_codes), len(role_codes), 1)):
        options.append([
            status_rows[index] if index < len(status_rows) else "",
            department_codes[index] if index < len(department_codes) else "",
            position_codes[index] if index < len(position_codes) else "",
            role_codes[index] if index < len(role_codes) else "",
        ])
    options.sheet_state = "hidden"
    status_validation = DataValidation(type="list", formula1="'_选项'!$A$2:$A$3", allow_blank=True)
    sheet.add_data_validation(status_validation)
    status_validation.add("D2:D1001")
    if department_codes:
        department_validation = DataValidation(type="list", formula1=f"'_选项'!$B$2:$B${len(department_codes) + 1}", allow_blank=True)
        sheet.add_data_validation(department_validation)
        department_validation.add("E2:E1001")
    _instruction_sheet(workbook)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()


def export_users(ctx: AuthContext, query: str = "", status: str | None = None, department_id: str | None = None) -> bytes:
    """导出用户（需要 system.users.export），输出格式与导入模板一致，便于“导出→修改→导入”。

    参数：筛选条件与用户列表接口相同（复用 list_users，因此数据范围限制完全一致）。
    返回：xlsx 字节流；密码列固定留空（导出绝不包含密码），状态输出为中文“启用/停用”。
    异常：PermissionErrorEx（缺少导出权限）；ValidationError（结果超过 10000 条，提示先筛选）。
    副作用：只读；分页读取直到取满 total，超过 100 页即判定超限，避免一次性把整个工作区拉进内存。
    约定：导入模板与导出共用 HEADERS，因此导出的文件可以直接再导入（更新时密码列留空即可）。
    """
    require_permission(ctx, "system.users.export")
    items: list[dict] = []
    for page in range(1, 101):
        result = list_users(ctx, page, 100, query, status, department_id)
        items.extend(result["items"])
        if len(items) >= result["total"]:
            break
    else:
        raise ValidationError("导出结果超过 10000 条，请先按部门或关键字筛选")
    role_codes = {role.id: role.code for role in ctx.db.scalars(select(Role).where(Role.workspace_id == ctx.workspace_id))}
    position_codes = {position.id: position.code for position in ctx.db.scalars(select(Position).where(Position.workspace_id == ctx.workspace_id))}
    department_codes = {department.id: department.code for department in ctx.db.scalars(select(Department).where(Department.workspace_id == ctx.workspace_id))}
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "用户导入"
    sheet.append(HEADERS)
    for item in items:
        sheet.append([
            _safe_excel(item["username"]),
            _safe_excel(item["display_name"]),
            "",
            "启用" if item["status"] == "active" else "停用",
            _safe_excel(department_codes.get(item["department_id"], "")),
            _safe_excel("，".join(position_codes.get(value, value) for value in item["position_ids"])),
            _safe_excel("，".join(role_codes.get(value, value) for value in item["role_ids"])),
            _safe_excel(item["employee_code"]),
            _safe_excel(item["email"]),
            _safe_excel(item["phone"]),
        ])
    _style_sheet(sheet)
    _instruction_sheet(workbook)
    output = BytesIO()
    workbook.save(output)
    return output.getvalue()
