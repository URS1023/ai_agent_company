# Enterprise source management implementation plan

> For agentic workers: use subagent-driven-development; preserve the approved V2 enterprise scope and native Dify behavior.

**Goal:** Replace operator-only source registration with versioned management contracts and actual persistence, then expose ordinary-user configuration and both device scenarios. This is a delivery step, not a new definition of full project completion.

**Architecture:** Keep the separate enterprise database and current RegisteredRead/InputCapture seam. A managed source resource owns one connection/read pair, human name, exact workspace/device scope, immutable revisions and encrypted credentials. Published historical revisions remain resolvable for queued runs. API derives workspace/actor from native authentication. No native table changes, account changes, live migrations or long-running backend starts.

- [x] Implement typed source drafts/views and revision-safe management service. Browser supplies human configuration, not source/workspace/read IDs or encrypted blobs. Default passwords are never returned; unchanged credentials on edit require an explicit documented preservation contract.
- [x] Add dedicated persistence and a reviewed additive migration; preserve old immutable reads and use exact version lookup for capture. Local tests use SQL compilation/protocol doubles; real transactions stay in CI.
- [x] Compose authenticated source routes, server-owned encryption keys and explicit configuration; default missing keys disables source writes rather than plaintext storage. Regenerate OpenAPI/client contracts from actual routes.
- [x] Add same-origin BFF route whitelist and source management UI using generated clients and native primitives; clear states for unconfigured service, conflicts and permissions. No invented successful connection test.
- [x] Expand device detail to separate alert/quality state, binding and recent runs; retain original uncertain-request recovery and native editor links. Hidden-panel request recovery and narrow cache invalidation are covered by regression tests and independent review.
- [x] Verify source revision resolution, no credential leakage, stale revision conflicts, workspace scope, original capture behavior, native guards and current frontend types in local protocol/unit checks. Build/source-verify current artifact only after final changes. Real source/DB transactions and authenticated browser flows remain separate gates below.

Current evidence: `enterprise/verification-source-management-final-2026-09-08.log` exited 0 (785 enterprise API units, 222 focused web tests, cold full-web type checking and the other named checks). `enterprise/artifacts/source-management-package-verification.json` verifies all 45 Python/SQL source members in the current wheel. Source database integration collected 7 cases but was not run locally or in remote CI. A downloaded static DSL is not an installed/published/bound workflow. See the implementation ledger for the superseded run-event Page mock and stale incremental type-cache findings.

Future required deliverables remain default native workflow provisioning and binding wizard, source preview, scheduler, event handling/notification, quality review, fixed dashboard templates and SQL bindings, AI workbench with editable PPT/DOCX, database CI, browser and full native regression. A source CRUD unit suite alone does not complete them.

## Default native workflow assets (root, parallel to source persistence)

- [x] Add `enterprise/workflows/default-workflows.mjs` and its Node tests: two editable native workflow DSL graphs, same registered assessment provider/tool, no supplied identity, secrets, arbitrary HTTP/code nodes or model-rewritten results.
- [x] Generate and byte-check the alert/quality YAML assets (JSON syntax accepted by the native YAML parser), with a direct tool text → `result` end-node mapping. Read-only download routes and per-scenario device links are covered by RTL tests.
- [x] Validate start/tool/end node shapes with the installed native graphon schemas in an isolated test command; this proves node format, not plugin installation or published execution.
- [ ] Keep credential selection, publication, exact native allowlist registration and authenticated binding provisioning as explicit remaining steps. Template import alone must never be advertised as a functioning end-to-end deployment.

## Next binding slice: published profiles, then complete native provisioning

This is a staged implementation boundary, **not a replacement for automatic native workflow creation and binding**. The first ordinary-user wizard should select a real device, a saved source version, a server-registered published execution profile, and typed alert/quality rules. App/workflow/read/credential IDs and secret references are resolved by the server, not typed or copied by the user. Profiles that are not executable must display a concrete unavailable state rather than be presented as ready.

### Verified native contracts and reuse (read-only inspection, 2026-09-08)

All routes below are native Console routes under `/console/api`; use the existing native login/CSRF client and ACL checks, not Service API tokens for administration.

| Step | Actual contract / existing generated call | Boundary |
| --- | --- | --- |
| Import a bundled DSL | `POST /apps/imports`, body `{mode:"yaml-content",yaml_content,name?}`; `consoleClient.apps.imports.post({body})` | Returns `id` (import ID), `app_id`, `status`, versions and `permission_keys`. Status may be `pending` (HTTP 202), `failed`, or `completed-with-warnings`; none proves an executable deployment. Do not supply `app_id` when creating a new application, as that updates an existing one. |
| Confirm pending import | `POST /apps/imports/{import_id}/confirm`; `apps.imports.byImportId.confirm.post({params:{import_id}})` | Explicit confirmation; importing has internal native commits, so an outer enterprise transaction does not make it atomic or idempotent. Lost responses require reconciliation rather than repeating create. |
| Dependency check | `GET /apps/imports/{app_id}/check-dependencies`; `apps.imports.byAppId.checkDependencies.get({params:{app_id}})` | This compares cached import dependencies against installed plugins. The native cache expires after 10 minutes and an absent cache returns an empty list; an empty result is not durable proof that the required provider/tool is installed and usable. Recheck the actual installed provider/tool. |
| Select native plugin credential | `GET /workspaces/current/tool-provider/builtin/{provider}/credentials`; `workspaces.current.toolProvider.builtin.byProvider.credentials.get({params:{provider}})` | Native service filters by workspace and credential visibility and masks values. Display name/type/visibility, retain only selected IDs. Do not treat a masked secret as a reusable credential value, auto-select another member's credential, or change the workspace default for a managed workflow. |
| Configure a credential | `POST .../builtin/{provider}/add`, body `credentials`, `name`, `type`, `visibility`; existing native plugin configuration UI is reusable | Native add returns only `{result}`, not the credential UUID. Re-list/reconcile by the deliberately assigned credential name and verify identity. The enterprise plugin validates its configuration locally; this is not a successful business-source connection test. Normal DSL export removes tool `credential_id` unless secrets are explicitly included, so imported templates need an explicit credential assignment. |
| Save credential-bound draft | `GET`/`POST /apps/{app_id}/workflows/draft`; `apps.byAppId.workflows.draft.get/post` | GET returns `graph`, `features`, `hash`, environment/conversation variables. POST carries the complete graph and current `hash`; native `sync_draft_workflow` rejects a changed hash. Preserve unrelated graph fields and never inject enterprise actor/workspace/run identity into tool parameters. |
| Publish | `POST /apps/{app_id}/workflows/publish`, body `{marked_name?,marked_comment?}`; `apps.byAppId.workflows.publish.post` | **Response is only `{result,created_at}`, not a workflow UUID and not an expected-draft-hash acknowledgement.** Service creates a new Workflow row; controller updates the app's latest pointer in the same native transaction. Credential-policy validation is conditional on native plugin-manager feature configuration, not universal readiness validation. |
| Resolve the published version | `GET /apps/{app_id}/workflows` with `page`, `limit`, optional `user_id`/`named_only`; `apps.byAppId.workflows.get` | Items contain `id`, full graph, hash and marked metadata. `GET .../workflows/publish` means *latest*, so POST then GET-latest can race another publish. `/workflows/{workflow_id}` itself exposes PATCH/DELETE, not a snapshot GET. Reconcile a unique marked operation and exact graph/hash from the version list, failing on ambiguity; do not silently pin whatever is latest. A future narrowly reviewed native publish seam could atomically return the created UUID and validate the expected draft hash. |
| Obtain execution credential | `POST /apps/{resource_id}/api-keys`; `apps.byResourceId.apiKeys.post({params:{resource_id}})` | Native response contains a real app API token. Provisioning must keep it server-side in an encrypted, workspace/app/ref-scoped vault, never in wizard state, model inputs or a plaintext profile. Current enterprise gateway uses explicit operator configuration, not a dynamic profile vault. |

Native evidence in the current worktree:

- `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/controllers/console/app/app_import.py`: lines 37–48 (body), 71–135 (import/status/ACL), 138–216 (confirm/dependencies).
- `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/services/app_dsl_service.py`: lines 53–65 (cache lifetime/import response), 364–385 (dependency cache), 474–516 (draft synchronization), 601–609 (credential removal on ordinary export).
- `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/controllers/console/app/workflow.py`: lines 285–333 (snapshot/list response), 349–351 (publish response), 502–606 (draft), 1188–1256 (publish), 1396–1446 (version list), 1488–1565 (version mutation routes).
- `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/services/workflow_service.py`: lines 216–239 (tenant/app/exact UUID lookup and draft rejection), 312–382 (draft hash check), 500–572 (publish and conditional policy validation).
- `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/controllers/console/workspace/tool_providers.py`: lines 575–659 (credential add/list); `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/services/tools/builtin_tools_manage_service.py`: lines 213–305 (credential validation/encryption), 347–438 (visibility/masking).
- `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/controllers/console/apikey.py`: lines 94–125 and 175–201 (app token creation); `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/controllers/console/app/wraps.py`: lines 27–43 (current-tenant, normal-app scope); `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/controllers/console/wraps.py`: lines 400–414 (edit gate).
- Generated contracts: `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/packages/contracts/generated/api/console/apps/orpc.gen.ts` (imports 497–551, draft/publish 4394–4487, versions 4690–4718, API keys 4841–4888); `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/packages/contracts/generated/api/console/workspaces/orpc.gen.ts` (credential list 3206–3226, provider namespace 3405–3422).

Import requires native edit permission and `APP_IMPORT_EXPORT_DSL`; draft/version reads require `APP_VIEW_LAYOUT`; publishing and app API-key creation require `APP_RELEASE_AND_VERSION`. Preserve native RBAC/role behavior rather than equating enterprise source-management permission with native app publishing permission. Credential creation/update also retains native visibility and credential-policy behavior. No real native import, publication, plugin installation or key creation was executed for this inspection.

### Minimum profile/binding service checks

- [ ] Derive actor/workspace from native identity; enforce the business binding action and native profile/app eligibility. Return only human-readable profile metadata, its opaque selection ID and actual readiness reasons.
- [ ] Resolve the selected source **exact revision**, not a moving head; verify workspace, device membership, current device scope and source/read version consistency. Missing or changed versions fail explicitly. The current `SourceView` exposes declared columns/configuration, not a verified live schema; saving/binding is not a source probe.
- [ ] Resolve an immutable published profile by workspace/scenario/revision. Require an existing non-draft workflow UUID, expected provider/tool/node topology and explicit native credential UUID. Do not substitute the latest workflow or the default credential.
- [ ] Verify the profile's plugin credential is registered for the same app, the gateway credential exists for exact workspace/app/ref, and native managed metadata registration includes the exact workspace/app/workflow/provider/tool/credential/node tuple. A profile with only a YAML asset or app UUID is not ready.
- [ ] Validate typed alert/quality specifications through the existing specification/domain models, including scope/revision, declared source columns, long/wide measurement mapping, units/conversions and sample/revision selection. Store immutable rules, not model-generated expressions. Reject missing columns/specification bindings; retain incomplete/no-data semantics after actual capture.
- [ ] Generate `BindingWrite` server-side with pinned source/read/profile/specification references and perform existing binding CAS. Browser submits selections/rules and expected revision only; it never supplies `secret_ref`, arbitrary workflow IDs or run identity. Preserve old bindings/runs and their immutable evidence.

### Full provisioning remains required after that slice

- [ ] Add a durable, idempotent provisioning record/state machine: import → dependency verification → app-specific plugin credential → hash-checked draft → verified published UUID → encrypted Service API-key registration → managed-version registration → business binding. Ambiguous network outcomes remain pending/reconcilable, not repeated native creates or fake success.
- [ ] Complete server-side encrypted workflow credential storage and rotation scoped by workspace/app/ref; source-registration encryption alone is not this vault.
- [ ] Replace operator-only managed-version enrollment with an explicitly governed dynamic enrollment seam. Current `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/core/workflow/node_factory.py:343–351` reads `ENTERPRISE_MANAGED_TOOLS_JSON`, and `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/api/core/workflow/enterprise_execution.py:23,146–190` requires the exact published version. Publishing a new UUID does not register it automatically.
- [ ] Preserve plugin three-way app binding (`expected_app_id`, native session app and trusted metadata) in `F:/code_f/llm_f/dify/.worktrees/enterprise-platform/enterprise/plugin/managed_device_plugin/models.py`; create/reconcile per-app credentials rather than weakening that check.
- [ ] Verify both scenarios with real CI transactions and native/plugin integration, plus browser lifecycle/error/CAS tests. Registration, import and publish-unit tests alone do not demonstrate end-to-end device evaluation.

### Executing next: durable default-draft creation

The first native side effect in the full provisioning chain is now a concrete delivery task. It does not replace the remaining publication/credential/enrollment/binding steps and must never return an executable-ready result for an imported draft.

- [x] Native scoped import seam: opt-in `ENTERPRISE_WORKFLOW_SETUP_ENABLED`; authenticated capabilities route; managed-only expected-workspace header check on the exact account object passed to AppDslService, before Session/import. Keep every original setup, account, billing and RBAC decorator. Ordinary import responses and behavior stay unchanged. Managed responses acknowledge workspace; no backend automatically calls an unpatched importer.
- [x] Dedicated setup contracts/0003 persistence: freeze selected source/read versions and expected binding revision, durable workspace/request idempotency, queued→importing CAS, nonce-checked outcome storage. A crash or ambiguous native response stays importing/uncertain rather than repeating app creation. Unit SQL/protocol checks locally; real transactions only in CI.
- [x] Native setup client: reuse filtered native authentication forwarding, confirm capabilities/scope, import bundled DSL with no app_id/secret/workspace in DSL inputs, validate actual status and scope acknowledgement. Never retry native POST or treat pending/warnings as published readiness.
- [x] Application/HTTP composition: validate real device and selected source scope/revision, recover frozen requests before consulting moving versions, store before I/O, return a public setup projection with no nonce/cookie/key. Feature-disabled deployments make no new database calls.
- [x] Ordinary UI: select source and create the default draft from a device's alert/quality panel; show progress, current creation record, native editor link, uncertainty and remaining setup steps. Generated enterprise contracts and 23-language strings, no manual opaque workflow IDs. Preserve existing queue request recovery.
- [x] Review the exact additive native integration, rebuild native preservation evidence, verify source templates inside the Python wheel and run focused tests. Complete guarded 0003 deployment/CI wiring before enabling runtime configuration. This phase is draft creation, not full one-click runnable binding.

Local S5 verification: `enterprise/verification-workflow-setup-final-fixed-2026-09-09.log`, exit 0. CI wiring exists; the 11 setup integration cases were collected only, not executed locally or remotely. Runtime flags remain off until deployment checks; complete native/plugin/browser acceptance and full provisioning remain open.

### S6 executing: exact publication and encrypted execution credentials

This extends the approved full provisioning chain, not its completion definition. Existing S5, native functionality, static credentials and immutable queued run references stay intact. No backend starts, local database integration runs, automatic migrations or Git writes. Independent implementers and read-only task review follow subagent-driven-development; parent owns composition and complete verification.

- [x] Native exact publication: opt-in managed-only expected-workspace plus expected-draft-hash headers on the existing publish endpoint. Preserve its decorators/normal response. Check the same actor and app scope before Session; lock and validate the exact draft read by the original publish service, retaining all normal credential/graph/agent/billing/event behavior. Managed response returns the UUID created in that transaction and the accepted draft hash, plus workspace acknowledgement. A mismatch fails before creation. No latest-pointer inference or automatic retries. Add real native-unit regression tests first; guard review only after exact diff review.
- [x] Dedicated execution credential vault: purpose-bound AES-256-GCM with workspace/app/ref/revision/status in AAD; separate versioned keyring setting, no plaintext persistence/API output/logging. Immutable app token references: identical register may replay, different token at same reference conflicts; token rotation uses a new reference. Explicit revision-checked revoke and encryption-key reseal preserve history. SQLAlchemy separate metadata, audited atomic transitions; local SQL/protocol units and CI-only transaction cases.
- [x] Gateway and bootstrap: inject a typed exact-scope credential resolver; configured static mode stays unchanged when vault disabled. Vault mode must not silently fall back after missing/revoked/corrupt references; no import-time connections or automatic schema changes. Test failures before any native HTTP and encrypted lookup in execution/recovery.
- [x] Guarded 0004 and CI sequence: require 0001/0002/0003 shapes, deterministic packaged SQL, explicit dedicated-database checks. Run old setup seven-table smoke before 0004, then new vault integration cases. Collect only locally.
- [x] Enterprise native publish client: fixed Console URL, filtered ephemeral authentication, capabilities preflight, strict response UUID/app/scope/hash checks; accepted graph hash is not credential readiness or enrollment. Ambiguous POST stays uncertain without another POST. This client will be used by the next durable provisioning phase, never a browser-secret API.
- [x] Review focused implementation, fix findings, rebuild package/native evidence and rerun unified tests. Wire app-specific plugin configuration, durable remaining provisioning transitions, dynamic managed enrollment and final ordinary-user binding next; do not label this substrate as full workflow setup.

S6 local evidence: `enterprise/verification-publish-vault-final-2026-09-09.log`, exit 0; native import/publish38 tests separately passed. Seven new DB integration cases collected only. Encrypted vault and publish adapter are implemented; durable full provisioning, dynamic managed enrollment and final ordinary-user binding remain required.

### Next full-provisioning constraints confirmed in native source (S7 input)

Do not jump from draft_ready/published to bound. The next durable operation must include actual per-app plugin credential preparation, dynamic execution-key and managed-version enrollment, native Service API-token creation into the completed vault, typed rule/specification persistence and final binding CAS. Each external side effect must have a persisted claim and reconciliation path.

- Installed provider query is `GET /workspaces/current/tool-providers?type=builtin`, with qualified ID/name `enterprise/enterprise_device_assessment/enterprise_device`, plugin ID `enterprise/enterprise_device_assessment`, and `plugin_unique_identifier` for installed version/digest. Check `evaluate_device` and supported api-key credentials, not import cache alone.
- Native credential add returns only `{result:"success"}`. Recover the UUID from the provider's list by one owned operation name plus provider and public configuration; do not compare masked secret values as proof of key identity. Names have a 30-character limit and the provider has a 100-credential cap. Provisioning must surface the native cap rather than silently bypass it or claim unbounded per-device app creation.
- The plugin signing secret is separate from the Service API token stored by S6. Current `ExecutionKey` requires workspace/app/node IDs and current bootstrap installs its router only for configured keys. Automatic new-app key provisioning and dynamic registration are still required, not satisfied by the new execution-token vault.
- Per-app plugin credential includes fixed operator-owned origin, key_id, matching HMAC secret, exact expected_app_id, and explicit private HTTP opt-in if used. Never update workspace default or repurpose another app's credential. The existing plugin validation only parses configuration; it is not a live backend connectivity test.
- Provider credential add uses a current-workspace endpoint. Before server-side automated writes, retain native permission checks and add an opt-in expected-workspace guard bound to the actual tenant passed to the service, not merely a prior GET capability. No cross-workspace secret write on a session switch.
- Draft patch must set only the intended assessment tool node's canonical credential UUID and preserve all graph/features/environment/conversation values and variable IDs. Missing or invalid tool credential ID falls back to default/oldest in native tool_manager, so it is never an acceptable managed state. Omitting variable lists clears them; use existing native masked-secret normalization by ID.
- Ordinary draft hash also covers only graph. Preparing a draft and publishing still need exact graph/credential validation and native permission checks, followed by verified enrollment of the published UUID. Readiness must distinguish credentials configured from truly deployed/usable.

Evidence inspected, not executed: api/controllers/console/workspace/tool_providers.py (list/add/credentials), api/services/tools/builtin_tools_manage_service.py (limits/name/visibility), api/core/tools/entities/api_entities.py (list response), api/core/helper/provider_encryption.py (masking), api/core/tools/tool_manager.py (credential fallback), api/controllers/console/app/workflow.py and api/models/workflow.py (draft variables), enterprise/plugin/provider/enterprise_device.yaml and application/managed_execution.py (actual plugin key scope).

### S7 executing: per-app plugin credential preparation

This task advances the same full provisioning chain; it does not replace durable phase ownership, execution-key deployment, dynamic enrollment or final binding. Native credential encryption, visibility, limits, validation and default selection remain unchanged.

- [x] Add opt-in expected-workspace guards and response acknowledgements to the native provider list, builtin tools list, credential info and credential add endpoints. Compare the tenant actually passed to each service; when the same Account is present also compare its cached workspace. Ordinary calls preserve decorators, payloads and responses. No credential-service/model changes or default-credential route calls. Advertise a separate credential_setup_enabled capability only with these guards present.
- [x] Add a server-only typed per-app plugin configuration (origin, signing key ID/secret, explicit HTTP policy) and credential preparation client. Require capability and canonical identity, verify exact installed provider/plugin/version and evaluate_device plus api-key support, enforce native 100-credential limit and a deterministic <=30-character owned name. POST once with exact expected_app_id; secret is sent only to the fixed native Console credential endpoint, never public setup state/model inputs/logs.
- [x] Read back one matching owned credential UUID and public configuration only after a confirmed scoped native POST. A prior matching name, lost POST, mismatching response/config or ambiguous list remains uncertain, never silently re-creates or treats masked secret equality as proof. This step reports credential_created, not execution-ready. Later durable orchestration must persist ownership and reconcile these outcomes.
- [x] Add a pure native draft patch adapter using the full returned graph/features/environment/conversation snapshots, replacing only the intended assessment node's canonical credential_id. Preserve layouts, unrelated nodes and variable IDs/masked values; retain the original native hash for CAS and reject wrong provider/tool/topology before writes. No alternative DAG engine or model-authored rules.
- [x] Test native ordinary/managed scope behavior and strict enterprise protocol/patch behavior with RED→GREEN and independent review; update exact native preservation evidence only after review and rebuild the backend package. No local DB/service execution or Git writes; deployed plugin/key, actual draft sync and whole provisioning UI remain separate gates.


### S8 executing: exact credential-only draft mutation

Evidence changes the write design: native GET adds response-only Agent projection, native hash covers stored graph only, and ordinary full sync replaces features/environment/conversation. Therefore the S7 complete-snapshot helper stays a pure preparation/preview utility; it is not used to overwrite live unrelated fields.

- [x] Add a managed-only branch to existing draft POST, preserving ordinary decorators and behavior. Paired expected-workspace and X-Enterprise-Draft-Operation: bind-assessment-credential; strict JSON {draft_id,hash,credential_id}. Lock and refresh exact tenant/app/draft ID/version, compare old graph hash, patch only unique assessment credential in stored graph, preserve feature/variable fields and original validations/agent/events. Capture immutable exact receipt before commit expiry; no lookup of latest result.
- [x] Advertise separate draft_credential_bind_enabled under the existing default-off setup flag only with the managed route implemented. Body-only capabilities acknowledgment remains unchanged.
- [x] Typed server client validates canonical IDs/hash and capability, sends one delta POST, verifies exact scope/IDs/accepted hash and fresh hash. Unconfirmed POST remains uncertain; no automatic retries, no full-snapshot writes and no published/device-bound claim.
- [x] RED-to-GREEN native/client tests, cross-author review, exact-byte native preservation update, CI inclusion, root unified verification and rebuilt package.

Concurrency boundary: managed row lock protects its transaction and leaves unrelated columns untouched. Ordinary writers remain unchanged and may overwrite the graph later; subsequent publication must use the captured resulting graph hash and report conflict if it changed. This stage does not claim global writer serialization. Durable phase ownership/reconciliation, deployed signing keys, exact published-version enrollment and final business binding remain required.


### S9 executing: scoped metadata acquisition for the provisioning chain

The imported setup has app_id but not the native draft ID/hash needed by S8. Ordinary GET returns graph projection and variables; automated provisioning needs only exact native metadata, not a copied graph snapshot.

- [x] Managed metadata-only GET on existing draft route: paired expected-workspace + X-Enterprise-Draft-Operation: read-assessment-draft, default-off flag, canonical workspace/app tenant guard, exact returned draft tenant/app/version. Body only app_id/draft_id/hash/version=draft plus workspace acknowledgment. Ordinary GET projection and decorators unchanged; no DB writes or additional locks.
- [x] Separate draft_read_enabled capability and typed bounded server reader. Canonical app/draft IDs and exact returned app/workspace; no secrets/graph/variables retained; no retries or writes; failures sanitized.
- [x] RED-to-GREEN native/client tests, independent reviews, CI and native byte review updates, unified regression and packaging.

This closes acquisition only. Persisted claim and reconcile phases must orchestrate reader, credential creation, delta binder and exact publisher before automatic execution enrollment and final device binding. Metadata is an observation; later writes still require graph CAS.


### Next durable orchestration decision (source-reviewed after S9)

Keep import-only SetupView and 0003 state/nonce constraints unchanged. Add a separate operation + typed phase journal referencing a confirmed draft_ready setup. Freeze actor, workspace/app/device/scenario, source/read versions and expected binding revision at creation. Freeze per-phase command plus stable operation UUID before side effects; preserve claims across cancellation and failed finalization.

Phases: read metadata -> prepare plugin credential -> bind credential delta -> exact publish. Persist only metadata/credential IDs/plugin identity/native hashes/exact published UUID and configuration references, never NativeSetupSession, signing secret, Service API token or full graphs. One authenticated advance(operation_id, expected_revision) performs one phase: transaction claim commit, external I/O, nonce/phase/revision-fenced audited finish. No lease-expiry replay of ambiguous POSTs.

Confirmed publication remains published_pending_enrollment. S9 metadata alone does not reconcile lost credential creation, bind assignment or operation-specific publication. Add explicit reconciliation later; never infer ownership from a name or GET-latest. Required subsequent dependencies remain dynamic HMAC registration, native Service API token issuance into the encrypted vault, exact native managed tuple enrollment, specification selection and final BindingWrite CAS. Bootstrap currently wires importer only; future compose must opt in these clients using server configuration and ephemeral native auth.


### S10 executing: durable provisioning contracts and phase execution

- [x] Separate frozen operation/phase contracts and repository port, preserving import-only S5. Ordered four-phase commands and receipts, exact scope/hash/config snapshots, actor and nonce/CAS ownership, no credential/session serialization. Pure transitions reject skipped phases and reuse of ambiguous claims; published_pending_enrollment remains non-ready.
- [x] Phase executor maps existing scoped reader, config-resolved credential client, delta binder and exact publisher into validated public receipts. No storage or HTTP retries inside executor; caller must persist claims first. Distinguish definite preflight rejection from uncertain post-side-effect outcomes; cancellation leaves durable claim recoverable.
- [x] Independent reviews and focused/aggregate validation. No in-memory journal presented as durable delivery.
- [ ] Follow with transactional journal repository, additive migration, orchestration service and generated API/UI.


### S11 executing: transactional provisioning storage and advance service

- [x] Add ProvisioningBase/enterprise_workflow_provisioning operation row, bounded phase journal JSON with mirrored scope/revision/nonce fields, exact decode checks, request replay actor/hash checks and atomic audited CAS. Create/claim validate linked confirmed setup snapshots and live device; finish preserves owned outcomes after device deletion.
- [x] Add guarded0005 migration requiring exact eight prior tables and validated prior artifacts; CI runs after credential smoke, then new provisioning transactions and nine-table public smoke. No local database execution.
- [x] Start/get/advance application service: require actor/workspace/role/device access, freeze setup/config snapshots, commit one claim before executor, validate exact returned transition, finalize with nonce/revision. No automatic phase replay or native calls inside DB transaction. Public bootstrap/routes/UI remain separate next work.
- [x] Review, focused and aggregate tests, integration collection (not execution), package and ledger update. Do not claim durable recovery proven from unit doubles.

### S12 API、配置解析与前端传输接入

- [x] 新增受身份/权限保护的 start/get/advance API，公开请求只包含配置引用与版本或 expected_revision，保持 private,no-store 和错误脱敏。
- [x] 提供不可变版本化服务器 profile，按精确工作区/应用派生签名配置；客户端固定作用域，解析无外部副作用。
- [x] bootstrap 默认关闭，启用需 setup 和显式 profiles；构造不启动服务、连接数据库或执行原生请求。
- [x] 重新生成 OpenAPI/oRPC/Zod，增加精确前端代理路径及生成客户端传输回归。
- [x] 完整统一回归和本轮 wheel 源码核验（1366 API、257前端、冷类型检查；84包内源码核对）。
- [ ] 配置选择/推进 UI、动态执行 key 登记、原生 token/精确执行版本登记和最终设备绑定，继而真实业务闭环测试。

独立接口与 profile/bootstrap 审查均无确定发现。所有勾选项是源码接入，不代表实际环境部署；完整产品与原生功能验收继续保持未完成。


### S13 配置向导与刷新恢复

- [x] 增加setup范围的profile发现：公开config_ref/config_revision/display_name；管理权限和精确setup/device/workspace检查；隐藏origin与所有密钥。配置display_name可选以兼容S12，缺省展示config_ref；更改标签不改变派生密钥。
- [x] 增加setup范围的持久化操作历史；仓储actor条件必须在count和分页之前应用，服务再校验所有结果的actor/workspace/setup/device/app/scenario，返回Page公开投影。刷新历史恢复操作ID，不重发advance。
- [x] 新增GET /workflow-setups/{setup_id}/provisioning-profiles和GET /workflow-setups/{setup_id}/provisioning，生成契约与精确代理路径。
- [x] 扩展现有workflow-setup对话框而非新增嵌套弹窗：具名配置选择、四阶段状态、明确推进；claimed/uncertain/rejected禁止推进；published_pending_enrollment显示发布但未登记/绑定。保留仅创建草稿功能。
- [x] 作用域隔离、丢响应、刷新恢复、权限与分页专项及完整回归。没有真实执行登记时不显示可运行。


### S14 受作用域校验的 Service API 凭据获取

- [x] 在既有app api-keys POST新增成对managed headers（expected workspace + operation=issue-workflow-key），默认setup开关关闭时拒绝；保留普通app/dataset路由、RBAC/edit装饰器、10-key上限和原生创建流程。创建前验证当前tenant与期望workspace，返回精确app_id、原生token id及workspace确认，private,no-store。无原生新增表。
- [x] capability新增service_api_token_issue_enabled；typed企业客户端先做能力/作用域预检，再只发一次POST。任何发送后未确认的返回均uncertain；不GET列表认领任意token，不自动重试。
- [x] 临时token使用SecretStr、排除公开序列化/repr，只有确认后的内存结果允许未来持久化编排立即写入已存在的加密vault。接口本身不启用设备、不登记执行，也不把token明文写入日志。
- [x] native普通/managed专项、企业客户端专项、精确源码保护审查及回归。后续继续独立数据库登记、原生精确图检查与最终BindingWrite。


### S15 精确已发布版本与工具绑定核验

- [x] 既有publish GET增加默认关闭的managed只读分支：成对要求expected workspace、expected workflow UUID、expected graph hash和operation=read-assessment-publication。只读取精确tenant/app/workflow版本，不读取latest代替；普通GET不变。
- [x] 校验published非draft、精确ID/hash与唯一assessment节点、固定builtin provider/evaluate_device及canonical credential UUID。返回仅app/workflow/hash/provider/tool/credential/node公开元数据与workspace确认，禁止graph、环境变量、token进入响应。复用已有按ID查询，不增原生表或写入。
- [x] 企业typed reader在能力预检后单次GET，比较调用者持久化的workflow UUID/hash/credential UUID。任何不匹配或读失败均拒绝登记，不退回latest或省略校验。
- [x] 原生普通读取/managed拒绝边界、客户端专项、源码保护与回归；这只是登记前核验，不是凭据存活、动态执行授权或最终设备绑定的证明。


### S16 登记准备的持久化状态契约与执行边界

- [x] 独立Enrollment记录冻结完整已完成ProvisioningView及私有新secret_ref，保留原四阶段日志兼容性。状态依次pending_verification→verification_claimed→verified→token_claimed→token_stored，失败终止为rejected/uncertain；token_stored只是待激活检查点，不是已绑定。
- [x] 领取以actor/revision/nonce校验，无过期重领。核验收尾严格比较既有发布与凭据回执的workspace/app/workflow/hash/credential/provider/tool/node。token成功收尾必须与已加密入库的CredentialView scope/ref/revision/active精确匹配。
- [x] 仓储端口明确成功token结果与加密vault记录/登记回执必须同事务写入；不能分别提交后假定原子成功。调用原生接口前先提交claim；发送后不明结果保留uncertain，不重复创建token。
- [x] 实现单阶段执行器，返回只读核验结果或临时SecretStr token结果，持久化由后续仓储服务处理。独立数据库模型/迁移和实际事务测试随后接入，动态tuple/key激活与最终绑定仍是必需后续项。

### S17 独立数据库登记持久化

- [x] 独立 ORM 元数据与严格行映射：完整快照、唯一登记、私有凭据引用、状态/nonce/时间约束，覆盖所有状态和损坏镜像拒绝。
- [ ] 事务仓储：校验已完成 provisioning 与当前设备/setup，领取先提交；成功 token 的加密凭据、登记收尾和审计同事务提交。
- [ ] 受保护的独立数据库迁移及 CI-only 数据库并发、回滚、唯一性集成测试。
- [ ] 服务/API 接线、动态执行登记激活和最终设备绑定，端到端真实运行验收。

### S20 动态执行激活

- [x] 可注入的精确 key_id/workflow 运行时密钥查询边界：保留 HMAC、时间、租户/应用/节点及原生运行关联检查；不缓存撤销结果；默认不启用。
- [ ] 独立数据库的激活记录与只读解析器，绑定已核验 publication、插件配置版本与加密凭据引用。
- [ ] 原生运行时从固定受保护接口读取精确工具注册，不新增原生业务表、不使用通配符或 latest 回退。
- [ ] 激活与最终设备绑定的修订校验、冲突处理、真实运行联调和全系统验收。
