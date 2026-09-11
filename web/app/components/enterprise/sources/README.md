# Sources

Server-backed read-only connection configuration with ordinary database / HTTP fields and real device selection.

## Internal Modules

- `sources/source-editor`: dialog-session snapshots, conditional write controls, retained request attempts and generated mutations.
- `sources/source-fields`: connection, device scope, additional parameters, and advanced limit / cursor controls.
- `sources/device-picker`: paginated live device search; selected opaque IDs remain strings, never typed manually or converted to numbers.
- `sources/form-values`: generated writable schema validation, explicit read-only confirmation, credential-retention and target-change checks.
- `sources/controls`: labeled native Dify UI fields, choices and toggles used by this form.
- `devices/access`: native account/workspace identity and enterprise read access.
- `feature-flag`: source route remains behind the existing default-off rollout flag.

## External Modules

None.

## Contracts and behavior

The real generated `consoleQuery.business.sources` contract supplies capabilities, list/get, create and update. Read access does not require source management privileges. Editing requires both `can_manage` and `write_enabled`; device-management permission is not a substitute. Query keys include account and workspace, and page state lives in `nuqs`. Returned list/detail/write workspace identities, and known source IDs, are checked before actionable display or success. The API still enforces identity, permissions, target policy and credentials on every request.

The form uses `SourceDraftWritable`, never the credential-stripped ordinary `SourceDraft` export. Users configure human names, database host/port/name/TLS/allowlisted tables/SQL, or fixed HTTP URL/method/headers/row path/cursor settings. Device scope comes from live paginated device records. Source/read IDs, workspace and revisions are server-owned, not editable form fields. Additional run parameters have separate input-name, source-name, value-type and nullability controls. Limits and pagination use collapsible controls, not bulk JSON editing.

Database username/password are always empty when opening a stored source. Leaving both empty sends paired nulls to retain credentials only for an unchanged target. Changing connection kind, engine, host, port, database or TLS requires a fresh pair. HTTP headers distinguish null (retain), replacement rows, and `[]` (explicit removal); changing a previously credentialed HTTP target requires replacement or removal. Stored header values are never returned. Read-only confirmation starts unchecked for each new edit session, including existing sources.

Cards show the actual `not_tested` state, not a simulated connection check. There is no test-connection action, deletion API, generated binding/configuration wizard, or implied deployment in this slice.

## Recovery and confidentiality boundaries

Creating a source freezes a random idempotency key and the exact writable body before sending. Transport errors, 5xx and 409 retain that attempt for an explicit same-request retry, including closing/reopening the dialog. A definitive 422 permits correction with a new attempt. Capabilities refreshes keep the create owner mounted and temporarily hide/disable writes; they do not replace the key. Successful operations invalidate source records only, not account access.

Opening an existing source fetches its latest view. That dialog session fixes the original revision for `If-Match`; cache updates do not silently replace the edit snapshot. Unknown PUT results retain values and revision. An edit 409 blocks another submit until closing and reopening to inspect current state. Background list failures preserve cached source cards and their edit attempts while disabling writes. A write response from a different workspace/source leaves the original attempt intact and blocks confirmation for administrator reconciliation, rather than claiming success or creating a replacement request.

Passwords/header values exist only in form controls and the component-owned pending attempt / active mutation. They never enter query keys, query-cache records, URL parameters or local storage; mutations have zero retention after their observer detaches. They are not rendered in errors or logs. Full navigation, page reload, list pagination that removes an edited row, identity-scope changes, or parent access failures can unmount this in-memory state. Pending attempts are not durably restored across those boundaries; this UI does not claim exactly-once writes or universal recovery. Source management does not itself prove that a workflow binding points to the newly stored source/read revisions.

Tests use real Query/Jotai/nuqs providers and native dialog/select/checkbox primitives with generated-client boundaries mocked. No backend service, database, migration or real connection was started by this UI task.
