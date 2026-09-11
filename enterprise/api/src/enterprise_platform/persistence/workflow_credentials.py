"""Atomic encrypted credential registration and explicit revision-fenced lifecycle."""

import hmac

from pydantic import SecretStr, TypeAdapter, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from enterprise_platform.application.contracts import Identifier
from enterprise_platform.application.errors import Conflict, InvalidInput, NotFound, PersistenceError
from enterprise_platform.application.workflow_credential_contracts import (
    CredentialView,
    WorkflowCredential,
    validate_token,
)
from enterprise_platform.application.workflow_credential_ports import CredentialCipher, SealedCredential

from .mapping import audit, cas, decode, serialized, transaction, utc, utc_now, validate_revision
from .workflow_credential_models import WorkflowCredentialRow

_IDENTIFIER = TypeAdapter(Identifier)


def _validate(*values: str) -> None:
    try:
        for value in values:
            _IDENTIFIER.validate_python(value)
    except ValidationError:
        raise InvalidInput("invalid_workflow_credential_scope") from None


def credential_row(view: CredentialView, sealed: SealedCredential) -> WorkflowCredentialRow:
    return WorkflowCredentialRow(
        **view.model_dump(),
        public_json=serialized(view),
        key_id=sealed.key_id,
        nonce=sealed.nonce,
        ciphertext=sealed.ciphertext,
    )


def as_credential(row: WorkflowCredentialRow) -> tuple[CredentialView, SealedCredential]:
    try:
        view = decode(CredentialView, row.public_json)
    except PersistenceError:
        raise PersistenceError("workflow_credential_corrupt") from None
    for name in ("workspace_id", "app_id", "secret_ref", "revision", "active"):
        if getattr(row, name) != getattr(view, name):
            raise PersistenceError("workflow_credential_corrupt")
    if utc(row.created_at) != view.created_at or utc(row.updated_at) != view.updated_at:
        raise PersistenceError("workflow_credential_corrupt")
    return view, SealedCredential(row.key_id, row.nonce, row.ciphertext)


def _find(
    session: Session, workspace_id: str, app_id: str, secret_ref: str
) -> tuple[CredentialView, SealedCredential] | None:
    row = session.scalar(
        select(WorkflowCredentialRow)
        .where(
            WorkflowCredentialRow.workspace_id == workspace_id,
            WorkflowCredentialRow.app_id == app_id,
            WorkflowCredentialRow.secret_ref == secret_ref,
        )
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    result = as_credential(row)
    if (result[0].workspace_id, result[0].app_id, result[0].secret_ref) != (workspace_id, app_id, secret_ref):
        raise PersistenceError("workflow_credential_corrupt")
    return result


def _get(session: Session, workspace_id: str, app_id: str, secret_ref: str) -> tuple[CredentialView, SealedCredential]:
    result = _find(session, workspace_id, app_id, secret_ref)
    if result is None:
        raise NotFound("workflow_credential_not_found")
    return result


def _audit(session: Session, view: CredentialView, actor_id: str, action: str) -> None:
    audit(
        session,
        view.workspace_id,
        "workflow_credential",
        view.secret_ref,
        actor_id,
        "workflow_credential_" + action,
        view.updated_at,
        {"app_id": view.app_id, "secret_ref": view.secret_ref, "revision": view.revision, "active": view.active},
    )


class SqlAlchemyWorkflowCredentialVault:
    _sessions: sessionmaker[Session]
    _cipher: CredentialCipher

    def __init__(self, sessions: sessionmaker[Session], cipher: CredentialCipher) -> None:
        self._sessions, self._cipher = sessions, cipher

    def _replay(
        self, stored: tuple[CredentialView, SealedCredential], credential: WorkflowCredential
    ) -> CredentialView:
        view, sealed = stored
        if not view.active:
            raise Conflict("workflow_credential_revoked")
        token = self._cipher.open(view, sealed)
        if not hmac.compare_digest(token.get_secret_value(), credential.api_key.get_secret_value()):
            raise Conflict("workflow_credential_reference_conflict")
        return view

    def register(self, credential: WorkflowCredential, *, actor_id: str) -> CredentialView:
        _validate(credential.workspace_id, credential.app_id, credential.secret_ref, actor_id)
        try:
            validate_token(credential.api_key)
        except ValueError:
            raise InvalidInput("invalid_workflow_credential") from None
        try:
            with transaction(self._sessions) as session:
                existing = _find(session, credential.workspace_id, credential.app_id, credential.secret_ref)
                if existing is not None:
                    return self._replay(existing, credential)
                now = utc_now()
                view = CredentialView(
                    workspace_id=credential.workspace_id,
                    app_id=credential.app_id,
                    secret_ref=credential.secret_ref,
                    revision=1,
                    active=True,
                    created_at=now,
                    updated_at=now,
                )
                session.add(credential_row(view, self._cipher.seal(view, credential.api_key)))
                session.flush()
                _audit(session, view, actor_id, "registered")
                return view
        except Conflict as error:
            if str(error) != "database_constraint_conflict":
                raise
            with transaction(self._sessions) as session:
                existing = _find(session, credential.workspace_id, credential.app_id, credential.secret_ref)
                if existing is None:
                    raise Conflict("workflow_credential_reference_conflict") from None
                return self._replay(existing, credential)

    def get(self, workspace_id: str, app_id: str, secret_ref: str) -> CredentialView:
        _validate(workspace_id, app_id, secret_ref)
        with transaction(self._sessions) as session:
            view, sealed = _get(session, workspace_id, app_id, secret_ref)
            self._cipher.open(view, sealed)
            return view

    def resolve(self, workspace_id: str, app_id: str, secret_ref: str) -> SecretStr:
        _validate(workspace_id, app_id, secret_ref)
        with transaction(self._sessions) as session:
            view, sealed = _get(session, workspace_id, app_id, secret_ref)
            if not view.active:
                raise Conflict("workflow_credential_revoked")
            return self._cipher.open(view, sealed)

    def revoke(
        self, workspace_id: str, app_id: str, secret_ref: str, *, expected_revision: int, actor_id: str
    ) -> CredentialView:
        return self._change(
            workspace_id, app_id, secret_ref, expected_revision=expected_revision, actor_id=actor_id, revoke=True
        )

    def reseal(
        self, workspace_id: str, app_id: str, secret_ref: str, *, expected_revision: int, actor_id: str
    ) -> CredentialView:
        return self._change(
            workspace_id, app_id, secret_ref, expected_revision=expected_revision, actor_id=actor_id, revoke=False
        )

    def _change(
        self, workspace_id: str, app_id: str, secret_ref: str, *, expected_revision: int, actor_id: str, revoke: bool
    ) -> CredentialView:
        _validate(workspace_id, app_id, secret_ref, actor_id)
        validate_revision(expected_revision)
        with transaction(self._sessions) as session:
            old, sealed = _get(session, workspace_id, app_id, secret_ref)
            if old.revision != expected_revision or (revoke and not old.active):
                raise Conflict("workflow_credential_revision_conflict")
            token = self._cipher.open(old, sealed)
            view = CredentialView.model_validate(
                old.model_dump()
                | {"revision": old.revision + 1, "active": False if revoke else old.active, "updated_at": utc_now()}
            )
            new = self._cipher.seal(view, token)
            cas(
                session,
                update(WorkflowCredentialRow)
                .where(
                    WorkflowCredentialRow.workspace_id == workspace_id,
                    WorkflowCredentialRow.app_id == app_id,
                    WorkflowCredentialRow.secret_ref == secret_ref,
                    WorkflowCredentialRow.revision == expected_revision,
                    WorkflowCredentialRow.active == old.active,
                )
                .values(
                    revision=view.revision,
                    active=view.active,
                    updated_at=view.updated_at,
                    public_json=serialized(view),
                    key_id=new.key_id,
                    nonce=new.nonce,
                    ciphertext=new.ciphertext,
                ),
            )
            _audit(session, view, actor_id, "revoked" if revoke else "resealed")
            return view
