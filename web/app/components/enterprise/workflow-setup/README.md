# Workflow setup

Creates default workflow drafts and inspects durable import history. Optional provisioning selects a server profile and advances one journal phase at a time; publication still requires enrollment and device binding. Pending command safety stays in the mounted setup controller across dialog closes; reload recovery uses server history, not browser-persisted keys.

## Internal Modules

- devices/access

## External Modules

None.

Enrollment restores the unique provisioning relation through a read-only query.
Its controls appear only for a fully published four-phase journal. Token storage
alone does not imply activation: the matching token_stored record mounts the
ActivationSection keyed by enrollment ID, preserving parent permission/error gates.
Recovery uses server state; local pending state suppresses repeated clicks while
the operation is unconfirmed.

ActivationSection owns generated specification discovery, explicit version selection,
activation recovery and revision-fenced revocation. Mount it keyed by enrollment ID
only after token storage. It blocks mismatched enrollment snapshots and ambiguous
mutations, refreshes with reads only, and treats revoked records as terminal. It
does not claim the native deployment is ready solely from an activation receipt.
