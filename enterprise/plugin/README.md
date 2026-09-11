# Managed device assessment plugin

Source implementation for the Dify Python plugin SDK **0.9.1**. It evaluates the
server-registered device read and rule configuration for an already-associated
native workflow execution. It does not dispatch workflows, execute caller SQL,
or accept execution identity from a model.

## Native registration and workflow wiring

- Manifest: `manifest.yaml`, publisher `enterprise`, plugin
  `enterprise_device_assessment`.
- Provider: `provider/enterprise_device.yaml`, name `enterprise_device`;
  registration ID `enterprise/enterprise_device_assessment/enterprise_device`.
- Tool: `tools/evaluate_device.yaml`, name `evaluate_device`, no parameters.
- Register the exact workspace, app, provider, tool, credential ID, workflow and
  node using the native managed-execution hook. Configure the enterprise API key
  for that same workspace/app and allowed node IDs. Installation alone does not
  create these server registrations.
- The server injects `runtime.credentials['__enterprise_execution']` for that
  invocation only. It is a dictionary with exactly `workspace_id`, `app_id`,
  `workflow_id`, `native_run_id`, `node_id`, `node_execution_id`, and
  `invoke_from: service-api`. Workflow, run, and node-execution IDs must be
  canonical UUID strings. Ordinary workflow inputs are not consulted.
- The plugin independently matches SDK `session.app_id`, injected `app_id`, and
  its configured `expected_app_id`. Missing metadata or a scope mismatch fails
  before HTTP. It does not send the native session, dispatch nonce, or signing
  secret to the enterprise API or to a model.
- On success the tool emits **one text message containing the complete
  BusinessEnvelope JSON**, including run/device/spec identity, snapshot digest,
  and the typed business result. Select the tool node's **text** output for the
  workflow End node's **result** variable; do not substitute an LLM summary.

## Operator-only credentials

| Field                 | Meaning                                                                                                               |
| --------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `origin`              | Fixed enterprise API origin, without a path/query/user info. HTTPS by default.                                        |
| `key_id`              | Server-registered signing-key identifier.                                                                             |
| `secret`              | 32–256 printable ASCII characters; stored as a secret input and SecretStr.                                            |
| `expected_app_id`     | Exact Dify application ID bound to the key and native registration.                                                   |
| `allow_insecure_http` | Boolean, default `false`; explicit `true` permits a fixed HTTP origin for an operator-managed private Docker network. |

Provider validation checks syntax locally, not live server connectivity or key
validity. Keys are operator configuration, never tool parameters. Use a separate
key and registration for each intended app boundary. HTTP opt-in changes
transport confidentiality; it does not relax identity or HMAC checks. Keep the
endpoint on the intended private network when enabling it.

## Request and failure contract

The client sends `POST /enterprise/internal/v1/evaluate`, with the seven native
fields and integer `issued_at` / `expires_at` (30-second lifetime). Headers
`x-enterprise-key-id` and `x-enterprise-signature` carry the key ID and lowercase
HMAC-SHA256 of the **exact UTF-8 request body**. The backend authenticator remains
the authority for key scope, freshness, and native-run association.

Only HTTP 409 with exactly `{"code":"execution_association_pending"}` is
retried: the same immutable native identity is re-signed, up to 20 attempts,
0.25 seconds apart, within a 5-second association window. This handles the
streamed `workflow_started` event reaching persistence shortly after the tool
starts. No other status, transport failure, or malformed body is retried; a
retry never redispatches a workflow or substitutes model-supplied identity.

The total evaluation deadline is 60 seconds, with HTTPX I/O timeouts and
monotonic checks during body consumption and retries. A currently blocked
synchronous I/O call is governed by its transport timeout, not a claim of
forced thread cancellation. Redirects and environment proxy inheritance are
disabled. TLS verification is on; compressed responses are rejected. Responses
are capped at 512 KiB, strictly decoded as UTF-8 JSON, checked for duplicate
keys/nonfinite numbers, and validated against the business-envelope schema.
Errors expose stable failure codes, not response bodies or credentials. Both
decoded values and serialized output are checked for signing-secret echoes.

## Offline verification and source artifact

From the plugin directory, using the existing isolated enterprise API runtime:

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
$env:PYTHONPATH = (Resolve-Path '../api/src').Path
uv run --project ../api --no-sync python -m pytest tests/test_client.py tests/test_bridge.py tests/test_backend_contract.py tests/test_package.py -q
uv run --project ../api --no-sync mypy --strict managed_device_plugin
uv run --project ../api --no-sync python build_source.py
```

The client and bridge use HTTPX MockTransport. The backend contract test checks
the actual backend authenticator against the exact client request bytes; it
needs `enterprise_platform` on the test import path. This is a test-only
dependency, not a plugin runtime import. `tests/test_sdk.py` separately uses the
**real installed SDK** to validate manifest/provider/tool loading and the SDK
text-message wrapper. It skips if the SDK is absent; that skip is not an SDK
pass. Run it in an environment with the pinned SDK and its own dependencies.

These tests do not install the plugin, start a daemon, contact a database or
source service, or demonstrate a deployed end-to-end workflow. Actual signed
plugin installation and native/daemon/backend execution remain deployment
verification steps. No signature-verification policy was changed.

`build_source.py` uses an explicit file whitelist, verifies archive CRC and every
stored file, and writes `dist/enterprise-device-assessment-0.1.0-source.zip` plus
its `.zip.sha256`. `SOURCE_SHA256.json` records each included source file's
digest. Credentials, environment files, caches, virtual environments, and the
output directory are excluded. The ZIP is a reproducible **source archive**,
not a `.difypkg` or a signed installation package. Build and sign the actual
installation package with the normal Dify plugin release tooling and policy.
