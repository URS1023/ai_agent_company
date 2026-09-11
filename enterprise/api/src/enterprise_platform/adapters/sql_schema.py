"""Exact-name SQLGlot catalog without a shared qualification-depth constraint.

An unqualified grant and a schema-qualified grant can coexist without merging their
columns. SQL qualification runs against this immutable projection, not a database.
"""

from collections.abc import Mapping

from sqlglot import exp
from sqlglot.schema import MappingSchema, Schema


class ExactSqlSchema(Schema):
    def __init__(self, columns: Mapping[str, Mapping[str, str]], *, dialect: str) -> None:
        self._tables: dict[str, MappingSchema] = {}
        for name, fields in columns.items():
            table = MappingSchema(dialect=dialect, normalize=False)
            table.add_table(name, column_mapping=dict(fields))
            self._tables[name] = table

    def _lookup(self, table: exp.Table | str) -> MappingSchema:
        name = ".".join(part.name for part in exp.to_table(table).parts)
        if name not in self._tables:
            raise ValueError("Table is outside the schema projection")
        return self._tables[name]

    def add_table(
        self,
        table: exp.Table | str,
        column_mapping: object = None,
        dialect: object = None,
        normalize: bool | None = None,
        match_depth: bool = True,
    ) -> None:
        raise ValueError("Schema inference is disabled")

    def column_names(
        self,
        table: exp.Table | str,
        only_visible: bool = False,
        dialect: object = None,
        normalize: bool | None = None,
    ) -> tuple[str, ...]:
        return tuple(self._lookup(table).column_names(table, only_visible=only_visible, normalize=False))

    def get_column_type(
        self,
        table: exp.Table | str,
        column: exp.Column | str,
        dialect: object = None,
        normalize: bool | None = None,
    ) -> exp.DataType:
        return self._lookup(table).get_column_type(table, column, normalize=False)

    @property
    def supported_table_args(self) -> tuple[str, ...]:
        return "this", "db", "catalog"

    @property
    def empty(self) -> bool:
        return not self._tables
