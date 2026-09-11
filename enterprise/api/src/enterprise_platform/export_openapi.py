"""Export the real HTTP contract without deployment settings, identity or database access.

This module is a schema-only CLI, not an ASGI deployment factory. It uses the same
HTTP factory and business/repository classes as bootstrap, with deliberately
unusable runtime dependencies rather than a fabricated account or connection URL.
Generated schemas omit deployment origins and endpoints, never API requirements.
"""

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import JsonObject, Principal
from enterprise_platform.application.service import BusinessService
from enterprise_platform.http.app import create_app
from enterprise_platform.persistence.repository import SqlAlchemyRepository


class _SchemaOnlySession(Session):
    def __init__(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("Schema export must never create a database session")


class _SchemaOnlyIdentity:
    async def resolve(
        self, *, cookie_header: str | None, authorization: str | None, csrf_token: str | None
    ) -> Principal:
        raise RuntimeError("Schema export must never resolve runtime identity")


def build_openapi() -> JsonObject:
    repository = SqlAlchemyRepository(sessionmaker(class_=_SchemaOnlySession))
    app = create_app(BusinessService(repository), _SchemaOnlyIdentity())
    document: JsonObject = TypeAdapter(JsonObject).validate_python(app.openapi())
    document.pop("servers", None)
    paths = document.get("paths")
    if isinstance(paths, dict):
        for path in paths.values():
            if not isinstance(path, dict):
                continue
            path.pop("servers", None)
            for operation in path.values():
                if not isinstance(operation, dict):
                    continue
                operation.pop("servers", None)
                parameters = operation.get("parameters")
                if not isinstance(parameters, list):
                    continue
                for parameter in parameters:
                    if not isinstance(parameter, dict) or parameter.get("name") != "Origin":
                        continue
                    schema = parameter.get("schema")
                    if isinstance(schema, dict):
                        schema.pop("enum", None)
    return document


def serialize_openapi() -> str:
    return json.dumps(build_openapi(), ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="Explicit destination for the business OpenAPI JSON")
    parser.add_argument("--check", action="store_true", help="Fail on schema drift without writing files")
    arguments = parser.parse_args(argv)
    output: Path = arguments.output
    content = serialize_openapi().encode("utf-8")
    if arguments.check:
        if not output.is_file() or output.read_bytes() != content:
            sys.stderr.write("Business OpenAPI is missing or differs from the current HTTP contract.\n")
            return 1
        return 0
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
