"""仅凭平台留存证据重新核对快照权限；绝不连接业务库或刷新元数据。"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlglot import exp

from app.errors import PermissionErrorEx, ValidationError
from app.modules.analytics.connections import database_name
from app.modules.analytics.metadata import _column_scope
from app.modules.analytics.models import SourceMetadata
from app.modules.analytics.sources import get_source
from app.modules.analytics.sql_safety import authorize_sql, parse_read_query


class SnapshotScopeError(PermissionErrorEx):
    """旧快照无法证明仍在当前表字段授权范围内，不暴露其 SQL 或字段值。"""

    code = 'snapshot_scope_revoked'


class SnapshotAccess:
    """一次读取内复用源和元数据校验，缓存不跨请求或权限变更。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.sources = {}
        self.tables = {}
        self.checked_sql = {}
        self.wildcard_projections = {}

    @staticmethod
    def restricted(source) -> bool:
        """空表清单代表不限表；单表的空字段清单仍代表撤回整张表。"""
        config = source.connection_config or {}
        return bool(config.get('allowedTables') or config.get('allowedColumns'))

    def source(self, identity):
        """重新核对源资源权限；停用可追溯，删除沿用报表既有拒绝合同。"""
        identity = str(identity)
        if identity not in self.sources:
            self.sources[identity] = get_source(self.ctx, identity, provider='any')
        return self.sources[identity]

    def sql(self, source, sql: str, parameters: dict | None = None) -> set[str] | None:
        """用当前配置收窄缓存结构，检查留存 SQL；配置版本变化不等同于撤权。"""
        if source.provider == 'http' or not self.restricted(source):
            return
        if not isinstance(sql, str) or not sql.strip():
            raise SnapshotScopeError('留存结果缺少可核对的查询来源，无法确认当前数据范围')
        key = (source.id, sql, json.dumps(parameters, sort_keys=True, ensure_ascii=False, default=str))
        if key in self.checked_sql:
            return self.checked_sql[key]
        if source.id not in self.tables:
            snapshot = self.ctx.db.scalar(select(SourceMetadata).where(
                SourceMetadata.workspace_id == self.ctx.workspace_id,
                SourceMetadata.datasource_id == source.id,
            ))
            if snapshot is None or snapshot.status != 'ready':
                raise SnapshotScopeError('缺少可核对的授权元数据，不能读取受限的历史结果')
            # 缓存可以早于停用/改名版本，但必须再次施加当前表列限制，不能直接信任旧缓存。
            self.tables[source.id] = _column_scope(source, snapshot.tables_json)
        try:
            if parameters is None:
                # 报表可能保留待用户选择的钻取参数；只验证字段范围，不执行参数或查询。
                tree = parse_read_query(sql, source.provider)
                parameters = {item.name: None for item in tree.find_all(exp.Placeholder)}
            safe = authorize_sql(sql, provider=source.provider, tables=self.tables[source.id],
                                 database=database_name(source), max_rows=10000, parameters=parameters)
        except ValidationError:
            raise SnapshotScopeError('留存结果引用了当前不可访问的表或字段，不能继续读取或导出') from None
        names = {str(name).casefold() for name in safe.tree.named_selects}
        self.checked_sql[key] = names
        return names

    @staticmethod
    def rows(names: set[str] | None, rows, columns=(), *, dimensions=(), source_header=None) -> None:
        """旧 SELECT * 会按新授权缩列；还须核对留存数据，不能只验证重新展开的 SQL。"""
        if names is None:
            return
        if isinstance(rows, str):
            try:
                rows = json.loads(rows) if rows.strip() else []
            except ValueError:
                raise SnapshotScopeError('留存数据格式无效，无法确认其字段范围') from None
        if isinstance(rows, dict):
            rows = [rows]
        if rows is not None and not isinstance(rows, (list, tuple)):
            raise SnapshotScopeError('留存行数据格式无效，无法确认其字段范围')
        if rows and any(not isinstance(row, dict) for row in rows):
            # 二维数组没有对象键，必须保留可靠的表头或 dimensions；列数相同不是授权证据。
            if not all(isinstance(row, (list, tuple)) for row in rows):
                raise SnapshotScopeError('留存数据缺少可验证的字段名称，请按当前权限重新生成')
            if not isinstance(dimensions, (list, tuple)) or source_header not in (None, True, False, 'auto'):
                raise SnapshotScopeError('留存数据维度或表头声明无效，请按当前权限重新生成')
            labels = [item.get('name') if isinstance(item, dict) else item for item in dimensions or []]
            first = rows[0]
            has_header = source_header is not False and bool(first) and all(isinstance(item, str) for item in first)
            if source_header is True and not has_header:
                raise SnapshotScopeError('留存数据声明的表头无效，请按当前权限重新生成')
            if has_header:
                if labels and labels != list(first):
                    raise SnapshotScopeError('留存数据表头与维度不一致，请按当前权限重新生成')
                labels = list(first)
                rows = rows[1:]
            if (not labels or not all(isinstance(label, str) and label for label in labels)
                    or len({label.casefold() for label in labels}) != len(labels)
                    or not {label.casefold() for label in labels}.issubset(names)
                    or any(len(row) != len(labels) for row in rows)):
                raise SnapshotScopeError('留存数组数据无法证明当前字段授权，请按当前权限重新生成')
        for row in rows or []:
            if isinstance(row, dict) and not {str(key).casefold() for key in row}.issubset(names):
                raise SnapshotScopeError('留存数据含当前 SQL 未授权的字段，不能继续读取或导出')
        for column in columns or []:
            name = column.get('columnName') if isinstance(column, dict) else column
            if name is not None and str(name).casefold() not in names:
                raise SnapshotScopeError('留存列目录超出当前 SQL 授权范围，不能继续读取或导出')

    def wildcard_projection(self, source, sql):
        """仅识别对外投影的星号；COUNT(*) 与显式外层投影不会误判为未知输出列。"""
        key = (source.provider, sql)
        if key not in self.wildcard_projections:
            tree = parse_read_query(sql, source.provider)
            self.wildcard_projections[key] = any(
                isinstance(item, exp.Star) or (isinstance(item, exp.Column) and isinstance(item.this, exp.Star))
                for item in tree.selects
            )
        return self.wildcard_projections[key]

    def query_result(self, source, sql, result) -> None:
        """历史列表、详情和导出共用相同的实际投影检查。"""
        names = self.sql(source, sql, (result or {}).get('parameters') or {})
        self.rows(names, (result or {}).get('data'), (result or {}).get('columns'))

    def content(self, content: dict | str, source_ids=(), *, trusted_widget=None) -> bool:
        """核对画布与单组件中的全部真实绑定，不能用本次新源授权替代旧结果来源。"""
        if isinstance(content, str):
            try:
                content = json.loads(content)
            except (ValueError, TypeError):
                raise SnapshotScopeError('留存内容格式无效，无法确认其数据范围') from None
        if not isinstance(content, dict):
            raise SnapshotScopeError('留存内容格式无效，无法确认其数据范围')
        identities = {str(identity) for identity in source_ids if identity not in (None, '', 0)}
        for identity in identities:
            self.source(identity)
        widgets = content.get('widgets', [content] if content.get('component') else [])
        if not isinstance(widgets, list):
            raise SnapshotScopeError('留存组件格式无效，无法确认其数据范围')
        has_reference = False
        for widget in widgets:
            if not isinstance(widget, dict):
                raise SnapshotScopeError('留存组件格式无效，无法确认其数据范围')
            config = widget.get('config') or {}
            query = widget.get('query') or {}
            if not isinstance(config, dict) or not isinstance(query, dict):
                raise SnapshotScopeError('留存数据绑定格式无效，无法确认其数据范围')
            primary = widget.get('datasourceId') or config.get('datasourceId') or query.get('datasourceId')
            primary = primary or (next(iter(identities)) if len(identities) == 1 else None)
            bindings = [query, config, {'datasourceId': primary, 'sql': widget.get('generatedSql')},
                        widget.get('drilldown') or {}, config.get('drilldown') or {}]
            candidates = set(identities)
            for binding in bindings:
                if not isinstance(binding, dict):
                    raise SnapshotScopeError('留存数据绑定格式无效，无法确认其数据范围')
                identity = binding.get('datasourceId') or primary
                if identity:
                    candidates.add(str(identity))
                    self.source(identity)
            if trusted_widget is not None and trusted_widget(widget):
                # 只有平台登记且载荷逐字段一致的仓库素材例外；源可见性仍在上面检查。
                # 这条读取例外不参与任何 SQL 执行授权。
                has_reference = True
                continue
            row_bindings = []
            restricted_wildcard = False
            for index, binding in enumerate(bindings):
                if not isinstance(binding, dict):
                    raise SnapshotScopeError('留存数据绑定格式无效，无法确认其数据范围')
                identity = binding.get('datasourceId') or primary
                if not identity:
                    continue
                source = self.source(identity)
                if binding.get('sql'):
                    names = self.sql(source, binding['sql'])
                    if index < 3:
                        row_bindings.append(names)
                        restricted_wildcard = restricted_wildcard or (names is not None and self.wildcard_projection(source, binding['sql']))
            for names in row_bindings:
                self.rows(names, widget.get('data'))
                self.rows(names, config.get('chartDataParsed'))
                self.rows(names, config.get('chartData'))
            has_rows = (widget.get('data') or config.get('chartDataParsed')
                        or config.get('chartData') not in (None, '', '[]', '{}'))
            # 两个渲染入口都可能保存数据，dataset/series 也同时支持单对象与数组。
            for option in (config.get('option'), widget.get('echartsOption'), widget.get('option')):
                if not isinstance(option, dict):
                    continue
                datasets = option.get('dataset') or []
                if isinstance(datasets, dict):
                    datasets = [datasets]
                for dataset in datasets if isinstance(datasets, list) else []:
                    if isinstance(dataset, dict) and dataset.get('source'):
                        has_rows = True
                        for names in row_bindings:
                            self.rows(names, dataset['source'], dimensions=dataset.get('dimensions') or [],
                                      source_header=dataset.get('sourceHeader'))
                for key in ('series', 'xAxis', 'yAxis', 'singleAxis', 'parallelAxis', 'legend'):
                    items = option.get(key) or []
                    if isinstance(items, dict):
                        items = [items]
                    if isinstance(items, list):
                        inline_values = any(isinstance(item, dict) and any(
                            item.get(field) for field in ('data', 'nodes', 'links', 'edges')) for item in items)
                        if inline_values and restricted_wildcard:
                            # 旧星号重新展开后已丢失原输出列；series/坐标值不能凭长度或位置推断来源。
                            # 显式授权 SQL 生成的系列不受此限制，可信目录素材也已在上面单独证明。
                            raise SnapshotScopeError('旧星号图表的系列缺少可验证字段来源，请按当前权限重新生成')
                        has_rows = has_rows or inline_values
            if has_rows and not row_bindings and any(self.restricted(self.source(identity)) for identity in candidates):
                # 多个来源且逐组件没有绑定也不能绕过；空布局和无业务行的装饰仍可正常查看。
                raise SnapshotScopeError('留存数据缺少可核对的 SQL 来源，请在当前授权范围重新生成')
        return has_reference
