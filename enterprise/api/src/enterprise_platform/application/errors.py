"""Public domain error codes; never include a credential or raw driver response."""


class EnterpriseError(Exception):
    code = "enterprise_error"
    status_code = 500

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class NotFound(EnterpriseError):
    code = "not_found"
    status_code = 404


class Conflict(EnterpriseError):
    code = "conflict"
    status_code = 409


class AccessDenied(EnterpriseError):
    code = "access_denied"
    status_code = 403


class Unauthenticated(EnterpriseError):
    code = "unauthenticated"
    status_code = 401


class DependencyUnavailable(EnterpriseError):
    code = "dependency_unavailable"
    status_code = 503


class InvalidInput(EnterpriseError):
    code = "invalid_input"
    status_code = 422


class InvalidState(Conflict):
    code = "invalid_state"


class PersistenceError(EnterpriseError):
    code = "persistence_error"
    status_code = 503
