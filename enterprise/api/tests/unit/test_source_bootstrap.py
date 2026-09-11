import asyncio
import json
import threading
from unittest.mock import patch

import pytest
from test_bootstrap import settings
from test_source_encryption import keyring
from test_source_service import policy

from enterprise_platform.application.input_capture import ImmutableReadCatalog
from enterprise_platform.application.source_service import StoredReadCatalog
from enterprise_platform.bootstrap import Settings, create_runtime


def environment():
    config = settings()
    return {
        "ENTERPRISE_DATABASE_URL": config.database_url.get_secret_value(),
        "ENTERPRISE_DIFY_CONSOLE_URL": config.dify_console_url,
        "ENTERPRISE_DIFY_SERVICE_URL": config.dify_service_url,
        "ENTERPRISE_ALLOWED_ORIGINS": config.allowed_origins[0],
    }


def test_explicit_source_keyring_and_egress_environment_are_bounded_and_redacted() -> None:
    env = environment()
    keys = keyring()
    data = {
        "active_key_id": keys.active_key_id,
        "keys": [{"key_id": k.key_id, "key": k.key.get_secret_value()} for k in keys.keys],
    }
    env["ENTERPRISE_SOURCE_ENCRYPTION_KEYS_JSON"] = json.dumps(data)
    env["ENTERPRISE_SOURCE_ALLOWED_ENDPOINTS_JSON"] = json.dumps([entry.model_dump() for entry in policy().entries])
    config = Settings.from_environment(env)
    assert config.source_keyring.active_key_id == "key-1"
    assert config.source_endpoints == policy().entries
    assert keys.keys[0].key.get_secret_value() not in repr(config)
    for invalid in ("malformed-fixture-secret", " " * 65537):
        env["ENTERPRISE_SOURCE_ENCRYPTION_KEYS_JSON"] = invalid
        with pytest.raises(ValueError) as error:
            Settings.from_environment(env)
        assert invalid not in str(error.value)


def test_default_static_catalog_stays_independent_of_new_source_database_tables() -> None:
    with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("no DB")):
        runtime = create_runtime(settings())
        try:
            assert isinstance(runtime.input_capture.registry, ImmutableReadCatalog)
        finally:
            runtime.close()


def test_configured_composition_constructs_real_stored_catalog_without_connecting() -> None:
    config = Settings.model_validate(
        {**settings().model_dump(), "source_keyring": keyring(), "source_endpoints": policy().entries}
    )
    with patch("sqlalchemy.engine.Engine.connect", side_effect=AssertionError("no DB")):
        runtime = create_runtime(config)
        try:
            assert isinstance(runtime.input_capture.registry, StoredReadCatalog)
            assert "/enterprise/api/v1/sources" in runtime.app.openapi()["paths"]
        finally:
            runtime.close()


def test_fresh_registry_lookup_runs_off_event_loop_but_historical_capture_bypasses_it() -> None:
    from test_input_capture import dependencies, registered, run_record

    service, repository, reader = dependencies()
    main = threading.get_ident()
    observed = []

    class Registry:
        def resolve(self, *args):
            observed.append(threading.get_ident())
            return registered()

    service.registry = Registry()
    asyncio.run(service.capture("workspace-1", "run-1", "nonce-secret"))
    assert observed and observed[0] != main
    snapshot = repository.capture_input.call_args.args[3]
    repository.get_run.return_value = run_record().model_copy(update={"input_snapshot": snapshot})
    observed.clear()
    asyncio.run(service.capture("workspace-1", "run-1", "nonce-secret"))
    assert not observed
