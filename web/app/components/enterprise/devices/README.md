# Devices

Server-backed equipment management and separate alert / quality workflow entry points; no local business records or synthetic statistics.

## Internal Modules

- `devices/access`: current native account/workspace must match the enterprise `/me` response before mounting business surfaces; all query keys include both identities.
- `devices/device-actions`: dialog-open snapshots fix edit/delete revisions; successful writes invalidate this identity's enterprise cache.
- `devices/detail`: native tabs own local scenario selection. Separate mounted panels keep each scenario's binding/runs queries and unresolved queue attempt independent; query keys include the generated scenario value as well as account/workspace/device identity.
- `devices/run-dialog`: a lost queue response retains the same idempotency key, parameters and binding revision. Check run reads that exact request through the generated lookup contract, independently of the recent-runs list.
- `devices/schedule`: each scenario reads its server-owned schedule, including paused configuration. State commands carry the displayed revision, never retry automatically, and refetch after success. An uncertain state write disables further writes until a successful read refresh. Creation uses current binding revision and generated actor discovery, locks its single attempt before transport and recovers through server lookup; a null refresh does not unlock an ambiguous creation. Neither path replays a command automatically.
- `devices/schedule-form`: mounted for managers when the server reports no schedule and the current binding is known. Uncontrolled inputs use the generated schema plus grace/interval and current actor membership checks. Local start time is serialized as a UTC instant; stale actor selection is rejected rather than replaced. Binding changes remount an unsubmitted form; submitted payload revision stays frozen. Empty or failed actor discovery never enables creation. Creation attempt protection is component-lifetime only, not a durable cross-reload journal.

Schedule creation HTTP 422 denotes pre-write validation rejection. Only that response offers an explicit Edit action: both the absence lookup and fresh actor discovery must succeed before unlocking the form. No request is replayed by Edit. Conflicts, transport failures and other statuses retain the uncertain-attempt lock; a missing recovery response never proves non-execution. Cross-reload command journaling remains separate from browser preference storage.

- `devices/use-device-filters`: `nuqs` owns page/search/department URL state. Filtering occurs on the server before pagination.
- `feature-flag`: every route is protected by the existing rollout flag.

## External Modules

- `context/account-state`
- `context/workspace-state`

Both scenarios read their actual binding and recent runs, show loading/error/404/empty states, and queue through the existing generated contract when `/me` grants run permission. A binding with a mismatched workspace/device/scenario, or a run row from another device/scenario, shows a retryable load failure instead of an actionable record. Binding display opens the original Dify workflow editor. Binding creation/configuration is not presented as a completed wizard in this slice. Recent runs link to `/enterprise/runs/{runId}` and show execution state, not a fabricated business conclusion. The API rechecks permissions on every read and write; hiding controls is not the authorization boundary.

Scenario tabs use the actual Dify UI primitive: arrow keys move focus and Enter/Space selects, with one accessible panel. `keepMounted` preserves an unresolved alert attempt while the user visits quality and vice versa. Selection is deliberately local, not URL-driven: the queue dialog owns its modal lifecycle, so browser history does not select another scenario behind an open dialog. Both scenarios' server queries may load while one panel is hidden. No production fixtures or inferred cross-scenario statistics are used.

## Recovery boundary

A definitive validation rejection (422) allows correcting parameters with a new attempt. Transport failures and 409 retain the original attempt: a queue response can be lost before a later binding revision changes, so a subsequent 409 does not prove that nothing was queued.

Check run issues a fresh `GET /enterprise/api/v1/run-requests/lookup?request_key=...`, with account/workspace-isolated query keys and no automatic retry. The returned run must match the retained device, scenario, binding revision, specification revision and JSON parameters. A match displays the original execution ID/status and revision, clears the attempt, and performs no POST. A mismatch retains the attempt and blocks confirmation. A 404 means only that this lookup found no record at that moment; it and transport failures preserve the same key, parameters and original revision for an explicit retry. Refreshing the recent five runs remains observation, never authoritative recovery.

Recovery state is component-owned. Each scenario keeps `QueueRun` mounted outside binding-query success/error branches. A missing, mismatched or refreshing binding disables opening, submission and lookup, including controls in an already-open dialog; cancellation still works once any in-flight request finishes. The original attempt remains in that stable component, so a hidden panel's failed binding refresh and later recovery do not create a new request key. Queue success and authoritative recovery invalidate only the submitted device/scenario's binding and recent runs, not the parent device, account access, or the other scenario: those operations do not change device definitions or permissions. Closing/reopening the dialog and switching scenario tabs also retain unresolved attempts. Changing workspace/account/device or losing run permission unmounts that scope and prevents its late response from being displayed elsewhere. Unrelated parent query failures can still unmount the entire surface; persistence across that unmount, full navigation or page reload is not implemented. This slice does not claim full recovery or exactly-once execution, and never silently creates a replacement attempt after uncertainty.
